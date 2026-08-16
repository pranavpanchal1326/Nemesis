"""Projections, snapshots and replay.

The Phase 2 gate here is "full replay from an empty projection reproduces
current state byte-identically". Byte-identically is taken literally: the
comparison is a hash of the canonical form, the same function the event chain
uses, so "equal" means the same thing for a projection as for a payload.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from nemesis.db.models.event import EventSnapshot
from nemesis.domain.lifecycle import ComplaintStatus, EntityType, WorkOrderStatus
from nemesis.events.store import EventStore
from nemesis.projections import (
    ProjectionEvent,
    has_projector,
    project,
    replay_entity,
    state_hash,
    unhandled_event_types,
    write_snapshot_if_due,
)
from nemesis.tenancy.context import tenant_scope
from tests.conftest import postgres_required

pytestmark = postgres_required


@pytest.fixture
async def session(
    migrated_engine: AsyncEngine, tenants: tuple[uuid.UUID, uuid.UUID]
) -> AsyncIterator[AsyncSession]:
    factory = async_sessionmaker(bind=migrated_engine, expire_on_commit=False, autoflush=False)
    async with factory() as active:
        yield active
        await active.rollback()


async def _seed_complaint(
    session: AsyncSession, tenant_id: uuid.UUID, complaint_id: uuid.UUID
) -> None:
    """A realistic §10 lifecycle: submit, verify, classify, score."""
    store = EventStore(session)
    base = datetime(2026, 8, 10, 9, 0, tzinfo=UTC)
    with tenant_scope(tenant_id):
        await store.append(
            entity_id=complaint_id,
            event_type="complaint_submitted",
            payload={
                "latitude": 19.076,
                "longitude": 72.8777,
                "description_text": "Deep pothole",
                "photo_url": "https://uploads.invalid/a.jpg",
            },
            occurred_at=base,
        )
        await store.append(
            entity_id=complaint_id,
            event_type="exif_check_completed",
            payload={"exif_present": True, "distance_meters": 3.5, "trust_delta": 0.25},
            occurred_at=base + timedelta(seconds=30),
        )
        await store.append(
            entity_id=complaint_id,
            event_type="classification_scored",
            payload={
                "category": "road_surface_defect",
                "confidence": 0.91,
                "model_id": "ViT-B-32",
                "prompt_set_version": "prompts-v1",
            },
            occurred_at=base + timedelta(minutes=1),
        )
        await store.append(
            entity_id=complaint_id,
            event_type="severity_scored",
            payload={
                "score": 7.5,
                "components": {"visual_damage": 0.8, "road_class": 0.6},
                "weights": {"visual_damage": 0.4, "road_class": 0.25},
                "policy_version": "severity_rubric_v1",
            },
            occurred_at=base + timedelta(minutes=2),
        )
        await session.commit()


async def test_replay_reproduces_the_full_lifecycle(
    session: AsyncSession, tenant_id: uuid.UUID
) -> None:
    complaint_id = uuid.uuid4()
    await _seed_complaint(session, tenant_id, complaint_id)

    with tenant_scope(tenant_id):
        result = await replay_entity(
            session,
            tenant_id=tenant_id,
            entity_type=EntityType.COMPLAINT,
            entity_id=complaint_id,
        )

    assert result.sequence == 4
    assert result.state["status"] == ComplaintStatus.SCORED.value
    assert result.state["category"] == "road_surface_defect"
    assert result.state["severity_score"] == 7.5
    assert result.state["trust_score"] == 0.25
    assert result.state["reported_at"].startswith("2026-08-10T09:00:00")


async def test_replay_is_deterministic_across_runs(
    session: AsyncSession, tenant_id: uuid.UUID
) -> None:
    """The gate, stated as a hash equality.

    Two independent replays of the same log must produce identical bytes. A
    projector that reached for the clock, a random value, or a database read
    would pass a spot-check on field values and fail this.
    """
    complaint_id = uuid.uuid4()
    await _seed_complaint(session, tenant_id, complaint_id)

    with tenant_scope(tenant_id):
        first = await replay_entity(
            session,
            tenant_id=tenant_id,
            entity_type=EntityType.COMPLAINT,
            entity_id=complaint_id,
            use_snapshots=False,
        )
        second = await replay_entity(
            session,
            tenant_id=tenant_id,
            entity_type=EntityType.COMPLAINT,
            entity_id=complaint_id,
            use_snapshots=False,
        )

    assert first.hash == second.hash
    assert first.hash == state_hash(first.state)


async def test_seeking_to_an_earlier_sequence_yields_the_state_at_that_point(
    session: AsyncSession, tenant_id: uuid.UUID
) -> None:
    """Phase 21's time machine, in miniature."""
    complaint_id = uuid.uuid4()
    await _seed_complaint(session, tenant_id, complaint_id)

    with tenant_scope(tenant_id):
        midpoint = await replay_entity(
            session,
            tenant_id=tenant_id,
            entity_type=EntityType.COMPLAINT,
            entity_id=complaint_id,
            upto_sequence=2,
        )

    assert midpoint.sequence == 2
    assert midpoint.state["status"] == ComplaintStatus.SUBMITTED.value
    assert "category" not in midpoint.state
    assert "severity_score" not in midpoint.state


async def test_snapshotted_replay_equals_full_replay(
    session: AsyncSession, tenant_id: uuid.UUID
) -> None:
    """Snapshots are a cache, so they must be provably discardable.

    A snapshot that changed the answer would be a correctness bug wearing a
    performance optimisation's clothes — and it would only show up on entities
    with long histories, which are the ones that matter most.
    """
    complaint_id = uuid.uuid4()
    store = EventStore(session)
    base = datetime(2026, 8, 10, 9, 0, tzinfo=UTC)

    with tenant_scope(tenant_id):
        await store.append(
            entity_id=complaint_id,
            event_type="complaint_submitted",
            payload={"latitude": 19.0, "longitude": 72.8},
            occurred_at=base,
        )
        for index in range(1, 50):
            await store.append(
                entity_id=complaint_id,
                event_type="exif_check_completed",
                payload={"exif_present": True, "trust_delta": 0.001},
                occurred_at=base + timedelta(seconds=index),
            )
        await session.commit()

        uncached = await replay_entity(
            session,
            tenant_id=tenant_id,
            entity_type=EntityType.COMPLAINT,
            entity_id=complaint_id,
            use_snapshots=False,
        )
        assert uncached.sequence == 50
        written = await write_snapshot_if_due(session, tenant_id=tenant_id, result=uncached)
        assert written is True
        await session.commit()

        cached = await replay_entity(
            session,
            tenant_id=tenant_id,
            entity_type=EntityType.COMPLAINT,
            entity_id=complaint_id,
        )

    assert cached.snapshot_sequence == 50
    assert cached.events_applied == 0, "the snapshot should have covered every event"
    assert cached.hash == uncached.hash


async def test_a_snapshot_from_an_older_projector_is_ignored(
    session: AsyncSession, tenant_id: uuid.UUID
) -> None:
    """Otherwise a projector bug fix never reaches the entities it affected."""
    complaint_id = uuid.uuid4()
    await _seed_complaint(session, tenant_id, complaint_id)

    with tenant_scope(tenant_id):
        session.add(
            EventSnapshot(
                tenant_id=tenant_id,
                entity_type=EntityType.COMPLAINT.value,
                entity_id=complaint_id,
                sequence=2,
                state={"status": "nonsense_from_an_old_build"},
                state_hash="0" * 64,
                projector_version=0,
            )
        )
        await session.commit()

        result = await replay_entity(
            session,
            tenant_id=tenant_id,
            entity_type=EntityType.COMPLAINT,
            entity_id=complaint_id,
        )

    assert result.snapshot_sequence is None
    assert result.state["status"] == ComplaintStatus.SCORED.value


async def test_snapshot_write_is_idempotent(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Two workers replaying the same entity at the same sequence is not a conflict."""
    complaint_id = uuid.uuid4()
    store = EventStore(session)
    with tenant_scope(tenant_id):
        await store.append(
            entity_id=complaint_id,
            event_type="complaint_submitted",
            payload={"latitude": 1.0, "longitude": 2.0},
        )
        for _ in range(49):
            await store.append(
                entity_id=complaint_id,
                event_type="exif_check_completed",
                payload={"exif_present": False, "trust_delta": 0.0},
            )
        await session.commit()

        result = await replay_entity(
            session,
            tenant_id=tenant_id,
            entity_type=EntityType.COMPLAINT,
            entity_id=complaint_id,
            use_snapshots=False,
        )
        await write_snapshot_if_due(session, tenant_id=tenant_id, result=result)
        await write_snapshot_if_due(session, tenant_id=tenant_id, result=result)
        await session.commit()

        rows = (
            (
                await session.execute(
                    select(EventSnapshot).where(
                        EventSnapshot.tenant_id == tenant_id,
                        EventSnapshot.entity_id == complaint_id,
                    )
                )
            )
            .scalars()
            .all()
        )

    assert len(rows) == 1


# ---------------------------------------------------------------------------
# Handler behaviour worth stating explicitly
# ---------------------------------------------------------------------------


def _event(event_type: str, sequence: int, **payload: object) -> ProjectionEvent:
    return ProjectionEvent(
        event_type=event_type,
        version=1,
        sequence=sequence,
        occurred_at="2026-08-10T09:00:00.000000Z",
        payload=payload,
    )


def test_a_safety_flag_is_not_walked_back_by_a_later_stage() -> None:
    """§11.2 routes the report out of the normal pipeline.

    A later stage completing must not restore a status that puts it back in the
    ordinary work queue — which is exactly what a naive "last event wins"
    projector would do.
    """
    state = project(
        EntityType.COMPLAINT,
        [
            _event("complaint_submitted", 1, latitude=1.0, longitude=2.0),
            _event(
                "safety_trigger_fired",
                2,
                rule_id="exposed_live_wire",
                ruleset_version="safety-v1",
            ),
            _event(
                "classification_scored",
                3,
                category="electrical",
                confidence=0.9,
                model_id="m",
                prompt_set_version="p",
            ),
        ],
    )
    assert state["is_safety_flagged"] is True
    assert state["status"] == ComplaintStatus.FLAGGED.value


def test_a_resubmitted_before_photo_cannot_close_a_work_order() -> None:
    """§15's perceptual-hash guard, at the projection layer.

    An identical photo scores a *perfect* SSIM, so reading the score alone would
    treat the clearest case of fraud as the strongest evidence of repair.
    """
    state = project(
        EntityType.WORK_ORDER,
        [
            _event("work_order_created", 1, cluster_id=str(uuid.uuid4())),
            _event(
                "ssim_verification_completed",
                2,
                ssim_score=1.0,
                threshold=0.6,
                passed=True,
                perceptual_hash_matched=True,
            ),
        ],
    )
    assert state["status"] == WorkOrderStatus.IN_PROGRESS.value


def test_repeated_cluster_merges_do_not_inflate_the_report_count() -> None:
    """§12.5 re-scores on report count, so a redelivered merge would be a
    severity escalation manufactured by a retry."""
    complaint_id = str(uuid.uuid4())
    events = [
        _event(
            "cluster_created", 1, seed_complaint_id=str(uuid.uuid4()), latitude=1.0, longitude=2.0
        ),
    ]
    for sequence in (2, 3):
        events.append(
            _event(
                "cluster_match_found",
                sequence,
                complaint_id=complaint_id,
                geo_distance_meters=10.0,
                combined_confidence=0.9,
                policy_version="dedup-v1",
                report_count_after=2,
            )
        )

    state = project(EntityType.COMPLAINT_CLUSTER, events)
    assert state["report_count"] == 2
    assert state["complaint_ids"].count(complaint_id) == 1


def test_a_reverted_merge_removes_the_member_without_erasing_history() -> None:
    complaint_id = str(uuid.uuid4())
    state = project(
        EntityType.COMPLAINT_CLUSTER,
        [
            _event(
                "cluster_created",
                1,
                seed_complaint_id=str(uuid.uuid4()),
                latitude=1.0,
                longitude=2.0,
            ),
            _event(
                "cluster_match_found",
                2,
                complaint_id=complaint_id,
                geo_distance_meters=10.0,
                combined_confidence=0.9,
                policy_version="dedup-v1",
                report_count_after=2,
            ),
            _event(
                "cluster_merge_reverted", 3, complaint_id=complaint_id, reason="different defect"
            ),
        ],
    )
    assert complaint_id not in state["complaint_ids"]
    assert state["report_count"] == 1
    assert state["reverted_merges"][0]["reason"] == "different defect"


def test_auto_confirmation_stays_distinguishable_from_a_real_one() -> None:
    """§44 requires the distinction to survive into every surface."""
    state = project(
        EntityType.WORK_ORDER,
        [
            _event("work_order_created", 1, cluster_id=str(uuid.uuid4())),
            _event("citizen_confirmed", 2, auto_confirmed=True, confirmed_by=None),
        ],
    )
    assert state["status"] == WorkOrderStatus.CLOSED.value
    assert state["auto_confirmed"] is True
    assert state["confirmed_by"] is None


def test_events_without_a_projector_are_reported_not_silently_dropped() -> None:
    events = [_event("admin_action", 1, action="x", justification="y")]
    assert not has_projector(EntityType.COMPLAINT, "admin_action")
    assert unhandled_event_types(EntityType.COMPLAINT, events) == {"admin_action"}
