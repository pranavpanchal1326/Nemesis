"""§11.4 — the human review queue, and the feedback loop that hangs off it.

§11.4 is one paragraph and states a Nemesis-standard requirement: *no flag is
ever a dead end*. Every "flag for review" produced anywhere in this system has a
real destination — a filtered table showing the item, the evidence bundle, and
one-click approve / reject / escalate.

Three things this module does that the paragraph does not spell out, and each is
load-bearing:

**The bundle is frozen at queueing.** ``evidence`` is a snapshot written once,
never recomputed. Recomputing it on read would show a reviewer today's numbers
for a flag raised against last week's thresholds — and Phase 11 would then learn
from a label attached to evidence that never produced it. ``evidence_hash`` is
what makes the pairing checkable rather than assumed.

**A repeat raises ``occurrences``, not a second row.** A device tripping the
velocity check three times in an hour is one judgement a human has to make, not
three. The partial unique index on ``status = 'open'`` is what enforces it, and
it is partial so the same reason can legitimately be raised again months later
against new evidence.

**Every decision is a Phase 11 label, by construction rather than by intention.**
Architectural principle 4 — *every human decision is training data* — and
critique-log defect #6, which is that the previous revision of the plan threw
this loop away. ``review_decisions`` is written in the same transaction as the
event, carries the reason and the evidence hash, and is indexed for the query
Phase 11 will actually run: "every decision of this kind, for this tenant".

**Nothing here commits.** The same contract ``policy``, ``control_plane`` and
``simulation`` state, for the same reason: this is called from an HTTP handler,
from the Celery pipeline, and from tests, and a module that knew about any one
of those could not be called from the other two.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.db.models.trust import ReviewDecision, ReviewQueueItem
from nemesis.events.canonical import canonicalise
from nemesis.events.store import EventStore
from nemesis.observability import metrics
from nemesis.observability.logging import get_logger
from nemesis.outbox import writer as outbox
from nemesis.projections.replay import replay_entity
from nemesis.projections.writer import write_projection
from nemesis.trust.errors import (
    ReviewConflictError,
    ReviewNotFoundError,
    ReviewValidationError,
)

log = get_logger(__name__)

COMPLAINT: Final = "complaint"


class ReviewReason(StrEnum):
    """Why an item is in the queue. Platform structure — each value names code.

    A closed set for the reason ``PolicyKind`` is closed: a reason needs a check
    that raises it, an evidence shape, and a priority, so a tenant cannot invent
    one and an unrecognised value would only mean a typo nothing will ever read.

    ``LOW_TRUST`` is the backstop and is not a detector. Three mild signals that
    each decline to queue on their own still add up to a report worth a human's
    attention; without it they add up to nothing, which is how a trust score
    becomes a number that is computed and never used.
    """

    SAFETY_TRIGGER = "safety_trigger"
    EXIF_MISMATCH = "exif_mismatch"
    PERCEPTUAL_DUPLICATE = "perceptual_duplicate"
    DEVICE_VELOCITY = "device_velocity"
    GEOGRAPHIC_CLUSTER = "geographic_cluster"
    LOW_TRUST = "low_trust"


class ReviewDecisionKind(StrEnum):
    """§11.4's three actions, and only those three.

    ``ESCALATE`` is not "reject with feeling". It is the outcome that says *this
    needs someone with more authority than me*, and it is a distinct label for
    Phase 11 precisely because it means the reviewer could not decide — training
    on it as though it were a rejection would teach the model the opposite of
    what the human expressed.
    """

    APPROVE = "approve"
    REJECT = "reject"
    ESCALATE = "escalate"


#: Reason → queue priority, lower first. Declared as a table rather than
#: computed, so the order a reviewer sees is reviewable itself.
#:
#: The safety trigger is first by a wide margin and that is §11.2, not a
#: preference: a danger signal waiting behind forty velocity flags is the
#: failure mode the dedicated safety queue exists one layer down to prevent, and
#: it would be reintroduced here if the ordering were left to insertion time.
PRIORITY: Final[dict[ReviewReason, int]] = {
    ReviewReason.SAFETY_TRIGGER: 0,
    ReviewReason.EXIF_MISMATCH: 40,
    ReviewReason.PERCEPTUAL_DUPLICATE: 50,
    ReviewReason.GEOGRAPHIC_CLUSTER: 60,
    ReviewReason.DEVICE_VELOCITY: 70,
    ReviewReason.LOW_TRUST: 90,
}


def evidence_hash(evidence: Mapping[str, Any]) -> str:
    """Canonical hash of an evidence bundle.

    The same canonicaliser the event chain uses, so the hash does not move when
    a dict is built in a different order or when JSON serialisation reorders
    keys. A hash computed by ``json.dumps`` would be stable within one process
    and not across two, which is the worst possible property for a value whose
    only job is to match across a queueing and a decision hours apart.
    """
    return hashlib.sha256(canonicalise(dict(evidence))).hexdigest()


@dataclass(frozen=True, slots=True)
class QueuedReview:
    """What queueing produced, ready to become a ``review_queued`` payload."""

    review_item_id: uuid.UUID
    reason: ReviewReason
    priority: int
    occurrences: int
    trust_score: float
    evidence_hash: str
    #: False when an open item for this (complaint, reason) already existed and
    #: this call only raised its count. The caller still emits the event — the
    #: log should show the escalation — but a notification path needs to know
    #: this is not a new judgement arriving.
    is_new: bool


async def queue(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    complaint_id: uuid.UUID,
    reason: ReviewReason,
    evidence: Mapping[str, Any],
    trust_score: float,
) -> QueuedReview:
    """Raise, or escalate, one queue item. Writes the row; emits no event.

    The event is the caller's to emit, because the two callers put it in
    different places: the trust stage returns it as an ``EmittedEvent`` for the
    orchestrator to append atomically with everything else the stage produced,
    while a future direct-flag path would append it itself. A function that did
    both would have to know which, and would get it wrong for one of them.

    The upsert is ``ON CONFLICT DO UPDATE`` against the partial unique index, so
    a concurrent second flag for the same reason increments rather than raising
    — two workers processing a redelivered stage must not turn one judgement
    into an integrity error on a citizen's submission.
    """
    digest = evidence_hash(evidence)
    priority = PRIORITY[reason]
    statement = (
        pg_insert(ReviewQueueItem)
        .values(
            tenant_id=tenant_id,
            complaint_id=complaint_id,
            reason=reason.value,
            status="open",
            priority=priority,
            occurrences=1,
            evidence=dict(evidence),
            trust_score=trust_score,
        )
        .on_conflict_do_update(
            index_elements=[
                ReviewQueueItem.tenant_id,
                ReviewQueueItem.complaint_id,
                ReviewQueueItem.reason,
            ],
            # ``text``, not ``ReviewQueueItem.status == "open"``. The latter
            # renders the predicate as a **bound parameter**, and Postgres
            # cannot infer a partial unique index from a predicate it can only
            # see as ``status = $9`` — the statement fails at plan time with
            # "no unique or exclusion constraint matching the ON CONFLICT
            # specification", on the second flag for a complaint rather than the
            # first. The literal has to match the index's own predicate exactly.
            index_where=text("status = 'open'"),
            set_={
                "occurrences": ReviewQueueItem.occurrences + 1,
                # The bundle is replaced on an escalation, not merged. The
                # reviewer must see the state that most recently justified the
                # flag; a merged bundle would be a composite of several moments
                # that never existed together, which is worse than either.
                "evidence": dict(evidence),
                "trust_score": trust_score,
                "updated_at": datetime.now(tz=UTC),
            },
        )
        .returning(
            ReviewQueueItem.id,
            ReviewQueueItem.occurrences,
        )
    )
    row = (await session.execute(statement)).one()
    is_new = int(row.occurrences) == 1

    metrics.review_queue_items_total.labels(reason=reason.value).inc()
    log.info(
        "review_queued",
        complaint_id=str(complaint_id),
        reason=reason.value,
        occurrences=int(row.occurrences),
        is_new=is_new,
    )
    return QueuedReview(
        review_item_id=row.id,
        reason=reason,
        priority=priority,
        occurrences=int(row.occurrences),
        trust_score=trust_score,
        evidence_hash=digest,
        is_new=is_new,
    )


async def list_items(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    status: str | None = "open",
    reason: ReviewReason | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[Sequence[ReviewQueueItem], int]:
    """§11.4's filtered table, plus the total so a client can page it.

    Ordered by priority then age. Both, not either: priority alone leaves the
    oldest low-priority item permanently at the bottom of a page nobody reaches,
    and age alone puts a gas-leak trigger behind whatever arrived first.
    """
    # The optional filters are composed; the tenant filter is written inline at
    # both ``select()`` calls below. ``check_tenant_scoping.py`` reads the AST at
    # the call site, so a tenant predicate hidden inside a list is one it cannot
    # verify — and an isolation guarantee nothing can check is not one.
    filters = []
    if status is not None:
        filters.append(ReviewQueueItem.status == status)
    if reason is not None:
        filters.append(ReviewQueueItem.reason == reason.value)

    total = int(
        (
            await session.execute(
                select(func.count(ReviewQueueItem.id)).where(
                    ReviewQueueItem.tenant_id == tenant_id, *filters
                )
            )
        ).scalar_one()
    )
    rows = (
        (
            await session.execute(
                select(ReviewQueueItem)
                .where(ReviewQueueItem.tenant_id == tenant_id, *filters)
                .order_by(
                    ReviewQueueItem.priority.asc(),
                    ReviewQueueItem.created_at.asc(),
                )
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return rows, total


async def get_item(
    session: AsyncSession, *, tenant_id: uuid.UUID, item_id: uuid.UUID
) -> ReviewQueueItem:
    """One item, tenant-scoped. Raises rather than returning ``None``.

    The tenant filter is in the query and not applied afterwards: fetching by id
    and then comparing tenants leaks existence through timing and through any
    logging that happens in between, and it is one refactor away from the
    comparison being dropped.
    """
    item = (
        await session.execute(
            select(ReviewQueueItem).where(
                ReviewQueueItem.tenant_id == tenant_id,
                ReviewQueueItem.id == item_id,
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise ReviewNotFoundError(f"no review item {item_id} for this tenant")
    return item


async def decide(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    item_id: uuid.UUID,
    decision: ReviewDecisionKind,
    rationale: str,
    decided_by: uuid.UUID | None = None,
    decided_by_label: str,
    correlation_id: str | None = None,
) -> ReviewDecision:
    """Attach the human judgement, record the label, and append the event.

    Everything in the caller's transaction: the decision row, the item's status,
    the ``review_decided`` event on the complaint's own chain, the projection
    that event implies, and the outbox row that publishes it. §9.1 requires the
    state change and its event to commit together, and a label that survived a
    rollback would be a training example for something that did not happen.

    ``evidence_hash`` is recomputed from the item's frozen bundle rather than
    accepted from the caller. A hash supplied by the client would let a decision
    claim to have been made against evidence it never saw, which is the one
    thing the hash exists to prevent.
    """
    if not rationale.strip():
        raise ReviewValidationError(
            "a review decision must carry a rationale. §11.4 makes this queue an "
            "accountability surface, and a decision that answers 'what' while "
            "refusing to answer 'why' is the objection admin_action.justification "
            "already closes elsewhere."
        )

    item = await get_item(session, tenant_id=tenant_id, item_id=item_id)
    if item.status != "open":
        raise ReviewConflictError(
            f"review item {item_id} was already decided at {item.decided_at}. §11.4 "
            f"takes one judgement per item — a second would be a second Phase 11 "
            f"label for one example with no way to choose between them. Raise a new "
            f"item against the new evidence instead."
        )

    # Read out of the ORM object before anything else touches the session. The
    # transition below is an explicit UPDATE rather than a mutation of `item`,
    # so nothing after this point may depend on the instance staying loaded.
    complaint_id = item.complaint_id
    reason = item.reason
    digest = evidence_hash(item.evidence)
    now = datetime.now(tz=UTC)

    record = ReviewDecision(
        tenant_id=tenant_id,
        review_item_id=item_id,
        complaint_id=complaint_id,
        reason=reason,
        decision=decision.value,
        rationale=rationale.strip(),
        decided_by=decided_by,
        decided_by_label=decided_by_label,
        evidence_hash=digest,
    )
    session.add(record)

    # An explicit, tenant-scoped UPDATE rather than ``item.status = "decided"``.
    # The ORM would emit ``UPDATE ... WHERE id = :id`` with no tenant predicate,
    # which ``tenancy.guard`` refuses at execution — correctly, because a
    # primary key is not a tenant boundary and a compromised or mistaken id
    # would otherwise reach across one. Writing the predicate makes the
    # statement provably scoped and readable as such.
    await session.execute(
        update(ReviewQueueItem)
        .where(ReviewQueueItem.tenant_id == tenant_id, ReviewQueueItem.id == item_id)
        .values(status="decided", decided_at=now, updated_at=now)
    )
    # The instance is now stale, and leaving it in the identity map would hand
    # the next ``get_item`` in this session a row that still says "open".
    session.expunge(item)
    await session.flush()

    event = await EventStore(session).append(
        entity_id=complaint_id,
        event_type="review_decided",
        payload={
            "review_item_id": str(item_id),
            "reason": reason,
            "decision": decision.value,
            "rationale": record.rationale,
            "decided_by": str(decided_by) if decided_by else None,
            "decided_by_label": decided_by_label,
            "evidence_hash": digest,
        },
        tenant_id=tenant_id,
        actor_id=decided_by,
        correlation_id=correlation_id,
        occurred_at=now,
        # Keyed on the item, not on the decision: the unique constraint already
        # makes a second decision impossible, and a key derived from the payload
        # would let two *different* decisions on one item both append.
        idempotency_key=f"review:decided:{item_id}",
    )
    projection = await replay_entity(
        session, tenant_id=tenant_id, entity_type=COMPLAINT, entity_id=complaint_id
    )
    await write_projection(session, tenant_id=tenant_id, result=projection)
    await outbox.enqueue(session, event)

    metrics.review_decisions_total.labels(reason=reason, decision=decision.value).inc()
    log.info(
        "review_decided",
        complaint_id=str(complaint_id),
        review_item_id=str(item_id),
        reason=reason,
        decision=decision.value,
    )
    return record


async def labels_for_training(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    reason: ReviewReason | None = None,
    since: datetime | None = None,
    limit: int = 1000,
) -> Sequence[ReviewDecision]:
    """Every decision, as Phase 11 will read them.

    Here rather than in Phase 11 for one reason: it is the query the index
    ``ix_review_decisions_tenant_label`` exists to serve, and an index whose
    query lives in a phase that has not been written yet is an index nobody can
    show is the right one. Writing the reader now is what makes the schema
    decision checkable today.
    """
    filters = []
    if reason is not None:
        filters.append(ReviewDecision.reason == reason.value)
    if since is not None:
        filters.append(ReviewDecision.created_at >= since)
    return (
        (
            await session.execute(
                select(ReviewDecision)
                # Inline, for the reason ``list_items`` states.
                .where(ReviewDecision.tenant_id == tenant_id, *filters)
                .order_by(ReviewDecision.created_at.asc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )


__all__ = [
    "PRIORITY",
    "QueuedReview",
    "ReviewDecisionKind",
    "ReviewReason",
    "decide",
    "evidence_hash",
    "get_item",
    "labels_for_training",
    "list_items",
    "queue",
]
