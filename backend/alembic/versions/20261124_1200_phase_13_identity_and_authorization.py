"""Phase 13: identity, composed roles, scoped grants, sessions, impersonation.

Revision ID: 3b91ce07af54
Revises: 6f2a41c9b7d3
Create Date: 2026-11-24 12:00:00.000000

Eight new tables and six new columns on ``users``. The models argue each design
decision at length (``db/models/identity.py``); this file records only what a
replay needs and the two things a *migration* has to decide that a model cannot.

**One: ``users.role`` becomes nullable.** It does not get dropped. Phase 5's
provisioning templates write it, the demo seed writes it, and §18.1's role names
are still the words a customer says out loud — but no authorization decision
reads it any more. Dropping a column is an unrecoverable loss of history for the
rows that have one; making it nullable costs nothing and leaves the label in
place. The column comment is updated in the same statement so a reader in psql
is told what happened rather than inferring it from a nullable column nothing
writes.

**Two: the partial indexes are created by this migration and not inferable from
the models alone.** ``ix_role_grants_live`` and ``uq_users_tenant_id_email``
both carry a ``WHERE`` clause, and a partial unique index is the only way to say
"unique among the rows that have a value" — which is what an optional email on a
tenant-scoped user needs. Autogenerate reproduces them from the model metadata,
and they are written out here explicitly so a reader of the migration sees the
predicate rather than trusting that it round-tripped.

**No data migration, and that is a decision rather than an absence.** Existing
tenants get their role library installed by ``identity.bootstrap.install_roles``
when they are next provisioned or when an operator runs the CLI — not by this
revision. A migration that inserted roles would be writing *tenant data* from a
schema change, which means a tenant could not refuse it, and the roles it wrote
would be identical to the library at the moment the migration was authored
rather than at the moment it ran. Schema migrations change shape; the control
plane changes content.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "3b91ce07af54"
down_revision: str | None = "6f2a41c9b7d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Mirrors ``identity.permissions.SCOPE_KINDS`` and is written out as literals
#: for the reason the Phase 10 migration states: a migration that imports the
#: model describes whatever the model says *today*, so replaying this revision
#: after a later change would apply a constraint from the future.
_SCOPES_WITHOUT_REF = ("tenant", "self")

#: Matches ``db.models.identity.MIN_JUSTIFICATION_LENGTH``. Same reasoning.
_MIN_JUSTIFICATION = 30

#: **Every ``CheckConstraint`` below names itself *bare*** --
#: ``role_permits_something``, not ``ck_roles_role_permits_something``. The
#: metadata's naming convention (``db.base.NAMING_CONVENTION``) expands
#: ``ck_%(table_name)s_%(constraint_name)s``, so a pre-expanded name is
#: expanded a second time, overruns Postgres's 63-character identifier limit,
#: and arrives in the database as ``ck_roles_ck_roles_role_permits_so_4f21``.
#: Nothing fails at migration time; ``alembic check`` then reports permanent
#: drift between a model that names the constraint one way and a database that
#: spells it another, and no later migration can reference it by name.


def upgrade() -> None:
    op.create_table(
        "roles",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("permissions", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("is_system", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("version", sa.BigInteger(), server_default="0", nullable=False),
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
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_roles_tenant", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_roles"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_roles_tenant_id_code"),
        sa.CheckConstraint("cardinality(permissions) > 0", name="role_permits_something"),
        sa.CheckConstraint(
            "permissions <> ARRAY[]::text[] AND array_position(permissions, NULL) IS NULL",
            name="role_permissions_are_present",
        ),
    )
    op.create_index("ix_roles_tenant_id", "roles", ["tenant_id"])

    op.create_table(
        "identity_providers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False),
        # The *name* of an environment variable, never a secret. See the model.
        sa.Column("client_secret_setting", sa.String(length=128), nullable=False),
        sa.Column(
            "scopes",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{openid,email,profile}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "allowed_email_domains",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("auto_provision", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("auto_provision_role_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "discovery",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("discovery_fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("version", sa.BigInteger(), server_default="0", nullable=False),
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
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_identity_providers_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["auto_provision_role_id"],
            ["roles.id"],
            name="fk_identity_providers_role",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_identity_providers"),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_identity_providers_tenant_id_slug"),
        sa.UniqueConstraint("tenant_id", "issuer", name="uq_identity_providers_tenant_id_issuer"),
    )
    op.create_index("ix_identity_providers_tenant_id", "identity_providers", ["tenant_id"])

    # --- users: six columns, and one loosened constraint --------------------
    op.add_column("users", sa.Column("email", sa.String(length=320), nullable=True))
    op.add_column(
        "users",
        sa.Column("kind", sa.String(length=32), server_default="operator", nullable=False),
    )
    op.add_column(
        "users",
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    op.add_column(
        "users", sa.Column("identity_provider_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_users_provider",
        "users",
        "identity_providers",
        ["identity_provider_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "uq_users_tenant_id_email",
        "users",
        ["tenant_id", "email"],
        unique=True,
        postgresql_where=sa.text("email IS NOT NULL"),
    )
    op.alter_column("users", "role", existing_type=sa.String(length=64), nullable=True)
    op.execute(
        """
        COMMENT ON COLUMN users.role IS
        'Superseded by role_grants (Phase 13) and read by no authorization '
        'decision. Retained as an org-chart label: Phase 5 templates and the '
        'demo seed write it, and dropping it would lose the label on every '
        'existing row. Nullable since Phase 13.'
        """
    )

    op.create_table(
        "user_credentials",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("algorithm", sa.String(length=32), server_default="scrypt", nullable=False),
        sa.Column(
            "params",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("salt", sa.String(length=64), nullable=False),
        sa.Column("digest", sa.String(length=256), nullable=False),
        sa.Column("must_change", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_user_credentials_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_user_credentials_user", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_user_credentials"),
        sa.UniqueConstraint("user_id", name="uq_user_credentials_user_id"),
        sa.CheckConstraint("failed_attempts >= 0", name="failed_attempts_not_negative"),
    )
    op.create_index("ix_user_credentials_tenant_id", "user_credentials", ["tenant_id"])

    op.create_table(
        "credential_policies",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("min_length", sa.SmallInteger(), server_default="12", nullable=False),
        sa.Column("max_failed_attempts", sa.SmallInteger(), server_default="10", nullable=False),
        sa.Column("lockout_seconds", sa.Integer(), server_default="900", nullable=False),
        sa.Column("max_age_days", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column(
            "refuse_known_breached",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
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
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_credential_policies_tenant",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_credential_policies"),
        sa.UniqueConstraint("tenant_id", name="uq_credential_policies_tenant_id"),
        sa.CheckConstraint("min_length >= 8", name="min_length_is_not_absurd"),
        sa.CheckConstraint(
            "max_failed_attempts >= 3",
            name="lockout_leaves_room_for_typos",
        ),
    )
    op.create_index("ix_credential_policies_tenant_id", "credential_policies", ["tenant_id"])

    op.create_table(
        "role_grants",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope_kind", sa.String(length=32), nullable=False),
        # No foreign key, on purpose: it references one of four tables selected
        # by `scope_kind`. `identity.roles.resolve_scope` enforces existence on
        # write and the decision point denies a scope that no longer resolves.
        sa.Column("scope_ref", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delegated_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_role_grants_tenant", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_role_grants_user", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["role_id"], ["roles.id"], name="fk_role_grants_role", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["delegated_by_user_id"],
            ["users.id"],
            name="fk_role_grants_delegated_by",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_role_grants"),
        sa.UniqueConstraint(
            "tenant_id",
            "user_id",
            "role_id",
            "scope_kind",
            "scope_ref",
            name="uq_role_grants_subject_scope",
        ),
        sa.CheckConstraint(
            "valid_until IS NULL OR valid_from IS NULL OR valid_until > valid_from",
            name="validity_span_is_ordered",
        ),
        sa.CheckConstraint(
            "(scope_kind IN ({unref}) AND scope_ref IS NULL) "
            "OR (scope_kind NOT IN ({unref}) AND scope_ref IS NOT NULL)".format(
                unref=", ".join(f"'{kind}'" for kind in _SCOPES_WITHOUT_REF)
            ),
            name="scope_ref_matches_kind",
        ),
    )
    op.create_index("ix_role_grants_tenant_id", "role_grants", ["tenant_id"])
    op.create_index("ix_role_grants_role_id", "role_grants", ["role_id"])
    op.create_index("ix_role_grants_tenant_id_user_id", "role_grants", ["tenant_id", "user_id"])
    # Partial: the decision point loads *live* grants on every request, and an
    # index covering revoked history would grow without bound against a read
    # that never wants it.
    op.create_index(
        "ix_role_grants_live",
        "role_grants",
        ["tenant_id", "user_id"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    op.create_table(
        "user_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("refresh_digest", sa.String(length=64), nullable=False),
        sa.Column("family_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "issued_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=64), nullable=True),
        sa.Column("reuse_detected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("method", sa.String(length=32), server_default="password", nullable=False),
        sa.Column("impersonation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("client_network", sa.String(length=64), nullable=True),
        sa.Column("client_kind", sa.String(length=64), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_user_sessions_tenant", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_user_sessions_user", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["user_sessions.id"], name="fk_user_sessions_parent", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_user_sessions"),
        # Globally unique: the digest is what a presented refresh token resolves
        # to *before* a tenant is known.
        sa.UniqueConstraint("refresh_digest", name="uq_user_sessions_refresh_digest"),
        sa.CheckConstraint("expires_at > issued_at", name="session_span_is_ordered"),
    )
    op.create_index("ix_user_sessions_tenant_id", "user_sessions", ["tenant_id"])
    op.create_index("ix_user_sessions_tenant_id_user_id", "user_sessions", ["tenant_id", "user_id"])
    op.create_index("ix_user_sessions_family_id", "user_sessions", ["family_id"])

    op.create_table(
        "oidc_auth_requests",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state", sa.String(length=128), nullable=False),
        sa.Column("nonce", sa.String(length=128), nullable=False),
        sa.Column("code_verifier", sa.String(length=128), nullable=False),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("return_to", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_oidc_auth_requests_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["identity_providers.id"],
            name="fk_oidc_requests_provider",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_oidc_auth_requests"),
        sa.UniqueConstraint("state", name="uq_oidc_auth_requests_state"),
    )
    op.create_index("ix_oidc_auth_requests_tenant_id", "oidc_auth_requests", ["tenant_id"])
    op.create_index("ix_oidc_auth_requests_expires_at", "oidc_auth_requests", ["expires_at"])

    op.create_table(
        "impersonation_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        # The *impersonated* tenant. The gate clause rests on this column.
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operator_subject", sa.String(length=255), nullable=False),
        sa.Column("operator_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operator_tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("justification", sa.Text(), nullable=False),
        sa.Column("ticket_ref", sa.String(length=128), nullable=True),
        sa.Column("allows_writes", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_reason", sa.String(length=64), nullable=True),
        sa.Column("decisions_allowed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("decisions_denied", sa.Integer(), server_default="0", nullable=False),
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
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_impersonation_sessions_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["subject_user_id"],
            ["users.id"],
            name="fk_impersonation_subject_user",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["operator_user_id"],
            ["users.id"],
            name="fk_impersonation_operator_user",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["operator_tenant_id"],
            ["tenants.id"],
            name="fk_impersonation_operator_tenant",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_impersonation_sessions"),
        sa.CheckConstraint(
            f"length(justification) >= {_MIN_JUSTIFICATION}",
            name="justification_is_substantive",
        ),
        sa.CheckConstraint("ends_at > started_at", name="impersonation_span_is_ordered"),
    )
    op.create_index("ix_impersonation_sessions_tenant_id", "impersonation_sessions", ["tenant_id"])
    op.create_index(
        "ix_impersonation_sessions_tenant_id_started_at",
        "impersonation_sessions",
        ["tenant_id", "started_at"],
    )

    op.create_table(
        "authorization_decisions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("impersonation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("operator_subject", sa.String(length=255), nullable=True),
        sa.Column("permission", sa.String(length=64), nullable=False),
        sa.Column("allowed", sa.Boolean(), nullable=False),
        sa.Column("reason_code", sa.String(length=48), nullable=False),
        sa.Column("resource_kind", sa.String(length=48), nullable=True),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("matched_scope", sa.Text(), nullable=True),
        sa.Column("matched_grant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("route", sa.String(length=160), nullable=True),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.Column("purge_after", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_authorization_decisions_tenant",
            ondelete="RESTRICT",
        ),
        # SET NULL, not CASCADE: removing a user must not remove the record of
        # what they were refused.
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_authorization_decisions_user",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_authorization_decisions"),
    )
    op.create_index(
        "ix_authorization_decisions_tenant_id", "authorization_decisions", ["tenant_id"]
    )
    op.create_index(
        "ix_authorization_decisions_decided_at", "authorization_decisions", ["decided_at"]
    )
    op.create_index(
        "ix_authorization_decisions_tenant_id_decided_at",
        "authorization_decisions",
        ["tenant_id", "decided_at"],
    )
    op.create_index(
        "ix_authorization_decisions_tenant_id_user_id_decided_at",
        "authorization_decisions",
        ["tenant_id", "user_id", "decided_at"],
    )
    # Phase 26 sweeps on this, exactly as it sweeps `submission_media`.
    op.create_index(
        "ix_authorization_decisions_purge_after", "authorization_decisions", ["purge_after"]
    )


def downgrade() -> None:
    """Reverse, in dependency order.

    The ``users`` columns come back off and ``role`` goes back to ``NOT NULL`` —
    which is only possible because nothing in this phase ever writes a user with
    a null role except through the API this phase adds, and that API is being
    removed by the same downgrade. A deployment that has created users since
    upgrading will have rows with a null ``role``, so the ``NOT NULL`` restore
    backfills the column first rather than failing halfway through a downgrade
    with the new tables already dropped.
    """
    op.drop_table("authorization_decisions")
    op.drop_table("impersonation_sessions")
    op.drop_table("oidc_auth_requests")
    op.drop_table("user_sessions")
    op.drop_table("role_grants")
    op.drop_table("credential_policies")
    op.drop_table("user_credentials")

    op.drop_index("uq_users_tenant_id_email", table_name="users")
    op.drop_constraint("fk_users_provider", "users", type_="foreignkey")
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "identity_provider_id")
    op.drop_column("users", "is_active")
    op.drop_column("users", "kind")
    op.drop_column("users", "email")
    # Backfill before restoring NOT NULL. The literal is the §18.1 label a
    # pre-Phase-13 row would have carried; it is a placeholder in a downgrade
    # path, not a role the system acts on.
    op.execute("UPDATE users SET role = 'unassigned' WHERE role IS NULL")
    op.alter_column("users", "role", existing_type=sa.String(length=64), nullable=False)
    op.execute("COMMENT ON COLUMN users.role IS NULL")

    op.drop_table("identity_providers")
    op.drop_table("roles")
