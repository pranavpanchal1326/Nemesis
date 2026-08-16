"""Multi-tenancy primitives.

Principle 3 of the program plan: tenant isolation is designed in from row zero,
never retrofitted, because retrofitting tenancy is a rewrite. Three pieces make
that concrete:

``context``
    The ambient tenant for the current request or task, carried in a
    ``ContextVar`` so it survives an ``await`` and cannot leak across
    concurrent requests the way a module global would.
``registry``
    Which tables are tenant-scoped, populated by declaring the mixin rather
    than by maintaining a list someone forgets to append to.
``guard``
    A statement interceptor that refuses to execute a query touching a scoped
    table without a ``tenant_id`` predicate. Convention is not isolation; a
    cross-tenant read is a data breach, and the only acceptable failure mode is
    a loud one before the query runs.
"""

from __future__ import annotations

from nemesis.tenancy.context import (
    TenantContextError,
    current_tenant_id,
    require_tenant_id,
    tenant_scope,
)
from nemesis.tenancy.guard import (
    TENANT_SCOPE_EXEMPT,
    CrossTenantQueryError,
    install_tenant_guard,
)
from nemesis.tenancy.registry import (
    TENANT_COLUMN,
    is_tenant_scoped,
    register_tenant_scoped_table,
    tenant_scoped_tables,
)

__all__ = [
    "TENANT_COLUMN",
    "TENANT_SCOPE_EXEMPT",
    "CrossTenantQueryError",
    "TenantContextError",
    "current_tenant_id",
    "install_tenant_guard",
    "is_tenant_scoped",
    "register_tenant_scoped_table",
    "require_tenant_id",
    "tenant_scope",
    "tenant_scoped_tables",
]
