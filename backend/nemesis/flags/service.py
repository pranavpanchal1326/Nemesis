"""Flag evaluation.

Resolution order, highest precedence first. It is short on purpose — every rule
added here is a rule an operator has to hold in their head while deciding
whether pulling a handle will actually do what they need at the moment they are
least able to reason carefully.

    1. killed                 → off. Wins over everything, including tenants_on.
    2. tenant in tenants_off  → off. The narrow negative beats the broad yes.
    3. tenant in tenants_on   → on.
    4. rollout_percent        → stable hash of (flag, tenant). Same tenant, same
                                answer, forever — a flag that flips per request
                                produces bug reports nobody can reproduce.
    5. override.enabled       → whatever was set.
    6. spec.default           → the declared value.

Evaluation reads an in-process snapshot refreshed on a TTL, so a flag check
costs a dict lookup rather than a Redis round trip. The cost of that choice is
stated plainly rather than hidden: **a change takes effect within one reload
interval, not instantly.** Five seconds is the default. Anything that must be
instant is not a flag, it is a code path with a runtime argument.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Literal

from nemesis.flags.registry import REGISTRY, FlagSpec, get_spec
from nemesis.flags.store import FlagOverride, FlagStore
from nemesis.observability import metrics
from nemesis.observability.logging import get_logger

log = get_logger(__name__)

DecisionSource = Literal[
    "killed",
    "tenant_off",
    "tenant_on",
    "rollout",
    "override",
    "default",
    "default_store_unavailable",
]


@dataclass(frozen=True, slots=True)
class FlagDecision:
    """A resolved value *and why*.

    The reason is not decoration. "Why is this feature off for this tenant?" is
    the question a flag system is asked most often, and a boolean return value
    cannot answer it — which is how teams end up reading Redis by hand during an
    incident to work out what their own tooling decided.
    """

    name: str
    value: bool
    source: DecisionSource

    @property
    def outcome_label(self) -> str:
        if self.source == "killed":
            return "killed"
        return "on" if self.value else "off"


def _rollout_bucket(flag: str, tenant_id: str) -> int:
    """Stable bucket in [0, 100) for a (flag, tenant) pair.

    Salted with the flag name so that two flags at 10 percent do not select the
    *same* 10 percent of tenants. Without the salt, an unlucky tenant receives
    every staged rollout in the system and becomes everyone's canary without
    anyone choosing that.

    SHA-256 rather than ``hash()``: Python's string hash is randomised per
    process, so buckets would differ between the API and a worker, and a tenant
    would see a feature appear and disappear depending on which process answered.
    """
    digest = hashlib.sha256(f"{flag}:{tenant_id}".encode()).digest()
    return int.from_bytes(digest[:4], "big") % 100


class FeatureFlags:
    """Evaluator over a TTL-cached snapshot of the override store."""

    def __init__(
        self,
        store: FlagStore,
        *,
        reload_interval_seconds: float = 5.0,
    ) -> None:
        self._store = store
        self._ttl = reload_interval_seconds
        self._overrides: dict[str, FlagOverride] = {}
        self._loaded_at: float | None = None

    # -- snapshot ----------------------------------------------------------

    async def refresh(self, *, force: bool = False) -> None:
        """Reload overrides if the snapshot is stale.

        On store failure the **previous snapshot is retained**, not cleared.
        Clearing would silently revert every kill switch to its default at the
        exact moment the system is already unhealthy — turning a Redis blip into
        the re-enabling of a capability somebody switched off for a reason.
        """
        now = time.monotonic()
        if not force and self._loaded_at is not None and (now - self._loaded_at) < self._ttl:
            return

        try:
            self._overrides = await self._store.load()
            self._loaded_at = now
        except Exception as exc:
            metrics.system_degradation_total.labels(
                dependency=metrics.Dependency.REDIS.value,
                reason="flag_store_unavailable",
            ).inc()
            if self._loaded_at is None:
                # Nothing has ever loaded, so there is no last-known-good state
                # and evaluation falls back to declared defaults. Logged at
                # error, not warning: for the duration, kill switches issued
                # before this process started are not in effect and nobody can
                # tell from the outside.
                log.error(
                    "flag_store_unreachable_no_snapshot",
                    error_type=type(exc).__name__,
                    consequence="evaluating declared defaults; kill switches not honoured",
                )
            else:
                log.warning(
                    "flag_store_refresh_failed",
                    error_type=type(exc).__name__,
                    snapshot_age_seconds=round(now - self._loaded_at, 1),
                )

        self._publish_state()

    def _publish_state(self) -> None:
        """Export every declared flag's resolved global state as a gauge.

        Exported for all declared flags, not just overridden ones, so a flag's
        absence from the dashboard means "not declared" rather than the far more
        ambiguous "not currently deviating".
        """
        for name, spec in REGISTRY.items():
            override = self._overrides.get(name)
            if override is not None and override.killed:
                value = -1.0
            else:
                explicit = None if override is None else override.enabled
                enabled = spec.default if explicit is None else explicit
                value = 1.0 if enabled else 0.0
            metrics.feature_flag_state.labels(flag=name).set(value)

    # -- evaluation --------------------------------------------------------

    async def decide(self, name: str, tenant_id: str | None = None) -> FlagDecision:
        spec = get_spec(name)
        await self.refresh()
        decision = self._resolve(spec, self._overrides.get(name), tenant_id)
        metrics.feature_flag_evaluations_total.labels(
            flag=name, outcome=decision.outcome_label
        ).inc()
        return decision

    async def is_enabled(self, name: str, tenant_id: str | None = None) -> bool:
        return (await self.decide(name, tenant_id)).value

    def _resolve(
        self,
        spec: FlagSpec,
        override: FlagOverride | None,
        tenant_id: str | None,
    ) -> FlagDecision:
        if override is None:
            source: DecisionSource = (
                "default" if self._loaded_at is not None else "default_store_unavailable"
            )
            return FlagDecision(spec.name, spec.default, source)

        if override.killed:
            return FlagDecision(spec.name, False, "killed")

        if tenant_id is not None:
            if tenant_id in override.tenants_off:
                return FlagDecision(spec.name, False, "tenant_off")
            if tenant_id in override.tenants_on:
                return FlagDecision(spec.name, True, "tenant_on")
            if override.rollout_percent is not None:
                in_rollout = _rollout_bucket(spec.name, tenant_id) < override.rollout_percent
                return FlagDecision(spec.name, in_rollout, "rollout")

        if override.enabled is not None:
            return FlagDecision(spec.name, override.enabled, "override")

        return FlagDecision(spec.name, spec.default, "default")

    # -- mutation ----------------------------------------------------------

    async def set_override(self, name: str, override: FlagOverride) -> None:
        """Write an override, then refresh immediately.

        The forced refresh means the writing process observes its own change at
        once. Without it, an operator pulling a kill switch and then checking the
        ops endpoint on the same instance would see the old value for up to a
        reload interval and reasonably conclude the switch did not work.
        """
        get_spec(name)  # reject a typo before it reaches the store
        if override.is_noop():
            await self._store.delete(name)
        else:
            await self._store.put(name, override)
        log.info(
            "feature_flag_override_set",
            flag=name,
            killed=override.killed,
            enabled=override.enabled,
            rollout_percent=override.rollout_percent,
            actor=override.actor or "unknown",
            reason=override.reason or "none given",
        )
        await self.refresh(force=True)

    async def kill(self, name: str, *, actor: str, reason: str) -> None:
        """Pull the emergency handle.

        Logged at warning, not info. This is the one flag operation that should
        show up when somebody greps the logs for what went wrong, and it should
        show up without them having to know it happened.
        """
        spec = get_spec(name)
        if not spec.kill_switch:
            log.warning(
                "killing_non_kill_switch_flag",
                flag=name,
                note="flag is not declared as a kill switch; proceeding, but "
                "consider whether the declaration is wrong",
            )
        existing = self._overrides.get(name, FlagOverride())
        await self.set_override(
            name,
            existing.model_copy(update={"killed": True, "actor": actor, "reason": reason}),
        )
        log.warning("feature_flag_killed", flag=name, actor=actor, reason=reason)

    async def clear(self, name: str, *, actor: str = "", reason: str = "") -> None:
        """Drop the override entirely, returning the flag to its declared default."""
        get_spec(name)
        await self._store.delete(name)
        log.info("feature_flag_override_cleared", flag=name, actor=actor, reason=reason)
        await self.refresh(force=True)

    # -- introspection -----------------------------------------------------

    async def snapshot(self) -> dict[str, FlagDecision]:
        """Resolved global state of every declared flag, for the ops listing."""
        await self.refresh()
        return {
            name: self._resolve(spec, self._overrides.get(name), None)
            for name, spec in REGISTRY.items()
        }

    def overrides(self) -> dict[str, FlagOverride]:
        return dict(self._overrides)
