"""Chain verification, and the integrity sweep §17.4 left on the roadmap.

The blueprint is candid that write-path chaining without a re-verification job
is "tamper-evident in principle". It is worth being precise about why that gap
matters: hashing on write proves that *this process* computed a consistent link
at the moment it wrote. It proves nothing about the row afterwards. An attacker
with database access — or, far more commonly, a well-meaning engineer running an
UPDATE to "fix" a stuck complaint — leaves a chain that only a reader who
recomputes the hashes can detect.

So verification lives here, and the sweep runs on a schedule (§17.4 closed
rather than disclosed).

**What a break tells you.** ``verify_chain`` reports the *first* sequence where
recomputation disagrees, and which component disagreed. That precision is the
difference between "the log may be corrupt" — useless to an on-call — and "event
418 for complaint X was altered; every event before it is intact", which is an
incident with a blast radius.

**What it deliberately does not do.** It never repairs. A chain that repairs
itself has no evidentiary value whatsoever, since the repair is
indistinguishable from the tamper. A break produces a finding and an alert; a
human decides what happened.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.db.models.event import Event, EventChainHead
from nemesis.events.canonical import JSONValue
from nemesis.events.hashing import GENESIS_HASH, compute_event_hash
from nemesis.tenancy.guard import TENANT_SCOPE_EXEMPT


class BreakKind(StrEnum):
    """Why a chain failed to verify. Each has a different likely cause."""

    #: Recomputing the hash from the row's own fields gives a different value.
    #: The row's content was altered after it was written.
    CONTENT_ALTERED = "content_altered"
    #: The row's ``previous_hash`` does not match its predecessor's
    #: ``event_hash``. A row was inserted, deleted, or reordered.
    LINK_BROKEN = "link_broken"
    #: Sequence numbers skip. An event was deleted outright.
    SEQUENCE_GAP = "sequence_gap"
    #: Two rows share a sequence. The head lock failed, or history was forked
    #: deliberately.
    SEQUENCE_DUPLICATE = "sequence_duplicate"
    #: The chain head disagrees with the last event in the log.
    HEAD_MISMATCH = "head_mismatch"


@dataclass(frozen=True, slots=True)
class ChainBreak:
    kind: BreakKind
    sequence: int
    detail: str


@dataclass(frozen=True, slots=True)
class ChainVerification:
    entity_type: str
    entity_id: uuid.UUID
    tenant_id: uuid.UUID
    events_checked: int
    breaks: tuple[ChainBreak, ...] = field(default_factory=tuple)

    @property
    def is_intact(self) -> bool:
        return not self.breaks

    @property
    def first_break(self) -> ChainBreak | None:
        return self.breaks[0] if self.breaks else None


async def verify_chain(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    entity_type: str,
    entity_id: uuid.UUID,
) -> ChainVerification:
    """Recompute one entity's chain from its rows and report every disagreement.

    Every break is collected rather than stopping at the first. A single altered
    row breaks its own hash *and* the link of the row after it; reporting only
    the first would understate a bulk tamper as a single-row incident, and the
    shape of the break list is itself evidence — one content break is a
    fat-fingered UPDATE, forty consecutive ones are not.
    """
    events = (
        (
            await session.execute(
                select(Event)
                .where(
                    Event.tenant_id == tenant_id,
                    Event.entity_type == entity_type,
                    Event.entity_id == entity_id,
                )
                .order_by(Event.sequence, Event.id)
            )
        )
        .scalars()
        .all()
    )

    breaks = list(_verify_rows(events))

    head = (
        await session.execute(
            select(EventChainHead.sequence, EventChainHead.head_hash).where(
                EventChainHead.tenant_id == tenant_id,
                EventChainHead.entity_type == entity_type,
                EventChainHead.entity_id == entity_id,
            )
        )
    ).one_or_none()

    if head is not None and events:
        expected_sequence, expected_hash = int(head[0]), str(head[1])
        last = events[-1]
        if expected_sequence != last.sequence or expected_hash != last.event_hash:
            breaks.append(
                ChainBreak(
                    kind=BreakKind.HEAD_MISMATCH,
                    sequence=last.sequence,
                    detail=(
                        f"head records sequence {expected_sequence} / {expected_hash[:12]}…, "
                        f"log ends at sequence {last.sequence} / {last.event_hash[:12]}…"
                    ),
                )
            )

    return ChainVerification(
        entity_type=entity_type,
        entity_id=entity_id,
        tenant_id=tenant_id,
        events_checked=len(events),
        breaks=tuple(breaks),
    )


def _verify_rows(events: Sequence[Event]) -> list[ChainBreak]:
    breaks: list[ChainBreak] = []
    previous_hash = GENESIS_HASH
    expected_sequence = 1
    seen_sequences: set[int] = set()

    for event in events:
        if event.sequence in seen_sequences:
            breaks.append(
                ChainBreak(
                    kind=BreakKind.SEQUENCE_DUPLICATE,
                    sequence=event.sequence,
                    detail=f"sequence {event.sequence} appears more than once",
                )
            )
        seen_sequences.add(event.sequence)

        if event.sequence != expected_sequence:
            breaks.append(
                ChainBreak(
                    kind=BreakKind.SEQUENCE_GAP,
                    sequence=event.sequence,
                    detail=f"expected sequence {expected_sequence}, found {event.sequence}",
                )
            )

        if event.previous_hash != previous_hash:
            breaks.append(
                ChainBreak(
                    kind=BreakKind.LINK_BROKEN,
                    sequence=event.sequence,
                    detail=(
                        f"previous_hash {event.previous_hash[:12]}… does not match the "
                        f"preceding event_hash {previous_hash[:12]}…"
                    ),
                )
            )

        payload: JSONValue = event.payload  # type: ignore[assignment]
        recomputed = compute_event_hash(
            previous_hash=event.previous_hash,
            tenant_id=event.tenant_id,
            entity_type=event.entity_type,
            entity_id=event.entity_id,
            sequence=event.sequence,
            event_type=event.event_type,
            event_version=event.event_version,
            payload=payload,
            occurred_at=event.occurred_at,
        )
        if recomputed != event.event_hash:
            breaks.append(
                ChainBreak(
                    kind=BreakKind.CONTENT_ALTERED,
                    sequence=event.sequence,
                    detail=(
                        f"stored hash {event.event_hash[:12]}… but the row's own fields "
                        f"hash to {recomputed[:12]}…"
                    ),
                )
            )

        # Continue from the row's *stored* hash, not the recomputed one. Using
        # the recomputation would cascade one altered row into a break on every
        # row after it, burying the actual point of tampering in noise.
        previous_hash = event.event_hash
        expected_sequence = event.sequence + 1

    return breaks


@dataclass(frozen=True, slots=True)
class SweepResult:
    chains_checked: int
    chains_broken: int
    findings: tuple[ChainVerification, ...]


async def sweep_chains(
    session: AsyncSession, *, limit: int | None = None, tenant_id: uuid.UUID | None = None
) -> SweepResult:
    """Verify many chains — the scheduled integrity job.

    Cross-tenant by design when no tenant is given: tampering is not something
    one customer is asked to detect on their own data, and an integrity sweep
    that only ran inside a tenant scope would miss exactly the cross-tenant row
    moves that the widened hash preimage was added to catch (ADR-0010).
    """
    # An integrity sweep with no tenant argument is cross-tenant by definition.
    # Scoping it would make each customer responsible for detecting tampering in
    # their own data, and would blind it to exactly the cross-tenant row moves
    # the widened hash preimage exists to catch (ADR-0010). Each chain is then
    # verified individually, and that verification *is* tenant-scoped.
    # tenant-scope-exempt: enumerates chains across all tenants for the sweep
    statement = select(
        EventChainHead.tenant_id, EventChainHead.entity_type, EventChainHead.entity_id
    ).where(EventChainHead.sequence > 0)
    if tenant_id is not None:
        statement = statement.where(EventChainHead.tenant_id == tenant_id)
    else:
        statement = statement.execution_options(**{TENANT_SCOPE_EXEMPT: True})
    statement = statement.order_by(EventChainHead.updated_at.desc())
    if limit is not None:
        statement = statement.limit(limit)

    chains = (await session.execute(statement)).all()

    findings: list[ChainVerification] = []
    for chain_tenant, entity_type, entity_id in chains:
        result = await verify_chain(
            session, tenant_id=chain_tenant, entity_type=entity_type, entity_id=entity_id
        )
        if not result.is_intact:
            findings.append(result)

    return SweepResult(
        chains_checked=len(chains), chains_broken=len(findings), findings=tuple(findings)
    )


async def count_events(session: AsyncSession, *, tenant_id: uuid.UUID) -> int:
    """Event count for one tenant — used by tests and the support console."""
    return int(
        (
            await session.execute(
                select(func.count()).select_from(Event).where(Event.tenant_id == tenant_id)
            )
        ).scalar_one()
    )
