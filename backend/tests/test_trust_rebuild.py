"""The review queue is derived from the log, and this is what proves it.

``projections.writer`` makes "current state is derived" structurally true for
``complaints`` and its two siblings — nothing else writes them. The review
tables are written by a stage and by an HTTP handler, for the one-to-many shape
reason ``db.models.trust`` gives, which turns the same property into a *claim*.

``trust.rebuild`` is what turns it back into a fact, and this file is what keeps
``trust.rebuild`` honest: it populates the tables the way production does, drops
them, rebuilds from ``review_queued`` and ``review_decided`` alone, and compares
field by field. A change that started writing something the log does not carry
fails here rather than six months later during a repair.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from nemesis.db.models.trust import ReviewDecision, ReviewQueueItem
from nemesis.events.store import EventStore
from nemesis.projections.replay import replay_entity
from nemesis.projections.writer import write_projection
from nemesis.tenancy.context import tenant_scope
from nemesis.trust.rebuild import rebuild_reviews
from nemesis.trust.review import ReviewDecisionKind, ReviewReason, decide, queue
from tests.conftest import postgres_required
from tests.test_trust_review import make_complaint

pytestmark = [postgres_required, pytest.mark.integration]

BASE = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)

#: Which columns must survive a rebuild. ``id`` is included deliberately:
#: ``review_queued`` carries the item id, so identity is recoverable — and if it
#: were not, every reference an operator had written down would break on repair.
ITEM_FIELDS = (
    "id",
    "tenant_id",
    "complaint_id",
    "reason",
    "status",
    "priority",
    "occurrences",
    "trust_score",
    "evidence",
)
DECISION_FIELDS = (
    "review_item_id",
    "complaint_id",
    "reason",
    "decision",
    "rationale",
    "decided_by",
    "decided_by_label",
    "evidence_hash",
)


@pytest.fixture
def sessions(migrated_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(migrated_engine, expire_on_commit=False)


def snapshot(rows: object, fields: tuple[str, ...]) -> list[tuple[object, ...]]:
    return sorted(
        (tuple(getattr(row, field) for field in fields) for row in rows),  # type: ignore[union-attr]
        key=repr,
    )


async def queue_and_emit(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    complaint_id: uuid.UUID,
    reason: ReviewReason,
    evidence: dict[str, object],
    trust_score: float,
) -> uuid.UUID:
    """Queue an item and append its event, the way the trust stage does.

    ``review.queue`` deliberately writes the row and emits nothing — the caller
    owns the event, because the stage hands it to the orchestrator to append
    atomically while an HTTP path would append it directly. A rebuild test that
    used ``queue`` alone would populate rows the log does not explain and then
    "prove" that a rebuild deletes them, which is true and beside the point.

    So this mirrors the stage's half of the contract, and is the reason the
    contract is worth stating in ``queue``'s docstring.
    """
    queued = await queue(
        session,
        tenant_id=tenant_id,
        complaint_id=complaint_id,
        reason=reason,
        evidence=evidence,
        trust_score=trust_score,
    )
    event = await EventStore(session).append(
        entity_id=complaint_id,
        event_type="review_queued",
        payload={
            "review_item_id": str(queued.review_item_id),
            "reason": queued.reason.value,
            "priority": queued.priority,
            "occurrences": queued.occurrences,
            "trust_score": queued.trust_score,
            "evidence_hash": queued.evidence_hash,
        },
        tenant_id=tenant_id,
    )
    del event
    projection = await replay_entity(
        session, tenant_id=tenant_id, entity_type="complaint", entity_id=complaint_id
    )
    await write_projection(session, tenant_id=tenant_id, result=projection)
    await session.flush()
    return queued.review_item_id


async def populate(session: AsyncSession, *, tenant_id: uuid.UUID) -> None:
    """A queue with every shape the rebuild has to handle.

    One open item, one item that escalated twice before being decided, one
    decided item, and two complaints — so the rebuild is exercised on more than
    a single chain, which is where an ordering bug hides.
    """
    first = await make_complaint(session, tenant_id=tenant_id, reported_at=BASE)
    second = await make_complaint(session, tenant_id=tenant_id, reported_at=BASE)

    await queue_and_emit(
        session,
        tenant_id=tenant_id,
        complaint_id=first,
        reason=ReviewReason.EXIF_MISMATCH,
        evidence={"distance_meters": 3100.0},
        trust_score=-0.4,
    )
    await queue_and_emit(
        session,
        tenant_id=tenant_id,
        complaint_id=first,
        reason=ReviewReason.DEVICE_VELOCITY,
        evidence={"observed": 13},
        trust_score=-0.7,
    )
    velocity = await queue_and_emit(
        session,
        tenant_id=tenant_id,
        complaint_id=first,
        reason=ReviewReason.DEVICE_VELOCITY,
        evidence={"observed": 41},
        trust_score=-1.0,
    )
    await decide(
        session,
        tenant_id=tenant_id,
        item_id=velocity,
        decision=ReviewDecisionKind.REJECT,
        rationale="Volunteer clean-up drive, confirmed by the ward office.",
        decided_by_label="ops-oncall",
    )

    duplicate = await queue_and_emit(
        session,
        tenant_id=tenant_id,
        complaint_id=second,
        reason=ReviewReason.PERCEPTUAL_DUPLICATE,
        evidence={"hamming_distance": 2, "matches": [{"complaint_id": str(first)}]},
        trust_score=-0.35,
    )
    await decide(
        session,
        tenant_id=tenant_id,
        item_id=duplicate,
        decision=ReviewDecisionKind.ESCALATE,
        rationale="Third re-upload from this reporter; fraud team should look.",
        decided_by_label="ops-oncall",
    )


async def _read(
    session: AsyncSession, *, tenant_id: uuid.UUID
) -> tuple[list[tuple[object, ...]], list[tuple[object, ...]]]:
    items = (
        (
            await session.execute(
                select(ReviewQueueItem).where(ReviewQueueItem.tenant_id == tenant_id)
            )
        )
        .scalars()
        .all()
    )
    decisions = (
        (await session.execute(select(ReviewDecision).where(ReviewDecision.tenant_id == tenant_id)))
        .scalars()
        .all()
    )
    return snapshot(items, ITEM_FIELDS), snapshot(decisions, DECISION_FIELDS)


async def test_a_rebuild_reproduces_what_the_pipeline_wrote(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> None:
    """§9.1's promise, kept for tables the projection writer does not own."""
    with tenant_scope(tenant_id):
        async with sessions() as session:
            await populate(session, tenant_id=tenant_id)
            await session.commit()
            before_items, before_decisions = await _read(session, tenant_id=tenant_id)

            result = await rebuild_reviews(session, tenant_id=tenant_id)
            await session.commit()
            after_items, after_decisions = await _read(session, tenant_id=tenant_id)

    assert before_items and before_decisions, "the fixture wrote nothing; nothing is proven"
    assert after_items == before_items
    assert after_decisions == before_decisions
    assert result.items == len(before_items)
    assert result.decisions == len(before_decisions)
    assert result.evidence_unavailable == 0


async def test_a_rebuild_removes_a_row_the_log_does_not_explain(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> None:
    """The main thing a rebuild is *for*.

    A merge-style rebuild could correct a wrong value and could never remove a
    row that should not exist — which is the shape of the bug somebody reaches
    for a rebuild to fix.
    """
    with tenant_scope(tenant_id):
        async with sessions() as session:
            await populate(session, tenant_id=tenant_id)
            orphan_complaint = await make_complaint(session, tenant_id=tenant_id, reported_at=BASE)
            session.add(
                ReviewQueueItem(
                    tenant_id=tenant_id,
                    complaint_id=orphan_complaint,
                    reason="low_trust",
                    status="open",
                    priority=90,
                    occurrences=1,
                    evidence={"written": "by a bug"},
                    trust_score=-0.9,
                )
            )
            await session.commit()

            before, _ = await _read(session, tenant_id=tenant_id)
            await rebuild_reviews(session, tenant_id=tenant_id)
            await session.commit()
            after, _ = await _read(session, tenant_id=tenant_id)

    assert len(before) == len(after) + 1
    assert all(row[3] != "low_trust" for row in after)


async def test_a_rebuild_without_the_bundle_says_so_rather_than_writing_an_empty_one(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> None:
    """The honest cost of keeping the evidence out of the log.

    §22.4 requires the bundle to expire and an append-only chain cannot expire
    anything, so only its hash is in the log. A rebuild that could not recover
    the bundle writes a marker saying so — an empty ``{}`` in front of a
    reviewer looks like a flag with no reason, which is exactly what §11.4
    exists to prevent.
    """
    with tenant_scope(tenant_id):
        async with sessions() as session:
            await populate(session, tenant_id=tenant_id)
            await session.commit()

            result = await rebuild_reviews(session, tenant_id=tenant_id, preserve_evidence=False)
            await session.commit()
            items, _ = await _read(session, tenant_id=tenant_id)

    assert result.evidence_unavailable == result.items > 0
    for row in items:
        evidence = row[ITEM_FIELDS.index("evidence")]
        assert evidence["unavailable"] is True  # type: ignore[index]
        assert len(evidence["evidence_hash"]) == 64  # type: ignore[index]


async def test_a_rebuild_is_scoped_to_one_tenant(
    sessions: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
    other_tenant_id: uuid.UUID,
) -> None:
    """A repair for one customer must not delete another customer's queue."""
    with tenant_scope(other_tenant_id):
        async with sessions() as session:
            await populate(session, tenant_id=other_tenant_id)
            await session.commit()
            theirs_before, _ = await _read(session, tenant_id=other_tenant_id)

    with tenant_scope(tenant_id):
        async with sessions() as session:
            await populate(session, tenant_id=tenant_id)
            await session.commit()
            await rebuild_reviews(session, tenant_id=tenant_id)
            await session.commit()

    with tenant_scope(other_tenant_id):
        async with sessions() as session:
            theirs_after, _ = await _read(session, tenant_id=other_tenant_id)

    assert theirs_after == theirs_before


async def test_the_escalation_count_comes_from_the_log_not_from_counting_events(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> None:
    """``occurrences`` is what the event says the queue reached.

    Incrementing per event would disagree with the log the moment one event was
    archived by §22.4's partition retention — and the disagreement would show up
    as a queue item claiming fewer escalations than actually happened, which is
    the number a reviewer uses to decide how seriously to take it.
    """
    with tenant_scope(tenant_id):
        async with sessions() as session:
            complaint_id = await make_complaint(session, tenant_id=tenant_id, reported_at=BASE)
            for observed in (13, 41, 88):
                await queue_and_emit(
                    session,
                    tenant_id=tenant_id,
                    complaint_id=complaint_id,
                    reason=ReviewReason.DEVICE_VELOCITY,
                    evidence={"observed": observed},
                    trust_score=-1.0,
                )
            await session.commit()

            await rebuild_reviews(session, tenant_id=tenant_id)
            await session.commit()

            item = (
                await session.execute(
                    select(ReviewQueueItem).where(ReviewQueueItem.tenant_id == tenant_id)
                )
            ).scalar_one()
            events = [
                event.event_type
                for event in await EventStore(session).read_stream(
                    entity_type="complaint", entity_id=complaint_id
                )
            ]

    assert item.occurrences == 3
    assert events == ["complaint_submitted"] + ["review_queued"] * 3
