"""Which tables are tenant-scoped.

Populated by declaring ``TenantScopedMixin`` on a model, not by maintaining a
list. A hand-maintained list is a second place to remember, and the failure mode
of forgetting is that a new table silently opts out of every isolation check —
the one class of mistake this whole module exists to prevent.

A small number of tables are deliberately *not* scoped, and each is named here
with its reason rather than being absent by accident.
"""

from __future__ import annotations

from typing import Final

#: The column every scoped table carries, and the predicate the guard demands.
TENANT_COLUMN: Final = "tenant_id"

#: Tables that legitimately have no ``tenant_id``, with the reason each is safe.
#: The guard consults this only to produce a clearer error; membership here is
#: not a bypass, because these tables have no tenant column to scope by.
UNSCOPED_TABLES: Final[dict[str, str]] = {
    "tenants": "the tenant registry itself — its primary key *is* the tenant",
    "alembic_version": "migration bookkeeping, owned by the schema not by a customer",
    "archived_partitions": (
        "a record of detached `events` partitions, which span every tenant by "
        "construction — retention runs on the calendar, not per customer"
    ),
}

_scoped: set[str] = set()


def register_tenant_scoped_table(table_name: str) -> None:
    """Record that ``table_name`` carries a tenant column and must be scoped."""
    _scoped.add(table_name)


def tenant_scoped_tables() -> frozenset[str]:
    return frozenset(_scoped)


def is_tenant_scoped(table_name: str) -> bool:
    return table_name in _scoped
