"""Refuse to execute a query that reads or writes a scoped table unscoped.

The program plan asks for "a query-construction guard that makes an unscoped
domain query fail at import time rather than at runtime". That is two different
guarantees, and this codebase ships both rather than pretending either one is
sufficient alone:

**Import time** — ``scripts/check_tenant_scoping.py`` walks the AST of every
domain module in CI. It catches the mistake before the code merges, which is
where a mistake is cheap. It cannot see a predicate assembled at runtime.

**Execution time** — this module, a ``before_execute`` interceptor on the
engine. It sees the statement actually about to run, however it was built, and
raises before the database is touched. It cannot see raw SQL.

Neither covers ``text()``. Parsing arbitrary SQL to find a predicate is a
losing game, so raw SQL is handled by the static check flagging it inside
domain modules, and by Postgres Row-Level Security in Phase 25 — the only layer
that also binds a human with a ``psql`` session. Saying that plainly is better
than implying a guarantee that stops at the ORM boundary.

The failure mode is chosen deliberately: raise, never silently inject a
predicate. Auto-scoping a query is friendlier and much worse — it hides the bug,
so the developer never learns the query was wrong and the same omission ships
again in a code path where auto-scoping does not apply.
"""

from __future__ import annotations

from typing import Any, Final

from sqlalchemy import Column, Delete, Select, Table, Update, event
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.sql import ClauseElement, visitors
from sqlalchemy.sql.selectable import Join

from nemesis.tenancy.context import current_tenant_id
from nemesis.tenancy.registry import TENANT_COLUMN, UNSCOPED_TABLES, is_tenant_scoped

#: Execution option that opts a statement out. Every use must carry a comment
#: naming the reason, and the static check requires one — the legitimate cases
#: are cross-tenant by definition (the integrity sweep, partition maintenance,
#: the tenant registry itself), and each is auditable precisely because opting
#: out is explicit.
TENANT_SCOPE_EXEMPT: Final = "nemesis_tenant_scope_exempt"

_GUARDED_STATEMENTS: Final = (Select, Update, Delete)


class CrossTenantQueryError(RuntimeError):
    """A statement touched a tenant-scoped table without a ``tenant_id`` predicate."""


def install_tenant_guard(engine: Engine) -> None:
    """Attach the guard to a synchronous ``Engine``.

    Async engines expose their sync counterpart as ``engine.sync_engine``; the
    event system lives there, and listening on it covers every statement the
    async layer emits.
    """
    if not event.contains(engine, "before_execute", _before_execute):
        event.listen(engine, "before_execute", _before_execute, retval=False)


def _before_execute(
    conn: Connection,
    clause: ClauseElement,
    multiparams: Any,
    params: Any,
    execution_options: dict[str, Any],
) -> None:
    if execution_options.get(TENANT_SCOPE_EXEMPT):
        return
    if not isinstance(clause, _GUARDED_STATEMENTS):
        # INSERT is covered by the NOT NULL constraint on every tenant column:
        # an insert that omits the tenant fails in the database, so duplicating
        # that check here would only add a second place to keep correct.
        # DDL and text() are handled by the layers named in the module docstring.
        return

    touched = _scoped_tables_referenced(clause)
    if not touched:
        return

    scoped_by = _tables_with_tenant_predicate(clause)
    missing = sorted(touched - scoped_by)
    if not missing:
        return

    raise CrossTenantQueryError(
        f"{type(clause).__name__} touches tenant-scoped table(s) {', '.join(missing)} "
        f"without a {TENANT_COLUMN} predicate (tenant in context: {current_tenant_id()}). "
        f"Add the predicate, or set .execution_options({TENANT_SCOPE_EXEMPT}=True) "
        f"with a comment explaining why this statement is legitimately cross-tenant."
    )


def _scoped_tables_referenced(clause: ClauseElement) -> set[str]:
    """Every tenant-scoped table anywhere in the statement.

    Traversing the whole tree rather than the top-level FROM list on purpose:
    subqueries, CTEs, and EXISTS clauses read exactly the same rows as a
    top-level select, and a guard that only inspected the outer FROM would wave
    through the most convenient way to write an unscoped read.
    """
    return {
        element.name
        for element in visitors.iterate(clause)
        if isinstance(element, Table) and is_tenant_scoped(element.name)
    }


def _tables_with_tenant_predicate(clause: ClauseElement) -> set[str]:
    """Tables whose ``tenant_id`` appears in a filtering position.

    Filtering positions only — WHERE clauses and JOIN conditions. Selecting the
    column into the result set does not constrain anything, and counting it
    would make ``select(Complaint.tenant_id, Complaint.id)`` look scoped while
    returning every tenant's rows.
    """
    predicates: list[ClauseElement] = []

    whereclause = getattr(clause, "whereclause", None)
    if whereclause is not None:
        predicates.append(whereclause)

    for element in visitors.iterate(clause):
        if isinstance(element, Join) and element.onclause is not None:
            predicates.append(element.onclause)
        # A subquery's own WHERE scopes the subquery's tables.
        elif isinstance(element, Select) and element.whereclause is not None:
            predicates.append(element.whereclause)

    scoped: set[str] = set()
    for predicate in predicates:
        for element in visitors.iterate(predicate):
            if (
                isinstance(element, Column)
                and element.name == TENANT_COLUMN
                and isinstance(element.table, Table)
            ):
                scoped.add(element.table.name)
    return scoped


def explain_unscoped(table_name: str) -> str:
    """Human-readable reason a table is exempt, for error messages and docs."""
    return UNSCOPED_TABLES.get(table_name, "not registered as tenant-scoped")
