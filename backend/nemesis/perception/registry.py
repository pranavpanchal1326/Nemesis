"""The model registry: one loader, one copy, one bounded memory ceiling.

**The problem this exists for, stated as it actually happens.** Four Celery
children in ``worker-ml`` pick up four image complaints in the same second. Each
one asks for CLIP. Without a guard all four call ``open_clip.create_model``
simultaneously, each allocates ~600 MB while the others do, and the container's
memory cap kills the pool — at which point Celery redelivers the four tasks and
it happens again. The failure looks like a broken model, and it is a broken
*load*, which is why the guard is here and not inside each encoder.

**Single flight is the whole design.** One caller loads; every other caller for
the same key blocks on an event and receives the *same object*. It is not a lock
around the whole registry: a thread loading Whisper must not block a thread that
wants the already-resident CLIP, because that turns a 40-second cold start into
40 seconds of stalled classification for every complaint in flight. So the map
is guarded briefly and the load happens outside the lock, which is the only
arrangement where "never load twice" and "never serialise unrelated work" are
both true.

**The ceiling is a refusal, not an eviction cascade.** ``ModelCapacityError``
is raised when a load cannot fit within ``max_resident_bytes`` after evicting
everything idle. The tempting alternative — evict whatever is least recently
used, in use or not — produces a worker that thrashes: CLIP evicts Whisper,
the next voice complaint reloads Whisper which evicts CLIP, and throughput
collapses to the reload time with nothing in any log naming the cause. A refusal
degrades one complaint to ``pending_classification`` and says the number out
loud.

**Footprints are declared, and that is a deliberate imprecision.** Python cannot
tell you the resident cost of a torch module — ``sys.getsizeof`` reports the
wrapper, and RSS is process-wide and lies under copy-on-write after a fork. A
declared number is honest about being an estimate and still does the job the
ceiling exists for, which is to stop the *third* large model from loading into a
container sized for two.

**What is registered here beyond weights.** Prompt matrices. Embedding forty
category prompts costs a full text-tower pass, and it is the same result for
every complaint until the tenant edits the taxonomy — so a matrix is cached
under the prompt set's own content hash. That is what makes the registry a
registry of *model and prompt-set pairs* rather than of weights: an entry is
only reusable if both halves match, and a tenant publishing new prompts gets a
new key rather than a stale matrix.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Final

from nemesis.observability import metrics
from nemesis.observability.logging import get_logger
from nemesis.perception.errors import ModelCapacityError, ModelLoadError

log = get_logger(__name__)

#: How long a caller waits for another thread's in-flight load before giving up.
#: Above the slowest cold start measured on the reference machine (Whisper, ~40 s
#: from a cold page cache) with room to spare, and finite rather than absent: a
#: loader that died between setting the flag and clearing it would otherwise park
#: every subsequent caller forever, and a worker that has silently stopped
#: processing is worse than one that fails a task with a message.
LOAD_WAIT_TIMEOUT_SECONDS: Final = 180.0


@dataclass(slots=True)
class _Entry:
    """One resident artefact and what the registry knows about it."""

    key: str
    value: Any
    footprint_bytes: int
    loaded_at: float
    last_used: float
    #: How many callers currently hold this entry. Incremented under the lock at
    #: hand-out and decremented when the borrower releases it. Eviction skips a
    #: non-zero count, because freeing memory out from under a running forward
    #: pass does not free it — the object stays alive through the borrower's
    #: reference and the registry has merely lost track of it.
    borrowers: int = 0
    uses: int = 0


@dataclass(slots=True)
class _Loading:
    """An in-flight load other callers wait on rather than duplicating."""

    event: threading.Event = field(default_factory=threading.Event)
    value: Any = None
    error: BaseException | None = None


class ModelRegistry:
    """Process-wide, warm-loaded, bounded. One instance; see ``REGISTRY`` below.

    Not a cache in the ordinary sense — a cache miss here costs 600 MB and forty
    seconds rather than a database round trip, so the policies differ at every
    point: entries never expire on a clock, eviction refuses rather than
    thrashes, and a concurrent miss is coalesced instead of raced.
    """

    def __init__(self, *, max_resident_bytes: int) -> None:
        if max_resident_bytes <= 0:
            raise ValueError("max_resident_bytes must be positive")
        self._max_resident_bytes = max_resident_bytes
        self._lock = threading.Lock()
        self._entries: dict[str, _Entry] = {}
        self._loading: dict[str, _Loading] = {}

    # -- introspection ----------------------------------------------------

    @property
    def max_resident_bytes(self) -> int:
        return self._max_resident_bytes

    def resident_bytes(self) -> int:
        with self._lock:
            return sum(entry.footprint_bytes for entry in self._entries.values())

    def resident_keys(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._entries))

    def is_resident(self, key: str) -> bool:
        with self._lock:
            return key in self._entries

    # -- the load path ----------------------------------------------------

    def get(
        self,
        key: str,
        *,
        footprint_bytes: int,
        load: Callable[[], Any],
        kind: str = "model",
    ) -> Any:
        """The artefact for ``key``, loading it at most once across all callers.

        ``footprint_bytes`` is checked *before* the load rather than after, so a
        model that cannot fit is refused without first allocating it — checking
        afterwards would mean the OOM has already happened by the time the
        registry notices it should not have.
        """
        if footprint_bytes <= 0:
            raise ValueError(f"{key!r} declared a non-positive footprint; see the module docstring")

        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                entry.last_used = time.monotonic()
                entry.uses += 1
                metrics.perception_model_cache_total.labels(kind=kind, result="hit").inc()
                return entry.value

            in_flight = self._loading.get(key)
            if in_flight is None:
                # This caller owns the load. The flag goes up *inside* the lock
                # so a second caller arriving one instruction later sees it and
                # waits, rather than starting a second copy of a 600 MB load.
                in_flight = _Loading()
                self._loading[key] = in_flight
                owner = True
            else:
                owner = False

        if not owner:
            metrics.perception_model_cache_total.labels(kind=kind, result="coalesced").inc()
            return self._await_load(key, in_flight)

        metrics.perception_model_cache_total.labels(kind=kind, result="miss").inc()
        return self._perform_load(
            key, in_flight, footprint_bytes=footprint_bytes, load=load, kind=kind
        )

    def _await_load(self, key: str, in_flight: _Loading) -> Any:
        if not in_flight.event.wait(timeout=LOAD_WAIT_TIMEOUT_SECONDS):
            raise ModelLoadError(
                f"waited {LOAD_WAIT_TIMEOUT_SECONDS:.0f}s for another thread to load "
                f"{key!r} and it never finished. Either the load is pathologically slow "
                f"on this host or the loading thread died without clearing its flag; "
                f"either way this worker cannot classify until it is restarted."
            )
        if in_flight.error is not None:
            # Re-raised, not wrapped in a fresh traceback: every waiter should
            # see the *same* failure the loader saw, so a log full of "load
            # failed" lines has one cause rather than N indistinguishable ones.
            raise in_flight.error
        return in_flight.value

    def _perform_load(
        self,
        key: str,
        in_flight: _Loading,
        *,
        footprint_bytes: int,
        load: Callable[[], Any],
        kind: str,
    ) -> Any:
        started = time.monotonic()
        try:
            self._make_room(key, footprint_bytes=footprint_bytes)
            value = load()
            if value is None:
                raise ModelLoadError(
                    f"the loader for {key!r} returned None; a registry entry that is "
                    f"None is indistinguishable from a miss and would be reloaded on "
                    f"every single call"
                )
        except BaseException as exc:
            in_flight.error = exc
            with self._lock:
                self._loading.pop(key, None)
            in_flight.event.set()
            metrics.perception_model_loads_total.labels(kind=kind, outcome="failed").inc()
            log.error(
                "perception_model_load_failed",
                key=key,
                kind=kind,
                error_type=type(exc).__name__,
                runbook="docs/runbooks/perception-model-unavailable.md",
            )
            raise

        elapsed = time.monotonic() - started
        now = time.monotonic()
        with self._lock:
            self._entries[key] = _Entry(
                key=key,
                value=value,
                footprint_bytes=footprint_bytes,
                loaded_at=now,
                last_used=now,
                uses=1,
            )
            self._loading.pop(key, None)
            resident = sum(entry.footprint_bytes for entry in self._entries.values())
        in_flight.value = value
        in_flight.event.set()

        metrics.perception_model_loads_total.labels(kind=kind, outcome="ok").inc()
        metrics.perception_model_load_seconds.labels(kind=kind).observe(elapsed)
        metrics.perception_resident_bytes.set(resident)
        metrics.perception_models_resident.set(len(self._entries))
        log.info(
            "perception_model_loaded",
            key=key,
            kind=kind,
            seconds=round(elapsed, 3),
            footprint_mb=round(footprint_bytes / 1_048_576, 1),
            resident_mb=round(resident / 1_048_576, 1),
            ceiling_mb=round(self._max_resident_bytes / 1_048_576, 1),
        )
        return value

    # -- eviction ---------------------------------------------------------

    def _make_room(self, key: str, *, footprint_bytes: int) -> None:
        """Evict idle entries until ``footprint_bytes`` fits, or refuse.

        Called with the lock *not* held, and it takes the lock itself in short
        bursts — the loading flag already excludes a second load of this key, and
        holding the registry lock across an eviction loop would block every
        cache hit in the process for the duration.
        """
        if footprint_bytes > self._max_resident_bytes:
            raise ModelCapacityError(
                f"{key!r} declares {footprint_bytes / 1_048_576:.0f} MB, which exceeds "
                f"the whole registry ceiling of "
                f"{self._max_resident_bytes / 1_048_576:.0f} MB. Nothing can be evicted "
                f"to make this fit; raise NEMESIS_PERCEPTION__MAX_RESIDENT_MB or run "
                f"this model in a container sized for it."
            )

        while True:
            with self._lock:
                resident = sum(entry.footprint_bytes for entry in self._entries.values())
                if resident + footprint_bytes <= self._max_resident_bytes:
                    return
                idle = [entry for entry in self._entries.values() if entry.borrowers == 0]
                if not idle:
                    raise ModelCapacityError(
                        f"loading {key!r} needs {footprint_bytes / 1_048_576:.0f} MB but "
                        f"{resident / 1_048_576:.0f} MB of a "
                        f"{self._max_resident_bytes / 1_048_576:.0f} MB ceiling is resident "
                        f"and every entry is in use. Refusing rather than evicting a model "
                        f"mid-inference, which would not free the memory anyway."
                    )
                victim = min(idle, key=lambda entry: entry.last_used)
                del self._entries[victim.key]
                remaining = len(self._entries)
            metrics.perception_model_evictions_total.inc()
            metrics.perception_models_resident.set(remaining)
            log.info(
                "perception_model_evicted",
                evicted=victim.key,
                to_load=key,
                freed_mb=round(victim.footprint_bytes / 1_048_576, 1),
                uses=victim.uses,
                note=(
                    "the next request for the evicted key pays a full cold start; "
                    "repeated evictions mean the ceiling is below the working set"
                ),
            )

    # -- lifecycle --------------------------------------------------------

    def borrow(self, key: str) -> None:
        """Mark an entry as in use so eviction skips it.

        Deliberately explicit rather than inferred from ``get``. A borrower that
        holds a model across a forty-second Whisper pass and a caller that read
        one attribute look identical from inside the registry, and only the first
        one must block eviction. The stage borrows around its inference; nothing
        else needs to.
        """
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                entry.borrowers += 1

    def release(self, key: str) -> None:
        """Drop one borrow. Never below zero — see the note in ``borrow``."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry.borrowers > 0:
                entry.borrowers -= 1

    def drop(self, key: str) -> bool:
        """Forget one entry. Returns whether it was resident.

        For the operator path — a model file replaced on disk, a prompt matrix
        invalidated by hand — and for tests. Not part of any request path: an
        entry that a request can drop is an entry a request can thrash.
        """
        with self._lock:
            removed = self._entries.pop(key, None) is not None
            remaining = len(self._entries)
        metrics.perception_models_resident.set(remaining)
        return removed

    def clear(self) -> None:
        """Forget everything. Worker shutdown and test teardown only."""
        with self._lock:
            self._entries.clear()
            self._loading.clear()
        metrics.perception_models_resident.set(0)
        metrics.perception_resident_bytes.set(0)

    def snapshot(self) -> tuple[dict[str, Any], ...]:
        """What is resident, for the readiness surface and the gate.

        Values are deliberately omitted — this is what an operator needs to
        answer "did the model I expect actually load", and serialising a torch
        module into a health response is not a feature anybody wants twice.
        """
        with self._lock:
            entries = sorted(self._entries.values(), key=lambda entry: entry.key)
            return tuple(
                {
                    "key": entry.key,
                    "footprint_bytes": entry.footprint_bytes,
                    "borrowers": entry.borrowers,
                    "uses": entry.uses,
                    "resident_seconds": round(time.monotonic() - entry.loaded_at, 1),
                }
                for entry in entries
            )


def _default_registry() -> ModelRegistry:
    from nemesis.config import get_settings

    return ModelRegistry(max_resident_bytes=get_settings().perception.max_resident_bytes)


#: The process's registry. A module-level singleton for the same reason the
#: encoder registry is one: two registries would each enforce the ceiling
#: against their own half of the resident set, and the sum — which is what the
#: container's memory cap actually applies to — would be enforced by nobody.
REGISTRY: Final[ModelRegistry] = _default_registry()


__all__ = ["LOAD_WAIT_TIMEOUT_SECONDS", "REGISTRY", "ModelRegistry"]
