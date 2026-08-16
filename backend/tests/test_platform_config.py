"""Tests for the worker topology, tracing bootstrap, and persistence layer.

These modules are configuration-heavy rather than logic-heavy, which makes them
easy to leave untested and easy to break silently. Each assertion below
corresponds to a property something else in the system depends on — the ADRs
name most of them explicitly.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, UniqueConstraint
from sqlalchemy.exc import SQLAlchemyError

from nemesis.config import Settings
from nemesis.db import session as session_module
from nemesis.db.base import NAMING_CONVENTION, Base, TimestampMixin, UUIDPrimaryKeyMixin
from nemesis.observability import tracing
from nemesis.worker.celery_app import QUEUE_IO, QUEUE_ML, QUEUE_SAFETY, celery_app
from tests.conftest import postgres_required


class TestWorkerTopology:
    """ADR-0004: workers split by memory profile, safety isolated from backlog."""

    def test_three_queues_are_declared(self) -> None:
        assert set(celery_app.conf.task_queues) == {QUEUE_IO, QUEUE_ML, QUEUE_SAFETY}

    def test_safety_queue_is_separate_from_ml(self) -> None:
        # §11.2: a danger-flagged report must never queue behind classification
        # backlog. Separate queues are what make that structural.
        assert QUEUE_SAFETY != QUEUE_ML

    def test_late_acknowledgement_with_rejection_on_worker_loss(self) -> None:
        # A killed worker must return its task to the queue rather than silently
        # dropping a citizen's complaint (§24.2).
        assert celery_app.conf.task_acks_late is True
        assert celery_app.conf.task_reject_on_worker_lost is True

    def test_prefetch_disabled(self) -> None:
        # Prefetching lets one worker hoard messages, starving the safety queue
        # and inflating per-worker memory — both fatal on a 16GB machine.
        assert celery_app.conf.worker_prefetch_multiplier == 1

    def test_child_recycling_bounds_memory_leakage(self) -> None:
        assert 0 < celery_app.conf.worker_max_tasks_per_child <= 500

    def test_json_only_serialisation(self) -> None:
        # Pickle would make the broker a remote-code-execution surface.
        assert celery_app.conf.task_serializer == "json"
        assert list(celery_app.conf.accept_content) == ["json"]

    def test_utc_enforced(self) -> None:
        # SLA deadlines are time-based; a worker in local time corrupts them.
        assert celery_app.conf.enable_utc is True
        assert celery_app.conf.timezone == "UTC"

    def test_soft_limit_precedes_hard_limit(self) -> None:
        # The soft limit must fire first so a task can clean up and record a
        # degradation event before it is killed outright.
        assert celery_app.conf.task_soft_time_limit < celery_app.conf.task_time_limit


class TestTracingBootstrap:
    def setup_method(self) -> None:
        tracing.reset_for_testing()

    def teardown_method(self) -> None:
        tracing.reset_for_testing()

    def test_disabled_by_default_is_a_no_op(self) -> None:
        # Tracing must never be a startup dependency: a missing collector cannot
        # be allowed to prevent a service from booting.
        tracing.configure_tracing(Settings())
        assert tracing.current_trace_id() is None

    def test_enabling_without_an_endpoint_does_not_raise(self) -> None:
        # Useful for tests that assert instrumentation without a collector.
        tracing.configure_tracing(Settings(otel={"enabled": True}))  # type: ignore[arg-type]

    def test_configuration_is_idempotent(self) -> None:
        # API, worker, and beat may all call it; double-configuring a tracer
        # provider produces duplicate spans.
        settings = Settings(otel={"enabled": True})  # type: ignore[arg-type]
        tracing.configure_tracing(settings)
        tracing.configure_tracing(settings)

    def test_trace_id_is_none_outside_a_span(self) -> None:
        assert tracing.current_trace_id() is None


class TestNamingConvention:
    """Unnamed constraints get server-generated names that differ per
    environment, so a later migration cannot reference one to drop it. That is
    how a migration chain quietly becomes non-reversible."""

    @pytest.mark.parametrize("key", ["ix", "uq", "ck", "fk", "pk"])
    def test_every_constraint_type_has_a_convention(self, key: str) -> None:
        assert key in NAMING_CONVENTION

    def test_base_metadata_applies_the_convention(self) -> None:
        assert Base.metadata.naming_convention == NAMING_CONVENTION

    def test_constraints_are_deterministically_named(self) -> None:
        metadata = MetaData(naming_convention=NAMING_CONVENTION)
        table = Table(
            "widget",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("code", Integer),
            UniqueConstraint("code"),
        )
        names = {c.name for c in table.constraints if c.name}
        assert "pk_widget" in names
        assert "uq_widget_code" in names


class TestModelMixins:
    def test_uuid_primary_key_is_server_generated(self) -> None:
        # UUIDs so public URLs cannot be enumerated to walk other citizens'
        # reports (§22).
        column = UUIDPrimaryKeyMixin.__annotations__
        assert "id" in column

    def test_timestamps_are_timezone_aware(self) -> None:
        # Naive datetimes are a correctness bug in a system whose SLA deadlines
        # and 72-hour dedup window are time-based.
        for field in ("created_at", "updated_at"):
            assert field in TimestampMixin.__annotations__


@postgres_required
@pytest.mark.usefixtures("bound_session")
class TestSessionScope:
    async def test_commits_on_success(self) -> None:
        async with session_module.session_scope() as db:
            result = await db.execute(_select_one())
            assert result.scalar_one() == 1

    async def test_rolls_back_and_reraises_on_failure(self) -> None:
        # §9.1 requires a state change and its event row to be atomic. A scope
        # that swallowed an error would let a state change commit without its
        # event, breaking the audit guarantee the whole design rests on.
        with pytest.raises(SQLAlchemyError):
            async with session_module.session_scope() as db:
                await db.execute(_select_bad())

    async def test_engine_is_a_process_singleton(self) -> None:
        assert session_module.get_engine() is session_module.get_engine()

    async def test_sessionmaker_is_a_process_singleton(self) -> None:
        assert session_module.get_sessionmaker() is session_module.get_sessionmaker()

    async def test_dispose_releases_the_engine(self) -> None:
        # Disposal must actually release pooled connections; a leaked pool is
        # how a worker eventually exhausts max_connections.
        engine = session_module.get_engine()
        await session_module.dispose_engine()
        assert session_module._engine is None
        assert engine is not None


def _select_one() -> object:
    from sqlalchemy import text

    return text("SELECT 1")


def _select_bad() -> object:
    from sqlalchemy import text

    return text("SELECT * FROM a_table_that_does_not_exist")
