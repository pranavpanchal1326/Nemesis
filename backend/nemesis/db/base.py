"""Declarative base and shared column conventions.

The naming convention is set explicitly so Alembic autogenerate produces stable,
named constraints. Without it, unnamed constraints get server-generated names
that differ between environments, and a later migration that needs to drop one
cannot reference it — which is how migration chains quietly become
non-reversible.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, MetaData, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPrimaryKeyMixin:
    """UUID primary keys throughout.

    Entity ids appear in public URLs and in the event log. Sequential integers
    would leak total complaint volume and allow enumeration of other citizens'
    reports — a privacy regression the DPDP posture in §22 does not permit.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )


class TimestampMixin:
    """Timezone-aware timestamps only. Naive datetimes are a correctness bug in
    a system whose SLA deadlines and 72-hour dedup windows are time-based."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class TenantScopedMixin:
    """Every domain table carries its tenant, from the first migration.

    ``NOT NULL`` is doing real work here: it is what makes the tenancy guard
    able to skip checking INSERTs. A row cannot exist without a tenant, so the
    only way to produce a cross-tenant leak is a *read* or a *write* that
    forgets to filter — which is exactly what ``tenancy.guard`` intercepts.

    The foreign key is ``ON DELETE RESTRICT`` rather than cascade. Deleting a
    tenant must be a deliberate, audited offboarding procedure that reckons with
    an append-only log (§22.4 retention, Phase 26 erasure), never a side effect
    of removing one row.
    """

    @declared_attr
    @classmethod
    def tenant_id(cls) -> Mapped[uuid.UUID]:
        # `getattr` rather than `cls.__tablename__`: annotating the mixin with
        # `__tablename__: ClassVar[str]` to satisfy the type checker collides
        # with the instance-level declaration `DeclarativeBase` already makes,
        # and mypy rejects the override on every model. Every concrete subclass
        # sets it — a model without a table name is not a table — so the default
        # is unreachable and exists only to keep this expression total.
        table_name: str = getattr(cls, "__tablename__", "unknown")
        return mapped_column(
            UUID(as_uuid=True),
            ForeignKey("tenants.id", ondelete="RESTRICT", name=f"fk_{table_name}_tenant"),
            nullable=False,
            index=True,
        )


class OptimisticVersionMixin:
    """A version counter on every mutable aggregate.

    Current-state rows are projections of the event log, and two workers can
    finish two pipeline stages for the same complaint at the same time. Without
    a version column the later write silently overwrites the earlier one and the
    projection stops matching the log — which the replay gate would then report
    as a corrupt event store rather than as the lost update it actually is.
    """

    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")


__all__ = [
    "NAMING_CONVENTION",
    "Base",
    "OptimisticVersionMixin",
    "TenantScopedMixin",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
]
