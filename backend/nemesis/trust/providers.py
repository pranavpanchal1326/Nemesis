"""Binding Phase 8's two stages to the pipeline graph.

Kept out of ``nemesis.trust.__init__`` deliberately. Importing the package —
which the API does, to serve the review queue — must not register a pipeline
provider: registration is a claim that *this process will execute the stage*,
and the API process will not. ``pipeline.stages.register_provider`` refuses a
second registration precisely so that claim stays single-valued, and an
import-time side effect would make it depend on which module was imported first.

So registration is a function, called from the worker startup path, and calling
it twice is an error rather than a shrug.
"""

from __future__ import annotations

from nemesis.observability.logging import get_logger
from nemesis.observability.metrics import PipelineStage
from nemesis.pipeline.stages import register_provider, registered_stages
from nemesis.trust.detectors import detector_is_registered, install_mediapipe_detector
from nemesis.trust.safety import safety_stage
from nemesis.trust.verification import trust_stage

log = get_logger(__name__)


def register_trust_stages() -> None:
    """Bind the safety and trust-verification providers, once per process.

    Idempotent at the level that matters: a stage already registered is skipped
    rather than re-registered, because Celery's ``worker_ready`` can fire more
    than once in a process that reconnects to the broker, and a hard failure
    there would take down a worker for a reason unrelated to any complaint.
    """
    already = registered_stages()
    if PipelineStage.SAFETY_CHECK.value not in already:
        register_provider(PipelineStage.SAFETY_CHECK, safety_stage)
    if PipelineStage.TRUST_VERIFICATION.value not in already:
        register_provider(PipelineStage.TRUST_VERIFICATION, trust_stage)


def install_trust_workers() -> None:
    """Everything a worker needs before it can run a Phase 8 stage.

    The detector and the providers are installed together and the *outcome* is
    logged as one line, because the pair is what determines what this worker can
    actually do — and the failure that matters is the asymmetric one: providers
    registered on a worker serving the ``ml`` queue with no detector, which
    accepts trust-verification work and halts every complaint carrying a photo.
    That is the correct behaviour (§22.1 must not degrade) and it is a very
    confusing thing to discover from a queue depth graph, so it is said here.
    """
    register_trust_stages()
    installed = install_mediapipe_detector()
    log.info(
        "trust_stages_registered",
        face_detector=detector_is_registered(),
        mediapipe_installed=installed,
        note=(
            "this worker can redact"
            if detector_is_registered()
            else "this worker cannot redact; trust-verification work routed here will "
            "halt every complaint carrying a photo, which is §22.1 failing closed"
        ),
    )


__all__ = ["install_trust_workers", "register_trust_stages"]
