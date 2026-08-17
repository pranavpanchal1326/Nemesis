"""The connection hub: bounded queues, and a firm answer to a slow client.

**The failure this module exists to prevent.** A browser tab is backgrounded,
throttled, or on a train. Its TCP receive window closes and ``send`` stops
returning. If the fan-out path awaits that send while holding the loop that also
feeds every other connection, one throttled tab stalls the map for every other
user on the process — and the symptom is "the map froze", reported by people
whose own connections were perfectly healthy.

So no fan-out path ever awaits a socket. Each connection owns a bounded
``asyncio.Queue`` and its own writer task. Fan-out does ``put_nowait``; when the
queue is full the connection is **shed** — detached from the tenant's set,
closed with a code that means "you fell behind", and left to reconnect. The
writer task is the only thing that ever touches the socket, so a blocked socket
blocks exactly one task.

**Shedding is a documented recovery, not a dropped client.** The close frame
carries the last cursor the client actually received; it reconnects with
``?since=<cursor>`` and is replayed from the outbox, so being shed costs a round
trip. §27.3's stated fallback — the client drops to 5-second polling — remains
available underneath that.

**Why the queue is small.** 64 envelopes. A large queue does not rescue a slow
client; it delays the moment the hub notices and then delivers a burst of stale
events. Being shed at 64 and resuming from a cursor is both faster and more
correct than being fed five thousand events from ten minutes ago.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from dataclasses import dataclass
from typing import Final, Protocol

from nemesis.observability import metrics
from nemesis.observability.logging import get_logger

log = get_logger(__name__)

#: Envelopes buffered per connection before it is considered lagging.
DEFAULT_QUEUE_SIZE: Final = 64

#: WebSocket close code for a shed client. 1013 is "Try Again Later", the
#: closest standard code to "you were too slow; reconnect". A private 4xxx code
#: would carry more meaning and be understood by nothing.
CLOSE_LAGGING: Final = 1013

#: Close code for shutdown or the kill switch being pulled.
CLOSE_GOING_AWAY: Final = 1001


class Sink(Protocol):
    """The subset of a WebSocket the hub uses.

    Two methods, so the backpressure behaviour is testable without a network:
    the Phase 3 gate has to prove a client that stops reading is shed without
    stalling the hub, and the honest way to build a client that stops reading is
    a sink whose ``send`` never returns.
    """

    async def send(self, message: str) -> None: ...
    async def close(self, code: int, reason: str) -> None: ...


@dataclass(eq=False)
class Connection:
    """One subscriber, its queue, and the position it has actually received."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    sink: Sink
    queue: asyncio.Queue[str]
    #: The outbox id of the last envelope written to this socket — not the last
    #: one *queued*. A cursor that counted queued-but-unsent envelopes would
    #: tell a resuming client it had seen events that were still sitting in a
    #: queue when it was shed, which is exactly the gap the cursor exists to
    #: close.
    cursor: int = 0
    lagging: bool = False
    detached: bool = False


class ConnectionHub:
    """Fan-out to every connection subscribed to a tenant."""

    def __init__(self, *, queue_size: int = DEFAULT_QUEUE_SIZE) -> None:
        self._queue_size = queue_size
        self._by_tenant: dict[uuid.UUID, set[Connection]] = {}
        self._writers: dict[uuid.UUID, asyncio.Task[None]] = {}
        #: Close-and-forget tasks for shed connections. Held so they are not
        #: garbage collected mid-await, and awaitable by tests that need the
        #: close frame to have actually been sent before asserting on it.
        self._shedders: set[asyncio.Task[None]] = set()

    # -- membership --------------------------------------------------------

    def register(self, *, tenant_id: uuid.UUID, sink: Sink, cursor: int = 0) -> Connection:
        connection = Connection(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            sink=sink,
            queue=asyncio.Queue(self._queue_size),
            cursor=cursor,
        )
        self._by_tenant.setdefault(tenant_id, set()).add(connection)
        self._writers[connection.id] = asyncio.create_task(self._write_loop(connection))
        metrics.websocket_connections.inc()
        return connection

    async def unregister(self, connection: Connection) -> None:
        writer = self._detach(connection)
        if writer is not None:
            # Awaited rather than fired and forgotten: an un-awaited cancelled
            # task produces a "Task exception was never retrieved" warning at
            # collection time, which under `filterwarnings = ["error"]` fails
            # whichever unrelated test happens to be running when the GC runs.
            with contextlib.suppress(asyncio.CancelledError):
                await writer

    def _detach(self, connection: Connection) -> asyncio.Task[None] | None:
        """Remove from fan-out and stop the writer. Idempotent."""
        if connection.detached:
            return None
        connection.detached = True

        peers = self._by_tenant.get(connection.tenant_id)
        if peers is not None:
            peers.discard(connection)
            if not peers:
                del self._by_tenant[connection.tenant_id]

        metrics.websocket_connections.dec()

        writer = self._writers.pop(connection.id, None)
        if writer is not None:
            writer.cancel()
        return writer

    def tenants(self) -> frozenset[uuid.UUID]:
        """Tenants with at least one connection — what this process subscribes to."""
        return frozenset(self._by_tenant)

    def connection_count(self, tenant_id: uuid.UUID) -> int:
        return len(self._by_tenant.get(tenant_id, ()))

    # -- fan-out -----------------------------------------------------------

    def broadcast(self, tenant_id: uuid.UUID, message: str) -> int:
        """Queue ``message`` for every connection on ``tenant_id``.

        Synchronous and non-blocking by construction — there is no ``await`` on
        this path, which is the property that makes "one slow client cannot
        stall the hub" structural rather than something to be careful about.
        Returns how many connections accepted it.
        """
        delivered = 0
        for connection in tuple(self._by_tenant.get(tenant_id, ())):
            try:
                connection.queue.put_nowait(message)
            except asyncio.QueueFull:
                self._shed(connection, reason="slow_consumer")
                continue
            delivered += 1
        return delivered

    def _shed(self, connection: Connection, *, reason: str) -> None:
        connection.lagging = True
        metrics.websocket_clients_shed_total.labels(reason=reason).inc()
        log.info(
            "websocket_client_shed",
            connection_id=str(connection.id),
            reason=reason,
            queue_size=self._queue_size,
            cursor=connection.cursor,
            note="client may reconnect with ?since=<cursor>",
        )
        self._detach(connection)
        self._spawn_close(
            connection, CLOSE_LAGGING, f"lagging; reconnect with since={connection.cursor}"
        )

    def _spawn_close(self, connection: Connection, code: int, reason: str) -> None:
        async def _close() -> None:
            # The close frame is best-effort. The socket is already unhealthy by
            # definition, and failing to deliver "you were shed" must not stop
            # the connection being gone from the hub — which it already is.
            with contextlib.suppress(Exception):
                await connection.sink.close(code, reason)

        task = asyncio.create_task(_close())
        self._shedders.add(task)
        task.add_done_callback(self._shedders.discard)

    async def drain_pending_closes(self) -> None:
        """Await outstanding close frames. For shutdown and for tests."""
        while self._shedders:
            await asyncio.gather(*tuple(self._shedders), return_exceptions=True)

    # -- writing -----------------------------------------------------------

    async def _write_loop(self, connection: Connection) -> None:
        """The only code in the hub that touches a socket."""
        while True:
            message = await connection.queue.get()
            try:
                await connection.sink.send(message)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.info(
                    "websocket_send_failed",
                    connection_id=str(connection.id),
                    error_type=type(exc).__name__,
                )
                metrics.websocket_clients_shed_total.labels(reason="send_failed").inc()
                self._detach(connection)
                return
            metrics.websocket_messages_sent_total.inc()
            connection.cursor = max(connection.cursor, cursor_of(message))

    async def close_all(
        self, *, code: int = CLOSE_GOING_AWAY, reason: str = "shutting down"
    ) -> None:
        """Close every connection — process shutdown, or the kill switch."""
        for peers in tuple(self._by_tenant.values()):
            for connection in tuple(peers):
                self._detach(connection)
                self._spawn_close(connection, code, reason)
        await self.drain_pending_closes()


def cursor_of(message: str) -> int:
    """Read the cursor back out of an envelope that was just sent.

    Parsing the serialised form rather than carrying a parallel
    ``(cursor, message)`` tuple through the queue: the cursor the client
    received is by definition the one inside the bytes the client received, and
    two representations of that are two things that can disagree.
    """
    try:
        value = json.loads(message).get("cursor", 0)
    except (ValueError, AttributeError, TypeError):  # pragma: no cover — hub queues envelopes
        return 0
    return value if isinstance(value, int) else 0
