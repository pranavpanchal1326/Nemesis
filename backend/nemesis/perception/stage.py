"""The classification stage — §10 step 3, and the second §11.2 pass.

**What runs here, in order, and why the order is not negotiable.**

1. *Transcribe*, if the report carries audio. Everything after this reads the
   transcript, so it has to be first.
2. *Re-run the safety ruleset* over the text that now exists, plus whatever the
   image matched visually. This is the gap ``trust.safety`` names in its own
   docstring and declines to paper over: the first safety pass runs before
   transcription, so a voice-only report of a gas leak reaches it with nothing to
   match. Phase 8 could not fix it because the fix needs Phase 9's output. This
   is Phase 9, so it is fixed here, and it fires *before* any category is claimed
   — a report that trips a danger rule must not also acquire a routing decision.
3. *Embed and score* the image and the text against the tenant's prompt sets.
4. *Persist the embeddings*, because Phase 10's dedup runs on them and this is
   the only pass that has the decoded media in hand.
5. *Emit the classification*, or abstain.

**Why one stage rather than four.** The same argument ``trust.verification``
makes, reached the same way: three of the five steps need the decoded media, and
a hop per step would decode and re-embed on a worker with the tightest memory cap
in the deployment. The cost is stated rather than absorbed — a failure anywhere
degrades the whole stage to ``pending_classification``, which is the declared
fallback and parks the report for a human.

**Why abstention is a first-class outcome.** ``StageAbstainedError`` is raised
rather than a low-confidence category being emitted. §24.2 gives a report with no
category a real place to go; a report with a *wrong* category at 0.22 confidence
goes to a department that cannot act on it, and nothing downstream can tell that
apart from knowledge. The rule for when to abstain is the tenant's approved
calibration document, not a constant in this file.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from nemesis.domain.lifecycle import EntityType
from nemesis.flags import get_flags
from nemesis.observability import metrics
from nemesis.observability.logging import get_logger
from nemesis.perception import calibration as calibration_module
from nemesis.perception import embeddings, media, prompts
from nemesis.perception.encoders import (
    QUERY_PREFIX,
    EncoderKind,
    Transcript,
    active_image_encoder,
    active_text_encoder,
    active_transcriber,
    cosine,
    encoder_is_registered,
)
from nemesis.perception.errors import EncoderUnavailableError, PromptSetUnavailableError
from nemesis.perception.scoring import ScoreResult, decide, score_against
from nemesis.pipeline.stages import (
    EmittedEvent,
    StageAbstainedError,
    StageContext,
    StagePermanentError,
    StageResult,
    StageUnavailableError,
)
from nemesis.policy.documents import PerceptionCalibration, SafetyRuleset
from nemesis.policy.resolver import RESOLVER, evaluate_safety
from nemesis.trust.review import ReviewReason, queue

log = get_logger(__name__)

COMPLAINT = EntityType.COMPLAINT

#: Kill switch name for transcription. Declared in ``flags.registry``.
FLAG_TRANSCRIPTION = "perception_audio_transcription"


@dataclass
class _Perceived:
    """What this stage learned about one submission, accumulated in order.

    A mutable accumulator for the reason ``trust.verification._Accumulator``
    gives: the steps are genuinely sequential — safety re-check needs the
    transcript, scoring needs both modalities, the event needs all of it — and
    threading seven values through five signatures would obscure that the order
    is a dependency rather than a style.
    """

    emitted: list[EmittedEvent] = field(default_factory=list)
    transcript: Transcript | None = None
    language_uncertain: bool = False
    image_result: ScoreResult | None = None
    text_result: ScoreResult | None = None
    image_vector: tuple[float, ...] | None = None
    text_vector: tuple[float, ...] | None = None
    visual_matches: tuple[str, ...] = ()
    #: (encoder, locale) pairs the tenant has authored no prompts for. Collected
    #: rather than raised so the abstention message can name the configuration
    #: gap instead of claiming the submission carried nothing scoreable — which
    #: is the difference between an operator fixing it in a minute and an
    #: operator opening a model investigation.
    prompts_missing: list[str] = field(default_factory=list)
    model_ids: dict[str, str] = field(default_factory=dict)
    prompt_version: str = ""

    def event(self, complaint_id: uuid.UUID, event_type: str, payload: dict[str, Any]) -> None:
        self.emitted.append(
            EmittedEvent(
                entity_type=COMPLAINT,
                entity_id=complaint_id,
                event_type=event_type,
                payload=payload,
            )
        )


async def classification_stage(ctx: StageContext) -> StageResult:
    """Classify one submission against its tenant's taxonomy, or decline to."""
    resolved = await RESOLVER.perception_calibration(ctx.session, tenant_id=ctx.tenant_id)
    policy: PerceptionCalibration = resolved.body
    perceived = _Perceived()

    locales = await prompts.tenant_locales(ctx.session, tenant_id=ctx.tenant_id)
    await _transcribe(ctx, policy=policy, locales=locales, perceived=perceived)

    locale = _scoring_locale(ctx.state, perceived)
    await _score_image(ctx, policy=policy, locale=locale, locales=locales, perceived=perceived)

    # The safety re-check sits between the two scoring passes on purpose: the
    # image half supplies ``visual_matches`` and the transcript supplies the
    # text, so this is the first moment both halves of a §11.2 rule can be
    # evaluated — and it must happen before a category is claimed.
    halt = await _recheck_safety(ctx, policy=policy, perceived=perceived)
    if halt is not None:
        return halt

    await _score_text(ctx, policy=policy, locale=locale, locales=locales, perceived=perceived)

    await embeddings.store(
        ctx.session,
        tenant_id=ctx.tenant_id,
        complaint_id=ctx.complaint_id,
        text_embedding=perceived.text_vector,
        image_embedding=perceived.image_vector,
    )

    return _conclude(ctx, policy=policy, stamp=resolved.stamp, perceived=perceived)


# ---------------------------------------------------------------------------
# Transcription (§8.4)
# ---------------------------------------------------------------------------


async def _transcribe(
    ctx: StageContext,
    *,
    policy: PerceptionCalibration,
    locales: Sequence[str],
    perceived: _Perceived,
) -> None:
    """Turn a voice complaint into text, or record why it stayed audio.

    Every failure here is survivable and none of them stops the stage. A report
    with audio *and* a photograph still classifies on the photograph; a report
    with audio alone reaches the scoring step with no text and abstains, which
    routes it to a human — who can play the clip, which is what they would have
    done anyway.
    """
    try:
        audio = media.audio_bytes(ctx.state)
    except media.MediaUnavailableError as exc:
        # Survivable, unlike the image path's equivalent, and for a reason that
        # is about §22.4 rather than about audio: the clip and the photograph
        # expire on the same retention clock, so a report re-processed after the
        # window has no audio to read and never will. Degrading the whole stage
        # for it would mean a rebuild of old complaints degraded every voice
        # report it touched.
        log.warning(
            "audio_unreadable",
            complaint_id=str(ctx.complaint_id),
            reason=str(exc)[:300],
            consequence="this report is classified on its description alone, if it has one",
        )
        return
    if audio is None:
        return

    if not await get_flags().is_enabled(FLAG_TRANSCRIPTION, tenant_id=str(ctx.tenant_id)):
        # Logged, not silent — the same reasoning ``trust.verification`` applies
        # to its abuse detectors. A killed transcriber and a transcriber that
        # heard nothing produce identical output, and the difference is the whole
        # question when somebody asks why voice reports stopped classifying.
        log.info(
            "transcription_disabled",
            complaint_id=str(ctx.complaint_id),
            flag=FLAG_TRANSCRIPTION,
            note="§8.4 transcription skipped by kill switch; the clip is unread",
        )
        return

    if not encoder_is_registered(EncoderKind.TRANSCRIBE):
        metrics.perception_transcriptions_total.labels(
            language="unknown", outcome="unavailable"
        ).inc()
        log.warning(
            "transcriber_unavailable",
            complaint_id=str(ctx.complaint_id),
            consequence="this voice report has no text for scoring or for the safety re-check",
            runbook="docs/runbooks/perception-model-unavailable.md",
        )
        return

    try:
        transcript = active_transcriber().transcribe(audio, locales=list(locales))
    except Exception as exc:
        metrics.perception_transcriptions_total.labels(language="unknown", outcome="failed").inc()
        log.warning(
            "transcription_failed",
            complaint_id=str(ctx.complaint_id),
            error_type=type(exc).__name__,
            error=str(exc)[:200],
        )
        return

    if not transcript.text:
        # Silence, or speech the model could not resolve into words. Not an
        # error and not an event: recording an empty transcript would put a
        # useless string in the log and, worse, would make the projected state
        # claim a transcript exists for a report a reviewer still has to listen
        # to.
        metrics.perception_transcriptions_total.labels(
            language=transcript.language, outcome="empty"
        ).inc()
        return

    uncertain = transcript.language_confidence < policy.min_language_confidence
    perceived.transcript = transcript
    perceived.language_uncertain = uncertain
    perceived.model_ids[EncoderKind.TRANSCRIBE.value] = transcript.model_id
    metrics.perception_transcriptions_total.labels(language=transcript.language, outcome="ok").inc()
    perceived.event(
        ctx.complaint_id,
        "media_transcribed",
        {
            "transcript": transcript.text,
            "language": transcript.language,
            "language_confidence": round(min(max(transcript.language_confidence, 0.0), 1.0), 6),
            "audio_seconds": round(max(transcript.duration_seconds, 0.0), 3),
            "model_id": transcript.model_id,
            "language_uncertain": uncertain,
        },
    )


# ---------------------------------------------------------------------------
# The second §11.2 pass
# ---------------------------------------------------------------------------


async def _recheck_safety(
    ctx: StageContext, *, policy: PerceptionCalibration, perceived: _Perceived
) -> StageResult | None:
    """Run the tenant's safety ruleset again, now that text and pixels are known.

    **Why a second pass rather than moving the first one.** The first pass runs on
    ``QUEUE_SAFETY``, served by a container that has never imported torch, and
    that separation is what makes "a saturated ml queue cannot delay a danger
    signal" a fact about two operating-system processes rather than a scheduling
    promise. Moving the safety check behind transcription would put the danger
    signal on the ml queue and throw that away. So the deterministic keyword pass
    stays where it is and runs first on whatever text exists, and this pass adds
    what only the ml worker can know.

    **Why it does nothing when nothing new is known.** A report with no transcript
    and no visual match has already been evaluated against exactly this text by
    exactly this ruleset. Re-firing on it would raise a second review item for
    one hazard and emit a duplicate event on the chain.
    """
    if perceived.transcript is None and not perceived.visual_matches:
        return None
    if ctx.state.get("is_safety_flagged"):
        # Already flagged by the first pass. §11.2 halted the pipeline then, so
        # reaching here means a redelivery; firing again would queue a second
        # review item for one hazard.
        return None

    resolved = await RESOLVER.safety_ruleset(ctx.session, tenant_id=ctx.tenant_id)
    ruleset: SafetyRuleset = resolved.body
    text = _text_of(ctx.state, perceived)
    decision = evaluate_safety(
        ruleset,
        text=text,
        locale=_optional_str(ctx.state.get("locale")),
        visual_matches=perceived.visual_matches,
    )
    if not decision.fired:
        return None

    assert decision.rule_id is not None
    metrics.safety_triggers_total.labels(
        rule_id=decision.rule_id, detection_source=decision.detection_source
    ).inc()

    evidence = {
        "rule_id": decision.rule_id,
        "ruleset_version": resolved.stamp,
        "matched_terms": list(decision.matched_terms),
        "detection_source": decision.detection_source,
        "severity_floor": decision.severity_floor,
        "text_examined": text[:500] if text else None,
        # The distinguishing evidence, and the reason this pass exists: a
        # reviewer needs to know the danger was found in a transcript or in a
        # photograph rather than in the text the citizen typed, because the
        # first pass already looked at that and did not fire.
        "found_after_perception": True,
        "visual_matches": list(perceived.visual_matches),
        "from_transcript": perceived.transcript is not None,
    }
    queued = await queue(
        ctx.session,
        tenant_id=ctx.tenant_id,
        complaint_id=ctx.complaint_id,
        reason=ReviewReason.SAFETY_TRIGGER,
        evidence=evidence,
        trust_score=float(ctx.state.get("trust_score", 0.0)),
    )

    log.warning(
        "safety_trigger_fired_after_perception",
        complaint_id=str(ctx.complaint_id),
        rule_id=decision.rule_id,
        detection_source=decision.detection_source,
        matched_terms=list(decision.matched_terms),
        note="found in a transcript or a photograph, which the pre-perception pass could not read",
        runbook="docs/runbooks/safety-path-degraded.md",
    )

    perceived.event(
        ctx.complaint_id,
        "safety_trigger_fired",
        {
            "rule_id": decision.rule_id,
            "ruleset_version": resolved.stamp,
            "matched_terms": list(decision.matched_terms),
            "detection_source": decision.detection_source,
        },
    )
    perceived.event(
        ctx.complaint_id,
        "review_queued",
        {
            "review_item_id": str(queued.review_item_id),
            "reason": queued.reason.value,
            "priority": queued.priority,
            "occurrences": queued.occurrences,
            "trust_score": queued.trust_score,
            "evidence_hash": queued.evidence_hash,
        },
    )
    return StageResult(
        emitted=tuple(perceived.emitted),
        halt=True,
        halt_reason=(
            f"§11.2 safety rule {decision.rule_id!r} fired on evidence only the "
            f"perception layer could see ({decision.detection_source}); the report is "
            f"never given a category and never routed"
        ),
    )


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


async def _score_image(
    ctx: StageContext,
    *,
    policy: PerceptionCalibration,
    locale: str | None,
    locales: Sequence[str],
    perceived: _Perceived,
) -> None:
    """Embed the redacted photograph and score it against the CLIP prompt set.

    **An unusable photograph does not fail the stage when there is text.** A
    report whose redacted image was purged by the §22.4 retention sweep, or whose
    bytes will not decode, still carries a description or a transcript — and
    classifying on that is strictly better than degrading a report the system can
    read. The image failure is logged either way, so a *systematic* one is
    visible rather than showing up as a slow drift in accuracy. With no text to
    fall back to there is nothing to score, and that is permanent: a file that
    will not decode will not decode in thirty seconds either.

    **A tenant with no CLIP prompts is a configuration gap, not a stage failure,
    and it is handled here exactly as the text side handles its own version.**
    An earlier revision let ``PromptSetUnavailableError`` propagate out of this
    function, which meant a tenant who had authored text prompts and no image
    prompts had *every photographed report* retried three times and then degraded
    to ``pending_classification`` — including reports whose description the text
    side would have classified correctly. The asymmetry was backwards: the image
    modality is the optional one when the tenant has not configured it. The
    embedding is kept regardless, because Phase 10's dedup needs it whether or
    not anything was scored against it.
    """
    try:
        data = media.redacted_image_bytes(ctx.state)
    except media.MediaUnavailableError as exc:
        _image_unusable(ctx, perceived, reason=str(exc), error_type="unavailable")
        return
    if data is None:
        return

    encoder = _require_encoder(active_image_encoder, "image")
    try:
        vector = encoder.encode_image(data)
    except Exception as exc:
        _image_unusable(
            ctx, perceived, reason=f"{type(exc).__name__}: {exc}", error_type="undecodable"
        )
        return

    perceived.image_vector = vector
    perceived.model_ids[EncoderKind.IMAGE.value] = encoder.model_id

    try:
        bundle = await prompts.load_bundle(
            ctx.session,
            tenant_id=ctx.tenant_id,
            locale=locale,
            encoder=prompts.ENCODER_IMAGE,
            fallback_locales=locales,
        )
    except PromptSetUnavailableError as exc:
        perceived.prompts_missing.append(f"{prompts.ENCODER_IMAGE}/{locale or '(none)'}")
        log.info(
            "image_prompts_absent",
            complaint_id=str(ctx.complaint_id),
            reason=str(exc)[:200],
            note="scoring falls back to the text modality; the image embedding is still "
            "stored, and the §11.2 visual pass below still runs",
        )
    else:
        embedded = prompts.embed(bundle, encoder=encoder)
        perceived.prompt_version = bundle.version
        perceived.image_result = score_against(
            vector,
            embedded.categories,
            calibration=calibration_module.per_category(policy),
            default=calibration_module.default_of(policy),
        )

    # Outside the branch on purpose. §11.2's visual prompts come from the safety
    # ruleset, not from the taxonomy, so a tenant with no CLIP *category* prompts
    # still has a working visual half to its danger rules — and skipping it here
    # would make the fail-safe depend on an unrelated piece of configuration.
    perceived.visual_matches = await _visual_safety_matches(
        ctx, policy=policy, encoder=encoder, vector=vector
    )


async def _score_text(
    ctx: StageContext,
    *,
    policy: PerceptionCalibration,
    locale: str | None,
    locales: Sequence[str],
    perceived: _Perceived,
) -> None:
    """Embed the description and transcript, and score them against the text prompts.

    The text is embedded with the **query** prefix while the prompts use
    **passage** — e5's asymmetry, and getting it backwards costs retrieval
    quality silently. A complaint is the thing being looked up; a category
    description is the thing being looked up *in*.
    """
    text = _text_of(ctx.state, perceived)
    if not text:
        return

    encoder = _require_encoder(active_text_encoder, "text")
    vectors = encoder.encode([text], prefix=QUERY_PREFIX)
    if not vectors:  # pragma: no cover - an encoder contract breach
        return
    vector = vectors[0]
    perceived.text_vector = vector
    perceived.model_ids[EncoderKind.TEXT.value] = encoder.model_id

    try:
        bundle = await prompts.load_bundle(
            ctx.session,
            tenant_id=ctx.tenant_id,
            locale=locale,
            encoder=prompts.ENCODER_TEXT,
            fallback_locales=locales,
        )
    except PromptSetUnavailableError:
        # The text side is optional in a way the image side is not: a tenant may
        # legitimately have authored CLIP prompts and no text prompts, and a
        # submission with a photograph still classifies. The embedding is kept
        # regardless, because Phase 10's dedup needs it whether or not anything
        # was scored against it.
        perceived.prompts_missing.append(f"{prompts.ENCODER_TEXT}/{locale or '(none)'}")
        log.info(
            "text_prompts_absent",
            complaint_id=str(ctx.complaint_id),
            note="scoring falls back to the image modality; the text embedding is still stored",
        )
        return

    embedded = prompts.embed(bundle, encoder=encoder)
    if not perceived.prompt_version:
        perceived.prompt_version = bundle.version
    else:
        # Both modalities contributed, so the stamp has to name both prompt sets
        # or a later reader cannot reproduce the decision from it.
        perceived.prompt_version = f"{perceived.prompt_version}+{bundle.version}"
    perceived.text_result = score_against(
        vector,
        embedded.categories,
        calibration=calibration_module.per_category(policy),
        default=calibration_module.default_of(policy),
    )


async def _visual_safety_matches(
    ctx: StageContext,
    *,
    policy: PerceptionCalibration,
    encoder: Any,
    vector: Sequence[float],
) -> tuple[str, ...]:
    """Which §11.2 visual prompts this photograph matches above the threshold.

    **A raw cosine, not a softmax.** "Is there fire in this image" is a yes/no
    against one phrase, not a ranking against forty — a probability from a
    distribution over unrelated categories would answer a different question and
    would move whenever the taxonomy changed.

    Returns the prompt strings themselves because that is what
    ``evaluate_safety`` matches against: the ruleset owns the vocabulary, and a
    translation layer between the two would be one more place for a rule's
    visual half to go quietly inert.
    """
    resolved = await RESOLVER.safety_ruleset(ctx.session, tenant_id=ctx.tenant_id)
    ruleset: SafetyRuleset = resolved.body
    phrases: list[str] = []
    for rule in ruleset.rules:
        if rule.is_active:
            phrases.extend(rule.visual_prompts)
    if not phrases:
        return ()

    # Deduplicated while preserving order: two rules may name the same phrase,
    # and embedding it twice costs a tower pass for an identical vector.
    unique = list(dict.fromkeys(phrases))
    try:
        embedded = encoder.encode_prompts(unique)
    except Exception as exc:
        # A failure here must not fail the stage. The keyword half of every rule
        # already ran on the safety queue and §11.2 guarantees no rule is wholly
        # visual, so the fail-safe is degraded, not absent — and that is worth a
        # warning rather than a halt.
        log.warning(
            "visual_safety_scoring_failed",
            complaint_id=str(ctx.complaint_id),
            error_type=type(exc).__name__,
            consequence="the visual half of every safety rule is inert for this report",
        )
        return ()

    threshold = policy.visual_safety_threshold
    return tuple(
        phrase
        for phrase, prompt_vector in zip(unique, embedded, strict=True)
        if cosine(vector, prompt_vector) >= threshold
    )


# ---------------------------------------------------------------------------
# Conclusion
# ---------------------------------------------------------------------------


def _conclude(
    ctx: StageContext,
    *,
    policy: PerceptionCalibration,
    stamp: str,
    perceived: _Perceived,
) -> StageResult:
    """Emit the classification, or abstain and let §24.2 park the report."""
    if perceived.image_result is None and perceived.text_result is None:
        metrics.perception_classifications_total.labels(outcome="no_evidence").inc()
        if perceived.prompts_missing:
            # Distinguished from "nothing scoreable" deliberately. Both park the
            # report, and only one of them is fixed by somebody typing prompts
            # into the control plane — so they must not read the same in a log.
            raise StageAbstainedError(
                f"this tenant has authored no prompts for "
                f"{', '.join(sorted(set(perceived.prompts_missing)))}, so there is "
                f"nothing to score this submission against. The evidence is intact and "
                f"the embeddings are stored; add the prompt sets through the taxonomy "
                f"API and the next submission classifies. This is a configuration gap, "
                f"not a model failure"
            )
        raise StageAbstainedError(
            "this submission carries nothing scoreable — no photograph, and no text "
            "from either a description or a transcript. There is no category to claim "
            "and no evidence to claim it from"
        )

    # One decision rule, two callers. ``scoring.decide`` is also what the
    # validation harness runs, which is what makes the published per-category F1
    # a measurement of this stage rather than of a re-implementation that agrees
    # with it on the day it was written.
    decision = decide(
        perceived.image_result,
        perceived.text_result,
        calibration=calibration_module.per_category(policy),
        default=calibration_module.default_of(policy),
        image_weight=policy.image_weight,
    )
    fused = decision.fused
    if decision.abstained:
        metrics.perception_classifications_total.labels(outcome="abstained").inc()
        assert decision.abstain_reason is not None
        raise StageAbstainedError(decision.abstain_reason)

    metrics.perception_classifications_total.labels(outcome="classified").inc()
    metrics.perception_confidence.observe(fused.confidence)

    transcript = perceived.transcript
    perceived.event(
        ctx.complaint_id,
        "classification_scored",
        {
            "category": fused.category,
            "confidence": _bounded(fused.confidence),
            # The model behind the modality that decided. Both ids are in
            # ``model_ids``; this field is what ``complaints.classifier_model_id``
            # projects from and what Phase 11 groups training examples by.
            "model_id": _deciding_model(perceived, fused),
            "prompt_set_version": perceived.prompt_version,
            "alternatives": dict(fused.alternatives),
            "transcript": transcript.text if transcript is not None else None,
            "detected_language": transcript.language if transcript is not None else None,
            "margin": round(fused.margin, 6),
            "raw_similarities": dict(fused.raw_similarities),
            "calibration_version": stamp,
            "model_ids": dict(perceived.model_ids),
            "language_confidence": (
                _bounded(transcript.language_confidence) if transcript is not None else None
            ),
        },
    )
    return StageResult(emitted=tuple(perceived.emitted))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _image_unusable(
    ctx: StageContext, perceived: _Perceived, *, reason: str, error_type: str
) -> None:
    """Record an unreadable photograph, and fail only if it was the sole evidence."""
    if _text_of(ctx.state, perceived) is None:
        raise StagePermanentError(
            f"the only evidence on this submission is a photograph that cannot be read "
            f"({error_type}): {reason}. There is no description and no transcript to "
            f"classify instead, so this goes straight to the §24.2 fallback rather than "
            f"spending a retry budget on a file that will not change."
        )
    log.warning(
        "classification_image_unusable",
        complaint_id=str(ctx.complaint_id),
        error_type=error_type,
        reason=reason[:300],
        note="classifying on text alone; a systematic occurrence here is an infrastructure "
        "problem wearing an accuracy costume",
    )


def _require_encoder(accessor: Any, label: str) -> Any:
    """Fetch an encoder, translating "not installed" into the stage's vocabulary.

    ``StageUnavailableError`` rather than a retry: an encoder absent from this
    image will still be absent in thirty seconds, and the §24.2 fallback is
    available now. The same reasoning ``trust.detectors`` applies to a missing
    face detector, with a different consequence — this one degrades a report,
    that one halts it, because §22.1 is an obligation and a category is not.
    """
    try:
        return accessor()
    except EncoderUnavailableError as exc:
        raise StageUnavailableError(
            f"the {label} encoder is not registered in this process, so nothing here can "
            f"classify. The stage runs on the `ml` queue; a worker on another queue "
            f"reaching this line means the stage was routed wrongly. ({exc})"
        ) from exc


def _deciding_model(perceived: _Perceived, fused: ScoreResult) -> str:
    """Which model's id to record as *the* classifier for this decision.

    The image tower when it contributed, because it is the modality §43.1 names
    and the one whose drift Phase 11 will be watching. Text otherwise. A fused
    decision genuinely has two authors, which is why ``model_ids`` carries both —
    this field exists for the single-valued column and says so.
    """
    for key in (EncoderKind.IMAGE.value, EncoderKind.TEXT.value):
        model_id = perceived.model_ids.get(key)
        if model_id is not None:
            return model_id
    return "unknown"  # pragma: no cover - fused implies at least one modality


def _scoring_locale(state: Mapping[str, Any], perceived: _Perceived) -> str | None:
    """Which locale's prompt set to score against.

    The detected language wins over the submitted locale when the transcriber was
    confident, because a citizen who selected English in a form and then spoke
    Marathi should be scored against Marathi prompts. When detection was *not*
    confident the submitted locale wins — scoring Marathi text against prompts
    chosen for a misdetected Hindi is worse than scoring it against the tenant's
    default, and ``media_transcribed.language_uncertain`` records which happened.
    """
    if perceived.transcript is not None and not perceived.language_uncertain:
        return perceived.transcript.language
    return _optional_str(state.get("locale"))


def _text_of(state: Mapping[str, Any], perceived: _Perceived) -> str | None:
    """The description and the transcript, joined. ``None`` when there is neither.

    ``None`` rather than an empty string, for the reason ``trust.safety._text_of``
    gives: "no text to match" is a distinct state from "text that matched
    nothing", and ``SafetyRuleset.on_indeterminate`` exists to treat them
    differently.
    """
    parts = [
        value
        for value in (
            _optional_str(state.get("description_text")),
            perceived.transcript.text if perceived.transcript is not None else None,
        )
        if value and value.strip()
    ]
    return "\n".join(parts) if parts else None


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _bounded(value: float) -> float:
    """Clamp into [0, 1] before it reaches a ``Confidence`` field.

    Softmax output is already in range and floating-point rounding at six
    decimals can still produce 1.0000000000000002. The payload model would reject
    it — correctly, and inside ``EventStore.append``, after the stage believed it
    had succeeded and the embeddings had already been written.
    """
    return round(min(max(float(value), 0.0), 1.0), 6)


__all__ = ["FLAG_TRANSCRIPTION", "classification_stage"]
