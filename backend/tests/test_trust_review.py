"""§11.4 — the human review queue, and the Phase 11 feedback loop.

§11.4's requirement is one sentence: *no flag is ever a dead end*. The tests
here are about the three things that sentence does not spell out and that are
each load-bearing — the evidence bundle is frozen, a repeat escalates rather
than duplicating, and every decision becomes a training label attached to the
evidence that produced it.

``make_complaint`` lives here and is imported by the other Phase 8 test modules.
It writes a real ``complaint_submitted`` event through the real ``EventStore``
and materialises the projection, rather than inserting a row: the review tables
carry RESTRICT foreign keys to ``complaints``, and a fixture that bypassed the
event log would be testing against a complaint the system could not explain.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from nemesis.db.models.trust import ReviewDecision, ReviewQueueItem
from nemesis.events.store import EventStore
from nemesis.projections.replay import replay_entity
from nemesis.projections.writer import write_projection
from nemesis.tenancy.context import tenant_scope
from nemesis.trust.errors import (
    ReviewConflictError,
    ReviewNotFoundError,
    ReviewValidationError,
)
from nemesis.trust.review import (
    PRIORITY,
    ReviewDecisionKind,
    ReviewReason,
    decide,
    evidence_hash,
    get_item,
    labels_for_training,
    list_items,
    queue,
)
from tests.conftest import postgres_required

pytestmark = [postgres_required, pytest.mark.integration]

BASE = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


async def make_complaint(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    reported_at: datetime | None = None,
    latitude: float = 18.5204,
    longitude: float = 73.8567,
    device_fingerprint: str | None = None,
    description_text: str | None = "lift stuck between floors",
    photo_uri: str | None = None,
) -> uuid.UUID:
    """One real complaint chain plus its projection. Returns the id."""
    complaint_id = uuid.uuid4()
    event = await EventStore(session).append(
        entity_id=complaint_id,
        event_type="complaint_submitted",
        payload={
            "latitude": latitude,
            "longitude": longitude,
            "description_text": description_text,
            "photo_url": photo_uri,
            "audio_url": None,
            "locale": "en",
            "device_fingerprint": device_fingerprint,
            "submitted_via": "web",
        },
        tenant_id=tenant_id,
        occurred_at=reported_at or BASE,
    )
    del event
    projection = await replay_entity(
        session, tenant_id=tenant_id, entity_type="complaint", entity_id=complaint_id
    )
    await write_projection(session, tenant_id=tenant_id, result=projection)
    await session.flush()
    return complaint_id


@pytest.fixture
def sessions(migrated_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(migrated_engine, expire_on_commit=False)


EVIDENCE = {"rule_id": "gas_leak", "matched_terms": ["gas leak"], "severity_floor": 9.0}


# ---------------------------------------------------------------------------
# Queueing
# ---------------------------------------------------------------------------


async def test_a_flag_reaches_the_queue_with_its_evidence(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> None:
    """§11.4's whole requirement, at its smallest."""
    with tenant_scope(tenant_id):
        async with sessions() as session:
            complaint_id = await make_complaint(session, tenant_id=tenant_id)
            queued = await queue(
                session,
                tenant_id=tenant_id,
                complaint_id=complaint_id,
                reason=ReviewReason.SAFETY_TRIGGER,
                evidence=EVIDENCE,
                trust_score=0.0,
            )
            await session.commit()

            item = await get_item(session, tenant_id=tenant_id, item_id=queued.review_item_id)

    assert queued.is_new
    assert queued.occurrences == 1
    assert item.status == "open"
    assert item.evidence == EVIDENCE
    assert item.priority == PRIORITY[ReviewReason.SAFETY_TRIGGER]


async def test_a_repeat_escalates_rather_than_duplicating(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> None:
    """One judgement, not three.

    A device tripping the velocity check three times in an hour is one thing a
    human has to decide. Three rows would be three decisions, three Phase 11
    labels for one example, and a queue length that counts retries.
    """
    with tenant_scope(tenant_id):
        async with sessions() as session:
            complaint_id = await make_complaint(session, tenant_id=tenant_id)
            first = await queue(
                session,
                tenant_id=tenant_id,
                complaint_id=complaint_id,
                reason=ReviewReason.DEVICE_VELOCITY,
                evidence={"observed": 13},
                trust_score=-0.3,
            )
            second = await queue(
                session,
                tenant_id=tenant_id,
                complaint_id=complaint_id,
                reason=ReviewReason.DEVICE_VELOCITY,
                evidence={"observed": 19},
                trust_score=-0.6,
            )
            await session.commit()

            total = (
                await session.execute(
                    select(func.count(ReviewQueueItem.id)).where(
                        ReviewQueueItem.tenant_id == tenant_id
                    )
                )
            ).scalar_one()
            item = await get_item(session, tenant_id=tenant_id, item_id=first.review_item_id)

    assert first.review_item_id == second.review_item_id
    assert second.occurrences == 2
    assert not second.is_new
    assert total == 1
    # The bundle is *replaced*, not merged: the reviewer must see the state that
    # most recently justified the flag, not a composite of moments that never
    # existed together.
    assert item.evidence == {"observed": 19}
    assert item.trust_score == pytest.approx(-0.6)


async def test_two_different_reasons_are_two_items(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> None:
    """Two judgements, because a reviewer may reasonably accept one and reject
    the other — they are different questions with different evidence."""
    with tenant_scope(tenant_id):
        async with sessions() as session:
            complaint_id = await make_complaint(session, tenant_id=tenant_id)
            await queue(
                session,
                tenant_id=tenant_id,
                complaint_id=complaint_id,
                reason=ReviewReason.DEVICE_VELOCITY,
                evidence={"observed": 13},
                trust_score=-0.3,
            )
            await queue(
                session,
                tenant_id=tenant_id,
                complaint_id=complaint_id,
                reason=ReviewReason.EXIF_MISMATCH,
                evidence={"distance_meters": 3100.0},
                trust_score=-0.7,
            )
            await session.commit()
            rows, total = await list_items(session, tenant_id=tenant_id)

    assert total == 2
    assert {row.reason for row in rows} == {"device_velocity", "exif_mismatch"}


async def test_the_queue_orders_a_danger_signal_ahead_of_a_fraud_flag(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> None:
    """§11.2's priority, defended at the layer that would otherwise lose it.

    The dedicated safety *queue* keeps a danger signal off a saturated ML
    worker. It does nothing about a danger signal sitting behind forty velocity
    flags in front of a human, which is the same failure one layer up.
    """
    with tenant_scope(tenant_id):
        async with sessions() as session:
            noisy = await make_complaint(session, tenant_id=tenant_id)
            dangerous = await make_complaint(session, tenant_id=tenant_id)
            await queue(
                session,
                tenant_id=tenant_id,
                complaint_id=noisy,
                reason=ReviewReason.DEVICE_VELOCITY,
                evidence={"observed": 40},
                trust_score=-0.3,
            )
            await queue(
                session,
                tenant_id=tenant_id,
                complaint_id=dangerous,
                reason=ReviewReason.SAFETY_TRIGGER,
                evidence=EVIDENCE,
                trust_score=0.0,
            )
            await session.commit()
            rows, _ = await list_items(session, tenant_id=tenant_id)

    assert rows[0].reason == "safety_trigger"


async def test_an_item_from_another_tenant_is_not_found(
    sessions: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
    other_tenant_id: uuid.UUID,
) -> None:
    """ "Exists but belongs to someone else" and "does not exist" are one answer,
    or a queue item id becomes a way to enumerate another customer's flags."""
    with tenant_scope(other_tenant_id):
        async with sessions() as session:
            complaint_id = await make_complaint(session, tenant_id=other_tenant_id)
            queued = await queue(
                session,
                tenant_id=other_tenant_id,
                complaint_id=complaint_id,
                reason=ReviewReason.LOW_TRUST,
                evidence={"trust_score": -0.9},
                trust_score=-0.9,
            )
            await session.commit()

    with tenant_scope(tenant_id):
        async with sessions() as session:
            with pytest.raises(ReviewNotFoundError):
                await get_item(session, tenant_id=tenant_id, item_id=queued.review_item_id)
            _, total = await list_items(session, tenant_id=tenant_id)
    assert total == 0


# ---------------------------------------------------------------------------
# Deciding — and the Phase 11 label
# ---------------------------------------------------------------------------


async def test_a_decision_is_recorded_as_a_label_against_its_own_evidence(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> None:
    """Architectural principle 4, made checkable.

    A label whose inputs cannot be identified is noise with a confident tone.
    ``evidence_hash`` is recomputed from the item's frozen bundle, so the pair
    can be verified rather than assumed.
    """
    with tenant_scope(tenant_id):
        async with sessions() as session:
            complaint_id = await make_complaint(session, tenant_id=tenant_id)
            queued = await queue(
                session,
                tenant_id=tenant_id,
                complaint_id=complaint_id,
                reason=ReviewReason.SAFETY_TRIGGER,
                evidence=EVIDENCE,
                trust_score=0.0,
            )
            record = await decide(
                session,
                tenant_id=tenant_id,
                item_id=queued.review_item_id,
                decision=ReviewDecisionKind.APPROVE,
                rationale="Confirmed: gas smell reported by two neighbours.",
                decided_by_label="ops-oncall",
            )
            await session.commit()

            item = await get_item(session, tenant_id=tenant_id, item_id=queued.review_item_id)
            labels = await labels_for_training(session, tenant_id=tenant_id)

    assert record.decision == "approve"
    assert record.evidence_hash == queued.evidence_hash == evidence_hash(EVIDENCE)
    assert item.status == "decided"
    assert item.decided_at is not None
    assert [label.id for label in labels] == [record.id]


async def test_the_decision_is_on_the_complaints_own_chain(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> None:
    """§9.1: the state change and its event commit together.

    A label that survived a rollback would be a training example for something
    that did not happen.
    """
    with tenant_scope(tenant_id):
        async with sessions() as session:
            complaint_id = await make_complaint(session, tenant_id=tenant_id)
            queued = await queue(
                session,
                tenant_id=tenant_id,
                complaint_id=complaint_id,
                reason=ReviewReason.EXIF_MISMATCH,
                evidence={"distance_meters": 3100.0},
                trust_score=-0.4,
            )
            await decide(
                session,
                tenant_id=tenant_id,
                item_id=queued.review_item_id,
                decision=ReviewDecisionKind.REJECT,
                rationale="Photograph is of a different street.",
                decided_by_label="ops-oncall",
            )
            await session.commit()

            events = await EventStore(session).read_stream(
                entity_type="complaint", entity_id=complaint_id
            )

    assert [event.event_type for event in events] == ["complaint_submitted", "review_decided"]
    assert events[-1].payload["decision"] == "reject"
    assert events[-1].payload["decided_by_label"] == "ops-oncall"


async def test_a_second_decision_on_one_item_is_refused(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> None:
    """Two contradictory labels for one example, with no way to choose.

    Changing a decision is a new item raised against the new evidence — which is
    also the only version of the change that leaves an honest record of what was
    thought first.
    """
    with tenant_scope(tenant_id):
        async with sessions() as session:
            complaint_id = await make_complaint(session, tenant_id=tenant_id)
            queued = await queue(
                session,
                tenant_id=tenant_id,
                complaint_id=complaint_id,
                reason=ReviewReason.SAFETY_TRIGGER,
                evidence=EVIDENCE,
                trust_score=0.0,
            )
            await decide(
                session,
                tenant_id=tenant_id,
                item_id=queued.review_item_id,
                decision=ReviewDecisionKind.APPROVE,
                rationale="Confirmed.",
                decided_by_label="ops",
            )
            await session.commit()

            with pytest.raises(ReviewConflictError, match="already decided"):
                await decide(
                    session,
                    tenant_id=tenant_id,
                    item_id=queued.review_item_id,
                    decision=ReviewDecisionKind.REJECT,
                    rationale="Changed my mind.",
                    decided_by_label="ops",
                )


async def test_a_decision_with_no_rationale_is_refused(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> None:
    """§11.4 is an accountability surface. A decision that answers "what" and
    refuses to answer "why" is the objection ``admin_action.justification``
    already closes elsewhere."""
    with tenant_scope(tenant_id):
        async with sessions() as session:
            complaint_id = await make_complaint(session, tenant_id=tenant_id)
            queued = await queue(
                session,
                tenant_id=tenant_id,
                complaint_id=complaint_id,
                reason=ReviewReason.LOW_TRUST,
                evidence={"trust_score": -0.9},
                trust_score=-0.9,
            )
            with pytest.raises(ReviewValidationError):
                await decide(
                    session,
                    tenant_id=tenant_id,
                    item_id=queued.review_item_id,
                    decision=ReviewDecisionKind.APPROVE,
                    rationale="   ",
                    decided_by_label="ops",
                )


async def test_escalate_is_its_own_label_and_not_a_rejection(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> None:
    """ "This needs someone with more authority than me" is not "this is false".

    Training on an escalation as though it were a rejection teaches the model
    the opposite of what the human expressed, which is why the three actions are
    three values rather than a boolean and a note.
    """
    with tenant_scope(tenant_id):
        async with sessions() as session:
            complaint_id = await make_complaint(session, tenant_id=tenant_id)
            queued = await queue(
                session,
                tenant_id=tenant_id,
                complaint_id=complaint_id,
                reason=ReviewReason.GEOGRAPHIC_CLUSTER,
                evidence={"distinct_devices": 9},
                trust_score=-0.25,
            )
            record = await decide(
                session,
                tenant_id=tenant_id,
                item_id=queued.review_item_id,
                decision=ReviewDecisionKind.ESCALATE,
                rationale="Looks coordinated; needs the fraud team.",
                decided_by_label="ops",
            )
            await session.commit()
    assert record.decision == "escalate"


async def test_a_decided_reason_can_be_raised_again_later(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> None:
    """Why the unique index is partial.

    A total constraint would mean a complaint cleared of a velocity flag in
    March can never be flagged for velocity again, which turns one reviewer's
    judgement into a permanent exemption.
    """
    with tenant_scope(tenant_id):
        async with sessions() as session:
            complaint_id = await make_complaint(session, tenant_id=tenant_id)
            first = await queue(
                session,
                tenant_id=tenant_id,
                complaint_id=complaint_id,
                reason=ReviewReason.DEVICE_VELOCITY,
                evidence={"observed": 13},
                trust_score=-0.3,
            )
            await decide(
                session,
                tenant_id=tenant_id,
                item_id=first.review_item_id,
                decision=ReviewDecisionKind.REJECT,
                rationale="Legitimate volunteer clean-up drive.",
                decided_by_label="ops",
            )
            await session.commit()

            second = await queue(
                session,
                tenant_id=tenant_id,
                complaint_id=complaint_id,
                reason=ReviewReason.DEVICE_VELOCITY,
                evidence={"observed": 60},
                trust_score=-0.9,
            )
            await session.commit()
            rows, total = await list_items(session, tenant_id=tenant_id, status=None)

    assert second.review_item_id != first.review_item_id
    assert second.is_new
    assert total == 2
    assert {row.status for row in rows} == {"open", "decided"}


async def test_labels_can_be_filtered_by_reason_and_time(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> None:
    """The query ``ix_review_decisions_tenant_label`` exists to serve.

    Written now rather than in Phase 11, so the index decision is checkable
    today instead of being justified by a query nobody has written.
    """
    with tenant_scope(tenant_id):
        async with sessions() as session:
            for reason in (ReviewReason.EXIF_MISMATCH, ReviewReason.DEVICE_VELOCITY):
                complaint_id = await make_complaint(session, tenant_id=tenant_id)
                queued = await queue(
                    session,
                    tenant_id=tenant_id,
                    complaint_id=complaint_id,
                    reason=reason,
                    evidence={"k": reason.value},
                    trust_score=-0.4,
                )
                await decide(
                    session,
                    tenant_id=tenant_id,
                    item_id=queued.review_item_id,
                    decision=ReviewDecisionKind.APPROVE,
                    rationale="ok",
                    decided_by_label="ops",
                )
            await session.commit()

            only_exif = await labels_for_training(
                session, tenant_id=tenant_id, reason=ReviewReason.EXIF_MISMATCH
            )
            future = await labels_for_training(
                session,
                tenant_id=tenant_id,
                since=datetime.now(tz=UTC) + timedelta(days=1),
            )
            everything = await labels_for_training(session, tenant_id=tenant_id)

    assert [label.reason for label in only_exif] == ["exif_mismatch"]
    assert list(future) == []
    assert len(everything) == 2


async def test_the_decision_row_and_the_item_agree_on_the_complaint(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> None:
    """Phase 11 reads ``review_decisions`` without joining the queue, so the
    denormalised columns have to be right rather than merely present."""
    with tenant_scope(tenant_id):
        async with sessions() as session:
            complaint_id = await make_complaint(session, tenant_id=tenant_id)
            queued = await queue(
                session,
                tenant_id=tenant_id,
                complaint_id=complaint_id,
                reason=ReviewReason.PERCEPTUAL_DUPLICATE,
                evidence={"hamming_distance": 3},
                trust_score=-0.35,
            )
            await decide(
                session,
                tenant_id=tenant_id,
                item_id=queued.review_item_id,
                decision=ReviewDecisionKind.REJECT,
                rationale="Genuinely a second pothole on the same street.",
                decided_by_label="ops",
            )
            await session.commit()

            row = (
                await session.execute(
                    select(ReviewDecision).where(ReviewDecision.tenant_id == tenant_id)
                )
            ).scalar_one()

    assert row.complaint_id == complaint_id
    assert row.reason == "perceptual_duplicate"


def test_every_reason_has_a_priority() -> None:
    """A reason with no entry would raise a ``KeyError`` inside the queue call —
    on the submission path, for the flag nobody thought about."""
    assert set(PRIORITY) == set(ReviewReason)
