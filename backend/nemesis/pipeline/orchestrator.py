"""Run one pipeline stage atomically, or degrade it honestly.

Everything a stage does lands in a single transaction: the events it emits, the
projections they imply, and the outbox rows that will publish them. §9.1 already
requires the state change and its event to commit together; the outbox row joins
that unit for the same reason, one step further out — a notification that
survives a rollback is a claim about the world the database never agreed to.

**How a redelivery is made a provable no-op.** Every event a stage emits is
appended under the key ``pipeline:<stage>:<complaint_id>:<index>``. On a
redelivery the first append matches an existing key, ``EventStore`` returns the
original event instead of writing, and this module treats that single fact as
proof the stage already completed: it abandons the transaction and moves on.

Checking only the *first* append is deliberate and is the whole reason the rule
is stated as one sentence. Suppose a stage emitted two events on its first run
and a redelivery is allowed to append each under its own key: if the provider is
not perfectly deterministic and emits three this time, slots 0 and 1 are
no-ops — and slot 2 appends a brand-new event onto a chain that already moved
on. The stage would have half-run twice. Bailing on the first redelivery makes
the unit of idempotency the *stage*, not the event, which is the unit Celery
actually redelivers.

A stage that emits nothing has no first append and therefore no guard. That is
recorded rather than papered over: a stage that emits no events changed no
state, so running it twice changes nothing twice.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.db.models.outbox import PipelineDeadLetter
from nemesis.db.session import session_scope
from nemesis.domain.lifecycle import DegradationFallback, EntityType
from nemesis.events.store import AppendedEvent, EventStore
from nemesis.observability import metrics
from nemesis.observability.logging import get_logger
from nemesis.outbox import writer as outbox
from nemesis.pipeline.stages import (
    StageContext,
    StagePermanentError,
    StageUnavailableError,
    spec_for,
)
from nemesis.projections.replay import replay_entity, write_snapshot_if_due
from nemesis.projections.writer import is_materialised, write_projection
from nemesis.tenancy.context import tenant_scope

log = get_logger(__name__)

#: Prefix on every idempotency key this module mints. Namespaced so a pipeline
#: key can never collide with the submission key an API client chose — a citizen
#: supplying `pipeline:dedup:...` as their own key would otherwise be able to
#: suppress a stage.
IDEMPOTENCY_PREFIX: Final = "pipeline"

COMPLAINT: Final = EntityType.COMPLAINT.value


class _StageAlreadyRanError(Exception):
    """Internal. Raised to abandon the transaction on a detected redelivery.

    An exception rather than an early return because the abandonment has to
    reach ``session_scope`` as a rollback: a provider is allowed to write
    through the supplied session, and a redelivered run's writes must not
    survive just because it emitted no *events*.
    """


@dataclass(frozen=True, slots=True)
class StageExecution:
    """What running one stage did."""

    stage: str
    ran: bool
    #: True when a redelivery was detected and nothing was written.
    already_ran: bool
    halted: bool
    halt_reason: str | None
    events_appended: int
    next_stage: str | None


async def execute_stage(
    *,
    tenant_id: uuid.UUID,
    complaint_id: uuid.UUID,
    stage: str,
    correlation_id: str | None = None,
    attempt: int = 1,
) -> StageExecution:
    """Run ``stage`` for one complaint in a single transaction.

    Raises ``StageUnavailableError`` when no provider is registered and
    ``StageError`` when a provider fails. Both are the caller's decision to
    make — the Celery task owns the retry budget, because it is the thing being
    retried.
    """
    from nemesis.pipeline.stages import provider_for  # late: providers register at import

    spec = spec_for(stage)
    provider = provider_for(stage)
    if provider is None:
        raise StageUnavailableError(
            f"no provider registered for stage '{stage}' "
            f"(owned by {spec.owner_phase}, Blueprint §{spec.blueprint})"
        )

    started = datetime.now(tz=UTC)
    try:
        execution = await _run(
            tenant_id=tenant_id,
            complaint_id=complaint_id,
            stage=stage,
            provider=provider,
            correlation_id=correlation_id,
            attempt=attempt,
        )
    except _StageAlreadyRanError:
        metrics.pipeline_stage_duration_seconds.labels(
            stage=stage, outcome=metrics.StageOutcome.OK.value
        ).observe(_elapsed(started))
        log.info(
            "pipeline_stage_redelivered",
            stage=stage,
            complaint_id=str(complaint_id),
            note="idempotency key matched; nothing appended",
        )
        return StageExecution(
            stage=stage,
            ran=False,
            already_ran=True,
            halted=False,
            halt_reason=None,
            events_appended=0,
            next_stage=spec.next_stage.value if spec.next_stage else None,
        )
    except Exception:
        metrics.pipeline_stage_duration_seconds.labels(
            stage=stage, outcome=metrics.StageOutcome.FAILED.value
        ).observe(_elapsed(started))
        raise

    metrics.pipeline_stage_duration_seconds.labels(
        stage=stage, outcome=metrics.StageOutcome.OK.value
    ).observe(_elapsed(started))
    return execution


async def _run(
    *,
    tenant_id: uuid.UUID,
    complaint_id: uuid.UUID,
    stage: str,
    provider: Any,
    correlation_id: str | None,
    attempt: int,
) -> StageExecution:
    spec = spec_for(stage)

    with tenant_scope(tenant_id):
        async with session_scope() as session:
            replay = await replay_entity(
                session, tenant_id=tenant_id, entity_type=COMPLAINT, entity_id=complaint_id
            )
            if replay.sequence == 0:
                # No history at all. Not retryable: the submission transaction
                # either committed or it did not, and a later attempt cannot
                # make a complaint that was never written appear.
                raise StagePermanentError(
                    f"complaint {complaint_id} has no events; nothing to process"
                )

            result = await provider(
                StageContext(
                    session=session,
                    tenant_id=tenant_id,
                    complaint_id=complaint_id,
                    state=replay.state,
                    correlation_id=correlation_id,
                    attempt=attempt,
                )
            )

            store = EventStore(session)
            appended: list[AppendedEvent] = []
            for index, emitted in enumerate(result.emitted):
                event = await store.append(
                    entity_id=emitted.entity_id,
                    event_type=emitted.event_type,
                    payload=emitted.payload,
                    tenant_id=tenant_id,
                    correlation_id=correlation_id,
                    idempotency_key=f"{IDEMPOTENCY_PREFIX}:{stage}:{complaint_id}:{index}",
                )
                if index == 0 and event.was_redelivery:
                    raise _StageAlreadyRanError
                appended.append(event)

            await _materialise_and_enqueue(session, tenant_id=tenant_id, appended=appended)

    for event in appended:
        metrics.pipeline_events_total.labels(event_type=event.event_type).inc()

    if spec.next_stage is None and appended:
        _observe_end_to_end(replay.state)

    return StageExecution(
        stage=stage,
        ran=True,
        already_ran=False,
        halted=result.halt,
        halt_reason=result.halt_reason,
        events_appended=len(appended),
        next_stage=None if result.halt or spec.next_stage is None else spec.next_stage.value,
    )


async def _materialise_and_enqueue(
    session: AsyncSession, *, tenant_id: uuid.UUID, appended: list[AppendedEvent]
) -> None:
    """Update every touched projection, then enqueue every event for fan-out.

    Projections first, publishes second, and both before the commit. A client
    that receives ``cluster_match_found`` and immediately fetches the cluster
    must not find the pre-merge row — which is what would happen if the publish
    were enqueued in this transaction and the projection written in the next.
    """
    touched: dict[tuple[str, uuid.UUID], None] = {}
    for event in appended:
        touched[(event.entity_type, event.entity_id)] = None

    for entity_type, entity_id in touched:
        if not is_materialised(entity_type):
            continue
        projection = await replay_entity(
            session, tenant_id=tenant_id, entity_type=entity_type, entity_id=entity_id
        )
        await write_projection(session, tenant_id=tenant_id, result=projection)
        await write_snapshot_if_due(session, tenant_id=tenant_id, result=projection)

    for event in appended:
        await outbox.enqueue(session, event)


async def record_degradation(
    *,
    tenant_id: uuid.UUID,
    complaint_id: uuid.UUID,
    stage: str,
    failure_mode: str,
    attempts: int,
    correlation_id: str | None = None,
) -> bool:
    """Take the stage's declared fallback, on the record.

    Three things happen, and each is load-bearing:

    1. ``pipeline_stage_degraded`` on the **complaint's own chain**, which is
       what moves the report to ``pending_classification`` and what makes "this
       report was processed without a classifier" answerable six months later
       from the report's own history.
    2. A **dead letter row**, so the parked complaint is queryable. §24.2's
       "degrades rather than loses" is only true if somebody can find it.
    3. ``system_degradation`` on the system chain, plus the metric, which is the
       deployment-level view: what broke, not which reports it touched.

    Returns ``False`` if this degradation was already recorded — the retry that
    exhausted the budget can itself be redelivered.
    """
    spec = spec_for(stage)
    recorded = False

    with tenant_scope(tenant_id):
        async with session_scope() as session:
            event = await EventStore(session).append(
                entity_id=complaint_id,
                event_type="pipeline_stage_degraded",
                payload={
                    "stage": stage,
                    "failure_mode": failure_mode,
                    "fallback_taken": spec.fallback.value,
                    "attempts": attempts,
                    "correlation_id": correlation_id,
                },
                tenant_id=tenant_id,
                correlation_id=correlation_id,
                idempotency_key=f"{IDEMPOTENCY_PREFIX}:degraded:{stage}:{complaint_id}",
            )
            if not event.was_redelivery:
                recorded = True
                await _materialise_and_enqueue(session, tenant_id=tenant_id, appended=[event])

            await _upsert_dead_letter(
                session,
                tenant_id=tenant_id,
                complaint_id=complaint_id,
                stage=stage,
                attempts=attempts,
                failure_mode=failure_mode,
                correlation_id=correlation_id,
            )

    if recorded:
        metrics.pipeline_stage_degraded_total.labels(
            stage=stage, fallback=spec.fallback.value
        ).inc()
        metrics.pipeline_stage_duration_seconds.labels(
            stage=stage, outcome=metrics.StageOutcome.DEGRADED.value
        ).observe(0.0)
        log.warning(
            "pipeline_stage_degraded",
            stage=stage,
            complaint_id=str(complaint_id),
            failure_mode=failure_mode,
            fallback=spec.fallback.value,
            attempts=attempts,
            owner_phase=spec.owner_phase,
            runbook="docs/runbooks/pipeline-stage-failures.md",
        )
        await _record_system_degradation(
            stage=stage,
            failure_mode=failure_mode,
            spec_fallback=spec.fallback,
            correlation_id=correlation_id,
        )

    return recorded


async def _record_system_degradation(
    *,
    stage: str,
    failure_mode: str,
    spec_fallback: DegradationFallback,
    correlation_id: str | None,
) -> None:
    """The deployment-level half. Never allowed to mask the failure it describes.

    Wrapped because this writes to a *different* tenant on a *different* chain
    in a *different* transaction, and if that write fails — the reserved system
    tenant missing, the database gone — the complaint-level degradation has
    already been recorded successfully and must not be undone by a bookkeeping
    error in the audit trail for it.
    """
    from nemesis.pipeline.integrity import record_system_degradation

    metrics.system_degradation_total.labels(
        dependency=metrics.Dependency.DATABASE.value
        if "database" in failure_mode.lower()
        else "pipeline",
        reason=failure_mode,
    ).inc()
    try:
        await record_system_degradation(
            component=f"pipeline.{stage}",
            failure_mode=failure_mode,
            fallback_taken=spec_fallback.value,
            correlation_id=correlation_id,
        )
    except Exception as exc:  # pragma: no cover — requires the system chain to be broken
        log.error(
            "system_degradation_record_failed",
            stage=stage,
            error_type=type(exc).__name__,
            consequence="complaint-level degradation is recorded; the system chain is not",
        )


async def _upsert_dead_letter(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    complaint_id: uuid.UUID,
    stage: str,
    attempts: int,
    failure_mode: str,
    correlation_id: str | None,
) -> None:
    """One open dead letter per (complaint, stage), updated rather than duplicated."""
    await session.execute(
        pg_insert(PipelineDeadLetter)
        .values(
            tenant_id=tenant_id,
            entity_type=COMPLAINT,
            entity_id=complaint_id,
            stage=stage,
            task_name=f"nemesis.pipeline.{stage}",
            attempts=attempts,
            failure_mode=failure_mode,
            last_error=failure_mode,
            correlation_id=correlation_id,
        )
        .on_conflict_do_update(
            index_elements=[
                PipelineDeadLetter.tenant_id,
                PipelineDeadLetter.entity_id,
                PipelineDeadLetter.stage,
            ],
            index_where=PipelineDeadLetter.resolved_at.is_(None),
            set_={
                "attempts": attempts,
                "failure_mode": failure_mode,
                "last_error": failure_mode,
                "updated_at": datetime.now(tz=UTC),
            },
        )
    )


def _observe_end_to_end(state: dict[str, Any]) -> None:
    """Record submit → work-order-created against the §27.1 30-second budget.

    Measured from the complaint's own ``reported_at`` rather than from when this
    task started, because the budget is a promise to the citizen about the whole
    journey — the part spent waiting in a queue is exactly the part a per-stage
    timer would hide.
    """
    reported = state.get("reported_at")
    if not isinstance(reported, str):
        return
    try:
        started = datetime.fromisoformat(reported.replace("Z", "+00:00"))
    except ValueError:  # pragma: no cover — projections write format_timestamp output
        return
    metrics.pipeline_stage_duration_seconds.labels(
        stage=metrics.PipelineStage.END_TO_END.value, outcome=metrics.StageOutcome.OK.value
    ).observe(max(0.0, (datetime.now(tz=UTC) - started).total_seconds()))


def _elapsed(started: datetime) -> float:
    return (datetime.now(tz=UTC) - started).total_seconds()


__all__ = [
    "IDEMPOTENCY_PREFIX",
    "StageExecution",
    "execute_stage",
    "record_degradation",
]
