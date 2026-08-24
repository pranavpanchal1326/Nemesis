"""Binding the dedup stage to the pipeline graph.

Kept out of ``nemesis.dedup.__init__`` for the reason ``trust.providers`` and
``perception.providers`` both state: importing the package — which the harness
does, and which the API does to read a cluster — must not register a pipeline
provider. Registration is a claim that *this process will execute the stage*,
and a claim that depends on which module was imported first is not
single-valued.

There is no warm-up counterpart here. Unlike perception, this stage loads no
weights: it needs Postgres, which every worker already has, so "registered" and
"ready" are the same state and a second function would only imply otherwise.
"""

from __future__ import annotations

from nemesis.dedup.stage import dedup_stage
from nemesis.observability.logging import get_logger
from nemesis.observability.metrics import PipelineStage
from nemesis.pipeline.stages import register_provider, registered_stages

log = get_logger(__name__)


def register_dedup_stages() -> None:
    """Bind the dedup provider, once per process.

    Idempotent at the level that matters, exactly as the Phase 8 and Phase 9
    registrations are: a stage already registered is skipped rather than
    re-registered, because Celery's process-init signal can fire more than once
    in a child that reconnects, and a hard failure there would take a worker
    down for a reason unrelated to any complaint.
    """
    if PipelineStage.DEDUP.value not in registered_stages():
        register_provider(PipelineStage.DEDUP, dedup_stage)
        log.info(
            "dedup_stage_registered",
            queue="io",
            note="this worker can deduplicate",
        )


__all__ = ["register_dedup_stages"]
