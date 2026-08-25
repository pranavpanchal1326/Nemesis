"""What one complaint's own history may say to whoever holds its id.

**This is ADR-0016's second shape, not a relaxation of its first.**
``nemesis/realtime/envelope.py`` governs a *broadcast*: an unauthenticated,
tenant-scoped stream that anyone who knows a tenant id can open, carrying every
report in the city. This module governs a *capability-scoped read*: one
complaint, addressed by a UUIDv4 that the system hands to exactly one person —
the submitter, in the 202 body and on §E17.3's receipt — and to the officers who
work it. ADR-0016 itself anticipated the split: *"an authenticated department
user may legitimately receive more than an anonymous map viewer. That is a
second shape per event type, not a removal of the first."*

The two tables are declared separately and deliberately duplicate the handful of
shapes that happen to agree today. A shared helper would mean that widening what
a citizen may see about their own report silently widens what the whole city
sees about theirs, which is the one coupling this file exists to prevent.

**The rule, in one sentence.** The history publishes *that* an event happened,
and publishes a field only where a shaper below allows it.

That first half is the part worth arguing, because the alternative is tempting.
Hiding the rows a citizen may not read in full would leave gaps in the sequence,
and a gap is either invisible — in which case §E17.3's claim that *"this record
cannot be edited"* is unverifiable, since a removed row and a suppressed row look
identical — or it is visible, in which case it announces itself anyway. Two of
the fourteen types on a complaint's chain are ones a bad actor would like to know
about (``abuse_pattern_flagged``, ``perceptual_duplicate_detected``), and both of
them *always* also append a ``review_queued``, so concealing the type name buys
nothing it does not already give away. What concealment would cost is real: a
citizen with a false-positive flag would see their report stall with no
explanation, on a product whose entire proposition is that the record can be
read. So every row is disclosed as a row, and the payloads carry the judgement.

**Hashes are published per row.** ``previous_hash`` and ``event_hash`` reveal
nothing — the preimage carries a microsecond timestamp, so no payload here is
recoverable by search — and publishing them turns the ledger from a list into a
chain the reader can check link by link against the head. That is what makes
§E17.3's sentence a property rather than a slogan.

Nothing here imports the database. Like ``realtime/envelope.py``, this is a pure
function from a stored payload to a publishable one, which is what makes
"no withheld field reaches a citizen" a unit test rather than a fixture.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Final

#: Builds the citizen-visible payload for one event type from its stored one.
HistoryShaper = Callable[[Mapping[str, Any]], dict[str, Any]]


def _complaint_submitted(_: Mapping[str, Any]) -> dict[str, Any]:
    """Nothing. The row is the ledger's first line; the payload is the report.

    ``latitude``/``longitude`` are the exact position §22.1 treats as personal
    data, ``description_text`` is what the citizen typed, ``photo_url`` and
    ``audio_url`` are handles to artefacts, and ``device_fingerprint`` is §11.3
    abuse-detection data that §22 forbids leaving the system at all. A reader
    holding the complaint id already knows what they submitted; echoing it back
    to whoever else holds the id buys nothing and risks everything.
    """
    return {}


def _media_transcribed(payload: Mapping[str, Any]) -> dict[str, Any]:
    """That a voice report became text, in which language, and how long it ran.

    The transcript itself stays out. It is the citizen's own words, and the id
    is a capability rather than proof of who is holding it — so the honest
    position is that this endpoint can confirm §8.4's promise was kept without
    republishing the sentence that proves it.

    ``language_uncertain`` is included because it changes what happened next:
    below the tenant's confidence floor the locale-specific prompt set was *not*
    used, and a reader wondering why a Marathi report was scored against the
    default locale's prompts deserves the answer from the record.
    """
    return {
        "language": payload.get("language"),
        "language_uncertain": payload.get("language_uncertain"),
        "audio_seconds": payload.get("audio_seconds"),
    }


def _exif_check_completed(payload: Mapping[str, Any]) -> dict[str, Any]:
    """§11.1's cross-check, in full except for the arithmetic.

    ``distance_meters`` and ``reason`` are published here and withheld from the
    broadcast, and the asymmetry is the whole point of there being two tables:
    this is the distance between *your* photograph and *your* report, stated to
    someone holding the handle to it.

    ``trust_delta`` is withheld even here, and for a reason that is not privacy.
    The trust score is the §11.3 control surface, and publishing what each
    behaviour costs is publishing the gradient an abuser needs to descend. The
    finding is disclosed; the price is not.
    """
    return {
        "exif_present": payload.get("exif_present"),
        "distance_meters": payload.get("distance_meters"),
        "reason": payload.get("reason"),
    }


def _media_redacted(payload: Mapping[str, Any]) -> dict[str, Any]:
    """§22.1's promise, and the two numbers that let a reader check it was kept.

    ``faces_detected`` and ``faces_blurred`` are separate fields in the catalog
    precisely so a redaction that blurred *some* faces is distinguishable from
    one that blurred all of them. Publishing only one of them, or a single
    boolean, would make failing §22.1 invisible on the one surface where the
    person whose photograph it is happens to be looking.

    ``detector_id`` names the model and version. §E17.4's *"why? →"* opens the
    real rubric and its version for a severity score; the same argument applies
    to the thing that decided where the faces were.

    **Neither SHA-256.** ``redacted_sha256`` is a working content address on
    ``/api/v1/review/media/{sha}``, and ``source_sha256`` addresses the
    *unblurred* original. A hash in a JSON body that resolves to an image is not
    a hash, it is a URL with extra steps (ADR-0031).
    """
    return {
        "media_kind": payload.get("media_kind"),
        "faces_detected": payload.get("faces_detected"),
        "faces_blurred": payload.get("faces_blurred"),
        "exif_stripped": payload.get("exif_stripped"),
        "detector_id": payload.get("detector_id"),
    }


def _safety_trigger_fired(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The same shape the broadcast gets, and for the same two reasons.

    ``matched_terms`` republishes the citizen's own text verbatim, and it is the
    part of a safety trigger most certain to be sensitive. ``rule_id`` and
    ``ruleset_version`` name a governed rule an abuser would tune against; §11.2
    is a deterministic fail-safe, and a deterministic fail-safe whose rules are
    published is a fail-safe with a documented bypass.
    """
    return {"detection_source": payload.get("detection_source")}


def _classification_scored(payload: Mapping[str, Any]) -> dict[str, Any]:
    """What it was called and how sure the model was.

    ``alternatives``, ``raw_similarities``, ``margin`` and ``model_ids`` are the
    diagnostics Phase 11's active learning ranks on. They are legitimate
    operator material and they are not what §E17.4's ledger is for — a citizen
    asking *what happened to my report* is not asking for a softmax.
    ``transcript`` is withheld for ``_media_transcribed``'s reason.
    """
    return {
        "category": payload.get("category"),
        "confidence": payload.get("confidence"),
        "calibration_version": payload.get("calibration_version"),
    }


def _severity_scored(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The score **and the policy version that produced it**.

    The version is the difference between this and the broadcast shape, and it
    is what makes §E17.4's *"why? →"* answerable: the weights are governed data
    with a revision id, and without the id the link opens whatever the rubric
    happens to be today rather than the one that scored this report.
    """
    return {
        "score": payload.get("score"),
        "policy_version": payload.get("policy_version"),
    }


def _complaint_clustered(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Which incident this report joined, and on what evidence.

    ``cluster_id`` is published because ``GET /complaints/{id}`` already
    publishes it on the projection — withholding it here would be a second
    answer to a question the read path has already answered, which is worse than
    either answer alone.

    ``outcome`` is carried because a cluster of one has three quite different
    causes, and §E17.2's dedup payoff — *"you're the 4th person to report
    this"* — is a different sentence from *"nothing like this was nearby"*.
    """
    return {
        "cluster_id": _as_str(payload.get("cluster_id")),
        "outcome": payload.get("outcome"),
        "combined_confidence": payload.get("combined_confidence"),
        "policy_version": payload.get("policy_version"),
    }


def _pipeline_stage_degraded(payload: Mapping[str, Any]) -> dict[str, Any]:
    """§24.2's third outcome, in full. Nothing here is about a person.

    This is the event behind §E16.1's *CLASSIFIER UNAVAILABLE · PARKED FOR HUMAN
    REVIEW* stamp, and a stamp that cannot say which stage failed or what was
    done instead is decoration. The whole payload is operational fact.
    """
    return {
        "stage": payload.get("stage"),
        "failure_mode": payload.get("failure_mode"),
        "fallback_taken": payload.get("fallback_taken"),
        "attempts": payload.get("attempts"),
    }


def _perceptual_duplicate_detected(_: Mapping[str, Any]) -> dict[str, Any]:
    """The fact, and none of the numbers.

    ``matched_complaint_id`` is a handle to a **different citizen's report** and
    is the hardest withholding in this file — §26.4 forbids citizen identifiers
    on a published surface, and the id of the report you were matched against is
    exactly that. ``hamming_distance`` and ``threshold`` together publish how far
    a re-upload has to be perturbed to clear the check, which is a recipe.
    """
    return {}


def _abuse_pattern_flagged(_: Mapping[str, Any]) -> dict[str, Any]:
    """The fact, and none of the detector.

    ADR-0033 is that abuse detection **flags and cannot block**, so a reader
    learning that a pattern check fired learns that a human will look, not that
    anything was done to them — and they were going to learn that from the
    ``review_queued`` row on the next line regardless.

    ``pattern``, ``observation_count`` and ``window_hours`` are the gradient: the
    detector's name, its threshold, and the window it watches. A campaign that
    can read those can A/B-test its way under them. It cannot do that against a
    binary outcome it also has no way to attribute.
    """
    return {}


def _review_queued(_: Mapping[str, Any]) -> dict[str, Any]:
    """*"No flag is ever a dead end"* — §11.4, stated to the person it is about.

    ``reason`` is withheld and it is the field a reader would most want. For an
    EXIF mismatch it would be harmless and for an abuse pattern it would be the
    detector's name, and a table that discloses a field for some values and not
    others is a table whose omissions are informative. ``evidence_hash`` pins a
    bundle that lives under a retention clock; ``trust_score`` is the §11.3
    surface again.
    """
    return {}


def _review_decided(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The outcome. §11.4's three actions, and nothing around them.

    ``rationale`` is an operator writing for other operators, ``decided_by``
    and ``decided_by_label`` name a shared control-plane token rather than a
    person until Phase 13, and ``reason`` carries ``_review_queued``'s problem.
    The decision itself belongs to the report, and withholding it would make the
    queue exactly the dead end §11.4 forbids.
    """
    return {"decision": payload.get("decision")}


#: Event type → the shape its payload takes in a complaint's own history.
#:
#: **Absence is not permission.** A type on the complaint chain with no entry
#: here publishes ``{}`` — the row is still disclosed, its payload is not. That
#: is the same default-deny mechanic ADR-0016 applies to the broadcast, and it
#: fails safe on the same change: a new field on an existing type is invisible
#: until somebody adds it above, with a test next to it.
_HISTORY_SHAPERS: Final[dict[str, HistoryShaper]] = {
    "complaint_submitted": _complaint_submitted,
    "media_transcribed": _media_transcribed,
    "exif_check_completed": _exif_check_completed,
    "media_redacted": _media_redacted,
    "safety_trigger_fired": _safety_trigger_fired,
    "classification_scored": _classification_scored,
    "severity_scored": _severity_scored,
    "complaint_clustered": _complaint_clustered,
    "pipeline_stage_degraded": _pipeline_stage_degraded,
    "perceptual_duplicate_detected": _perceptual_duplicate_detected,
    "abuse_pattern_flagged": _abuse_pattern_flagged,
    "review_queued": _review_queued,
    "review_decided": _review_decided,
}

#: Fields that must never appear in a shaped history payload, whatever a future
#: shaper does. A belt-and-braces assertion target rather than a runtime filter:
#: filtering here would let a careless shaper "work", and the point is that it
#: should fail loudly in the test suite instead.
FORBIDDEN_FIELDS: Final = frozenset(
    {
        "device_fingerprint",
        "description_text",
        "transcript",
        "photo_url",
        "audio_url",
        "matched_complaint_id",
        "matched_media_sha256",
        "source_sha256",
        "redacted_sha256",
        "evidence_hash",
        "matched_terms",
        "trust_delta",
        "trust_score",
        "rationale",
        "decided_by",
        "decided_by_label",
        "latitude",
        "longitude",
        "evidence",
    }
)


def history_payload(event_type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """The citizen-visible subset of a stored payload, or ``{}`` if none is declared."""
    shaper = _HISTORY_SHAPERS.get(event_type)
    if shaper is None:
        return {}
    return shaper(payload)


def disclosed_event_types() -> frozenset[str]:
    """Event types with a declared history shape — for the disclosure test."""
    return frozenset(_HISTORY_SHAPERS)


def _as_str(value: Any) -> str | None:
    return None if value is None else str(value)
