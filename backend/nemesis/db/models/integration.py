"""API keys, usage accounting, and outbound webhook delivery — Phase 4.

§16.3 promises civil society and journalists a durable public interface. That is
an API *product*, and a product needs three things this module provides: a way
to identify a consumer, a way to bill or bound what they consume, and a way to
push events at them rather than making them poll.

Four decisions here are worth reading before the columns.

**A key is stored as a SHA-256 digest, and deliberately not as a bcrypt/argon2
hash.** Password hashing exists to make a *low-entropy* secret expensive to
guess. A key minted here is 256 bits from ``secrets.token_bytes`` — there is
nothing to guess, and the search space does not care how slow the hash is. What
a slow KDF would buy is a hundred milliseconds of CPU on the hottest
authentication path in the system, paid per request, to defend against an attack
that is already computationally impossible. The digest is unsalted for the same
reason a salt exists at all: salts defeat precomputation against *reused,
guessable* secrets, and a random 256-bit value has no rainbow table.

**Usage is a daily rollup, not a row per request.** A row per request is an
unbounded write on the read path — it doubles the cost of every public API call,
it grows without a natural bound, and it duplicates data Prometheus already
holds at better resolution. What a *tenant* actually asks is "how much did we
use this month, broken down by endpoint", and that question is answered by an
upsert into one row per (key, day, endpoint). §22 also applies: a per-request log
would carry a client address, which is personal data this system has no reason
to retain in order to count requests.

**Webhook deliveries are rows, not queue messages.** A queue can tell you a
message is pending; it cannot answer "show me every delivery to this endpoint
last week and why the third one failed", which is what the phase's "delivery log
tenants can inspect" means. The row *is* the queue state, exactly as
``outbox_messages.dispatched_at`` is — so there is no in-memory position that can
disagree with the database about what has been sent.

**The fan-out reads a cursor over the outbox rather than hooking the write
path.** ``outbox_messages`` is already the committed event feed in commit order,
which is precisely what a webhook dispatcher needs, and reading it keeps the
submission transaction free of a subscription lookup. The cursor is a table
rather than a Redis key because losing it would silently skip every event
between the loss and the restart — a durable side effect must not depend on a
cache.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from nemesis.db.base import (
    Base,
    TenantScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)

#: Characters of the minted key shown to the operator forever after. Enough to
#: identify which key is in a log line or a support ticket, far too few to
#: reconstruct. Stored in the clear precisely so a key can be *named* without
#: anybody needing to hold the secret.
KEY_PREFIX_LENGTH = 12

#: A SHA-256 digest as lowercase hex.
DIGEST_LENGTH = 64


class ApiKey(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """One API consumer's credential, scoped to a tenant."""

    __tablename__ = "api_keys"
    __table_args__ = (
        # The digest is the lookup key on every authenticated request, and it
        # must be unique across the deployment: two tenants cannot share a
        # secret, and the lookup happens *before* a tenant is known — the key is
        # what names the tenant.
        UniqueConstraint("key_digest", name="uq_api_keys_key_digest"),
        Index("ix_api_keys_tenant_id_prefix", "tenant_id", "key_prefix"),
        CheckConstraint(
            f"char_length(key_digest) = {DIGEST_LENGTH}", name="digest_is_a_sha256_hex"
        ),
        CheckConstraint("quota_per_hour > 0", name="quota_is_positive"),
        # A revoked key keeps its row forever — see ``revoked_at`` — so the
        # constraint is about ordering, not about existence.
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at", name="revocation_follows_creation"
        ),
    )

    #: Operator-facing name. "Times of India data desk", not a UUID.
    name: Mapped[str] = mapped_column(Text, nullable=False)

    #: The visible identifier. Appears in logs, in the developer portal, and in
    #: the usage rollup, so a consumer can be discussed without the secret being
    #: pasted into a chat window in order to identify them.
    key_prefix: Mapped[str] = mapped_column(String(KEY_PREFIX_LENGTH), nullable=False)
    key_digest: Mapped[str] = mapped_column(String(DIGEST_LENGTH), nullable=False)

    #: What this key may do. Free-text strings validated against a code-declared
    #: set at mint time rather than a database enum, for the reason
    #: ``pipeline_dead_letters.stage`` gives: the vocabulary is checked by CI
    #: against the routes, and a database type would be a second thing to
    #: migrate every time a phase adds a capability.
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String(64)), nullable=False)

    #: Requests per hour. Per-key rather than per-plan because the whole point of
    #: issuing a key to a research partner is that their allowance differs from
    #: the anonymous public one, and §26.4's 60/min/IP is a floor for people who
    #: have no key at all.
    quota_per_hour: Mapped[int] = mapped_column(Integer, nullable=False, server_default="3600")

    #: Coarse, and that is deliberate. An exact last-used timestamp would mean a
    #: write on every read; this is refreshed by the usage rollup, so it is
    #: accurate to the rollup interval and costs nothing extra.
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    #: Expiry is optional and revocation is permanent. Both are recorded rather
    #: than implemented by deleting the row: a key that authenticated a request
    #: last March must stay resolvable, or the usage rollup and the audit trail
    #: point at nothing.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class ApiKeyUsage(TenantScopedMixin, Base):
    """Requests attributed to one key, on one day, for one endpoint template.

    ``endpoint`` is the **route template**, never the resolved path — the same
    cardinality argument ``observability.metrics`` makes, applied to a table.
    A resolved path would put a ward code and a fiscal year into the grouping
    key and turn a bounded rollup into one row per distinct URL.
    """

    __tablename__ = "api_key_usage"
    __table_args__ = (
        # The upsert target. One row per key per day per endpoint, which is what
        # makes the write an ON CONFLICT increment instead of an insert.
        UniqueConstraint(
            "tenant_id",
            "api_key_id",
            "usage_date",
            "endpoint",
            name="uq_api_key_usage_bucket",
        ),
        Index("ix_api_key_usage_tenant_id_usage_date", "tenant_id", "usage_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)

    api_key_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("api_keys.id", ondelete="CASCADE", name="fk_api_key_usage_api_key"),
        nullable=False,
    )

    #: A date, not a timestamp, and in UTC. A tenant-local day would make the
    #: rollup depend on a timezone that can be reconfigured, which would silently
    #: rewrite the meaning of every historical row.
    usage_date: Mapped[date] = mapped_column(Date, nullable=False)
    endpoint: Mapped[str] = mapped_column(String(128), nullable=False)

    request_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    #: 4xx and 5xx kept apart from the total rather than derived from it. "We
    #: made 10 000 calls" and "9 000 of them were rejected" are the same number
    #: to a single counter and completely different to whoever is paying.
    error_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    #: Requests refused for exceeding ``quota_per_hour``. Separate from
    #: ``error_count`` because a throttled consumer is a capacity conversation
    #: and a 4xx is a bug conversation.
    throttled_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")


class WebhookEndpoint(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """A URL a tenant wants events pushed to, and what it wants pushed."""

    __tablename__ = "webhook_endpoints"
    __table_args__ = (
        # One subscription per URL per tenant. A second identical subscription
        # would deliver everything twice, which reads to the receiver as a
        # redelivery bug in this system rather than as their own configuration.
        UniqueConstraint("tenant_id", "url", name="uq_webhook_endpoints_tenant_id_url"),
        Index(
            "ix_webhook_endpoints_active",
            "tenant_id",
            postgresql_where=text("is_active"),
        ),
        CheckConstraint("cardinality(event_types) > 0", name="subscribes_to_something"),
        CheckConstraint("secret_version >= 1", name="secret_version_is_positive"),
    )

    description: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)

    #: **The signing secret is not stored — it is derived.**
    #:
    #: HMAC needs the plaintext at every delivery, so unlike an API key this
    #: cannot be a one-way digest. The two obvious answers are both worse than
    #: this one: storing it in the clear puts a live credential in every backup
    #: and every ``pg_dump`` a support engineer takes, and encrypting it
    #: introduces a key-management problem — a second secret, a rotation
    #: procedure, and a new dependency — to protect a value the deployment could
    #: simply recompute.
    #:
    #: So the secret is ``HMAC(deployment_key, endpoint_id || secret_version)``.
    #: Nothing sensitive is at rest, rotation is an integer increment, and a
    #: database dump on its own reveals no signing key at all.
    #:
    #: ``secret_fingerprint`` is the first bytes of the secret's digest, kept so
    #: a tenant can confirm *which* secret an endpoint is on — after a rotation,
    #: "is my handler verifying against the new one" is otherwise unanswerable
    #: without one side revealing the secret.
    secret_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    secret_fingerprint: Mapped[str] = mapped_column(String(KEY_PREFIX_LENGTH), nullable=False)

    #: Which event types to deliver. An explicit list, never "all": a tenant
    #: that subscribes to everything receives every future event type this
    #: system ever adds, including ones whose payload their handler has never
    #: seen, and discovers that in production.
    event_types: Mapped[list[str]] = mapped_column(ARRAY(String(128)), nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    #: Set when the dispatcher gives up on an endpoint that has failed every
    #: delivery for long enough that it is clearly gone rather than briefly down.
    #: Recorded rather than deactivating silently, because "we stopped sending"
    #: is information the tenant needs and cannot infer from an absence.
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    disabled_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


class WebhookDelivery(TenantScopedMixin, Base):
    """One attempt-tracked delivery of one event to one endpoint.

    The row is a *pointer* to the event, matching ``OutboxMessage``'s reasoning
    exactly: a denormalised payload copy doubles what Phase 26 must erase and can
    drift from the row whose hash was signed. The signed body is rebuilt at each
    attempt from the event, so a retry three hours later sends bytes that still
    correspond to the log.
    """

    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        # At-most-once *enqueue* per (endpoint, event). The dispatcher is
        # at-least-once on the wire — a crash between the HTTP 200 and the row
        # update resends — which the receiver detects by the delivery id in the
        # signature header. Enqueueing twice would be undetectable to them.
        UniqueConstraint(
            "tenant_id", "endpoint_id", "event_id", name="uq_webhook_deliveries_endpoint_event"
        ),
        # The dispatcher's only hot query: "due, not yet terminal, oldest first".
        # Partial, so it stays proportional to the backlog rather than to the
        # history of every event ever delivered.
        Index(
            "ix_webhook_deliveries_due",
            "next_attempt_at",
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "ix_webhook_deliveries_tenant_id_endpoint_id_created_at",
            "tenant_id",
            "endpoint_id",
            "created_at",
        ),
        CheckConstraint("status IN ('pending', 'delivered', 'failed')", name="status_is_known"),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)

    endpoint_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "webhook_endpoints.id", ondelete="CASCADE", name="fk_webhook_deliveries_endpoint"
        ),
        nullable=False,
    )

    #: Locates the event, with ``event_recorded_at`` carried alongside purely so
    #: the planner prunes to one monthly partition instead of scanning every
    #: month of history for a single row — the same trick ``OutboxMessage`` uses.
    event_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")

    #: NULL once terminal. While pending it is the schedule: the dispatcher
    #: selects on it, so backoff is expressed as a future timestamp rather than
    #: as a sleep somebody has to keep a process alive to honour.
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, server_default=func.now()
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    #: The receiver's status code on the last attempt, and the error if there
    #: was one. The *body* they returned is deliberately not stored — it is
    #: attacker-influenced content on an inspectable surface (§25), and the code
    #: plus the exception type is what diagnoses a delivery failure.
    last_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class WebhookCursor(Base):
    """How far the fan-out has read the outbox. Exactly one row.

    **Not tenant-scoped, and that is the point.** The outbox is a single
    deployment-wide ordered feed; a cursor per tenant would mean N scans of the
    same table and would let one tenant's stuck subscription hold back nothing
    while another's silently raced ahead. One reader, one position, and the
    fan-out decides per row which tenants care.

    A table rather than a Redis key because losing the position would skip every
    event between the loss and the restart, silently and permanently. A durable
    side effect must not depend on a cache.
    """

    __tablename__ = "webhook_cursor"
    __table_args__ = (
        # Exactly one row, enforced rather than assumed. Two cursors would both
        # advance, and each would deliver the half the other skipped.
        CheckConstraint("id = 1", name="cursor_is_a_singleton"),
    )

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)

    #: The highest ``outbox_messages.id`` already fanned out. Advanced in the
    #: same transaction as the delivery rows it produced, so a crash re-reads
    #: the batch rather than skipping it — and the unique constraint on
    #: ``webhook_deliveries`` makes that re-read a no-op.
    last_outbox_id: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
