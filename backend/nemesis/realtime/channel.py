"""Redis pub/sub between the relay and whichever API process holds the socket.

A client is connected to one API replica; the event that concerns it was written
by a worker and published by the relay. Something has to cross that gap, and
Redis is already a hard dependency (broker, result backend, flag store), so this
adds no new failure mode to the deployment — only a new consequence for one that
already existed.

**Pub/sub, not a stream, and the trade is stated rather than hidden.** Redis
pub/sub is fire-and-forget: an event published while no API process is listening
is gone. That is acceptable *here* and nowhere else, because durability already
lives one layer down — the event is in the log and its outbox row records
whether it was dispatched. What a subscriber misses during a restart it recovers
by asking for a cursor replay, which reads the outbox. Using a Redis Stream
instead would put a second durable queue next to a durable log, with its own
trimming policy and its own way to disagree with the database.

Channels are **per tenant**. A process subscribes only to the tenants it
currently has clients for, so cross-tenant delivery is not prevented by a filter
somebody has to remember to write — the bytes never arrive.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any, Final

import redis.asyncio as redis

from nemesis.observability.logging import get_logger

log = get_logger(__name__)

_CHANNEL_PREFIX: Final = "nemesis:pipeline-events"


def channel_name(tenant_id: uuid.UUID) -> str:
    return f"{_CHANNEL_PREFIX}:{tenant_id}"


class RedisEventChannel:
    """Publisher side. Held by the relay."""

    def __init__(self, redis_url: str) -> None:
        self._url = redis_url
        self._client: redis.Redis[str] | None = None

    def _connect(self) -> redis.Redis[str]:
        if self._client is None:
            self._client = redis.from_url(self._url, decode_responses=True)
        return self._client

    async def publish(self, tenant_id: uuid.UUID, envelope: dict[str, Any]) -> int:
        """Publish one envelope. Returns the number of subscribers that received it.

        Zero subscribers is **not** an error and is not retried. Nobody is
        watching this tenant's map right now; the event is in the log, the
        outbox row is about to be marked dispatched, and a client that connects
        later resumes from its cursor. Treating zero as a failure would mean the
        relay never drains on a deployment with no browser open.
        """
        return int(await self._connect().publish(channel_name(tenant_id), json.dumps(envelope)))

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()  # type: ignore[attr-defined]
            self._client = None


class RedisEventSubscription:
    """Subscriber side. One per API process, multiplexed across tenants."""

    def __init__(self, redis_url: str) -> None:
        self._url = redis_url
        self._client: redis.Redis[str] | None = None
        self._pubsub: Any | None = None

    async def subscribe(self, tenant_id: uuid.UUID) -> None:
        if self._pubsub is None:
            self._client = redis.from_url(self._url, decode_responses=True)
            self._pubsub = self._client.pubsub(ignore_subscribe_messages=True)
        await self._pubsub.subscribe(channel_name(tenant_id))

    async def unsubscribe(self, tenant_id: uuid.UUID) -> None:
        if self._pubsub is not None:
            await self._pubsub.unsubscribe(channel_name(tenant_id))

    async def listen(self) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Yield ``(channel, envelope)`` until cancelled.

        A message that will not parse is logged and dropped rather than raising.
        The alternative — letting it propagate — takes the whole fan-out loop
        down for every tenant because one publisher wrote something malformed.
        """
        if self._pubsub is None:  # pragma: no cover — subscribe() precedes listen()
            return
        async for message in self._pubsub.listen():
            if message.get("type") != "message":
                continue
            try:
                envelope = json.loads(message["data"])
            except (ValueError, KeyError, TypeError):
                log.warning("realtime_message_unparseable", channel=message.get("channel"))
                continue
            if isinstance(envelope, dict):
                yield str(message["channel"]), envelope

    async def close(self) -> None:
        if self._pubsub is not None:
            await self._pubsub.aclose()
            self._pubsub = None
        if self._client is not None:
            await self._client.aclose()  # type: ignore[attr-defined]
            self._client = None
