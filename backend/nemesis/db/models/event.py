"""The append-only event log and the three tables that make it safe.

``events`` is the system of record (§9.1). The other three exist because the
design decisions around it — partitioning, per-entity chaining, idempotent
redelivery — each need a guarantee a single partitioned table cannot give.

**Why ``events`` is partitioned by month.** It is append-only and must live for
years to serve Phase 21's replay and §22.4's retention schedule. Converting a
large append-only table to partitioned later means a full rewrite under a lock;
doing it in the first migration costs one design decision now. Retention then
becomes ``DETACH PARTITION`` — instant, and it does not fight autovacuum the way
a bulk ``DELETE`` from a hot table does.

**Why partitioning forces three companion tables.** Postgres requires the
partition key in every unique constraint on a partitioned table. That makes
``UNIQUE (tenant_id, entity_type, entity_id, sequence)`` unenforceable — it
would have to include ``recorded_at``, and would then permit a duplicate
sequence number as long as the two rows landed in different months. Since
per-entity ordering *is* the tamper-evidence property, it cannot be left to
convention:

``event_chain_heads``
    Unpartitioned, one row per entity, holding the tail hash and sequence. This
    is the row ``EventStore.append`` takes ``FOR UPDATE`` on. Locking here
    rather than on the log tail is both correct and faster: a single primary-key
    lookup instead of an ordered scan across partitions, and the row exists
    before the first event so there is no race to create it.
``event_idempotency``
    Unpartitioned, primary key ``(tenant_id, idempotency_key)``. A redelivery
    weeks later must still be a no-op, and a unique index inside ``events``
    would only ever be per-partition — so the same task retried across a month
    boundary would silently append a second copy of the same event.
``event_snapshots``
    Materialised projector state at a sequence number, so replay stays O(1) in
    history length rather than O(n) (§9.1's time travel is only usable if
    seeking is cheap).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Identity,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from nemesis.db.base import Base, TenantScopedMixin, UUIDPrimaryKeyMixin
from nemesis.domain.constants import HASH_HEX_LENGTH

#: Hex SHA-256. Fixed-width so a truncated or padded hash is a constraint
#: violation at write time rather than a verification failure months later.
HashColumn = String(HASH_HEX_LENGTH)


class Event(TenantScopedMixin, Base):
    """One immutable fact. Never updated, never deleted — only archived."""

    __tablename__ = "events"
    __table_args__ = (
        # Postgres requires the partition key in the primary key. `id` alone is
        # still globally unique in practice (it comes from one sequence); the
        # composite exists to satisfy the partitioning rule, not to express that
        # two events can share an id.
        PrimaryKeyConstraint("id", "recorded_at", name="pk_events"),
        # Defence in depth. Per-partition only — `event_chain_heads` is the real
        # enforcement — but it turns the overwhelmingly common case of a
        # same-month duplicate into an immediate constraint violation instead of
        # a fork discovered by the next integrity sweep.
        UniqueConstraint(
            "tenant_id",
            "entity_type",
            "entity_id",
            "sequence",
            "recorded_at",
            name="uq_events_entity_sequence",
        ),
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        CheckConstraint("event_version >= 1", name="version_positive"),
        # §9.2's two indexes, both widened by tenant_id so a query the tenancy
        # guard accepts is also a query the planner can serve from an index.
        Index("ix_events_entity", "tenant_id", "entity_type", "entity_id", "sequence"),
        Index("ix_events_type_time", "tenant_id", "event_type", "recorded_at"),
        Index("ix_events_correlation_id", "correlation_id"),
        {"postgresql_partition_by": "RANGE (recorded_at)"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), nullable=False)

    #: **Partition key and system time**: when this process wrote the row.
    #: Distinct from ``occurred_at`` on purpose. An offline field submission
    #: (Phase 22) occurred hours before it was recorded, and conflating the two
    #: would either misplace it in the partition layout or misreport when the
    #: pothole was actually photographed.
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    #: **Business time**, and the value that enters the hash preimage. Replay
    #: and the §41 KPIs order by this; it is supplied by the caller and is
    #: therefore part of what the chain attests to.
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    #: 1-based position in this entity's chain. Makes reordering detectable and
    #: lets a reader ask for "events 40 onward" without scanning from genesis.
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)

    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    #: Payload schema version. Never inferred from the payload's shape —
    #: inference is what makes an append-only log unreadable after two
    #: refactors, because the *first* ambiguous payload is unresolvable forever.
    event_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)

    #: Who or what caused this. Nullable because the actor may be the system —
    #: a Beat sweep or a projector — and inventing a synthetic user id for those
    #: would make the audit trail read as if a person did it.
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    actor_role: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: The correlation id already carried through logs and traces, so one
    #: submission's events, spans, and log lines join without a bespoke id.
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: The event that caused this one. Turns the log into a causal graph, which
    #: is what "walk me through what the agent decided" (§12.4) needs in order
    #: to be answerable rather than merely logged.
    causation_event_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)

    previous_hash: Mapped[str] = mapped_column(HashColumn, nullable=False)
    event_hash: Mapped[str] = mapped_column(HashColumn, nullable=False)


class EventChainHead(TenantScopedMixin, Base):
    """The tail of one entity's chain, and the lock that serialises appends."""

    __tablename__ = "event_chain_heads"
    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "entity_type", "entity_id", name="pk_event_chain_heads"),
        CheckConstraint("sequence >= 0", name="sequence_non_negative"),
    )

    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    #: 0 before the first event, so the head row can be created up front and the
    #: append path never has to branch on "does this entity exist yet".
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    #: ``GENESIS_HASH`` until the first append.
    head_hash: Mapped[str] = mapped_column(HashColumn, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class EventIdempotency(TenantScopedMixin, Base):
    """Proof that a given append already happened, independent of partitioning."""

    __tablename__ = "event_idempotency"
    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "idempotency_key", name="pk_event_idempotency"),
    )

    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Which event the first delivery produced, so a redelivery can return the
    #: original rather than merely reporting "already done" — the caller needs
    #: the event id to continue its own pipeline.
    event_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class EventSnapshot(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    """Projector state at a sequence number, so seeking does not replay from zero."""

    __tablename__ = "event_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "entity_type",
            "entity_id",
            "sequence",
            name="uq_event_snapshots_entity_sequence",
        ),
        Index(
            "ix_event_snapshots_lookup",
            "tenant_id",
            "entity_type",
            "entity_id",
            "sequence",
        ),
    )

    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)

    #: The projected state, canonicalised. Stored as JSONB and compared by
    #: ``state_hash`` so "replay reproduces current state byte-identically" is a
    #: hash equality rather than a recursive dict diff whose failure output
    #: nobody can read.
    state: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    state_hash: Mapped[str] = mapped_column(HashColumn, nullable=False)
    #: The projector build that produced this state. A snapshot written by an
    #: older projector must be discarded rather than trusted, or a projector bug
    #: fix would never take effect for entities that already had snapshots.
    projector_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ArchivedPartition(Base):
    """Registry of detached ``events`` partitions (§22.4 retention).

    Detaching a partition removes it from the log's query path while leaving the
    data intact. Without a record of what was detached and when, "the event is
    not in the table" becomes indistinguishable from "the event never existed" —
    which is precisely the ambiguity an append-only log exists to eliminate.
    """

    __tablename__ = "archived_partitions"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    partition_name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    range_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    range_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    row_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: Where the detached data went, and the digest that proves it arrived
    #: unaltered. Free text because the destination is a deployment decision
    #: (Phase 1b), not an application one.
    archive_location: Mapped[str | None] = mapped_column(Text, nullable=True)
    archive_digest: Mapped[str | None] = mapped_column(HashColumn, nullable=True)
    detached_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
