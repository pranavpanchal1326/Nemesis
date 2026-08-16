"""The tenant registry — the one table whose primary key *is* the tenant.

Phase 5 owns the rich version of this model: plan entitlements, branding, the
custom taxonomy, the organisation hierarchy, business calendars. What lands here
is only what Phase 2 needs to make every other table's foreign key real, plus
the attributes that are structural rather than configurable — a locale set and a
timezone shape how data is *stored*, so they cannot wait for the control plane
without a migration later.

Deliberately absent: any column enumerating categories, roles, or wards. Those
are tenant-defined data (Phase 5), and putting a placeholder enum here would be
the exact hardcoding defect the program plan's critique log opens with.
"""

from __future__ import annotations

from sqlalchemy import Boolean, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from nemesis.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Tenant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tenants"

    #: Stable, human-readable handle used in operator tooling and support
    #: conversations. Unique, immutable by convention, and never the primary key
    #: — renaming a customer must not rewrite every foreign key in the database.
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)

    #: Free text, not an enum. Plan names are a commercial decision that changes
    #: without an engineering release; Phase 27 gives them entitlements.
    plan: Mapped[str] = mapped_column(String(64), nullable=False, server_default="pilot")

    #: BCP-47 tags. The set is data because §5 requires adding a language to be
    #: an import rather than a release, and the *primary* is separate because
    #: notification fallback and SLA reporting need one unambiguous default.
    primary_locale: Mapped[str] = mapped_column(String(35), nullable=False, server_default="en")
    locales: Mapped[list[str]] = mapped_column(
        ARRAY(String(35)), nullable=False, server_default="{en}"
    )

    #: IANA zone. SLA deadlines, business calendars, and the 72-hour dedup
    #: window are all computed against it, so a tenant in a different zone is a
    #: configuration change rather than a second deployment.
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, server_default="Asia/Kolkata")

    #: Where this tenant's data may live. Enforced in Phase 26; recorded from
    #: row zero because retrofitting residency means migrating data across a
    #: boundary it should never have crossed.
    data_residency: Mapped[str] = mapped_column(String(32), nullable=False, server_default="in")

    branding: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, server_default="{}")

    #: Suspension is reversible and auditable; deletion is neither. An offboarded
    #: tenant is deactivated here and erased through the Phase 26 procedure.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
