"""Phase 3 gate: the pipeline emits the §10 sequence, in order, on a valid chain.

**About the providers in this module.** Phase 3 owns orchestration, not
perception — the classifier is Phase 9's and the dedup engine is Phase 10's. So
the gate needs something at the provider seam, and these are it: small, explicit
recorders that emit the events the real stages will emit.

That is deliberately not the same thing as shipping a fake classifier. Nothing
here is registered outside a test; ``provider_scope`` unregisters on exit; and
the assertions are about the *orchestrator* — ordering, chain validity,
transactionality, idempotency, degradation — every one of which is Phase 3's own
work and none of which depends on what a provider computes.
"""

from __future__ import annotations

import uuid
from contextlib import ExitStack

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from nemesis.db.models.complaint import Complaint, ComplaintCluster
from nemesis.db.models.outbox import OutboxMessage, PipelineDeadLetter
from nemesis.db.models.work_order import WorkOrder
from nemesis.domain.lifecycle import ComplaintStatus, DegradationFallback, EntityType
from nemesis.events.store import EventStore
from nemesis.events.verify import verify_chain
from nemesis.ingest.service import Submission, submit
from nemesis.observability.metrics import PipelineStage
from nemesis.pipeline.orchestrator import execute_stage, record_degradation
from nemesis.pipeline.stages import (
    PIPELINE_SEQUENCE,
    EmittedEvent,
    StageContext,
    StageError,
    StageResult,
    StageUnavailableError,
    provider_scope,
    spec_for,
)
from nemesis.tenancy.context import tenant_scope
from tests.conftest import postgres_required

pytestmark = [postgres_required, pytest.mark.integration]


# ---------------------------------------------------------------------------
# Provider doubles, one per stage, emitting what the real stage will emit.
# ---------------------------------------------------------------------------


def _safety_provider() -> object:
    async def run(ctx: StageContext) -> StageResult:
        return StageResult(
            emitted=[
                EmittedEvent(
                    entity_type=EntityType.COMPLAINT,
                    entity_id=ctx.complaint_id,
                    event_type="exif_check_completed",
                    payload={"exif_present": True, "distance_meters": 4.0, "trust_delta": 0.2},
                )
            ]
        )

    return run


def _classification_provider() -> object:
    async def run(ctx: StageContext) -> StageResult:
        return StageResult(
            emitted=[
                EmittedEvent(
                    entity_type=EntityType.COMPLAINT,
                    entity_id=ctx.complaint_id,
                    event_type="classification_scored",
                    payload={
                        "category": "elevator_fault",
                        "confidence": 0.91,
                        "model_id": "test-model",
                        "prompt_set_version": "test-prompts-v1",
                    },
                )
            ]
        )

    return run


def _dedup_provider(cluster_id: uuid.UUID) -> object:
    async def run(ctx: StageContext) -> StageResult:
        # Emits onto the *cluster* chain, which is the interesting case for the
        # orchestrator: one stage writing two entities in one transaction.
        return StageResult(
            emitted=[
                EmittedEvent(
                    entity_type=EntityType.COMPLAINT_CLUSTER,
                    entity_id=cluster_id,
                    event_type="cluster_created",
                    payload={
                        "seed_complaint_id": str(ctx.complaint_id),
                        "latitude": ctx.state["latitude"],
                        "longitude": ctx.state["longitude"],
                    },
                )
            ]
        )

    return run


def _severity_provider() -> object:
    async def run(ctx: StageContext) -> StageResult:
        return StageResult(
            emitted=[
                EmittedEvent(
                    entity_type=EntityType.COMPLAINT,
                    entity_id=ctx.complaint_id,
                    event_type="severity_scored",
                    payload={
                        "score": 7.4,
                        "components": {"visual_damage": 0.8},
                        "weights": {"visual_damage": 1.0},
                        "policy_version": "test-rubric-v1",
                    },
                )
            ]
        )

    return run


def _routing_provider(cluster_id: uuid.UUID, work_order_id: uuid.UUID) -> object:
    async def run(ctx: StageContext) -> StageResult:
        return StageResult(
            emitted=[
                EmittedEvent(
                    entity_type=EntityType.WORK_ORDER,
                    entity_id=work_order_id,
                    event_type="work_order_created",
                    payload={"cluster_id": str(cluster_id), "routing_rule_id": "test-rule"},
                )
            ]
        )

    return run


def _all_providers(cluster_id: uuid.UUID, work_order_id: uuid.UUID) -> ExitStack:
    """Register the whole graph for the duration of a block."""
    stack = ExitStack()
    stack.enter_context(provider_scope(PipelineStage.SAFETY_CHECK, _safety_provider()))
    stack.enter_context(provider_scope(PipelineStage.CLASSIFICATION, _classification_provider()))
    stack.enter_context(provider_scope(PipelineStage.DEDUP, _dedup_provider(cluster_id)))
    stack.enter_context(provider_scope(PipelineStage.SEVERITY_SCORING, _severity_provider()))
    stack.enter_context(
        provider_scope(PipelineStage.ROUTING, _routing_provider(cluster_id, work_order_id))
    )
    return stack


async def _submit(tenant_id: uuid.UUID, **overrides: object) -> uuid.UUID:
    receipt = await submit(
        tenant_id=tenant_id,
        submission=Submission(
            latitude=18.5204,
            longitude=73.8567,
            description_text="lift stuck between floors",
            photo_uri="nemesis+quarantine://ab/abc.jpg",
            **overrides,  # type: ignore[arg-type]
        ),
        correlation_id="test-correlation",
    )
    return receipt.complaint_id


async def _run_to_completion(tenant_id: uuid.UUID, complaint_id: uuid.UUID) -> list[str]:
    """Walk the graph the way the Celery hop does, recording what ran."""
    ran: list[str] = []
    stage: str | None = PIPELINE_SEQUENCE[0].value
    while stage is not None:
        execution = await execute_stage(tenant_id=tenant_id, complaint_id=complaint_id, stage=stage)
        ran.append(stage)
        stage = execution.next_stage
    return ran


# ---------------------------------------------------------------------------
# Gate clause 1 — one submission emits the full sequence, in order, valid chain
# ---------------------------------------------------------------------------


async def test_submission_emits_the_full_event_sequence_in_order_on_a_valid_chain(
    bound_session: None, tenant_id: uuid.UUID
) -> None:
    cluster_id, work_order_id = uuid.uuid4(), uuid.uuid4()
    complaint_id = await _submit(tenant_id)

    with _all_providers(cluster_id, work_order_id):
        ran = await _run_to_completion(tenant_id, complaint_id)

    assert ran == [stage.value for stage in PIPELINE_SEQUENCE]

    from nemesis.db.session import session_scope

    with tenant_scope(tenant_id):
        async with session_scope() as session:
            store = EventStore(session)

            complaint_events = [
                event.event_type
                for event in await store.read_stream(
                    entity_type=EntityType.COMPLAINT.value, entity_id=complaint_id
                )
            ]
            assert complaint_events == [
                "complaint_submitted",
                "exif_check_completed",
                "classification_scored",
                "severity_scored",
            ]

            cluster_events = [
                event.event_type
                for event in await store.read_stream(
                    entity_type=EntityType.COMPLAINT_CLUSTER.value, entity_id=cluster_id
                )
            ]
            assert cluster_events == ["cluster_created"]

            work_order_events = [
                event.event_type
                for event in await store.read_stream(
                    entity_type=EntityType.WORK_ORDER.value, entity_id=work_order_id
                )
            ]
            assert work_order_events == ["work_order_created"]

            # Every chain the pipeline touched recomputes. Ordering alone is not
            # the claim — a sequence written with a broken hash link is exactly
            # as ordered and exactly as useless as evidence.
            for entity_type, entity_id in (
                (EntityType.COMPLAINT.value, complaint_id),
                (EntityType.COMPLAINT_CLUSTER.value, cluster_id),
                (EntityType.WORK_ORDER.value, work_order_id),
            ):
                verification = await verify_chain(
                    session, tenant_id=tenant_id, entity_type=entity_type, entity_id=entity_id
                )
                assert verification.is_intact, verification.first_break


async def test_every_touched_projection_is_materialised(
    bound_session: None, tenant_id: uuid.UUID
) -> None:
    """The Phase 2 defect #12 check, applied to Phase 3's writes.

    Emitting the events and leaving the current-state tables empty would pass
    every assertion above. It is the same "proven against the projection rather
    than against the tables" gap, and it is worth one explicit test.
    """
    cluster_id, work_order_id = uuid.uuid4(), uuid.uuid4()
    complaint_id = await _submit(tenant_id)

    with _all_providers(cluster_id, work_order_id):
        await _run_to_completion(tenant_id, complaint_id)

    from nemesis.db.session import session_scope

    with tenant_scope(tenant_id):
        async with session_scope() as session:
            complaint = (
                await session.execute(
                    select(Complaint.status, Complaint.category).where(
                        Complaint.tenant_id == tenant_id, Complaint.id == complaint_id
                    )
                )
            ).one()
            assert complaint == (ComplaintStatus.SCORED.value, "elevator_fault")

            cluster = (
                await session.execute(
                    select(ComplaintCluster.report_count).where(
                        ComplaintCluster.tenant_id == tenant_id,
                        ComplaintCluster.id == cluster_id,
                    )
                )
            ).one()
            assert cluster[0] == 1

            work_order = (
                await session.execute(
                    select(WorkOrder.status).where(
                        WorkOrder.tenant_id == tenant_id, WorkOrder.id == work_order_id
                    )
                )
            ).one()
            assert work_order[0] == "created"


# ---------------------------------------------------------------------------
# Gate clause 2 — a redelivered stage is a provable no-op
# ---------------------------------------------------------------------------


async def test_redelivering_a_stage_appends_nothing(
    bound_session: None, tenant_id: uuid.UUID
) -> None:
    """What ``SIGKILL`` mid-pipeline reduces to once the broker redelivers.

    ``task_acks_late`` means a worker killed mid-stage has its message
    redelivered. The claim "loses nothing on restart" therefore has two halves:
    the work is retried (Celery's) and the retry cannot double-write (this).
    """
    complaint_id = await _submit(tenant_id)

    calls = 0

    async def counting(ctx: StageContext) -> StageResult:
        nonlocal calls
        calls += 1
        return StageResult(
            emitted=[
                EmittedEvent(
                    entity_type=EntityType.COMPLAINT,
                    entity_id=ctx.complaint_id,
                    event_type="exif_check_completed",
                    payload={"exif_present": False, "trust_delta": -0.3},
                )
            ]
        )

    with provider_scope(PipelineStage.SAFETY_CHECK, counting):
        first = await execute_stage(
            tenant_id=tenant_id, complaint_id=complaint_id, stage="safety_check"
        )
        second = await execute_stage(
            tenant_id=tenant_id, complaint_id=complaint_id, stage="safety_check", attempt=2
        )

    assert first.ran and first.events_appended == 1
    assert second.already_ran and second.events_appended == 0
    # The provider ran twice — the orchestrator cannot know a stage is a
    # redelivery until it tries to append — and the log took one event.
    assert calls == 2

    from nemesis.db.session import session_scope

    with tenant_scope(tenant_id):
        async with session_scope() as session:
            events = await EventStore(session).read_stream(
                entity_type=EntityType.COMPLAINT.value, entity_id=complaint_id
            )
            assert [event.event_type for event in events] == [
                "complaint_submitted",
                "exif_check_completed",
            ]

            # And the redelivery did not enqueue a second realtime publish,
            # which would have animated the same event twice on the map.
            outbox = (
                (
                    await session.execute(
                        select(OutboxMessage.event_type).where(OutboxMessage.tenant_id == tenant_id)
                    )
                )
                .scalars()
                .all()
            )
            assert sorted(outbox) == ["complaint_submitted", "exif_check_completed"]


async def test_a_provider_that_raises_leaves_no_partial_write(
    bound_session: None, tenant_id: uuid.UUID
) -> None:
    """A stage is atomic: two events emitted, one append failing, nothing kept."""
    complaint_id = await _submit(tenant_id)

    async def half_failing(ctx: StageContext) -> StageResult:
        return StageResult(
            emitted=[
                EmittedEvent(
                    entity_type=EntityType.COMPLAINT,
                    entity_id=ctx.complaint_id,
                    event_type="exif_check_completed",
                    payload={"exif_present": True, "trust_delta": 0.1},
                ),
                EmittedEvent(
                    entity_type=EntityType.COMPLAINT,
                    entity_id=ctx.complaint_id,
                    event_type="classification_scored",
                    # `confidence` out of its 0..1 bound: rejected by the
                    # registry at append time, after the first event has already
                    # been written in this transaction.
                    payload={
                        "category": "x",
                        "confidence": 4.2,
                        "model_id": "m",
                        "prompt_set_version": "p",
                    },
                ),
            ]
        )

    # ValidationError specifically: the second event is rejected by the event
    # registry, which is the realistic way a provider's output fails late.
    with provider_scope(PipelineStage.SAFETY_CHECK, half_failing):  # noqa: SIM117
        with pytest.raises(ValidationError):
            await execute_stage(
                tenant_id=tenant_id, complaint_id=complaint_id, stage="safety_check"
            )

    from nemesis.db.session import session_scope

    with tenant_scope(tenant_id):
        async with session_scope() as session:
            events = await EventStore(session).read_stream(
                entity_type=EntityType.COMPLAINT.value, entity_id=complaint_id
            )
            assert [event.event_type for event in events] == ["complaint_submitted"]


# ---------------------------------------------------------------------------
# Degradation (§24.2)
# ---------------------------------------------------------------------------


async def test_an_unregistered_provider_raises_rather_than_silently_skipping(
    bound_session: None, tenant_id: uuid.UUID
) -> None:
    complaint_id = await _submit(tenant_id)
    with pytest.raises(StageUnavailableError):
        await execute_stage(tenant_id=tenant_id, complaint_id=complaint_id, stage="classification")


async def test_classification_degradation_parks_the_complaint_and_records_why(
    bound_session: None, tenant_id: uuid.UUID
) -> None:
    """§24.2 by name: pending_classification, reached through the log."""
    complaint_id = await _submit(tenant_id)

    recorded = await record_degradation(
        tenant_id=tenant_id,
        complaint_id=complaint_id,
        stage=PipelineStage.CLASSIFICATION.value,
        failure_mode="provider_unavailable",
        attempts=1,
    )
    assert recorded is True

    from nemesis.db.session import session_scope

    with tenant_scope(tenant_id):
        async with session_scope() as session:
            status = (
                await session.execute(
                    select(Complaint.status).where(
                        Complaint.tenant_id == tenant_id, Complaint.id == complaint_id
                    )
                )
            ).scalar_one()
            assert status == ComplaintStatus.PENDING_CLASSIFICATION.value

            events = await EventStore(session).read_stream(
                entity_type=EntityType.COMPLAINT.value, entity_id=complaint_id
            )
            assert events[-1].event_type == "pipeline_stage_degraded"
            assert events[-1].payload["fallback_taken"] == (
                DegradationFallback.PENDING_CLASSIFICATION.value
            )

            # And it is queryable, which is what makes "degraded, not lost" true.
            dead_letter = (
                await session.execute(
                    select(PipelineDeadLetter.stage, PipelineDeadLetter.failure_mode).where(
                        PipelineDeadLetter.tenant_id == tenant_id,
                        PipelineDeadLetter.entity_id == complaint_id,
                    )
                )
            ).one()
            assert dead_letter == ("classification", "provider_unavailable")

            # The chain still verifies: a degradation is an ordinary event.
            verification = await verify_chain(
                session,
                tenant_id=tenant_id,
                entity_type=EntityType.COMPLAINT.value,
                entity_id=complaint_id,
            )
            assert verification.is_intact


async def test_repeated_degradation_updates_one_dead_letter_rather_than_piling_up(
    bound_session: None, tenant_id: uuid.UUID
) -> None:
    complaint_id = await _submit(tenant_id)

    first = await record_degradation(
        tenant_id=tenant_id,
        complaint_id=complaint_id,
        stage="classification",
        failure_mode="provider_unavailable",
        attempts=1,
    )
    second = await record_degradation(
        tenant_id=tenant_id,
        complaint_id=complaint_id,
        stage="classification",
        failure_mode="provider_unavailable",
        attempts=3,
    )
    assert first is True
    # The event is idempotent; the dead letter is upserted.
    assert second is False

    from nemesis.db.session import session_scope

    with tenant_scope(tenant_id):
        async with session_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(PipelineDeadLetter.attempts).where(
                            PipelineDeadLetter.tenant_id == tenant_id,
                            PipelineDeadLetter.entity_id == complaint_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert rows == [3]


async def test_a_halting_stage_stops_the_pipeline(
    bound_session: None, tenant_id: uuid.UUID
) -> None:
    """§11.2: a safety trigger bypasses the scoring pipeline *entirely*."""
    complaint_id = await _submit(tenant_id)

    async def dangerous(ctx: StageContext) -> StageResult:
        return StageResult(
            emitted=[
                EmittedEvent(
                    entity_type=EntityType.COMPLAINT,
                    entity_id=ctx.complaint_id,
                    event_type="safety_trigger_fired",
                    payload={
                        "rule_id": "live-wire",
                        "ruleset_version": "test-v1",
                        "matched_terms": ["sparking"],
                        "detection_source": "keyword",
                    },
                )
            ],
            halt=True,
            halt_reason="safety fail-safe fired",
        )

    with provider_scope(PipelineStage.SAFETY_CHECK, dangerous):
        execution = await execute_stage(
            tenant_id=tenant_id, complaint_id=complaint_id, stage="safety_check"
        )

    assert execution.halted
    # `next_stage` is None even though the declaration names classification —
    # a halt that still handed the pipeline its successor would be four no-ops
    # holding queue slots behind a danger signal, not a bypass.
    assert execution.next_stage is None

    from nemesis.db.session import session_scope

    with tenant_scope(tenant_id):
        async with session_scope() as session:
            status = (
                await session.execute(
                    select(Complaint.status).where(
                        Complaint.tenant_id == tenant_id, Complaint.id == complaint_id
                    )
                )
            ).scalar_one()
            assert status == ComplaintStatus.FLAGGED.value


async def test_a_stage_on_a_complaint_with_no_history_is_not_retryable(
    bound_session: None, tenant_id: uuid.UUID
) -> None:
    async def never_called(ctx: StageContext) -> StageResult:  # pragma: no cover
        raise AssertionError("the provider must not run for an unknown complaint")

    with provider_scope(PipelineStage.SAFETY_CHECK, never_called):  # noqa: SIM117
        with pytest.raises(StageError):
            await execute_stage(
                tenant_id=tenant_id, complaint_id=uuid.uuid4(), stage="safety_check"
            )


# ---------------------------------------------------------------------------
# The declarations themselves
# ---------------------------------------------------------------------------


def test_every_stage_declares_a_reachable_successor() -> None:
    """The graph is a chain with one terminus, not a cycle or a fork."""
    seen: list[str] = []
    stage = PIPELINE_SEQUENCE[0]
    while True:
        assert stage.value not in seen, "the stage graph contains a cycle"
        seen.append(stage.value)
        successor = spec_for(stage.value).next_stage
        if successor is None:
            break
        stage = successor
    assert seen == [s.value for s in PIPELINE_SEQUENCE]


def test_the_safety_stage_never_degrades_by_skipping() -> None:
    """A report the danger check never saw must not be scored as though cleared."""
    spec = spec_for(PipelineStage.SAFETY_CHECK.value)
    assert spec.fallback is DegradationFallback.HALTED_FOR_REVIEW
    assert spec.continue_on_degrade is False
    # And it gets the largest retry budget in the graph, because a missed danger
    # signal is the worst outcome the system can produce.
    assert spec.max_attempts == max(
        spec_for(stage.value).max_attempts for stage in PIPELINE_SEQUENCE
    )


async def test_the_reserved_system_tenant_can_actually_be_written_to(
    bound_session: None, tenant_id: uuid.UUID
) -> None:
    """Regression: ``events.tenant_id`` has a foreign key to ``tenants``.

    Phase 2 introduced ``SYSTEM_TENANT_ID`` as a Python constant with no row
    behind it, so every ``system_degradation`` append would have failed on the
    foreign key — raised *inside* the handler recording some other failure,
    where the second error masks the first. The only code path that would ever
    have exercised it is the one that runs when the system is already broken.
    The Phase 3 migration seeds the row; this proves the append completes.
    """
    from nemesis.db.session import session_scope
    from nemesis.domain.constants import SYSTEM_TENANT_ID
    from nemesis.pipeline.integrity import record_system_degradation

    await record_system_degradation(
        component="pipeline.test",
        failure_mode="unit_test",
        fallback_taken="none",
        correlation_id="test",
    )

    with tenant_scope(SYSTEM_TENANT_ID):
        async with session_scope() as session:
            events = await EventStore(session).read_stream(
                entity_type=EntityType.SYSTEM.value, entity_id=SYSTEM_TENANT_ID
            )
    assert [event.event_type for event in events] == ["system_degradation"]
