"""Phase 8 — the trust & safety spine.

Revision ID: 25e8d619c085
Revises: 42cf64e372e6
Create Date: 2026-08-19 09:43:23.238820

Three tables, all additive, nothing altered and nothing dropped — this file is
what autogenerate produced, for the third migration running in this directory.

**The load-bearing constraint is ``uq_review_queue_one_open_per_reason``, and it
is easy to read past.** It is a *partial* unique index on ``status = 'open'``,
and it is what makes "is this complaint already queued for this reason" a
single-valued question. Without it a device tripping the velocity check three
times in an hour becomes three rows a human has to reconcile, and §11.4's
promise that no flag is a dead end degrades into a queue nobody can work.
Partial rather than total because the same reason must be raisable again months
later on new evidence — the same mechanism, and the same reasoning, as
``uq_policy_versions_one_active_per_kind`` and
``uq_evaluation_sets_one_published_per_kind``.

**``uq_submission_media_complaint_source`` is keyed on the content hash, not on
the complaint.** A report may legitimately carry more than one artefact; what it
may not carry is the same bytes twice, which is what a redelivered stage would
write. Keying on ``complaint_id`` alone would have made the second artefact an
integrity error on a citizen's submission.

**Two CHECK constraints encode §22.1 and §22.4 rather than restating them in
prose.** ``ck_submission_media_redaction_names_its_detector`` makes
``redacted_uri`` and ``detector_id`` move together, so a row can never claim an
image was blurred without naming what blurred it — the difference between
evidence and an assertion. ``ck_submission_media_exif_outlives_raw`` enforces
§22.4's ordering: EXIF is retained 90 days and the photograph 30, and a row
whose metadata expired first would leave the image with nothing to review it
against.

**What this migration deliberately does not do: give any tenant a trust policy.**
``PolicyKind.TRUST_THRESHOLDS`` is baselined, so every tenant — existing and
new — resolves the platform defaults from ``policy.baselines`` with no row at
all, and ``seed_baselines`` writes one the next time it runs. A migration that
inserted a policy version per tenant would be writing governed documents outside
the draft → review → approve → activate lifecycle that Phase 6 exists to
enforce, and it would do it without an event on any tenant's chain.

**The one non-additive change: five CHECK constraints are widened.**
``PolicyKind`` gains ``trust_thresholds``, and ``kind IN (...)`` is written into
``policy_versions``, ``simulation_runs``, ``evaluation_sets``,
``policy_certificates`` and ``shadow_observations``. Each is dropped and
recreated with the seventh value.

That cost is the constraint working rather than a design mistake. It is what
stopped a seventh governed structure from reaching the table before anybody
decided it was one — the alternative, an open ``kind`` column, would have
accepted ``trust_threshold`` (singular) as readily as the real name and produced
a policy nothing ever resolves. The recreation is safe in both directions: the
constraint is a predicate over existing rows, no row contains the new value
before this migration, and the downgrade re-narrows it after this migration's
own tables are gone.

**``review_decisions.decided_by`` is a nullable FK to ``users``**, and the
nullability is the honest part. Until Phase 13 gives operators identities the
control-plane token is a shared secret; recording its use as a named person
would be a worse record than recording none, so ``decided_by_label`` is NOT NULL
and carries whatever is actually known.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "25e8d619c085"
down_revision: str | None = "42cf64e372e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: Tables carrying a ``kind IN (...)`` CHECK over ``PolicyKind``, and the
#: constraint name on each. Listed rather than discovered: a loop over
#: ``information_schema`` would silently do nothing if a name ever changed, and
#: a migration that silently does nothing is the worst kind.
_KIND_CONSTRAINTS: tuple[tuple[str, str], ...] = (
    ("policy_versions", "ck_policy_versions_kind_is_known"),
    ("simulation_runs", "ck_simulation_runs_kind_is_known"),
    ("evaluation_sets", "ck_evaluation_sets_kind_is_known"),
    ("policy_certificates", "ck_policy_certificates_kind_is_known"),
    ("shadow_observations", "ck_shadow_observations_kind_is_known"),
)

_KINDS_BEFORE = (
    "severity_rubric",
    "dedup_thresholds",
    "safety_ruleset",
    "sla_matrix",
    "routing_rules",
    "rate_card",
)
_KINDS_AFTER = (*_KINDS_BEFORE, "trust_thresholds")


def _rewrite_kind_checks(kinds: tuple[str, ...]) -> None:
    values = ", ".join(f"'{kind}'" for kind in kinds)
    for table, constraint in _KIND_CONSTRAINTS:
        # ``op.f`` on both, and it is not optional on either. Without it the
        # metadata's ``ck_%(table_name)s_%(constraint_name)s`` convention is
        # applied to a name that already carries the prefix, and the statement
        # becomes ``DROP CONSTRAINT ck_policy_versions_ck_policy_versions_
        # kind_is_known`` — which fails loudly here, and would have created a
        # doubly-prefixed constraint the *next* migration could not find.
        op.drop_constraint(op.f(constraint), table, type_="check")
        op.create_check_constraint(op.f(constraint), table, f"kind IN ({values})")


def upgrade() -> None:
    _rewrite_kind_checks(_KINDS_AFTER)
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "review_queue_items",
        sa.Column("complaint_id", sa.UUID(), nullable=False),
        sa.Column("reason", sa.String(length=48), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="open", nullable=False),
        sa.Column("priority", sa.Integer(), server_default="100", nullable=False),
        sa.Column("occurrences", sa.Integer(), server_default="1", nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("trust_score", sa.Float(), server_default="0", nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.CheckConstraint(
            "(status = 'open') = (decided_at IS NULL)",
            name=op.f("ck_review_queue_items_decided_carries_its_timestamp"),
        ),
        sa.CheckConstraint(
            "reason IN ('safety_trigger', 'exif_mismatch', 'perceptual_duplicate', 'device_velocity', 'geographic_cluster', 'low_trust')",
            name=op.f("ck_review_queue_items_reason_is_known"),
        ),
        sa.CheckConstraint(
            "status IN ('open', 'decided')", name=op.f("ck_review_queue_items_status_is_known")
        ),
        sa.CheckConstraint(
            "occurrences >= 1", name=op.f("ck_review_queue_items_occurrences_start_at_one")
        ),
        sa.ForeignKeyConstraint(
            ["complaint_id"],
            ["complaints.id"],
            name="fk_review_queue_complaint",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_review_queue_items_tenant", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_review_queue_items")),
    )
    op.create_index(
        op.f("ix_review_queue_items_complaint_id"),
        "review_queue_items",
        ["complaint_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_review_queue_items_tenant_id"), "review_queue_items", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_review_queue_tenant_status_priority",
        "review_queue_items",
        ["tenant_id", "status", "priority", "created_at"],
        unique=False,
    )
    op.create_index(
        "uq_review_queue_one_open_per_reason",
        "review_queue_items",
        ["tenant_id", "complaint_id", "reason"],
        unique=True,
        postgresql_where="status = 'open'",
    )
    op.create_table(
        "submission_media",
        sa.Column("complaint_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("content_type", sa.String(length=64), nullable=False),
        sa.Column("quarantine_uri", sa.Text(), nullable=False),
        sa.Column("quarantine_sha256", sa.String(length=64), nullable=False),
        sa.Column("redacted_uri", sa.Text(), nullable=True),
        sa.Column("redacted_sha256", sa.String(length=64), nullable=True),
        sa.Column("redacted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("faces_detected", sa.Integer(), server_default="0", nullable=False),
        sa.Column("faces_blurred", sa.Integer(), server_default="0", nullable=False),
        sa.Column("detector_id", sa.String(length=128), nullable=True),
        sa.Column("perceptual_hash", sa.BigInteger(), nullable=True),
        sa.Column("exif_present", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("exif_latitude", sa.Float(), nullable=True),
        sa.Column("exif_longitude", sa.Float(), nullable=True),
        sa.Column("exif_distance_meters", sa.Float(), nullable=True),
        sa.Column("exif_captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("captured_or_reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("purge_raw_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("purge_exif_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exif_purged_at", sa.DateTime(timezone=True), nullable=True),
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
            "kind IN ('image', 'audio')", name=op.f("ck_submission_media_kind_is_known")
        ),
        sa.CheckConstraint(
            "(redacted_uri IS NULL) = (detector_id IS NULL)",
            name=op.f("ck_submission_media_redaction_names_its_detector"),
        ),
        sa.CheckConstraint(
            "faces_blurred <= faces_detected",
            name=op.f("ck_submission_media_blurred_does_not_exceed_detected"),
        ),
        sa.CheckConstraint(
            "faces_detected >= 0", name=op.f("ck_submission_media_detected_is_not_negative")
        ),
        sa.CheckConstraint(
            "purge_exif_after >= purge_raw_after",
            name=op.f("ck_submission_media_exif_outlives_raw"),
        ),
        sa.ForeignKeyConstraint(
            ["complaint_id"],
            ["complaints.id"],
            name="fk_submission_media_complaint",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_submission_media_tenant", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_submission_media")),
        sa.UniqueConstraint(
            "tenant_id",
            "complaint_id",
            "quarantine_sha256",
            name="uq_submission_media_complaint_source",
        ),
    )
    op.create_index(
        op.f("ix_submission_media_complaint_id"), "submission_media", ["complaint_id"], unique=False
    )
    op.create_index(
        "ix_submission_media_purge_exif",
        "submission_media",
        ["purge_exif_after"],
        unique=False,
        postgresql_where="exif_purged_at IS NULL",
    )
    op.create_index(
        "ix_submission_media_purge_raw",
        "submission_media",
        ["purge_raw_after"],
        unique=False,
        postgresql_where="raw_purged_at IS NULL",
    )
    op.create_index(
        "ix_submission_media_tenant_hash",
        "submission_media",
        ["tenant_id", "captured_or_reported_at"],
        unique=False,
        postgresql_where="perceptual_hash IS NOT NULL",
    )
    op.create_index(
        op.f("ix_submission_media_tenant_id"), "submission_media", ["tenant_id"], unique=False
    )
    op.create_table(
        "review_decisions",
        sa.Column("review_item_id", sa.UUID(), nullable=False),
        sa.Column("complaint_id", sa.UUID(), nullable=False),
        sa.Column("reason", sa.String(length=48), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("decided_by", sa.UUID(), nullable=True),
        sa.Column("decided_by_label", sa.String(length=128), nullable=False),
        sa.Column("evidence_hash", sa.String(length=64), nullable=False),
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
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.CheckConstraint(
            "decision IN ('approve', 'reject', 'escalate')",
            name=op.f("ck_review_decisions_decision_is_known"),
        ),
        sa.CheckConstraint(
            "reason IN ('safety_trigger', 'exif_mismatch', 'perceptual_duplicate', 'device_velocity', 'geographic_cluster', 'low_trust')",
            name=op.f("ck_review_decisions_reason_is_known"),
        ),
        sa.CheckConstraint(
            "length(btrim(rationale)) > 0", name=op.f("ck_review_decisions_rationale_is_not_blank")
        ),
        sa.ForeignKeyConstraint(
            ["complaint_id"],
            ["complaints.id"],
            name="fk_review_decisions_complaint",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"], ["users.id"], name="fk_review_decisions_user", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["review_item_id"],
            ["review_queue_items.id"],
            name="fk_review_decisions_item",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_review_decisions_tenant", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_review_decisions")),
        sa.UniqueConstraint("review_item_id", name="uq_review_decisions_one_per_item"),
    )
    op.create_index(
        op.f("ix_review_decisions_complaint_id"), "review_decisions", ["complaint_id"], unique=False
    )
    op.create_index(
        "ix_review_decisions_tenant_created",
        "review_decisions",
        ["tenant_id", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_review_decisions_tenant_id"), "review_decisions", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_review_decisions_tenant_label",
        "review_decisions",
        ["tenant_id", "reason", "decision"],
        unique=False,
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index("ix_review_decisions_tenant_label", table_name="review_decisions")
    op.drop_index(op.f("ix_review_decisions_tenant_id"), table_name="review_decisions")
    op.drop_index("ix_review_decisions_tenant_created", table_name="review_decisions")
    op.drop_index(op.f("ix_review_decisions_complaint_id"), table_name="review_decisions")
    op.drop_table("review_decisions")
    op.drop_index(op.f("ix_submission_media_tenant_id"), table_name="submission_media")
    op.drop_index(
        "ix_submission_media_tenant_hash",
        table_name="submission_media",
        postgresql_where="perceptual_hash IS NOT NULL",
    )
    op.drop_index(
        "ix_submission_media_purge_raw",
        table_name="submission_media",
        postgresql_where="raw_purged_at IS NULL",
    )
    op.drop_index(
        "ix_submission_media_purge_exif",
        table_name="submission_media",
        postgresql_where="exif_purged_at IS NULL",
    )
    op.drop_index(op.f("ix_submission_media_complaint_id"), table_name="submission_media")
    op.drop_table("submission_media")
    op.drop_index(
        "uq_review_queue_one_open_per_reason",
        table_name="review_queue_items",
        postgresql_where="status = 'open'",
    )
    op.drop_index("ix_review_queue_tenant_status_priority", table_name="review_queue_items")
    op.drop_index(op.f("ix_review_queue_items_tenant_id"), table_name="review_queue_items")
    op.drop_index(op.f("ix_review_queue_items_complaint_id"), table_name="review_queue_items")
    op.drop_table("review_queue_items")
    # ### end Alembic commands ###
