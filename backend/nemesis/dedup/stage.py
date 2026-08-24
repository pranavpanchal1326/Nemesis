"""The dedup stage, as the pipeline sees it.

Thin on purpose. Everything decidable lives in ``engine`` and ``decide``, which
are callable without a pipeline; this module is the part that knows about
``StageContext``, metrics, and the idempotency rule, and it is the part that
cannot be unit-tested without a database. Keeping the two apart is why the merge
rule has property tests and this has integration tests, rather than both having
whichever kind is easier to write.

**Idempotency is a guard here, not only in the orchestrator.** The orchestrator
keys redelivery on the first append, which is enough for a stage that emits once.
This one writes a review row through ``trust.review.queue`` on the ambiguous
path, and a redelivered task that re-entered the engine would spend the whole
§27.1 budget recomputing a decision already recorded. The cheap check — is this
complaint already in a cluster — costs one projected-state lookup the context
has already done.
"""

from __future__ import annotations

import uuid

from nemesis.config import get_settings
from nemesis.dedup.decide import DedupOutcome
from nemesis.dedup.engine import evaluate
from nemesis.dedup.merge import events_for, queue_ambiguous
from nemesis.observability import metrics
from nemesis.observability.logging import get_logger
from nemesis.pipeline.stages import StageContext, StageResult

log = get_logger(__name__)


async def dedup_stage(ctx: StageContext) -> StageResult:
    """Decide what incident this report belongs to, and record it on both chains."""
    if ctx.state.get("cluster_id") is not None:
        # Already decided. Emitting nothing makes the retry a provable no-op,
        # which is the same shape `safety_stage` uses for "nothing fired".
        log.info(
            "dedup_already_decided",
            complaint_id=str(ctx.complaint_id),
            cluster_id=str(ctx.state["cluster_id"]),
            note="redelivered stage; the earlier decision stands",
        )
        return StageResult()

    settings = get_settings().dedup
    evaluation = await evaluate(
        ctx.session,
        tenant_id=ctx.tenant_id,
        complaint_id=ctx.complaint_id,
        state=ctx.state,
        settings=settings,
    )
    decision = evaluation.decision

    metrics.dedup_decisions_total.labels(outcome=decision.outcome.value).inc()
    metrics.dedup_candidates.observe(evaluation.stage1_candidates)
    metrics.dedup_confidence.observe(decision.combined_confidence)
    if evaluation.truncated:
        metrics.dedup_truncations_total.inc()

    emitted = events_for(
        evaluation,
        complaint_id=ctx.complaint_id,
        latitude=float(ctx.state["latitude"]),
        longitude=float(ctx.state["longitude"]),
        # Minted here rather than inside `events_for`, so that function stays
        # deterministic for the harness and the simulator.
        new_cluster_id=uuid.uuid4(),
    )

    if decision.outcome is DedupOutcome.INVESTIGATE:
        emitted = (
            *emitted,
            await queue_ambiguous(
                ctx.session,
                tenant_id=ctx.tenant_id,
                complaint_id=ctx.complaint_id,
                evaluation=evaluation,
                trust_score=float(ctx.state.get("trust_score", 0.0)),
            ),
        )

    log.info(
        "dedup_decided",
        complaint_id=str(ctx.complaint_id),
        outcome=decision.outcome.value,
        cluster_id=str(decision.cluster_id) if decision.cluster_id else None,
        combined_confidence=decision.combined_confidence,
        image_similarity=decision.image_similarity,
        text_similarity=decision.text_similarity,
        geo_distance_meters=decision.geo_distance_meters,
        candidates=decision.considered,
        runner_up=decision.runner_up_confidence,
        ambiguous_between=[str(value) for value in decision.ambiguous_between],
        policy_version=evaluation.policy_version,
        truncated=evaluation.truncated,
        # Said in words because it is the one outcome that looks identical to a
        # healthy "nothing matched" in every dashboard: no vector, no
        # comparison, a new cluster every time.
        blind=decision.blind,
    )
    if decision.blind and evaluation.stage1_candidates:
        log.warning(
            "dedup_ran_blind",
            complaint_id=str(ctx.complaint_id),
            candidates=evaluation.stage1_candidates,
            consequence=(
                "nearby incidents existed but neither this report nor their members "
                "carried a comparable embedding; a new cluster was created on the "
                "absence of evidence, not on evidence of difference"
            ),
        )

    return StageResult(emitted=emitted)


__all__ = ["dedup_stage"]
