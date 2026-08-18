"""Phase 6 — policy & rules engine.

Revision ID: faf19d8bbddc
Revises: a41c9e5b7d02
Create Date: 2026-08-18 09:34:49.465146

One table. The DDL is exactly what autogenerate produced, which is unusual in
this directory and worth saying: ``policy_versions`` introduces no geography
column, adds no NOT NULL column to a populated table, and drops nothing — the
three things that have made every previous migration here a hand-written file.

**Two constraints in it are load-bearing and easy to mistake for boilerplate.**

``uq_policy_versions_one_active_per_kind`` is *partial*, on ``status = 'active'``.
It is the database half of the Phase 6 gate clause "an unapproved draft can
never influence a production decision": the resolver reads only ``active``, and
this is what additionally makes ``active`` mean *exactly one*. A service-level
check would hold right up until two operators pressed Activate in the same
second, which is precisely when it matters.

``ck_policy_versions_live_versions_were_approved`` refuses any row that reached
``active`` or ``superseded`` without an ``approved_at``. "Who signed off on the
rubric that scored this complaint" has to be answerable from the row itself, and
a service-only check leaves a psql session able to activate anonymously.

**What this migration deliberately does not do: seed baseline documents for
tenants that already exist.**

The tempting version inserts a baseline rubric, dedup band, safety ruleset, and
SLA matrix for every row in ``tenants``. It was rejected for a reason that is
structural rather than stylistic: every policy write is required to append a
``policy_drafted`` / ``policy_transitioned`` pair to the tenant's hash chain, and
a migration cannot do that correctly. Appending needs the chain-tail lock, the
previous hash, the canonical payload encoding, and the schema registry — all of
which live in ``events.store``, and reimplementing any of it in raw SQL here
would produce rows that look like chain entries and fail ``verify_chain``.

So existing tenants are covered two ways instead, and both are honest:

1. ``policy.resolver`` falls back to ``policy.baselines`` — the *same* objects
   provisioning seeds — so nothing stops working the moment this lands. Those
   decisions are stamped ``baseline`` rather than with a revision number, which
   is deliberately not a plausible-looking version: a complaint scored before
   its tenant had a rubric must stay identifiable as such forever.
2. ``POST /api/v1/control-plane/policies/seed-baselines`` runs the real service
   against a tenant, writing real documents and real chain events. It is
   idempotent, so running it across a fleet twice is safe.

The gap between the two is *measurable* rather than assumed: every baseline
resolution logs ``policy_baseline_used`` with the tenant and kind, so "which
tenants have not been seeded" is a log query rather than an audit.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "faf19d8bbddc"
down_revision: str | None = "a41c9e5b7d02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "policy_versions",
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="draft", nullable=False),
        sa.Column(
            "body", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("based_on_id", sa.UUID(), nullable=True),
        sa.Column("rolled_back_from_id", sa.UUID(), nullable=True),
        sa.Column("change_reason", sa.Text(), server_default="", nullable=False),
        sa.Column("change_summary", sa.Text(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("reviewed_by", sa.UUID(), nullable=True),
        sa.Column("approved_by", sa.UUID(), nullable=True),
        sa.Column("activated_by", sa.UUID(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version", sa.BigInteger(), server_default="0", nullable=False),
        sa.CheckConstraint(
            "kind IN ('severity_rubric', 'dedup_thresholds', 'safety_ruleset', 'sla_matrix', 'routing_rules', 'rate_card')",
            name=op.f("ck_policy_versions_kind_is_known"),
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'in_review', 'approved', 'active', 'superseded', 'archived')",
            name=op.f("ck_policy_versions_status_is_known"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('active', 'superseded') OR approved_at IS NOT NULL",
            name=op.f("ck_policy_versions_live_versions_were_approved"),
        ),
        sa.CheckConstraint(
            "effective_until IS NULL OR effective_until > effective_from",
            name=op.f("ck_policy_versions_effective_window_is_ordered"),
        ),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_policy_versions_revision_starts_at_one")),
        sa.ForeignKeyConstraint(
            ["based_on_id"],
            ["policy_versions.id"],
            name="fk_policy_versions_based_on",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["rolled_back_from_id"],
            ["policy_versions.id"],
            name="fk_policy_versions_rolled_back_from",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_policy_versions_tenant", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_policy_versions")),
        sa.UniqueConstraint(
            "tenant_id", "kind", "revision", name="uq_policy_versions_tenant_id_kind_revision"
        ),
    )
    op.create_index(
        op.f("ix_policy_versions_tenant_id"), "policy_versions", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_policy_versions_tenant_id_content_hash",
        "policy_versions",
        ["tenant_id", "content_hash"],
        unique=False,
    )
    op.create_index(
        "ix_policy_versions_tenant_id_kind_status",
        "policy_versions",
        ["tenant_id", "kind", "status"],
        unique=False,
    )
    op.create_index(
        "uq_policy_versions_one_active_per_kind",
        "policy_versions",
        ["tenant_id", "kind"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_policy_versions_one_active_per_kind",
        table_name="policy_versions",
        postgresql_where=sa.text("status = 'active'"),
    )
    op.drop_index("ix_policy_versions_tenant_id_kind_status", table_name="policy_versions")
    op.drop_index("ix_policy_versions_tenant_id_content_hash", table_name="policy_versions")
    op.drop_index(op.f("ix_policy_versions_tenant_id"), table_name="policy_versions")
    op.drop_table("policy_versions")
