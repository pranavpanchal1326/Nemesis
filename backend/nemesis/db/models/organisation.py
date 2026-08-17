"""Departments, zones, shifts, users, and contractors — §9.2, de-hardcoded.

**Two hierarchies, and the distinction is load-bearing.** ``departments`` is a
tree of *responsibility* — who does the work. ``zones`` is a tree of *place* —
where the work is. It is tempting to collapse them into one "org unit" table,
and it reads tidier, but Phase 6's routing rule is
``(category x place) → responsible unit``: a rule that cannot name both sides
cannot be written. A ward is not a department that happens to be geographic, and
a department is not a place — a Public Works division covers eleven wards, and
each of those wards is worked by four different divisions.

Both trees are *arbitrary*. §9.2 models a flat department list with a ``ward``
string, which ships one shape. A campus has faculties, buildings, and floors; an
industrial park has estates and units; a municipality has zones and wards. So
each carries a tenant-defined ``kind``, a self-referencing parent, and a
materialised path, and none of those words appear as an enum anywhere in this
file.

Three further deviations, each closing a defect the program plan names:

- **``users.role`` stays free text and is not an enum.** Phase 13 replaces it
  with composed, scoped roles. A ``CHECK`` constraint listing six roles here
  would have to be dropped by a migration the moment the first customer wants a
  seventh, which is critique-log defect #1.
- **Contractor certification is a table, not an array.** §9.2's
  ``categories_certified TEXT[]`` cannot carry a validity window or a
  certificate reference, and cannot be joined against the taxonomy — so a
  certification that lapsed last March is indistinguishable from a current one,
  and §15.3's contractor ranking would rank on it anyway.
- **Everything carries ``tenant_id``**, including the contractor registry. Two
  municipalities can each register a contractor with the same registration
  number, and neither may see the other's rating.
"""

from __future__ import annotations

import uuid
from datetime import date, time

from geoalchemy2 import Geography
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from nemesis.db.base import (
    Base,
    OptimisticVersionMixin,
    TenantScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)

#: Shared with ``db.models.taxonomy``'s tree for the same reasons: a bounded
#: ancestor walk and a bounded path column. Declared separately rather than
#: imported because the two trees are free to diverge — an organisation chart is
#: plausibly deeper than a defect taxonomy — and a shared constant would make
#: changing one a change to both.
MAX_ORG_DEPTH = 10
ORG_PATH_MAX_LENGTH = MAX_ORG_DEPTH * 65

#: ISO weekday numbering, matching ``db.models.calendar``. Monday is 1.
ISO_WEEKDAY_MIN = 1
ISO_WEEKDAY_MAX = 7

#: "every element of weekdays is a legal ISO weekday", as a SQL containment
#: test. Built from the constants rather than written out, so the bound and the
#: constraint cannot disagree.
_ISO_WEEKDAY_SUBSET = "weekdays <@ ARRAY[{}]".format(
    ",".join(str(day) for day in range(ISO_WEEKDAY_MIN, ISO_WEEKDAY_MAX + 1))
)


class Department(
    UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, OptimisticVersionMixin, Base
):
    """A unit of responsibility: a department, a division, a team, a crew.

    The table keeps its §9.2 name even though ``kind`` now makes it general.
    Renaming it would rename ``work_orders.department_id``, and that column's
    name is mirrored in the ``work_order_created`` payload — so the rename costs
    an event version bump plus an upcaster, for no behavioural change. That cost
    belongs to Phase 14, which owns work-order workflow and will be touching the
    payload anyway. Paying it here would be churn wearing a tidiness argument.
    """

    __tablename__ = "departments"
    __table_args__ = (
        # Scoped to the tenant, never global: two tenants may both have a
        # "Public Works" with code PW, and a global unique index would make the
        # second tenant's onboarding fail for a reason nobody could explain.
        UniqueConstraint("tenant_id", "code", name="uq_departments_tenant_id_code"),
        Index("ix_departments_tenant_id_path", "tenant_id", "path"),
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="not_its_own_parent"),
        CheckConstraint(f"depth >= 0 AND depth < {MAX_ORG_DEPTH}", name="depth_within_bound"),
    )

    name: Mapped[str] = mapped_column(Text, nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)

    #: What this unit *is*, in the tenant's own vocabulary. Free text, because
    #: "faculty", "estate", "beat", and "circle" are all real answers and none of
    #: them is knowable at build time.
    kind: Mapped[str] = mapped_column(String(64), nullable=False, server_default="department")

    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("departments.id", ondelete="RESTRICT", name="fk_departments_parent"),
        nullable=True,
        index=True,
    )

    #: Derived from ``parent_id`` by ``control_plane.organisation``. Never
    #: written by a caller, and rebuilt from scratch by the service's own test.
    path: Mapped[str] = mapped_column(String(ORG_PATH_MAX_LENGTH), nullable=False)
    depth: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    #: Whether a work order may be routed here. An interior grouping usually
    #: cannot do the work — "Infrastructure" is an org chart node, and assigning
    #: a pothole to it produces a work order with no crew.
    is_assignable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    #: Which working week this unit's SLA deadlines are computed against.
    #: Nullable: an unset calendar falls back to the tenant default, which is
    #: what makes onboarding possible before anyone has thought about shifts.
    calendar_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("business_calendars.id", ondelete="RESTRICT", name="fk_departments_calendar"),
        nullable=True,
    )

    #: The §9.2 ward, retained as an optional denormalised label so Phase 2 and
    #: Phase 3 code keeps working unchanged. ``zones`` supersedes it: a label
    #: cannot carry a boundary, a parent, or a second level. Phase 12's routing
    #: reads the zone tree, and this column is not extended further.
    ward: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    attributes: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )


class Zone(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, OptimisticVersionMixin, Base):
    """A unit of place: a ward, a site, a building, an estate, a campus block."""

    __tablename__ = "zones"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_zones_tenant_id_code"),
        Index("ix_zones_tenant_id_path", "tenant_id", "path"),
        # Declared here with ``spatial_index=False`` on the column, because
        # GeoAlchemy2 otherwise emits its own GiST index alongside this one —
        # two identical indexes, doubling write cost, both invisible in the
        # model. Phase 2 defect #7, in a new place.
        Index("ix_zones_boundary", "boundary", postgresql_using="gist"),
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="not_its_own_parent"),
        CheckConstraint(f"depth >= 0 AND depth < {MAX_ORG_DEPTH}", name="depth_within_bound"),
    )

    name: Mapped[str] = mapped_column(Text, nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False, server_default="zone")

    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("zones.id", ondelete="RESTRICT", name="fk_zones_parent"),
        nullable=True,
        index=True,
    )
    path: Mapped[str] = mapped_column(String(ORG_PATH_MAX_LENGTH), nullable=False)
    depth: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    #: Nullable, and that is the common case at onboarding: a tenant knows its
    #: ward names long before anyone has a shapefile. §23.2's underreporting
    #: flag needs the geometry and degrades to per-code grouping without it,
    #: which is a smaller claim honestly made rather than a fabricated boundary.
    boundary: Mapped[object | None] = mapped_column(
        Geography(geometry_type="MULTIPOLYGON", srid=4326, spatial_index=False), nullable=True
    )
    #: Not derived from ``boundary`` on read. ``ST_Centroid`` over a ward polygon
    #: on every routing decision is a cost paid per complaint for a value that
    #: changes when a boundary is redrawn, which is approximately never.
    centroid: Mapped[object | None] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False), nullable=True
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    attributes: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )


class Shift(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """When a unit's people are actually on duty.

    Distinct from the business calendar, and both are needed. A calendar answers
    "is the SLA clock running"; a shift answers "is there anyone to hand this
    to right now" — the question §15.2 assignment and §11.2's fifteen-minute
    danger response ask. A tenant whose calendar says 09:00-17:00 may still run
    a night crew for emergencies, and collapsing the two would make that crew
    either invisible or permanently late.
    """

    __tablename__ = "shifts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "department_id", "code", name="uq_shifts_tenant_id_department_id_code"
        ),
        Index("ix_shifts_tenant_id_department_id", "tenant_id", "department_id"),
        CheckConstraint("cardinality(weekdays) > 0", name="shift_covers_a_day"),
        CheckConstraint(_ISO_WEEKDAY_SUBSET, name="weekdays_are_iso"),
        CheckConstraint(
            "effective_to IS NULL OR effective_from IS NULL OR effective_to >= effective_from",
            name="effective_span_is_ordered",
        ),
    )

    department_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("departments.id", ondelete="CASCADE", name="fk_shifts_department"),
        nullable=False,
    )

    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)

    #: ISO weekday numbers this shift runs on. An array rather than seven
    #: booleans or a bitmask: it reads correctly in psql during an incident,
    #: which is the only time anybody looks at this table by hand.
    weekdays: Mapped[list[int]] = mapped_column(ARRAY(Integer), nullable=False)

    #: Local to the department's calendar timezone, not UTC. A shift is defined
    #: by the clock on the wall where the crew is; storing 09:00 as UTC would
    #: make a daylight-saving transition silently move everyone's start time.
    starts_at: Mapped[time] = mapped_column(Time, nullable=False)
    ends_at: Mapped[time] = mapped_column(Time, nullable=False)

    #: ``ends_at < starts_at`` means the shift crosses midnight, which is normal
    #: for a night crew and is why there is no CHECK constraint ordering them.
    #: The service documents the interpretation; the schema does not forbid it.
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")


class Contractor(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "contractors"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "registration_id", name="uq_contractors_tenant_id_registration_id"
        ),
    )

    name: Mapped[str] = mapped_column(Text, nullable=False)
    registration_id: Mapped[str] = mapped_column(String(128), nullable=False)
    registered_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)

    #: An array rather than a join table, matching §9.2. Phase 17's entity
    #: resolution does `pg_trgm` fuzzy matching over address, phone, and these
    #: names to detect the same beneficial owner behind two registrations, and
    #: it wants them adjacent to the row it is scoring. Certifications moved to
    #: their own table for the opposite reason — see the module docstring.
    director_names: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    active_since: Mapped[date | None] = mapped_column(Date, nullable=True)

    #: Derived from the event log by Phase 17, never manually entered, and never
    #: collapsed to a single star rating (§16.1). JSONB because the *shape* of a
    #: reputation is a product decision that will change more than once.
    computed_rating: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )


class ContractorCertification(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """What a contractor is certified to work on, and until when.

    §15.3 ranks contractors for assignment and §17 audits those rankings for
    favouritism. Both need a certification that can *lapse* — a scope with no
    validity window means an audit cannot distinguish "was certified when
    assigned" from "is certified now", and those are different questions with
    different answers about whether an assignment was proper.
    """

    __tablename__ = "contractor_certifications"
    __table_args__ = (
        # Named for what it means rather than by the convention's
        # column-concatenation rule: that name is 69 characters and Postgres
        # truncates identifiers at 63, which SQLAlchemy refuses outright. A
        # silently truncated constraint name is one a later migration cannot
        # reference.
        UniqueConstraint(
            "tenant_id",
            "contractor_id",
            "taxonomy_node_id",
            name="uq_contractor_certifications_scope",
        ),
        Index(
            "ix_contractor_certifications_tenant_id_taxonomy_node_id",
            "tenant_id",
            "taxonomy_node_id",
        ),
        CheckConstraint(
            "valid_until IS NULL OR valid_from IS NULL OR valid_until >= valid_from",
            name="validity_span_is_ordered",
        ),
    )

    contractor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "contractors.id", ondelete="CASCADE", name="fk_contractor_certifications_contractor"
        ),
        nullable=False,
    )
    #: RESTRICT: a taxonomy node somebody is certified against cannot be deleted
    #: out from under the certification, because §17's audit has to resolve what
    #: the contractor was allowed to do at the time of an assignment.
    taxonomy_node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "taxonomy_nodes.id", ondelete="RESTRICT", name="fk_contractor_certifications_node"
        ),
        nullable=False,
    )

    #: The certificate this scope rests on — a licence number, a registration
    #: reference. §6.1 again: a scope with no evidence behind it is an assertion.
    certificate_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)


class User(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tenant_id", "subject", name="uq_users_tenant_id_subject"),)

    #: The identity-provider subject claim. Phase 13 brings OIDC; storing the
    #: external subject rather than an email means a customer changing SSO
    #: providers is a re-mapping, and means no password ever lands in this table.
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Free text by design — see the module docstring. Phase 13 supersedes it.
    role: Mapped[str] = mapped_column(String(64), nullable=False)

    department_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("departments.id", ondelete="RESTRICT", name="fk_users_department"),
        nullable=True,
        index=True,
    )
    contractor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("contractors.id", ondelete="RESTRICT", name="fk_users_contractor"),
        nullable=True,
        index=True,
    )
