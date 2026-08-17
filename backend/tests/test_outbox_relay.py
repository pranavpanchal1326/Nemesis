"""The outbox and its relay — including the gate clause about rolled-back writes.

The clause is "a rolled-back transaction never emits a WebSocket event", and it
is asserted at the only place where it can be *structurally* true rather than
carefully arranged: the publish reads committed rows, so there is no code path
from an uncommitted event to a socket. The test below rolls a transaction back
and shows the relay has nothing to publish.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from nemesis.db.models.outbox import OutboxMessage
from nemesis.db.session import session_scope
from nemesis.domain.lifecycle import EntityType
from nemesis.events.store import EventStore
from nemesis.ingest.service import Submission, submit
from nemesis.outbox import writer as outbox
from nemesis.outbox.relay import drain_once
from nemesis.tenancy.context import tenant_scope
from tests.conftest import postgres_required

pytestmark = [postgres_required, pytest.mark.integration]


class RecordingChannel:
    """Stands in for Redis pub/sub.

    A double here rather than real Redis, and the reason is that the *transport*
    is not what these tests are about — the claim being checked is which rows
    reach a publish at all, and against real Redis a passing test would prove
    that plus the ability to reach Redis, with no way to tell which half failed.
    ``test_realtime_channel_round_trip`` covers the transport itself.
    """

    def __init__(self) -> None:
        self.published: list[tuple[uuid.UUID, dict[str, Any]]] = []
        self.fail_after: int | None = None

    async def publish(self, tenant_id: uuid.UUID, envelope: dict[str, Any]) -> int:
        if self.fail_after is not None and len(self.published) >= self.fail_after:
            raise ConnectionError("redis is unreachable")
        self.published.append((tenant_id, envelope))
        return 1


async def _submit(tenant_id: uuid.UUID) -> uuid.UUID:
    receipt = await submit(
        tenant_id=tenant_id,
        submission=Submission(latitude=18.52, longitude=73.85, description_text="pothole"),
        correlation_id="outbox-test",
    )
    return receipt.complaint_id


async def test_a_committed_event_is_enqueued_and_relayed(
    bound_session: None, tenant_id: uuid.UUID
) -> None:
    complaint_id = await _submit(tenant_id)
    channel = RecordingChannel()

    async with session_scope() as session:
        result = await drain_once(session, channel)  # type: ignore[arg-type]

    assert result.published == 1
    published_tenant, envelope = channel.published[0]
    assert published_tenant == tenant_id
    assert envelope["event_type"] == "complaint_submitted"
    assert envelope["entity_id"] == str(complaint_id)
    assert envelope["cursor"] > 0

    with tenant_scope(tenant_id):
        async with session_scope() as session:
            assert await outbox.pending_count(session, tenant_id=tenant_id) == 0


async def test_a_rolled_back_transaction_publishes_nothing(
    bound_session: None, tenant_id: uuid.UUID
) -> None:
    """The gate clause. No outbox row survives, so no publish is possible."""
    complaint_id = uuid.uuid4()

    with pytest.raises(RuntimeError, match="deliberate"), tenant_scope(tenant_id):
        async with session_scope() as session:
            event = await EventStore(session).append(
                entity_id=complaint_id,
                event_type="complaint_submitted",
                payload={"latitude": 1.0, "longitude": 2.0},
                tenant_id=tenant_id,
            )
            enqueued = await outbox.enqueue(session, event)
            assert enqueued is True
            # Everything above is real and would have published. The rollback is
            # what makes it not have happened.
            raise RuntimeError("deliberate failure after the enqueue")

    channel = RecordingChannel()
    async with session_scope() as session:
        result = await drain_once(session, channel)  # type: ignore[arg-type]

    assert result.published == 0
    assert channel.published == []

    with tenant_scope(tenant_id):
        async with session_scope() as session:
            events = await EventStore(session).read_stream(
                entity_type=EntityType.COMPLAINT.value, entity_id=complaint_id
            )
            assert events == []


async def test_the_relay_stops_at_a_failed_publish_rather_than_skipping_ahead(
    bound_session: None, tenant_id: uuid.UUID
) -> None:
    """Ordering is the consumer's only ordering signal.

    Publishing row 3 after row 2 failed would deliver an entity's events out of
    order to a client whose only sequencing information is arrival order.
    """
    for _ in range(3):
        await _submit(tenant_id)

    channel = RecordingChannel()
    channel.fail_after = 1

    async with session_scope() as session:
        result = await drain_once(session, channel)  # type: ignore[arg-type]

    assert result.published == 1
    assert result.failed == 1

    with tenant_scope(tenant_id):
        async with session_scope() as session:
            # The two behind it are untouched and will be retried in order.
            assert await outbox.pending_count(session, tenant_id=tenant_id) == 2


async def test_only_publishable_chains_are_enqueued(
    bound_session: None, tenant_id: uuid.UUID
) -> None:
    """``admin_action`` is operational history, not something a browser sees."""
    with tenant_scope(tenant_id):
        async with session_scope() as session:
            event = await EventStore(session).append(
                entity_id=uuid.uuid4(),
                event_type="admin_action",
                payload={"action": "tenant_suspended", "justification": "non-payment"},
                tenant_id=tenant_id,
            )
            assert await outbox.enqueue(session, event) is False

    with tenant_scope(tenant_id):
        async with session_scope() as session:
            assert await outbox.pending_count(session, tenant_id=tenant_id) == 0


async def test_dispatched_rows_are_purged_only_past_the_resume_window(
    bound_session: None, tenant_id: uuid.UUID
) -> None:
    await _submit(tenant_id)
    channel = RecordingChannel()
    async with session_scope() as session:
        await drain_once(session, channel)  # type: ignore[arg-type]

    async with session_scope() as session:
        # A horizon before the row was dispatched keeps it: the row exists so a
        # client reconnecting with a cursor can be caught up from it.
        kept = await outbox.purge_dispatched(
            session, older_than=datetime.now(tz=UTC) - timedelta(hours=1)
        )
        assert kept == 0

        removed = await outbox.purge_dispatched(
            session, older_than=datetime.now(tz=UTC) + timedelta(hours=1)
        )
        assert removed == 1


async def test_the_outbox_row_carries_no_payload_copy(
    bound_session: None, tenant_id: uuid.UUID
) -> None:
    """A pointer, not a duplicate of the citizen's submission.

    A denormalised copy would double what Phase 26 has to erase and what Phase 4
    has to scrub, and could drift from the row whose hash was signed.
    """
    await _submit(tenant_id)
    with tenant_scope(tenant_id):
        async with session_scope() as session:
            row = (
                await session.execute(
                    select(OutboxMessage).where(OutboxMessage.tenant_id == tenant_id)
                )
            ).scalar_one()

    columns = {column.name for column in OutboxMessage.__table__.columns}
    assert "payload" not in columns
    assert row.event_id > 0
    assert row.correlation_id == "outbox-test"
