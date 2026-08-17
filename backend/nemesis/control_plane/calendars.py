"""Business calendars, and the deadline arithmetic §27.2 depends on.

The arithmetic is separated from the database on purpose. ``resolve_deadline``
is a pure function over a plain description of a working week — no session, no
ORM, no clock — because it is the piece most likely to be subtly wrong and the
piece hardest to test through a service. Property-based tests can hammer it with
generated calendars and generated start times; they could not do that through an
async session.

**The three ways this gets written wrong**, each of which the implementation
below refuses:

1. *Adding hours to a timestamp and then checking whether the result lands in a
   holiday.* That is not the same computation — a 72-hour budget spanning two
   holidays needs both removed, and checking only the landing point removes
   neither.
2. *Doing the arithmetic in UTC.* A working day is defined by the clock on the
   wall. 09:00 in ``Asia/Kolkata`` is a different UTC instant in a jurisdiction
   with daylight saving, and the deadline has to track the wall clock, not the
   offset it had when the work order was created.
3. *Treating a seasonal adjustment as non-working time.* §13.4's monsoon is
   harder, not impossible. Stopping the clock would say no work happens for
   three months, which is false and makes every monsoon deadline absurd.

**Termination.** The walk is bounded by ``MAX_DEADLINE_HORIZON_DAYS``. A
calendar whose every day is non-working — a plausible data-entry outcome — would
otherwise loop forever inside a request. Exceeding the horizon raises rather
than returning a guess, because a deadline nobody can justify is worse than an
error somebody has to fix.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from itertools import pairwise
from typing import Any, Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.control_plane.errors import ConflictError, NotFoundError, ValidationError
from nemesis.control_plane.schemas import CalendarSpec
from nemesis.db.models.calendar import ISO_MONDAY, ISO_SUNDAY, BusinessCalendar, CalendarException
from nemesis.db.models.tenant import Tenant

#: How far ahead the walk will look before declaring the calendar unusable.
#: Three years: long enough that a genuine 21-day low-severity SLA (§27.2)
#: crossing a badly-configured year still resolves, short enough that a calendar
#: with no working time at all fails in milliseconds rather than hanging.
MAX_DEADLINE_HORIZON_DAYS: Final = 1095

#: A window ending here means "to the end of the day". ``time.max`` is
#: 23:59:59.999999, so closing a window literally at it would drop a microsecond
#: of working time per day — invisible in a test and a full second lost over a
#: three-year horizon. The walk translates this sentinel into midnight of the
#: following day instead.
_END_OF_DAY: Final = time.max


class CalendarError(ValidationError):
    """The calendar cannot produce a deadline."""


@dataclass(frozen=True, slots=True)
class DayException:
    """A resolved override for a span of dates."""

    starts_on: date
    ends_on: date
    is_working: bool
    sla_multiplier: Decimal | None
    label: str

    def covers(self, day: date) -> bool:
        return self.starts_on <= day <= self.ends_on


@dataclass(frozen=True, slots=True)
class WorkingWeek:
    """A calendar reduced to what the arithmetic needs.

    A plain value object, constructible in a test in three lines. The service
    builds one from the database; nothing else about deadlines touches the
    database at all.
    """

    timezone: str
    #: ISO weekday (1-7) → ordered, non-overlapping ``(start, end)`` windows.
    windows: dict[int, tuple[tuple[time, time], ...]] = field(default_factory=dict)
    is_continuous: bool = False
    exceptions: tuple[DayException, ...] = ()

    def zone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise CalendarError(
                f"{self.timezone!r} is not a resolvable IANA timezone; every deadline "
                f"computed against this calendar would be in the wrong zone"
            ) from exc

    def exception_for(self, day: date) -> DayException | None:
        """The first exception covering ``day``, in declaration order.

        First rather than "most specific": overlapping exceptions are a data
        problem the service refuses at write time, so by the time the arithmetic
        runs there is at most one — and picking deterministically is what keeps
        the same input producing the same deadline.
        """
        for exception in self.exceptions:
            if exception.covers(day):
                return exception
        return None


@dataclass(frozen=True, slots=True)
class Deadline:
    """A computed due time, with the reasoning that produced it.

    §6.1: prove, don't log. A department told "this was due on Tuesday" can ask
    why, and the answer has to be reconstructible without re-running the walk
    against a calendar that may since have changed.
    """

    due_at: datetime
    #: Wall-clock hours actually consumed, including non-working time skipped.
    elapsed_hours: float
    #: Working hours the budget was spent over, after any seasonal multiplier.
    working_hours_consumed: float
    #: Seasonal spans (§13.4) that stretched the budget, with their multipliers.
    adjustments: tuple[tuple[str, Decimal], ...] = ()


def resolve_deadline(*, week: WorkingWeek, start: datetime, budget: timedelta) -> Deadline:
    """When ``budget`` of working time, starting at ``start``, runs out.

    ``start`` must be timezone-aware — a naive datetime here is the same class of
    bug as a naive ``occurred_at`` in the event store, and it is refused for the
    same reason: there is no correct default, only a silently wrong one.
    """
    if start.tzinfo is None:
        raise CalendarError("start must be timezone-aware; a wall clock needs a zone")
    if budget <= timedelta(0):
        raise CalendarError("an SLA budget must be positive")

    zone = week.zone()
    local_start = start.astimezone(zone)

    if week.is_continuous and not week.exceptions:
        # The common, and correct, fast path: §27.2's table states plain
        # durations, and a tenant that has not configured a calendar should get
        # exactly what the table says rather than an approximation of it.
        due = local_start + budget
        hours = budget.total_seconds() / 3600.0
        return Deadline(
            due_at=due.astimezone(UTC), elapsed_hours=hours, working_hours_consumed=hours
        )

    remaining = budget
    adjustments: dict[str, Decimal] = {}
    consumed = timedelta(0)
    cursor = local_start
    day = local_start.date()
    horizon = day + timedelta(days=MAX_DEADLINE_HORIZON_DAYS)

    while day <= horizon:
        for window_start, window_end in _windows_for(week, day):
            # Both ends built in the same zone as the cursor, then compared as
            # instants. Comparing naive local times would break across a DST
            # transition, on the one day a year nobody tests.
            opens = datetime.combine(day, window_start, tzinfo=zone)
            closes = (
                datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
                if window_end == _END_OF_DAY
                else datetime.combine(day, window_end, tzinfo=zone)
            )
            if closes <= cursor:
                continue
            if cursor < opens:
                cursor = opens

            multiplier = _multiplier_for(week, day)
            if multiplier is not None:
                exception = week.exception_for(day)
                if exception is not None:
                    adjustments[exception.label] = multiplier

            available = closes - cursor
            # A seasonal multiplier stretches the *budget*, which is the same
            # thing as making each working minute count for less. Applied to the
            # spend rather than to the total so a budget crossing into and out of
            # a monsoon window is charged correctly on each side, instead of
            # being multiplied wholesale by whichever season it started in.
            effective = available if multiplier is None else available / float(multiplier)

            if effective >= remaining:
                spent = remaining if multiplier is None else remaining * float(multiplier)
                due = cursor + spent
                consumed += spent
                return Deadline(
                    due_at=due.astimezone(UTC),
                    elapsed_hours=(due - local_start).total_seconds() / 3600.0,
                    working_hours_consumed=consumed.total_seconds() / 3600.0,
                    adjustments=tuple(sorted(adjustments.items())),
                )

            remaining -= effective
            consumed += available
            cursor = closes

        day += timedelta(days=1)
        cursor = max(cursor, datetime.combine(day, time.min, tzinfo=zone))

    raise CalendarError(
        f"no deadline within {MAX_DEADLINE_HORIZON_DAYS} days for a budget of {budget}. "
        f"The calendar declares too little working time to ever spend it — check the "
        f"working hours and any non-working exception spanning the whole period."
    )


def _windows_for(week: WorkingWeek, day: date) -> Sequence[tuple[time, time]]:
    """The working windows on one date, after exceptions."""
    exception = week.exception_for(day)
    if exception is not None and not exception.is_working:
        return ()
    if week.is_continuous:
        # A continuous calendar with exceptions still has to be walked day by
        # day, because a non-working exception has to be able to interrupt it.
        return ((time.min, _END_OF_DAY),)
    return week.windows.get(day.isoweekday(), ())


def _multiplier_for(week: WorkingWeek, day: date) -> Decimal | None:
    exception = week.exception_for(day)
    if exception is None or not exception.is_working:
        return None
    return exception.sla_multiplier


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


async def create_calendar(
    session: AsyncSession, *, tenant_id: uuid.UUID, spec: CalendarSpec
) -> BusinessCalendar:
    """Create a calendar and its exceptions, defaulting the zone from the tenant."""
    existing = await get_calendar(session, tenant_id=tenant_id, code=spec.code)
    if existing is not None:
        raise ConflictError(f"calendar code {spec.code!r} already exists for this tenant")

    timezone = spec.timezone or await _tenant_timezone(session, tenant_id=tenant_id)
    _assert_resolvable(timezone)
    _assert_exceptions_do_not_overlap(spec)

    if spec.is_default:
        await _clear_default(session, tenant_id=tenant_id)

    calendar = BusinessCalendar(
        tenant_id=tenant_id,
        code=spec.code,
        name=spec.name,
        timezone=timezone,
        working_hours={
            day: [
                {"start": window.start.isoformat(), "end": window.end.isoformat()}
                for window in sorted(windows, key=lambda w: w.start)
            ]
            for day, windows in spec.working_hours.items()
        },
        is_continuous=spec.is_continuous,
        is_default=spec.is_default,
    )
    session.add(calendar)
    await session.flush()

    for exception in spec.exceptions:
        session.add(
            CalendarException(
                tenant_id=tenant_id,
                calendar_id=calendar.id,
                starts_on=exception.starts_on,
                ends_on=exception.ends_on,
                is_working=exception.is_working,
                sla_multiplier=exception.sla_multiplier,
                label=exception.label,
                source=exception.source,
            )
        )
    await session.flush()
    return calendar


async def get_calendar(
    session: AsyncSession, *, tenant_id: uuid.UUID, code: str
) -> BusinessCalendar | None:
    row = await session.execute(
        select(BusinessCalendar).where(
            BusinessCalendar.tenant_id == tenant_id, BusinessCalendar.code == code
        )
    )
    return row.scalar_one_or_none()


async def list_calendars(session: AsyncSession, *, tenant_id: uuid.UUID) -> list[BusinessCalendar]:
    rows = await session.execute(
        select(BusinessCalendar)
        .where(BusinessCalendar.tenant_id == tenant_id)
        .order_by(BusinessCalendar.code)
    )
    return list(rows.scalars().all())


async def load_working_week(
    session: AsyncSession, *, tenant_id: uuid.UUID, calendar_id: uuid.UUID | None = None
) -> WorkingWeek:
    """Build the value object the arithmetic consumes.

    ``calendar_id=None`` resolves the tenant default. A tenant with no default
    calendar at all gets a *continuous* week rather than an error: §27.2's
    durations are the honest baseline, and refusing to compute a deadline
    because nobody has configured working hours would block onboarding on a
    decision that has a correct default.
    """
    statement = select(BusinessCalendar).where(BusinessCalendar.tenant_id == tenant_id)
    statement = (
        statement.where(BusinessCalendar.id == calendar_id)
        if calendar_id is not None
        else statement.where(BusinessCalendar.is_default.is_(True))
    )
    calendar = (await session.execute(statement)).scalar_one_or_none()

    if calendar is None:
        if calendar_id is not None:
            raise NotFoundError("no such calendar for this tenant")
        return WorkingWeek(
            timezone=await _tenant_timezone(session, tenant_id=tenant_id), is_continuous=True
        )

    exception_rows = await session.execute(
        select(CalendarException)
        .where(
            CalendarException.tenant_id == tenant_id,
            CalendarException.calendar_id == calendar.id,
        )
        .order_by(CalendarException.starts_on)
    )

    return WorkingWeek(
        timezone=calendar.timezone,
        windows=_windows_from_jsonb(calendar.working_hours),
        is_continuous=calendar.is_continuous,
        exceptions=tuple(
            DayException(
                starts_on=row.starts_on,
                ends_on=row.ends_on,
                is_working=row.is_working,
                sla_multiplier=row.sla_multiplier,
                label=row.label,
            )
            for row in exception_rows.scalars().all()
        ),
    )


def _windows_from_jsonb(raw: dict[str, Any]) -> dict[int, tuple[tuple[time, time], ...]]:
    """Parse the stored week back into typed windows.

    Tolerant of a malformed day key rather than raising, and that is the one
    place in this module where tolerance is right: the column is JSONB, a
    migration or a psql session can put anything in it, and a single bad key
    must not make every deadline in the tenant uncomputable. An unparseable day
    contributes no working time, which is visible as a deadline that slipped
    rather than as a 500 on the triage queue.
    """
    parsed: dict[int, tuple[tuple[time, time], ...]] = {}
    for day, windows in raw.items():
        try:
            iso_day = int(day)
        except (TypeError, ValueError):
            continue
        if not ISO_MONDAY <= iso_day <= ISO_SUNDAY or not isinstance(windows, list):
            continue
        entries: list[tuple[time, time]] = []
        for window in windows:
            if not isinstance(window, dict):
                continue
            start_raw, end_raw = window.get("start"), window.get("end")
            if not isinstance(start_raw, str) or not isinstance(end_raw, str):
                continue
            try:
                entries.append((time.fromisoformat(start_raw), time.fromisoformat(end_raw)))
            except ValueError:
                continue
        if entries:
            parsed[iso_day] = tuple(sorted(entries))
    return parsed


def _assert_resolvable(timezone: str) -> None:
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValidationError(
            f"{timezone!r} is not an IANA timezone. A pattern match cannot tell "
            f"'Asia/Kolkata' from 'Asia/Kolkatta', and the second silently produces "
            f"deadlines in the wrong zone."
        ) from exc


def _assert_exceptions_do_not_overlap(spec: CalendarSpec) -> None:
    """Overlapping spans make the deadline depend on row order.

    Refused at write time rather than resolved at read time by a precedence
    rule, because every precedence rule anyone proposes ("most specific wins",
    "non-working wins") is a guess about intent, and the operator who wrote both
    spans is right there to be asked.
    """
    ordered = sorted(spec.exceptions, key=lambda exception: exception.starts_on)
    for earlier, later in pairwise(ordered):
        if later.starts_on <= earlier.ends_on:
            raise ValidationError(
                f"calendar exceptions {earlier.label!r} and {later.label!r} overlap "
                f"({earlier.starts_on}-{earlier.ends_on} and "
                f"{later.starts_on}-{later.ends_on}). Which one applies would depend "
                f"on row order, so the deadline would not be reproducible."
            )


async def _clear_default(session: AsyncSession, *, tenant_id: uuid.UUID) -> None:
    """Demote the current default so the partial unique index accepts the new one."""
    await session.execute(
        update(BusinessCalendar)
        .where(BusinessCalendar.tenant_id == tenant_id, BusinessCalendar.is_default.is_(True))
        .values(is_default=False, version=BusinessCalendar.version + 1)
    )
    await session.flush()


async def _tenant_timezone(session: AsyncSession, *, tenant_id: uuid.UUID) -> str:
    # tenant-scope-exempt: `tenants` is the tenant registry; its primary key IS
    # the tenant. See tenancy.registry.
    zone = (
        await session.execute(select(Tenant.timezone).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    if zone is None:
        raise NotFoundError("tenant does not exist")
    return str(zone)
