"""The read-only guarantee: two independent layers, on a transaction of its own.

Phase 7's third gate clause is *shadow mode provably cannot mutate state or emit
domain events*. "Provably" is the load-bearing word, and it rules out the
implementation everybody writes first — a shadow path that simply never calls a
write function. That is a property of the code as it stands today, held in place
by nothing, and it survives exactly until somebody adds a metric write, a cache
row, or a "record what we saw" line to a function three frames down.

So the guarantee is enforced instead, twice, by mechanisms that fail differently:

**Postgres.** ``SET TRANSACTION READ ONLY``. The database itself refuses
``INSERT``, ``UPDATE``, ``DELETE`` and DDL for the rest of the transaction —
including from raw ``text()`` SQL, which the statement guard below cannot parse,
and including from code that does not know it is running inside a shadow
evaluation.

**The statement guard.** A ``before_execute`` listener in the shape
``tenancy.guard`` already uses. It refuses the write *before* the database is
touched, so the traceback names the offending statement rather than surfacing an
asyncpg ``ReadOnlySqlTransactionError`` several frames away with no indication
which of forty statements was the problem.

Neither is redundant. Postgres catches what the guard cannot parse; the guard
gives an error a developer can act on.

**Why this opens its own session rather than borrowing the caller's.** Because
in Postgres, read-only is a *one-way* property of a transaction: ``SET
TRANSACTION READ WRITE`` after the first query fails with "transaction read-write
mode must be set before any query". A scope that marked the caller's transaction
read-only would therefore poison it for the rest of its life — the caller could
never write again, and the failure would arrive at some unrelated statement much
later. The first version of this module did exactly that, and the tests for the
*recording* half caught it.

So the scope borrows the caller's **engine** and runs on a connection of its own,
whose transaction begins clean (making ``SET TRANSACTION READ ONLY`` unambiguously
legal as its first statement) and is rolled back on the way out. The caller's
session is never touched.

**The consequence, stated rather than discovered:** the scope reads *committed*
data. It cannot see the caller's uncommitted work, because it is not in the
caller's transaction. For shadow mode that is the correct reading — an
observation should describe the world other readers can see — but it does mean a
caller that has just written something must commit before observing against it.

**Recording is outside the scope, and that is the design.** A shadow run produces
observation *values*; persisting them is the caller's act, on the caller's
session, into a table holding no domain state. Doing it inside would require an
exemption, and an exemption is a hole with a comment next to it — which is what
this module exists to not have.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Final

from sqlalchemy import Delete, Insert, Update, event, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.sql import ClauseElement
from sqlalchemy.sql.ddl import DDLElement

from nemesis.observability.logging import get_logger
from nemesis.simulation.errors import ShadowWriteError

log = get_logger(__name__)

#: Statement types a shadow evaluation may never emit. ``Insert`` is listed even
#: though ``tenancy.guard`` deliberately omits it: that guard is about *scoping*
#: a write, and an insert carries its tenant in a NOT NULL column. This one is
#: about whether a write may happen at all, and an insert is the most likely way
#: one would — an event append, an observation row, a projection touch.
_FORBIDDEN: Final = (Insert, Update, Delete, DDLElement)


@asynccontextmanager
async def read_only(session: AsyncSession) -> AsyncIterator[AsyncSession]:
    """Yield a session on which writes are refused by two independent layers.

    The yielded session is **not** the one passed in — see the module docstring
    on why borrowing the caller's transaction would poison it permanently. It
    shares the engine, so it reads the same database, and it is closed and
    rolled back on exit.

    The parameter exists to name the engine, and to make the call read as
    "a read-only view of this session's database" at the call site.
    """
    maker = async_sessionmaker(_async_engine_for(session), expire_on_commit=False)

    async with maker() as scoped:
        connection = await scoped.connection()
        sync_connection = connection.sync_connection
        if sync_connection is None:  # pragma: no cover - AsyncConnection always has one
            raise ShadowWriteError("cannot establish a read-only scope without a connection")

        # First statement in a brand-new transaction, which is the only place
        # Postgres accepts it.
        await scoped.execute(text("SET TRANSACTION READ ONLY"))
        event.listen(sync_connection, "before_execute", _refuse_write, retval=False)
        try:
            yield scoped
        finally:
            event.remove(sync_connection, "before_execute", _refuse_write)
            await scoped.rollback()


def _async_engine_for(session: AsyncSession) -> AsyncEngine:
    """The async engine behind ``session``, whatever it was bound with.

    ``AsyncSession.get_bind()`` returns the **synchronous** ``Engine`` — the
    async layer is a facade over it — so it cannot be handed to
    ``async_sessionmaker`` directly. Wrapping it re-establishes the async facade
    over the *same connection pool*, which is what matters: the read-only scope
    borrows a connection from the pool the caller is already using rather than
    opening a second pool to the same database.
    """
    bind = session.get_bind()
    # `Connection.engine` for a session bound to a connection, `Engine.engine`
    # for one bound to an engine — the attribute exists on both and returns the
    # `Engine` either way, which is why this is one expression rather than an
    # isinstance ladder. mypy types `get_bind()` as `Engine | Connection` and
    # cannot see that; the annotation on the return is the claim being made.
    return AsyncEngine(bind.engine)


def _refuse_write(
    conn: Connection,
    clause: ClauseElement,
    multiparams: Any,
    params: Any,
    execution_options: dict[str, Any],
) -> None:
    if not isinstance(clause, _FORBIDDEN):
        return
    rendered = type(clause).__name__.upper()
    # Logged as well as raised. The raise reaches whoever called the shadow
    # runner; the log line reaches whoever is looking at why shadow coverage
    # dropped to zero, which is a different person on a different day.
    log.error("shadow_write_refused", statement=rendered)
    raise ShadowWriteError(
        f"a shadow evaluation attempted a {rendered}. Shadow mode records what a "
        f"candidate policy would have decided; it does not decide. If this write is "
        f"legitimate, it belongs outside the read-only scope on the caller's session — "
        f"not behind an exemption inside it."
    )


__all__ = ["read_only"]
