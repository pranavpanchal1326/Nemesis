"""The validation harness — the arithmetic behind the published F1 number.

**Why the metrics get hand-computed fixtures.** Precision, recall and F1 are four
lines of arithmetic that everybody believes they can write correctly, and the
particular way this harness can be wrong is the expensive one: an abstention
treated as a false positive, or a category with no predictions reporting perfect
precision, both make the published number *better* than reality. So every
counting rule below is asserted against a confusion matrix small enough to check
by eye.

**Why the fitting tests assert on shape rather than on values.** A fitted
temperature is a measurement; asserting it equals 0.0087 would pin the test to
one corpus and one checkpoint. What is asserted is what has to be true for the
fit to be usable at all — inside the policy document's bounds, ordered sensibly
against the similarity statistics it was derived from, and never fabricated for a
category with no evidence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nemesis.perception import harness
from nemesis.perception.corpus import Corpus, Example, Provenance
from nemesis.perception.harness import (
    F1_FLOOR,
    MIN_FITTED_TEMPERATURE,
    Prediction,
    PromptSpec,
    embed_specs,
    evaluate,
)
from nemesis.perception.scoring import Calibration
from tests.perception_fixtures import DictTextEncoder, axis, tilted

NEUTRAL = Calibration(temperature=0.05, bias=0.0, abstain_below=0.0, min_margin=0.0)


def _prediction(
    truth: str,
    predicted: str | None,
    *,
    forced: str | None = None,
    locale: str = "en",
    identifier: str = "x",
) -> Prediction:
    return Prediction(
        example_id=identifier,
        truth=truth,
        locale=locale,
        provenance="authored",
        predicted=predicted,
        forced=forced if forced is not None else (predicted or truth),
        confidence=0.5,
        margin=0.1,
        abstained=predicted is None,
        modality="text",
        seconds=0.01,
    )


# ---------------------------------------------------------------------------
# The counting rules
# ---------------------------------------------------------------------------


def test_an_abstention_costs_recall_and_accuses_nobody() -> None:
    """The single most consequential counting decision in the module.

    Two ``a`` examples, one classified correctly and one abstained. The
    abstention must be a false negative for ``a`` — the report was not
    classified — and a false positive for *nothing*, because no category was
    claimed. Folding it into the runner-up's false positives would invent a
    prediction the system deliberately declined to make.
    """
    predictions = [
        _prediction("a", "a", identifier="1"),
        _prediction("a", None, forced="b", identifier="2"),
    ]

    metrics = {
        entry.category: entry
        for entry in (harness._metrics_for(key, predictions) for key in ("a", "b"))
    }

    assert metrics["a"].true_positives == 1
    assert metrics["a"].false_negatives == 1
    assert metrics["a"].abstentions == 1
    assert metrics["a"].precision == pytest.approx(1.0)
    assert metrics["a"].recall == pytest.approx(0.5)
    assert metrics["b"].false_positives == 0, "an abstention accused b of something"


def test_coverage_exposes_the_precision_an_abstain_floor_can_buy() -> None:
    """The counting rule above is gameable and this is what makes it safe.

    Raise every abstain floor and precision goes to 1.0 while the system
    classifies nothing. Coverage is the column that says so in one glance.
    """
    predictions = [_prediction("a", "a")] + [
        _prediction("a", None, identifier=str(index)) for index in range(9)
    ]
    metrics = harness._metrics_for("a", predictions)

    assert metrics.precision == pytest.approx(1.0)
    assert metrics.coverage == pytest.approx(0.1)


def test_forced_columns_judge_the_same_model_with_abstention_disabled() -> None:
    predictions = [
        _prediction("a", None, forced="a", identifier="1"),
        _prediction("a", None, forced="a", identifier="2"),
    ]
    metrics = harness._metrics_for("a", predictions)

    assert metrics.f1 == pytest.approx(0.0)
    assert metrics.forced_f1 == pytest.approx(1.0)


def test_a_category_that_was_never_predicted_reports_zero_not_one() -> None:
    """The most misleading cell this table could contain.

    Precision is undefined with no predictions, and the two conventions say
    opposite things. 1.0 would let a category nobody ever guessed report perfect
    precision; 0.0 is conservative, and ``support`` beside it makes the
    undefined case identifiable rather than merely pessimistic.
    """
    metrics = harness._metrics_for("ghost", [_prediction("a", "a")])

    assert metrics.support == 0
    assert metrics.precision == pytest.approx(0.0)
    assert metrics.f1 == pytest.approx(0.0)


def test_macro_and_micro_disagree_where_a_rare_category_fails() -> None:
    """Why both are reported.

    Nine easy ``common`` examples all correct and one ``rare`` example wrong:
    micro is dominated by the common case and looks excellent, macro halves.
    The gate's floor is applied per category precisely because a taxonomy has a
    long tail and micro cannot see it.
    """
    predictions = [_prediction("common", "common", identifier=str(index)) for index in range(9)] + [
        _prediction("rare", "common", identifier="r")
    ]
    per_category = tuple(harness._metrics_for(key, predictions) for key in ("common", "rare"))

    assert harness._micro_f1(per_category) >= 0.9
    macro = sum(entry.f1 for entry in per_category) / 2
    assert macro < 0.55


def test_the_confusion_list_is_the_work_list_and_excludes_abstentions() -> None:
    """ "X was called Y eleven times" is actionable; "X abstained" is not a confusion."""
    predictions = [
        _prediction("a", "b", identifier="1"),
        _prediction("a", "b", identifier="2"),
        _prediction("a", None, identifier="3"),
        _prediction("c", "b", identifier="4"),
    ]
    assert harness._confusions(predictions) == (("a", "b", 2), ("c", "b", 1))


def test_the_p95_is_nearest_rank_not_interpolated() -> None:
    """The budget is a threshold a real request either met or did not, so the
    reported value has to be a measurement rather than a weighted average of two."""
    samples = [float(value) for value in range(1, 21)]
    summary = harness._latency("classify_one", samples)

    assert summary.p95 in samples
    assert summary.max == pytest.approx(20.0)
    assert summary.count == 20


def test_per_locale_f1_is_computed_within_each_locale() -> None:
    """§8.4: a single number over a mixed-language corpus hides a language that
    does not work."""
    predictions = [
        _prediction("a", "a", locale="en", identifier="1"),
        _prediction("a", "b", locale="mr", identifier="2"),
    ]
    per_locale = harness._per_locale_f1(predictions)

    assert per_locale["en"] == pytest.approx(1.0)
    assert per_locale["mr"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# End to end, through the shipped decision rule
# ---------------------------------------------------------------------------


def _corpus(*examples: Example) -> Corpus:
    return Corpus(
        corpus_id="test-v1",
        template="municipality",
        description="d",
        authored="2026-08-23",
        root=Path("."),
        examples=examples,
    )


def _text_example(identifier: str, category: str, text: str) -> Example:
    return Example(
        id=identifier,
        category=category,
        locale="en",
        provenance=Provenance.AUTHORED,
        text=text,
    )


def test_a_perfectly_separable_corpus_scores_one() -> None:
    """The sanity floor: if the harness cannot report 1.0 on a corpus where each
    query *is* its category's prompt, no other number it produces means anything."""
    encoder = DictTextEncoder(
        {
            "prompt a": axis(0),
            "prompt b": axis(1),
            "text a": axis(0),
            "text b": axis(1),
        }
    )
    specs = (
        PromptSpec(category="a", prompts=("prompt a",)),
        PromptSpec(category="b", prompts=("prompt b",)),
    )
    categories = embed_specs(specs, encoder=encoder)
    examples = (
        _text_example("1", "a", "text a"),
        _text_example("2", "b", "text b"),
    )

    result = evaluate(
        examples,
        corpus=_corpus(*examples),
        split="holdout",
        text_categories=categories,
        text_encoder=encoder,
        calibration={},
        default=NEUTRAL,
        image_weight=0.45,
    )

    assert result.macro_f1 == pytest.approx(1.0)
    assert result.coverage == pytest.approx(1.0)
    assert result.modality == "text"
    assert all(entry.meets_floor for entry in result.per_category)


def test_the_harness_embeds_prompts_as_passages_and_queries_as_queries() -> None:
    """e5's asymmetry, which has no symptom when it is wrong.

    Getting it backwards costs retrieval quality silently — and for this system
    that means dedup Stage 2 quietly working less well for exactly the Hindi and
    Marathi reporters ADR-0003 chose the model to serve.
    """
    encoder = DictTextEncoder({"prompt a": axis(0), "text a": axis(0)})
    categories = embed_specs((PromptSpec(category="a", prompts=("prompt a",)),), encoder=encoder)
    examples = (_text_example("1", "a", "text a"),)

    evaluate(
        examples,
        corpus=_corpus(*examples),
        split="holdout",
        text_categories=categories,
        text_encoder=encoder,
        calibration={},
        default=NEUTRAL,
        image_weight=0.45,
    )

    assert encoder.prefixes[0] == "passage: "
    assert encoder.prefixes[1] == "query: "


def test_an_example_this_run_cannot_read_is_skipped_not_counted_as_a_miss() -> None:
    """An image example on a text-only run is a fact about the run, not the model.

    Counting it would depress every category that happens to carry more
    photographs, which is a per-category bias with no visible cause.
    """
    encoder = DictTextEncoder({"prompt a": axis(0), "text a": axis(0)})
    categories = embed_specs((PromptSpec(category="a", prompts=("prompt a",)),), encoder=encoder)
    examples = (
        _text_example("1", "a", "text a"),
        Example(
            id="2",
            category="a",
            locale="en",
            provenance=Provenance.AUTHORED,
            image="nowhere.png",
        ),
    )

    result = evaluate(
        examples,
        corpus=_corpus(*examples),
        split="holdout",
        text_categories=categories,
        text_encoder=encoder,
        calibration={},
        default=NEUTRAL,
        image_weight=0.45,
    )

    assert len(result.predictions) == 1
    assert result.macro_f1 == pytest.approx(1.0)


def test_evaluate_with_no_encoder_at_all_is_refused() -> None:
    """An empty report is worse than no report, because it looks like a result."""
    examples = (_text_example("1", "a", "text a"),)
    with pytest.raises(ValueError, match="no encoder"):
        evaluate(
            examples,
            corpus=_corpus(*examples),
            split="holdout",
            calibration={},
            default=NEUTRAL,
            image_weight=0.45,
        )


def test_below_floor_lists_exactly_the_categories_under_the_gate_threshold() -> None:
    encoder = DictTextEncoder(
        {
            "prompt a": axis(0),
            "prompt b": axis(1),
            "good": axis(0),
            "bad": tilted(0, 1, 0.4),
        }
    )
    specs = (
        PromptSpec(category="a", prompts=("prompt a",)),
        PromptSpec(category="b", prompts=("prompt b",)),
    )
    categories = embed_specs(specs, encoder=encoder)
    examples = (
        _text_example("1", "a", "good"),
        _text_example("2", "b", "bad"),
    )

    result = evaluate(
        examples,
        corpus=_corpus(*examples),
        split="holdout",
        text_categories=categories,
        text_encoder=encoder,
        calibration={},
        default=NEUTRAL,
        image_weight=0.45,
    )

    below = {entry.category for entry in result.below_floor}
    assert all(entry.f1 < F1_FLOOR for entry in result.below_floor)
    assert "b" in below


# ---------------------------------------------------------------------------
# Fitting
# ---------------------------------------------------------------------------


def test_a_fitted_curve_stays_inside_the_policy_document_bounds() -> None:
    """A fit that produces a document the API refuses is a fit nobody can use.

    Reached in practice: e5 cosines sit in a band roughly 0.02 wide, and the gap
    divided by the target logit separation lands right on the document's lower
    temperature bound.
    """
    from nemesis.policy.documents import CategoryCalibration

    encoder = DictTextEncoder(
        {
            "prompt a": axis(0),
            "prompt b": axis(1),
            "a one": axis(0),
            "a two": tilted(0, 2, 0.1),
            "b one": axis(1),
            "b two": tilted(1, 2, 0.1),
        }
    )
    specs = (
        PromptSpec(category="a", prompts=("prompt a",)),
        PromptSpec(category="b", prompts=("prompt b",)),
    )
    categories = embed_specs(specs, encoder=encoder)
    examples = (
        _text_example("1", "a", "a one"),
        _text_example("2", "a", "a two"),
        _text_example("3", "b", "b one"),
        _text_example("4", "b", "b two"),
    )

    fitted = harness.fit(examples, categories=categories, encoder=encoder, provenance="test")

    assert {entry.category for entry in fitted} == {"a", "b"}
    for entry in fitted:
        assert entry.temperature >= MIN_FITTED_TEMPERATURE
        # Constructed, not asserted about: the document is the arbiter of whether
        # these numbers are expressible, and building one is the check.
        CategoryCalibration(
            category=entry.category,
            temperature=entry.temperature,
            bias=entry.bias,
            abstain_below=entry.abstain_below,
            min_margin=entry.min_margin,
            sample_size=entry.sample_size,
            provenance=entry.provenance,
        )


def test_the_fitted_centre_tracks_the_out_of_class_similarity_it_was_measured_from() -> None:
    """``bias`` is the per-category centre in similarity units, so it is the
    negation of the measured out-of-class mean and an approver can check it
    against the two columns printed beside it."""
    encoder = DictTextEncoder(
        {
            "prompt a": axis(0),
            "prompt b": axis(1),
            "a one": axis(0),
            "a two": axis(0),
            "b one": axis(1),
            "b two": axis(1),
        }
    )
    specs = (
        PromptSpec(category="a", prompts=("prompt a",)),
        PromptSpec(category="b", prompts=("prompt b",)),
    )
    categories = embed_specs(specs, encoder=encoder)
    examples = (
        _text_example("1", "a", "a one"),
        _text_example("2", "a", "a two"),
        _text_example("3", "b", "b one"),
        _text_example("4", "b", "b two"),
    )

    fitted = harness.fit(examples, categories=categories, encoder=encoder, provenance="test")

    for entry in fitted:
        assert entry.bias == pytest.approx(-entry.mean_negative_similarity, abs=1e-6)
        assert entry.mean_positive_similarity > entry.mean_negative_similarity


def test_a_category_with_no_in_class_example_is_not_fitted_at_all() -> None:
    """A fabricated entry would be an approver reading a measurement that was
    never taken. An uncalibrated category gets the tenant's defaults, which is
    what an uncalibrated category is supposed to get."""
    encoder = DictTextEncoder(
        {
            "prompt a": axis(0),
            "prompt absent": axis(3),
            "a one": axis(0),
            "a two": axis(0),
        }
    )
    specs = (
        PromptSpec(category="a", prompts=("prompt a",)),
        PromptSpec(category="absent", prompts=("prompt absent",)),
    )
    categories = embed_specs(specs, encoder=encoder)
    examples = (
        _text_example("1", "a", "a one"),
        _text_example("2", "a", "a two"),
        # A third label so a has out-of-class examples to measure a centre
        # against. absent still has none of its own, which is the point.
        _text_example("3", "other", "a two"),
    )

    fitted = harness.fit(examples, categories=categories, encoder=encoder, provenance="test")

    assert {entry.category for entry in fitted} == {"a"}


def test_the_proposed_document_is_shaped_for_the_policy_api() -> None:
    """The harness proposes; an approver decides. It never deploys anything.

    Asserted by constructing the real ``PerceptionCalibration`` from the dict,
    which is what the control-plane endpoint does with it.
    """
    from nemesis.policy.documents import PerceptionCalibration

    fitted = (
        harness.FittedCategory(
            category="pothole",
            temperature=0.0087,
            bias=-0.81,
            abstain_below=0.164,
            min_margin=0.05,
            sample_size=54,
            positives=6,
            mean_positive_similarity=0.8445,
            mean_negative_similarity=0.81,
            provenance="test",
        ),
    )

    document = PerceptionCalibration.model_validate(harness.calibration_document(fitted))

    assert document.categories[0].category == "pothole"
    assert document.categories[0].sample_size == 54


def test_calibration_from_round_trips_into_the_scorer_shape() -> None:
    fitted = (
        harness.FittedCategory(
            category="a",
            temperature=0.01,
            bias=-0.8,
            abstain_below=0.2,
            min_margin=0.05,
            sample_size=10,
            positives=4,
            mean_positive_similarity=0.9,
            mean_negative_similarity=0.8,
            provenance="",
        ),
    )
    curves = harness.calibration_from(fitted)

    assert curves["a"] == Calibration(
        temperature=0.01, bias=-0.8, abstain_below=0.2, min_margin=0.05
    )


# ---------------------------------------------------------------------------
# Distant-face recall
# ---------------------------------------------------------------------------


class _SizeGatedDetector:
    """Finds a face only at or above ``floor_pixels``. A stand-in for a scale limit."""

    detector_id = "size-gated@1"

    def __init__(self, floor_pixels: int) -> None:
        self._floor = floor_pixels

    def detect(self, *, width: int, height: int, rgb: bytes):
        from nemesis.perception.corpus import face_stimulus
        from nemesis.trust.detectors import FaceBox

        # Recover the geometry the generator drew, which is legitimate here: the
        # double is standing in for a detector's *scale* behaviour and nothing
        # else, so it is allowed to know where the face is.
        for size in (128, 96, 88, 80, 72, 64, 48, 32, 24, 16, 12):
            probe = face_stimulus(size)
            if probe.width == width and probe.height == height and size >= self._floor:
                x, y, box_width, box_height = probe.boxes[0]
                return [FaceBox(x=x, y=y, width=box_width, height=box_height, confidence=0.9)]
        return []


def test_face_recall_reports_the_smallest_size_at_full_recall() -> None:
    """The number §22.1 actually needs.

    "Recall is 0.87 overall" is not actionable; "every face at 80 px and above
    was found, and nothing below" tells an operator how far away a bystander has
    to be before the guarantee stops holding.
    """
    result = harness.measure_face_recall(_SizeGatedDetector(80), sizes=(32, 64, 80, 96), repeats=2)

    assert result.smallest_reliable == 80
    assert {bucket.face_pixels: bucket.recall for bucket in result.buckets} == {
        32: 0.0,
        64: 0.0,
        80: 1.0,
        96: 1.0,
    }


def test_a_detector_that_finds_nothing_reports_no_reliable_size() -> None:
    """``None`` rather than the largest size tried — the difference between "the
    guarantee holds above 96 px" and "this measurement establishes nothing"."""
    result = harness.measure_face_recall(_SizeGatedDetector(9999), sizes=(32, 96), repeats=1)
    assert result.smallest_reliable is None


def test_overlapping_detections_cannot_score_multiple_hits_on_one_face() -> None:
    """A detector returning forty boxes over one face must not score forty."""
    from nemesis.perception.corpus import face_stimulus
    from nemesis.trust.detectors import FaceBox

    class _Spammer:
        detector_id = "spammer@1"

        def detect(self, *, width: int, height: int, rgb: bytes):
            x, y, box_width, box_height = face_stimulus(96, faces=2).boxes[0]
            return [
                FaceBox(x=x, y=y, width=box_width, height=box_height, confidence=0.9)
                for _ in range(40)
            ]

    result = harness.measure_face_recall(_Spammer(), sizes=(96,), repeats=1, faces=2)

    (bucket,) = result.buckets
    assert bucket.faces_present == 2
    assert bucket.faces_found == 1, "one face was matched by many boxes"
