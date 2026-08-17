"""Business calendars — the data behind every SLA deadline.

§27.2 states work-order SLAs in wall-clock durations ("72 hours"), and §13.4
requires performance to be normalised against conditions outside a contractor's
control. Both collapse into one question the system has to answer honestly: *when
is this due?* A 72-hour deadline issued at 18:00 on the Friday before a
three-day festival is not a 72-hour deadline in any sense a department would
recognise, and penalising a contractor for it is the kind of unfairness §6.5
requires the product to design out rather than apologise for.

So working time is tenant data. A calendar declares the week; exceptions
override named spans of it. Two kinds of exception, and the distinction is the
whole point:

**Non-working spans** remove time from the budget. A public holiday, a strike, a
site shutdown. The clock stops.

**Seasonal spans** keep the clock running and stretch the budget by a stated
multiplier. §13.4's example is monsoon road repair, which is genuinely harder
rather than impossible — treating it as non-working would say nothing happens
during the monsoon, which is false and would make every monsoon deadline absurd.

Both are recorded with their reason, because a deadline a department cannot
explain to a citizen is a deadline that erodes exactly the trust §3.1 describes
being lost.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from nemesis.db.base import (
    Base,
    OptimisticVersionMixin,
    TenantScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)

#: ISO-8601 weekday numbering: Monday is 1, Sunday is 7. Stated because Python's
#: ``date.weekday()`` is 0-based and ``isoweekday()`` is 1-based, and a system
#: that mixes the two produces deadlines that are wrong by exactly one day in a
#: way nobody notices until a contractor disputes one.
ISO_MONDAY = 1
ISO_SUNDAY = 7

#: Upper bound on the seasonal stretch. A multiplier is a fairness adjustment,
#: not an escape hatch: at 10x a 72-hour SLA becomes a month, which is not
#: normalisation, it is the SLA being switched off by data entry.
MAX_SLA_MULTIPLIER = Decimal("10.000")


class BusinessCalendar(
    UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, OptimisticVersionMixin, Base
):
    """A working week, plus the timezone it is a working week *in*."""

    __tablename__ = "business_calendars"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_business_calendars_tenant_id_code"),
        # One default per tenant, enforced by the database rather than by the
        # service. A second default is not a validation error the service can
        # reliably prevent — two concurrent onboarding requests each see zero
        # defaults and each set one — and the failure is silent: deadline
        # computation picks whichever row sorts first.
        Index(
            "uq_business_calendars_tenant_id_default",
            "tenant_id",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )

    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)

    #: IANA zone. Defaulted from the tenant at creation but stored per calendar,
    #: because a municipality with a control room on a different shift pattern
    #: from its field crews is one tenant with two working weeks.
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)

    #: ``{"1": [{"start": "09:00", "end": "17:00"}], ...}`` keyed by ISO weekday.
    #: A missing key is a non-working day; a day may carry more than one window,
    #: because a shift split around a two-hour break is the normal case in the
    #: field, not an exotic one.
    working_hours: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )

    #: Whether time runs continuously. A control room, an emergency crew, or any
    #: tenant that simply does not want calendar arithmetic sets this and gets
    #: plain elapsed time — which is what §27.2's table literally says, and the
    #: right default for a deployment that has not configured a calendar yet.
    is_continuous: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")


class CalendarException(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """A named span that overrides the calendar's ordinary week."""

    __tablename__ = "calendar_exceptions"
    __table_args__ = (
        Index(
            "ix_calendar_exceptions_tenant_id_calendar_id_starts_on",
            "tenant_id",
            "calendar_id",
            "starts_on",
        ),
        CheckConstraint("ends_on >= starts_on", name="span_is_ordered"),
        # A non-working span with a multiplier, or a seasonal span without one,
        # are both meaningless combinations that would silently do nothing. The
        # database refuses them rather than letting deadline arithmetic quietly
        # ignore a row somebody believed they had configured.
        CheckConstraint(
            "(is_working AND sla_multiplier IS NOT NULL) "
            "OR (NOT is_working AND sla_multiplier IS NULL)",
            name="multiplier_belongs_to_working_spans",
        ),
        CheckConstraint(
            f"sla_multiplier IS NULL OR (sla_multiplier > 0 AND sla_multiplier <= "
            f"{MAX_SLA_MULTIPLIER})",
            name="multiplier_within_bound",
        ),
    )

    calendar_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "business_calendars.id", ondelete="CASCADE", name="fk_calendar_exceptions_calendar"
        ),
        nullable=False,
    )

    #: Inclusive on both ends. A monsoon window is months and a holiday is one
    #: day, and expressing the holiday as a half-open range ending the next
    #: morning is how off-by-one deadline bugs are written.
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    ends_on: Mapped[date] = mapped_column(Date, nullable=False)

    #: ``False`` stops the clock; ``True`` keeps it running and applies
    #: ``sla_multiplier``. See the module docstring on why these are one table
    #: with a flag rather than two tables — they answer the same question at the
    #: same point in the arithmetic, and splitting them means every caller has
    #: to remember to consult both.
    is_working: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    #: §13.4 context normalisation. NUMERIC, not FLOAT: this multiplies a
    #: duration that a contractor may later dispute, and a deadline that differs
    #: by a floating-point epsilon between two computations is a deadline nobody
    #: can defend.
    sla_multiplier: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), nullable=True)

    #: Free text, tenant-defined: 'holiday', 'monsoon', 'shutdown', whatever the
    #: customer calls it. Reported to citizens and contractors, so it is a label
    #: rather than a code.
    label: Mapped[str] = mapped_column(Text, nullable=False)

    #: Why this span exists — an IMD advisory reference, a gazette notification,
    #: a site notice. §6.1: prove, don't log. A fairness adjustment with no
    #: stated source is indistinguishable from a favour.
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
