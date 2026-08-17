"""The Phase 2 gate, stated against the real §9.2 tables.

``test_projections.py`` proves the *projection* is deterministic. This proves the
materialised row is — which is the claim §9.1 actually makes: current state is a
view derived from the log, so truncating it and rebuilding must reproduce it
exactly. Anything less means the tables hold information the log does not, and
the event store stops being the system of record.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from nemesis.db.models.complaint import Complaint, ComplaintCluster
from nemesis.db.models.work_order import WorkOrder
from nemesis.domain.lifecycle import ComplaintStatus, EntityType, WorkOrderStatus
from nemesis.events.store import EventStore
from nemesis.projections import (
    is_materialised,
    rebuild_entity,
    rebuild_tenant,
    replay_entity,
    write_projection,
)
from nemesis.tenancy.context import tenant_scope
from tests.conftest import postgres_required

pytestmark = postgres_required

BASE = datetime(2026, 8, 10, 9, 0, tzinfo=UTC)


@pytest.fixture
async def session(
    migrated_engine: AsyncEngine, tenants: tuple[uuid.UUID, uuid.UUID]
) -> AsyncIterator[AsyncSession]:
    factory = async_sessionmaker(bind=migrated_engine, expire_on_commit=False, autoflush=False)
    async with factory() as active:
        yield active
        await active.rollback()


async def _seed_full_lifecycle(
    session: AsyncSession, tenant_id: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """One complaint, its cluster, and the resulting work order."""
    complaint_id, cluster_id, work_order_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    store = EventStore(session)

    with tenant_scope(tenant_id):
        await store.append(
            entity_id=complaint_id,
            event_type="complaint_submitted",
            payload={
                "latitude": 19.076,
                "longitude": 72.8777,
                "description_text": "Deep pothole at the junction",
                "photo_url": "https://uploads.invalid/a.jpg",
            },
            occurred_at=BASE,
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
            occurred_at=BASE + timedelta(minutes=1),
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
            occurred_at=BASE + timedelta(minutes=2),
        )

        await store.append(
            entity_id=cluster_id,
            event_type="cluster_created",
            payload={
                "seed_complaint_id": str(complaint_id),
                "latitude": 19.076,
                "longitude": 72.8777,
            },
            occurred_at=BASE + timedelta(minutes=3),
        )

        await store.append(
            entity_id=work_order_id,
            event_type="work_order_created",
            payload={"cluster_id": str(cluster_id), "routing_rule_id": "roads-default"},
            occurred_at=BASE + timedelta(minutes=4),
        )
        await store.append(
            entity_id=work_order_id,
            event_type="work_order_assigned",
            payload={
                "assignee_type": "contractor",
                "assignee_id": str(uuid.uuid4()),
                "sla_deadline": "2026-08-17T09:00:00.000000Z",
                "selection_rank": 1,
            },
            occurred_at=BASE + timedelta(minutes=5),
        )
        await session.commit()

    return complaint_id, cluster_id, work_order_id


async def _rebuild(session: AsyncSession, tenant_id: uuid.UUID) -> dict[str, int]:
    with tenant_scope(tenant_id):
        written = await rebuild_tenant(session, tenant_id=tenant_id)
        await session.commit()
    return written


async def _snapshot_rows(session: AsyncSession, tenant_id: uuid.UUID) -> list[tuple[object, ...]]:
    """Every materialised row, as comparable tuples.

    Geography is read back as WKT rather than as the opaque EWKB hex, so a
    failure prints coordinates a human can read instead of a wall of bytes.
    """
    rows = await session.execute(
        text(
            "SELECT id, status, category, severity_score, severity_policy_version, "
            "       ST_AsText(location::geometry), version "
            "FROM complaints WHERE tenant_id = :t ORDER BY id"
        ).bindparams(t=tenant_id)
    )
    complaints = [tuple(row) for row in rows]

    rows = await session.execute(
        text(
            "SELECT id, report_count, ST_AsText(centroid::geometry), first_reported, "
            "       last_reported, version "
            "FROM complaint_clusters WHERE tenant_id = :t ORDER BY id"
        ).bindparams(t=tenant_id)
    )
    clusters = [tuple(row) for row in rows]

    rows = await session.execute(
        text(
            "SELECT id, status, complaint_cluster_id, assigned_to_type, assigned_to_id, "
            "       sla_deadline, version "
            "FROM work_orders WHERE tenant_id = :t ORDER BY id"
        ).bindparams(t=tenant_id)
    )
    work_orders = [tuple(row) for row in rows]

    return complaints + clusters + work_orders


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


async def test_rebuild_from_an_empty_projection_reproduces_current_state(
    session: AsyncSession, tenant_id: uuid.UUID
) -> None:
    """Truncate every current-state table, rebuild from the log alone, compare.

    This is the §9.1 claim made checkable: the tables are a view derived from
    events, so destroying them must be recoverable *exactly*. If the rebuilt
    rows differed in any column, the tables would be holding information the log
    does not — and the event store would not be the system of record.
    """
    await _seed_full_lifecycle(session, tenant_id)

    await _rebuild(session, tenant_id)
    before = await _snapshot_rows(session, tenant_id)
    assert before, "the rebuild wrote nothing at all"

    await session.execute(text("TRUNCATE work_orders, complaints, complaint_clusters CASCADE"))
    await session.commit()
    assert await _snapshot_rows(session, tenant_id) == []

    await _rebuild(session, tenant_id)
    after = await _snapshot_rows(session, tenant_id)

    assert after == before


async def test_rebuild_is_idempotent(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Running it twice converges rather than duplicating or drifting."""
    await _seed_full_lifecycle(session, tenant_id)

    await _rebuild(session, tenant_id)
    first = await _snapshot_rows(session, tenant_id)
    await _rebuild(session, tenant_id)
    second = await _snapshot_rows(session, tenant_id)

    assert first == second


async def test_materialised_rows_carry_the_projected_values(
    session: AsyncSession, tenant_id: uuid.UUID
) -> None:
    complaint_id, cluster_id, work_order_id = await _seed_full_lifecycle(session, tenant_id)
    written = await _rebuild(session, tenant_id)
    assert written == {"complaint": 1, "complaint_cluster": 1, "work_order": 1}

    with tenant_scope(tenant_id):
        complaint = (
            await session.execute(
                select(Complaint).where(
                    Complaint.tenant_id == tenant_id, Complaint.id == complaint_id
                )
            )
        ).scalar_one()
        cluster = (
            await session.execute(
                select(ComplaintCluster).where(
                    ComplaintCluster.tenant_id == tenant_id,
                    ComplaintCluster.id == cluster_id,
                )
            )
        ).scalar_one()
        work_order = (
            await session.execute(
                select(WorkOrder).where(
                    WorkOrder.tenant_id == tenant_id, WorkOrder.id == work_order_id
                )
            )
        ).scalar_one()

    assert complaint.status == ComplaintStatus.SCORED.value
    assert complaint.category == "road_surface_defect"
    assert complaint.severity_score == 7.5
    assert complaint.severity_policy_version == "severity_rubric_v1"
    # `version` is the log position the row reflects, not a counter this layer
    # increments — three complaint events were appended.
    assert complaint.version == 3

    assert cluster.report_count == 1
    assert work_order.status == WorkOrderStatus.ASSIGNED.value
    assert work_order.complaint_cluster_id == cluster_id
    assert work_order.assigned_to_type == "contractor"


async def test_longitude_and_latitude_are_not_transposed(
    session: AsyncSession, tenant_id: uuid.UUID
) -> None:
    """``ST_MakePoint`` takes longitude first, and getting it backwards produces
    valid geometry at a plausible location — so nothing errors and every §14
    distance query is silently wrong. Asserted against a real ``ST_Distance``
    rather than against the stored representation."""
    complaint_id, _, _ = await _seed_full_lifecycle(session, tenant_id)
    await _rebuild(session, tenant_id)

    metres = (
        await session.execute(
            text(
                "SELECT ST_Distance(location, ST_SetSRID(ST_MakePoint(72.8777, 19.076), "
                "4326)::geography) FROM complaints WHERE tenant_id = :t AND id = :id"
            ).bindparams(t=tenant_id, id=complaint_id)
        )
    ).scalar_one()

    assert metres < 1.0, "the stored point is not where the complaint was reported"


async def test_a_stale_writer_cannot_move_the_projection_backwards(
    session: AsyncSession, tenant_id: uuid.UUID
) -> None:
    """Two workers replaying at different log positions is normal, not an error.

    Without the version guard the one that commits last wins regardless of which
    read more history, and the projection silently regresses to an older state.
    """
    complaint_id, _, _ = await _seed_full_lifecycle(session, tenant_id)

    with tenant_scope(tenant_id):
        latest = await replay_entity(
            session,
            tenant_id=tenant_id,
            entity_type=EntityType.COMPLAINT,
            entity_id=complaint_id,
            use_snapshots=False,
        )
        stale = await replay_entity(
            session,
            tenant_id=tenant_id,
            entity_type=EntityType.COMPLAINT,
            entity_id=complaint_id,
            upto_sequence=1,
            use_snapshots=False,
        )

        assert await write_projection(session, tenant_id=tenant_id, result=latest) is True
        # The stale writer is refused by the database, not by ordering luck.
        assert await write_projection(session, tenant_id=tenant_id, result=stale) is False
        await session.commit()

        row = (
            await session.execute(
                select(Complaint.status, Complaint.version).where(
                    Complaint.tenant_id == tenant_id, Complaint.id == complaint_id
                )
            )
        ).one()

    assert row.status == ComplaintStatus.SCORED.value
    assert row.version == 3


async def test_log_only_entities_are_not_materialised(
    session: AsyncSession, tenant_id: uuid.UUID
) -> None:
    """``admin_action`` describes a decision, not a thing with a lifecycle."""
    assert is_materialised(EntityType.COMPLAINT.value)
    assert not is_materialised(EntityType.ADMIN_ACTION.value)
    assert not is_materialised(EntityType.SYSTEM.value)

    admin_id = uuid.uuid4()
    with tenant_scope(tenant_id):
        await EventStore(session).append(
            entity_id=admin_id,
            event_type="admin_action",
            payload={"action": "suspend_user", "justification": "abuse report #12"},
        )
        await session.commit()

        assert (
            await rebuild_entity(
                session,
                tenant_id=tenant_id,
                entity_type=EntityType.ADMIN_ACTION.value,
                entity_id=admin_id,
            )
            is False
        )


async def test_an_entity_with_no_events_produces_no_row(
    session: AsyncSession, tenant_id: uuid.UUID
) -> None:
    """Writing here would invent current state for an entity with no history."""
    with tenant_scope(tenant_id):
        written = await rebuild_entity(
            session,
            tenant_id=tenant_id,
            entity_type=EntityType.COMPLAINT.value,
            entity_id=uuid.uuid4(),
        )
    assert written is False


async def test_rebuild_does_not_cross_tenants(
    session: AsyncSession, tenant_id: uuid.UUID, other_tenant_id: uuid.UUID
) -> None:
    await _seed_full_lifecycle(session, tenant_id)
    await _seed_full_lifecycle(session, other_tenant_id)

    await _rebuild(session, tenant_id)

    with tenant_scope(other_tenant_id):
        leaked = (
            (
                await session.execute(
                    select(Complaint.id).where(Complaint.tenant_id == other_tenant_id)
                )
            )
            .scalars()
            .all()
        )

    assert leaked == [], "rebuilding one tenant materialised another tenant's rows"
