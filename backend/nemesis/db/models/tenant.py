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

from sqlalchemy import Boolean, Integer, String, Text
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

    #: Which seeded template this tenant was provisioned from, and at what
    #: version. Recorded because the library is the thing that drifts: a campus
    #: onboarded in March and one onboarded in September have different defaults,
    #: and without this the difference is invisible to support.
    provisioned_from_template: Mapped[str | None] = mapped_column(String(64), nullable=True)
    template_version: Mapped[str | None] = mapped_column(String(32), nullable=True)

    #: Monotonic counter over taxonomy mutations, bumped in the same transaction
    #: as the change and stamped into ``taxonomy_published``. A counter rather
    #: than a timestamp because two edits in the same millisecond must still be
    #: orderable, and because "revision 7" is what an operator quotes.
    taxonomy_revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    #: Suspension is reversible and auditable; deletion is neither. An offboarded
    #: tenant is deactivated here and erased through the Phase 26 procedure.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    #: Whether §16.3's unauthenticated public API serves this tenant's
    #: aggregates. **Default false, and the default is the decision.**
    #:
    #: The endpoints are no-auth by design — that is what makes the platform
    #: infrastructure other tools can build on rather than a closed app. But
    #: "the code is capable of publishing this" and "this customer agreed to
    #: publish it" are different statements, and defaulting to true would make
    #: the first one silently mean the second for every tenant already
    #: provisioned. A municipality that has not decided its disclosure posture
    #: is not a municipality that has decided yes.
    public_api_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    #: Suppression floor for every published aggregate. A "ward summary"
    #: computed over two complaints is not an aggregate — it is one citizen's
    #: report with a category and a location attached, which §26.4 forbids on
    #: this surface. Per-tenant because the right floor depends on population:
    #: five is defensible for a city ward and far too low for a building with
    #: nine occupants.
    public_api_min_aggregate: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="5"
    )
