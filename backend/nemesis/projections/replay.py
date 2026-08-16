"""Replay an entity's state from the log, with snapshots so seeking stays cheap.

Without snapshots, projecting an entity is O(n) in its history and Phase 21's
time scrubber degrades as the product succeeds — the busiest ward, the one an
operator most wants to inspect, is the slowest to render. A snapshot every N
events makes any seek a bounded read: the nearest snapshot plus at most N-1
events.

**Snapshots are a cache and are always discardable.** Every function here can be
run with ``use_snapshots=False`` and must produce the identical result; that
equivalence is the property the test suite asserts, and it is what makes a
corrupt or stale snapshot a performance problem rather than a correctness one. A
snapshot written by an older ``PROJECTOR_VERSION`` is ignored rather than
trusted, so fixing a projector bug actually reaches the entities that have
history — which is to say, the ones the bug affected.

Replay is **read-only**. It appends nothing and mutates no projection table. The
Phase 21 gate requires that to be provable, so the write path lives in
``projections.writer`` and nothing in this module imports it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.db.models.event import Event, EventSnapshot
from nemesis.events.hashing import format_timestamp
from nemesis.events.registry import upcast
from nemesis.projections import handlers as _handlers  # noqa: F401 — registration side effect
from nemesis.projections.registry import (
    PROJECTOR_VERSION,
    ProjectedState,
    ProjectionEvent,
    project,
    state_hash,
)

#: Events between snapshots. Small enough that a seek reads a bounded number of
#: rows, large enough that a busy complaint does not write a snapshot per stage.
#: A complaint's full lifecycle is roughly a dozen events, so most entities never
#: get one — which is correct: they are cheap to replay anyway.
SNAPSHOT_INTERVAL: Final = 50


@dataclass(frozen=True, slots=True)
class ReplayResult:
    entity_type: str
    entity_id: uuid.UUID
    state: ProjectedState
    #: Highest sequence folded in. The caller needs this to know what the state
    #: is *as of* — a projection with no sequence is an assertion with no date.
    sequence: int
    events_applied: int
    snapshot_sequence: int | None

    @property
    def hash(self) -> str:
        return state_hash(self.state)


def to_projection_event(event: Event) -> ProjectionEvent:
    """Convert a stored row into what a projector may see.

    Upcasting happens here, on read, exactly once per event. The stored payload
    is never rewritten — its hash was computed over those bytes — so every
    reader pays this small cost and the chain stays verifiable forever.
    """
    payload: dict[str, Any] = upcast(event.event_type, event.event_version, event.payload)
    return ProjectionEvent(
        event_type=event.event_type,
        version=event.event_version,
        sequence=event.sequence,
        occurred_at=format_timestamp(event.occurred_at),
        payload=payload,
    )


async def replay_entity(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    entity_type: str,
    entity_id: uuid.UUID,
    upto_sequence: int | None = None,
    use_snapshots: bool = True,
) -> ReplayResult:
    """Project one entity's state as of ``upto_sequence`` (default: all of it)."""
    snapshot_sequence: int | None = None
    initial: ProjectedState | None = None
    from_sequence = 1

    if use_snapshots:
        snapshot = await _latest_snapshot(
            session,
            tenant_id=tenant_id,
            entity_type=entity_type,
            entity_id=entity_id,
            upto_sequence=upto_sequence,
        )
        if snapshot is not None:
            snapshot_sequence, initial = snapshot
            from_sequence = snapshot_sequence + 1

    statement = (
        select(Event)
        .where(
            Event.tenant_id == tenant_id,
            Event.entity_type == entity_type,
            Event.entity_id == entity_id,
            Event.sequence >= from_sequence,
        )
        .order_by(Event.sequence)
    )
    if upto_sequence is not None:
        statement = statement.where(Event.sequence <= upto_sequence)

    rows = (await session.execute(statement)).scalars().all()
    projection_events = [to_projection_event(row) for row in rows]
    state = project(entity_type, projection_events, initial=initial)

    last_sequence = rows[-1].sequence if rows else (snapshot_sequence or 0)
    return ReplayResult(
        entity_type=entity_type,
        entity_id=entity_id,
        state=state,
        sequence=last_sequence,
        events_applied=len(projection_events),
        snapshot_sequence=snapshot_sequence,
    )


async def _latest_snapshot(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    entity_type: str,
    entity_id: uuid.UUID,
    upto_sequence: int | None,
) -> tuple[int, ProjectedState] | None:
    statement = (
        select(EventSnapshot.sequence, EventSnapshot.state)
        .where(
            EventSnapshot.tenant_id == tenant_id,
            EventSnapshot.entity_type == entity_type,
            EventSnapshot.entity_id == entity_id,
            # A snapshot from an older projector is not merely stale, it is
            # wrong: it was produced by code whose output has since changed.
            EventSnapshot.projector_version == PROJECTOR_VERSION,
        )
        .order_by(EventSnapshot.sequence.desc())
        .limit(1)
    )
    if upto_sequence is not None:
        statement = statement.where(EventSnapshot.sequence <= upto_sequence)

    row = (await session.execute(statement)).one_or_none()
    if row is None:
        return None
    return int(row[0]), dict(row[1])


async def write_snapshot_if_due(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    result: ReplayResult,
    interval: int = SNAPSHOT_INTERVAL,
) -> bool:
    """Persist a snapshot when the sequence crosses an interval boundary.

    ``ON CONFLICT DO NOTHING``: two workers can replay the same entity at the
    same sequence concurrently, and both computing the identical state is not a
    conflict worth failing a request over.
    """
    if result.sequence == 0 or result.sequence % interval != 0:
        return False

    await session.execute(
        pg_insert(EventSnapshot)
        .values(
            tenant_id=tenant_id,
            entity_type=result.entity_type,
            entity_id=result.entity_id,
            sequence=result.sequence,
            state=result.state,
            state_hash=result.hash,
            projector_version=PROJECTOR_VERSION,
        )
        .on_conflict_do_nothing(constraint="uq_event_snapshots_entity_sequence")
    )
    return True
