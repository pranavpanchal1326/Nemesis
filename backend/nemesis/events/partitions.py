"""Keep the ``events`` partition window ahead of the clock.

Partitions are created here rather than by migrations because they are a
function of the calendar, not of the schema version. A migration that created
"the next twelve months" would silently stop working in month thirteen, and the
symptom would be every write in the system failing at once.

The DEFAULT partition means that failure cannot happen: an insert whose
``recorded_at`` matches no declared range lands there instead of being rejected.
That safety net has a cost, and it is the reason this job matters. Attaching a
partition for a range that already has rows in DEFAULT requires Postgres to scan
the default partition and take an ``ACCESS EXCLUSIVE`` lock — on a hot,
append-only table. So the invariant to maintain is not "partitions exist", it is
**the default partition stays empty**, and ``ensure_partitions`` running ahead of
time is what keeps it that way.

Both facts are observable: ``nemesis_event_default_partition_rows`` is exported
for the alert rule, and ``ensure_partitions`` refuses to attach over a
non-empty default rather than blocking the write path while it scans.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.tenancy.guard import TENANT_SCOPE_EXEMPT

logger = structlog.get_logger(__name__)

PARENT_TABLE: Final = "events"
DEFAULT_PARTITION: Final = "events_default"

#: How far ahead to keep partitions. Three months is comfortably more than any
#: plausible gap between maintenance runs, and small enough that an empty
#: partition per month is not a meaningful catalog cost.
MONTHS_AHEAD: Final = 3

#: Guards the identifier interpolated into DDL. Partition names are derived
#: here, never supplied by a caller, so this is a defence against a future
#: refactor introducing a parameter — not against today's inputs.
_SAFE_PARTITION_NAME: Final = re.compile(r"^events_\d{4}_\d{2}$")


@dataclass(frozen=True, slots=True)
class PartitionPlan:
    name: str
    start: datetime
    end: datetime


def month_start(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0, tzinfo=UTC
    )


def next_month(moment: datetime) -> datetime:
    if moment.month == 12:
        return moment.replace(year=moment.year + 1, month=1)
    return moment.replace(month=moment.month + 1)


def plan_partitions(now: datetime, *, months_ahead: int = MONTHS_AHEAD) -> list[PartitionPlan]:
    """The partitions that should exist, current month through ``months_ahead``."""
    plans: list[PartitionPlan] = []
    start = month_start(now)
    for _ in range(months_ahead + 1):
        end = next_month(start)
        plans.append(PartitionPlan(name=f"events_{start:%Y_%m}", start=start, end=end))
        start = end
    return plans


async def ensure_partitions(
    session: AsyncSession, *, now: datetime | None = None, months_ahead: int = MONTHS_AHEAD
) -> list[str]:
    """Create any missing partitions in the window. Returns the names created.

    Idempotent, so it is safe to call on every startup as well as on a schedule.
    ``CREATE TABLE IF NOT EXISTS ... PARTITION OF`` is not a no-op when the
    range overlaps an existing partition — it raises — but the window is derived
    from the calendar, so two concurrent runs compute identical ranges and the
    loser sees the table already exists.
    """
    moment = now or datetime.now(tz=UTC)
    existing = await _existing_partitions(session)
    created: list[str] = []

    for plan in plan_partitions(moment, months_ahead=months_ahead):
        if plan.name in existing:
            continue
        if not _SAFE_PARTITION_NAME.match(plan.name):  # pragma: no cover — derived, not supplied
            raise ValueError(f"refusing to create partition with unexpected name {plan.name!r}")

        default_rows = await default_partition_rows(session, since=plan.start, until=plan.end)
        if default_rows:
            # Attaching would scan and lock the default partition, stalling
            # every append while it ran. Surfacing it as an alert lets the work
            # be scheduled instead of ambushing the write path.
            logger.error(
                "event_partition_attach_blocked",
                partition=plan.name,
                rows_in_default=default_rows,
                remedy="docs/runbooks/event-partition-maintenance.md",
            )
            continue

        # Literals, not bind parameters. Postgres accepts parameters only in
        # DML — a parameterised CREATE TABLE fails with "the server expects 0
        # arguments for this query", which is how this was found.
        #
        # Safe to interpolate because nothing here comes from a caller: the
        # bounds are produced by `plan_partitions` from the calendar and
        # rendered by `strftime` into a fixed digit-and-separator format, and
        # the table name is matched against `_SAFE_PARTITION_NAME` above.
        await session.execute(
            text(
                f"CREATE TABLE IF NOT EXISTS {plan.name} PARTITION OF {PARENT_TABLE} "
                f"FOR VALUES FROM ('{_bound_literal(plan.start)}') "
                f"TO ('{_bound_literal(plan.end)}')"
            )
        )
        created.append(plan.name)
        logger.info("event_partition_created", partition=plan.name, start=plan.start.isoformat())

    return created


def _bound_literal(moment: datetime) -> str:
    """A partition bound as an unambiguous UTC timestamp literal.

    Explicit ``+00`` rather than relying on the server's ``TimeZone`` setting: a
    naive literal would be interpreted in the session's zone, so the same
    migration run from two machines could produce partitions whose boundaries
    are hours apart — and the overlap would only surface as an attach failure
    months later.
    """
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S+00")


async def _existing_partitions(session: AsyncSession) -> set[str]:
    rows = await session.execute(
        text(
            "SELECT c.relname FROM pg_class c "
            "JOIN pg_inherits i ON i.inhrelid = c.oid "
            # CAST(...) rather than `:parent::regclass`: SQLAlchemy's bind
            # parameter regex mis-parses a parameter immediately followed by the
            # `::` cast operator, and raises "doesn't define a bound parameter"
            # for a parameter that is plainly there.
            "WHERE i.inhparent = CAST(:parent AS regclass)"
        ).bindparams(parent=PARENT_TABLE)
    )
    return {str(row[0]) for row in rows}


async def default_partition_rows(
    session: AsyncSession, *, since: datetime | None = None, until: datetime | None = None
) -> int:
    """How many rows sit in the default partition, optionally within a range.

    Cross-tenant by construction — a partition spans every tenant — so the
    tenancy guard is opted out of explicitly rather than by omission.
    """
    clauses = []
    params: dict[str, datetime] = {}
    if since is not None:
        clauses.append("recorded_at >= :since")
        params["since"] = since
    if until is not None:
        clauses.append("recorded_at < :until")
        params["until"] = until
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""

    result = await session.execute(
        text(f"SELECT count(*) FROM ONLY {DEFAULT_PARTITION}{where}")
        .bindparams(**params)
        .execution_options(**{TENANT_SCOPE_EXEMPT: True})
    )
    return int(result.scalar_one())


async def detachable_partitions(session: AsyncSession, *, older_than: datetime) -> list[str]:
    """Partitions entirely older than the retention horizon (§22.4).

    Returns candidates; it does not detach. Retention on an append-only civic
    record is a decision with legal weight, so the automated half stops at
    identifying what is eligible — Phase 26 adds the approval and the proof of
    deletion that make acting on it defensible.
    """
    rows = await session.execute(
        text(
            "SELECT c.relname, pg_get_expr(c.relpartbound, c.oid) "
            "FROM pg_class c JOIN pg_inherits i ON i.inhrelid = c.oid "
            "WHERE i.inhparent = CAST(:parent AS regclass) AND c.relname <> :default_name "
            "ORDER BY c.relname"
        ).bindparams(parent=PARENT_TABLE, default_name=DEFAULT_PARTITION)
    )

    horizon = month_start(older_than)
    candidates: list[str] = []
    for name, _bound in rows:
        match = re.match(r"^events_(\d{4})_(\d{2})$", str(name))
        if match is None:  # pragma: no cover — names are generated here
            continue
        partition_start = datetime(int(match[1]), int(match[2]), 1, tzinfo=UTC)
        if next_month(partition_start) <= horizon:
            candidates.append(str(name))
    return candidates


def retention_horizon(now: datetime, *, retain_months: int) -> datetime:
    """The oldest month that must remain online.

    Counted in calendar months, not in days. ``timedelta(days=31 * n)`` drifts —
    it is short by up to three days a year — and a retention boundary that
    drifts eventually detaches a month the schedule still required.
    """
    horizon = month_start(now)
    for _ in range(retain_months):
        horizon = previous_month(horizon)
    return horizon


def previous_month(moment: datetime) -> datetime:
    if moment.month == 1:
        return moment.replace(year=moment.year - 1, month=12)
    return moment.replace(month=moment.month - 1)
