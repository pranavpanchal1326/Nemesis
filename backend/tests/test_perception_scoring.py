"""The zero-shot decision rule, asserted exactly.

``scoring`` holds no model and touches no database, which is what makes every
claim in its docstring checkable rather than plausible. These tests are that
check: each one names a sentence from that module and either confirms it or
would fail if somebody changed the arithmetic under it.

Two of them exist because the validation harness found the defect first, and
they are marked as such. A test written after a bug is worth more than one
written before it, and worth much more if it says which bug.
"""

from __future__ import annotations

import math

import pytest

from nemesis.perception.encoders import cosine, l2_normalise
from nemesis.perception.errors import ModelLoadError
from nemesis.perception.scoring import (
    Calibration,
    CategoryVectors,
    ScoreResult,
    combine,
    decide,
    score_against,
)
from tests.perception_fixtures import axis, tilted, unit

NEUTRAL = Calibration(temperature=0.05, bias=0.0, abstain_below=0.0, min_margin=0.0)


def _categories(*specs: tuple[str, int]) -> tuple[CategoryVectors, ...]:
    return tuple(CategoryVectors(category=name, positives=(axis(index),)) for name, index in specs)


# ---------------------------------------------------------------------------
# Pooling
# ---------------------------------------------------------------------------


def test_pooling_is_max_so_a_better_described_category_is_not_punished() -> None:
    """ "Pooling is max, not mean" — the module docstring's first decision.

    ``rich`` has four prompts of which one matches perfectly; ``thin`` has one
    prompt that matches slightly less well. Under a mean, ``rich`` loses because
    its three irrelevant prompts drag it down — which would mean writing more
    ways to describe a category made it *harder* to classify into.
    """
    query = axis(0)
    rich = CategoryVectors(
        category="rich",
        positives=(axis(0), axis(3), axis(4), axis(5)),
    )
    thin = CategoryVectors(category="thin", positives=(tilted(0, 1, 0.15),))

    result = score_against(query, (rich, thin), calibration={}, default=NEUTRAL)

    assert result.category == "rich"
    assert result.raw_similarities["rich"] == pytest.approx(1.0)
    # And the mean would have gone the other way, which is the point.
    mean_rich = sum(cosine(query, vector) for vector in rich.positives) / 4
    assert mean_rich < result.raw_similarities["thin"]


# ---------------------------------------------------------------------------
# Temperature and bias
# ---------------------------------------------------------------------------


def test_bias_is_applied_in_similarity_space_before_the_temperature() -> None:
    """``logit = (cosine + bias) / temperature``, not ``cosine / temperature + bias``.

    Asserted through observable output rather than by reading the source: with a
    bias of ``-cos`` the category's logit is exactly zero, so against a second
    category with the same treatment the two are tied and the winner is decided
    by the documented alphabetical tiebreak. Under the other ordering the two
    logits would differ by a factor of the temperature ratio and ``b`` would win
    outright.
    """
    query = axis(0)
    alpha = CategoryVectors(category="alpha", positives=(tilted(0, 1, 0.25),))
    beta = CategoryVectors(category="beta", positives=(tilted(0, 1, 0.25),))
    similarity = cosine(query, alpha.positives[0])

    result = score_against(
        query,
        (alpha, beta),
        calibration={
            "alpha": Calibration(temperature=0.01, bias=-similarity),
            "beta": Calibration(temperature=0.5, bias=-similarity),
        },
        default=NEUTRAL,
    )

    assert result.confidence == pytest.approx(0.5)
    assert result.margin == pytest.approx(0.0, abs=1e-9)


def test_a_negative_pool_shares_its_category_temperature_and_bias() -> None:
    """The contrast entry gets the same affine transform as the positive one.

    The regression test for the defect the harness found on its first real run:
    an uncentred contrast logit at a fitted temperature of 0.006 sits ~140 above
    the centred positives, takes the entire softmax, and the layer abstains on
    everything it can classify correctly. Here the negative matches *less* well
    than the positive, so a correctly-centred contrast leaves the category
    winning comfortably; an uncentred one would crush it.
    """
    query = axis(0)
    entry = CategoryVectors(
        category="target",
        positives=(axis(0),),
        negatives=(tilted(0, 1, 0.5),),
    )
    other = CategoryVectors(category="other", positives=(axis(2),))

    result = score_against(
        query,
        (entry, other),
        calibration={"target": Calibration(temperature=0.006, bias=-0.9)},
        default=Calibration(temperature=0.006, bias=-0.9),
    )

    assert result.category == "target"
    assert result.confidence > 0.5


def test_a_temperature_at_zero_produces_a_number_rather_than_a_nan() -> None:
    """``MIN_TEMPERATURE`` guards the divisor, and that is *all* it guards.

    Without the clamp, a temperature of zero divides a cosine into an infinity
    and the softmax denominator becomes ``inf``, which yields NaN — a value that
    propagates silently into a confidence field the event log keeps forever and
    fails a Pydantic bound three layers later.

    **The clamp does not prevent over-confidence, and this test says so rather
    than pretending otherwise.** At the clamped floor the winner still rounds to
    1.000. That is the honest scope of the guard: it turns an unusable value into
    a usable one, and the thing that stops a temperature this small from reaching
    production is the ``gt=0.005`` bound on the policy document, which is a
    different mechanism in a different file.
    """
    result = score_against(
        axis(0),
        _categories(("a", 0), ("b", 1)),
        calibration={"a": Calibration(temperature=0.0)},
        default=NEUTRAL,
    )
    assert math.isfinite(result.confidence)
    assert math.isfinite(result.margin)
    assert 0.0 <= result.confidence <= 1.0
    assert result.category == "a"


# ---------------------------------------------------------------------------
# Abstention
# ---------------------------------------------------------------------------


def test_below_the_floor_no_category_is_claimed_and_the_reason_names_it() -> None:
    result = score_against(
        axis(0),
        _categories(("a", 0), ("b", 1)),
        calibration={},
        default=Calibration(temperature=0.5, abstain_below=0.99),
    )
    assert result.category is None
    assert result.abstained
    assert result.abstain_reason is not None and "floor" in result.abstain_reason


def test_an_abstention_still_reports_the_category_it_declined_to_claim() -> None:
    """``top_category`` is populated whether or not the category was claimed.

    The regression test for the harness's other first-run defect. ``alternatives``
    is everything *except* the winner, so a caller reading the top of it on an
    abstained result gets the runner-up while believing it has the winner — which
    turned a 70%-correct ranking into a forced-choice accuracy indistinguishable
    from chance, in a number nobody would have known to disbelieve.
    """
    result = score_against(
        axis(0),
        _categories(("winner", 0), ("loser", 1)),
        calibration={},
        default=Calibration(temperature=0.5, abstain_below=0.99),
    )
    assert result.category is None
    assert result.top_category == "winner"
    assert "winner" not in result.alternatives


def test_two_categories_within_the_margin_are_a_question_for_a_human() -> None:
    query = tilted(0, 1, 0.5)  # exactly between the two
    result = score_against(
        query,
        _categories(("a", 0), ("b", 1)),
        calibration={},
        default=Calibration(temperature=0.5, abstain_below=0.0, min_margin=0.2),
    )
    assert result.abstained
    assert result.abstain_reason is not None and "margin" in result.abstain_reason


def test_scoring_zero_categories_raises_rather_than_abstaining() -> None:
    """An empty prompt bundle is a control-plane gap, not a model abstention.

    Returning "no category" for it would be indistinguishable from a confident
    refusal, and the two need completely different people to fix them.
    """
    with pytest.raises(ValueError, match="zero categories"):
        score_against(axis(0), (), calibration={}, default=NEUTRAL)


# ---------------------------------------------------------------------------
# Fusion
# ---------------------------------------------------------------------------


def test_fusion_is_over_distributions_so_text_can_resolve_an_ambiguous_image() -> None:
    """The case ``combine`` exists for, and the one "take the more confident" fails.

    The image is split evenly between two categories; the description names one
    of them. Neither modality alone decides, and the fused result must.
    """
    image = ScoreResult(
        category="a",
        confidence=0.5,
        margin=0.0,
        alternatives={"b": 0.5},
        raw_similarities={},
        abstained=False,
        top_category="a",
    )
    text = ScoreResult(
        category="b",
        confidence=0.9,
        margin=0.8,
        alternatives={"a": 0.1},
        raw_similarities={},
        abstained=False,
        top_category="b",
    )

    fused = combine(image, text, image_weight=0.45)

    assert fused.category == "b"
    assert fused.confidence == pytest.approx(0.45 * 0.5 + 0.55 * 0.9)


def test_a_single_modality_fuses_to_exactly_itself() -> None:
    """A photo-only or text-only report degrades gracefully, not to half strength."""
    only = ScoreResult(
        category="a",
        confidence=0.8,
        margin=0.6,
        alternatives={"b": 0.2},
        raw_similarities={},
        abstained=False,
        top_category="a",
    )
    assert combine(None, only, image_weight=0.45) is only
    assert combine(only, None, image_weight=0.45) is only


def test_combine_with_neither_modality_is_a_caller_error() -> None:
    with pytest.raises(ValueError, match="at least one modality"):
        combine(None, None, image_weight=0.45)


# ---------------------------------------------------------------------------
# The shared decision rule
# ---------------------------------------------------------------------------


def test_decide_takes_the_abstain_decision_on_the_fused_result() -> None:
    """Not per modality — that would abstain on the case fusion exists for.

    Each modality alone sits under the floor; fused they clear it, and a rule
    applied before fusion would park a report both halves agreed about.
    """
    weak_image = ScoreResult(
        category="a",
        confidence=0.4,
        margin=0.1,
        alternatives={"b": 0.3},
        raw_similarities={},
        abstained=False,
        top_category="a",
    )
    weak_text = ScoreResult(
        category="a",
        confidence=0.45,
        margin=0.1,
        alternatives={"b": 0.35},
        raw_similarities={},
        abstained=False,
        top_category="a",
    )
    calibration = {"a": Calibration(temperature=0.05, abstain_below=0.42, min_margin=0.0)}

    decision = decide(
        weak_image, weak_text, calibration=calibration, default=NEUTRAL, image_weight=0.45
    )

    assert not decision.abstained
    assert decision.category == "a"


def test_decide_carries_the_reason_rather_than_raising() -> None:
    """The harness counts abstentions; the stage raises. One rule, two callers."""
    result = ScoreResult(
        category="a",
        confidence=0.2,
        margin=0.1,
        alternatives={"b": 0.1},
        raw_similarities={},
        abstained=False,
        top_category="a",
    )
    decision = decide(
        None,
        result,
        calibration={"a": Calibration(temperature=0.05, abstain_below=0.9)},
        default=NEUTRAL,
        image_weight=0.45,
    )
    assert decision.abstained
    assert decision.category is None
    assert decision.fused.top_category == "a"


# ---------------------------------------------------------------------------
# Vector hygiene
# ---------------------------------------------------------------------------


def test_a_zero_vector_is_refused_rather_than_clamped() -> None:
    """A zero vector has no direction; scoring it reads as confident emptiness."""
    with pytest.raises(ModelLoadError, match="no direction"):
        l2_normalise([0.0, 0.0, 0.0])


def test_comparing_different_widths_is_refused() -> None:
    """The most expensive silent bug available: 512 against 384 truncates."""
    with pytest.raises(ValueError, match="different encoders"):
        cosine(unit(1.0, 0.0), (0.1,) * 3)
