"""ORM models.

Every model module must be imported here. ``alembic/env.py`` imports this
package to populate ``Base.metadata``; a model that is defined but not imported
is invisible to autogenerate, which silently produces a migration that drops it.

Importing here also does the tenant-scoped table registration, and it does it
**from the schema rather than from a list**. Any table that has a ``tenant_id``
column is registered, whether or not it declared ``TenantScopedMixin``. A
hand-maintained list would be a second thing to remember, and the failure mode
of forgetting — a new table silently exempt from every isolation check — is the
one this module exists to prevent.
"""

from __future__ import annotations

from nemesis.db.base import Base
from nemesis.db.models.complaint import Complaint, ComplaintCluster
from nemesis.db.models.event import (
    ArchivedPartition,
    Event,
    EventChainHead,
    EventIdempotency,
    EventSnapshot,
)
from nemesis.db.models.organisation import Contractor, Department, User
from nemesis.db.models.outbox import OutboxMessage, PipelineDeadLetter
from nemesis.db.models.tenant import Tenant
from nemesis.db.models.work_order import BudgetAllocation, WorkOrder
from nemesis.tenancy.registry import TENANT_COLUMN, register_tenant_scoped_table

for _table in Base.metadata.tables.values():
    if TENANT_COLUMN in _table.c:
        register_tenant_scoped_table(_table.name)

__all__ = [
    "ArchivedPartition",
    "BudgetAllocation",
    "Complaint",
    "ComplaintCluster",
    "Contractor",
    "Department",
    "Event",
    "EventChainHead",
    "EventIdempotency",
    "EventSnapshot",
    "OutboxMessage",
    "PipelineDeadLetter",
    "Tenant",
    "User",
    "WorkOrder",
]
