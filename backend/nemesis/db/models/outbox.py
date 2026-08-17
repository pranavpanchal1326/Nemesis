"""The transactional outbox, and the parking bay for work that gave up.

Two tables that exist for the same reason: **a side effect must not be able to
happen for a state change that did not.**

``outbox_messages``
    §26.3 promises the map a live event stream. The obvious implementation —
    publish to Redis from the request handler or the task body — publishes
    before the transaction commits, so a rollback leaves the browser rendering a
    cluster merge the database never recorded. Nothing later can retract it: the
    frame is already on screen. Writing a row in the *same transaction* as the
    event and letting a relay publish committed rows makes the failure mode the
    survivable one instead — a delayed event rather than a false one.

    **The row is a pointer, not a copy.** ``event_id`` plus
    ``event_recorded_at`` locate the event; the payload stays in ``events``
    alone. A denormalised copy would be faster to publish and wrong in two ways
    that matter here: it doubles the citizen data Phase 26 has to erase and
    Phase 4 has to scrub, and it can drift from the row whose hash was signed —
    at which point the thing the client renders is no longer the thing the chain
    attests to.

``pipeline_dead_letters``
    A stage that exhausts its retry budget has to leave a trace an operator can
    *query*, not just a log line that rotates away and a metric that says a
    number went up. §24.2's rule is that a failing stage degrades the complaint
    rather than losing it, and "degraded" is only true if somebody can find the
    complaint afterwards.

Both are tenant-scoped like every other domain table. The relay reads across
tenants by construction — it serves one deployment, not one customer — and says
so explicitly through the guard's exemption rather than by having no tenant
column to check.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from nemesis.db.base import Base, TenantScopedMixin, UUIDPrimaryKeyMixin


class OutboxMessage(TenantScopedMixin, Base):
    """One committed event awaiting fan-out to the realtime transport."""

    __tablename__ = "outbox_messages"
    __table_args__ = (
        # One row per event. The relay is at-least-once on the *publish* side,
        # but enqueueing twice for one event would be a duplicate the consumer
        # could not distinguish from a genuine redelivery of a later event.
        UniqueConstraint("tenant_id", "event_id", name="uq_outbox_messages_tenant_id_event_id"),
        # The relay's only hot query is "oldest undispatched, in id order". A
        # partial index keeps it proportional to the *backlog* rather than to the
        # history — which matters because the dispatched rows are the ones that
        # accumulate forever and the undispatched ones are meant to be a handful.
        Index(
            "ix_outbox_messages_pending",
            "id",
            postgresql_where=text("dispatched_at IS NULL"),
        ),
        # Retention sweep: dispatched rows older than the resume window.
        Index("ix_outbox_messages_dispatched_at", "dispatched_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)

    #: Locates the event in the partitioned log. ``event_recorded_at`` is carried
    #: alongside the id purely so the planner can prune to one monthly partition
    #: instead of scanning every month of history for a single row.
    event_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    #: Envelope fields, duplicated deliberately and *only* these. They are the
    #: §26.3 envelope minus the payload, so the relay can route and order without
    #: reading the log, and a client that only needs "something changed on this
    #: entity" never causes a payload fetch.
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    #: NULL until the relay has published it. The column *is* the queue state;
    #: there is no in-memory position that could disagree with the database
    #: about what has been sent.
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class PipelineDeadLetter(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    """A pipeline stage that exhausted its retry budget, parked for a human."""

    __tablename__ = "pipeline_dead_letters"
    __table_args__ = (
        # One open dead letter per (entity, stage). A stage that keeps failing
        # should read as one unresolved problem getting worse, not as a hundred
        # rows that bury the other complaints in the queue.
        Index(
            "uq_pipeline_dead_letters_open",
            "tenant_id",
            "entity_id",
            "stage",
            unique=True,
            postgresql_where=text("resolved_at IS NULL"),
        ),
        Index("ix_pipeline_dead_letters_tenant_id_created_at", "tenant_id", "created_at"),
    )

    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    #: A ``PipelineStage`` value. Text rather than an enum column: the stage
    #: vocabulary is a Python enum checked against the dashboards by CI, and
    #: duplicating it as a database type means two places to migrate whenever a
    #: phase adds a stage.
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    task_name: Mapped[str] = mapped_column(String(128), nullable=False)

    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    failure_mode: Mapped[str] = mapped_column(String(128), nullable=False)
    #: The exception's *type and message*, never its traceback. A traceback in a
    #: queryable table is an information-disclosure surface (§25) and the trace
    #: is already in the logs, keyed by the correlation id below.
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    #: Resolution is recorded, never implied by deletion. "This complaint's
    #: classification failed and somebody dealt with it" and "no record exists"
    #: must not look the same, which is the same argument ``archived_partitions``
    #: makes for detached history.
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
