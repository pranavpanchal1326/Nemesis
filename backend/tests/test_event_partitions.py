"""Partition maintenance, the integrity sweep, and the operator CLI.

These three are the parts of Phase 2 that only run on a schedule or in an
incident — which is exactly why they need tests. Code that executes once a day
and code that executes once a year fail the same way: silently, and at the worst
possible moment. The partition maintainer is the difference between a warning
and an outage of the write path; the sweep is the only thing that can detect
tampering after the write; and the CLI is what an on-call reaches for instead of
improvising SQL against the event log.
"""

from __future__ import annotations

import itertools
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from nemesis.domain.lifecycle import EntityType
from nemesis.events.partitions import (
    default_partition_rows,
    detachable_partitions,
    ensure_partitions,
    month_start,
    next_month,
    plan_partitions,
    previous_month,
    retention_horizon,
)
from nemesis.events.store import EventStore
from nemesis.events.verify import sweep_chains
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


@pytest.fixture
async def bound_global_engine(
    migrated_engine: AsyncEngine, tenants: tuple[uuid.UUID, uuid.UUID]
) -> AsyncIterator[None]:
    """Point ``session_scope()`` at the migrated test database.

    The Celery tasks resolve their engine from process-global settings, which is
    correct in production and wrong in tests — without this they would exercise
    the *application* database. Teardown clears the module globals but does not
    dispose the engine, because ``migrated_engine`` owns it.
    """
    from nemesis.db import session as session_module

    await session_module.dispose_engine()
    session_module._engine = migrated_engine
    session_module._sessionmaker = None
    try:
        yield
    finally:
        session_module._engine = None
        session_module._sessionmaker = None


# ---------------------------------------------------------------------------
# Calendar arithmetic — pure, and wrong in interesting places if done by days
# ---------------------------------------------------------------------------


def test_month_boundaries_cross_the_year() -> None:
    december = datetime(2026, 12, 15, 10, 30, tzinfo=UTC)
    assert next_month(month_start(december)) == datetime(2027, 1, 1, tzinfo=UTC)
    assert previous_month(datetime(2027, 1, 1, tzinfo=UTC)) == datetime(2026, 12, 1, tzinfo=UTC)


def test_month_start_normalises_to_utc_midnight() -> None:
    assert month_start(datetime(2026, 8, 16, 23, 59, 59, 999999, tzinfo=UTC)) == datetime(
        2026, 8, 1, tzinfo=UTC
    )


def test_plan_covers_the_current_month_through_the_window() -> None:
    plans = plan_partitions(datetime(2026, 11, 20, tzinfo=UTC), months_ahead=3)
    assert [plan.name for plan in plans] == [
        "events_2026_11",
        "events_2026_12",
        "events_2027_01",
        "events_2027_02",
    ]
    # Ranges must be contiguous and half-open, or a timestamp falls in a gap and
    # lands in DEFAULT despite both neighbouring partitions existing.
    for earlier, later in itertools.pairwise(plans):
        assert earlier.end == later.start


def test_retention_horizon_counts_calendar_months_not_days() -> None:
    """``timedelta(days=31 * n)`` drifts by up to three days a year, and a
    retention boundary that drifts eventually detaches a month still required."""
    horizon = retention_horizon(datetime(2027, 3, 15, tzinfo=UTC), retain_months=12)
    assert horizon == datetime(2026, 3, 1, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Against a real partitioned table
# ---------------------------------------------------------------------------


async def test_ensure_partitions_is_idempotent(session: AsyncSession) -> None:
    """Safe to call on every startup as well as on a schedule."""
    first = await ensure_partitions(session, now=datetime(2026, 8, 16, tzinfo=UTC))
    await session.commit()
    second = await ensure_partitions(session, now=datetime(2026, 8, 16, tzinfo=UTC))
    await session.commit()

    assert second == []
    assert all(name.startswith("events_") for name in first)


async def test_ensure_partitions_extends_the_window_forward(session: AsyncSession) -> None:
    created = await ensure_partitions(session, now=datetime(2027, 6, 1, tzinfo=UTC))
    await session.commit()

    assert "events_2027_06" in created
    assert "events_2027_09" in created, "the three-month window must be created ahead of time"


async def test_default_partition_stays_empty_under_normal_writes(
    session: AsyncSession, tenant_id: uuid.UUID
) -> None:
    with tenant_scope(tenant_id):
        await EventStore(session).append(
            entity_id=uuid.uuid4(),
            event_type="complaint_submitted",
            payload={"latitude": 19.0, "longitude": 72.8},
        )
        await session.commit()

    assert await default_partition_rows(session) == 0


async def test_a_write_outside_the_window_lands_in_default_rather_than_failing(
    session: AsyncSession, tenant_id: uuid.UUID
) -> None:
    """The whole reason the DEFAULT partition exists.

    Without it this insert is *rejected*, turning a missed maintenance run into
    a total outage of the write path of an append-only log — with no way to
    record that it happened.
    """
    far_future = datetime(2099, 1, 15, tzinfo=UTC)
    await session.execute(
        text(
            "INSERT INTO events (tenant_id, recorded_at, occurred_at, entity_type, entity_id, "
            "sequence, event_type, event_version, payload, previous_hash, event_hash) "
            "VALUES (:tenant, :recorded, :recorded, 'complaint', :entity, 1, "
            "'complaint_submitted', 1, '{}'::jsonb, :h, :h)"
        ).bindparams(tenant=tenant_id, recorded=far_future, entity=uuid.uuid4(), h="0" * 64)
    )
    await session.commit()

    assert await default_partition_rows(session) == 1
    assert await default_partition_rows(session, since=far_future) == 1


async def test_ensure_partitions_refuses_to_attach_over_a_non_empty_default(
    session: AsyncSession, tenant_id: uuid.UUID
) -> None:
    """Refusing is the correct behaviour, not a failure.

    Attaching would scan the default partition under an ACCESS EXCLUSIVE lock,
    stalling every writer for a job nobody scheduled. The task hands the
    decision back instead, and the alert makes it visible.
    """
    stranded_month = datetime(2099, 3, 10, tzinfo=UTC)
    await session.execute(
        text(
            "INSERT INTO events (tenant_id, recorded_at, occurred_at, entity_type, entity_id, "
            "sequence, event_type, event_version, payload, previous_hash, event_hash) "
            "VALUES (:tenant, :recorded, :recorded, 'complaint', :entity, 1, "
            "'complaint_submitted', 1, '{}'::jsonb, :h, :h)"
        ).bindparams(tenant=tenant_id, recorded=stranded_month, entity=uuid.uuid4(), h="0" * 64)
    )
    await session.commit()

    created = await ensure_partitions(session, now=stranded_month, months_ahead=0)
    assert "events_2099_03" not in created


async def test_detachable_partitions_reports_only_fully_expired_months(
    session: AsyncSession,
) -> None:
    """Reports candidates; never detaches. Retention on an append-only civic
    record is a decision with legal weight, so the automated half stops here."""
    await ensure_partitions(session, now=datetime(2026, 8, 16, tzinfo=UTC))
    await session.commit()

    eligible = await detachable_partitions(session, older_than=datetime(2026, 8, 1, tzinfo=UTC))
    assert "events_2026_07" in eligible
    # The current month is still being written to; its range end is *after* the
    # horizon, so it must never be eligible.
    assert "events_2026_08" not in eligible
    assert "events_default" not in eligible


# ---------------------------------------------------------------------------
# The scheduled tasks
# ---------------------------------------------------------------------------


async def test_sweep_reports_a_clean_run(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    with tenant_scope(tenant_id):
        store = EventStore(session)
        for _ in range(3):
            await store.append(
                entity_id=uuid.uuid4(),
                event_type="complaint_submitted",
                payload={"latitude": 1.0, "longitude": 2.0},
            )
        await session.commit()

    result = await sweep_chains(session)
    assert result.chains_checked == 3
    assert result.chains_broken == 0


async def test_sweep_finds_a_tampered_chain_across_tenants(
    session: AsyncSession, tenant_id: uuid.UUID, other_tenant_id: uuid.UUID
) -> None:
    """The sweep is deliberately cross-tenant.

    Scoping it would make each customer responsible for detecting tampering in
    their own data, and blind it to the cross-tenant row moves the widened hash
    preimage exists to catch (ADR-0010).
    """
    victim = uuid.uuid4()
    with tenant_scope(other_tenant_id):
        await EventStore(session).append(
            entity_id=victim,
            event_type="complaint_submitted",
            payload={"latitude": 1.0, "longitude": 2.0},
        )
        await session.commit()

    await session.execute(
        text(
            "UPDATE events SET payload = jsonb_set(payload, '{latitude}', '80') "
            "WHERE entity_id = :entity"
        ).bindparams(entity=victim)
    )
    await session.commit()

    # Swept with no tenant argument, from a session scoped to a *different*
    # tenant — the finding must still surface.
    with tenant_scope(tenant_id):
        result = await sweep_chains(session)

    assert result.chains_broken == 1
    assert result.findings[0].tenant_id == other_tenant_id
    assert result.findings[0].first_break is not None


async def test_maintenance_task_reports_partitions_and_stranded_rows(
    bound_global_engine: None,
) -> None:
    from nemesis.pipeline.integrity import _maintain_event_partitions

    result = await _maintain_event_partitions()
    assert result["rows_in_default_partition"] == 0
    assert isinstance(result["created"], list)
    assert isinstance(result["eligible_for_archival"], list)


async def test_sweep_task_reports_a_clean_run(
    bound_global_engine: None, tenant_id: uuid.UUID
) -> None:
    from nemesis.pipeline.integrity import _sweep_chain_integrity

    result = await _sweep_chain_integrity(limit=10)
    assert result["chains_broken"] == 0
    assert "swept_at" in result


async def test_system_degradation_is_recorded_on_its_own_chain(
    bound_global_engine: None, migrated_engine: AsyncEngine
) -> None:
    """§24.2. In the log rather than only in metrics, because a complaint
    processed during a degradation was processed *differently*."""
    from nemesis.pipeline.integrity import SYSTEM_TENANT_ID, record_system_degradation

    # No manual insert. This test used to create the reserved tenant itself,
    # which made it pass while the production path could not: `events.tenant_id`
    # has a foreign key, and nothing outside this test had ever created the row.
    # Phase 3 seeds it by migration and the `tenants` fixture restores it after
    # every truncate, so the append here now exercises what actually ships.
    await record_system_degradation(
        component="ollama",
        failure_mode="connect_timeout",
        fallback_taken="human_review",
        correlation_id="abc-123",
    )

    factory = async_sessionmaker(bind=migrated_engine, expire_on_commit=False)
    async with factory() as reader:
        with tenant_scope(SYSTEM_TENANT_ID):
            stream = await EventStore(reader).read_stream(
                entity_type=EntityType.SYSTEM, entity_id=SYSTEM_TENANT_ID
            )

    assert len(stream) == 1
    assert stream[0].payload["component"] == "ollama"


def test_scheduled_tasks_are_actually_registered_with_celery() -> None:
    """A scheduled job nobody registered is worse than no job at all.

    `autodiscover_tasks(["nemesis.pipeline"])` only looks for a module named
    `tasks.py`, so `pipeline/integrity.py` registered **zero** tasks and loaded
    **zero** beat schedules — silently. `celery inspect registered` reported
    "empty" and no error appeared anywhere. The integrity sweep would never have
    run, and the only thing that would eventually have said so is the
    dead-man's-switch alert built to catch exactly that.

    This asserts registration and scheduling together, because either one
    missing produces the same symptom: nothing happens, quietly.
    """
    from nemesis.worker.celery_app import celery_app

    # Loaded the way a worker loads them — through `include`, not by importing
    # the task modules here. Importing them by hand would register the tasks and
    # prove nothing about whether a real worker ever would, which is precisely
    # the gap that let `pipeline/integrity.py` contribute zero tasks silently.
    celery_app.loader.import_default_modules()

    registered = set(celery_app.tasks)
    assert "nemesis.integrity.sweep_chains" in registered
    assert "nemesis.integrity.maintain_partitions" in registered

    assert "nemesis.integrity.purge_outbox" in registered
    # The Phase 3 pipeline entry point. Registered by name for the same reason:
    # a stage task the worker never loaded fails as "nothing happened".
    assert "nemesis.pipeline.run_stage" in registered
    # Phase 4. The dedicated dispatcher process is the primary delivery path;
    # these are the safety net for a deployment that has not started it, and a
    # webhook that silently stops being delivered is exactly the "nothing
    # happened" failure this whole assertion exists to catch.
    assert "nemesis.integrations.fan_out" in registered
    assert "nemesis.integrations.dispatch" in registered
    assert "nemesis.integrations.sweep" in registered

    # Redis implements `task_acks_late` as a visibility timeout, not as an
    # acknowledgement, so an unset value means a worker killed mid-task has its
    # work redelivered an hour later — with nothing anywhere saying so. It must
    # also exceed the task time limit, or a task still legitimately running is
    # redelivered and runs concurrently with itself.
    visibility = celery_app.conf.broker_transport_options.get("visibility_timeout")
    assert visibility is not None, "visibility_timeout is unset; the default is 3600 seconds"
    assert visibility > celery_app.conf.task_time_limit

    scheduled = {entry["task"] for entry in celery_app.conf.beat_schedule.values()}
    assert scheduled == {
        "nemesis.integrity.sweep_chains",
        "nemesis.integrity.maintain_partitions",
        "nemesis.integrity.purge_outbox",
        "nemesis.integrations.fan_out",
        "nemesis.integrations.dispatch",
        "nemesis.integrations.sweep",
    }


# ---------------------------------------------------------------------------
# Operator CLI
# ---------------------------------------------------------------------------


async def test_inspect_cli_reports_an_intact_chain(
    bound_global_engine: None, migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    from nemesis.events import inspect

    complaint_id = uuid.uuid4()
    factory = async_sessionmaker(bind=migrated_engine, expire_on_commit=False)
    async with factory() as writer:
        with tenant_scope(tenant_id):
            await EventStore(writer).append(
                entity_id=complaint_id,
                event_type="complaint_submitted",
                payload={"latitude": 1.0, "longitude": 2.0},
            )
            await writer.commit()

    exit_code = await inspect._verify_one(
        tenant_id, EntityType.COMPLAINT.value, complaint_id, show_state=True
    )
    assert exit_code == 0


async def test_inspect_cli_exits_non_zero_on_a_broken_chain(
    bound_global_engine: None, migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A non-zero exit is what makes this usable from a script or a healthcheck."""
    from nemesis.events import inspect

    complaint_id = uuid.uuid4()
    factory = async_sessionmaker(bind=migrated_engine, expire_on_commit=False)
    async with factory() as writer:
        with tenant_scope(tenant_id):
            await EventStore(writer).append(
                entity_id=complaint_id,
                event_type="complaint_submitted",
                payload={"latitude": 1.0, "longitude": 2.0},
            )
            await writer.commit()
        await writer.execute(
            text("UPDATE events SET event_hash = :bogus WHERE entity_id = :entity").bindparams(
                bogus="f" * 64, entity=complaint_id
            )
        )
        await writer.commit()

    assert (
        await inspect._verify_one(
            tenant_id, EntityType.COMPLAINT.value, complaint_id, show_state=False
        )
        == 1
    )
    assert await inspect._sweep(tenant_id, limit=10) == 1
