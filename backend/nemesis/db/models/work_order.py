"""Work orders and budget allocations — §9.2, projected from the log.

``NUMERIC`` for money, never ``FLOAT``. §17.2's rate-card deviation detector
compares a budgeted line against an effective-dated card and flags the
difference; binary floating point would make that comparison produce
sub-rupee ghosts, and a fraud signal that fires on rounding error is a fraud
signal that gets switched off.

``fund_released`` is **SIMULATED** and labelled as such in the column comment,
the API response, and the UI (§44). Phase 14 ships the workflow; no code in this
system moves money, and the schema says so where somebody reading it will look.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from nemesis.db.base import (
    Base,
    OptimisticVersionMixin,
    TenantScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from nemesis.domain.lifecycle import WorkOrderStatus

#: Rupee amounts with paise. 14 integer digits is more headroom than any ward
#: budget needs and still far inside NUMERIC's exact range.
MONEY = Numeric(16, 2)


class WorkOrder(
    UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, OptimisticVersionMixin, Base
):
    __tablename__ = "work_orders"
    __table_args__ = (
        Index("ix_work_orders_tenant_id_status", "tenant_id", "status"),
        # The Phase 14 triage queue sorts by SLA deadline within a tenant, and
        # the Phase 12 sweeper scans for deadlines that have passed. Both are
        # this index; without it the sweeper degrades to a full scan on every
        # Beat tick, which is how a background job becomes an outage.
        Index("ix_work_orders_tenant_id_sla_deadline", "tenant_id", "sla_deadline"),
    )

    complaint_cluster_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("complaint_clusters.id", ondelete="RESTRICT", name="fk_work_orders_cluster"),
        nullable=False,
        index=True,
    )
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("departments.id", ondelete="RESTRICT", name="fk_work_orders_department"),
        nullable=True,
        index=True,
    )

    #: 'staff' | 'contractor' — see ``domain.lifecycle.AssigneeType``. A
    #: polymorphic reference rather than two nullable foreign keys, because the
    #: two are genuinely alternatives and a row with both set is meaningless.
    assigned_to_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    assigned_to_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=WorkOrderStatus.CREATED.value
    )
    sla_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Recorded when the deadline is computed, so a later calendar or policy
    #: change is visible as a difference rather than silently rewriting history.
    sla_policy_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    budgeted_cost: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    actual_cost: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    #: SIMULATED (§44). Phase 14 records the decision; nothing disburses funds.
    fund_released: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    before_photo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_photo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Structural similarity between the two photos (§21.2). Phase 15's state
    #: machine — not the UI — refuses to reach `resolved` without it.
    ssim_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    milestone_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)


class BudgetAllocation(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "budget_allocations"
    __table_args__ = (
        Index(
            "ix_budget_allocations_tenant_id_ward_fiscal_year",
            "tenant_id",
            "ward",
            "fiscal_year",
        ),
    )

    ward: Mapped[str | None] = mapped_column(String(128), nullable=True)
    funding_source: Mapped[str] = mapped_column(String(128), nullable=False)
    #: A label, not a date range: fiscal years differ by tenant and by country,
    #: and "2025-26" is what appears on the document a citizen is shown.
    fiscal_year: Mapped[str] = mapped_column(String(16), nullable=False)
    allocated_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    spent_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, server_default="0")
