"""The §9.4 event catalog as versioned, validated payload schemas.

**What is registered here, and what deliberately is not.**

Registering an event type fixes its payload shape under a compatibility rule
that is expensive to change on purpose — that is the whole point of the
registry. So inventing a payload for a stage whose implementation is six phases
away would be the opposite of careful: it locks in a guess, and the phase that
finally builds it pays for the guess with a version bump and an upcaster that
exist only to correct something nobody had learned yet.

The catalog therefore registers the events whose shape the blueprint already
determines, and records the rest in ``DEFERRED_EVENT_TYPES`` with the phase that
owns them. ``scripts/check_event_schemas.py`` parses §9.4 **from the blueprint**
and fails if a catalogued type is neither registered nor explicitly deferred —
so a type added to the blueprint later fails CI instead of being noticed never.

**Deviation from §9.4's naming.** The blueprint names the scoring event
``severity_rubric_v1_scored``, versioning the rubric inside the event *type*.
This registers ``severity_scored`` with the rubric identifier in the payload
instead. Encoding a version in the type name means rubric v2 is a *different
event type* with no upcaster path from v1 — replay would have to know that two
unrelated names mean the same thing, which is critique-log defect #11 arriving
by a side door. The version belongs in the two places built to carry it: the
registry's ``event_version`` for payload shape, and ``policy_version`` for which
rubric did the scoring.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Annotated, Any, Final

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nemesis.domain.lifecycle import EntityType
from nemesis.events.canonical import MAX_SAFE_INTEGER, CanonicalisationError, canonicalise
from nemesis.events.registry import register_event

#: A probability or normalised score. Bounded here rather than checked at the
#: call site, so an out-of-range confidence is rejected before it is hashed into
#: a permanent record.
Confidence = Annotated[float, Field(ge=0.0, le=1.0)]

#: WGS84. Bounds are the real ones, not a sanity range: a longitude of 200 is a
#: bug in the client, and storing it would place a complaint outside the planet.
Latitude = Annotated[float, Field(ge=-90.0, le=90.0)]
Longitude = Annotated[float, Field(ge=-180.0, le=180.0)]


class EventPayload(BaseModel):
    """Base for every event payload.

    ``extra="forbid"`` because a field the schema does not declare is a field
    replay cannot interpret, and accepting it means the log quietly accumulates
    data with no owner. ``frozen=True`` because a payload that can be mutated
    after validation can be mutated after hashing.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=False)

    @model_validator(mode="after")
    def _must_be_canonicalisable(self) -> EventPayload:
        """Reject at the boundary anything the chain could not hash stably.

        Validation and canonicalisation are checked together on purpose. A
        payload that validates but cannot be canonicalised — a NaN severity, an
        integer beyond double precision — would fail inside
        ``EventStore.append``, after the caller believed the event was accepted
        and possibly after other work in the same transaction had been done.
        """
        dumped = self.model_dump(mode="json")
        _reject_unsafe_integers(dumped)
        try:
            canonicalise(dumped)
        except CanonicalisationError as exc:
            raise ValueError(f"payload cannot be canonicalised: {exc}") from exc
        return self


def _reject_unsafe_integers(value: Any, path: str = "") -> None:
    """Refuse integers that lose precision as doubles.

    The canonicaliser accepts them — it has to, because re-reading its own
    output produces them — but a *payload* containing one is a design mistake
    with a knowable consequence: any JavaScript consumer of the §16.3 public API
    reads a different number than the one that was signed.
    """
    if isinstance(value, bool):
        return
    if isinstance(value, int):
        if not -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
            raise ValueError(
                f"integer at {path or 'root'} exceeds safe double precision; "
                f"use a string if the exact digits matter"
            )
    elif isinstance(value, dict):
        for key, item in value.items():
            _reject_unsafe_integers(item, f"{path}.{key}" if path else str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_unsafe_integers(item, f"{path}[{index}]")


# ---------------------------------------------------------------------------
# Complaint chain
# ---------------------------------------------------------------------------


@register_event("complaint_submitted", version=1, entity_type=EntityType.COMPLAINT)
class ComplaintSubmittedV1(EventPayload):
    """A citizen submitted a report (§10 step 1).

    Media are referenced by URL, never embedded. A photo inlined into the
    payload would be hashed into an append-only log that must live for years and
    can never be deleted — which collides head-on with the §22 face-blur
    obligation and Phase 26's erasure requirement.
    """

    latitude: Latitude
    longitude: Longitude
    description_text: str | None = None
    photo_url: str | None = None
    audio_url: str | None = None
    #: The tenant's locale tag for the submission, so transcription and
    #: classification know which language models to reach for.
    locale: str | None = None
    #: §11.3 abuse clustering. Never leaves the system; Phase 4 scrubs it.
    device_fingerprint: str | None = None
    submitted_via: str = "web"


@register_event("exif_check_completed", version=1, entity_type=EntityType.COMPLAINT)
class ExifCheckCompletedV1(EventPayload):
    """§11.1 EXIF/GPS cross-check.

    ``exif_present=False`` **reduces trust**; it is not a rejection, and the
    schema keeps that distinction visible by making the distance optional rather
    than defaulting it to zero. A default of zero would read downstream as
    "EXIF confirmed the location", which is the opposite of what happened.
    """

    exif_present: bool
    distance_meters: float | None = Field(default=None, ge=0.0)
    trust_delta: float
    reason: str | None = None


@register_event("safety_trigger_fired", version=1, entity_type=EntityType.COMPLAINT)
class SafetyTriggerFiredV1(EventPayload):
    """§11.2 deterministic fail-safe. Bypasses the queue, so it records *why*."""

    #: Which rule matched. Phase 6 makes the ruleset governed data; the id is
    #: recorded so a decision can always be traced to the exact approved version
    #: that produced it, even after the ruleset changes.
    rule_id: str
    ruleset_version: str
    matched_terms: list[str] = Field(default_factory=list)
    detection_source: str = Field(description="'keyword' | 'visual' | 'both'")


@register_event("classification_scored", version=1, entity_type=EntityType.COMPLAINT)
class ClassificationScoredV1(EventPayload):
    """§10 step 3. CLIP zero-shot against the tenant's taxonomy prompt set."""

    #: A tenant taxonomy node key — never one of a fixed five categories.
    category: str
    confidence: Confidence
    model_id: str
    prompt_set_version: str
    #: Runner-up scores, kept because Phase 11's active learning ranks review
    #: candidates by margin, and a margin cannot be reconstructed after the fact
    #: from the winner alone.
    alternatives: dict[str, float] = Field(default_factory=dict)
    transcript: str | None = None
    detected_language: str | None = None


def _classification_scored_v1_to_v2(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Fill the Phase 9 evidence fields for an event written before Phase 9.

    Every default here is a statement that the information *was not recorded*,
    never a reconstruction. ``margin`` is 0.0 rather than derived from
    ``alternatives``, because a v1 event's alternatives were whatever its writer
    chose to include and a margin computed from a partial distribution is a
    number with no provenance. ``calibration_version`` says so in words for the
    same reason: the alternative — restating today's stamp — would attribute a
    historical decision to a document that did not exist when it was made.
    """
    upcast = dict(payload)
    upcast.setdefault("margin", 0.0)
    upcast.setdefault("raw_similarities", {})
    upcast.setdefault("calibration_version", "unrecorded:pre-phase-9")
    upcast.setdefault("model_ids", {})
    upcast.setdefault("language_confidence", None)
    return upcast


@register_event(
    "classification_scored",
    version=2,
    entity_type=EntityType.COMPLAINT,
    upcaster_from_previous=_classification_scored_v1_to_v2,
)
class ClassificationScoredV2(EventPayload):
    """§10 step 3, as Phase 9 actually performs it.

    **Why a version rather than an edit.** v1 was registered in Phase 2 from
    §9.4's description, before anything could produce one. Editing it would still
    be the wrong move — ``schema_lock.json`` exists so that "nothing has been
    written yet" is never the argument, because the next schema change will be
    made by somebody for whom it is not true, and the rule has to hold before it
    is tested rather than after.

    **What the four new fields buy, concretely.** They make a *calibration*
    change backtestable. Phase 7 replays the log to ask "what would this document
    have decided", and for a rubric or an SLA the log already carries the inputs.
    For classification it did not: v1 recorded the calibrated probabilities and
    nothing about the similarities they were computed from, so replaying a new
    temperature would have required re-embedding every photograph — which costs
    more than the change is worth and silently folds *model* drift into a report
    about *policy*. ``raw_similarities`` is the model's output before any
    governed number touched it, which is exactly the boundary a backtest needs.

    **``model_ids`` is a map rather than more scalar fields.** A submission can
    be scored by up to three models — the image tower, the text encoder, the
    transcriber — and which of them ran depends on what the citizen attached. A
    fixed set of nullable columns would encode today's three into a schema that
    lives forever; the map records what actually ran, keyed by role.
    """

    #: A tenant taxonomy node key — never one of a fixed five categories.
    category: str
    confidence: Confidence
    #: The model that produced the winning modality. Kept from v1 unchanged: it
    #: is what ``complaints.classifier_model_id`` projects from, and Phase 11
    #: will group training examples by it.
    model_id: str
    prompt_set_version: str
    #: Runner-up scores, kept because Phase 11's active learning ranks review
    #: candidates by margin, and a margin cannot be reconstructed after the fact
    #: from the winner alone.
    alternatives: dict[str, float] = Field(default_factory=dict)
    transcript: str | None = None
    detected_language: str | None = None

    #: The winner's lead over the runner-up, after calibration. Recorded rather
    #: than derived on read: ``alternatives`` is truncated by nothing today, but
    #: a future writer that caps it at five entries would silently change what a
    #: derived margin meant for every event after the change.
    margin: float = 0.0
    #: Category → cosine similarity, before temperature, bias, or softmax. The
    #: model's own opinion, which is the only part of this event a calibration
    #: change does not invalidate.
    raw_similarities: dict[str, float] = Field(default_factory=dict)
    #: Which ``perception_calibration`` revision turned those similarities into
    #: the confidence above. The same contract ``severity_scored.policy_version``
    #: has, for the same reason.
    calibration_version: str = "unrecorded"
    #: Role → model id, for every model that contributed. Roles are
    #: ``image``/``text``/``transcribe``, matching ``EncoderKind``.
    model_ids: dict[str, str] = Field(default_factory=dict)
    #: How sure the transcriber was about the language, when one ran. ``None``
    #: for a submission with no audio — distinct from 0.0, which would mean a
    #: transcriber ran and had no idea.
    language_confidence: Confidence | None = None


@register_event("media_transcribed", version=1, entity_type=EntityType.COMPLAINT)
class MediaTranscribedV1(EventPayload):
    """§8.4. A voice complaint became text, and in which language.

    **Not in §9.4, and deliberately added anyway** — the same argument the trust
    spine's events make below. §8.4 promises a citizen can report in Hindi,
    Marathi or English by speaking, and without this event the *only* record that
    a transcription happened is a field inside ``classification_scored``. That
    fails in the case §8.4 cares most about: a voice report whose classification
    abstains emits no ``classification_scored`` at all, so the transcript — the
    thing a human reviewer needs in order to work the report — would exist
    nowhere in the log.

    **Why the transcript is also carried on ``classification_scored``.** Not an
    oversight and not redundancy for its own sake: a classification is only
    re-arguable if the exact text it scored sits next to it, and "read the
    preceding transcription event, assuming one exists and assuming nothing
    re-transcribed in between" is a chain of assumptions a reviewer should not
    have to make. This event records *that a transcription happened*; that field
    records *what was scored*.

    **No audio, ever.** The clip stays in quarantine under §22.4's retention
    clock, exactly like the photograph, for all of ADR-0031's reasons — the log
    lives for years and cannot be redacted after the fact.
    """

    transcript: str
    #: BCP-47, from the model's own detection. Not constrained to the tenant's
    #: declared locales: a citizen who speaks something the tenant did not list
    #: is precisely the population §8.4 exists for, and recording their language
    #: as one of the declared ones would be a fabrication.
    language: str
    language_confidence: Confidence
    #: The clip's length, not the inference time. §27.1's budget is meaningless
    #: without it — a p95 over four-second and ninety-second clips mixed together
    #: tracks the length distribution rather than the model.
    audio_seconds: float = Field(ge=0.0)
    model_id: str
    #: True when detection scored below the tenant's ``min_language_confidence``
    #: and the locale-specific prompt set was therefore *not* used. Recorded
    #: because the alternative is a reviewer wondering why a Marathi report was
    #: scored against the default locale's prompts.
    language_uncertain: bool = False


@register_event("pipeline_stage_degraded", version=1, entity_type=EntityType.COMPLAINT)
class PipelineStageDegradedV1(EventPayload):
    """§24.2: a stage took its fallback path instead of doing its job.

    **Not in §9.4, and deliberately added anyway.** ``system_degradation``
    already records that a dependency failed, but it lives on the ``system``
    chain — which is correct for "Ollama was unreachable for nine minutes" and
    useless for "*this* complaint was processed without a classifier". §24.2
    requires the degraded report to reach ``pending_classification``, and a
    status is projected state: reaching it without an event on the complaint's
    own chain would mean mutating a row the log does not explain, which is the
    one thing §9.1 forbids.

    So both are emitted, and they are not redundant. The system event answers
    "what broke"; this one answers "which citizen reports were affected", six
    months later, from the complaint's own history.

    ``fallback_taken`` is a plain string carrying a ``DegradationFallback``
    value rather than a ``Literal``. A closed set here would make adding a
    fourth fallback a payload version bump plus an upcaster, for a change that
    invalidates nothing already written — and the projector is total over
    unrecognised values (it records the degradation and leaves the status
    alone), so the open type costs no determinism.
    """

    stage: str
    failure_mode: str
    fallback_taken: str = Field(
        description="A DegradationFallback value: what was done instead of the stage's work"
    )
    #: Attempts made before giving up, so the retry budget that was actually
    #: spent is recoverable from the log rather than only from worker logs that
    #: have long since rotated away.
    attempts: int = Field(ge=1)
    correlation_id: str | None = None


@register_event("severity_scored", version=1, entity_type=EntityType.COMPLAINT)
class SeverityScoredV1(EventPayload):
    """§13.5 rubric evaluation — see the module docstring on the type name.

    ``components`` is the whole point: Phase 12's gate requires a scored
    complaint to reproduce its score from its own logged breakdown, which is
    only possible if every weighted input is recorded next to the total.
    """

    score: float = Field(ge=0.0, le=10.0)
    components: dict[str, float]
    weights: dict[str, float]
    #: Which rubric produced this. Phase 6 makes it a policy document id.
    policy_version: str


# ---------------------------------------------------------------------------
# Trust spine (Phase 8) — still the complaint's own chain
# ---------------------------------------------------------------------------
#
# **Why these are not in §9.4, and are registered anyway.** The blueprint's
# catalog names ``exif_check_completed`` and ``safety_trigger_fired`` — the two
# §11 outcomes it anticipated — and stops there. §11.1's perceptual hashing,
# §11.3's coordinated-abuse detection, §11.4's review queue and §22.1's face
# blur are all stated obligations with no event to record them, which would make
# each one a state change the log does not explain. The blueprint is a floor
# (see the tenant-chain note below); ``check_event_catalog.py`` requires every
# §9.4 row to be registered or deferred and does not forbid types §9.4 omitted.
#
# **Why they live on the complaint's chain rather than a chain of their own.**
# Every one of them is a fact *about this report*: the photograph attached to it
# was redacted, this report looks like an earlier one, this report was queued
# and a human decided. The question they exist to answer is "what happened to my
# complaint", and the answer has to be readable in order from one place.


@register_event("media_redacted", version=1, entity_type=EntityType.COMPLAINT)
class MediaRedactedV1(EventPayload):
    """§22.1 face blur, recorded as the thing that makes the claim auditable.

    **``faces_detected`` and ``faces_blurred`` are separate fields and both are
    required.** They are equal on every successful redaction, which is exactly
    why the schema keeps them apart: a future change that blurs only the largest
    face, or that drops a box for being too small, shows up here as a divergence
    rather than as an unchanged "we blurred it" boolean. §22.1 is a promise
    about *every* face, and a single flag cannot express failing it.

    ``detector_id`` carries the model and its version. "Faces were blurred" with
    no record of what did the blurring is not evidence — it is the same claim
    the pre-Phase-8 code could have made by doing nothing.

    ``exif_stripped`` is here rather than assumed because the redacted copy is
    the one the review queue and every later phase serve, and a served image
    still carrying its capture GPS would re-leak the location §22.1 coarsens.
    """

    #: SHA-256 of the uploaded bytes. Identifies which artefact this is about
    #: without the payload carrying a path, which would tie the log to a storage
    #: layout that will change.
    source_sha256: str = Field(min_length=64, max_length=64)
    #: SHA-256 of the redacted bytes — a different value, always, when a face
    #: was blurred, and the handle a dispute uses to prove which image was shown.
    redacted_sha256: str = Field(min_length=64, max_length=64)
    media_kind: str = Field(description="'image' | 'audio'")
    content_type: str
    faces_detected: int = Field(ge=0)
    faces_blurred: int = Field(ge=0)
    detector_id: str
    exif_stripped: bool

    @model_validator(mode="after")
    def _blurred_cannot_exceed_detected(self) -> MediaRedactedV1:
        if self.faces_blurred > self.faces_detected:
            raise ValueError(
                f"faces_blurred ({self.faces_blurred}) exceeds faces_detected "
                f"({self.faces_detected}); the redaction reported blurring regions "
                f"the detector never found, which means the two numbers came from "
                f"different runs and neither can be trusted"
            )
        return self


@register_event("perceptual_duplicate_detected", version=1, entity_type=EntityType.COMPLAINT)
class PerceptualDuplicateDetectedV1(EventPayload):
    """§11.1 perceptual hashing found this photograph before.

    **Not a dedup event, and the distinction is the whole reason this is a
    separate type.** §14's ``cluster_match_found`` says *two citizens reported
    the same problem*, which is the system working. This says *the same image
    file was submitted twice*, which is a fraud signal: a screenshot, a re-used
    photograph, a report padded to inflate a ward's numbers. Merging the two
    would make a successful dedup indistinguishable from an attempted fraud.

    ``hamming_distance`` and ``threshold`` are both recorded so a disputed flag
    can be re-argued against the number that produced it rather than against
    whatever the threshold has since become.
    """

    matched_complaint_id: uuid.UUID
    matched_media_sha256: str = Field(min_length=64, max_length=64)
    hamming_distance: int = Field(ge=0, le=64)
    threshold: int = Field(ge=0, le=64)
    #: Hours between the two captures. A re-upload minutes apart and one a year
    #: apart are different claims, and the window is policy that will change.
    age_hours: float = Field(ge=0.0)
    trust_delta: float
    policy_version: str


@register_event("abuse_pattern_flagged", version=1, entity_type=EntityType.COMPLAINT)
class AbusePatternFlaggedV1(EventPayload):
    """§11.3 coordinated abuse. **Flags, never blocks** — and says so in its shape.

    There is no ``blocked`` field and no ``action_taken`` field, because §11.3 is
    explicit that detection routes to human review rather than auto-rejecting.
    A schema with a slot for an enforcement action is a schema that invites one,
    and the first false positive would suppress a real citizen's report about a
    real hazard on the strength of a device fingerprint.

    ``evidence`` is a free-shaped map for the same reason
    ``pipeline_stage_degraded.fallback_taken`` is an open string: the detectors
    are the part of this phase most likely to gain a signal, and a closed schema
    would make each new one a payload version bump plus an upcaster for a change
    that invalidates nothing already written. What is *not* open is the field
    list around it — the pattern, the window, and the count are the three things
    every detector must be able to state.
    """

    #: An ``AbusePattern`` value: which detector fired.
    pattern: str
    #: How many submissions the detector saw inside the window.
    observation_count: int = Field(ge=1)
    window_hours: float = Field(gt=0.0)
    trust_delta: float
    policy_version: str
    evidence: dict[str, Any] = Field(default_factory=dict)


@register_event("review_queued", version=1, entity_type=EntityType.COMPLAINT)
class ReviewQueuedV1(EventPayload):
    """§11.4: a flag reached its destination. "No flag is ever a dead end."

    ``evidence_hash`` rather than the bundle itself. The bundle contains the
    photograph reference, the EXIF distance, and similarity scores — it is
    exactly the material §22 requires to expire on a retention clock, and an
    append-only log is the one place a value cannot be expired from. The hash
    pins *which* bundle a reviewer saw without making the log the place it
    lives, and it is what ``review_decided`` matches against so a label can
    never be attributed to evidence that did not produce it.
    """

    review_item_id: uuid.UUID
    #: A ``ReviewReason`` value.
    reason: str
    priority: int = Field(ge=0)
    #: Rising on a repeat rather than a second event, so the log shows one
    #: escalating item instead of a queue of identical ones.
    occurrences: int = Field(ge=1)
    trust_score: float
    evidence_hash: str = Field(min_length=64, max_length=64)


@register_event("review_decided", version=1, entity_type=EntityType.COMPLAINT)
class ReviewDecidedV1(EventPayload):
    """A human decided, and the decision becomes a Phase 11 label.

    Architectural principle 4 — *every human decision is training data* — is
    only true if the decision and the inputs it was made against are recorded
    together. ``evidence_hash`` is repeated from ``review_queued`` deliberately:
    matching them is what proves the label belongs to that example, and a label
    whose inputs cannot be identified is noise with a confident tone.

    ``decided_by`` is optional and ``decided_by_label`` is not. Until Phase 13
    gives operators identities, the control-plane token is a shared secret, and
    recording a shared secret's use as a named person would be a worse record
    than recording none — so the label says what is actually known.
    """

    review_item_id: uuid.UUID
    reason: str
    #: ``approve`` | ``reject`` | ``escalate`` — §11.4's three actions.
    decision: str
    #: Required. A decision with no stated reason answers "what" and refuses to
    #: answer "why", which is the objection ``admin_action.justification`` closes.
    rationale: str = Field(min_length=1)
    decided_by: uuid.UUID | None = None
    decided_by_label: str
    evidence_hash: str = Field(min_length=64, max_length=64)


@register_event("complaint_clustered", version=1, entity_type=EntityType.COMPLAINT)
class ComplaintClusteredV1(EventPayload):
    """Which incident this report belongs to, on the report's own chain.

    **Why this exists alongside ``cluster_match_found``, which records the same
    merge.** The two are on different chains and neither can be read from the
    other. §9.1's rule is that an entity's current state derives from its own
    event log, and a complaint whose ``cluster_id`` could only be learned by
    replaying every cluster in the tenant would break that rule in the most
    expensive possible direction — the projector for one complaint would have to
    scan an unbounded number of unrelated chains. So the cluster chain records
    *what happened to the incident* and the complaint chain records *what
    happened to the report*, and the dedup stage appends both in one
    transaction.

    ``outcome`` is carried because a cluster of one is produced by three quite
    different situations — nothing nearby, something nearby that scored too low,
    and something nearby that scored high enough to be ambiguous — and a reader
    who cannot tell them apart will read the third as the first. It is also the
    field that makes §14.1's middle band observable: an ``investigate`` rate of
    zero means the ambiguous band has collapsed and dedup has quietly become
    binary, which is the failure ``DedupBand`` validates against at authoring
    time and this makes visible at run time.
    """

    cluster_id: uuid.UUID
    #: ``merge``, ``investigate`` or ``distinct``.
    outcome: str
    #: Absent when the report was the first thing in its neighbourhood, so there
    #: was nothing to be confident about.
    combined_confidence: Confidence | None = None
    #: The Phase 6 policy version whose bands decided. Stamped here as well as
    #: on the cluster event so the report's own chain is self-contained.
    policy_version: str


# ---------------------------------------------------------------------------
# Cluster chain
# ---------------------------------------------------------------------------


@register_event("cluster_created", version=1, entity_type=EntityType.COMPLAINT_CLUSTER)
class ClusterCreatedV1(EventPayload):
    """No match found — this report is a new incident."""

    seed_complaint_id: uuid.UUID
    latitude: Latitude
    longitude: Longitude


@register_event("cluster_match_found", version=1, entity_type=EntityType.COMPLAINT_CLUSTER)
class ClusterMatchFoundV1(EventPayload):
    """§14 dedup merged a report into an existing incident.

    Records both stage scores and the band that decided, so a disputed merge can
    be re-argued from the event rather than from a screenshot — and so Phase 7
    can backtest a threshold change against what the old thresholds actually did.
    """

    complaint_id: uuid.UUID
    geo_distance_meters: float = Field(ge=0.0)
    image_similarity: Confidence | None = None
    text_similarity: Confidence | None = None
    combined_confidence: Confidence
    #: The Phase 6 policy version whose bands produced this decision.
    policy_version: str
    report_count_after: int = Field(ge=1)


@register_event("cluster_merge_reverted", version=1, entity_type=EntityType.COMPLAINT_CLUSTER)
class ClusterMergeRevertedV1(EventPayload):
    """§14.3 merge reversibility, as a compensating event.

    An incorrect merge suppresses a real citizen report, so undoing one must be
    possible — and it is done by appending this, never by deleting the merge.
    History records that the system was wrong and then corrected itself; editing
    the log would record only that it was always right.
    """

    complaint_id: uuid.UUID
    reverted_by: uuid.UUID | None = None
    reason: str


# ---------------------------------------------------------------------------
# Work order chain
# ---------------------------------------------------------------------------


@register_event("work_order_created", version=1, entity_type=EntityType.WORK_ORDER)
class WorkOrderCreatedV1(EventPayload):
    cluster_id: uuid.UUID
    department_id: uuid.UUID | None = None
    #: Which routing rule fired, so silent misrouting is diagnosable.
    routing_rule_id: str | None = None
    policy_version: str | None = None


@register_event("work_order_assigned", version=1, entity_type=EntityType.WORK_ORDER)
class WorkOrderAssignedV1(EventPayload):
    """§15.3. Records the ranking inputs, which is what makes §17's favouritism
    detection possible: an assignment with no recorded reason cannot be audited."""

    assignee_type: str = Field(description="'staff' | 'contractor'")
    assignee_id: uuid.UUID
    sla_deadline: str
    sla_policy_version: str | None = None
    selection_rank: int | None = Field(default=None, ge=1)


@register_event("ssim_verification_completed", version=1, entity_type=EntityType.WORK_ORDER)
class SsimVerificationCompletedV1(EventPayload):
    """§21.2 before/after structural comparison.

    ``perceptual_hash_matched`` is the §15 guard against submitting the "before"
    photo as the "after". It is a separate field from the SSIM score because a
    resubmitted identical photo scores a *perfect* similarity — the check that
    catches fraud and the check that confirms repair would otherwise be the same
    number pointing in opposite directions.
    """

    ssim_score: float = Field(ge=-1.0, le=1.0)
    threshold: float
    passed: bool
    perceptual_hash_matched: bool = False


@register_event("citizen_confirmation_requested", version=1, entity_type=EntityType.WORK_ORDER)
class CitizenConfirmationRequestedV1(EventPayload):
    window_hours: int = Field(gt=0)
    channel: str


@register_event("citizen_confirmed", version=1, entity_type=EntityType.WORK_ORDER)
class CitizenConfirmedV1(EventPayload):
    """§21 closure.

    ``auto_confirmed`` is mandatory and never inferred from a null confirming
    user. §44 requires an auto-confirmed closure to stay distinguishable from a
    real one in the API, the UI, and the contractor's computed rating — a
    distinction that only survives if the log recorded it explicitly.
    """

    auto_confirmed: bool
    confirmed_by: uuid.UUID | None = None


@register_event("citizen_disputed", version=1, entity_type=EntityType.WORK_ORDER)
class CitizenDisputedV1(EventPayload):
    reason: str
    disputed_by: uuid.UUID | None = None
    evidence_url: str | None = None


# ---------------------------------------------------------------------------
# Tenant chain — the control plane's own history (Phase 5)
# ---------------------------------------------------------------------------
#
# **Why the control plane writes to the event log at all.** A tenant's taxonomy
# is not application state that can be re-derived; it is the definition against
# which every classification, route, and SLA was decided. "Why was this
# complaint categorised as `lab_spill` when that category no longer exists?" is a
# question a citizen or an auditor can legitimately ask years later, and the only
# thing that can answer it is a record of what the taxonomy was at the time.
#
# **Why they are not in §9.4.** The blueprint's catalog was written for a
# single-tenant deployment with a fixed domain model, so it has no vocabulary for
# a control plane. `check_event_catalog.py` requires every §9.4 type to be
# registered or explicitly deferred; it does not forbid types the blueprint never
# imagined, which is the correct asymmetry — the blueprint is a floor.
#
# **Why one chain per tenant rather than one per node.** These events are
# per-tenant configuration history, and their value is being readable *in order*:
# "the taxonomy changed, then the calendar changed, then everything scored
# differently". A chain per taxonomy node would make that ordering unrecoverable
# without a global sort across thousands of chains.


@register_event("tenant_provisioned", version=1, entity_type=EntityType.TENANT)
class TenantProvisionedV1(EventPayload):
    """A tenant was created, and from what.

    ``template`` is recorded because the seeded library is the thing that will
    change: a campus onboarded from template v1 and one onboarded from v3 have
    different defaults, and six months later that difference is the explanation
    for a support question nobody can otherwise answer.
    """

    slug: str
    name: str
    plan: str
    primary_locale: str
    locales: list[str]
    timezone: str
    data_residency: str
    #: ``None`` when a tenant is provisioned bare, which is a supported and
    #: distinct case from "provisioned from a template that happened to be empty".
    template: str | None = None
    template_version: str | None = None


@register_event("taxonomy_published", version=1, entity_type=EntityType.TENANT)
class TaxonomyPublishedV1(EventPayload):
    """The tenant's taxonomy reached a new revision.

    ``content_hash`` is the canonical hash of the whole taxonomy, not of the
    change. That makes the event answer the question actually asked at audit
    time — "what was the taxonomy when this complaint was classified" — with one
    comparison, instead of requiring a replay that folds every prior revision.

    The changed keys are carried alongside because a revision that touched one
    node and a revision that rewrote the tree are operationally different events,
    and a hash alone cannot distinguish them.
    """

    revision: int = Field(ge=1)
    node_count: int = Field(ge=0)
    content_hash: str = Field(min_length=64, max_length=64)
    changed_keys: list[str] = Field(default_factory=list)
    #: 'created' | 'updated' | 'deactivated' | 'imported' — free text for the
    #: same reason ``pipeline_stage_degraded.fallback_taken`` is: a closed set
    #: here makes a fifth kind of change a payload version bump plus an upcaster,
    #: for a change that invalidates nothing already written.
    change_kind: str


@register_event("organisation_changed", version=1, entity_type=EntityType.TENANT)
class OrganisationChangedV1(EventPayload):
    """A department, zone, shift, calendar, or certification was altered.

    One event type covering five tables, rather than five types. They share a
    shape, they share a chain, and they are read together — the question is
    always "what changed about this tenant's organisation", never "show me only
    the shift edits". Five near-identical schemas would be five things to keep in
    step and five upcasters to write the first time the shape moves.
    """

    #: Which table — 'department', 'zone', 'shift', 'calendar', 'certification'.
    subject: str
    #: The tenant-facing identifier (a code), not the UUID. An operator reading
    #: this during an incident knows the code; the UUID is in ``subject_id``.
    subject_key: str
    subject_id: uuid.UUID
    change_kind: str
    #: Field names only, never values. §22 applies to the audit trail too, and a
    #: department's contact details are personal data that must not be duplicated
    #: into an append-only log Phase 26 then has to erase from.
    changed_fields: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Policy chain — governed configuration history (Phase 6)
# ---------------------------------------------------------------------------
#
# On the *tenant* chain, alongside `taxonomy_published` and `organisation_changed`
# rather than on a chain of their own. The question these answer is never "show
# me the policy edits in isolation"; it is "the taxonomy changed, then the rubric
# changed, then everything scored differently" — and that ordering is only
# recoverable if the three share a chain.
#
# **Two event types, not one per verb.** `policy_drafted` records that a document
# now exists with a given content; `policy_transitioned` records every movement
# through the lifecycle, including activation and rollback. Splitting further —
# `policy_approved`, `policy_activated`, `policy_rejected` — would put the
# lifecycle's shape into the event catalog, where changing it means a payload
# version bump and an upcaster for a change that invalidates nothing already
# written. `organisation_changed` makes the same call for the same reason.


@register_event("policy_drafted", version=1, entity_type=EntityType.TENANT)
class PolicyDraftedV1(EventPayload):
    """A new revision of a governed structure was authored (Phase 6).

    ``content_hash`` is the canonical hash of the document body, so the chain
    can prove which bytes were drafted without carrying the body itself. The
    body deliberately does not go into the payload: a safety ruleset is a few
    kilobytes of terms, it changes often, and inlining it would grow an
    append-only log that must live for years by the full document on every edit
    — the same reasoning ``complaint_submitted`` applies to photographs.
    """

    kind: str
    revision: int = Field(ge=1)
    content_hash: str = Field(min_length=64, max_length=64)
    #: The revision this was derived from, or ``None`` when authored fresh.
    based_on_revision: int | None = Field(default=None, ge=1)
    #: Set when this draft exists to restore an earlier version. Recorded here
    #: rather than inferred from equal content hashes: two revisions can be
    #: byte-identical by coincidence, and a rollback is an operator decision
    #: that an incident review has to be able to find.
    rolled_back_from_revision: int | None = Field(default=None, ge=1)
    change_reason: str


@register_event("policy_transitioned", version=1, entity_type=EntityType.TENANT)
class PolicyTransitionedV1(EventPayload):
    """A policy document moved through its lifecycle (Phase 6).

    Phase 6's gate requires *every* transition to be an event in the same hash
    chain, which is what makes "an unapproved draft never influenced a decision"
    a provable claim rather than a policy: the chain contains the activation, so
    a decision stamped with a version whose activation is absent is detectable.

    ``effective_from`` is carried because activation may be future-dated, and
    "when was this approved" and "when did it start applying" are different
    questions that a single timestamp would conflate.
    """

    kind: str
    revision: int = Field(ge=1)
    from_status: str
    to_status: str
    #: Required on every transition. The same argument
    #: ``admin_action.justification`` makes: a lifecycle record that answers
    #: "what" and refuses to answer "why" is half an audit trail, and the half
    #: it keeps is the half nobody needs during an incident.
    reason: str
    effective_from: str | None = None
    #: The revision this one displaced, on an activation. Null on every other
    #: transition, and on the first activation of a kind.
    superseded_revision: int | None = Field(default=None, ge=1)


# ---------------------------------------------------------------------------
# Simulation & evaluation chain (Phase 7) — also on the tenant entity
# ---------------------------------------------------------------------------
#
# **Why these are events at all.** A backtest reads and decides nothing, so it
# is tempting to leave it as a row in `simulation_runs` and nothing more. But
# the three types below are not records of *computation* — they are records of
# **evidence being created, accepted, or set aside**, and each one changes what
# the system will allow. Publishing an evaluation set makes activations
# refusable; a passing certificate makes one possible; a waiver makes one
# possible without the certificate. An audit that can see the rubric change and
# not see the control that was supposed to stop it is an audit of the wrong half.
#
# `simulation_run_completed` is deliberately **not** here: a run that issues no
# certificate changes nothing, and putting every exploratory backtest on an
# append-only chain that must live for years would bury the three types that
# matter under thousands that do not. The run row carries it, queryable and
# prunable, which is the right home for telemetry.


@register_event("evaluation_set_published", version=1, entity_type=EntityType.TENANT)
class EvaluationSetPublishedV1(EventPayload):
    """A labelled evaluation set became the gate for a policy kind (Phase 7).

    Publication is what turns the guardrail on — there is no separate flag — so
    this event is the record of a control being *created*. ``labels_hash`` is
    carried rather than the labels themselves, for the reason ``policy_drafted``
    carries ``content_hash``: it proves which questions were on the exam without
    inlining a few hundred judgements into a log that lives for years.
    """

    code: str
    kind: str
    label_count: int = Field(ge=1)
    labels_hash: str = Field(min_length=64, max_length=64)
    #: The share of labels a candidate must satisfy. In the payload because a
    #: certificate's verdict cannot be re-derived from its counts without it.
    pass_ratio: float = Field(gt=0.0, le=1.0)
    #: The set this one replaced, if any. Publication retires the incumbent in
    #: the same transaction, and recording which one keeps the succession
    #: walkable.
    retired_code: str | None = None


@register_event("evaluation_set_retired", version=1, entity_type=EntityType.TENANT)
class EvaluationSetRetiredV1(EventPayload):
    """A guardrail was switched off for a policy kind (Phase 7).

    Its own event rather than a quiet status update. Removing the control that
    stops an unevaluated rubric reaching production is at least as consequential
    as changing the rubric, and a chain that recorded the second and not the
    first would let the interesting half of an incident happen off the record.
    """

    code: str
    kind: str


@register_event("policy_certified", version=1, entity_type=EntityType.TENANT)
class PolicyCertifiedV1(EventPayload):
    """A candidate document was marked against an evaluation set (Phase 7).

    Emitted on a failure as well as a pass. "We evaluated this and it failed" is
    the record that matters when the same candidate is activated a week later by
    somebody who did not know — a chain holding only passes would make a history
    of refusals indistinguishable from a history of nobody looking.

    ``labels_unresolvable`` is separate from a failure because the two are
    different findings: a complaint whose partition has been archived is a fact
    about retention, not about the candidate.
    """

    kind: str
    revision: int = Field(ge=1)
    content_hash: str = Field(min_length=64, max_length=64)
    evaluation_set_code: str
    labels_hash: str = Field(min_length=64, max_length=64)
    verdict: str = Field(description="'pass' | 'fail'")
    labels_evaluated: int = Field(ge=0)
    labels_passed: int = Field(ge=0)
    labels_unresolvable: int = Field(ge=0)


@register_event("policy_certification_waived", version=1, entity_type=EntityType.TENANT)
class PolicyCertificationWaivedV1(EventPayload):
    """An activation proceeded without a passing certificate (Phase 7).

    The only path that reaches this today is ``policy.service.rollback``, whose
    restored content was previously live and therefore previously certified.
    That justification is written into ``waiver`` rather than assumed, because
    the question this event exists to answer is *"which activations bypassed the
    evaluation set, and on what grounds"* — and an answer that requires knowing
    which code path emitted it is not an answer an auditor can reach.
    """

    kind: str
    revision: int = Field(ge=1)
    content_hash: str = Field(min_length=64, max_length=64)
    evaluation_set_code: str
    waiver: str


# ---------------------------------------------------------------------------
# Cross-cutting
# ---------------------------------------------------------------------------


@register_event("admin_action", version=1, entity_type=EntityType.ADMIN_ACTION)
class AdminActionV1(EventPayload):
    """§9.4: any super-admin action, logged, never a silent edit."""

    action: str
    target_entity_type: str | None = None
    target_entity_id: uuid.UUID | None = None
    #: Required, not optional. An audited action with no stated reason is an
    #: audit trail that answers "what" and refuses to answer "why".
    justification: str
    changes: dict[str, Any] = Field(default_factory=dict)


@register_event("system_degradation", version=1, entity_type=EntityType.SYSTEM)
class SystemDegradationV1(EventPayload):
    """The failure-policy event every external call must emit on fallback.

    In the log rather than only in metrics because a complaint processed during
    a degradation was processed *differently*, and six months later the only way
    to know that is for the log to say so next to the complaint's own events.
    """

    component: str
    failure_mode: str
    fallback_taken: str
    correlation_id: str | None = None


# ---------------------------------------------------------------------------
# Not yet registered — with the phase that owns each shape.
# ---------------------------------------------------------------------------

#: §9.4 types whose payload is determined by work that has not been done. Each
#: is claimed by the phase that will build it; ``check_event_schemas.py``
#: requires every §9.4 row to be either registered above or listed here, so the
#: catalog can never drift silently away from the blueprint.
#: §9.4 names this catalog deliberately does not use, and what replaced each.
#: Declared rather than quietly absorbed: ``check_event_catalog.py`` compares
#: this catalog against the blueprint, and a rename that is not recorded is
#: indistinguishable from a missing event type.
RENAMED_EVENT_TYPES: Final[dict[str, str]] = {
    # See the module docstring. Versioning the rubric inside the event *type*
    # makes rubric v2 an unrelated event with no upcaster path from v1 — the
    # log would contain two names for one thing and no way to relate them.
    "severity_rubric_v1_scored": "severity_scored",
}

DEFERRED_EVENT_TYPES: Final[dict[str, str]] = {
    "investigation_agent_invoked": "Phase 16 — payload is the agent's state machine entry",
    "investigation_agent_evidence_gathered": "Phase 16 — shape follows the tool registry",
    "investigation_agent_concluded": "Phase 16 — §12.4 fixes the conclusion shape",
    "budget_allocated": "Phase 14 — depends on the Phase 6 rate card structure",
    "budget_spent": "Phase 14 — as above",
    "milestone_evidence_uploaded": "Phase 14 — milestone set is tenant workflow data",
    "anomaly_flagged": "Phase 17 — detector outputs define the evidence payload",
    "dispute_raised": "Phase 17 — contractor appeal workflow",
    "dispute_resolved": "Phase 17 — as above",
}
