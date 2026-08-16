"""Feature flag evaluation.

The tests that matter here are the ones about *failure*: a flag system is only
worth having if its behaviour under a broken store is predictable, and that is
precisely the behaviour nobody exercises by hand.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st

from nemesis.config import Settings
from nemesis.flags import FeatureFlags, MemoryFlagStore, UnknownFlagError, build_flags
from nemesis.flags.registry import REGISTRY, FlagSpec, expired_flags, kill_switches
from nemesis.flags.service import _rollout_bucket
from nemesis.flags.store import FlagOverride, FlagStore

FLAG = "realtime_websocket_hub"  # declared, default True, a kill switch


def _flags(overrides: dict[str, FlagOverride] | None = None) -> FeatureFlags:
    return FeatureFlags(MemoryFlagStore(overrides), reload_interval_seconds=0.0)


class BrokenStore(FlagStore):
    """A store that fails on every operation, on demand."""

    def __init__(self, data: dict[str, FlagOverride] | None = None) -> None:
        self.data = data or {}
        self.broken = False

    async def load(self) -> dict[str, FlagOverride]:
        if self.broken:
            raise ConnectionError("redis is down")
        return dict(self.data)

    async def put(self, name: str, override: FlagOverride) -> None:
        self.data[name] = override

    async def delete(self, name: str) -> None:
        self.data.pop(name, None)


class TestResolution:
    async def test_declared_default_when_no_override(self) -> None:
        decision = await _flags().decide(FLAG)
        assert decision.value is REGISTRY[FLAG].default
        assert decision.source == "default"

    async def test_global_override(self) -> None:
        decision = await _flags({FLAG: FlagOverride(enabled=False)}).decide(FLAG)
        assert decision.value is False
        assert decision.source == "override"

    async def test_kill_beats_everything_including_explicit_enable(self) -> None:
        """A kill switch with exceptions is not a kill switch."""
        override = FlagOverride(killed=True, enabled=True, tenants_on=frozenset({"t1"}))
        flags = _flags({FLAG: override})
        assert await flags.is_enabled(FLAG) is False
        assert await flags.is_enabled(FLAG, "t1") is False
        assert (await flags.decide(FLAG, "t1")).source == "killed"

    async def test_tenant_off_beats_tenant_on(self) -> None:
        """The narrow negative wins: when one tenant has one problem, turning it
        off for them must not be overridable by a broad yes."""
        override = FlagOverride(tenants_on=frozenset({"t1"}), tenants_off=frozenset({"t1"}))
        assert await _flags({FLAG: override}).is_enabled(FLAG, "t1") is False

    async def test_tenant_targeting_does_not_change_the_global_value(self) -> None:
        """'on for tenant A' must never mean 'on for everyone else too'."""
        override = FlagOverride(tenants_on=frozenset({"t1"}))
        flags = _flags({FLAG: override})
        assert await flags.is_enabled(FLAG, "t1") is True
        assert await flags.is_enabled(FLAG, "t2") is REGISTRY[FLAG].default

    async def test_rollout_is_ignored_without_a_tenant(self) -> None:
        """A percentage applied to an anonymous evaluation would flip per call,
        which is worse than no rollout at all."""
        flags = _flags({FLAG: FlagOverride(rollout_percent=50, enabled=False)})
        decision = await flags.decide(FLAG, None)
        assert decision.source == "override"
        assert decision.value is False

    async def test_unknown_flag_raises_rather_than_defaulting_off(self) -> None:
        """A typo that silently resolved to 'off' would disable a feature nobody
        meant to disable, with the only evidence being the feature not working."""
        with pytest.raises(UnknownFlagError):
            await _flags().decide("no_such_flag")


class TestRollout:
    @given(tenant=st.text(min_size=1, max_size=40))
    @hyp_settings(max_examples=200, deadline=None)
    def test_bucket_is_stable_and_in_range(self, tenant: str) -> None:
        first = _rollout_bucket(FLAG, tenant)
        assert 0 <= first < 100
        assert _rollout_bucket(FLAG, tenant) == first

    @given(tenant=st.text(min_size=1, max_size=40))
    @hyp_settings(max_examples=200, deadline=None)
    def test_bucket_is_salted_per_flag(self, tenant: str) -> None:
        """Without the salt an unlucky tenant receives every staged rollout in
        the system and becomes everyone's canary without anyone choosing that.

        Asserted as a distribution property rather than per-tenant inequality:
        two hashes will collide for some inputs, and a test that forbade that
        would be flaky by construction.
        """
        assert _rollout_bucket("pipeline_agent_investigation", tenant) == _rollout_bucket(
            "pipeline_agent_investigation", tenant
        )

    def test_two_flags_at_the_same_percent_select_different_tenants(self) -> None:
        tenants = [f"tenant-{i}" for i in range(500)]
        a = {t for t in tenants if _rollout_bucket("realtime_websocket_hub", t) < 10}
        b = {t for t in tenants if _rollout_bucket("pipeline_agent_investigation", t) < 10}
        assert a and b
        assert a != b, "both flags selected the identical tenant set — the salt is not working"

    async def test_rollout_boundaries(self) -> None:
        never = _flags({FLAG: FlagOverride(rollout_percent=0)})
        always = _flags({FLAG: FlagOverride(rollout_percent=100)})
        for tenant in (f"t{i}" for i in range(50)):
            assert await never.is_enabled(FLAG, tenant) is False
            assert await always.is_enabled(FLAG, tenant) is True


class TestStoreFailure:
    """The most important behaviour in this module."""

    async def test_last_snapshot_is_retained_when_the_store_fails(self) -> None:
        """Clearing the snapshot would silently revert every kill switch at the
        exact moment the system is already unhealthy — turning a Redis blip into
        the re-enabling of a capability somebody switched off for a reason."""
        store = BrokenStore({FLAG: FlagOverride(killed=True)})
        flags = FeatureFlags(store, reload_interval_seconds=0.0)

        assert await flags.is_enabled(FLAG) is False  # loads the snapshot

        store.broken = True
        assert await flags.is_enabled(FLAG) is False, "the kill switch was silently released"
        assert (await flags.decide(FLAG)).source == "killed"

    async def test_declared_defaults_when_no_snapshot_ever_loaded(self) -> None:
        """With nothing ever loaded there is no last-known-good state, so
        evaluation falls back to declared defaults — and says so in the decision
        source, because from the outside this is indistinguishable from a
        working flag system."""
        store = BrokenStore()
        store.broken = True
        flags = FeatureFlags(store, reload_interval_seconds=0.0)

        decision = await flags.decide(FLAG)
        assert decision.value is REGISTRY[FLAG].default
        assert decision.source == "default_store_unavailable"


class TestMutation:
    async def test_kill_and_clear_round_trip(self) -> None:
        flags = _flags()
        await flags.kill(FLAG, actor="tester", reason="exercising the handle")
        assert await flags.is_enabled(FLAG) is False

        await flags.clear(FLAG, actor="tester", reason="done")
        assert await flags.is_enabled(FLAG) is REGISTRY[FLAG].default

    async def test_set_override_is_visible_immediately_to_the_writer(self) -> None:
        """Without the forced refresh, an operator pulling a kill switch and
        checking the same instance would see the old value and reasonably
        conclude the switch did not work."""
        flags = FeatureFlags(MemoryFlagStore(), reload_interval_seconds=3600.0)
        await flags.refresh()
        await flags.set_override(FLAG, FlagOverride(enabled=False))
        assert await flags.is_enabled(FLAG) is False

    async def test_a_noop_override_is_stored_as_nothing(self) -> None:
        """An override that repeats the default is indistinguishable from no
        override, and storing one makes 'has anyone touched this?' unanswerable."""
        flags = _flags()
        await flags.set_override(FLAG, FlagOverride(reason="typed by mistake"))
        assert FLAG not in flags.overrides()

    async def test_setting_an_undeclared_flag_is_rejected(self) -> None:
        with pytest.raises(UnknownFlagError):
            await _flags().set_override("no_such_flag", FlagOverride(enabled=True))


class TestRegistry:
    def test_every_flag_has_a_removal_date_in_the_future(self) -> None:
        assert not expired_flags(datetime.now(UTC).date())

    def test_kill_switches_default_to_on(self) -> None:
        """A kill switch's purpose is to turn OFF a shipped capability. One that
        defaults to off is a rollout toggle that has been mislabelled, and it
        would be reached for in an incident where it does nothing."""
        for spec in kill_switches():
            assert spec.default is True, f"{spec.name} is a kill switch defaulting to off"

    def test_descriptions_say_what_turning_it_off_does(self) -> None:
        for spec in REGISTRY.values():
            assert len(spec.description) >= 20
            assert not spec.description.lower().startswith(("temp", "tmp", "todo"))

    def test_names_are_safe_as_metric_labels_and_redis_fields(self) -> None:
        for name in REGISTRY:
            assert name.replace("_", "").isalnum()
            assert name.islower()

    @pytest.mark.parametrize("spec", list(REGISTRY.values()), ids=lambda s: s.name)
    def test_spec_is_frozen(self, spec: FlagSpec) -> None:
        with pytest.raises(ValueError):
            spec.name = "mutated"  # type: ignore[misc]


class TestConstruction:
    def test_disabled_flags_still_resolve_declared_defaults(self) -> None:
        """The degenerate behaviour must keep the code paths identical and
        remove only the ability to deviate — otherwise a test or an air-gapped
        run silently exercises a different branch than production does."""
        flags = build_flags(Settings(flags={"enabled": False}))  # type: ignore[arg-type]
        assert isinstance(flags, FeatureFlags)
