"""What a complaint's own history may say — ADR-0043, ADR-0045.

The tests here are deliberately pure-function assertions against
``nemesis.events.disclosure``, for the reason ``test_realtime.py`` gives about
its own half: a privacy property asserted through HTTP is a property whose test
needs a database, and a test that needs a database is a test nobody extends on
the next event type.

The one that matters most is
``test_no_shaper_publishes_a_field_the_table_forbids``. It runs every shaper
against a payload carrying **every** field the catalog stores for that type, so
a shaper that grows a field is caught by the forbidden list rather than by a
reviewer noticing.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

import nemesis.events.catalog  # noqa: F401  (registers the catalog)
from nemesis.events.disclosure import (
    FORBIDDEN_FIELDS,
    disclosed_event_types,
    history_payload,
)
from nemesis.events.registry import registered_events
from nemesis.realtime.envelope import shaped_event_types

#: One realistic stored payload per complaint-chain event type, carrying every
#: field the catalog declares. Written out rather than generated from the models
#: on purpose: a generator would produce whatever the model happens to say
#: today, and the point of this fixture is to be an independent statement of
#: what is actually on disk.
STORED: dict[str, dict[str, Any]] = {
    "complaint_submitted": {
        "latitude": 18.520431,
        "longitude": 73.856743,
        "description_text": "open drain outside 42 Elm Street, my daughter walks past it",
        "photo_url": "nemesis+quarantine://ab/abc.jpg",
        "audio_url": "nemesis+quarantine://cd/cde.ogg",
        "locale": "mr-IN",
        "device_fingerprint": "5f3a9c2e1b",
        "submitted_via": "web",
    },
    "media_transcribed": {
        "transcript": "इथे मोठा खड्डा आहे",
        "language": "mr",
        "language_confidence": 0.94,
        "audio_seconds": 7.5,
        "model_id": "whisper-small@1",
        "language_uncertain": False,
    },
    "exif_check_completed": {
        "exif_present": True,
        "distance_meters": 4.2,
        "trust_delta": 0.15,
        "reason": "the photograph's own GPS is 4 m from the reported location",
    },
    "media_redacted": {
        "source_sha256": "a" * 64,
        "redacted_sha256": "b" * 64,
        "media_kind": "image",
        "content_type": "image/jpeg",
        "faces_detected": 3,
        "faces_blurred": 3,
        "detector_id": "retinaface@1.2.0",
        "exif_stripped": True,
    },
    "safety_trigger_fired": {
        "rule_id": "live-wire",
        "ruleset_version": "v3",
        "matched_terms": ["sparking cable outside 42 Elm Street"],
        "detection_source": "keyword",
    },
    "classification_scored": {
        "category": "roads.pothole",
        "confidence": 0.88,
        "model_id": "clip-vit-b32@1",
        "prompt_set_version": "v4",
        "alternatives": {"roads.crack": 0.31},
        "transcript": "इथे मोठा खड्डा आहे",
        "detected_language": "mr",
        "margin": 0.57,
        "raw_similarities": {"roads.pothole": 0.28},
        "calibration_version": "cal-9",
        "model_ids": {"image": "clip-vit-b32@1"},
        "language_confidence": 0.94,
    },
    "severity_scored": {
        "score": 71.5,
        "components": {"hazard": 0.8},
        "weights": {"hazard": 0.4},
        "policy_version": "severity-rubric@12",
    },
    "complaint_clustered": {
        "cluster_id": str(uuid.uuid4()),
        "outcome": "merge",
        "combined_confidence": 0.91,
        "policy_version": "dedup@7",
    },
    "pipeline_stage_degraded": {
        "stage": "classification",
        "failure_mode": "ollama_unreachable",
        "fallback_taken": "park_for_human_review",
        "attempts": 3,
        "correlation_id": "corr-1",
    },
    "perceptual_duplicate_detected": {
        "matched_complaint_id": str(uuid.uuid4()),
        "matched_media_sha256": "c" * 64,
        "hamming_distance": 3,
        "threshold": 8,
        "age_hours": 19.5,
        "trust_delta": -0.3,
        "policy_version": "phash@2",
    },
    "abuse_pattern_flagged": {
        "pattern": "device_velocity",
        "observation_count": 11,
        "window_hours": 1.0,
        "trust_delta": -0.4,
        "policy_version": "abuse@5",
        "evidence": {"device_fingerprint": "5f3a9c2e1b"},
    },
    "review_queued": {
        "review_item_id": str(uuid.uuid4()),
        "reason": "device_velocity",
        "priority": 40,
        "occurrences": 2,
        "trust_score": 0.31,
        "evidence_hash": "d" * 64,
    },
    "review_decided": {
        "review_item_id": str(uuid.uuid4()),
        "reason": "device_velocity",
        "decision": "approve",
        "rationale": "the reporter is a ward volunteer logging on behalf of neighbours",
        "decided_by": str(uuid.uuid4()),
        "decided_by_label": "control-plane token",
        "evidence_hash": "d" * 64,
    },
}


def _complaint_chain_types() -> set[str]:
    return {event.event_type for event in registered_events() if event.entity_type == "complaint"}


# ---------------------------------------------------------------------------
# The line, asserted rather than described
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("event_type", sorted(STORED))
def test_no_shaper_publishes_a_field_the_table_forbids(event_type: str) -> None:
    """The whole file in one assertion, and the reason ``FORBIDDEN_FIELDS`` is
    a constant rather than a filter.

    Filtering at run time would let a careless shaper *work*: the field would be
    declared, quietly removed, and the mistake would live in the source until
    somebody widened the filter. Asserting instead means the shaper fails here,
    loudly, in the pull request that added it.
    """
    shaped = history_payload(event_type, STORED[event_type])
    leaked = FORBIDDEN_FIELDS & set(shaped)
    assert not leaked, f"{event_type} disclosed {sorted(leaked)}"


@pytest.mark.parametrize("event_type", sorted(STORED))
def test_no_shaper_republishes_a_forbidden_value_under_a_different_key(
    event_type: str,
) -> None:
    """Renaming is not withholding.

    ``FORBIDDEN_FIELDS`` catches a shaper that copies ``description_text``
    through. It does not catch one that copies it through as ``summary``. This
    does, by looking for the values rather than the keys.
    """
    stored = STORED[event_type]
    serialised = json.dumps(history_payload(event_type, stored), ensure_ascii=False)
    for key in FORBIDDEN_FIELDS & set(stored):
        value = stored[key]
        if isinstance(value, bool) or value is None:
            # A boolean or a null carries nothing identifying and collides with
            # every other boolean in the payload; searching for it would fail on
            # coincidence rather than on disclosure.
            continue
        assert json.dumps(value, ensure_ascii=False).strip('"') not in serialised, (
            f"{event_type} republished the value of {key}"
        )


def test_the_submission_itself_discloses_nothing() -> None:
    """The hardest case, and the one a reader would most expect to be disclosed.

    A citizen holding their own complaint id already knows what they submitted.
    The id is a capability, not proof of identity, so echoing the report back to
    whoever holds it buys nothing and risks the exact GPS, the free text, the
    media handles and the §11.3 fingerprint in one response.
    """
    assert history_payload("complaint_submitted", STORED["complaint_submitted"]) == {}


def test_the_transcript_never_leaves_but_the_language_does() -> None:
    """§8.4's promise is *"report by speaking, in your language"*.

    Confirming the promise was kept needs the language and the duration. It does
    not need the sentence.
    """
    shaped = history_payload("media_transcribed", STORED["media_transcribed"])
    assert shaped == {"language": "mr", "language_uncertain": False, "audio_seconds": 7.5}


def test_the_exif_distance_is_disclosed_here_and_not_on_the_broadcast() -> None:
    """The asymmetry that justifies there being two tables at all.

    The same field, two audiences, two answers: the metres between your
    photograph and your report is a fact about you, stated to someone holding
    the handle to it — and a second constraint on a coarsened pin, when stated
    to the whole city.
    """
    from nemesis.realtime.envelope import public_payload

    stored = STORED["exif_check_completed"]
    assert history_payload("exif_check_completed", stored)["distance_meters"] == 4.2
    assert "distance_meters" not in public_payload("exif_check_completed", stored)


def test_the_trust_delta_is_withheld_from_both_audiences() -> None:
    """And not for a privacy reason, which is why it is asserted separately.

    The trust score is the §11.3 control surface. Publishing what each behaviour
    costs publishes the gradient an abuser descends — so this one is withheld
    from the citizen too, on a read that discloses the finding it produced.
    """
    from nemesis.realtime.envelope import public_payload

    for event_type in ("exif_check_completed", "perceptual_duplicate_detected"):
        stored = STORED[event_type]
        assert "trust_delta" not in history_payload(event_type, stored)
        assert "trust_delta" not in public_payload(event_type, stored)


def test_a_flag_is_disclosed_as_a_row_and_withheld_as_a_detector() -> None:
    """ADR-0043's most-argued line.

    ADR-0033 is that abuse detection **flags and cannot block**, so a reader
    learning that a check fired learns that a human will look — which the
    ``review_queued`` row on the next line was going to tell them anyway. What
    stays back is the gradient: the detector's name, its window, its count.
    """
    assert history_payload("abuse_pattern_flagged", STORED["abuse_pattern_flagged"]) == {}
    assert "abuse_pattern_flagged" in disclosed_event_types()


def test_a_perceptual_duplicate_never_names_the_report_it_matched() -> None:
    """§26.4's hard rule: no citizen identifier on a published surface.

    The id of the report you were matched against is another citizen's report,
    and the distance and threshold together publish how far a re-upload must be
    perturbed to clear the check.
    """
    assert (
        history_payload("perceptual_duplicate_detected", STORED["perceptual_duplicate_detected"])
        == {}
    )


def test_a_review_decision_is_the_outcome_and_not_the_argument() -> None:
    """§11.4: *"no flag is ever a dead end."*

    Withholding the decision would make the queue exactly the dead end §11.4
    forbids. Disclosing the rationale would publish an operator writing for
    other operators, and ``decided_by_label`` names a shared control-plane token
    rather than a person until Phase 13.
    """
    shaped = history_payload("review_decided", STORED["review_decided"])
    assert shaped == {"decision": "approve"}


def test_the_severity_row_carries_the_policy_version_the_broadcast_omits() -> None:
    """§E17.4's *"why? →"* opens the rubric **and its version**.

    Without the version the link opens whatever the rubric happens to be today,
    which is a different document from the one that scored this report.
    """
    from nemesis.realtime.envelope import public_payload

    stored = STORED["severity_scored"]
    assert history_payload("severity_scored", stored)["policy_version"] == "severity-rubric@12"
    assert "policy_version" not in public_payload("severity_scored", stored)


# ---------------------------------------------------------------------------
# The table cannot fall behind the catalog
# ---------------------------------------------------------------------------


def test_every_disclosed_type_is_a_registered_event_type() -> None:
    """A shaper for an event nobody emits is a promise that cannot be kept."""
    registered = {event.event_type for event in registered_events()}
    unknown = disclosed_event_types() - registered
    assert not unknown, f"disclosed but never emitted: {sorted(unknown)}"


def test_every_complaint_chain_type_has_a_stated_position() -> None:
    """The forcing function. A new event on the complaint chain must be decided.

    Default-deny means an undeclared type publishes ``{}`` and nothing breaks —
    which is safe and is also how a decision gets skipped. This makes skipping
    it a test failure: adding a complaint event obliges the author to write a
    shaper, even if the shaper's body is ``return {}`` and its docstring is the
    argument for that.
    """
    undecided = _complaint_chain_types() - disclosed_event_types()
    assert not undecided, (
        f"on the complaint chain with no declared history shape: {sorted(undecided)}. "
        f"Add one to nemesis/events/disclosure.py — `return {{}}` is a valid answer, "
        f"an absent entry is not."
    )


def test_the_fixture_covers_every_complaint_chain_type() -> None:
    """Otherwise the leak assertions above quietly stop covering a type."""
    missing = _complaint_chain_types() - set(STORED)
    assert not missing, f"no stored-payload fixture for: {sorted(missing)}"


def test_the_two_tables_are_independent() -> None:
    """They agree on some types today and must be able to diverge tomorrow.

    Asserted because the tempting refactor — one table, one audience flag — is
    the coupling this file exists to prevent: widening what a citizen may see
    about their own report would silently widen what the whole city sees about
    theirs.
    """
    assert disclosed_event_types() != shaped_event_types()
    both = disclosed_event_types() & shaped_event_types()
    assert both, "the two tables share no type at all, which means one of them is wrong"
