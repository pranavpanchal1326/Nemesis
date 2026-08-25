"""The Celery half of the pipeline: retry budgets, dead-lettering, and the hop.

The split from ``orchestrator`` is the useful one. This module knows about
queues, attempts, and backoff; the orchestrator knows about transactions,
events, and projections. Neither imports the other's concerns, which is why the
orchestrator is testable without a broker — the Phase 3 gate runs it directly.

**Why each stage enqueues its successor instead of a Celery chain.** A chain
fixes the whole graph at enqueue time. §11.2 requires a safety-triggered report
to *bypass the scoring pipeline entirely*, and inside a chain the only way to
express that is for the remaining tasks to run and decline to act — which is not
a bypass, it is four no-ops holding queue slots behind a danger signal. Hopping
also means a redelivered task carries its own complete context, rather than a
position in a structure the broker is holding.

**Retries are the task's job, degradation is the last attempt's job.** Celery's
``autoretry_for`` would retry and then simply fail, leaving the complaint in
whatever state it was in — which is precisely the "silently lost" outcome §24.2
forbids. So retries are explicit, the budget is read from the stage declaration,
and the attempt that exhausts it calls ``record_degradation`` before it stops.
"""

from __future__ import annotations

import random
import uuid
from typing import Any

from celery import Task

from nemesis.observability import metrics
from nemesis.observability.logging import get_logger, set_correlation_id
from nemesis.pipeline.orchestrator import execute_stage, record_degradation
from nemesis.pipeline.stages import (
    FIRST_STAGE,
    StageAbstainedError,
    StagePermanentError,
    StageUnavailableError,
    spec_for,
)
from nemesis.worker.celery_app import QUEUE_IO, celery_app
from nemesis.worker.loop import run_async

log = get_logger(__name__)

#: Jitter applied to every backoff, as a fraction of the computed delay. Without
#: it, a dependency that comes back up is hit simultaneously by every task that
#: failed against it — the retry storm that turns a recovery into a second
#: outage.
_JITTER = 0.25

TASK_RUN_STAGE = "nemesis.pipeline.run_stage"


@celery_app.task(name=TASK_RUN_STAGE, bind=True, queue=QUEUE_IO)
def run_stage(
    self: Task,
    tenant_id: str,
    complaint_id: str,
    stage: str,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """Run one stage, then enqueue the next — or degrade and stop.

    Arguments are strings, not UUIDs: the broker serialises to JSON, and a task
    signature whose round trip depends on a custom serialiser is one that breaks
    the first time somebody inspects the queue with a different tool.
    """
    set_correlation_id(correlation_id)
    # `run_async`, never `asyncio.run` — see nemesis/worker/loop.py. A new loop
    # per task hands the second task a pooled asyncpg connection owned by the
    # first task's closed loop.
    return run_async(
        _run_stage(
            task=self,
            tenant_id=uuid.UUID(tenant_id),
            complaint_id=uuid.UUID(complaint_id),
            stage=stage,
            correlation_id=correlation_id,
        )
    )


async def _run_stage(
    *,
    task: Task,
    tenant_id: uuid.UUID,
    complaint_id: uuid.UUID,
    stage: str,
    correlation_id: str | None,
) -> dict[str, Any]:
    spec = spec_for(stage)
    # `retries` is 0 on the first delivery, so attempts are 1-based here to match
    # the way `max_attempts` reads in the stage declaration.
    attempt = int(task.request.retries or 0) + 1
    if attempt > 1:
        metrics.pipeline_stage_retries_total.labels(stage=stage).inc()

    try:
        execution = await execute_stage(
            tenant_id=tenant_id,
            complaint_id=complaint_id,
            stage=stage,
            correlation_id=correlation_id,
            attempt=attempt,
        )
    except StageUnavailableError as exc:
        # Not retried. The provider will still be unregistered in thirty
        # seconds, and burning the budget to reach a conclusion available now
        # only delays the §24.2 fallback by minutes.
        await _degrade(
            tenant_id=tenant_id,
            complaint_id=complaint_id,
            stage=stage,
            failure_mode="provider_unavailable",
            attempts=attempt,
            correlation_id=correlation_id,
        )
        log.info(
            "pipeline_stage_unavailable",
            stage=stage,
            complaint_id=str(complaint_id),
            owner_phase=spec.owner_phase,
            detail=str(exc),
        )
        return _report(stage, "degraded", next_stage=None)
    except StageAbstainedError as exc:
        # Counted and logged as a *decision*, not an error. The stage ran, the
        # models loaded, and the answer is "not confident enough to claim a
        # category" — which §24.2 already has a shipped path for, and which a
        # human resolves from the review queue in seconds.
        await _degrade(
            tenant_id=tenant_id,
            complaint_id=complaint_id,
            stage=stage,
            # A label, not a sentence. The reason is long — it names the
            # missing prompt sets and tells a tenant how to add them — and it
            # belongs in `last_error`, which is `Text`. Packing it into
            # `failure_mode` clamped to 200 against a `varchar(128)`, so every
            # abstained classification failed to record its own dead letter.
            failure_mode="abstained",
            detail=str(exc),
            attempts=attempt,
            correlation_id=correlation_id,
        )
        log.info(
            "pipeline_stage_abstained",
            stage=stage,
            complaint_id=str(complaint_id),
            reason=str(exc),
            note="the stage declined to answer; the report is parked for a human",
        )
        return _report(stage, "degraded", next_stage=None)
    except StagePermanentError as exc:
        await _degrade(
            tenant_id=tenant_id,
            complaint_id=complaint_id,
            stage=stage,
            failure_mode=f"permanent:{type(exc).__name__}",
            attempts=attempt,
            correlation_id=correlation_id,
        )
        return _report(stage, "degraded", next_stage=None)
    except Exception as exc:
        if attempt < spec.max_attempts:
            delay = _backoff(spec.retry_backoff_seconds, attempt)
            log.warning(
                "pipeline_stage_retrying",
                stage=stage,
                complaint_id=str(complaint_id),
                attempt=attempt,
                max_attempts=spec.max_attempts,
                retry_in_seconds=round(delay, 2),
                error_type=type(exc).__name__,
            )
            raise task.retry(exc=exc, countdown=delay, max_retries=spec.max_attempts - 1) from exc
        await _degrade(
            tenant_id=tenant_id,
            complaint_id=complaint_id,
            stage=stage,
            failure_mode=f"retry_budget_exhausted:{type(exc).__name__}",
            attempts=attempt,
            correlation_id=correlation_id,
        )
        return _report(stage, "degraded", next_stage=None)

    if execution.halted:
        log.info(
            "pipeline_halted",
            stage=stage,
            complaint_id=str(complaint_id),
            reason=execution.halt_reason,
        )
        return _report(stage, "halted", next_stage=None)

    if execution.next_stage is not None:
        _enqueue(
            tenant_id=tenant_id,
            complaint_id=complaint_id,
            stage=execution.next_stage,
            correlation_id=correlation_id,
        )

    return _report(
        stage,
        "already_ran" if execution.already_ran else "ok",
        next_stage=execution.next_stage,
    )


async def _degrade(
    *,
    tenant_id: uuid.UUID,
    complaint_id: uuid.UUID,
    stage: str,
    failure_mode: str,
    attempts: int,
    correlation_id: str | None,
    detail: str | None = None,
) -> None:
    """Record the fallback, then continue the pipeline if the stage allows it.

    ``continue_on_degrade`` is read from the declaration rather than decided
    here: whether a missing dedup pass should stop everything is a property of
    dedup (§14.3 says an unmerged duplicate is the safe error), not a property
    of the error handling.
    """
    await record_degradation(
        tenant_id=tenant_id,
        complaint_id=complaint_id,
        stage=stage,
        failure_mode=failure_mode,
        attempts=attempts,
        correlation_id=correlation_id,
        detail=detail,
    )
    spec = spec_for(stage)
    if spec.continue_on_degrade and spec.next_stage is not None:
        _enqueue(
            tenant_id=tenant_id,
            complaint_id=complaint_id,
            stage=spec.next_stage.value,
            correlation_id=correlation_id,
        )


def _enqueue(
    *,
    tenant_id: uuid.UUID,
    complaint_id: uuid.UUID,
    stage: str,
    correlation_id: str | None,
) -> None:
    """Dispatch a stage onto the queue its declaration names."""
    spec = spec_for(stage)
    run_stage.apply_async(
        args=[str(tenant_id), str(complaint_id), stage, correlation_id],
        queue=spec.queue,
    )


def dispatch_pipeline(
    *, tenant_id: uuid.UUID, complaint_id: uuid.UUID, correlation_id: str | None = None
) -> None:
    """Entry point: start a newly submitted complaint through the graph.

    Called **after** the submission transaction commits, never inside it. A task
    enqueued inside the transaction can be picked up by a worker before the
    commit lands, at which point the worker replays a complaint that does not
    exist yet — the read-your-own-write race that makes "enqueue in the handler"
    fail only under load.
    """
    _enqueue(
        tenant_id=tenant_id,
        complaint_id=complaint_id,
        stage=FIRST_STAGE.value,
        correlation_id=correlation_id,
    )


def _backoff(base: float, attempt: int) -> float:
    exponential: float = base * float(2 ** (attempt - 1))
    return exponential * (1.0 + random.uniform(-_JITTER, _JITTER))


def _report(stage: str, outcome: str, *, next_stage: str | None) -> dict[str, Any]:
    return {"stage": stage, "outcome": outcome, "next_stage": next_stage}
