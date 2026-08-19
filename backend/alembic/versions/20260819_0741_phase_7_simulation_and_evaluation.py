"""Phase 7 — configuration simulation, backtesting & the activation guardrail.

Revision ID: 42cf64e372e6
Revises: faf19d8bbddc
Create Date: 2026-08-19 07:41:52.362872

Five tables, all additive, nothing altered and nothing dropped — so this file is
what autogenerate produced, for the second migration running in this directory.
That is the whole point of the shape Phase 6 chose: a governed structure is a
row in one table, and the machinery that evaluates one is a set of tables beside
it rather than a change to it.

**The load-bearing constraint is on ``evaluation_sets``, and it is easy to read
past.** ``uq_evaluation_sets_one_published_per_kind`` is *partial*, on
``status = 'published'``. It is what makes "is this kind gated" a single-valued
question — ``policy.service.activate`` runs exactly that query on every
activation, and two published sets would make the answer depend on which row the
planner returned first. The same reasoning, and the same mechanism, as
``uq_policy_versions_one_active_per_kind``.

The three CHECK constraints beside it close the ways a guardrail can be on and
useless: a published set with no labels gates every activation on an exam with
no questions (which every candidate passes), and a published set with no
``labels_hash`` cannot have its certificates invalidated when its contents
change. ``ck_policy_certificates_a_pass_evaluated_something`` closes the third:
zero of zero is a ratio of 1.0 under most implementations, so a certificate that
marked nothing would otherwise be a pass.

**What this migration deliberately does not do: enable the guardrail anywhere.**
No tenant gets an evaluation set. Publishing one is an act of tenant
configuration — it needs labelled complaints, which need a human who has read
them — and a migration that published an empty set for every tenant would turn
the guardrail on and simultaneously make it vacuous. Until a tenant publishes a
set, ``activate`` behaves exactly as it did in Phase 6, which is the correct
default for a control that is only meaningful once somebody has done the work
behind it.

**``evaluation_labels`` cascades from its set, and it is the only cascade in this
schema.** A label has no meaning apart from the exam it belongs to — it is not
evidence about a complaint, it is one row of one test — and the service refuses
to delete a published set, so the cascade can only ever reach a draft. Every
other foreign key here is RESTRICT, including a certificate's link to its set: a
certificate whose exam had been deleted would be a row attesting to nothing.

**Neither ``evaluation_labels.complaint_id`` nor
``shadow_observations.complaint_id`` is a foreign key**, deliberately. Both name
complaints whose event partitions §22.4 retention may archive, and a set that
stopped being loadable because retention ran would be a guardrail with an expiry
date nobody chose. The evaluation counts unresolvable labels and reports them
separately from failures instead.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "42cf64e372e6"
down_revision: str | None = "faf19d8bbddc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evaluation_sets",
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="draft", nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("label_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("labels_hash", sa.String(length=64), nullable=True),
        sa.Column("pass_ratio", sa.Float(), server_default="1.0", nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by", sa.UUID(), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
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
            name=op.f("ck_evaluation_sets_kind_is_known"),
        ),
        sa.CheckConstraint(
            "status <> 'published' OR label_count > 0",
            name=op.f("ck_evaluation_sets_published_sets_have_labels"),
        ),
        sa.CheckConstraint(
            "status <> 'published' OR labels_hash IS NOT NULL",
            name=op.f("ck_evaluation_sets_published_sets_are_hashed"),
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'retired')",
            name=op.f("ck_evaluation_sets_status_is_known"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_evaluation_sets_tenant", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_sets")),
        sa.UniqueConstraint("tenant_id", "code", name="uq_evaluation_sets_tenant_id_code"),
    )
    op.create_index(
        op.f("ix_evaluation_sets_tenant_id"), "evaluation_sets", ["tenant_id"], unique=False
    )
    op.create_index(
        "uq_evaluation_sets_one_published_per_kind",
        "evaluation_sets",
        ["tenant_id", "kind"],
        unique=True,
        postgresql_where=sa.text("status = 'published'"),
    )
    op.create_table(
        "shadow_observations",
        sa.Column("complaint_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("candidate_revision", sa.BigInteger(), nullable=True),
        sa.Column("candidate_content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "live_stamps",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("live_digest", sa.String(length=64), nullable=False),
        sa.Column("candidate_digest", sa.String(length=64), nullable=False),
        sa.Column("diverged", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("difference", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
        sa.CheckConstraint(
            "kind IN ('severity_rubric', 'dedup_thresholds', 'safety_ruleset', 'sla_matrix', 'routing_rules', 'rate_card')",
            name=op.f("ck_shadow_observations_kind_is_known"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_shadow_observations_tenant", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shadow_observations")),
        sa.UniqueConstraint(
            "tenant_id",
            "complaint_id",
            "candidate_content_hash",
            name="uq_shadow_observations_complaint_candidate",
        ),
    )
    op.create_index(
        "ix_shadow_observations_divergent",
        "shadow_observations",
        ["tenant_id", "candidate_content_hash"],
        unique=False,
        postgresql_where=sa.text("diverged"),
    )
    op.create_index(
        op.f("ix_shadow_observations_tenant_id"), "shadow_observations", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_shadow_observations_tenant_id_kind_created",
        "shadow_observations",
        ["tenant_id", "kind", "created_at"],
        unique=False,
    )
    op.create_table(
        "simulation_runs",
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=32), server_default="backtest", nullable=False),
        sa.Column("status", sa.String(length=32), server_default="running", nullable=False),
        sa.Column("candidate_revision", sa.BigInteger(), nullable=True),
        sa.Column("candidate_content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "baseline_stamps",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("case_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("population", sa.Integer(), server_default="0", nullable=False),
        sa.Column("sampling_stride", sa.Integer(), server_default="1", nullable=False),
        sa.Column("affected", sa.Integer(), server_default="0", nullable=False),
        sa.Column("report", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
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
            name=op.f("ck_simulation_runs_kind_is_known"),
        ),
        sa.CheckConstraint(
            "mode IN ('backtest', 'evaluation')", name=op.f("ck_simulation_runs_mode_is_known")
        ),
        sa.CheckConstraint(
            "status <> 'completed' OR report IS NOT NULL",
            name=op.f("ck_simulation_runs_completed_runs_have_a_report"),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name=op.f("ck_simulation_runs_status_is_known"),
        ),
        sa.CheckConstraint(
            "case_count >= 0", name=op.f("ck_simulation_runs_case_count_is_not_negative")
        ),
        sa.CheckConstraint(
            "sampling_stride >= 1", name=op.f("ck_simulation_runs_stride_starts_at_one")
        ),
        sa.CheckConstraint(
            "window_end > window_start", name=op.f("ck_simulation_runs_window_is_ordered")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_simulation_runs_tenant", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_simulation_runs")),
    )
    op.create_index(
        op.f("ix_simulation_runs_tenant_id"), "simulation_runs", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_simulation_runs_tenant_id_candidate_hash",
        "simulation_runs",
        ["tenant_id", "candidate_content_hash"],
        unique=False,
    )
    op.create_index(
        "ix_simulation_runs_tenant_id_kind_created",
        "simulation_runs",
        ["tenant_id", "kind", "created_at"],
        unique=False,
    )
    op.create_table(
        "evaluation_labels",
        sa.Column("evaluation_set_id", sa.UUID(), nullable=False),
        sa.Column("complaint_id", sa.UUID(), nullable=False),
        sa.Column("expected_severity_tier", sa.String(length=64), nullable=True),
        sa.Column("expected_severity_min", sa.Float(), nullable=True),
        sa.Column("expected_severity_max", sa.Float(), nullable=True),
        sa.Column("expected_safety_fired", sa.Boolean(), nullable=True),
        sa.Column("expected_department_code", sa.String(length=64), nullable=True),
        sa.Column("expected_dedup_outcome", sa.String(length=32), nullable=True),
        sa.Column("rationale", sa.Text(), server_default="", nullable=False),
        sa.Column("labelled_by", sa.UUID(), nullable=True),
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
            "expected_severity_min IS NULL OR expected_severity_max IS NULL OR expected_severity_max >= expected_severity_min",
            name=op.f("ck_evaluation_labels_severity_bounds_are_ordered"),
        ),
        sa.CheckConstraint(
            "expected_severity_tier IS NOT NULL OR expected_severity_min IS NOT NULL OR expected_severity_max IS NOT NULL OR expected_safety_fired IS NOT NULL OR expected_department_code IS NOT NULL OR expected_dedup_outcome IS NOT NULL",
            name=op.f("ck_evaluation_labels_a_label_must_assert_something"),
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_set_id"],
            ["evaluation_sets.id"],
            name="fk_evaluation_labels_set",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_evaluation_labels_tenant", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_labels")),
        sa.UniqueConstraint(
            "tenant_id",
            "evaluation_set_id",
            "complaint_id",
            name="uq_evaluation_labels_set_complaint",
        ),
    )
    op.create_index(
        op.f("ix_evaluation_labels_tenant_id"), "evaluation_labels", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_evaluation_labels_tenant_id_set",
        "evaluation_labels",
        ["tenant_id", "evaluation_set_id"],
        unique=False,
    )
    op.create_table(
        "policy_certificates",
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("candidate_content_hash", sa.String(length=64), nullable=False),
        sa.Column("candidate_revision", sa.BigInteger(), nullable=True),
        sa.Column("evaluation_set_id", sa.UUID(), nullable=False),
        sa.Column("labels_hash", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=True),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column("labels_evaluated", sa.Integer(), server_default="0", nullable=False),
        sa.Column("labels_passed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("labels_unresolvable", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "findings", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("issued_by", sa.UUID(), nullable=True),
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
        sa.CheckConstraint(
            "kind IN ('severity_rubric', 'dedup_thresholds', 'safety_ruleset', 'sla_matrix', 'routing_rules', 'rate_card')",
            name=op.f("ck_policy_certificates_kind_is_known"),
        ),
        sa.CheckConstraint(
            "verdict <> 'pass' OR labels_evaluated > 0",
            name=op.f("ck_policy_certificates_a_pass_evaluated_something"),
        ),
        sa.CheckConstraint(
            "verdict IN ('pass', 'fail')", name=op.f("ck_policy_certificates_verdict_is_known")
        ),
        sa.CheckConstraint(
            "labels_evaluated >= 0", name=op.f("ck_policy_certificates_evaluated_is_not_negative")
        ),
        sa.CheckConstraint(
            "labels_passed <= labels_evaluated",
            name=op.f("ck_policy_certificates_passed_within_evaluated"),
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_set_id"],
            ["evaluation_sets.id"],
            name="fk_policy_certificates_set",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["simulation_runs.id"],
            name="fk_policy_certificates_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_policy_certificates_tenant", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_policy_certificates")),
    )
    op.create_index(
        "ix_policy_certificates_lookup",
        "policy_certificates",
        ["tenant_id", "kind", "candidate_content_hash", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_policy_certificates_tenant_id"), "policy_certificates", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_policy_certificates_tenant_id_set",
        "policy_certificates",
        ["tenant_id", "evaluation_set_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_policy_certificates_tenant_id_set", table_name="policy_certificates")
    op.drop_index(op.f("ix_policy_certificates_tenant_id"), table_name="policy_certificates")
    op.drop_index("ix_policy_certificates_lookup", table_name="policy_certificates")
    op.drop_table("policy_certificates")
    op.drop_index("ix_evaluation_labels_tenant_id_set", table_name="evaluation_labels")
    op.drop_index(op.f("ix_evaluation_labels_tenant_id"), table_name="evaluation_labels")
    op.drop_table("evaluation_labels")
    op.drop_index("ix_simulation_runs_tenant_id_kind_created", table_name="simulation_runs")
    op.drop_index("ix_simulation_runs_tenant_id_candidate_hash", table_name="simulation_runs")
    op.drop_index(op.f("ix_simulation_runs_tenant_id"), table_name="simulation_runs")
    op.drop_table("simulation_runs")
    op.drop_index("ix_shadow_observations_tenant_id_kind_created", table_name="shadow_observations")
    op.drop_index(op.f("ix_shadow_observations_tenant_id"), table_name="shadow_observations")
    op.drop_index(
        "ix_shadow_observations_divergent",
        table_name="shadow_observations",
        postgresql_where=sa.text("diverged"),
    )
    op.drop_table("shadow_observations")
    op.drop_index(
        "uq_evaluation_sets_one_published_per_kind",
        table_name="evaluation_sets",
        postgresql_where=sa.text("status = 'published'"),
    )
    op.drop_index(op.f("ix_evaluation_sets_tenant_id"), table_name="evaluation_sets")
    op.drop_table("evaluation_sets")
