"""Dedup threshold proposals — and the direction the evidence cannot support.

§13.3 promises the rubric improves as resolution data accumulates. The dedup
half of that promise is constrained by what the log actually contains, and this
file is mostly about defending the constraint rather than the arithmetic:

``cluster_merge_reverted`` records that a human looked at a merge and said no.
Nothing records the opposite — a human noticing two clusters that should have
been one — because the product does not yet let them say so. So a proposal can
only ever *raise* a threshold, and a test asserts that no code path produces a
decrease. Loosening on this evidence would require assuming everything not
reverted was correct, which assumes exactly what it is trying to measure.

The other property under test is that nothing is applied. A proposal becomes a
draft revision through the ordinary lifecycle and decides nothing until a human
approves it.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from nemesis.events.store import EventStore
from nemesis.policy import service as policy_service
from nemesis.policy.documents import PolicyKind, PolicyStatus
from nemesis.simulation import tuning
from nemesis.simulation.corpus import CorpusWindow
from nemesis.simulation.errors import SimulationValidationError
from nemesis.tenancy.context import tenant_scope
from tests.conftest import postgres_required
from tests.test_simulation_corpus import BASE, WINDOW, seed_complaint

pytestmark = [postgres_required, pytest.mark.integration]


@asynccontextmanager
async def scoped(engine: AsyncEngine, tenant_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    with tenant_scope(tenant_id):
        async with maker() as session:
            yield session
            await session.commit()


async def seed_reverted_merge(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    confidence: float,
    index: int,
) -> None:
    """One merge that a human then undid, with the confidence that produced it."""
    cluster_id = uuid.uuid4()
    at = BASE + timedelta(hours=index)
    seed = await seed_complaint(session, tenant_id=tenant_id, at=at)
    merged = await seed_complaint(session, tenant_id=tenant_id, at=at + timedelta(minutes=30))

    store = EventStore(session)
    await store.append(
        entity_id=cluster_id,
        event_type="cluster_created",
        payload={"seed_complaint_id": str(seed), "latitude": 19.0, "longitude": 72.8},
        occurred_at=at,
    )
    await store.append(
        entity_id=cluster_id,
        event_type="cluster_match_found",
        payload={
            "complaint_id": str(merged),
            "geo_distance_meters": 8.0,
            "image_similarity": confidence,
            "text_similarity": confidence,
            "combined_confidence": confidence,
            "policy_version": "dedup_thresholds@1",
            "report_count_after": 2,
        },
        occurred_at=at + timedelta(minutes=31),
    )
    await store.append(
        entity_id=cluster_id,
        event_type="cluster_merge_reverted",
        payload={"complaint_id": str(merged), "reason": "different potholes, same street"},
        occurred_at=at + timedelta(hours=1),
    )


# ---------------------------------------------------------------------------


async def test_too_few_reverts_produce_no_proposal_rather_than_a_weak_one(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Two reverts is one operator having a bad afternoon.

    A suggestion presented anyway would be read as a recommendation whatever
    hedging surrounded it.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        await policy_service.seed_baselines(session, tenant_id=tenant_id)
        for index in range(2):
            await seed_reverted_merge(session, tenant_id=tenant_id, confidence=0.95, index=index)
        proposals = await tuning.propose_dedup_thresholds(
            session, tenant_id=tenant_id, window=WINDOW
        )

    assert proposals == ()


async def test_enough_reverts_propose_a_threshold_above_the_worst_of_them(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The proposal must actually have prevented the merges it was derived from.

    ``dedup_outcome`` is inclusive at the lower edge, so a threshold set *at* the
    reverted confidence would leave that exact merge still qualifying.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        await policy_service.seed_baselines(session, tenant_id=tenant_id)
        for index in range(6):
            await seed_reverted_merge(
                session, tenant_id=tenant_id, confidence=0.90 + index * 0.01, index=index
            )
        proposals = await tuning.propose_dedup_thresholds(
            session, tenant_id=tenant_id, window=WINDOW
        )

    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.revert_count == 6
    assert proposal.highest_reverted_confidence == pytest.approx(0.95)
    assert proposal.proposed_threshold > proposal.highest_reverted_confidence
    assert proposal.proposed_threshold > proposal.current_threshold
    assert len(proposal.evidence) == 6
    assert proposal.evidence[0].combined_confidence >= proposal.evidence[-1].combined_confidence


async def test_reverts_below_the_current_threshold_propose_nothing(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Those merges were not produced by the threshold.

    They came through the geo radius or the time window, and proposing a change
    that would have prevented none of them is arithmetic dressed as insight.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        await policy_service.seed_baselines(session, tenant_id=tenant_id)
        for index in range(6):
            await seed_reverted_merge(session, tenant_id=tenant_id, confidence=0.30, index=index)
        proposals = await tuning.propose_dedup_thresholds(
            session, tenant_id=tenant_id, window=WINDOW
        )

    assert proposals == ()


async def test_a_proposal_never_lowers_a_threshold(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The property the module's whole argument rests on.

    Every revert says "this confidence was not high enough to merge on". Nothing
    in the log says the converse, so nothing here may act as though it did.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        await policy_service.seed_baselines(session, tenant_id=tenant_id)
        for index in range(8):
            await seed_reverted_merge(
                session, tenant_id=tenant_id, confidence=0.86 + index * 0.01, index=index
            )
        proposals = await tuning.propose_dedup_thresholds(
            session, tenant_id=tenant_id, window=WINDOW
        )

    assert all(proposal.proposed_threshold >= proposal.current_threshold for proposal in proposals)


async def test_a_revert_outside_the_window_is_not_counted(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    async with scoped(migrated_engine, tenant_id) as session:
        await policy_service.seed_baselines(session, tenant_id=tenant_id)
        for index in range(6):
            await seed_reverted_merge(session, tenant_id=tenant_id, confidence=0.95, index=index)
        narrow = CorpusWindow(start=BASE - timedelta(days=40), end=BASE - timedelta(days=30))
        proposals = await tuning.propose_dedup_thresholds(
            session, tenant_id=tenant_id, window=narrow
        )

    assert proposals == ()


async def test_a_proposal_becomes_a_draft_and_nothing_more(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """§13.3 wants a feedback loop, not an autonomous one.

    A threshold that retunes itself overnight is a system where nobody can
    answer "why did this change" — the exact failure the policy phase exists to
    prevent.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        await policy_service.seed_baselines(session, tenant_id=tenant_id)
        for index in range(6):
            await seed_reverted_merge(
                session, tenant_id=tenant_id, confidence=0.90 + index * 0.01, index=index
            )
        proposals = await tuning.propose_dedup_thresholds(
            session, tenant_id=tenant_id, window=WINDOW
        )
        drafted = await tuning.draft_from_proposals(
            session, tenant_id=tenant_id, proposals=proposals
        )
        live = await policy_service.active_version(
            session, tenant_id=tenant_id, kind=PolicyKind.DEDUP_THRESHOLDS
        )

    assert drafted.status == PolicyStatus.DRAFT.value
    assert "reverted by operators" in drafted.change_reason
    assert "Review the evidence" in drafted.change_reason
    assert live is not None
    assert live.revision == 1, "the live document must be untouched by a proposal"
    assert drafted.revision != live.revision


async def test_drafting_with_no_proposals_is_refused(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A draft with no changes asks somebody to approve nothing."""
    async with scoped(migrated_engine, tenant_id) as session:
        await policy_service.seed_baselines(session, tenant_id=tenant_id)
        with pytest.raises(SimulationValidationError):
            await tuning.draft_from_proposals(session, tenant_id=tenant_id, proposals=[])


async def test_a_drafted_proposal_carries_the_new_threshold(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The document is the proposal, not a note about it."""
    async with scoped(migrated_engine, tenant_id) as session:
        await policy_service.seed_baselines(session, tenant_id=tenant_id)
        for index in range(6):
            await seed_reverted_merge(session, tenant_id=tenant_id, confidence=0.93, index=index)
        proposals = await tuning.propose_dedup_thresholds(
            session, tenant_id=tenant_id, window=WINDOW
        )
        drafted = await tuning.draft_from_proposals(
            session, tenant_id=tenant_id, proposals=proposals
        )

    bands = drafted.body["bands"]
    default = next(band for band in bands if band["category"] is None)
    assert default["merge_threshold"] == pytest.approx(0.94)


async def test_a_tenant_with_no_reverts_gets_no_proposals(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    async with scoped(migrated_engine, tenant_id) as session:
        await policy_service.seed_baselines(session, tenant_id=tenant_id)
        proposals = await tuning.propose_dedup_thresholds(
            session, tenant_id=tenant_id, window=WINDOW
        )
    assert proposals == ()
