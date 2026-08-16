"""Tenant isolation, at both layers that enforce it.

A cross-tenant read is a data breach, so these tests are written as attacks
rather than as demonstrations: each one issues the query a careless or hurried
developer would actually write, and asserts it is refused.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from nemesis.db.models.complaint import Complaint
from nemesis.db.models.event import Event, EventChainHead
from nemesis.db.models.tenant import Tenant
from nemesis.domain.lifecycle import EntityType
from nemesis.events.store import EventStore
from nemesis.tenancy.context import (
    TenantContextError,
    current_tenant_id,
    require_tenant_id,
    tenant_scope,
)
from nemesis.tenancy.guard import TENANT_SCOPE_EXEMPT, CrossTenantQueryError
from nemesis.tenancy.registry import UNSCOPED_TABLES, is_tenant_scoped, tenant_scoped_tables
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


# ---------------------------------------------------------------------------
# The context itself
# ---------------------------------------------------------------------------


def test_no_tenant_by_default() -> None:
    assert current_tenant_id() is None
    with pytest.raises(TenantContextError, match="no tenant in context"):
        require_tenant_id()


def test_scope_restores_the_previous_tenant_on_exit() -> None:
    """Nesting must unwind, not clear.

    A support tool impersonating a tenant inside a request that already has one
    is a real Phase 27 flow. Resetting to ``None`` instead of to the token would
    strip the outer scope and make the rest of that request unscoped.
    """
    outer, inner = uuid.uuid4(), uuid.uuid4()
    with tenant_scope(outer):
        assert require_tenant_id() == outer
        with tenant_scope(inner):
            assert require_tenant_id() == inner
        assert require_tenant_id() == outer
    assert current_tenant_id() is None


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------


def test_every_domain_table_is_registered_as_scoped() -> None:
    """Derived from the schema, so a new table cannot silently opt out."""
    scoped = tenant_scoped_tables()
    assert "complaints" in scoped
    assert "events" in scoped
    assert "event_chain_heads" in scoped
    assert not is_tenant_scoped("tenants")


def test_unscoped_tables_each_carry_a_stated_reason() -> None:
    """An exemption with no reason is indistinguishable from an oversight."""
    for table, reason in UNSCOPED_TABLES.items():
        assert reason.strip(), table
        assert not is_tenant_scoped(table), table


def test_scoped_tables_are_declared_by_the_mixin_not_by_a_list() -> None:
    """The static CI check reads the same declaration this registry does.

    ``scripts/check_tenant_scoping.py`` cannot import SQLAlchemy, so it finds
    scoped models by looking for ``TenantScopedMixin`` in the model source. That
    only stays accurate if the mixin is genuinely how scoping is declared —
    a model that grew a ``tenant_id`` column by hand would be registered here
    (the registry reads the schema) and invisible there.
    """
    from nemesis.db.base import TenantScopedMixin
    from nemesis.db.models import Complaint, Event

    for model in (Complaint, Event):
        assert issubclass(model, TenantScopedMixin), model.__name__
        assert is_tenant_scoped(model.__tablename__)


# ---------------------------------------------------------------------------
# The runtime guard
# ---------------------------------------------------------------------------


async def test_unscoped_select_is_refused(session: AsyncSession) -> None:
    with pytest.raises(CrossTenantQueryError, match="complaints"):
        await session.execute(select(Complaint))


async def test_unscoped_update_is_refused(session: AsyncSession) -> None:
    with pytest.raises(CrossTenantQueryError, match="complaints"):
        await session.execute(update(Complaint).values(status="closed"))


async def test_unscoped_delete_is_refused(session: AsyncSession) -> None:
    with pytest.raises(CrossTenantQueryError, match="events"):
        await session.execute(delete(Event))


async def test_selecting_the_tenant_column_does_not_count_as_scoping(
    session: AsyncSession,
) -> None:
    """The subtle one.

    ``select(Complaint.tenant_id, Complaint.id)`` mentions the column but
    constrains nothing, and returns every tenant's rows. A guard that looked for
    the column anywhere in the statement would wave it through.
    """
    with pytest.raises(CrossTenantQueryError):
        await session.execute(select(Complaint.tenant_id, Complaint.id))


async def test_unscoped_subquery_is_refused(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """A scoped outer query does not launder an unscoped inner one."""
    inner = select(Complaint.cluster_id).scalar_subquery()
    with pytest.raises(CrossTenantQueryError):
        await session.execute(
            select(Event).where(Event.tenant_id == tenant_id, Event.entity_id.in_(inner))
        )


async def test_scoped_select_is_allowed(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    result = await session.execute(select(Complaint).where(Complaint.tenant_id == tenant_id))
    assert result.scalars().all() == []


async def test_unscoped_tables_are_not_guarded(session: AsyncSession) -> None:
    """``tenants`` has no tenant column — its primary key *is* the tenant."""
    result = await session.execute(select(Tenant))
    assert len(result.scalars().all()) == 2


async def test_explicit_exemption_is_honoured(session: AsyncSession) -> None:
    """The escape hatch exists, and it is loud.

    Cross-tenant work is legitimate — the integrity sweep, partition
    maintenance — and making it *possible but explicit* is what keeps it
    auditable. An implicit bypass would be neither.
    """
    statement = select(EventChainHead.entity_id).execution_options(**{TENANT_SCOPE_EXEMPT: True})
    result = await session.execute(statement)
    assert result.all() == []


async def test_a_tenant_cannot_read_another_tenants_events(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID, other_tenant_id: uuid.UUID
) -> None:
    """The property every other test here exists to protect.

    Both tenants write an event for the same entity id, then each reads its own
    stream. Correct scoping means each sees exactly one event — the leak this
    guards against would show two.
    """
    factory = async_sessionmaker(bind=migrated_engine, expire_on_commit=False, autoflush=False)
    shared_entity = uuid.uuid4()
    payload = {"latitude": 19.0, "longitude": 72.8}

    for owner in (tenant_id, other_tenant_id):
        async with factory() as writer:
            with tenant_scope(owner):
                await EventStore(writer).append(
                    entity_id=shared_entity,
                    event_type="complaint_submitted",
                    payload=payload,
                )
                await writer.commit()

    async with factory() as reader:
        for owner in (tenant_id, other_tenant_id):
            with tenant_scope(owner):
                stream = await EventStore(reader).read_stream(
                    entity_type=EntityType.COMPLAINT, entity_id=shared_entity
                )
            assert len(stream) == 1
            assert stream[0].tenant_id == owner


async def test_append_without_a_tenant_in_context_is_refused(
    session: AsyncSession,
) -> None:
    with pytest.raises(TenantContextError):
        await EventStore(session).append(
            entity_id=uuid.uuid4(),
            event_type="complaint_submitted",
            payload={"latitude": 0.0, "longitude": 0.0},
        )
