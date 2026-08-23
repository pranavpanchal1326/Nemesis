"""The model registry: one loader, one copy, one bounded ceiling.

Every property here is a claim about *concurrency* or *memory*, and both are
things that look fine in a single-threaded test and kill a container at three in
the morning. So the single-flight tests use real threads and a real barrier
rather than asserting on a call count, and the ceiling tests use declared
footprints large enough that the arithmetic is unambiguous.
"""

from __future__ import annotations

import threading
import time

import pytest

from nemesis.perception.errors import ModelCapacityError, ModelLoadError
from nemesis.perception.registry import ModelRegistry

MEGABYTE = 1024 * 1024


@pytest.fixture
def registry() -> ModelRegistry:
    return ModelRegistry(max_resident_bytes=100 * MEGABYTE)


def test_a_hit_returns_the_same_object_and_does_not_reload(registry: ModelRegistry) -> None:
    loads = 0

    def load() -> object:
        nonlocal loads
        loads += 1
        return object()

    first = registry.get("k", footprint_bytes=MEGABYTE, load=load)
    second = registry.get("k", footprint_bytes=MEGABYTE, load=load)

    assert first is second
    assert loads == 1


def test_concurrent_misses_are_coalesced_onto_one_load(registry: ModelRegistry) -> None:
    """The failure this class exists for, reproduced.

    Four Celery children pick up four image complaints in the same second and
    each asks for CLIP. Without the single-flight guard all four call
    ``create_model`` simultaneously, each allocates ~600 MB while the others do,
    and the container's memory cap kills the pool — after which Celery
    redelivers the four tasks and it happens again.

    The barrier makes the race deterministic instead of likely: every thread is
    held until all of them have arrived, so they enter ``get`` together.
    """
    threads = 8
    barrier = threading.Barrier(threads)
    loads = 0
    lock = threading.Lock()

    def load() -> object:
        nonlocal loads
        with lock:
            loads += 1
        # Long enough that a second loader would certainly have started by now.
        time.sleep(0.15)
        return object()

    results: list[object] = [None] * threads

    def worker(index: int) -> None:
        barrier.wait()
        results[index] = registry.get("clip", footprint_bytes=MEGABYTE, load=load)

    workers = [threading.Thread(target=worker, args=(index,)) for index in range(threads)]
    for thread in workers:
        thread.start()
    for thread in workers:
        thread.join()

    assert loads == 1, "the guard let a second thread start a duplicate load"
    assert all(item is results[0] for item in results), "callers got different objects"


def test_a_load_for_one_key_does_not_block_a_hit_on_another(registry: ModelRegistry) -> None:
    """ "Never load twice" and "never serialise unrelated work" are both true.

    A thread loading Whisper must not block a thread that wants the
    already-resident CLIP — that turns a 40-second cold start into 40 seconds of
    stalled classification for every complaint in flight.
    """
    registry.get("clip", footprint_bytes=MEGABYTE, load=object)
    started = threading.Event()
    release = threading.Event()

    def slow_load() -> object:
        started.set()
        release.wait(timeout=5)
        return object()

    loader = threading.Thread(
        target=lambda: registry.get("whisper", footprint_bytes=MEGABYTE, load=slow_load)
    )
    loader.start()
    assert started.wait(timeout=5)

    began = time.monotonic()
    registry.get("clip", footprint_bytes=MEGABYTE, load=object)
    elapsed = time.monotonic() - began

    release.set()
    loader.join()
    assert elapsed < 0.5, f"a hit waited {elapsed:.2f}s behind an unrelated load"


def test_a_failed_load_is_reraised_to_every_waiter_and_leaves_no_flag(
    registry: ModelRegistry,
) -> None:
    """A loader that dies must not park every subsequent caller forever."""

    def broken() -> object:
        raise ModelLoadError("the weights are truncated")

    with pytest.raises(ModelLoadError):
        registry.get("k", footprint_bytes=MEGABYTE, load=broken)

    # The in-flight flag was cleared, so the next caller loads rather than waits.
    assert registry.get("k", footprint_bytes=MEGABYTE, load=object) is not None


def test_a_loader_returning_none_is_refused(registry: ModelRegistry) -> None:
    """``None`` in the map is indistinguishable from a miss and reloads forever."""
    with pytest.raises(ModelLoadError, match="returned None"):
        registry.get("k", footprint_bytes=MEGABYTE, load=lambda: None)


def test_a_model_larger_than_the_whole_ceiling_is_refused_before_it_allocates(
    registry: ModelRegistry,
) -> None:
    """Checked *before* the load, not after — afterwards the OOM already happened."""
    loaded = False

    def load() -> object:
        nonlocal loaded
        loaded = True
        return object()

    with pytest.raises(ModelCapacityError, match="exceeds"):
        registry.get("huge", footprint_bytes=200 * MEGABYTE, load=load)
    assert not loaded


def test_eviction_frees_room_for_a_new_load(registry: ModelRegistry) -> None:
    registry.get("a", footprint_bytes=60 * MEGABYTE, load=object)
    registry.get("b", footprint_bytes=60 * MEGABYTE, load=object)

    assert registry.is_resident("b")
    assert not registry.is_resident("a"), "the idle entry should have been evicted"


def test_eviction_refuses_rather_than_evicting_a_model_mid_inference(
    registry: ModelRegistry,
) -> None:
    """The ceiling is a refusal, not an eviction cascade.

    Evicting a borrowed model does not free the memory anyway — the borrower's
    reference keeps it alive and the registry has merely lost track of it — and
    the thrash that follows collapses throughput to the reload time with nothing
    in any log naming the cause.
    """
    registry.get("busy", footprint_bytes=60 * MEGABYTE, load=object)
    registry.borrow("busy")

    with pytest.raises(ModelCapacityError, match="every entry is in use"):
        registry.get("new", footprint_bytes=60 * MEGABYTE, load=object)

    registry.release("busy")
    assert registry.get("new", footprint_bytes=60 * MEGABYTE, load=object) is not None


def test_release_never_drops_the_borrow_count_below_zero(registry: ModelRegistry) -> None:
    registry.get("k", footprint_bytes=MEGABYTE, load=object)
    registry.release("k")
    registry.release("k")
    registry.borrow("k")
    registry.release("k")

    entry = next(item for item in registry.snapshot() if item["key"] == "k")
    assert entry["borrowers"] == 0


def test_a_non_positive_footprint_is_a_caller_error(registry: ModelRegistry) -> None:
    """A declared footprint of zero makes the ceiling unenforceable for that entry."""
    with pytest.raises(ValueError, match="non-positive footprint"):
        registry.get("k", footprint_bytes=0, load=object)


def test_the_snapshot_names_what_is_resident_without_serialising_it(
    registry: ModelRegistry,
) -> None:
    """What an operator needs to answer "did the model I expect actually load".

    Values are deliberately absent: serialising a torch module into a health
    response is not a feature anybody wants twice.
    """
    registry.get("clip", footprint_bytes=2 * MEGABYTE, load=object)
    (entry,) = registry.snapshot()

    assert entry["key"] == "clip"
    assert entry["footprint_bytes"] == 2 * MEGABYTE
    assert "value" not in entry
