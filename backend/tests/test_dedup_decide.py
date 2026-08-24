"""The merge rule, attacked directly.

§14.3's claim — *zero false-positive merges* — is a claim about a decision
function, and this file is where it is tested as one. Every case here runs
without a database, which is the entire reason ``decide`` was separated from the
queries: a property test can generate ten thousand candidate sets and assert
that none of them produced a merge the band did not license, where an
integration test could only assert it about the handful of situations somebody
thought to seed.
"""

from __future__ import annotations

import uuid

import pytest
from hypothesis import given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st

from nemesis.dedup.decide import DedupOutcome, ScoredCandidate, decide
from nemesis.dedup.errors import DedupIntegrityError
from nemesis.policy.documents import DedupBand

MERGE_AT = 0.85
INVESTIGATE_AT = 0.65
MARGIN = 0.02


def band(**overrides: object) -> DedupBand:
    defaults: dict[str, object] = {
        "category": None,
        "geo_radius_meters": 50.0,
        "time_window_hours": 72,
        "merge_threshold": MERGE_AT,
        "investigate_threshold": INVESTIGATE_AT,
        "image_weight": 0.6,
        "text_weight": 0.4,
    }
    return DedupBand(**{**defaults, **overrides})  # type: ignore[arg-type]


def candidate(
    *,
    image: float | None = None,
    text: float | None = None,
    distance: float = 10.0,
    report_count: int = 1,
) -> ScoredCandidate:
    return ScoredCandidate(
        cluster_id=uuid.uuid4(),
        geo_distance_meters=distance,
        image_similarity=image,
        text_similarity=text,
        report_count=report_count,
    )


# ---------------------------------------------------------------------------
# The three bands
# ---------------------------------------------------------------------------


def test_no_candidates_is_distinct_and_attaches_to_nothing() -> None:
    decision = decide((), band=band(), ambiguous_margin=MARGIN)

    assert decision.outcome is DedupOutcome.DISTINCT
    assert decision.cluster_id is None
    assert decision.combined_confidence == 0.0
    assert decision.considered == 0
    # Not blind: there was nothing nearby to be blind about. Conflating "no
    # neighbours" with "neighbours I could not compare" is the specific
    # confusion the flag exists to prevent.
    assert decision.blind is False


def test_a_confident_single_candidate_merges() -> None:
    decision = decide((candidate(image=0.95, text=0.95),), band=band(), ambiguous_margin=MARGIN)

    assert decision.outcome is DedupOutcome.MERGE
    assert decision.cluster_id is not None


def test_the_middle_band_investigates_rather_than_merging() -> None:
    decision = decide((candidate(image=0.70, text=0.70),), band=band(), ambiguous_margin=MARGIN)

    assert decision.outcome is DedupOutcome.INVESTIGATE
    # Explicitly not attached. An investigated report is not a member of the
    # cluster it was nearly merged into, and a cluster_id here would be read as
    # membership by every downstream consumer.
    assert decision.cluster_id is None


def test_a_weak_candidate_is_distinct() -> None:
    decision = decide((candidate(image=0.20, text=0.20),), band=band(), ambiguous_margin=MARGIN)

    assert decision.outcome is DedupOutcome.DISTINCT
    assert decision.cluster_id is None


@pytest.mark.parametrize(
    ("confidence", "expected"),
    [
        (MERGE_AT, DedupOutcome.MERGE),
        (MERGE_AT - 1e-9, DedupOutcome.INVESTIGATE),
        (INVESTIGATE_AT, DedupOutcome.INVESTIGATE),
        (INVESTIGATE_AT - 1e-9, DedupOutcome.DISTINCT),
    ],
)
def test_band_edges_are_inclusive_at_the_lower_bound(
    confidence: float, expected: DedupOutcome
) -> None:
    """A confidence exactly on a threshold takes the *more* conservative action.

    Asserted at the boundary rather than in the middle of each band because an
    off-by-one in an inequality is invisible everywhere except here, and the
    direction it is wrong in is the direction §14.3 cares about.
    """
    decision = decide(
        (candidate(image=confidence, text=confidence),), band=band(), ambiguous_margin=MARGIN
    )

    assert decision.outcome is expected


# ---------------------------------------------------------------------------
# The tie rule
# ---------------------------------------------------------------------------


def test_two_strong_candidates_too_close_to_separate_do_not_merge() -> None:
    """The case the zero-false-merge gate exists for.

    Both candidates clear the merge threshold. Picking the higher one is a
    coin-flip: either they are duplicates of each other, or at least one match
    is wrong. Either way a merge here has a real chance of suppressing a
    citizen's report, so the engine refuses and asks.
    """
    decision = decide(
        (candidate(image=0.95, text=0.95), candidate(image=0.94, text=0.94)),
        band=band(),
        ambiguous_margin=MARGIN,
    )

    assert decision.outcome is DedupOutcome.INVESTIGATE
    assert decision.cluster_id is None
    assert len(decision.ambiguous_between) == 2


def test_two_strong_candidates_far_enough_apart_still_merge() -> None:
    """The tie rule must not swallow every multi-candidate case.

    A rule that downgraded whenever a second candidate existed would make dedup
    useless in any dense neighbourhood, which is where it matters most.
    """
    decision = decide(
        (candidate(image=0.99, text=0.99), candidate(image=0.70, text=0.70)),
        band=band(),
        ambiguous_margin=MARGIN,
    )

    assert decision.outcome is DedupOutcome.MERGE
    assert decision.ambiguous_between == ()


def test_distance_breaks_a_confidence_tie_toward_the_nearer_cluster() -> None:
    near = candidate(image=0.5, text=0.5, distance=5.0)
    far = candidate(image=0.5, text=0.5, distance=40.0)

    decision = decide((far, near), band=band(), ambiguous_margin=MARGIN)

    assert decision.geo_distance_meters == 5.0


# ---------------------------------------------------------------------------
# Missing modalities
# ---------------------------------------------------------------------------


def test_a_text_only_report_is_not_penalised_for_having_no_photograph() -> None:
    """Averaging a missing modality against zero switches dedup off silently.

    With weights 0.6/0.4 a text-only report scoring 1.0 would combine to 0.4 and
    could never clear any sane merge threshold — which reads in a dashboard as
    "dedup is being careful" and is actually "dedup does not work for anybody who
    submits without a camera".
    """
    decision = decide((candidate(text=0.95),), band=band(), ambiguous_margin=MARGIN)

    assert decision.outcome is DedupOutcome.MERGE
    assert decision.combined_confidence == pytest.approx(0.95)


def test_no_comparable_modality_at_all_is_distinct_and_flagged_blind() -> None:
    decision = decide((candidate(),), band=band(), ambiguous_margin=MARGIN)

    assert decision.outcome is DedupOutcome.DISTINCT
    assert decision.blind is True


# ---------------------------------------------------------------------------
# Integrity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [1.5, -1.5])
def test_a_similarity_outside_cosine_range_is_refused_not_compared(value: float) -> None:
    """A number that is not a similarity must never reach a threshold.

    It means the column is not holding unit vectors or the query compared
    mismatched embeddings. Both produce a plausible-looking float, and a merge
    decided on one is unexplainable after the fact.
    """
    with pytest.raises(DedupIntegrityError):
        candidate(image=value)


def test_a_negative_distance_is_refused() -> None:
    with pytest.raises(DedupIntegrityError):
        candidate(image=0.9, distance=-1.0)


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

similarity = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
candidates = st.lists(
    st.builds(
        lambda image, text, distance: ScoredCandidate(
            cluster_id=uuid.uuid4(),
            geo_distance_meters=distance,
            image_similarity=image,
            text_similarity=text,
            report_count=1,
        ),
        image=st.one_of(st.none(), similarity),
        text=st.one_of(st.none(), similarity),
        distance=st.floats(min_value=0.0, max_value=5000.0, allow_nan=False),
    ),
    min_size=0,
    max_size=8,
)


@given(found=candidates)
@hypothesis_settings(max_examples=400)
def test_a_merge_never_happens_below_the_merge_threshold(found: list[ScoredCandidate]) -> None:
    """The gate, as a property. No generated input may produce an unlicensed merge."""
    decision = decide(found, band=band(), ambiguous_margin=MARGIN)

    if decision.outcome is DedupOutcome.MERGE:
        assert decision.combined_confidence >= MERGE_AT


@given(found=candidates)
@hypothesis_settings(max_examples=400)
def test_a_merge_never_happens_when_the_runner_up_is_within_the_margin(
    found: list[ScoredCandidate],
) -> None:
    decision = decide(found, band=band(), ambiguous_margin=MARGIN)

    if decision.outcome is DedupOutcome.MERGE and decision.runner_up_confidence is not None:
        assert decision.combined_confidence - decision.runner_up_confidence > MARGIN


@given(found=candidates)
@hypothesis_settings(max_examples=400)
def test_cluster_id_is_set_exactly_on_the_merge_path(found: list[ScoredCandidate]) -> None:
    """Membership and near-membership must never be confusable downstream."""
    decision = decide(found, band=band(), ambiguous_margin=MARGIN)

    assert (decision.cluster_id is not None) == (decision.outcome is DedupOutcome.MERGE)


@given(found=candidates)
@hypothesis_settings(max_examples=400)
def test_the_reported_confidence_is_the_best_available(found: list[ScoredCandidate]) -> None:
    """No candidate may score higher than the one the decision reports.

    Catches a sort that silently reverses — which would make the engine pick the
    *worst* match, merge almost nothing, and look like a conservative threshold
    rather than a bug.
    """
    decision = decide(found, band=band(), ambiguous_margin=MARGIN)

    if not found:
        return
    everything = [
        decide((one,), band=band(), ambiguous_margin=MARGIN).combined_confidence for one in found
    ]
    assert decision.combined_confidence == pytest.approx(max(everything))
