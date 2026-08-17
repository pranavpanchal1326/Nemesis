"""SLA deadline arithmetic — the piece most likely to be subtly wrong.

``resolve_deadline`` is a pure function over a ``WorkingWeek``, which is why
most of this file needs no database at all. That separation is the point: a
deadline that is wrong by a day is a contractor penalised for something outside
their control (§6.5), and the only way to be confident is to hammer the
arithmetic directly rather than through three layers of async plumbing.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from nemesis.control_plane import calendars
from nemesis.control_plane.calendars import (
    CalendarError,
    DayException,
    WorkingWeek,
    resolve_deadline,
)
from nemesis.control_plane.errors import ConflictError, ValidationError
from nemesis.control_plane.schemas import CalendarExceptionSpec, CalendarSpec, WorkingWindow
from nemesis.tenancy.context import tenant_scope

KOLKATA = "Asia/Kolkata"

#: Monday to Friday, 09:00-17:00. Eight working hours a day, forty a week.
OFFICE_HOURS = {day: ((time(9, 0), time(17, 0)),) for day in range(1, 6)}


def office_week(**overrides: object) -> WorkingWeek:
    base: dict[str, object] = {"timezone": KOLKATA, "windows": OFFICE_HOURS}
    return WorkingWeek(**(base | overrides))  # type: ignore[arg-type]


def kolkata(year: int, month: int, day: int, hour: int = 9, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(KOLKATA))


# ---------------------------------------------------------------------------
# The arithmetic
# ---------------------------------------------------------------------------


def test_a_continuous_calendar_returns_exactly_what_section_27_2_states() -> None:
    """§27.2 states plain durations, and a tenant with no calendar gets them.

    Not an approximation of them. This is the path most deployments take on day
    one, and quietly turning "72 hours" into "72 working hours against a week
    nobody configured" would make the published SLA table a lie.
    """
    week = WorkingWeek(timezone=KOLKATA, is_continuous=True)
    start = kolkata(2026, 3, 2, 14, 30)
    deadline = resolve_deadline(week=week, start=start, budget=timedelta(hours=72))

    assert deadline.due_at == (start + timedelta(hours=72)).astimezone(UTC)
    assert deadline.elapsed_hours == pytest.approx(72.0)
    assert deadline.adjustments == ()


def test_an_eight_hour_budget_lands_at_close_of_the_same_day() -> None:
    week = office_week()
    deadline = resolve_deadline(
        week=week, start=kolkata(2026, 3, 2, 9, 0), budget=timedelta(hours=8)
    )
    assert deadline.due_at.astimezone(ZoneInfo(KOLKATA)) == kolkata(2026, 3, 2, 17, 0)


def test_a_budget_started_after_hours_begins_at_the_next_opening() -> None:
    """Time outside the working week is not spent.

    A report filed at 22:00 does not consume five hours of its SLA before
    anybody could have looked at it — which is what plain elapsed arithmetic
    would charge, and what a department would rightly dispute.
    """
    deadline = resolve_deadline(
        week=office_week(), start=kolkata(2026, 3, 2, 22, 0), budget=timedelta(hours=1)
    )
    assert deadline.due_at.astimezone(ZoneInfo(KOLKATA)) == kolkata(2026, 3, 3, 10, 0)


def test_a_budget_spanning_a_weekend_skips_it() -> None:
    """Sixteen working hours from Friday 09:00 lands Monday 17:00, not Sunday."""
    deadline = resolve_deadline(
        week=office_week(), start=kolkata(2026, 3, 6, 9, 0), budget=timedelta(hours=16)
    )
    landed = deadline.due_at.astimezone(ZoneInfo(KOLKATA))
    assert landed == kolkata(2026, 3, 9, 17, 0)
    assert landed.isoweekday() == 1


def test_a_non_working_exception_stops_the_clock_entirely() -> None:
    """A holiday removes its whole day, not just the part the deadline lands in.

    This is the failure mode the module docstring names first: adding hours and
    then checking whether the *result* falls on a holiday removes at most one
    day and silently keeps the rest.
    """
    holiday = DayException(
        starts_on=date(2026, 3, 3),
        ends_on=date(2026, 3, 4),
        is_working=False,
        sla_multiplier=None,
        label="festival",
    )
    deadline = resolve_deadline(
        week=office_week(exceptions=(holiday,)),
        start=kolkata(2026, 3, 2, 9, 0),
        budget=timedelta(hours=16),
    )
    # 8 hours Monday, nothing Tuesday or Wednesday, 8 hours Thursday.
    assert deadline.due_at.astimezone(ZoneInfo(KOLKATA)) == kolkata(2026, 3, 5, 17, 0)


def test_a_seasonal_exception_stretches_the_budget_without_stopping_the_clock() -> None:
    """§13.4: monsoon road repair is harder, not impossible.

    A 1.6x multiplier means each working hour counts for 1/1.6 of an hour of
    budget, so an eight-hour budget consumes 12.8 working hours — a day and a
    half rather than a day.
    """
    monsoon = DayException(
        starts_on=date(2026, 6, 10),
        ends_on=date(2026, 9, 30),
        is_working=True,
        sla_multiplier=Decimal("1.600"),
        label="monsoon",
    )
    deadline = resolve_deadline(
        week=office_week(exceptions=(monsoon,)),
        start=kolkata(2026, 6, 15, 9, 0),
        budget=timedelta(hours=8),
    )
    landed = deadline.due_at.astimezone(ZoneInfo(KOLKATA))
    assert landed == kolkata(2026, 6, 16, 13, 48)
    assert deadline.adjustments == (("monsoon", Decimal("1.600")),)
    assert deadline.working_hours_consumed == pytest.approx(12.8)


def test_the_adjustment_is_reported_so_a_deadline_can_be_explained() -> None:
    """§6.1: prove, don't log.

    A contractor told a deadline moved must be able to see *why* it moved,
    without re-running the walk against a calendar that may since have changed.
    """
    monsoon = DayException(
        starts_on=date(2026, 6, 1),
        ends_on=date(2026, 9, 30),
        is_working=True,
        sla_multiplier=Decimal("2.000"),
        label="monsoon",
    )
    deadline = resolve_deadline(
        week=office_week(exceptions=(monsoon,)),
        start=kolkata(2026, 6, 15, 9, 0),
        budget=timedelta(hours=4),
    )
    assert dict(deadline.adjustments) == {"monsoon": Decimal("2.000")}


def test_a_naive_start_is_refused_rather_than_assumed() -> None:
    with pytest.raises(CalendarError, match="timezone-aware"):
        resolve_deadline(
            week=office_week(),
            start=datetime(2026, 3, 2, 9, 0),  # noqa: DTZ001 — the point of the test
            budget=timedelta(hours=1),
        )


def test_a_calendar_with_no_working_time_fails_rather_than_looping() -> None:
    """The bound exists because the alternative is a hung request.

    A calendar whose every day is non-working is a plausible data-entry
    outcome — one exception with the wrong end date does it — and an unbounded
    walk would hold a worker forever rather than reporting the misconfiguration.
    """
    week = WorkingWeek(timezone=KOLKATA, windows={})
    with pytest.raises(CalendarError, match="no deadline within"):
        resolve_deadline(week=week, start=kolkata(2026, 3, 2), budget=timedelta(hours=1))


def test_a_continuous_calendar_with_a_shutdown_loses_exactly_that_span() -> None:
    """Continuous does not mean unstoppable — a shutdown still removes days.

    Also the case that catches the end-of-day sentinel: a continuous day is
    walked as one window, and closing it at ``time.max`` rather than at midnight
    would leak a microsecond per day.
    """
    shutdown = DayException(
        starts_on=date(2026, 4, 6),
        ends_on=date(2026, 4, 8),
        is_working=False,
        sla_multiplier=None,
        label="annual shutdown",
    )
    week = WorkingWeek(timezone=KOLKATA, is_continuous=True, exceptions=(shutdown,))
    deadline = resolve_deadline(
        week=week, start=kolkata(2026, 4, 5, 0, 0), budget=timedelta(hours=48)
    )
    # 24 hours on the 5th, nothing 6th-8th, 24 hours on the 9th.
    assert deadline.due_at.astimezone(ZoneInfo(KOLKATA)) == kolkata(2026, 4, 10, 0, 0)


@hypothesis_settings(max_examples=60, deadline=None)
@given(
    hours=st.floats(min_value=0.25, max_value=200.0, allow_nan=False),
    offset_hours=st.integers(min_value=0, max_value=24 * 21),
)
def test_a_deadline_is_never_before_its_start_and_is_deterministic(
    hours: float, offset_hours: int
) -> None:
    """Two properties that must hold for every calendar and every start.

    Determinism matters as much as ordering: the same inputs must produce the
    same deadline on every run, or a disputed SLA cannot be re-argued.
    """
    week = office_week()
    start = kolkata(2026, 1, 5, 0, 0) + timedelta(hours=offset_hours)
    budget = timedelta(hours=hours)

    first = resolve_deadline(week=week, start=start, budget=budget)
    second = resolve_deadline(week=week, start=start, budget=budget)

    assert first.due_at >= start
    assert first.due_at == second.due_at


# ---------------------------------------------------------------------------
# Validation and persistence
# ---------------------------------------------------------------------------


def test_overlapping_exceptions_are_refused_at_write_time() -> None:
    """Overlap makes the deadline depend on row order.

    Refused rather than resolved by a precedence rule, because every proposed
    rule ("most specific wins", "non-working wins") is a guess about intent and
    the operator who wrote both spans is right there to be asked.
    """
    spec = CalendarSpec(
        code="c1",
        name="Overlapping",
        working_hours={"1": [WorkingWindow(start=time(9), end=time(17))]},
        exceptions=[
            CalendarExceptionSpec(
                starts_on=date(2026, 6, 1),
                ends_on=date(2026, 6, 30),
                label="monsoon",
                is_working=True,
                sla_multiplier=Decimal("1.5"),
            ),
            CalendarExceptionSpec(
                starts_on=date(2026, 6, 15), ends_on=date(2026, 7, 15), label="holiday"
            ),
        ],
    )
    with pytest.raises(ValidationError, match="overlap"):
        calendars._assert_exceptions_do_not_overlap(spec)


def test_overlapping_working_windows_in_one_day_are_refused() -> None:
    """Overlapping windows would charge the same minute of budget twice."""
    with pytest.raises(ValueError, match="overlap"):
        CalendarSpec(
            code="c1",
            name="Double-counted",
            working_hours={
                "1": [
                    WorkingWindow(start=time(9), end=time(13)),
                    WorkingWindow(start=time(12), end=time(17)),
                ]
            },
        )


def test_a_working_exception_without_a_multiplier_is_refused() -> None:
    """The combination changes nothing, so accepting it is silently inert."""
    with pytest.raises(ValueError, match="changes nothing"):
        CalendarExceptionSpec(
            starts_on=date(2026, 6, 1), ends_on=date(2026, 6, 2), label="x", is_working=True
        )


def test_a_calendar_with_neither_continuous_time_nor_hours_is_refused() -> None:
    with pytest.raises(ValueError, match="no working time at all"):
        CalendarSpec(code="c1", name="Empty")


async def test_a_second_default_calendar_demotes_the_first(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """One default per tenant, enforced by a partial unique index.

    The service demotes rather than failing, because "make this the default" is
    an unambiguous instruction — but the index is what makes it impossible for
    two concurrent requests to each see zero defaults and each set one.
    """
    maker = async_sessionmaker(migrated_engine, expire_on_commit=False)
    async with maker() as session:
        with tenant_scope(tenant_id):
            for code in ("first", "second"):
                await calendars.create_calendar(
                    session,
                    tenant_id=tenant_id,
                    spec=CalendarSpec(code=code, name=code, is_continuous=True, is_default=True),
                )
            await session.commit()
            rows = await calendars.list_calendars(session, tenant_id=tenant_id)

    defaults = [row.code for row in rows if row.is_default]
    assert defaults == ["second"]


async def test_an_unresolvable_timezone_is_refused(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A pattern match cannot tell ``Asia/Kolkata`` from ``Asia/Kolkatta``."""
    maker = async_sessionmaker(migrated_engine, expire_on_commit=False)
    async with maker() as session:
        with tenant_scope(tenant_id), pytest.raises(ValidationError, match="IANA"):
            await calendars.create_calendar(
                session,
                tenant_id=tenant_id,
                spec=CalendarSpec(
                    code="c1", name="Typo", timezone="Asia/Kolkatta", is_continuous=True
                ),
            )


async def test_a_tenant_with_no_default_calendar_gets_continuous_time(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Onboarding must not be blocked on a decision that has a correct default.

    §27.2's plain durations are the honest baseline, and refusing to compute a
    deadline because nobody has configured working hours would make a tenant
    unusable for a reason no citizen would understand.
    """
    maker = async_sessionmaker(migrated_engine, expire_on_commit=False)
    async with maker() as session:
        with tenant_scope(tenant_id):
            week = await calendars.load_working_week(session, tenant_id=tenant_id)

    assert week.is_continuous is True
    assert week.exceptions == ()


async def test_a_stored_calendar_round_trips_through_the_arithmetic(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The JSONB week the service writes is the week the walk reads back.

    Worth a database test rather than a unit test, because the round trip goes
    through JSONB — where a time becomes a string and an integer weekday
    becomes an object key — and a mismatch there produces a calendar with no
    working time, which fails as an unusable-calendar error rather than as a
    parse error.
    """
    maker = async_sessionmaker(migrated_engine, expire_on_commit=False)
    async with maker() as session:
        with tenant_scope(tenant_id):
            await calendars.create_calendar(
                session,
                tenant_id=tenant_id,
                spec=CalendarSpec(
                    code="office",
                    name="Office",
                    timezone=KOLKATA,
                    is_default=True,
                    working_hours={
                        str(day): [WorkingWindow(start=time(9), end=time(17))]
                        for day in range(1, 6)
                    },
                ),
            )
            await session.commit()
            week = await calendars.load_working_week(session, tenant_id=tenant_id)

    assert week.timezone == KOLKATA
    assert week.windows[1] == ((time(9), time(17)),)
    deadline = resolve_deadline(
        week=week, start=kolkata(2026, 3, 2, 9, 0), budget=timedelta(hours=8)
    )
    assert deadline.due_at.astimezone(ZoneInfo(KOLKATA)) == kolkata(2026, 3, 2, 17, 0)


async def test_a_duplicate_calendar_code_is_a_conflict(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    maker = async_sessionmaker(migrated_engine, expire_on_commit=False)
    async with maker() as session:
        with tenant_scope(tenant_id):
            spec = CalendarSpec(code="dup", name="Dup", is_continuous=True)
            await calendars.create_calendar(session, tenant_id=tenant_id, spec=spec)
            await session.commit()
            with pytest.raises(ConflictError):
                await calendars.create_calendar(session, tenant_id=tenant_id, spec=spec)


def test_a_malformed_stored_week_degrades_rather_than_raising() -> None:
    """JSONB can hold anything a migration or a psql session put there.

    One bad day key must not make every deadline for the tenant uncomputable.
    An unparseable day contributes no working time, which surfaces as a
    deadline that slipped rather than as a 500 on the triage queue.
    """
    parsed = calendars._windows_from_jsonb(
        {
            "1": [{"start": "09:00", "end": "17:00"}],
            "9": [{"start": "09:00", "end": "17:00"}],
            "monday": [{"start": "09:00", "end": "17:00"}],
            "2": [{"start": "not-a-time", "end": "17:00"}],
            "3": "not-a-list",
        }
    )
    assert set(parsed) == {1}
