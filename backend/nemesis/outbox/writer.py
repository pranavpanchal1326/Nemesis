"""Enqueue a committed event for realtime fan-out — inside the caller's transaction.

The one rule this module exists to enforce: **an outbox row is written by the
same transaction that wrote the event, or not at all.** It therefore takes a
session and never commits. A function here that opened its own transaction would
be able to enqueue a notification for an event that subsequently rolled back,
which is precisely the failure the outbox pattern is chosen to prevent.

Not every event is enqueued. ``PUBLISHED_ENTITY_TYPES`` names the three chains
whose events describe a thing on the map; ``admin_action`` and
``system_degradation`` are operational history, and broadcasting them to every
connected browser would mean an outage's internals reaching a citizen's phone.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.db.models.outbox import OutboxMessage
from nemesis.domain.lifecycle import EntityType
from nemesis.events.store import AppendedEvent
from nemesis.tenancy.guard import TENANT_SCOPE_EXEMPT

#: Chains whose events reach a connected client. Everything else stays in the
#: log. This is an allow-list on purpose: a chain added later is silent until
#: somebody decides it should not be, which is the safe direction for a
#: broadcast surface.
PUBLISHED_ENTITY_TYPES: Final[frozenset[str]] = frozenset(
    {
        EntityType.COMPLAINT.value,
        EntityType.COMPLAINT_CLUSTER.value,
        EntityType.WORK_ORDER.value,
    }
)


def is_published(entity_type: str) -> bool:
    return entity_type in PUBLISHED_ENTITY_TYPES


async def enqueue(session: AsyncSession, event: AppendedEvent) -> bool:
    """Write the outbox row for ``event``. Returns ``False`` if it was not enqueued.

    Two reasons for ``False``, both normal:

    * the event is on a chain that is not published, and
    * the append was a redelivery, so the original event was enqueued the first
      time and re-enqueueing would publish it twice.

    ``ON CONFLICT DO NOTHING`` backs the second case up in the database rather
    than trusting the caller to check, because the caller that forgets is the
    retry path — the one place this is guaranteed to be exercised.
    """
    if event.was_redelivery or not is_published(event.entity_type):
        return False

    written = (
        await session.execute(
            pg_insert(OutboxMessage)
            .values(
                tenant_id=event.tenant_id,
                event_id=event.id,
                event_recorded_at=event.recorded_at,
                entity_type=event.entity_type,
                entity_id=event.entity_id,
                event_type=event.event_type,
                sequence=event.sequence,
                occurred_at=event.occurred_at,
                correlation_id=event.correlation_id,
            )
            .on_conflict_do_nothing(constraint="uq_outbox_messages_tenant_id_event_id")
            .returning(OutboxMessage.id)
        )
    ).scalar_one_or_none()
    return written is not None


async def pending_count(session: AsyncSession, *, tenant_id: uuid.UUID) -> int:
    """Undispatched rows for one tenant — for tests and the ops surface."""
    total = (
        await session.execute(
            select(func.count())
            .select_from(OutboxMessage)
            .where(
                OutboxMessage.tenant_id == tenant_id,
                OutboxMessage.dispatched_at.is_(None),
            )
        )
    ).scalar_one()
    return int(total)


async def undispatched_total(session: AsyncSession) -> int:
    """Backlog across every tenant, for the ``outbox_pending_messages`` gauge.

    A single number rather than a per-tenant series: the gauge answers "is the
    relay keeping up", which is a property of the deployment, and a tenant label
    on it would be unbounded cardinality on an operational signal.
    """
    # tenant-scope-exempt: the relay backlog spans every tenant by construction,
    # exactly as the integrity sweep does.
    total = (
        await session.execute(
            select(func.count())
            .select_from(OutboxMessage)
            .where(OutboxMessage.dispatched_at.is_(None))
            .execution_options(**{TENANT_SCOPE_EXEMPT: True})
        )
    ).scalar_one()
    return int(total)


async def purge_dispatched(
    session: AsyncSession, *, older_than: datetime, safe_below: int | None = None
) -> int:
    """Delete dispatched rows past the resume window.

    Dispatched rows are kept, not deleted on publish, so a client that
    reconnects with a cursor can be caught up from the outbox instead of from a
    scan of the partitioned log. That only works while the rows exist, which
    makes retention a real decision rather than housekeeping: the window is the
    longest disconnect a client may resume across.

    Deleting an outbox row destroys no history — the event it points at is
    untouched — so unlike §22.4 retention on ``events``, this one is safe to
    automate.

    **``safe_below`` is the Phase 4 correction, and it closes a real hole.** The
    realtime relay was the only reader when this was written; the webhook
    fan-out is a second one, and it advances on its own cursor. A row that the
    relay dispatched hours ago but the fan-out has not read yet is, by the
    predicate above, eligible for deletion — and deleting it means the events it
    pointed at are never delivered to any webhook subscriber, with no failed row
    anywhere to show it, because the delivery was never created.

    Passing the fan-out's cursor as ``safe_below`` keeps those rows. ``None``
    means "no second reader", which is only correct where there genuinely is
    none, so the scheduled task passes the cursor rather than defaulting it.
    """
    # tenant-scope-exempt: retention runs on the clock across the whole
    # deployment, exactly as `archived_partitions` maintenance does. A per-tenant
    # purge would hold the loop open for as long as the largest customer's
    # backlog takes and would still have to visit every tenant.
    predicates = [
        OutboxMessage.dispatched_at.is_not(None),
        OutboxMessage.dispatched_at < older_than,
    ]
    if safe_below is not None:
        predicates.append(OutboxMessage.id <= safe_below)

    result = await session.execute(
        # tenant-scope-exempt: clock-driven retention across the deployment.
        delete(OutboxMessage).where(*predicates).execution_options(**{TENANT_SCOPE_EXEMPT: True})
    )
    return int(getattr(result, "rowcount", 0) or 0)
