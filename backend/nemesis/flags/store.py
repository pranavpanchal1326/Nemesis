"""Override storage.

Redis, not Postgres, and not for convenience. Phase 2 owns the schema and the
event store; adding a flags table here would either be thrown away or would
quietly pre-empt a design decision that belongs to a later phase. Redis is
already a hard dependency, already healthchecked, and a flag override is
exactly the kind of small, hot, non-authoritative state it is good at.

The *authoritative* record of a flag change is not this store — it is the audit
event written when Phase 2 lands. Until then a change carries an actor and a
reason inline, which is the most that can honestly be claimed.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field


class FlagOverride(BaseModel):
    """A stored deviation from a flag's declared default.

    Every field is optional deviation, not restatement: an override that simply
    repeats the default is indistinguishable from no override, and storing one
    would make "has anyone touched this?" unanswerable.
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool | None = None
    """Global on/off. ``None`` means "defer to the declared default"."""

    killed: bool = False
    """The emergency handle. Wins over every other field, including
    ``tenants_on``. A kill switch with exceptions is not a kill switch."""

    tenants_on: frozenset[str] = frozenset()
    tenants_off: frozenset[str] = frozenset()
    """Tenant targeting. ``tenants_off`` beats ``tenants_on``: when a specific
    tenant is having a specific problem, the narrow negative must win over the
    broad positive, which is the direction that fails safe."""

    rollout_percent: int | None = Field(default=None, ge=0, le=100)
    """Staged rollout by stable tenant hash. Only consulted when a tenant is
    supplied — a rollout percentage applied to an anonymous evaluation would
    flip on every call, which is worse than no rollout at all."""

    reason: str = ""
    actor: str = ""
    """Who changed it and why. Not enforced as non-empty here because a kill
    switch pulled at 3am must never fail on a validation error — the CLI asks
    for both, and the ops listing shows an empty reason as a gap."""

    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def is_noop(self) -> bool:
        """True when this override deviates from nothing and can be dropped."""
        return (
            self.enabled is None
            and not self.killed
            and not self.tenants_on
            and not self.tenants_off
            and self.rollout_percent is None
        )


class FlagStore(ABC):
    """Override persistence."""

    @abstractmethod
    async def load(self) -> dict[str, FlagOverride]:
        """Every override, in one round trip.

        One call rather than a lookup per flag on purpose: evaluation happens on
        the request path, and a per-flag round trip would put Redis latency into
        every branch the system takes.
        """

    @abstractmethod
    async def put(self, name: str, override: FlagOverride) -> None: ...

    @abstractmethod
    async def delete(self, name: str) -> None: ...


class MemoryFlagStore(FlagStore):
    """In-process store, for tests and for a stack with no Redis."""

    def __init__(self, initial: dict[str, FlagOverride] | None = None) -> None:
        self._data: dict[str, FlagOverride] = dict(initial or {})

    async def load(self) -> dict[str, FlagOverride]:
        return dict(self._data)

    async def put(self, name: str, override: FlagOverride) -> None:
        self._data[name] = override

    async def delete(self, name: str) -> None:
        self._data.pop(name, None)


class RedisFlagStore(FlagStore):
    """Redis-backed store.

    Overrides live in a single hash rather than one key per flag, so ``load()``
    is a single ``HGETALL`` regardless of how many flags exist, and so a partial
    read is impossible — a set of flags read halfway through somebody else's
    two-flag change is a state the system was never designed for.
    """

    def __init__(self, redis_url: str, key: str) -> None:
        self._redis_url = redis_url
        self._key = key
        self._client: object | None = None

    def _get_client(self) -> object:
        if self._client is None:
            from redis.asyncio import Redis

            self._client = Redis.from_url(self._redis_url, decode_responses=True)
        return self._client

    async def load(self) -> dict[str, FlagOverride]:
        client = self._get_client()
        raw: dict[str, str] = await client.hgetall(self._key)  # type: ignore[attr-defined]
        overrides: dict[str, FlagOverride] = {}
        for name, payload in raw.items():
            try:
                overrides[name] = FlagOverride.model_validate_json(payload)
            except ValueError:
                # A single unparseable entry must not blind the system to every
                # other flag — including a kill switch someone is relying on
                # right now. Skip it; the ops listing reports the discrepancy
                # because the stored name has no resolved override.
                continue
        return overrides

    async def put(self, name: str, override: FlagOverride) -> None:
        client = self._get_client()
        payload = override.model_dump(mode="json")
        # frozenset is not JSON-serialisable and its iteration order is not
        # stable; sorting makes the stored value diffable between two reads.
        payload["tenants_on"] = sorted(override.tenants_on)
        payload["tenants_off"] = sorted(override.tenants_off)
        await client.hset(self._key, name, json.dumps(payload))  # type: ignore[attr-defined]

    async def delete(self, name: str) -> None:
        client = self._get_client()
        await client.hdel(self._key, name)  # type: ignore[attr-defined]

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()  # type: ignore[attr-defined]
            self._client = None
