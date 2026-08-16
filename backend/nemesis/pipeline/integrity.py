"""Scheduled integrity sweep and partition maintenance.

The sweep closes the gap §17.4 lists as ROADMAP. Write-path chaining proves that
*the writing process* computed a consistent link at the moment it wrote; it
proves nothing about the row afterwards. Only a reader that recomputes the hashes
can detect an ``UPDATE`` run after the fact — whether by an attacker or, far more
commonly, by an engineer "fixing" a stuck complaint at 2am.

Both tasks run on the ``io`` queue. Neither holds model weights, and neither may
compete with the ``safety`` queue for a worker slot.

**These tasks never repair anything.** A chain that repairs itself has no
evidentiary value at all, because the repair is indistinguishable from the
tamper. A finding produces a metric, a structured log, and an event on the
``system`` chain; a human decides what happened.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog

from nemesis.db.session import session_scope
from nemesis.events.partitions import (
    default_partition_rows,
    detachable_partitions,
    ensure_partitions,
    retention_horizon,
)
from nemesis.events.store import EventStore
from nemesis.events.verify import sweep_chains
from nemesis.observability.metrics import (
    event_chain_breaks_total,
    event_chains_verified_total,
    event_default_partition_rows,
)
from nemesis.tenancy.context import tenant_scope
from nemesis.worker.celery_app import QUEUE_IO, celery_app

logger = structlog.get_logger(__name__)

#: Chains verified per sweep. A bound, not a target: verification reads every
#: event for each chain, and an unbounded sweep on a year of history would hold
#: connections long enough to matter to the write path it is protecting.
SWEEP_BATCH_SIZE = 500

#: Months of event history kept online before a partition becomes eligible for
#: archival. Twelve because Phase 21's replay advertises a year, and detaching a
#: partition the time machine still reads would break a shipped feature.
RETENTION_MONTHS = 12

#: The tenant a system-level finding is recorded against. Integrity findings and
#: degradations belong to the deployment rather than to any customer, so they
#: get a fixed, reserved tenant rather than being attributed to whichever tenant
#: happened to own the broken chain — which would put an operational failure in
#: a customer's audit trail.
SYSTEM_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@celery_app.task(name="nemesis.integrity.sweep_chains", queue=QUEUE_IO, bind=False)
def sweep_chain_integrity(limit: int = SWEEP_BATCH_SIZE) -> dict[str, Any]:
    """Verify a batch of chains and report any that do not recompute."""
    return asyncio.run(_sweep_chain_integrity(limit))


async def _sweep_chain_integrity(limit: int) -> dict[str, Any]:
    async with session_scope() as session:
        result = await sweep_chains(session, limit=limit)

    event_chains_verified_total.inc(result.chains_checked)
    event_chain_breaks_total.inc(result.chains_broken)

    for finding in result.findings:
        first = finding.first_break
        logger.error(
            "event_chain_integrity_break",
            tenant_id=str(finding.tenant_id),
            entity_type=finding.entity_type,
            entity_id=str(finding.entity_id),
            events_checked=finding.events_checked,
            break_count=len(finding.breaks),
            # The exact offset is the whole value of the finding: "the log may
            # be corrupt" is useless to an on-call, "event 418 was altered and
            # everything before it is intact" is an incident with a blast radius.
            first_break_kind=None if first is None else str(first.kind),
            first_break_sequence=None if first is None else first.sequence,
            first_break_detail=None if first is None else first.detail,
            runbook="docs/runbooks/event-chain-integrity.md",
        )

    return {
        "chains_checked": result.chains_checked,
        "chains_broken": result.chains_broken,
        "swept_at": datetime.now(tz=UTC).isoformat(),
    }


@celery_app.task(name="nemesis.integrity.maintain_partitions", queue=QUEUE_IO, bind=False)
def maintain_event_partitions() -> dict[str, Any]:
    """Keep the partition window ahead of the clock and watch the default."""
    return asyncio.run(_maintain_event_partitions())


async def _maintain_event_partitions() -> dict[str, Any]:
    async with session_scope() as session:
        created = await ensure_partitions(session)
        stranded = await default_partition_rows(session)
        now = datetime.now(tz=UTC)
        eligible = await detachable_partitions(
            session, older_than=retention_horizon(now, retain_months=RETENTION_MONTHS)
        )

    event_default_partition_rows.set(stranded)

    if created:
        logger.info("event_partitions_created", partitions=created)
    if stranded:
        # Not an error yet — the rows are readable and correct. It is a warning
        # because attaching the partition that should have held them now needs a
        # scan and an exclusive lock, so the remedy gets more expensive daily.
        logger.warning(
            "event_default_partition_non_empty",
            rows=stranded,
            runbook="docs/runbooks/event-partition-maintenance.md",
        )
    if eligible:
        # Reported, never acted on. Retention on an append-only civic record is
        # a decision with legal weight (§22.4); Phase 26 adds the approval step
        # and the proof of deletion that make detaching one defensible.
        logger.info(
            "event_partitions_eligible_for_archival",
            partitions=eligible,
            retention_months=RETENTION_MONTHS,
        )

    return {
        "created": created,
        "rows_in_default_partition": stranded,
        "eligible_for_archival": eligible,
    }


async def record_system_degradation(
    *, component: str, failure_mode: str, fallback_taken: str, correlation_id: str | None = None
) -> None:
    """Append a ``system_degradation`` event (§24.2 failure policy).

    In the log rather than only in metrics because a complaint processed during
    a degradation was processed *differently*, and six months later the only way
    to know that is for the log to say so.
    """
    # Nested rather than combined in one `async with`: `tenant_scope` is a plain
    # context manager, and mixing it into an `async with` list fails at runtime
    # with a confusing `__aenter__` error.
    async with session_scope() as session:
        with tenant_scope(SYSTEM_TENANT_ID):
            await EventStore(session).append(
                entity_id=SYSTEM_TENANT_ID,
                event_type="system_degradation",
                payload={
                    "component": component,
                    "failure_mode": failure_mode,
                    "fallback_taken": fallback_taken,
                    "correlation_id": correlation_id,
                },
            )


# The beat schedule for these two tasks lives in `nemesis.worker.celery_app`,
# keyed by task name. Setting it here as an import side effect made the schedule
# depend on whether anything had imported this module — and nothing had.
