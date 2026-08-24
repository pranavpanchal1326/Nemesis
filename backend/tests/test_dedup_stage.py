"""The dedup stage as the pipeline actually runs it.

``test_dedup_decide`` pins the arithmetic and ``test_dedup_queries`` pins the
SQL. This file is about the seam: that a decision becomes events on *both*
chains in one transaction, that a redelivered task is a provable no-op, that the
ambiguous band raises a review item rather than silently behaving like
``distinct``, and that a merge can be undone by appending rather than by editing
history.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from nemesis.db.models.complaint import Complaint, ComplaintCluster
from nemesis.db.models.event import Event
from nemesis.db.models.trust import ReviewQueueItem
from nemesis.dedup.merge import revert
from nemesis.dedup.stage import dedup_stage
from nemesis.domain.lifecycle import EntityType
from nemesis.events.store import EventStore
from nemesis.observability.metrics import PipelineStage
from nemesis.pipeline.orchestrator import execute_stage
from nemesis.pipeline.stages import provider_scope
from nemesis.projections.replay import replay_entity
from nemesis.projections.writer import write_projection
from nemesis.tenancy.context import tenant_scope
from nemesis.trust.review import ReviewReason
from tests.conftest import postgres_required
from tests.dedup_fixtures import (
    BASE,
    PUNE_LAT,
    PUNE_LON,
    image_vector,
    offset,
    text_vector,
)

pytestmark = [postgres_required, pytest.mark.integration]

COMPLAINT_CLUSTER = EntityType.COMPLAINT_CLUSTER


@pytest.fixture
def sessions(migrated_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(migrated_engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def stage_environment(bound_session: None) -> AsyncIterator[None]:
    """Point the app's global engine at the test database, and tidy up after.

    ``execute_stage`` opens its own transaction through ``session_scope()``,
    which resolves its engine from process-global settings. Without
    ``bound_session`` every test here would drive the real application database
    and then assert against the throwaway one — which fails as "complaint has no
    events", a message that sends you looking at the event store rather than at
    the fixture. Autouse because every test in this file runs a stage.

    The flag-store disposal is the same accommodation
    ``test_trust_verification`` makes: an un-disposed Redis socket is finalised
    at interpreter shutdown, which under ``filterwarnings = ["error"]`` fails
    whichever test happened to trigger the collection.
    """
    yield
    from nemesis.flags import close_flags

    await close_flags()


async def submit(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    seed: float,
    north_m: float = 0.0,
    category: str = "pothole",
    with_embeddings: bool = True,
) -> uuid.UUID:
    """A real complaint chain, projected, with Phase 9's vectors written on."""
    latitude, longitude = offset(PUNE_LAT, PUNE_LON, north_m=north_m)
    complaint_id = uuid.uuid4()
    await EventStore(session).append(
        entity_id=complaint_id,
        event_type="complaint_submitted",
        payload={
            "latitude": latitude,
            "longitude": longitude,
            "description_text": "a hole in the road",
            "photo_url": None,
            "audio_url": None,
            "locale": "en",
            "device_fingerprint": None,
            "submitted_via": "web",
        },
        tenant_id=tenant_id,
        occurred_at=BASE,
    )
    await EventStore(session).append(
        entity_id=complaint_id,
        event_type="classification_scored",
        payload={
            "category": category,
            "confidence": 0.9,
            "model_id": "test-text-encoder",
            "prompt_set_version": "test-1",
            "alternatives": {},
            "margin": 0.4,
            "raw_similarities": {category: 0.9},
            "calibration_version": "test-calibration-1",
            "model_ids": {"text": "test-text-encoder"},
        },
        tenant_id=tenant_id,
        occurred_at=BASE,
    )
    projection = await replay_entity(
        session, tenant_id=tenant_id, entity_type="complaint", entity_id=complaint_id
    )
    await write_projection(session, tenant_id=tenant_id, result=projection)
    if with_embeddings:
        await session.execute(
            update(Complaint)
            .where(Complaint.tenant_id == tenant_id, Complaint.id == complaint_id)
            .values(text_embedding=text_vector(seed), image_embedding=image_vector(seed))
        )
    await session.flush()
    return complaint_id


async def run_dedup(
    sessions: async_sessionmaker[AsyncSession],
    *,
    tenant_id: uuid.UUID,
    complaint_id: uuid.UUID,
) -> None:
    with provider_scope(PipelineStage.DEDUP, dedup_stage), tenant_scope(tenant_id):
        await execute_stage(
            tenant_id=tenant_id,
            complaint_id=complaint_id,
            stage=PipelineStage.DEDUP.value,
        )


async def seed_incident(
    sessions: async_sessionmaker[AsyncSession],
    *,
    tenant_id: uuid.UUID,
    seed: float,
    north_m: float,
) -> uuid.UUID:
    """An existing incident, created the way the system creates them.

    Deliberately not ``dedup_fixtures.make_incident``, which inserts the cluster
    row directly. That is fine for the query tests, which only read rows, and
    wrong here: a cluster with no ``cluster_created`` event has no chain to
    replay, so the first ``cluster_match_found`` appended to it projects to a
    state with no ``first_reported`` and the write fails. Seeding through the
    real stage also means these tests prove the flow composes with itself, which
    is the property a merge test should be establishing anyway.
    """
    async with sessions() as session:
        complaint_id = await submit(session, tenant_id=tenant_id, seed=seed, north_m=north_m)
        await session.commit()
    await run_dedup(sessions, tenant_id=tenant_id, complaint_id=complaint_id)
    async with sessions() as session:
        cluster_id: uuid.UUID = (
            await session.execute(
                select(Complaint.cluster_id).where(
                    Complaint.tenant_id == tenant_id, Complaint.id == complaint_id
                )
            )
        ).scalar_one()
    return cluster_id


async def events_of(
    session: AsyncSession, *, tenant_id: uuid.UUID, entity_id: uuid.UUID
) -> list[str]:
    rows = await session.execute(
        select(Event.event_type)
        .where(Event.tenant_id == tenant_id, Event.entity_id == entity_id)
        .order_by(Event.sequence)
    )
    return list(rows.scalars())


# ---------------------------------------------------------------------------
# The three outcomes, through the real orchestrator
# ---------------------------------------------------------------------------


async def test_a_first_report_creates_its_own_cluster(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> None:
    async with sessions() as session:
        complaint_id = await submit(session, tenant_id=tenant_id, seed=1.0)
        await session.commit()

    await run_dedup(sessions, tenant_id=tenant_id, complaint_id=complaint_id)

    async with sessions() as session:
        assert "complaint_clustered" in await events_of(
            session, tenant_id=tenant_id, entity_id=complaint_id
        )
        cluster_id = (
            await session.execute(
                select(Complaint.cluster_id).where(
                    Complaint.tenant_id == tenant_id, Complaint.id == complaint_id
                )
            )
        ).scalar_one()
        # The gap Phase 10 closed: before `complaint_clustered` existed, nothing
        # on the complaint chain ever set this column and it stayed NULL forever.
        assert cluster_id is not None
        assert "cluster_created" in await events_of(
            session, tenant_id=tenant_id, entity_id=cluster_id
        )


async def test_a_near_identical_report_merges_into_the_existing_incident(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> None:
    cluster_id = await seed_incident(sessions, tenant_id=tenant_id, seed=7.0, north_m=5.0)
    async with sessions() as session:
        complaint_id = await submit(session, tenant_id=tenant_id, seed=7.0, north_m=8.0)
        await session.commit()

    await run_dedup(sessions, tenant_id=tenant_id, complaint_id=complaint_id)

    async with sessions() as session:
        assert "cluster_match_found" in await events_of(
            session, tenant_id=tenant_id, entity_id=cluster_id
        )
        joined = (
            await session.execute(
                select(Complaint.cluster_id).where(
                    Complaint.tenant_id == tenant_id, Complaint.id == complaint_id
                )
            )
        ).scalar_one()
        assert joined == cluster_id
        # The projection is what the rest of the system reads, so the count
        # moving is the part that matters — not just the event existing.
        report_count = (
            await session.execute(
                select(ComplaintCluster.report_count).where(
                    ComplaintCluster.tenant_id == tenant_id, ComplaintCluster.id == cluster_id
                )
            )
        ).scalar_one()
        assert report_count == 2


async def test_an_unrelated_report_at_the_same_junction_stays_distinct(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> None:
    """The false-merge case §14.3 is written about. Same place, same category,
    same hour — and a different problem."""
    existing = await seed_incident(sessions, tenant_id=tenant_id, seed=3.0, north_m=4.0)
    async with sessions() as session:
        complaint_id = await submit(session, tenant_id=tenant_id, seed=900.0, north_m=6.0)
        await session.commit()

    await run_dedup(sessions, tenant_id=tenant_id, complaint_id=complaint_id)

    async with sessions() as session:
        joined = (
            await session.execute(
                select(Complaint.cluster_id).where(
                    Complaint.tenant_id == tenant_id, Complaint.id == complaint_id
                )
            )
        ).scalar_one()
        assert joined is not None
        assert joined != existing
        assert "cluster_match_found" not in await events_of(
            session, tenant_id=tenant_id, entity_id=existing
        )


async def test_two_indistinguishable_neighbours_raise_a_review_item(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> None:
    """§14.1's middle band must do something visibly different from `distinct`.

    Two clusters with the same visual identity, both within the radius. The
    engine must refuse to pick and must say so somewhere a human will see it —
    otherwise the ambiguous band has collapsed and dedup is silently binary.
    """
    # Geometry chosen so the two incidents cannot see each other. Both sit 30 m
    # from the subject and therefore inside its 50 m radius, while being 60 m
    # apart and therefore outside each other's — otherwise the second one merges
    # into the first during setup, there is only ever one cluster, and the test
    # passes a report through an unambiguous merge while claiming to prove the
    # ambiguous band.
    await seed_incident(sessions, tenant_id=tenant_id, seed=11.0, north_m=-30.0)
    await seed_incident(sessions, tenant_id=tenant_id, seed=11.0, north_m=30.0)
    async with sessions() as session:
        complaint_id = await submit(session, tenant_id=tenant_id, seed=11.0, north_m=0.0)
        await session.commit()

    await run_dedup(sessions, tenant_id=tenant_id, complaint_id=complaint_id)

    async with sessions() as session:
        reason = (
            await session.execute(
                select(ReviewQueueItem.reason).where(
                    ReviewQueueItem.tenant_id == tenant_id,
                    ReviewQueueItem.complaint_id == complaint_id,
                )
            )
        ).scalar_one()
        assert reason == ReviewReason.AMBIGUOUS_DEDUP.value
        assert "review_queued" in await events_of(
            session, tenant_id=tenant_id, entity_id=complaint_id
        )


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


async def test_a_redelivered_stage_changes_nothing(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> None:
    """§12.5 re-scores on report count, so a double count is a severity
    escalation manufactured by a redelivery."""
    cluster_id = await seed_incident(sessions, tenant_id=tenant_id, seed=7.0, north_m=5.0)
    async with sessions() as session:
        complaint_id = await submit(session, tenant_id=tenant_id, seed=7.0, north_m=8.0)
        await session.commit()

    await run_dedup(sessions, tenant_id=tenant_id, complaint_id=complaint_id)
    async with sessions() as session:
        first = await events_of(session, tenant_id=tenant_id, entity_id=cluster_id)

    await run_dedup(sessions, tenant_id=tenant_id, complaint_id=complaint_id)
    async with sessions() as session:
        assert await events_of(session, tenant_id=tenant_id, entity_id=cluster_id) == first
        report_count = (
            await session.execute(
                select(ComplaintCluster.report_count).where(
                    ComplaintCluster.tenant_id == tenant_id, ComplaintCluster.id == cluster_id
                )
            )
        ).scalar_one()
        assert report_count == 2


# ---------------------------------------------------------------------------
# Reversibility (§14.3)
# ---------------------------------------------------------------------------


async def test_a_merge_is_undone_by_appending_never_by_deleting(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> None:
    cluster_id = await seed_incident(sessions, tenant_id=tenant_id, seed=7.0, north_m=5.0)
    async with sessions() as session:
        complaint_id = await submit(session, tenant_id=tenant_id, seed=7.0, north_m=8.0)
        await session.commit()

    await run_dedup(sessions, tenant_id=tenant_id, complaint_id=complaint_id)

    async with sessions() as session:
        emitted = await revert(
            session,
            tenant_id=tenant_id,
            complaint_id=complaint_id,
            reason="operator says these are different potholes",
            reverted_by=None,
        )
        store = EventStore(session)
        for event in emitted:
            await store.append(
                entity_id=event.entity_id,
                event_type=event.event_type,
                payload=dict(event.payload),
                tenant_id=tenant_id,
                entity_type=event.entity_type.value,
            )
        # Clusters before the complaint, and in emission order. A set here is a
        # real bug rather than a style point: the complaint row carries a
        # foreign key to its new cluster, so projecting it first violates
        # `fk_complaints_cluster` on whichever run the set happens to iterate
        # the wrong way. The orchestrator gets this right by keying an
        # insertion-ordered dict off the emission order; this hand-rolled
        # replacement has to do the same.
        for event in emitted:
            if event.entity_type is not COMPLAINT_CLUSTER:
                continue
            projection = await replay_entity(
                session,
                tenant_id=tenant_id,
                entity_type=event.entity_type.value,
                entity_id=event.entity_id,
            )
            await write_projection(session, tenant_id=tenant_id, result=projection)
        projection = await replay_entity(
            session, tenant_id=tenant_id, entity_type="complaint", entity_id=complaint_id
        )
        await write_projection(session, tenant_id=tenant_id, result=projection)
        await session.commit()

    async with sessions() as session:
        chain = await events_of(session, tenant_id=tenant_id, entity_id=cluster_id)
        # The mistake and the correction both survive. A log that showed only
        # the corrected state would record that the system was always right.
        assert "cluster_match_found" in chain
        assert "cluster_merge_reverted" in chain

        moved = (
            await session.execute(
                select(Complaint.cluster_id).where(
                    Complaint.tenant_id == tenant_id, Complaint.id == complaint_id
                )
            )
        ).scalar_one()
        assert moved != cluster_id

        remaining = (
            await session.execute(
                select(func.count(Complaint.id)).where(
                    Complaint.tenant_id == tenant_id, Complaint.cluster_id == cluster_id
                )
            )
        ).scalar_one()
        assert remaining == 1


async def test_reverting_a_single_member_cluster_is_refused(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> None:
    """That is an origin, not a merge. "Reverting" it would leave a cluster
    whose own `cluster_created` event still names the complaint as its seed."""
    from nemesis.dedup.errors import DedupIntegrityError

    async with sessions() as session:
        complaint_id = await submit(session, tenant_id=tenant_id, seed=1.0)
        await session.commit()

    await run_dedup(sessions, tenant_id=tenant_id, complaint_id=complaint_id)

    async with sessions() as session:
        with pytest.raises(DedupIntegrityError):
            await revert(
                session,
                tenant_id=tenant_id,
                complaint_id=complaint_id,
                reason="mistake",
                reverted_by=None,
            )
