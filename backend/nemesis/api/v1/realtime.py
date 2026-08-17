"""§26.3: ``/ws/pipeline-events``.

The path and the ``tenant_id`` query parameter are fixed by the blueprint. Two
things this endpoint adds to that sketch, both because the sketch does not
survive contact with a real connection:

``?since=<cursor>``
    A reconnecting client says where it got to and is replayed from there.
    Without it, a client that was disconnected for ninety seconds either replays
    everything or silently misses whatever happened — and on a map, "silently
    missed" is a pin that never appears with nothing to indicate it should have.

**heartbeats**
    Sent as ordinary envelopes on the same queue as everything else, so a tab
    that has stopped reading fills its queue on heartbeats alone and is shed on
    schedule instead of lingering as a healthy-looking connection with a dead
    page behind it.

**The kill switch is checked before the handshake, not after.**
``realtime_websocket_hub`` was declared in Phase 1a for exactly this endpoint.
Killing it must refuse the *upgrade*, so clients take §27.3's polling fallback
immediately; accepting the socket and then closing it teaches every client to
reconnect in a loop against a capability somebody deliberately switched off.
"""

from __future__ import annotations

import uuid
from typing import Final

from fastapi import APIRouter, Query, WebSocket, status
from sqlalchemy import select

from nemesis.db.models.tenant import Tenant
from nemesis.db.session import session_scope
from nemesis.domain.constants import SYSTEM_TENANT_ID
from nemesis.flags import get_flags
from nemesis.observability.logging import get_logger
from nemesis.realtime.service import get_realtime

log = get_logger(__name__)

router = APIRouter(tags=["realtime"])

WS_PATH: Final = "/ws/pipeline-events"

#: Close code for a rejected connection: unknown tenant, or the kill switch.
#: 1008 is "Policy Violation", which is what both of these are from the server's
#: point of view — and unlike a private 4xxx code it is one every client library
#: already reports usefully. Taken from Starlette rather than written as 1008 so
#: the documented client behaviour and the wire cannot drift apart.
CLOSE_POLICY: Final = status.WS_1008_POLICY_VIOLATION

REALTIME_FLAG: Final = "realtime_websocket_hub"


class _WebSocketSink:
    """Adapts a Starlette ``WebSocket`` to the hub's two-method ``Sink``.

    The adapter exists so the hub never imports Starlette, which is what makes
    the backpressure behaviour testable against a sink that simply refuses to
    return — the honest way to build a client that has stopped reading.
    """

    __slots__ = ("_socket",)

    def __init__(self, socket: WebSocket) -> None:
        self._socket = socket

    async def send(self, message: str) -> None:
        await self._socket.send_text(message)

    async def close(self, code: int, reason: str) -> None:
        await self._socket.close(code=code, reason=reason)


@router.websocket(WS_PATH)
async def pipeline_events(
    websocket: WebSocket,
    tenant_id: uuid.UUID = Query(...),
    since: int | None = Query(default=None, ge=0),
) -> None:
    """Stream this tenant's pipeline events until the client goes away."""
    flags = get_flags()
    if not await flags.is_enabled(REALTIME_FLAG, str(tenant_id)):
        # Refuse the handshake. A client that never completes an upgrade falls
        # to polling; a client that connects and is closed reconnects forever.
        await websocket.close(code=CLOSE_POLICY, reason="realtime transport disabled")
        return

    if not await _tenant_exists(tenant_id):
        await websocket.close(code=CLOSE_POLICY, reason="unknown tenant")
        return

    await websocket.accept()
    service = get_realtime()
    connection = await service.connect(
        tenant_id=tenant_id, sink=_WebSocketSink(websocket), since=since
    )
    log.info(
        "websocket_connected",
        connection_id=str(connection.id),
        tenant_id=str(tenant_id),
        resumed_from=since,
    )

    try:
        while True:
            # The hub owns writing; this loop exists only to observe the client
            # going away. Anything the client sends is discarded: §26.3 is a
            # one-directional contract, and accepting commands over an
            # unauthenticated socket would be a control surface Phase 13 has not
            # yet been able to protect.
            await websocket.receive_text()
    except Exception:
        # Every disconnect arrives as an exception of some kind, and none of
        # them is worth distinguishing here — the client is gone either way and
        # the teardown is identical.
        pass
    finally:
        await service.disconnect(connection)
        log.info(
            "websocket_disconnected",
            connection_id=str(connection.id),
            cursor=connection.cursor,
            lagging=connection.lagging,
        )


async def _tenant_exists(tenant_id: uuid.UUID) -> bool:
    """Reject an unknown or reserved tenant before accepting the socket.

    Without this a caller could hold open a subscription to any UUID and the
    process would subscribe to a Redis channel per guess — an unbounded
    allocation driven entirely by an unauthenticated query parameter.
    """
    if tenant_id == SYSTEM_TENANT_ID:
        return False
    # tenant-scope-exempt: this IS the tenant lookup; `tenants` has no tenant
    # column to scope by.
    async with session_scope() as session:
        found = (
            await session.execute(
                select(Tenant.id).where(Tenant.id == tenant_id, Tenant.is_active.is_(True))
            )
        ).one_or_none()
    return found is not None


__all__ = ["CLOSE_POLICY", "REALTIME_FLAG", "WS_PATH", "router"]
