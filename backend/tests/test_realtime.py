"""The hub's backpressure contract, and what an envelope is allowed to carry.

The gate clause — "a client that stops reading is shed without stalling the hub"
— is asserted against a sink whose ``send`` never returns. That is the honest
construction of a client that has stopped reading: not a slow one, not a
disconnected one, but a socket whose write blocks forever. If the hub can
survive that, it can survive the throttled background tab that motivates it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from datetime import UTC, datetime

import pytest

from nemesis.realtime.envelope import GPS_DECIMALS, build_envelope, public_payload
from nemesis.realtime.hub import CLOSE_LAGGING, ConnectionHub, cursor_of
from nemesis.realtime.service import HEARTBEAT_EVENT_TYPE, heartbeat_envelope


class BlockingSink:
    """A client that accepted the connection and then stopped reading."""

    def __init__(self) -> None:
        self.released = asyncio.Event()
        self.sent: list[str] = []
        self.closed: tuple[int, str] | None = None
        self.blocked = asyncio.Event()

    async def send(self, message: str) -> None:
        self.blocked.set()
        await self.released.wait()
        self.sent.append(message)

    async def close(self, code: int, reason: str) -> None:
        self.closed = (code, reason)
        self.released.set()


class FastSink:
    """A client keeping up."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed: tuple[int, str] | None = None

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def close(self, code: int, reason: str) -> None:
        self.closed = (code, reason)


def _envelope(cursor: int) -> str:
    return json.dumps({"event_type": "cluster_created", "cursor": cursor, "payload": {}})


async def test_a_client_that_stops_reading_is_shed_without_stalling_the_hub() -> None:
    """The gate clause, with a healthy peer proving the hub kept working."""
    hub = ConnectionHub(queue_size=4)
    tenant = uuid.uuid4()

    slow, fast = BlockingSink(), FastSink()
    slow_connection = hub.register(tenant_id=tenant, sink=slow)
    hub.register(tenant_id=tenant, sink=fast)

    # Let the slow client's writer pick up its first message and block on it.
    hub.broadcast(tenant, _envelope(1))
    await asyncio.wait_for(slow.blocked.wait(), timeout=2)

    # Fill its queue and then overflow it. `broadcast` is synchronous, so if it
    # could block on the slow sink this loop would never finish.
    #
    # The yield between messages is not test decoration — it is what the
    # production path does. The listener consumes the Redis subscription with
    # `async for`, so every message is a separate scheduling turn and a healthy
    # writer drains between them. Broadcasting the whole burst with no yield
    # (the first version of this test) fills *every* queue and sheds the fast
    # client too, which is correct behaviour for an input that cannot occur and
    # a misleading test for one that can.
    for cursor in range(2, 12):
        hub.broadcast(tenant, _envelope(cursor))
        await asyncio.sleep(0)

    await hub.drain_pending_closes()
    await asyncio.sleep(0.05)

    assert slow_connection.lagging is True
    assert slow.closed is not None
    code, reason = slow.closed
    assert code == CLOSE_LAGGING
    # The close frame carries the resume cursor, which is what makes shedding a
    # recovery rather than a dropped client.
    assert "since=" in reason

    # The healthy peer received everything, which is the half of the claim that
    # a shed-the-slow-client test usually forgets to assert.
    await asyncio.sleep(0)
    assert len(fast.sent) == 11
    assert hub.connection_count(tenant) == 1

    slow.released.set()
    await hub.close_all()


async def test_the_cursor_tracks_what_was_sent_not_what_was_queued() -> None:
    """A resuming client must not be told it saw events still in a queue."""
    hub = ConnectionHub(queue_size=8)
    tenant = uuid.uuid4()
    sink = FastSink()
    connection = hub.register(tenant_id=tenant, sink=sink)

    hub.broadcast(tenant, _envelope(41))
    hub.broadcast(tenant, _envelope(42))
    await asyncio.sleep(0.05)

    assert connection.cursor == 42
    await hub.unregister(connection)


async def test_broadcast_reaches_only_the_addressed_tenant() -> None:
    hub = ConnectionHub()
    first, second = uuid.uuid4(), uuid.uuid4()
    first_sink, second_sink = FastSink(), FastSink()
    hub.register(tenant_id=first, sink=first_sink)
    hub.register(tenant_id=second, sink=second_sink)

    hub.broadcast(first, _envelope(1))
    await asyncio.sleep(0.05)

    assert len(first_sink.sent) == 1
    assert second_sink.sent == []
    await hub.close_all()


async def test_a_failing_sink_removes_its_own_connection() -> None:
    class BrokenSink(FastSink):
        async def send(self, message: str) -> None:
            raise ConnectionResetError("client vanished")

    hub = ConnectionHub()
    tenant = uuid.uuid4()
    hub.register(tenant_id=tenant, sink=BrokenSink())

    hub.broadcast(tenant, _envelope(1))
    await asyncio.sleep(0.05)

    assert hub.connection_count(tenant) == 0


def test_cursor_of_survives_a_message_without_one() -> None:
    assert cursor_of(json.dumps({"event_type": HEARTBEAT_EVENT_TYPE})) == 0
    assert cursor_of(heartbeat_envelope()) == 0


# ---------------------------------------------------------------------------
# What an envelope may carry
# ---------------------------------------------------------------------------


def test_an_event_type_with_no_declared_shape_publishes_an_empty_payload() -> None:
    """Default deny. The whole point of the allow-list.

    ``complaint_submitted`` carries the citizen's exact GPS, their description,
    the media URIs, and the §11.3 device fingerprint. It has no declared shape,
    so none of that reaches a socket — and adding a field to that payload later
    cannot change this, which is the property a strip-the-bad-fields approach
    does not have.
    """
    assert (
        public_payload(
            "complaint_submitted",
            {
                "latitude": 18.520431,
                "longitude": 73.856743,
                "description_text": "outside my house",
                "device_fingerprint": "abc123",
                "photo_url": "nemesis+quarantine://ab/abc.jpg",
            },
        )
        == {}
    )


def test_published_coordinates_are_coarsened() -> None:
    envelope = build_envelope(
        event_type="cluster_created",
        entity_type="complaint_cluster",
        entity_id=uuid.uuid4(),
        sequence=1,
        occurred_at=datetime.now(tz=UTC),
        payload={"latitude": 18.520431, "longitude": 73.856743},
        cursor=7,
    )
    centroid = envelope["payload"]["cluster_centroid"]
    assert centroid == {"lat": 18.52, "lng": 73.857}
    # Rounding to three places, not truncation to a string — a client needs a
    # number it can place on a map.
    assert isinstance(centroid["lat"], float)
    assert GPS_DECIMALS == 3


def test_a_merge_envelope_names_no_complaint_ids() -> None:
    """§26.3's example lists ``merged_complaint_ids``; this deliberately omits it.

    A complaint id is an opaque handle to one citizen's report, and §26.4
    forbids citizen identifiers on the public surface. The scene needs to know a
    merge happened and what the cluster now looks like.
    """
    payload = public_payload(
        "cluster_match_found",
        {
            "complaint_id": str(uuid.uuid4()),
            "combined_confidence": 0.91,
            "report_count_after": 4,
            "geo_distance_meters": 12.5,
            "policy_version": "v1",
        },
    )
    assert payload == {
        "new_confidence": 0.91,
        "report_count": 4,
        "geo_distance_meters": 12.5,
    }
    assert "complaint_id" not in payload


def test_a_safety_envelope_does_not_republish_the_citizens_words() -> None:
    payload = public_payload(
        "safety_trigger_fired",
        {
            "rule_id": "live-wire",
            "ruleset_version": "v1",
            "matched_terms": ["sparking cable outside 42 Elm Street"],
            "detection_source": "keyword",
        },
    )
    assert payload == {"detection_source": "keyword"}


@pytest.mark.parametrize(
    "event_type",
    ["classification_scored", "severity_scored", "citizen_confirmed", "work_order_created"],
)
def test_every_declared_shape_returns_a_dict(event_type: str) -> None:
    assert isinstance(public_payload(event_type, {}), dict)


async def test_a_dead_listener_is_restarted_while_clients_are_connected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The silent-deafness failure, and why the heartbeat supervises.

    If the listener task dies — Redis restarts, the connection drops mid-listen
    — nothing recreated it until the *next* client connected. Every already-
    connected client kept receiving heartbeats and stopped receiving events, so
    the connection looked healthy from both ends and was deaf. That is strictly
    worse than a dropped socket, which a client at least knows to reconnect from.
    """
    from nemesis.realtime import service as service_module

    monkeypatch.setattr(service_module, "HEARTBEAT_SECONDS", 0.01)

    service = service_module.RealtimeService()
    resubscribed: list[uuid.UUID] = []

    async def _record_resubscribe(tenant_id: uuid.UUID) -> None:
        resubscribed.append(tenant_id)

    monkeypatch.setattr(service._subscription, "subscribe", _record_resubscribe)

    tenant = uuid.uuid4()
    sink = FastSink()
    service.hub.register(tenant_id=tenant, sink=sink)

    # A listener that has already died, exactly as a dropped Redis connection
    # leaves it.
    async def _dies_immediately() -> None:
        return None

    service._listener = asyncio.create_task(_dies_immediately())
    await service._listener

    beat = asyncio.create_task(service._beat())
    try:
        for _ in range(200):
            await asyncio.sleep(0.01)
            if service._listener is not None and not service._listener.done():
                break
    finally:
        beat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await beat

    assert service._listener is not None
    assert not service._listener.done(), "the dead listener was never replaced"
    # Re-subscribed as well as restarted. A read loop resumed on a pub/sub object
    # whose socket is gone runs forever and yields nothing — the same deafness,
    # now with a log line claiming it was fixed.
    assert resubscribed == [tenant]

    service._listener.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await service._listener
    await service.hub.close_all()


async def test_the_heartbeat_reaches_connected_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Heartbeats go through the same bounded queue as events, deliberately.

    A transport-level ping is answered by the browser's socket implementation,
    so it proves TCP is alive and says nothing about whether the page is still
    reading. An envelope on the same queue means a dead tab fills its buffer on
    heartbeats alone and is shed on schedule.
    """
    from nemesis.realtime import service as service_module

    monkeypatch.setattr(service_module, "HEARTBEAT_SECONDS", 0.01)
    service = service_module.RealtimeService()
    service._listener = asyncio.create_task(asyncio.sleep(3600))

    tenant = uuid.uuid4()
    sink = FastSink()
    service.hub.register(tenant_id=tenant, sink=sink)

    beat = asyncio.create_task(service._beat())
    try:
        for _ in range(200):
            await asyncio.sleep(0.01)
            if sink.sent:
                break
    finally:
        beat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await beat
        service._listener.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await service._listener

    assert sink.sent, "no heartbeat reached a connected client"
    assert json.loads(sink.sent[0])["event_type"] == HEARTBEAT_EVENT_TYPE
    await service.hub.close_all()
