"""Identity, composed roles, scoped grants, sessions, impersonation — Phase 13.

**``users`` is not here.** It arrived in Phase 5's control-plane migration
(``db.models.organisation``) because the organisation tree needed something to
hang a department membership on, and it carries the ``subject`` column with the
comment that says Phase 13 brings the provider behind it. This phase extends
that table rather than replacing it: a second user table would mean two answers
to "who is this", and the Phase 14 work-order assignee foreign key already
points at the first one.

**What each table is for, and why it is a table rather than a column.**

``roles`` / ``role_grants``
    Critique-log defect #7: authorization was RBAC over a fixed enum. A role is
    now a tenant-authored set of permission strings, and a *grant* is that role
    held by one user over one scope for one window of time. Two tables, not one:
    the same role is held by forty people, and editing "Ward Officer" must not
    mean forty rows to update — it means one, and every grant sees the change at
    the next decision.

``user_credentials``
    A one-to-one table rather than columns on ``users``, for the reason the
    Phase 5 docstring gives about passwords never landing in that table. The
    separation is real and not cosmetic: an SSO tenant has users with no row
    here at all, a ``SELECT * FROM users`` during an incident shows no password
    material, and the retention story for a credential (delete on
    deactivation) is different from the retention story for a user (keep, for
    the audit trail that references them).

``user_sessions``
    One row per refresh-token family member. Refresh rotation means the row is
    *replaced* on every use and the old digest is kept, revoked, so a replayed
    token is detectable rather than merely expired — see
    ``identity.sessions.rotate``.

``identity_providers``
    Per-tenant OIDC configuration, **holding no secret**. The client secret is
    named by ``client_secret_setting`` and read from the process environment, so
    a database dump of a customer's tenant does not contain a credential that
    authenticates us to their IdP. That asymmetry with ``api_keys`` (which does
    store a digest) is intentional: a digest cannot be replayed, a client secret
    can.

``impersonation_sessions``
    The audited support path. ``tenant_id`` is the **impersonated** tenant, not
    the operator's, and that is the column the gate clause rests on: "every
    impersonation session is visible to the impersonated tenant" is only true if
    the row is scoped to them and the event lands in their chain.

``authorization_decisions``
    The decision log. Every denial, and every allow of a permission the registry
    marks ``audited``. Append-only by convention and not by hash chain — it is
    not in ``events`` on purpose, see the class docstring.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
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

#: Hex characters in a SHA-256 digest of a refresh token. Matches
#: ``domain.constants.HASH_HEX_LENGTH``; declared as its own constant because
#: this one sizes a *credential* column and that one sizes the hash chain, and a
#: future change to either must not silently change the other.
TOKEN_DIGEST_LENGTH = 64

#: Longest permission name the registry can hold, with room. Permissions are
#: ``resource:action`` strings from ``identity.permissions``; the array column is
#: ``TEXT[]`` so this bound is advisory and enforced in the service, where the
#: error can say which permission and what the legal set is.
PERMISSION_MAX_LENGTH = 64

#: Minimum characters in an impersonation justification. A number rather than
#: "non-empty" because "test" is non-empty and answers nothing. Thirty
#: characters is roughly one clause — enough to name a ticket and a symptom.
MIN_JUSTIFICATION_LENGTH = 30


class Role(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, OptimisticVersionMixin, Base):
    """A tenant-authored set of permissions.

    ``permissions`` is a ``TEXT[]`` rather than a join table against a
    ``permissions`` table, and this is the one place in this schema that
    deliberately denormalises. The vocabulary lives in *code*
    (``identity.permissions`` says why), so a ``permissions`` table would be a
    mirror of a Python dict, maintained by a migration, joined on every
    decision, and capable of disagreeing with the registry it mirrors. The array
    is read in one row with the grant, validated against the registry on write,
    and checked by CI on every build.
    """

    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_roles_tenant_id_code"),
        CheckConstraint("cardinality(permissions) > 0", name="role_permits_something"),
        # Distinctness is **not** a constraint here, and the reason is a
        # Postgres limitation worth recording rather than discovering twice: a
        # CHECK may not contain a subquery, so the obvious
        # `cardinality(permissions) = cardinality(ARRAY(SELECT DISTINCT
        # unnest(permissions)))` is rejected outright by the server. The
        # alternatives are an immutable helper function (a migration that ships
        # PL/pgSQL to deduplicate an array, maintained forever) or doing it in
        # the service. `identity.roles` sorts and deduplicates on every write,
        # and a duplicate entry is harmless anyway — it renders a permission
        # twice in a UI and permits exactly what it already permitted.
        CheckConstraint(
            "permissions <> ARRAY[]::text[] AND array_position(permissions, NULL) IS NULL",
            name="role_permissions_are_present",
        ),
    )

    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    permissions: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)

    #: A role this deployment ships (``support``, ``tenant_admin``) rather than
    #: one the tenant composed. Not a different table: it is the same thing with
    #: a different author, and the only behavioural difference is that the API
    #: refuses to edit one — which is a rule about who may write, not about what
    #: the row means.
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")


class RoleGrant(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """One user holds one role over one scope, optionally for a window.

    **Expiry is a column, not a cleanup job.** ``valid_until`` is read by every
    decision, so a delegation that has lapsed stops authorising at the moment it
    lapses rather than at the next sweep. A job that deleted expired grants
    would make "what could this person do last Tuesday" unanswerable, which is
    the question an audit asks.

    **``delegated_by_user_id`` is what makes delegation auditable rather than
    merely possible.** A grant created by an administrator and a grant handed
    over by a colleague going on leave are different facts with different
    accountability, and a single ``created_by`` column cannot tell them apart.
    """

    __tablename__ = "role_grants"
    __table_args__ = (
        # Scope columns are mutually exclusive and selected by `scope_kind`; the
        # unique constraint uses the generic `scope_ref` so one index covers
        # every kind. A composite over three nullable columns would be unique
        # only where all three are non-null, which is never.
        UniqueConstraint(
            "tenant_id",
            "user_id",
            "role_id",
            "scope_kind",
            "scope_ref",
            name="uq_role_grants_subject_scope",
        ),
        Index("ix_role_grants_tenant_id_user_id", "tenant_id", "user_id"),
        # The decision point loads a user's live grants on every request. The
        # partial index keeps that read proportional to *live* grants rather
        # than to every grant in the tenant's history, which is what the
        # "expiry is a column, not a cleanup job" decision costs if unindexed.
        Index(
            "ix_role_grants_live",
            "tenant_id",
            "user_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
        CheckConstraint(
            "valid_until IS NULL OR valid_from IS NULL OR valid_until > valid_from",
            name="validity_span_is_ordered",
        ),
        # `tenant` and `self` scopes name no row; every other kind must.
        CheckConstraint(
            "(scope_kind IN ('tenant', 'self') AND scope_ref IS NULL) "
            "OR (scope_kind NOT IN ('tenant', 'self') AND scope_ref IS NOT NULL)",
            name="scope_ref_matches_kind",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE", name="fk_role_grants_user"),
        nullable=False,
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        # RESTRICT, not CASCADE: deleting a role that people hold must be a
        # deliberate act that deals with the grants first, not a silent removal
        # of forty people's access discovered at shift change.
        ForeignKey("roles.id", ondelete="RESTRICT", name="fk_role_grants_role"),
        nullable=False,
        index=True,
    )

    #: One of ``identity.permissions.SCOPE_KINDS``. Free text for the reason
    #: that module gives: the vocabulary belongs to the resolver, and a Postgres
    #: enum would be a migration every time a phase adds a scope.
    scope_kind: Mapped[str] = mapped_column(String(32), nullable=False)

    #: The department, zone, taxonomy node or contractor this grant is scoped
    #: to. **Deliberately not a foreign key**, and the reason is uncomfortable
    #: enough to write down: it references one of four tables depending on
    #: ``scope_kind``, and the alternatives are four nullable FK columns (which
    #: the CHECK above would have to police pairwise) or a polymorphic column
    #: with no referential integrity. Four columns was the better design right
    #: up until Phase 17 adds a fifth scope kind and every query grows a branch.
    #: The integrity this gives up is recovered in ``identity.roles``, which
    #: resolves the referenced row on write and refuses a grant naming something
    #: that does not exist in this tenant, and in the decision point, which
    #: denies a grant whose scope no longer resolves.
    scope_ref: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    #: Set when a colleague handed over their own authority rather than an
    #: administrator issuing it. Never the same as the row's creator in the
    #: audit sense — see the class docstring.
    delegated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_role_grants_delegated_by"),
        nullable=True,
    )
    #: Required on a delegation, optional otherwise. "Covering for Priya,
    #: 14-21 March" is the difference between an audit that explains itself and
    #: one that lists rows.
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class UserCredential(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """A local password, for a tenant with no identity provider.

    **The parameters are stored with the digest.** ``algorithm`` and ``params``
    are columns, not constants, because the cost of a KDF has to rise over the
    life of a deployment and a hardcoded parameter set means either every
    existing credential becomes unverifiable or the parameters never rise. With
    them on the row, raising the cost is a configuration change that applies to
    the next successful verification, credential by credential.

    **Lockout is on the row, not in Redis.** A lockout that evaporates when the
    cache restarts is a lockout an attacker can clear, and the restart is not
    hypothetical — it is a deploy. The cost is a write on a failed attempt,
    which is the path we are least worried about making slower.
    """

    __tablename__ = "user_credentials"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_user_credentials_user_id"),
        CheckConstraint("failed_attempts >= 0", name="failed_attempts_not_negative"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE", name="fk_user_credentials_user"),
        nullable=False,
    )

    #: ``scrypt`` today. A column so a future algorithm can coexist with it
    #: during a migration rather than requiring a flag day.
    algorithm: Mapped[str] = mapped_column(String(32), nullable=False, server_default="scrypt")
    params: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, server_default="{}")

    salt: Mapped[str] = mapped_column(String(64), nullable=False)
    digest: Mapped[str] = mapped_column(String(256), nullable=False)

    #: Forces a change at next sign-in. Set on an administrator-issued
    #: credential, because a password somebody else chose is a password somebody
    #: else knows.
    must_change: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserSession(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """One member of a refresh-token family.

    **Why the access token is checked against this row on every request.** A JWT
    that is verified by signature alone cannot be revoked before it expires, and
    "revocation" was a Phase 13 ship line. The alternatives were a short TTL and
    hope, a Redis denylist (a second datastore in the authentication path, and
    an outage that either fails open or locks everyone out), or an indexed
    primary-key read per request. The read is one index probe against a table
    with one row per active session, on a request that is already opening a
    database connection — and it makes "revoke this session" take effect on the
    next request rather than in up to ten minutes. ADR-0066 records the trade.

    **Reuse detection.** Rotation replaces the digest and keeps the old row,
    revoked. A presented token whose digest matches a revoked row means either a
    replay or a stolen token being used behind the legitimate holder, and both
    answers are the same: revoke the entire family and make everyone sign in
    again. ``family_id`` is what makes that one statement.
    """

    __tablename__ = "user_sessions"
    __table_args__ = (
        # Globally unique, not per tenant: the digest is what a presented token
        # resolves to *before* a tenant is known, exactly like `api_keys`.
        UniqueConstraint("refresh_digest", name="uq_user_sessions_refresh_digest"),
        Index("ix_user_sessions_tenant_id_user_id", "tenant_id", "user_id"),
        Index("ix_user_sessions_family_id", "family_id"),
        CheckConstraint("expires_at > issued_at", name="session_span_is_ordered"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE", name="fk_user_sessions_user"),
        nullable=False,
    )

    #: SHA-256 of the opaque refresh token. Unsalted, for the reason
    #: ``integrations.keys`` states: the token is 256 bits of
    #: ``secrets.token_bytes`` and there is no dictionary to attack.
    refresh_digest: Mapped[str] = mapped_column(String(TOKEN_DIGEST_LENGTH), nullable=False)

    #: Every session descended from one sign-in. Revoking a family is one
    #: statement, which is what makes reuse detection a usable response rather
    #: than a logged observation.
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    #: The session this one replaced, for walking a family's history.
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user_sessions.id", ondelete="SET NULL", name="fk_user_sessions_parent"),
        nullable=True,
    )

    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reuse_detected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    #: How this session was authenticated: ``password``, ``oidc``,
    #: ``impersonation``. In the row because "every session on this account was
    #: created by SSO except one" is a question an incident asks.
    method: Mapped[str] = mapped_column(String(32), nullable=False, server_default="password")

    #: Set when this session exists because support is acting as the user.
    impersonation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    #: **Truncated, never whole.** An IPv4 address loses its last octet and an
    #: IPv6 address keeps its /48, which is enough to say "this session moved
    #: countries" and not enough to locate a person's home. §22 treats an
    #: officer's address as personal data too, and a session table is not an
    #: access log.
    client_network: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: The user agent's product token only, trimmed by the API layer. The full
    #: string is a fingerprint; the product token answers "did they sign in from
    #: the field app or the console".
    client_kind: Mapped[str | None] = mapped_column(String(64), nullable=True)


class IdentityProvider(
    UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, OptimisticVersionMixin, Base
):
    """A tenant's OIDC provider. Holds configuration and no credential.

    ``client_secret_setting`` names an environment variable; the value is read
    from the process at the moment of the back-channel token request. See the
    module docstring on why this table, unlike ``api_keys``, stores nothing
    secret at all.

    ``discovery`` caches the provider's ``.well-known`` document with the time
    it was fetched. Cached because fetching it on every sign-in makes the
    provider's availability our availability on the hottest path in the system;
    re-fetched on a cadence, and ADR-0060's rule applies when it cannot be
    reached — a stale document that still has endpoints is an answer, and a
    sign-in that fails because a metadata endpoint was slow is not.
    """

    __tablename__ = "identity_providers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_identity_providers_tenant_id_slug"),
        # One issuer per tenant. Two providers with the same issuer would make
        # "which provider authenticated this subject" ambiguous, and the subject
        # claim is only unique *within* an issuer.
        UniqueConstraint("tenant_id", "issuer", name="uq_identity_providers_tenant_id_issuer"),
    )

    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)

    issuer: Mapped[str] = mapped_column(Text, nullable=False)
    client_id: Mapped[str] = mapped_column(Text, nullable=False)
    client_secret_setting: Mapped[str] = mapped_column(String(128), nullable=False)

    #: Scopes requested at the authorization endpoint. ``openid`` is mandatory
    #: and added by the service, so a misconfigured row cannot produce a request
    #: that is not an OIDC request.
    scopes: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{openid,email,profile}"
    )

    #: Email domains this provider is authoritative for. A sign-in whose
    #: verified email falls outside them is refused — without it, any Google
    #: account could sign in to a tenant that configured Google Workspace SSO,
    #: which is the single most common OIDC misconfiguration there is.
    allowed_email_domains: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )

    #: Create a user on first successful sign-in. Off by default: a tenant that
    #: has not said otherwise gets "unknown user" rather than a user table that
    #: fills up with everybody at the university.
    auto_provision: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    #: The role granted to an auto-provisioned user, tenant-wide. Nullable, and
    #: a null with ``auto_provision`` on means the user is created with *no*
    #: grants — which is the default-deny posture, visible rather than implied.
    auto_provision_role_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="RESTRICT", name="fk_identity_providers_role"),
        nullable=True,
    )

    discovery: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, server_default="{}")
    discovery_fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")


class OidcAuthRequest(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """One in-flight authorization-code flow: state, nonce and PKCE verifier.

    In the database rather than in a cookie or in Redis. A cookie would work and
    is what most implementations do; it also means the state parameter is
    verified against something the browser holds, and this product's threat
    model includes a console on a shared municipal desktop. Redis would work
    too, and would put a cache outage in the sign-in path.

    Rows are short-lived — ``expires_at`` is minutes — and swept by the same
    retention job that handles expired sessions. The verifier is stored because
    PKCE requires presenting it at the token endpoint; it is useless without the
    code, which arrives over TLS on a redirect we initiated.
    """

    __tablename__ = "oidc_auth_requests"
    __table_args__ = (
        UniqueConstraint("state", name="uq_oidc_auth_requests_state"),
        Index("ix_oidc_auth_requests_expires_at", "expires_at"),
    )

    provider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identity_providers.id", ondelete="CASCADE", name="fk_oidc_requests_provider"),
        nullable=False,
    )

    state: Mapped[str] = mapped_column(String(128), nullable=False)
    nonce: Mapped[str] = mapped_column(String(128), nullable=False)
    code_verifier: Mapped[str] = mapped_column(String(128), nullable=False)
    redirect_uri: Mapped[str] = mapped_column(Text, nullable=False)
    #: Where to send the browser once the session exists. Validated against the
    #: configured allow-list before it is stored, never on the way out: a URL
    #: checked at redirect time is a URL that was stored unchecked, and the next
    #: reader of this table will assume it was validated.
    return_to: Mapped[str | None] = mapped_column(Text, nullable=True)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ImpersonationSession(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """A support engineer acting as a tenant user, with a reason and an end.

    ``tenant_id`` is the **impersonated** tenant. That is the whole design: the
    gate clause is that every impersonation session is visible to the
    impersonated tenant, and a row scoped to the operator's own tenant would be
    invisible to the only party that needs to see it. The operator is named by
    ``operator_subject`` and ``operator_tenant_id``.

    ``allows_writes`` defaults false. Most support work is reading what the
    customer sees; a session that can also act is a different request with a
    different justification, and the decision point refuses a write on a
    read-only impersonation even where the impersonated user could perform it.
    """

    __tablename__ = "impersonation_sessions"
    __table_args__ = (
        Index("ix_impersonation_sessions_tenant_id_started_at", "tenant_id", "started_at"),
        CheckConstraint(
            f"length(justification) >= {MIN_JUSTIFICATION_LENGTH}",
            name="justification_is_substantive",
        ),
        CheckConstraint("ends_at > started_at", name="impersonation_span_is_ordered"),
    )

    #: Who is being acted as.
    subject_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_impersonation_subject_user"),
        nullable=False,
    )

    operator_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    operator_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_impersonation_operator_user"),
        nullable=False,
    )
    operator_tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT", name="fk_impersonation_operator_tenant"),
        nullable=False,
    )

    justification: Mapped[str] = mapped_column(Text, nullable=False)
    #: The support ticket this session answers. Optional because an incident at
    #: 03:00 does not always have a ticket yet, and refusing the session would
    #: be refusing the only thing that helps.
    ticket_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)

    allows_writes: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    #: A hard cap, written at start. An impersonation with no end is a standing
    #: grant wearing a support ticket.
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: Counters, maintained as the session is used. Cheaper than deriving them
    #: from the decision log and, more usefully, they are what a tenant's own
    #: impersonation view shows: "read 14 complaints, changed nothing".
    decisions_allowed: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    decisions_denied: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


class AuthorizationDecision(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    """Every denial, and every allow the registry marks ``audited``.

    **Why this is not in ``events``.** The event store is a hash-chained,
    append-only, replayable record of things that *happened to domain entities*,
    and every row in it is kept forever. An authorization decision is neither:
    it is an operational observation, it is high-volume, and §22.4 retention has
    to be able to expire it — which is the exact argument ``trust.submission_media``
    makes about EXIF data, reached from the other direction. Putting decisions
    in the chain would mean either a log that cannot honour retention or a chain
    with holes in it.

    What *is* an event: the impersonation session (``impersonation_started``),
    because that is a thing that happened to a tenant and must be provable
    forever.

    **No ``TimestampMixin``.** ``updated_at`` would be a lie on a table nothing
    updates, and ``onupdate`` on an append-only table is a trap for whoever
    eventually writes a correction script.
    """

    __tablename__ = "authorization_decisions"
    __table_args__ = (
        Index("ix_authorization_decisions_tenant_id_decided_at", "tenant_id", "decided_at"),
        Index(
            "ix_authorization_decisions_tenant_id_user_id_decided_at",
            "tenant_id",
            "user_id",
            "decided_at",
        ),
    )

    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    #: Nullable: an unauthenticated request that reached a guarded endpoint is
    #: exactly the denial most worth recording, and it has no user.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", name="fk_authorization_decisions_user"),
        nullable=True,
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    impersonation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    #: Denormalised from the impersonation session so the tenant's own view of
    #: "who read my data" is one table scan, and so the answer survives the
    #: operator's user row being removed.
    operator_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)

    permission: Mapped[str] = mapped_column(String(PERMISSION_MAX_LENGTH), nullable=False)
    allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    #: One of ``identity.scopes.REASON_CODES``. A closed vocabulary because this
    #: column is grouped by in every query that reads this table and is a metric
    #: label besides.
    reason_code: Mapped[str] = mapped_column(String(48), nullable=False)

    resource_kind: Mapped[str | None] = mapped_column(String(48), nullable=True)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    #: The scope of the grant that allowed it, as text ("department:pw/south").
    #: Text rather than a foreign key to the grant: a grant that is later
    #: revoked and deleted must not take the record of what it authorised with
    #: it.
    matched_scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    matched_grant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    #: The route, as FastAPI names it ("GET /api/v1/complaints/{complaint_id}").
    #: The templated path, never the resolved one: a path with an id in it makes
    #: this column unaggregatable and duplicates what ``resource_id`` holds.
    route: Mapped[str | None] = mapped_column(String(160), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: Phase 26 sweeps on this, exactly as ``submission_media`` is swept.
    #: Written by the service from the configured retention, so changing the
    #: retention changes what new rows promise and leaves old rows' promises
    #: intact.
    purge_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )


class CredentialPolicy(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """Per-tenant password rules. One row per tenant, or none for the defaults.

    A table rather than settings, for the Phase 5 reason: a municipality with a
    published IT policy needs a twelve-character minimum without waiting for a
    deploy, and the campus next door does not. A row that does not exist means
    the platform defaults apply, which is why there is no ``is_default`` column
    to get out of step with reality.
    """

    __tablename__ = "credential_policies"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_credential_policies_tenant_id"),
        CheckConstraint("min_length >= 8", name="min_length_is_not_absurd"),
        CheckConstraint("max_failed_attempts >= 3", name="lockout_leaves_room_for_typos"),
    )

    min_length: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="12")
    max_failed_attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="10"
    )
    lockout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default="900")
    #: Zero means "never expires", and that is the default on purpose: forced
    #: rotation on a schedule is a control current guidance considers harmful,
    #: and a tenant whose auditor requires it can set it rather than everybody
    #: inheriting it.
    max_age_days: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    #: Refuse a password that appeared in a known breach corpus, when one is
    #: configured. Off by default because the corpus is not shipped.
    refuse_known_breached: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )


__all__ = [
    "MIN_JUSTIFICATION_LENGTH",
    "PERMISSION_MAX_LENGTH",
    "TOKEN_DIGEST_LENGTH",
    "AuthorizationDecision",
    "CredentialPolicy",
    "IdentityProvider",
    "ImpersonationSession",
    "OidcAuthRequest",
    "Role",
    "RoleGrant",
    "UserCredential",
    "UserSession",
]
