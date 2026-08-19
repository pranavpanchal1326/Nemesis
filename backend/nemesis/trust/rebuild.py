"""Rebuilding the review queue from the event log.

§9.1 says current state is derived from the log, and ``projections.writer``
makes that true for ``complaints``, ``complaint_clusters`` and ``work_orders``
by construction — the writer is the only thing that touches them. The review
tables are written by a stage and by an HTTP handler instead, for the shape
reason ``db.models.trust`` gives, which means "derived from the log" becomes a
*claim* rather than a structural fact.

This module is what turns it back into a fact. It rebuilds both review tables
from ``review_queued`` and ``review_decided`` alone, and
``test_trust_rebuild.py`` runs it against a populated database and asserts the
result matches what the pipeline wrote, field for field. A change that started
writing something the log does not carry fails that test.

**``submission_media`` is deliberately not rebuilt.** It holds the values §22.4
requires to be purged after ninety days, and an append-only chain is the one
place a value can never be expired from — see the model's docstring for the full
argument. Attempting to rebuild it would either produce a table with holes in it
or require the log to carry data it must not.

**This is a repair tool, not a runtime path.** Nothing calls it in normal
operation. It exists for the case the projection layer already anticipates — a
bug wrote the wrong thing, and the log is the authority that can correct it —
and for the test that keeps the claim honest.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.db.models.event import Event
from nemesis.db.models.trust import ReviewDecision, ReviewQueueItem
from nemesis.observability.logging import get_logger
from nemesis.trust.review import PRIORITY, ReviewReason

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RebuildResult:
    items: int
    decisions: int
    #: Queue items whose evidence bundle could not be restored, because the log
    #: carries the bundle's *hash* and not the bundle. Counted and returned
    #: rather than silently written as ``{}``: an empty evidence bundle in front
    #: of a reviewer looks like a flag with no reason, and §11.4 exists to stop
    #: exactly that.
    evidence_unavailable: int


async def rebuild_reviews(
    session: AsyncSession, *, tenant_id: uuid.UUID, preserve_evidence: bool = True
) -> RebuildResult:
    """Reconstruct both review tables for one tenant from its complaint chains.

    ``preserve_evidence`` reads the existing bundles before deleting and carries
    them across by item id. It defaults to ``True`` and the alternative is worth
    stating: with it off, a rebuild produces a queue whose items are correct in
    every field except the one a human actually reads. That is the honest cost
    of keeping the bundle out of the log (§22.4), and it is why this is a repair
    tool rather than something that runs on a schedule.

    Tenant-scoped throughout, and it deletes before it writes: a rebuild that
    merged into existing rows could not remove an item the log does not explain,
    which is the main thing a rebuild is for.
    """
    existing_evidence: dict[uuid.UUID, Mapping[str, Any]] = {}
    if preserve_evidence:
        rows = await session.execute(
            select(ReviewQueueItem.id, ReviewQueueItem.evidence).where(
                ReviewQueueItem.tenant_id == tenant_id
            )
        )
        existing_evidence = {row.id: row.evidence for row in rows}

    # Decisions first: they reference items, and the foreign key is RESTRICT.
    await session.execute(delete(ReviewDecision).where(ReviewDecision.tenant_id == tenant_id))
    await session.execute(delete(ReviewQueueItem).where(ReviewQueueItem.tenant_id == tenant_id))
    # The Core deletes above do not touch the identity map, so any row this
    # session had already loaded is still sitting in it — and re-adding an
    # object with the same primary key would then be a conflict against a ghost.
    session.expire_all()

    events = (
        (
            await session.execute(
                select(Event)
                .where(
                    Event.tenant_id == tenant_id,
                    Event.entity_type == "complaint",
                    Event.event_type.in_(("review_queued", "review_decided")),
                )
                # Chain, then position within it. Not ``occurred_at``: two
                # events appended in one transaction share it, so an ordering
                # that led with it would be non-deterministic exactly where the
                # order matters — a queueing and its decision, or two
                # occurrences of one reason.
                .order_by(Event.entity_id.asc(), Event.sequence.asc())
            )
        )
        .scalars()
        .all()
    )

    items: dict[uuid.UUID, ReviewQueueItem] = {}
    decisions: list[ReviewDecision] = []
    unavailable = 0

    # **Three passes, and every write is an INSERT.** The decisions are read
    # first so an item can be *constructed* already decided, rather than
    # inserted open and then updated: ``tenancy.guard`` refuses an ORM UPDATE
    # keyed on a primary key with no tenant predicate — correctly, because a
    # primary key is not a tenant boundary — and an INSERT needs no predicate
    # because the ``NOT NULL`` tenant column already makes an unscoped one
    # impossible. Building the final state directly is both faster and the only
    # version of this that is provably scoped.
    decided: dict[uuid.UUID, Event] = {}
    for event in events:
        if event.event_type == "review_decided":
            decided[uuid.UUID(str(event.payload["review_item_id"]))] = event

    for event in events:
        if event.event_type != "review_queued":
            continue
        payload: Mapping[str, Any] = event.payload
        item_id = uuid.UUID(str(payload["review_item_id"]))
        reason = str(payload["reason"])
        item = items.get(item_id)
        if item is None:
            evidence = existing_evidence.get(item_id)
            if evidence is None:
                unavailable += 1
                evidence = {
                    "unavailable": True,
                    "evidence_hash": str(payload["evidence_hash"]),
                    "note": (
                        "the bundle is not in the log by design (§22.4 requires it to "
                        "expire); only its hash is. See trust.rebuild."
                    ),
                }
            closing = decided.get(item_id)
            item = ReviewQueueItem(
                id=item_id,
                tenant_id=tenant_id,
                complaint_id=event.entity_id,
                reason=reason,
                status="open" if closing is None else "decided",
                decided_at=None if closing is None else closing.occurred_at,
                priority=int(payload.get("priority", PRIORITY[ReviewReason(reason)])),
                occurrences=int(payload["occurrences"]),
                evidence=dict(evidence),
                trust_score=float(payload["trust_score"]),
                created_at=event.occurred_at,
                updated_at=event.occurred_at if closing is None else closing.occurred_at,
            )
            items[item_id] = item
            session.add(item)
        else:
            # A repeat, and the object is still pending — not yet flushed — so
            # setting these attributes shapes the INSERT rather than producing
            # an UPDATE. The event carries the count the queue reached, so the
            # rebuild takes it rather than incrementing: incrementing would
            # disagree with the log the moment one event was archived.
            item.occurrences = int(payload["occurrences"])
            item.trust_score = float(payload["trust_score"])
            item.evidence = dict(existing_evidence.get(item_id, item.evidence))

    # Items before decisions: the foreign key is RESTRICT, so every item row has
    # to exist in the database before any decision row is inserted. Relying on
    # the unit of work's table-dependency sort would be relying on an ordering
    # nothing here states.
    await session.flush()

    for item_id, event in decided.items():
        if item_id not in items:  # pragma: no cover — a decision with no queueing event
            log.warning(
                "review_rebuild_orphan_decision",
                review_item_id=str(item_id),
                note="review_decided with no review_queued on the same chain",
            )
            continue
        payload = event.payload
        decisions.append(
            ReviewDecision(
                tenant_id=tenant_id,
                review_item_id=item_id,
                complaint_id=event.entity_id,
                reason=str(payload["reason"]),
                decision=str(payload["decision"]),
                rationale=str(payload["rationale"]),
                decided_by=_optional_uuid(payload.get("decided_by")),
                decided_by_label=str(payload["decided_by_label"]),
                evidence_hash=str(payload["evidence_hash"]),
                created_at=event.occurred_at,
                updated_at=event.occurred_at,
            )
        )

    session.add_all(decisions)
    await session.flush()

    log.info(
        "review_queue_rebuilt",
        tenant_id=str(tenant_id),
        items=len(items),
        decisions=len(decisions),
        evidence_unavailable=unavailable,
    )
    return RebuildResult(
        items=len(items), decisions=len(decisions), evidence_unavailable=unavailable
    )


def _optional_uuid(value: Any) -> uuid.UUID | None:
    return uuid.UUID(str(value)) if value else None


__all__ = ["RebuildResult", "rebuild_reviews"]
