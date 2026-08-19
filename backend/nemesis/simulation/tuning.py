"""Dedup threshold proposals, derived from the merges humans undid.

§13.3's promise is that the rubric improves as resolution data accumulates. This
is the mechanism for the dedup half of it, and the shape it takes is constrained
by what the log actually contains — which is worth being exact about, because
the tempting version of this feature is not supported by the evidence.

**The only human dedup signal in the catalog is a revert.**
``cluster_merge_reverted`` records that somebody looked at a merge and said no.
There is no event for the opposite — a human noticing two clusters that should
have been one — because nothing in the product lets them say so yet; Phase 10
owns the merge review queue. So the evidence available is entirely one-sided.

**Therefore a proposal can only ever be more conservative.** Every revert says
"this confidence was not high enough to merge on", so the only inference the
data supports is *raise the threshold*. That is not a limitation to apologise
for: §14.3 already establishes the asymmetry — a false merge suppresses a
genuine citizen report, an unmerged duplicate costs an operator some time — and
a tuner that could only ever err toward the cheaper mistake is the right tuner
to ship first. A version that also lowered thresholds would need evidence that
does not exist, and would produce it by assuming that everything not reverted
was correct, which assumes exactly what it is trying to measure.

**Nothing is applied.** A proposal becomes a *draft* revision through the
ordinary ``policy.service.draft`` path, with a ``change_reason`` naming the
reverts it was derived from, and then it walks review and approval like anything
else. §13.3 wants a feedback loop, not an autonomous one: a threshold that
retunes itself overnight is a system where nobody can answer "why did this
change", which is the failure the whole policy phase exists to prevent.

**Below the evidence floor there is no proposal, not a weak one.** Two reverts
is one operator having a bad afternoon. ``MINIMUM_REVERTS`` is the point at
which a pattern is worth putting in front of somebody, and under it this module
returns nothing rather than a suggestion hedged with a confidence score that
would be read as a recommendation anyway.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.db.models.event import Event
from nemesis.db.models.policy import PolicyVersion
from nemesis.domain.lifecycle import EntityType
from nemesis.observability.logging import get_logger
from nemesis.policy import service as policy_service
from nemesis.policy.documents import DedupBand, DedupThresholds, PolicyKind
from nemesis.policy.resolver import category_lineage, resolve_dedup_band
from nemesis.simulation import bundles, corpus
from nemesis.simulation.errors import SimulationValidationError

log = get_logger(__name__)

#: How many reverted merges a band needs before its threshold is worth
#: questioning. Under this the signal is indistinguishable from one operator
#: having a bad afternoon, and a proposal presented anyway would be read as a
#: recommendation whatever hedging surrounded it.
MINIMUM_REVERTS: Final = 5

#: How far above the worst reverted confidence a proposed threshold sits.
#: Small, and above zero: setting the threshold *at* the reverted confidence
#: would leave that exact merge still qualifying, since ``dedup_outcome`` is
#: inclusive at the lower edge of each band.
THRESHOLD_MARGIN: Final = 0.01

_REVERTED: Final = "cluster_merge_reverted"
_MATCH_FOUND: Final = "cluster_match_found"


@dataclass(frozen=True, slots=True)
class RevertedMerge:
    """One merge a human undid, with the confidence that produced it."""

    complaint_id: uuid.UUID
    cluster_id: uuid.UUID
    combined_confidence: float
    category: str | None
    reverted_at: datetime
    reason: str


@dataclass(frozen=True, slots=True)
class BandProposal:
    """A suggested threshold change for one band, and the evidence for it.

    ``evidence`` is bounded and carried in full rather than summarised to a
    count. An operator asked to approve "raise the pothole merge threshold from
    0.85 to 0.93" will want to read the five reverts that produced the number,
    and a proposal that offers only the number is one they have to reconstruct
    by hand before they can honestly approve it.
    """

    category: str | None
    current_threshold: float
    proposed_threshold: float
    revert_count: int
    highest_reverted_confidence: float
    evidence: tuple[RevertedMerge, ...]

    @property
    def is_change(self) -> bool:
        return self.proposed_threshold > self.current_threshold


async def propose_dedup_thresholds(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    window: corpus.CorpusWindow,
    minimum_reverts: int = MINIMUM_REVERTS,
) -> tuple[BandProposal, ...]:
    """Read the reverts in a window and suggest thresholds that would have avoided them.

    Read-only and side-effect free: it returns proposals. Turning one into a
    document is ``draft_from_proposals``, which is a separate call because
    "show me what the data suggests" and "write that into the review queue" are
    different acts and only the second one should ever happen by accident.
    """
    reverts = await _reverted_merges(session, tenant_id=tenant_id, window=window)
    if not reverts:
        return ()

    live = await bundles.live_bundle(session, tenant_id=tenant_id)
    thresholds: DedupThresholds = live.dedup_thresholds.body

    lineages = await _lineages(
        session,
        tenant_id=tenant_id,
        categories={revert.category for revert in reverts},
    )

    # Grouped by the band that *decided*, not by the complaint's category. Two
    # categories sharing a band through the ancestor walk are governed by one
    # number, and proposing a change per category would produce two proposals
    # that overwrite each other in whichever order they were applied.
    grouped: dict[str | None, list[RevertedMerge]] = {}
    for revert in reverts:
        lineage = lineages.get(revert.category or "", (revert.category,) if revert.category else ())
        band = resolve_dedup_band(thresholds, lineage=lineage)
        grouped.setdefault(band.category, []).append(revert)

    proposals: list[BandProposal] = []
    for category, evidence in sorted(grouped.items(), key=lambda item: item[0] or ""):
        if len(evidence) < minimum_reverts:
            continue
        band = _band_for(thresholds, category)
        highest = max(revert.combined_confidence for revert in evidence)
        proposed = min(round(highest + THRESHOLD_MARGIN, 4), 1.0)
        if proposed <= band.merge_threshold:
            # The reverts all sat below the current threshold, which means the
            # threshold is not what produced them — the geo radius or the time
            # window is. Proposing a change that would not have prevented a
            # single one of these merges would be arithmetic dressed as insight.
            continue
        proposals.append(
            BandProposal(
                category=category,
                current_threshold=band.merge_threshold,
                proposed_threshold=proposed,
                revert_count=len(evidence),
                highest_reverted_confidence=highest,
                evidence=tuple(sorted(evidence, key=lambda item: -item.combined_confidence)),
            )
        )
    return tuple(proposals)


async def draft_from_proposals(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    proposals: Sequence[BandProposal],
    actor_id: uuid.UUID | None = None,
    correlation_id: str | None = None,
) -> PolicyVersion:
    """Write the proposals into a **draft** dedup document. Never activates one.

    Goes through ``policy.service.draft`` rather than writing a row, so the
    proposal enters the same lifecycle as a hand-authored change: it is
    validated, hashed, appended to the tenant chain, and it decides nothing
    until a human approves and activates it. A tuner with its own write path
    would be a second way for a document to exist — which is the thing Phase 6's
    single mutation path exists to prevent, arriving through a feature that
    sounds helpful.

    The ``change_reason`` names the reverts. Somebody reading the history in
    March needs to see "proposed from 7 reverted merges between June and
    December", not "automated tuning".
    """
    if not proposals:
        raise SimulationValidationError(
            "no proposals to draft. A draft with no changes would enter the review "
            "queue asking somebody to approve nothing."
        )

    live = await bundles.live_bundle(session, tenant_id=tenant_id)
    thresholds: DedupThresholds = live.dedup_thresholds.body
    by_category = {proposal.category: proposal for proposal in proposals if proposal.is_change}

    bands = tuple(
        band.model_copy(update={"merge_threshold": by_category[band.category].proposed_threshold})
        if band.category in by_category
        else band
        for band in thresholds.bands
    )
    updated = thresholds.model_copy(update={"bands": bands})

    total_reverts = sum(proposal.revert_count for proposal in by_category.values())
    described = ", ".join(
        f"{proposal.category or 'default'} {proposal.current_threshold}→"
        f"{proposal.proposed_threshold}"
        for proposal in by_category.values()
    )
    return await policy_service.draft(
        session,
        tenant_id=tenant_id,
        kind=PolicyKind.DEDUP_THRESHOLDS,
        body=updated.model_dump(mode="json"),
        change_reason=(
            f"Proposed from {total_reverts} merge(s) reverted by operators: {described}. "
            f"Raising a merge threshold only — the log carries no evidence of merges "
            f"that should have happened and did not, so tuning can move in one "
            f"direction. Review the evidence before approving."
        ),
        change_summary="Automatic dedup threshold proposal — not applied until approved",
        based_on_revision=live.dedup_thresholds.revision,
        actor_id=actor_id,
        correlation_id=correlation_id,
    )


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


async def _reverted_merges(
    session: AsyncSession, *, tenant_id: uuid.UUID, window: corpus.CorpusWindow
) -> list[RevertedMerge]:
    """Every revert in the window, joined to the merge it undid.

    Both events live on the same cluster chain, so the join is a single ordered
    scan rather than a query per revert: walk the chain, remember each match's
    confidence, and pair it with the revert naming the same complaint.

    A revert whose original merge is not in the scan — because the merge
    happened before the retention horizon — is dropped rather than counted with
    a guessed confidence. The proposal is a threshold, and a threshold derived
    partly from invented numbers is worse than one derived from fewer real ones.
    """
    rows = await session.execute(
        select(Event)
        .where(
            Event.tenant_id == tenant_id,
            Event.entity_type == EntityType.COMPLAINT_CLUSTER.value,
            Event.event_type.in_([_MATCH_FOUND, _REVERTED]),
        )
        .order_by(Event.entity_id, Event.sequence)
    )

    merges: dict[tuple[uuid.UUID, uuid.UUID], float] = {}
    reverts: list[RevertedMerge] = []
    for event in rows.scalars().all():
        complaint_id = uuid.UUID(str(event.payload["complaint_id"]))
        key = (event.entity_id, complaint_id)
        if event.event_type == _MATCH_FOUND:
            merges[key] = float(str(event.payload["combined_confidence"]))
            continue
        confidence = merges.get(key)
        if confidence is None:
            continue
        if not window.start <= event.occurred_at < window.end:
            continue
        reverts.append(
            RevertedMerge(
                complaint_id=complaint_id,
                cluster_id=event.entity_id,
                combined_confidence=confidence,
                category=None,
                reverted_at=event.occurred_at,
                reason=str(event.payload.get("reason") or ""),
            )
        )

    return await _attach_categories(session, tenant_id=tenant_id, reverts=reverts)


async def _attach_categories(
    session: AsyncSession, *, tenant_id: uuid.UUID, reverts: list[RevertedMerge]
) -> list[RevertedMerge]:
    """Fill in each reverted complaint's classified category.

    From the complaint's own chain, through the corpus builder, for the reason
    that module gives at length: the ``complaints`` projection's category may
    have been corrected since, and tuning a threshold against a category the
    classifier never assigned would attribute the reverts to the wrong band.
    """
    if not reverts:
        return []
    cases, _ = await corpus.build_cases(
        session, tenant_id=tenant_id, identifiers=[revert.complaint_id for revert in reverts]
    )
    categories = {case.complaint_id: case.category for case in cases}
    return [
        RevertedMerge(
            complaint_id=revert.complaint_id,
            cluster_id=revert.cluster_id,
            combined_confidence=revert.combined_confidence,
            category=categories.get(revert.complaint_id),
            reverted_at=revert.reverted_at,
            reason=revert.reason,
        )
        for revert in reverts
    ]


async def _lineages(
    session: AsyncSession, *, tenant_id: uuid.UUID, categories: set[str | None]
) -> dict[str, tuple[str, ...]]:
    """Ancestor chains for the categories the reverts touched.

    Through ``policy.resolver.category_lineage``, one query per distinct key.
    The corpus builder's batched version exists for tens of thousands of
    complaints; a set of reverts is tens of rows across a handful of categories,
    and reaching for the batched path here would trade a clearer call for an
    optimisation nothing measures.
    """
    return {
        category: await category_lineage(session, tenant_id=tenant_id, category=category)
        for category in sorted(category for category in categories if category)
    }


def _band_for(thresholds: DedupThresholds, category: str | None) -> DedupBand:
    for band in thresholds.bands:
        if band.category == category:
            return band
    return next(band for band in thresholds.bands if band.category is None)


__all__ = [
    "MINIMUM_REVERTS",
    "THRESHOLD_MARGIN",
    "BandProposal",
    "RevertedMerge",
    "draft_from_proposals",
    "propose_dedup_thresholds",
]
