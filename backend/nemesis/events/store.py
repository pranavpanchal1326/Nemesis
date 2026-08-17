"""``EventStore`` — the only sanctioned way to write history.

Blueprint §9.1: the append-only log is the system of record, and no state
mutation happens without an event in the same transaction. This class is where
that rule is mechanised.

**How concurrent appends are serialised.** Two workers finishing two pipeline
stages for the same complaint at the same moment must not both read sequence 7
and both write sequence 8 — that forks the chain, and a forked chain cannot be
verified, which silently destroys the tamper-evidence the whole design exists
for. The serialisation point is one row in ``event_chain_heads``, taken with a
row-level lock:

    INSERT ... ON CONFLICT ... DO UPDATE ... RETURNING

That single statement is doing something subtle and worth reading twice. The
obvious alternative — ``INSERT ... ON CONFLICT DO NOTHING`` followed by
``SELECT ... FOR UPDATE`` — has a race that only appears under the exact
concurrency this is meant to survive: two transactions both find the row absent,
both insert, one gets the conflict and does *nothing*, and its subsequent
``SELECT`` finds no row, because the winner has not committed yet. The upsert
form takes the lock and returns the row in one atomic step whether it created
the row or found it.

Locking the head row rather than the log tail is also what makes this fast: a
primary-key lookup on a small unpartitioned table, instead of an ordered scan
for ``MAX(sequence)`` across every monthly partition of a table that grows
forever.

**Idempotency.** Every append may carry a key. Redelivery — a Celery retry, a
duplicated webhook, a client that lost the response — returns the original event
rather than appending a second copy. §37.4 requires redelivery to be a *provable*
no-op, so the proof is a primary key in ``event_idempotency``, not a check that
races with the write it is trying to protect.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.db.models.event import Event, EventChainHead, EventIdempotency
from nemesis.events.canonical import JSONValue
from nemesis.events.hashing import GENESIS_HASH, compute_event_hash
from nemesis.events.registry import entity_type_of, latest_version, validate_payload
from nemesis.tenancy.context import require_tenant_id


class EventStoreError(RuntimeError):
    """An append could not be completed as specified."""


class ChainForkError(EventStoreError):
    """Two appends produced the same sequence for one entity.

    Should be unreachable while the head lock is held. It is raised rather than
    swallowed because the alternative — retrying — would paper over a broken
    invariant, and a chain that forks once will fork again under load.
    """


@dataclass(frozen=True, slots=True)
class AppendedEvent:
    """What was written, or what was already there on a redelivery."""

    id: int
    tenant_id: uuid.UUID
    entity_type: str
    entity_id: uuid.UUID
    sequence: int
    event_type: str
    event_version: int
    payload: JSONValue
    occurred_at: datetime
    recorded_at: datetime
    previous_hash: str
    event_hash: str
    #: Carried on the result, not just written to the row. The outbox needs it to
    #: stamp the published envelope, and re-reading the event to recover a value
    #: the caller had in hand a moment ago is a query that exists only because a
    #: dataclass field was missing.
    correlation_id: str | None = None
    #: True when an idempotency key matched an existing event and nothing was
    #: appended. Returned rather than hidden: a caller that emits a notification
    #: per event needs to know it is looking at a replay, or the citizen gets
    #: the same SMS twice.
    was_redelivery: bool = False


class EventStore:
    """Append-only writer bound to one session, and therefore to one transaction.

    Bound to a session on purpose. §9.1 requires the state change and its event
    to commit together, so the store must be able to participate in the caller's
    transaction rather than opening its own — a store that committed
    independently could leave an event describing a state change that rolled
    back.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(
        self,
        *,
        entity_id: uuid.UUID,
        event_type: str,
        payload: Mapping[str, Any],
        entity_type: str | None = None,
        tenant_id: uuid.UUID | None = None,
        actor_id: uuid.UUID | None = None,
        actor_role: str | None = None,
        correlation_id: str | None = None,
        causation_event_id: int | None = None,
        idempotency_key: str | None = None,
        occurred_at: datetime | None = None,
    ) -> AppendedEvent:
        """Append one event, or return the existing one for a known key."""
        resolved_tenant = tenant_id if tenant_id is not None else require_tenant_id()

        # The registry owns the entity/type relationship. Accepting a caller's
        # entity_type when it disagrees would let the same event type land on
        # two chains, making replay order ambiguous.
        registered_entity = entity_type_of(event_type)
        if entity_type is not None and entity_type != registered_entity:
            raise EventStoreError(
                f"'{event_type}' is registered against entity '{registered_entity}', "
                f"not '{entity_type}'"
            )

        version = latest_version(event_type)
        validated = validate_payload(event_type, version, payload)

        when = occurred_at or datetime.now(tz=UTC)
        if when.tzinfo is None:
            raise EventStoreError("occurred_at must be timezone-aware")

        if idempotency_key is not None:
            existing = await self._find_by_idempotency_key(resolved_tenant, idempotency_key)
            if existing is not None:
                return existing

        head_sequence, head_hash = await self._lock_chain_head(
            resolved_tenant, registered_entity, entity_id
        )
        sequence = head_sequence + 1

        event_hash = compute_event_hash(
            previous_hash=head_hash,
            tenant_id=resolved_tenant,
            entity_type=registered_entity,
            entity_id=entity_id,
            sequence=sequence,
            event_type=event_type,
            event_version=version,
            payload=validated,
            occurred_at=when,
        )

        try:
            row = (
                await self._session.execute(
                    pg_insert(Event)
                    .values(
                        tenant_id=resolved_tenant,
                        entity_type=registered_entity,
                        entity_id=entity_id,
                        sequence=sequence,
                        event_type=event_type,
                        event_version=version,
                        payload=validated,
                        actor_id=actor_id,
                        actor_role=actor_role,
                        correlation_id=correlation_id,
                        causation_event_id=causation_event_id,
                        idempotency_key=idempotency_key,
                        occurred_at=when,
                        previous_hash=head_hash,
                        event_hash=event_hash,
                    )
                    .returning(Event.id, Event.recorded_at)
                )
            ).one()
        except IntegrityError as exc:  # pragma: no cover — requires a lost head lock
            raise ChainForkError(
                f"sequence {sequence} already exists for {registered_entity} {entity_id}; "
                f"the chain head lock did not hold"
            ) from exc

        event_id, recorded_at = row

        await self._session.execute(
            sa_update(EventChainHead)
            .where(
                EventChainHead.tenant_id == resolved_tenant,
                EventChainHead.entity_type == registered_entity,
                EventChainHead.entity_id == entity_id,
            )
            .values(sequence=sequence, head_hash=event_hash)
        )

        if idempotency_key is not None:
            await self._session.execute(
                pg_insert(EventIdempotency).values(
                    tenant_id=resolved_tenant,
                    idempotency_key=idempotency_key,
                    event_id=event_id,
                    event_recorded_at=recorded_at,
                )
            )

        return AppendedEvent(
            id=event_id,
            tenant_id=resolved_tenant,
            entity_type=registered_entity,
            entity_id=entity_id,
            sequence=sequence,
            event_type=event_type,
            event_version=version,
            payload=validated,
            occurred_at=when,
            recorded_at=recorded_at,
            previous_hash=head_hash,
            event_hash=event_hash,
            correlation_id=correlation_id,
        )

    async def _lock_chain_head(
        self, tenant_id: uuid.UUID, entity_type: str, entity_id: uuid.UUID
    ) -> tuple[int, str]:
        """Create-or-lock this entity's chain head, returning its current tail.

        See the module docstring for why this is one statement rather than an
        insert followed by a select.
        """
        statement = (
            pg_insert(EventChainHead)
            .values(
                tenant_id=tenant_id,
                entity_type=entity_type,
                entity_id=entity_id,
                sequence=0,
                head_hash=GENESIS_HASH,
            )
            .on_conflict_do_update(
                constraint="pk_event_chain_heads",
                # A write that changes nothing. Its purpose is the row lock and
                # the RETURNING clause, both of which ON CONFLICT DO NOTHING
                # would give up.
                set_={"sequence": EventChainHead.sequence},
            )
            .returning(EventChainHead.sequence, EventChainHead.head_hash)
        )
        sequence, head_hash = (await self._session.execute(statement)).one()
        return int(sequence), str(head_hash)

    async def _find_by_idempotency_key(
        self, tenant_id: uuid.UUID, idempotency_key: str
    ) -> AppendedEvent | None:
        claim = (
            await self._session.execute(
                select(EventIdempotency.event_id, EventIdempotency.event_recorded_at).where(
                    EventIdempotency.tenant_id == tenant_id,
                    EventIdempotency.idempotency_key == idempotency_key,
                )
            )
        ).one_or_none()
        if claim is None:
            return None

        event_id, recorded_at = claim
        event = (
            await self._session.execute(
                select(Event).where(
                    Event.tenant_id == tenant_id,
                    Event.id == event_id,
                    # Included so the planner prunes to one partition instead of
                    # scanning every month of history for a single id.
                    Event.recorded_at == recorded_at,
                )
            )
        ).scalar_one()
        return _to_appended(event, was_redelivery=True)

    async def read_stream(
        self,
        *,
        entity_type: str,
        entity_id: uuid.UUID,
        tenant_id: uuid.UUID | None = None,
        from_sequence: int = 1,
        limit: int | None = None,
    ) -> Sequence[Event]:
        """Every event for one entity, in chain order.

        Ordered by ``sequence`` rather than by ``id`` or ``recorded_at``.
        Sequence is the chain's own ordering and the thing the hash attests to;
        the identity column is shared across entities and merely correlates with
        it, and ``recorded_at`` reflects when the writer ran.
        """
        resolved_tenant = tenant_id if tenant_id is not None else require_tenant_id()
        statement = (
            select(Event)
            .where(
                Event.tenant_id == resolved_tenant,
                Event.entity_type == entity_type,
                Event.entity_id == entity_id,
                Event.sequence >= from_sequence,
            )
            .order_by(Event.sequence)
        )
        if limit is not None:
            statement = statement.limit(limit)
        return (await self._session.execute(statement)).scalars().all()

    async def chain_head(
        self, *, entity_type: str, entity_id: uuid.UUID, tenant_id: uuid.UUID | None = None
    ) -> tuple[int, str] | None:
        """Current sequence and tail hash, or ``None`` if nothing was appended."""
        resolved_tenant = tenant_id if tenant_id is not None else require_tenant_id()
        row = (
            await self._session.execute(
                select(EventChainHead.sequence, EventChainHead.head_hash).where(
                    EventChainHead.tenant_id == resolved_tenant,
                    EventChainHead.entity_type == entity_type,
                    EventChainHead.entity_id == entity_id,
                )
            )
        ).one_or_none()
        if row is None:
            return None
        return int(row[0]), str(row[1])


def _to_appended(event: Event, *, was_redelivery: bool = False) -> AppendedEvent:
    payload: JSONValue = event.payload  # type: ignore[assignment]
    return AppendedEvent(
        id=event.id,
        tenant_id=event.tenant_id,
        entity_type=event.entity_type,
        entity_id=event.entity_id,
        sequence=event.sequence,
        event_type=event.event_type,
        event_version=event.event_version,
        payload=payload,
        occurred_at=event.occurred_at,
        recorded_at=event.recorded_at,
        previous_hash=event.previous_hash,
        event_hash=event.event_hash,
        correlation_id=event.correlation_id,
        was_redelivery=was_redelivery,
    )
