"""Binding Phase 9's stage to the pipeline graph, and its models to this process.

Kept out of ``nemesis.perception.__init__`` deliberately, for the reason
``trust.providers`` states: importing the package — which the harness does, and
which anything reading the errors does — must not register a pipeline provider.
Registration is a claim that *this process will execute the stage*, and a claim
that depends on which module was imported first is not single-valued.

**Registration and warming are separate calls, and the split is the useful one.**
Registering says what this worker *can* do and costs microseconds; warming loads
600 MB of weights and says it is *ready to*. A worker that registers and then
warms in the background accepts work it can serve slowly; a worker that blocks
startup on three cold loads reports nothing at all — including that one of them
failed — for the forty seconds it takes.
"""

from __future__ import annotations

from nemesis.observability.logging import get_logger
from nemesis.observability.metrics import PipelineStage
from nemesis.perception.backends import install_perception_encoders, warm_encoders
from nemesis.perception.encoders import EncoderKind, encoder_is_registered
from nemesis.perception.stage import classification_stage
from nemesis.pipeline.stages import register_provider, registered_stages

log = get_logger(__name__)


def register_perception_stages() -> None:
    """Bind the classification provider, once per process.

    Idempotent at the level that matters, exactly as ``register_trust_stages``
    is: a stage already registered is skipped rather than re-registered, because
    Celery's process-init signal can fire more than once in a child that
    reconnects, and a hard failure there would take a worker down for a reason
    unrelated to any complaint.
    """
    if PipelineStage.CLASSIFICATION.value not in registered_stages():
        register_provider(PipelineStage.CLASSIFICATION, classification_stage)


def install_perception_workers() -> None:
    """Everything a worker needs before it can classify, logged as one outcome.

    The pair — providers plus encoders — is what determines what this worker can
    actually do, and the failure that matters is the asymmetric one: the provider
    registered on a worker serving the ``ml`` queue with no encoders, which
    accepts classification work and degrades every complaint to
    ``pending_classification``. That is correct behaviour and a very confusing
    thing to discover from a queue-depth graph, so it is said here in words.
    """
    from nemesis.config import get_settings

    register_perception_stages()
    installed = install_perception_encoders()

    warmed: dict[str, str] = {}
    if installed and get_settings().perception.warm_load_on_start:
        warmed = warm_encoders()

    can_classify = encoder_is_registered(EncoderKind.IMAGE) or encoder_is_registered(
        EncoderKind.TEXT
    )
    log.info(
        "perception_stage_registered",
        encoders_installed=installed,
        image_encoder=encoder_is_registered(EncoderKind.IMAGE),
        text_encoder=encoder_is_registered(EncoderKind.TEXT),
        transcriber=encoder_is_registered(EncoderKind.TRANSCRIBE),
        warm=warmed,
        note=(
            "this worker can classify"
            if can_classify
            else "this worker cannot classify; classification work routed here will park "
            "every report as pending_classification, which is §24.2 degrading honestly"
        ),
    )


__all__ = ["install_perception_workers", "register_perception_stages"]
