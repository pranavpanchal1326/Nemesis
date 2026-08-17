"""phase 3 outbox and pipeline dead letters

Two new tables, two nullable columns on ``complaints``, and one seeded row. Every
change is additive and nothing existing is altered or rewritten, so this is safe
to apply against a database already carrying history — which matters more than
usual here, because the table it sits next to is append-only and cannot be
rebuilt.

The two columns are nullable with no backfill on purpose. They are projected
state, so the correct value for every existing row is produced by replaying the
log — `rebuild_tenant()` — not by a `DEFAULT` chosen in a migration, which would
be a migration inventing current state the log does not explain.

``outbox_messages`` is written inside the same transaction as the event it
points at, so a rolled-back pipeline stage takes its realtime notification down
with it. ``pipeline_dead_letters`` is where a stage that exhausted its retry
budget parks the complaint, so §24.2's "degrades rather than loses" is a row
somebody can query rather than a log line that rotated away.

Both are ``tenant_id NOT NULL`` from creation, per the Phase 2 rule that no
migration in this repository's future adds tenancy.

Revision ID: 69df6db2b72e
Revises: 5347db8d1387
Create Date: 2026-08-17 05:36:10.125017
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "69df6db2b72e"
down_revision: str | None = "5347db8d1387"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: Literals, not imports. A migration is a frozen description of one schema
#: transition; importing an application constant means a later refactor silently
#: rewrites history that has already been applied everywhere.
_SYSTEM_TENANT_ID = "00000000-0000-0000-0000-000000000001"
_SYSTEM_TENANT_SLUG = "__system__"


def upgrade() -> None:
    # The reserved tenant that deployment-level events are recorded against.
    #
    # Phase 2 introduced the id as a Python constant and Phase 3 is the first
    # phase to actually write with it, which is when the foreign key on
    # `events.tenant_id` turned out to make every `system_degradation` append
    # fail. The failure mode was the bad kind: it raises *inside* the handler
    # recording some other failure, so the second error masks the first, and the
    # only path that would ever have exercised it is the one that runs when the
    # system is already broken.
    op.execute(
        sa.text(
            "INSERT INTO tenants (id, slug, name, plan, is_active) "
            # CAST, not a bare parameter. asyncpg infers a bind parameter's type
            # from the driver, which sends a string, and Postgres refuses to
            # coerce varchar to uuid implicitly. The same class of defect the
            # Phase 2 log records twice for DDL and `::regclass` — a parameter
            # that reads correctly and does not run.
            "VALUES (CAST(:id AS uuid), :slug, 'NEMESIS platform', 'internal', false) "
            "ON CONFLICT (id) DO NOTHING"
        ).bindparams(id=_SYSTEM_TENANT_ID, slug=_SYSTEM_TENANT_SLUG)
    )

    # §24.2's degradation, materialised. §26.2 has to answer "why did this stop",
    # and §27.3 makes that endpoint a 5-second poll per client — so the answer
    # has to be a column rather than a replay of the complaint's whole chain.
    op.add_column("complaints", sa.Column("degraded_stage", sa.String(length=64), nullable=True))
    op.add_column("complaints", sa.Column("degraded_fallback", sa.String(length=32), nullable=True))

    op.create_table(
        "outbox_messages",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        # A pointer into the partitioned log, not a copy of it. `recorded_at`
        # travels with the id so the relay's fetch prunes to one partition.
        sa.Column("event_id", sa.BigInteger(), nullable=False),
        sa.Column("event_recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.UUID(), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_outbox_messages_tenant", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_messages")),
        sa.UniqueConstraint("tenant_id", "event_id", name="uq_outbox_messages_tenant_id_event_id"),
    )
    op.create_index(
        op.f("ix_outbox_messages_tenant_id"), "outbox_messages", ["tenant_id"], unique=False
    )
    # Partial: the relay reads the backlog, and the backlog is meant to be tiny
    # next to the dispatched rows that accumulate behind it.
    op.create_index(
        "ix_outbox_messages_pending",
        "outbox_messages",
        ["id"],
        unique=False,
        postgresql_where=sa.text("dispatched_at IS NULL"),
    )
    op.create_index(
        "ix_outbox_messages_dispatched_at", "outbox_messages", ["dispatched_at"], unique=False
    )

    op.create_table(
        "pipeline_dead_letters",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.UUID(), nullable=False),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("task_name", sa.String(length=128), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("failure_mode", sa.String(length=128), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
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
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_pipeline_dead_letters_tenant",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pipeline_dead_letters")),
    )
    op.create_index(
        op.f("ix_pipeline_dead_letters_tenant_id"),
        "pipeline_dead_letters",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_pipeline_dead_letters_tenant_id_created_at",
        "pipeline_dead_letters",
        ["tenant_id", "created_at"],
        unique=False,
    )
    # Unique only over *open* dead letters. A stage that keeps failing is one
    # unresolved problem, not a hundred rows burying every other complaint in
    # the review queue — while a resolved one stays as history.
    op.create_index(
        "uq_pipeline_dead_letters_open",
        "pipeline_dead_letters",
        ["tenant_id", "entity_id", "stage"],
        unique=True,
        postgresql_where=sa.text("resolved_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_pipeline_dead_letters_open",
        table_name="pipeline_dead_letters",
        postgresql_where=sa.text("resolved_at IS NULL"),
    )
    op.drop_index(
        "ix_pipeline_dead_letters_tenant_id_created_at", table_name="pipeline_dead_letters"
    )
    op.drop_index(op.f("ix_pipeline_dead_letters_tenant_id"), table_name="pipeline_dead_letters")
    op.drop_table("pipeline_dead_letters")

    op.drop_index(
        "ix_outbox_messages_pending",
        table_name="outbox_messages",
        postgresql_where=sa.text("dispatched_at IS NULL"),
    )
    op.drop_index("ix_outbox_messages_dispatched_at", table_name="outbox_messages")
    op.drop_index(op.f("ix_outbox_messages_tenant_id"), table_name="outbox_messages")
    op.drop_table("outbox_messages")

    op.drop_column("complaints", "degraded_fallback")
    op.drop_column("complaints", "degraded_stage")

    # Deliberately unguarded. If deployment-level events have already been
    # recorded, the RESTRICT foreign key refuses this delete and the downgrade
    # fails loudly — which is correct: reverting past this migration would
    # orphan real history, and the operator should find that out here rather
    # than from a dangling reference later.
    op.execute(
        sa.text("DELETE FROM tenants WHERE id = CAST(:id AS uuid)").bindparams(id=_SYSTEM_TENANT_ID)
    )
