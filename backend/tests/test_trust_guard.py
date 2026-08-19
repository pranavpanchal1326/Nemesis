"""The Phase 8 guards, checked against themselves.

Two classes of thing here, and they share one purpose: a check that cannot fail
is not evidence.

**The mirrored vocabularies.** ``db.models.trust`` restates four enums as tuples
so the migration's CHECK constraints do not depend on an application import.
Mirrors drift. These tests are what makes the drift a build failure rather than
a constraint that silently rejects a legitimate row.

**The stage graph.** §22.1 forces an ordering — the classifier reads the
redacted copy, so redaction has to precede it — and forces a fallback: a trust
stage that *skipped* on failure would let a complaint reach the review queue
with no redacted artefact and only an original to show. Both are one line in
``stages.py`` and neither has a symptom until it matters.

The redaction guard itself is not tested here; see the note below on where it is.
"""

from __future__ import annotations

from nemesis.db.models.policy import POLICY_KINDS
from nemesis.db.models.trust import (
    MEDIA_KINDS,
    REVIEW_DECISIONS,
    REVIEW_REASONS,
    REVIEW_STATUSES,
)
from nemesis.observability.metrics import PipelineStage
from nemesis.pipeline.stages import PIPELINE_SEQUENCE, SPECS
from nemesis.policy.documents import PolicyKind
from nemesis.trust.abuse import AbusePattern
from nemesis.trust.review import ReviewDecisionKind, ReviewReason

#: **Where the redaction guard is tested, and why not here.**
#:
#: ``scripts/check_media_redaction.py`` proves it can still fail *inside
#: itself*: before scanning the repository it runs each of its rules against a
#: synthetic module that violates it, and exits non-zero if any rule stays
#: silent. That is the property a pytest here would have asserted — and it could
#: not have, because the test suite runs inside the API container, which mounts
#: only ``./backend``. A test that shells out to ``scripts/`` would be skipped
#: in precisely the environment CI uses, which is the same as not having it.
#:
#: What is left in this file is everything that *is* reachable from the package:
#: the mirrored vocabularies the migration's CHECK constraints depend on, and
#: the shape of the pipeline graph.


# ---------------------------------------------------------------------------
# Mirrored vocabularies
# ---------------------------------------------------------------------------


def test_the_review_reason_mirror_matches_the_enum() -> None:
    """``REVIEW_REASONS`` becomes a CHECK constraint; ``ReviewReason`` is what
    the code writes. A member added to one and not the other is a constraint
    that rejects a legitimate flag at the moment it is raised."""
    assert set(REVIEW_REASONS) == {member.value for member in ReviewReason}


def test_the_review_decision_mirror_matches_the_enum() -> None:
    assert set(REVIEW_DECISIONS) == {member.value for member in ReviewDecisionKind}


def test_the_policy_kind_mirror_matches_the_enum() -> None:
    """Phase 6's mirror, re-checked because Phase 8 added a seventh kind — and
    adding one means altering five CHECK constraints, which is exactly the kind
    of change that gets half-done."""
    assert set(POLICY_KINDS) == {member.value for member in PolicyKind}


def test_the_abuse_patterns_are_review_reasons() -> None:
    """Every detector's pattern must be raisable as a queue reason.

    §11.4 says no flag is a dead end. A detector whose pattern had no
    corresponding reason would fire, emit an event, and have nowhere to send it.
    """
    assert {member.value for member in AbusePattern} <= {member.value for member in ReviewReason}


def test_the_media_kinds_are_the_ones_the_stage_writes() -> None:
    assert set(MEDIA_KINDS) == {"image", "audio"}


def test_the_review_statuses_are_the_two_the_service_writes() -> None:
    """Two, not four. "In progress" is a claim about a person, and Phase 13 owns
    the person."""
    assert set(REVIEW_STATUSES) == {"open", "decided"}


# ---------------------------------------------------------------------------
# The stage graph
# ---------------------------------------------------------------------------


def test_the_trust_stage_sits_between_safety_and_classification() -> None:
    """The ordering the §22.1 obligation forces.

    The classifier reads the *redacted* copy, so redaction has to precede it. If
    the trust stage moved after classification, the perception layer would be
    consuming an image §22.1 says must not exist outside quarantine.
    """
    order = list(PIPELINE_SEQUENCE)
    assert order.index(PipelineStage.SAFETY_CHECK) < order.index(PipelineStage.TRUST_VERIFICATION)
    assert order.index(PipelineStage.TRUST_VERIFICATION) < order.index(PipelineStage.CLASSIFICATION)


def test_the_trust_stage_halts_rather_than_skipping_when_it_degrades() -> None:
    """The load-bearing declaration of the phase.

    A ``SKIPPED_STAGE`` fallback would let a complaint reach classification and
    the review queue with no redacted artefact — at which point the only image
    that exists is the unredacted original, and the pressure to "just show that
    one" is a design decision made under incident conditions.
    """
    from nemesis.domain.lifecycle import DegradationFallback

    spec = SPECS[PipelineStage.TRUST_VERIFICATION.value]
    assert spec.fallback is DegradationFallback.HALTED_FOR_REVIEW
    assert spec.continue_on_degrade is False


def test_every_stage_in_the_graph_is_reachable_from_the_first() -> None:
    """A stage nobody enqueues is a stage that never runs, and the only symptom
    is work that quietly stops happening."""
    seen = [PIPELINE_SEQUENCE[0]]
    while True:
        following = SPECS[seen[-1].value].next_stage
        if following is None:
            break
        seen.append(following)
    assert seen == list(PIPELINE_SEQUENCE)
