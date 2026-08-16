"""Feature flags against a real Redis, the real CLI, and the real endpoint.

The unit tests in `test_feature_flags.py` cover resolution logic against an
in-memory store. These cover the parts that only break in contact with something
real: JSON round-tripping through a Redis hash, the CLI an operator actually
types during an incident, and the ops endpoint they read when they have no shell.

Redis rather than a fake, for the same reason the datastore tests use a real
Postgres: the two things most likely to break are serialisation and the store
itself, and neither is meaningfully testable against a mock.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient

from nemesis.config import Settings, get_settings
from nemesis.flags import FeatureFlags, MemoryFlagStore, build_flags, get_flags, reset_flags
from nemesis.flags import __main__ as cli
from nemesis.flags.registry import REGISTRY
from nemesis.flags.store import FlagOverride, RedisFlagStore

FLAG = "realtime_websocket_hub"


def _redis_reachable(url: str) -> bool:
    try:
        from redis import Redis

        Redis.from_url(url, socket_connect_timeout=2).ping()
        return True
    except Exception:
        return False


_REDIS_URL = get_settings().redis_url

redis_required = pytest.mark.skipif(
    not _redis_reachable(_REDIS_URL),
    reason="no Redis reachable; run `docker compose up -d redis`",
)


@pytest.fixture
async def redis_store() -> AsyncIterator[RedisFlagStore]:
    """A store on a throwaway key, dropped afterwards.

    Per-test key isolation rather than flushing: this Redis is also the live
    Celery broker for the running stack, and a test that flushed it would
    silently destroy queued work.
    """
    key = f"nemesis:test_flags:{uuid.uuid4().hex[:12]}"
    store = RedisFlagStore(_REDIS_URL, key)
    try:
        yield store
    finally:
        client = store._get_client()
        await client.delete(key)  # type: ignore[attr-defined]
        await store.close()


@redis_required
class TestRedisStore:
    async def test_round_trip_preserves_every_field(self, redis_store: RedisFlagStore) -> None:
        override = FlagOverride(
            enabled=False,
            tenants_on=frozenset({"t2", "t1"}),
            tenants_off=frozenset({"t3"}),
            rollout_percent=25,
            actor="tester",
            reason="round trip",
        )
        await redis_store.put(FLAG, override)

        loaded = (await redis_store.load())[FLAG]
        assert loaded.enabled is False
        assert loaded.tenants_on == frozenset({"t1", "t2"})
        assert loaded.tenants_off == frozenset({"t3"})
        assert loaded.rollout_percent == 25
        assert loaded.actor == "tester"

    async def test_kill_survives_the_store(self, redis_store: RedisFlagStore) -> None:
        flags = FeatureFlags(redis_store, reload_interval_seconds=0.0)
        await flags.kill(FLAG, actor="tester", reason="incident drill")

        # A fresh evaluator, as a restarted process would be.
        reloaded = FeatureFlags(redis_store, reload_interval_seconds=0.0)
        assert await reloaded.is_enabled(FLAG) is False
        assert (await reloaded.decide(FLAG)).source == "killed"

    async def test_delete_restores_the_declared_default(self, redis_store: RedisFlagStore) -> None:
        await redis_store.put(FLAG, FlagOverride(enabled=False))
        await redis_store.delete(FLAG)
        assert FLAG not in await redis_store.load()

    async def test_an_unparseable_entry_does_not_blind_the_rest(
        self, redis_store: RedisFlagStore
    ) -> None:
        """One corrupt value must not hide every other flag — including a kill
        switch somebody is relying on right now."""
        await redis_store.put(FLAG, FlagOverride(killed=True))
        client = redis_store._get_client()
        await client.hset(redis_store._key, "pipeline_agent_investigation", "{not json")  # type: ignore[attr-defined]

        loaded = await redis_store.load()
        assert loaded[FLAG].killed is True
        assert "pipeline_agent_investigation" not in loaded


class TestConstruction:
    def test_redis_store_when_enabled(self) -> None:
        flags = build_flags(Settings(redis_url=_REDIS_URL))
        assert isinstance(flags._store, RedisFlagStore)

    def test_memory_store_when_disabled(self) -> None:
        flags = build_flags(Settings(flags={"enabled": False}))  # type: ignore[arg-type]
        assert isinstance(flags._store, MemoryFlagStore)

    def test_singleton_is_shared_and_resettable(self) -> None:
        """A per-request evaluator would reload on every call and reintroduce
        exactly the Redis round trip the snapshot exists to avoid."""
        reset_flags()
        first = get_flags()
        assert get_flags() is first
        reset_flags()
        assert get_flags() is not first


class TestOpsEndpoint:
    # Teardown of the flag store's Redis client is handled by the `client`
    # fixture in conftest, alongside the SQLAlchemy engine — both are
    # process-global resources the app creates lazily and ASGITransport never
    # tears down, because it does not run lifespan events.

    async def test_lists_every_declared_flag(self, client: AsyncClient) -> None:
        response = await client.get("/ops/flags")
        assert response.status_code == 200

        payload = response.json()
        names = {entry["name"] for entry in payload["flags"]}
        assert names == set(REGISTRY)
        assert payload["reload_interval_seconds"] > 0

    async def test_exposes_what_an_operator_needs_and_nothing_more(
        self, client: AsyncClient
    ) -> None:
        entry = (await client.get("/ops/flags")).json()["flags"][0]
        assert set(entry) == {
            "name",
            "enabled",
            "source",
            "kill_switch",
            "owner",
            "remove_by",
            "description",
        }

    async def test_kill_switches_are_identifiable(self, client: AsyncClient) -> None:
        """Under pressure the emergency handles must not be buried among
        rollout toggles."""
        flags = (await client.get("/ops/flags")).json()["flags"]
        assert any(entry["kill_switch"] for entry in flags)


class TestCli:
    """The commands an operator types during an incident.

    `build_flags` is patched to an in-memory store so the CLI is exercised end
    to end without a live Redis — the store itself is covered above.
    """

    @pytest.fixture(autouse=True)
    def _memory_backed(self, monkeypatch: pytest.MonkeyPatch) -> FeatureFlags:
        flags = FeatureFlags(MemoryFlagStore(), reload_interval_seconds=0.0)
        monkeypatch.setattr(cli, "build_flags", lambda _settings: flags)
        return flags

    def test_list_shows_owner_and_removal_date(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert cli.main(["list"]) == 0
        out = capsys.readouterr().out
        assert FLAG in out
        assert "kill switch" in out
        assert "remove by" in out

    def test_kill_then_list_reports_it(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert cli.main(["kill", FLAG, "--actor", "tester", "--reason", "drill"]) == 0
        capsys.readouterr()
        cli.main(["list"])
        assert "KILLED" in capsys.readouterr().out

    def test_clear_restores_the_default(self, capsys: pytest.CaptureFixture[str]) -> None:
        cli.main(["off", FLAG, "--actor", "tester"])
        cli.main(["clear", FLAG, "--actor", "tester"])
        capsys.readouterr()
        cli.main(["list"])
        assert "(default)" in capsys.readouterr().out

    def test_tenant_targeting_reports_its_scope(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert cli.main(["on", FLAG, "--tenant", "t1", "--tenant", "t2"]) == 0
        out = capsys.readouterr().out
        assert "t1" in out and "t2" in out

    def test_unknown_flag_is_rejected_rather_than_created(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A typo that silently created a key would leave the operator believing
        they had disabled something."""
        assert cli.main(["off", "no_such_flag"]) == 1
        assert "not a declared flag" in capsys.readouterr().out

    def test_kill_requires_attribution(self) -> None:
        """Mandatory for kill and nothing else: an entry with no actor is a
        mystery during the post-mortem, and the post-mortem is guaranteed."""
        with pytest.raises(SystemExit):
            cli.main(["kill", FLAG])
