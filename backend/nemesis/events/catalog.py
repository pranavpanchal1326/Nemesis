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
