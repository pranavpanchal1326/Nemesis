"""Per-process realtime service: Redis in, hub out, plus cursor replay.

One instance per API process, created on first use and closed by the lifespan
hook. It owns three things a route handler should not:

* the **Redis subscription**, multiplexed across every tenant this process
  currently has a client for — and unsubscribed when the last one leaves, so a
  process serving one tenant does not receive another tenant's bytes at all;
* the **listener task**, which is the single consumer of that subscription and
  hands envelopes to the hub's non-blocking fan-out;
* the **heartbeat**, one task for the whole process rather than one per
  connection.

**Why an application-level heartbeat rather than WebSocket ping frames.** A ping
is answered by the browser's socket implementation, so it proves the TCP path is
alive and says nothing about whether the page is still processing events. A
heartbeat envelope goes through the same queue as everything else, which means a
client that has stopped reading fills its queue on heartbeats alone and is shed
on schedule instead of lingering as a healthy-looking connection with a dead tab
behind it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import select

from nemesis.config import Settings, get_settings
from nemesis.db.models.event import Event
from nemesis.db.models.outbox import OutboxMessage
from nemesis.db.session import session_scope
from nemesis.observability.logging import get_logger
from nemesis.realtime.channel import RedisEventSubscription, channel_name
from nemesis.realtime.envelope import build_envelope
from nemesis.realtime.hub import CLOSE_GOING_AWAY, Connection, ConnectionHub, Sink
from nemesis.tenancy.context import tenant_scope

log = get_logger(__name__)

#: Seconds between heartbeat envelopes.
HEARTBEAT_SECONDS: Final = 20.0

#: Most envelopes a reconnecting client is replayed from its cursor. Beyond
#: this the client is told to resynchronise from ``GET /api/v1/complaints``
#: rather than being handed ten thousand animations to play — a replay that
#: takes longer than a page reload is worse than a page reload.
MAX_REPLAY = 500

HEARTBEAT_EVENT_TYPE: Final = "heartbeat"
RESYNC_EVENT_TYPE: Final = "resync_required"


def heartbeat_envelope() -> str:
    return json.dumps(
        {
            "event_type": HEARTBEAT_EVENT_TYPE,
            "timestamp": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        }
    )


class RealtimeService:
    """Bridges the Redis channel to this process's connections."""

    def __init__(self, settings: Settings | None = None) -> None:
        cfg = settings or get_settings()
        self.hub = ConnectionHub()
        self._subscription = RedisEventSubscription(cfg.redis_url)
        self._listener: asyncio.Task[None] | None = None
        self._heartbeat: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    # -- connections -------------------------------------------------------

    async def connect(
        self, *, tenant_id: uuid.UUID, sink: Sink, since: int | None = None
    ) -> Connection:
        """Register a client, replaying from ``since`` if it supplied a cursor."""
        async with self._lock:
            first_for_tenant = self.hub.connection_count(tenant_id) == 0
            connection = self.hub.register(tenant_id=tenant_id, sink=sink, cursor=since or 0)
            if first_for_tenant:
                await self._subscription.subscribe(tenant_id)
            self._ensure_tasks()

        if since is not None:
            await self._replay(connection, since=since)
        return connection

    async def disconnect(self, connection: Connection) -> None:
        async with self._lock:
            await self.hub.unregister(connection)
            if self.hub.connection_count(connection.tenant_id) == 0:
                with contextlib.suppress(Exception):
                    await self._subscription.unsubscribe(connection.tenant_id)

    # -- replay ------------------------------------------------------------

    async def _replay(self, connection: Connection, *, since: int) -> None:
        """Queue everything this tenant produced after ``since``.

        Read from the outbox rather than from ``events`` directly, because the
        cursor *is* an outbox id — and because the outbox already encodes which
        chains are publishable, so a replay cannot hand a client an event the
        live stream would never have sent it.
        """
        envelopes = await load_replay(tenant_id=connection.tenant_id, since=since)
        if envelopes is None:
            connection.queue.put_nowait(
                json.dumps(
                    {
                        "event_type": RESYNC_EVENT_TYPE,
                        "detail": f"more than {MAX_REPLAY} events since cursor {since}",
                    }
                )
            )
            return
        for envelope in envelopes:
            # Through `broadcast`'s queue rather than the socket, so a client
            # that is already too slow to accept its own replay is shed by the
            # same rule as everything else.
            try:
                connection.queue.put_nowait(json.dumps(envelope))
            except asyncio.QueueFull:
                log.info(
                    "websocket_replay_truncated",
                    connection_id=str(connection.id),
                    since=since,
                    note="client could not accept its own replay",
                )
                return

    # -- background tasks --------------------------------------------------

    def _ensure_tasks(self) -> None:
        if self._listener is None or self._listener.done():
            self._listener = asyncio.create_task(self._listen())
        if self._heartbeat is None or self._heartbeat.done():
            self._heartbeat = asyncio.create_task(self._beat())

    async def _listen(self) -> None:
        try:
            async for channel, envelope in self._subscription.listen():
                tenant_id = _tenant_of(channel)
                if tenant_id is None:  # pragma: no cover — channel names are ours
                    continue
                self.hub.broadcast(tenant_id, json.dumps(envelope))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover — requires Redis to drop mid-listen
            log.error(
                "realtime_listener_failed",
                error_type=type(exc).__name__,
                runbook="docs/runbooks/websocket-hub-disconnect.md",
            )

    async def _beat(self) -> None:
        """Heartbeat, and supervise the listener while we are here.

        **The failure this supervision exists to prevent.** If the listener task
        dies — Redis restarts, the connection drops mid-``listen`` — nothing
        recreates it until the *next* client connects. Every already-connected
        client keeps receiving heartbeats from this loop and stops receiving
        events, so the connection looks healthy from both ends and is deaf. That
        is strictly worse than a dropped socket, which a client at least knows to
        reconnect from.

        Checked here rather than in a third task because the heartbeat is
        already the process's periodic tick, and a supervisor that itself needs
        supervising is where this stops being worth doing.
        """
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)

            if self.hub.tenants() and (self._listener is None or self._listener.done()):
                log.warning(
                    "realtime_listener_restarted",
                    consequence="clients were connected and receiving no events",
                    runbook="docs/runbooks/websocket-hub-disconnect.md",
                )
                await self._resubscribe()
                self._listener = asyncio.create_task(self._listen())

            message = heartbeat_envelope()
            for tenant_id in self.hub.tenants():
                self.hub.broadcast(tenant_id, message)

    async def _resubscribe(self) -> None:
        """Re-establish the subscription for every tenant still connected.

        A listener that died because its connection dropped left the pub/sub
        object subscribed to channels on a socket that no longer exists.
        Restarting the read loop without re-subscribing would produce a task
        that runs forever and yields nothing — the same silent deafness, now
        with a log line claiming it was fixed.
        """
        for tenant_id in self.hub.tenants():
            with contextlib.suppress(Exception):
                await self._subscription.subscribe(tenant_id)

    async def close(self) -> None:
        for task in (self._listener, self._heartbeat):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._listener = None
        self._heartbeat = None
        await self.hub.close_all(code=CLOSE_GOING_AWAY, reason="server shutting down")
        await self._subscription.close()


async def load_replay(*, tenant_id: uuid.UUID, since: int) -> list[dict[str, Any]] | None:
    """Envelopes after ``since`` for one tenant, or ``None`` if too far behind."""
    with tenant_scope(tenant_id):
        async with session_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(OutboxMessage)
                        .where(
                            OutboxMessage.tenant_id == tenant_id,
                            OutboxMessage.id > since,
                        )
                        .order_by(OutboxMessage.id)
                        .limit(MAX_REPLAY + 1)
                    )
                )
                .scalars()
                .all()
            )
            if len(rows) > MAX_REPLAY:
                return None
            if not rows:
                return []

            payloads = {
                int(event_id): dict(payload)
                for event_id, payload in (
                    await session.execute(
                        select(Event.id, Event.payload).where(
                            Event.tenant_id == tenant_id,
                            Event.id.in_([row.event_id for row in rows]),
                            Event.recorded_at >= min(row.event_recorded_at for row in rows),
                            Event.recorded_at <= max(row.event_recorded_at for row in rows),
                        )
                    )
                )
                .tuples()
                .all()
            }

    return [
        build_envelope(
            event_type=row.event_type,
            entity_type=row.entity_type,
            entity_id=row.entity_id,
            sequence=row.sequence,
            occurred_at=row.occurred_at,
            payload=payloads.get(row.event_id, {}),
            cursor=row.id,
        )
        for row in rows
    ]


def _tenant_of(channel: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(channel.rsplit(":", 1)[-1])
    except ValueError:  # pragma: no cover — we own the channel names
        return None


# The process-global instance, created lazily like the engine and the flag
# store, and closed by the same lifespan hook.
_service: RealtimeService | None = None


def get_realtime() -> RealtimeService:
    global _service
    if _service is None:
        _service = RealtimeService()
    return _service


async def close_realtime() -> None:
    global _service
    if _service is not None:
        await _service.close()
    _service = None


def _assert_channel_naming() -> None:
    """Guard the round trip the listener depends on.

    ``channel_name`` builds the string and ``_tenant_of`` takes it apart, in two
    modules. Asserting the round trip at import is cheaper than discovering it
    at 2am, when the symptom would be a hub that receives every message and
    delivers none.
    """
    probe = uuid.UUID("00000000-0000-0000-0000-0000000000ff")
    if _tenant_of(channel_name(probe)) != probe:  # pragma: no cover — structural invariant
        raise RuntimeError("realtime channel naming and parsing disagree")


_assert_channel_naming()
