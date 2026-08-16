"""Departments, users, and contractors — §9.2, scoped and de-hardcoded.

Three deviations from the blueprint's conceptual schema, each closing a defect
the program plan's critique log names:

- **``departments`` gains a self-referencing parent.** §9.2 models a flat list
  with a ``ward`` string. A campus has faculties and buildings, an industrial
  park has estates and units, and a municipality has zones and wards — those do
  not share a shape, so the hierarchy is arbitrary and the ward becomes one
  level of it rather than a column every tenant must pretend to have.
- **``users.role`` stays free text and is not an enum.** Phase 13 replaces it
  with composed, scoped roles. A ``CHECK`` constraint listing six roles here
  would have to be dropped by a migration the moment the first customer wants a
  seventh, which is defect #1 in the critique log.
- **Everything carries ``tenant_id``**, including the contractor registry. Two
  municipalities can each register a contractor with the same registration
  number, and neither may see the other's rating.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from nemesis.db.base import Base, TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class Department(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "departments"
    __table_args__ = (
        # Scoped to the tenant, never global: two tenants may both have a
        # "Public Works" with code PW, and a global unique index would make the
        # second tenant's onboarding fail for a reason nobody could explain.
        UniqueConstraint("tenant_id", "code", name="uq_departments_tenant_id_code"),
    )

    name: Mapped[str] = mapped_column(Text, nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)

    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("departments.id", ondelete="RESTRICT", name="fk_departments_parent"),
        nullable=True,
        index=True,
    )

    #: The §9.2 ward, kept as an optional denormalised label because routing and
    #: the §23.2 equity flag both group by it. It is a *label on* the hierarchy,
    #: not the hierarchy itself.
    ward: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)


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

    #: Arrays rather than join tables, matching §9.2. Phase 17's entity
    #: resolution does `pg_trgm` fuzzy matching over address, phone, and these
    #: names to detect the same beneficial owner behind two registrations, and
    #: it wants them adjacent to the row it is scoring.
    director_names: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    #: Taxonomy node keys, not a fixed category list — Phase 5 defines what the
    #: strings mean per tenant, so certifying a contractor for a new defect type
    #: is data entry rather than a migration.
    categories_certified: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    active_since: Mapped[date | None] = mapped_column(Date, nullable=True)

    #: Derived from the event log by Phase 17, never manually entered, and never
    #: collapsed to a single star rating (§16.1). JSONB because the *shape* of a
    #: reputation is a product decision that will change more than once.
    computed_rating: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )


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
