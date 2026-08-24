"""Per-category precision, recall and F1 over a held-out set — the phase gate.

**What this module is for.** Phase 9's gate is "a published per-category F1
number in the repo, reproducible by one command". This is the measurement behind
that sentence: it runs the *shipped* decision rule (``scoring.decide``, the same
function the pipeline stage calls) over a labelled corpus with the *shipped*
encoders, and computes the numbers per category, per locale, and per modality.
It writes a committed artefact and it prints the honest number whether or not
anybody likes it.

**Three things this file refuses to do, each because doing them is how a
validation harness stops measuring anything.**

*It does not re-implement the decision.* Fusing two modalities, applying a
temperature, and deciding whether the winner clears its abstain floor are all
``scoring``'s, called from here exactly as the stage calls them. A harness with
its own copy of the rule measures the copy, agrees with production on the day it
is written, and drifts silently afterwards — and the drift shows up as a report
that is *more* flattering than reality, because a harness is easier to get right
than a pipeline.

*It does not touch the calibration split when measuring.* The split is
``corpus.Corpus.split()``, stratified, deterministic, and computed from the
corpus file alone. Fitting temperature and bias on the same examples the F1 is
computed over would produce numbers in the high nineties that mean precisely
nothing.

*It does not treat an abstention as a wrong answer, and it does not hide the
cost of that.* An abstention is a false negative for the true category and a
false positive for nobody — which is correct, because §24.2 sends the report to
a human rather than to the wrong department. But that treatment can be gamed:
raise every abstain floor and precision goes to 1.0 while the system classifies
nothing. So every table carries the **coverage** — the share of examples that
got an answer at all — and a **forced** F1 computed with abstention disabled,
which is the same model judged as if it always had to guess. A category whose
shipped F1 is high and whose coverage is 0.2 is visible in one glance, which is
the only reason the abstain treatment is safe to use.

**On the latency numbers.** §27.1 budgets classification as CPU-bound and the
harness measures against ``PerceptionSettings.latency_budget_seconds`` rather
than against a constant of its own — the gate says "measured not estimated", and
a harness carrying its own budget would be estimating the thing it is checking.
What is timed is one example end to end through the encoders and the decision,
which is what a complaint costs; model *load* time is excluded and reported
separately, because a cold start is a deployment property and every complaint
after the first does not pay it.
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from nemesis.perception.corpus import Corpus, Example, FaceStimulus, face_stimuli
from nemesis.perception.encoders import (
    PASSAGE_PREFIX,
    QUERY_PREFIX,
    ImageEncoder,
    TextEncoder,
)
from nemesis.perception.scoring import (
    Calibration,
    CategoryVectors,
    Decision,
    ScoreResult,
    decide,
    score_against,
)

#: The gate's floor. A category below this triggers the §43.2 prompt pass and a
#: re-measure; the honest number ships either way. Stated here so the harness,
#: the report and the gate script cannot disagree about what "below" means.
F1_FLOOR: Final = 0.65

#: How much of a face's ground-truth box a detection must cover to count as a
#: hit. Intersection-over-union, the ordinary object-detection convention, and
#: 0.3 rather than the usual 0.5 on purpose: §22.1's obligation is discharged by
#: a blur that *covers* the face, and the redactor expands every box by a margin
#: before blurring. Demanding a tight box would report a miss for a detection
#: that fully protects the person, which measures the wrong thing.
FACE_IOU_THRESHOLD: Final = 0.3

#: Floor on a fitted temperature. The policy document bounds temperature above
#: 0.005 and a fit that lands under it produces a document the API refuses — so
#: the clamp is here, where the number is derived, rather than as a validation
#: error an operator meets three steps later. Reached in practice: e5 cosines sit
#: in a band roughly 0.02 wide, and a gap that narrow divided by the target logit
#: separation lands right on this value.
MIN_FITTED_TEMPERATURE: Final = 0.006


# ---------------------------------------------------------------------------
# Prompt bundles, without a database
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PromptSpec:
    """One category's prompts for one encoder and locale, as authored.

    The harness reads these from a *template* rather than from a tenant's rows
    for one reason: the gate says "reproducible by one command", and a command
    that first needs a provisioned database, a tenant, and a control-plane token
    is not one command. The template is the same document a tenant is
    provisioned from, so the prompts measured here are the prompts a new tenant
    gets — and Phase 9's third gate clause, that a *new* category is classifiable
    by adding prompts alone, is proven over HTTP against the running stack by
    ``scripts/gate_phase9.py`` where it belongs.
    """

    category: str
    prompts: tuple[str, ...]
    negative_prompts: tuple[str, ...] = ()


def prompt_specs_from_template(
    template_name: str, *, locale: str, encoder: str
) -> tuple[PromptSpec, ...]:
    """Every prompt set a template declares for one (locale, encoder) pair."""
    from nemesis.control_plane import templates

    template = templates.load(template_name)
    return tuple(
        PromptSpec(
            category=spec.node_key,
            prompts=tuple(spec.prompts),
            negative_prompts=tuple(spec.negative_prompts),
        )
        for spec in template.prompt_sets
        if spec.locale == locale and spec.encoder == encoder and spec.is_active
    )


def embed_specs(
    specs: Sequence[PromptSpec], *, encoder: ImageEncoder | TextEncoder
) -> tuple[CategoryVectors, ...]:
    """Embed prompt specs in one pass, split back per category.

    The same batching and the same offset bookkeeping ``prompts._embed_now``
    does, deliberately duplicated rather than shared: that function goes through
    the model registry keyed on a *tenant's* prompt-set content hash, and a
    harness run should not evict a worker's warm matrices — or, worse, be served
    one. Keeping the arithmetic identical is what matters, and it is fifteen
    lines.
    """
    flat: list[str] = []
    spans: list[tuple[int, int, int]] = []
    for spec in specs:
        start = len(flat)
        flat.extend(spec.prompts)
        middle = len(flat)
        flat.extend(spec.negative_prompts)
        spans.append((start, middle, len(flat)))
    if not flat:
        return ()

    encode_prompts = getattr(encoder, "encode_prompts", None)
    if encode_prompts is not None:
        vectors = tuple(encode_prompts(flat))
    else:
        vectors = tuple(encoder.encode(flat, prefix=PASSAGE_PREFIX))  # type: ignore[union-attr]
    if len(vectors) != len(flat):  # pragma: no cover - an encoder contract breach
        raise ValueError(
            f"the encoder returned {len(vectors)} vectors for {len(flat)} prompts; "
            f"splitting them per category would misattribute prompts and every number "
            f"in the report would be quietly wrong"
        )
    return tuple(
        CategoryVectors(
            category=spec.category,
            positives=tuple(vectors[start:middle]),
            negatives=tuple(vectors[middle:end]),
        )
        for spec, (start, middle, end) in zip(specs, spans, strict=True)
    )


# ---------------------------------------------------------------------------
# One example's outcome
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Prediction:
    """What the shipped rule concluded about one labelled example."""

    example_id: str
    truth: str
    locale: str
    provenance: str
    #: ``None`` when the rule abstained. Never a guess.
    predicted: str | None
    #: What it *would* have said with abstention disabled. Always a category, and
    #: it is what the ``forced`` columns are computed from.
    forced: str
    confidence: float
    margin: float
    abstained: bool
    modality: str
    seconds: float

    @property
    def correct(self) -> bool:
        return self.predicted == self.truth

    @property
    def forced_correct(self) -> bool:
        return self.forced == self.truth


@dataclass(frozen=True, slots=True)
class CategoryMetrics:
    """Precision, recall and F1 for one category, both as shipped and forced."""

    category: str
    support: int
    true_positives: int
    false_positives: int
    false_negatives: int
    abstentions: int
    precision: float
    recall: float
    f1: float
    forced_precision: float
    forced_recall: float
    forced_f1: float

    @property
    def coverage(self) -> float:
        """Share of this category's held-out examples that got any answer."""
        return 0.0 if self.support == 0 else (self.support - self.abstentions) / self.support

    @property
    def meets_floor(self) -> bool:
        return self.f1 >= F1_FLOOR


@dataclass(frozen=True, slots=True)
class LatencySummary:
    """Wall time per example, in seconds. Percentiles, never a mean alone."""

    operation: str
    count: int
    p50: float
    p95: float
    max: float

    @property
    def within(self) -> Callable[[float], bool]:
        return lambda budget: self.p95 <= budget


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    """Everything one harness pass concluded, before it is written anywhere."""

    modality: str
    split: str
    corpus_id: str
    corpus_fingerprint: str
    model_ids: tuple[str, ...]
    predictions: tuple[Prediction, ...]
    per_category: tuple[CategoryMetrics, ...]
    per_locale: Mapping[str, float]
    macro_f1: float
    micro_f1: float
    forced_macro_f1: float
    coverage: float
    latency: LatencySummary
    #: Ordered worst-first, so the pairs a prompt pass should attack are the
    #: first thing a reader sees rather than something they have to derive.
    confusions: tuple[tuple[str, str, int], ...]

    @property
    def below_floor(self) -> tuple[CategoryMetrics, ...]:
        return tuple(
            entry for entry in self.per_category if not entry.meets_floor and entry.support > 0
        )


# ---------------------------------------------------------------------------
# The measurement
# ---------------------------------------------------------------------------


def evaluate(
    examples: Sequence[Example],
    *,
    corpus: Corpus,
    split: str,
    text_categories: Sequence[CategoryVectors] = (),
    image_categories: Sequence[CategoryVectors] = (),
    text_encoder: TextEncoder | None = None,
    image_encoder: ImageEncoder | None = None,
    calibration: Mapping[str, Calibration],
    default: Calibration,
    image_weight: float,
) -> EvaluationResult:
    """Run the shipped decision rule over ``examples`` and count what happened.

    Both modalities are optional and the combination decides the reported
    ``modality`` string: ``text``, ``image``, or ``fused``. An example carrying
    an image is only scored on it when an image encoder *and* image prompts were
    supplied, so a run on a machine with no photograph corpus produces a text
    number that says ``text`` on it rather than a fused number quietly missing
    half its evidence.
    """
    if text_encoder is None and image_encoder is None:
        raise ValueError(
            "evaluate() was given no encoder at all; there is nothing to measure and "
            "an empty report is worse than no report because it looks like a result"
        )

    model_ids: list[str] = []
    if image_encoder is not None and image_categories:
        model_ids.append(image_encoder.model_id)
    if text_encoder is not None and text_categories:
        model_ids.append(text_encoder.model_id)

    predictions: list[Prediction] = []
    used_image = False
    used_text = False

    for example in examples:
        started = time.perf_counter()
        image_result: ScoreResult | None = None
        text_result: ScoreResult | None = None

        if example.image and image_encoder is not None and image_categories:
            data = corpus.image_path(example).read_bytes()
            image_result = score_against(
                image_encoder.encode_image(data),
                image_categories,
                calibration=calibration,
                default=default,
            )
            used_image = True

        if example.text and text_encoder is not None and text_categories:
            vectors = text_encoder.encode([example.text], prefix=QUERY_PREFIX)
            text_result = score_against(
                vectors[0], text_categories, calibration=calibration, default=default
            )
            used_text = True

        if image_result is None and text_result is None:
            # The example carries evidence this run cannot read — an image with
            # no image encoder, say. Skipped rather than scored as a miss: it is
            # a fact about the run, not about the model, and counting it would
            # depress every category that happens to have more photographs.
            continue

        decision = decide(
            image_result,
            text_result,
            calibration=calibration,
            default=default,
            image_weight=image_weight,
        )
        elapsed = time.perf_counter() - started
        predictions.append(
            Prediction(
                example_id=example.id,
                truth=example.category,
                locale=example.locale,
                provenance=example.provenance.value,
                predicted=decision.category,
                forced=_forced(decision),
                confidence=decision.confidence,
                margin=decision.margin,
                abstained=decision.abstained,
                modality=_modality(image_result is not None, text_result is not None),
                seconds=elapsed,
            )
        )

    categories = sorted(
        {entry.category for entry in (*text_categories, *image_categories)}
        | {example.category for example in examples}
    )
    per_category = tuple(_metrics_for(category, predictions) for category in categories)

    return EvaluationResult(
        modality=_modality(used_image, used_text),
        split=split,
        corpus_id=corpus.corpus_id,
        corpus_fingerprint=corpus.fingerprint(),
        model_ids=tuple(model_ids),
        predictions=tuple(predictions),
        per_category=per_category,
        per_locale=_per_locale_f1(predictions),
        macro_f1=_mean(entry.f1 for entry in per_category if entry.support > 0),
        micro_f1=_micro_f1(per_category),
        forced_macro_f1=_mean(entry.forced_f1 for entry in per_category if entry.support > 0),
        coverage=(
            0.0
            if not predictions
            else sum(1 for item in predictions if not item.abstained) / len(predictions)
        ),
        latency=_latency("classify_one", [item.seconds for item in predictions]),
        confusions=_confusions(predictions),
    )


def _forced(decision: Decision) -> str:
    """The category the rule would have claimed with abstention disabled.

    Read off the fused result rather than recomputed, so the forced number and
    the shipped number describe the same arithmetic differing in exactly one
    step. ``top_category`` rather than ``category`` because the latter is
    ``None`` on an abstention — and reaching for the top of ``alternatives``
    instead returns the *runner-up*, which is the defect this harness found on
    its first real run against multilingual-e5.
    """
    return decision.fused.top_category or decision.fused.category or ""


def _modality(image: bool, text: bool) -> str:
    if image and text:
        return "fused"
    if image:
        return "image"
    return "text" if text else "none"


def _metrics_for(category: str, predictions: Sequence[Prediction]) -> CategoryMetrics:
    """The confusion counts for one category, with abstentions kept separate.

    **An abstention is a false negative and never a false positive.** It costs
    the true category its recall — the report was not classified — and it accuses
    no other category of anything, because nothing was claimed. Folding it into
    the false positives of whichever category was second would be inventing a
    prediction the system deliberately declined to make.
    """
    support = sum(1 for item in predictions if item.truth == category)
    true_positives = sum(
        1 for item in predictions if item.truth == category and item.predicted == category
    )
    false_positives = sum(
        1 for item in predictions if item.truth != category and item.predicted == category
    )
    abstentions = sum(1 for item in predictions if item.truth == category and item.abstained)
    false_negatives = support - true_positives

    forced_tp = sum(1 for item in predictions if item.truth == category and item.forced == category)
    forced_fp = sum(1 for item in predictions if item.truth != category and item.forced == category)

    precision = _ratio(true_positives, true_positives + false_positives)
    recall = _ratio(true_positives, support)
    forced_precision = _ratio(forced_tp, forced_tp + forced_fp)
    forced_recall = _ratio(forced_tp, support)

    return CategoryMetrics(
        category=category,
        support=support,
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        abstentions=abstentions,
        precision=precision,
        recall=recall,
        f1=_f1(precision, recall),
        forced_precision=forced_precision,
        forced_recall=forced_recall,
        forced_f1=_f1(forced_precision, forced_recall),
    )


def _ratio(numerator: int, denominator: int) -> float:
    """Zero when the denominator is zero, and the zero is meaningful.

    A category nothing was predicted as has undefined precision, and the two
    conventions — 0.0 or 1.0 — say opposite things. 0.0 is chosen because the
    alternative lets a category that was never predicted report perfect
    precision, which is the single most misleading cell this table could
    contain. ``support`` is beside it in every row so an undefined number is
    identifiable rather than merely conservative.
    """
    return 0.0 if denominator == 0 else numerator / denominator


def _f1(precision: float, recall: float) -> float:
    total = precision + recall
    return 0.0 if total == 0.0 else 2.0 * precision * recall / total


def _mean(values: Any) -> float:
    collected = list(values)
    return 0.0 if not collected else sum(collected) / len(collected)


def _micro_f1(per_category: Sequence[CategoryMetrics]) -> float:
    """Pooled over examples rather than over categories.

    Reported *beside* macro rather than instead of it, because the two disagree
    in the case that matters here: a taxonomy has a couple of common categories
    and a long tail of rare ones, micro is dominated by the common ones, and the
    rare category that never works is invisible in it. Macro is the number the
    gate's floor is applied to per category; micro is the number that describes
    what a citizen experiences.
    """
    true_positives = sum(entry.true_positives for entry in per_category)
    false_positives = sum(entry.false_positives for entry in per_category)
    false_negatives = sum(entry.false_negatives for entry in per_category)
    precision = _ratio(true_positives, true_positives + false_positives)
    recall = _ratio(true_positives, true_positives + false_negatives)
    return _f1(precision, recall)


def _per_locale_f1(predictions: Sequence[Prediction]) -> dict[str, float]:
    """Macro F1 within each locale.

    Present because §8.4's promise is that a complaint in the citizen's own
    language works, and a single number over a mixed-language corpus can hide a
    language that does not — the Hindi and Marathi rows are the ones ADR-0003
    chose multilingual-e5 for, and they are the ones nobody would notice were
    broken.
    """
    locales = sorted({item.locale for item in predictions})
    result: dict[str, float] = {}
    for locale in locales:
        subset = [item for item in predictions if item.locale == locale]
        categories = sorted({item.truth for item in subset})
        result[locale] = _mean(_metrics_for(category, subset).f1 for category in categories)
    return result


def _confusions(predictions: Sequence[Prediction]) -> tuple[tuple[str, str, int], ...]:
    """(truth, predicted, count) for every wrong non-abstained answer, worst first.

    This is the §43.2 work list. "Category X scored 0.4" tells an author that
    something is wrong; "X was called Y eleven times" tells them which prompt to
    write, and the two prompts to contrast.
    """
    counts: dict[tuple[str, str], int] = {}
    for item in predictions:
        if item.abstained or item.predicted is None or item.predicted == item.truth:
            continue
        key = (item.truth, item.predicted)
        counts[key] = counts.get(key, 0) + 1
    ordered = sorted(counts.items(), key=lambda entry: (-entry[1], entry[0]))
    return tuple((truth, predicted, count) for (truth, predicted), count in ordered)


def _latency(operation: str, samples: Sequence[float]) -> LatencySummary:
    if not samples:
        return LatencySummary(operation=operation, count=0, p50=0.0, p95=0.0, max=0.0)
    ordered = sorted(samples)
    return LatencySummary(
        operation=operation,
        count=len(ordered),
        p50=statistics.median(ordered),
        # Nearest-rank rather than an interpolated quantile: with fifty samples
        # an interpolated p95 is a weighted average of two measurements, and the
        # number this is compared against is a budget a real request either met
        # or did not.
        p95=ordered[min(len(ordered) - 1, max(0, round(0.95 * len(ordered)) - 1))],
        max=ordered[-1],
    )


# ---------------------------------------------------------------------------
# Fitting the calibration curves (the "derived from measured curves" half)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FittedCategory:
    """One category's curve, fitted on the calibration split."""

    category: str
    temperature: float
    bias: float
    abstain_below: float
    min_margin: float
    sample_size: int
    positives: int
    #: Mean in-class and out-of-class cosine, so an approver can see the
    #: separation the numbers were derived from rather than only the conclusion.
    mean_positive_similarity: float
    mean_negative_similarity: float
    provenance: str


#: The margin rule the fitted document keeps. Held at the policy document's own
#: default while the abstain floor is swept, rather than fitted alongside it:
#: the two thresholds trade against each other in the same direction, and
#: fitting both on one small calibration split fits the split. Sweeping the
#: floor *under* the margin rule that will actually be in force is what makes
#: the fitted floor mean something in production.
FITTED_MIN_MARGIN: Final = 0.05

#: Where a category with nothing to sweep lands. The policy document's own
#: default, so an uncalibrated category and a category the calibration split
#: never exercised behave identically — which is true, and is what an approver
#: reading ``sample_size`` next to it needs it to be.
DEFAULT_ABSTAIN_FLOOR: Final = 0.35

#: Lower clamp on a fitted floor. A floor at the lowest confidence this category
#: ever won at claims everything, including the tail the sweep never saw, and a
#: threshold fitted on a few dozen examples should not be trusted to three
#: decimal places.
MIN_FITTED_ABSTAIN: Final = 0.05

#: Share of a category's correct calibration wins the fitted floor keeps. Nine
#: tenths: the tenth given up is the least confident, which is the tenth most
#: likely to have been right by accident, and giving it up costs a human read
#: rather than a wrong route. Stated as a number somebody can argue with rather
#: than derived, because it is a judgement about the relative cost of the two
#: errors and no split of a few dozen examples knows that.
RETAINED_RECALL: Final = 0.9


def fit(
    examples: Sequence[Example],
    *,
    categories: Sequence[CategoryVectors],
    encoder: TextEncoder | ImageEncoder,
    provenance: str,
    target_logit_gap: float = 4.0,
) -> tuple[FittedCategory, ...]:
    """Derive a per-category temperature, centre and abstain floor from measured curves.

    **Two passes, and the split between them is the point.**

    *Pass one fits the scale.* For each category the harness collects the pooled
    max-positive cosine on in-class calibration examples and on out-of-class
    ones, and picks the temperature that maps the *measured* gap between those
    two means onto a fixed gap in logit space, with the out-of-class mean as the
    category's centre. That makes a category whose prompts sit high and tight in
    the model's similarity band comparable inside the softmax to one whose
    prompts sit low and wide.

    *Pass two fits the floor, and it has to be a second pass.* The abstain
    threshold is compared against a confidence that comes out of a softmax over
    *every* category and its contrast set — a number that does not exist until
    pass one has produced curves for all of them. Sweeping a threshold against a
    one-vs-rest sigmoid instead, which is the obvious shortcut, produces floors
    near 0.9 for a scorer whose real confidences peak around 0.3, and the layer
    then abstains on nine tenths of what it classifies correctly. That is not
    hypothetical: it is what the first version of this function did, and the
    coverage column in the report is what showed it.

    **What this is not.** It is not a calibrated posterior. A true calibration
    would fit against observed correctness with a proper scoring rule and would
    need far more labelled data than a bootstrap corpus has. This is *scale
    normalisation plus an operating point*, which is what the softmax and the
    §24.2 fallback actually need; it is recorded as such in ``provenance`` on
    every entry, and the report says it in words. The distinction matters at
    approval time: an approver told "these are calibrated probabilities" would
    reasonably read a 0.7 as seven-in-ten, and it is not.
    """
    by_category = {entry.category: entry for entry in categories}
    embedded: list[tuple[Example, tuple[float, ...]]] = [
        (example, _encode_one(encoder, example.text)) for example in examples if example.text
    ]

    # -- pass one: temperature and centre, from the similarity statistics ----
    scored: dict[str, list[tuple[float, bool]]] = {key: [] for key in by_category}
    for example, vector in embedded:
        for key, entry in by_category.items():
            best = max(_cosine(vector, prompt) for prompt in entry.positives)
            scored[key].append((best, example.category == key))

    curves: dict[str, Calibration] = {}
    measured: dict[str, tuple[float, float, int, int]] = {}
    for key in sorted(by_category):
        samples = scored[key]
        positives = [value for value, is_positive in samples if is_positive]
        negatives = [value for value, is_positive in samples if not is_positive]
        if not positives or not negatives:
            # A category with no in-class calibration example cannot be fitted,
            # and a fabricated entry for it would be an approver reading a
            # measurement that was never taken. It keeps the tenant's defaults,
            # which is what an uncalibrated category is supposed to get.
            continue
        mean_positive = sum(positives) / len(positives)
        mean_negative = sum(negatives) / len(negatives)
        gap = max(mean_positive - mean_negative, 1e-4)
        temperature = max(gap / target_logit_gap, MIN_FITTED_TEMPERATURE)
        # The per-category centre, in similarity units, added before the
        # temperature divides. Without it, normalising the gap alone leaves the
        # category with the smallest temperature ahead of every other one on
        # arithmetic rather than on evidence — see ``scoring``'s module
        # docstring. In similarity space rather than logit space so the number
        # stays inside the ±10 bound the policy document puts on it and stays
        # readable by the person approving it: this is a cosine, and a cosine can
        # be sanity-checked against the two columns printed beside it.
        curves[key] = Calibration(
            temperature=round(temperature, 6),
            bias=round(-mean_negative, 6),
            abstain_below=0.0,
            min_margin=0.0,
        )
        measured[key] = (mean_positive, mean_negative, len(samples), len(positives))

    if not curves:
        return ()

    # -- pass two: the operating point, on the confidences the scorer emits --
    observations: list[tuple[str, str, float, float]] = []
    for example, vector in embedded:
        result = score_against(
            vector,
            categories,
            calibration=curves,
            default=Calibration(temperature=0.05, abstain_below=0.0, min_margin=0.0),
        )
        observations.append(
            (
                example.category,
                result.top_category or "",
                result.confidence,
                result.margin,
            )
        )

    # **One operating point for the tenant, not one per category, and the reason
    # is sample size rather than principle.** The document supports a floor per
    # category and a mature deployment should fit one — but a quantile taken over
    # the six correct wins a category gets on a corpus this size is noise with
    # three decimal places. Measured: per-category floors fitted that way landed
    # between 0.095 and 0.773 on the *same* corpus, and the two categories that
    # drew the high ones went from a usable ranking to a held-out F1 of exactly
    # zero. Pooling puts the whole calibration split behind one number. The
    # provenance on every entry says the floor is shared, so an approver is not
    # told a per-category measurement was made when it was not, and Phase 11's
    # labelling loop is what makes per-category floors honest later.
    threshold, achieved = _operating_point(observations)

    fitted: list[FittedCategory] = []
    for key in sorted(curves):
        mean_positive, mean_negative, sample_size, positive_count = measured[key]
        curve = curves[key]
        fitted.append(
            FittedCategory(
                category=key,
                temperature=curve.temperature,
                bias=curve.bias,
                abstain_below=round(threshold, 4),
                min_margin=FITTED_MIN_MARGIN,
                sample_size=sample_size,
                positives=positive_count,
                mean_positive_similarity=round(mean_positive, 6),
                mean_negative_similarity=round(mean_negative, 6),
                provenance=(
                    f"{provenance}; temperature and centre fitted per category, "
                    f"abstain floor shared across the tenant (too few labelled "
                    f"examples per category for a per-category floor to be a "
                    f"measurement); macro F1 on the calibration split at this "
                    f"operating point: {achieved:.3f}. Scale normalisation plus an "
                    f"operating point, not a calibrated posterior"
                )[:500],
            )
        )
    return tuple(fitted)


def _operating_point(
    observations: Sequence[tuple[str, str, float, float]],
) -> tuple[float, float]:
    """The tenant's abstain floor, as a quantile of every correct calibration win.

    **Why a quantile and not the F1-maximising threshold.** The obvious rule —
    sweep every observed confidence and keep the one with the best calibration
    F1 — was tried first and is what a textbook suggests. On a split of a few
    dozen examples it lands on a knife edge: the maximum is often one example
    wide, it moves several tenths when a sentence is added to the corpus, and it
    does not survive contact with the held-out set.

    So the floor is stated as an *operating point* instead: **claim a category
    when the confidence is at least as high as it was for ``RETAINED_RECALL`` of
    the calibration examples the layer already got right.** That is a decision
    somebody can argue with in words — "we accept losing the least confident
    tenth" — it is stable under small corpus changes because a quantile is, and
    it fails in the safe direction, because the examples it gives up are the ones
    nearest the boundary.

    Returned with the macro F1 the point achieves on the calibration split, which
    goes into every entry's ``provenance`` so an approver sees the evidence
    rather than only the number. The margin rule is applied at
    ``FITTED_MIN_MARGIN`` throughout, because that is the rule the fitted
    document ships with.
    """
    correct = sorted(
        confidence
        for truth, winner, confidence, margin in observations
        if winner == truth and margin >= FITTED_MIN_MARGIN
    )
    if not correct:
        # Nothing was classified correctly on the calibration split at all. The
        # tenant's default floor is the only honest answer, and the macro F1
        # returned beside it — zero — is what tells the approver so.
        return DEFAULT_ABSTAIN_FLOOR, 0.0

    index = int((1.0 - RETAINED_RECALL) * len(correct))
    threshold = min(max(correct[min(index, len(correct) - 1)], MIN_FITTED_ABSTAIN), 0.9)

    categories = sorted({truth for truth, _, _, _ in observations})
    scores: list[float] = []
    for category in categories:
        support = sum(1 for truth, _, _, _ in observations if truth == category)
        claimed = [
            truth
            for truth, winner, confidence, margin in observations
            if winner == category and confidence >= threshold and margin >= FITTED_MIN_MARGIN
        ]
        true_positives = sum(1 for truth in claimed if truth == category)
        scores.append(_f1(_ratio(true_positives, len(claimed)), _ratio(true_positives, support)))
    return threshold, _mean(scores)


def _encode_one(encoder: TextEncoder | ImageEncoder, text: str) -> tuple[float, ...]:
    return tuple(encoder.encode([text], prefix=QUERY_PREFIX)[0])  # type: ignore[union-attr]


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    from nemesis.perception.encoders import cosine

    return cosine(left, right)


def calibration_document(
    fitted: Sequence[FittedCategory], *, image_weight: float = 0.45
) -> dict[str, Any]:
    """The fitted curves as a ``perception_calibration`` policy body.

    A plain dict, not a ``PerceptionCalibration``. This module is imported by a
    script that runs in ``worker-ml`` and by the test suite, and the point of
    ``calibration.py`` existing at all is that the arithmetic side never imports
    the policy side. The dict is shaped so it can be POSTed to
    ``/api/v1/control-plane/policies/perception_calibration`` unchanged, which is
    the actual handoff: the harness *proposes*, an approver decides, and Phase 6
    keeps the trail.
    """
    return {
        "image_weight": image_weight,
        "categories": [
            {
                "category": entry.category,
                "temperature": entry.temperature,
                "bias": entry.bias,
                "abstain_below": entry.abstain_below,
                "min_margin": entry.min_margin,
                "sample_size": entry.sample_size,
                "provenance": entry.provenance[:500],
            }
            for entry in fitted
        ],
    }


def calibration_from(fitted: Sequence[FittedCategory]) -> dict[str, Calibration]:
    """The fitted curves in the shape ``scoring.score_against`` reads."""
    return {
        entry.category: Calibration(
            temperature=entry.temperature,
            bias=entry.bias,
            abstain_below=entry.abstain_below,
            min_margin=entry.min_margin,
        )
        for entry in fitted
    }


# ---------------------------------------------------------------------------
# Distant-face recall (§22.1), on the same harness
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FaceRecallBucket:
    """Recall at one face size, with the sample size beside it."""

    face_pixels: int
    faces_present: int
    faces_found: int
    mean_confidence: float

    @property
    def recall(self) -> float:
        return _ratio(self.faces_found, self.faces_present)


@dataclass(frozen=True, slots=True)
class FaceRecallResult:
    detector_id: str
    iou_threshold: float
    buckets: tuple[FaceRecallBucket, ...]

    @property
    def smallest_reliable(self) -> int | None:
        """The smallest face size where recall is still 1.0.

        The number §22.1 actually needs. "Recall is 0.87 overall" is not
        actionable; "every face at 32 px and above was found, and nothing below
        24 px was" tells an operator how far away a bystander has to be before
        the guarantee stops holding, which is the sentence the obligation is
        written in.
        """
        reliable = [bucket.face_pixels for bucket in self.buckets if bucket.recall >= 1.0]
        return min(reliable) if reliable else None


def measure_inference_latency(
    *,
    image_encoder: ImageEncoder | None = None,
    text_encoder: TextEncoder | None = None,
    transcriber: Any = None,
    image: bytes | None = None,
    audio: bytes | None = None,
    text: str = "there is a large pothole in the road outside the school gate",
    locales: Sequence[str] = ("en",),
    repeats: int = 5,
) -> tuple[LatencySummary, ...]:
    """One pass per model, timed, so §27.1 is measured for all three not one.

    **Why this exists as a separate function from ``evaluate``.** The corpus is
    text, so ``evaluate`` times the text encoder and nothing else — and the
    published latency number was therefore the *cheapest* of the three models
    while the clause it satisfied said "inference latency within the §27.1
    budget". CLIP encode and Whisper transcribe are the dominant costs in
    production. A budget checked against the one model that comfortably meets it
    is a budget nobody is checking.

    Each measurement is a single forward pass over one fixed input, repeated,
    with the first call outside the sample: the first call pays the model load,
    which is a deployment property every subsequent complaint does not pay and
    which ``perception_model_load_seconds`` reports separately.
    """
    summaries: list[LatencySummary] = []

    if image_encoder is not None and image is not None:
        image_encoder.encode_image(image)  # warm, discarded
        samples = []
        for _ in range(repeats):
            started = time.perf_counter()
            image_encoder.encode_image(image)
            samples.append(time.perf_counter() - started)
        summaries.append(_latency("encode_image", samples))

    if text_encoder is not None:
        text_encoder.encode([text], prefix=QUERY_PREFIX)
        samples = []
        for _ in range(repeats):
            started = time.perf_counter()
            text_encoder.encode([text], prefix=QUERY_PREFIX)
            samples.append(time.perf_counter() - started)
        summaries.append(_latency("encode_text", samples))

    if transcriber is not None and audio is not None:
        transcriber.transcribe(audio, locales=list(locales))
        samples = []
        for _ in range(repeats):
            started = time.perf_counter()
            transcriber.transcribe(audio, locales=list(locales))
            samples.append(time.perf_counter() - started)
        summaries.append(_latency("transcribe", samples))

    return tuple(summaries)


def measure_face_recall(
    detector: Any,
    *,
    sizes: Sequence[int] | None = None,
    repeats: int = 4,
    faces: int = 1,
    iou_threshold: float = FACE_IOU_THRESHOLD,
) -> FaceRecallResult:
    """Recall as a function of face size, against the registered face detector.

    Carried forward from Phase 0 and explicitly *not* discharged by Phase 8:
    ``blaze_face_short_range`` is a two-metre model, street photography is full
    of small bystanders, and those bystanders are exactly the population §22.1
    protects. Phase 8 proved a face gets blurred; this measures *which* faces.
    """
    from nemesis.perception.corpus import FACE_PIXEL_SIZES

    chosen = tuple(sizes) if sizes is not None else FACE_PIXEL_SIZES
    buckets: list[FaceRecallBucket] = []
    for size in chosen:
        present = 0
        found = 0
        confidences: list[float] = []
        for stimulus in face_stimuli((size,), repeats=repeats, faces=faces):
            detections = detector.detect(
                width=stimulus.width, height=stimulus.height, rgb=stimulus.rgb
            )
            present += stimulus.face_count
            matched, scores = _match(stimulus, detections, iou_threshold)
            found += matched
            confidences.extend(scores)
        buckets.append(
            FaceRecallBucket(
                face_pixels=size,
                faces_present=present,
                faces_found=found,
                mean_confidence=round(_mean(confidences), 4),
            )
        )
    return FaceRecallResult(
        detector_id=str(getattr(detector, "detector_id", "unknown")),
        iou_threshold=iou_threshold,
        buckets=tuple(buckets),
    )


def _match(
    stimulus: FaceStimulus, detections: Sequence[Any], threshold: float
) -> tuple[int, list[float]]:
    """How many ground-truth faces some detection covered, greedily, once each.

    Greedy and one-to-one: a detector that returns forty overlapping boxes must
    not score forty hits on one face. Whether the *extra* boxes are a problem is
    a different question — over-blurring is the direction §22.1 wants — so they
    are not counted against anything here, and the report says so.
    """
    used: set[int] = set()
    matched = 0
    scores: list[float] = []
    for box in stimulus.boxes:
        best_index, best_iou, best_confidence = -1, 0.0, 0.0
        for index, detection in enumerate(detections):
            if index in used:
                continue
            overlap = _iou(
                box,
                (
                    int(detection.x),
                    int(detection.y),
                    int(detection.width),
                    int(detection.height),
                ),
            )
            if overlap > best_iou:
                best_index, best_iou = index, overlap
                best_confidence = float(getattr(detection, "confidence", 0.0))
        if best_index >= 0 and best_iou >= threshold:
            used.add(best_index)
            matched += 1
            scores.append(best_confidence)
    return matched, scores


def _iou(left: tuple[int, int, int, int], right: tuple[int, int, int, int]) -> float:
    lx, ly, lw, lh = left
    rx, ry, rw, rh = right
    ix = max(0, min(lx + lw, rx + rw) - max(lx, rx))
    iy = max(0, min(ly + lh, ry + rh) - max(ly, ry))
    intersection = ix * iy
    if intersection == 0:
        return 0.0
    union = lw * lh + rw * rh - intersection
    return 0.0 if union <= 0 else intersection / union


# ---------------------------------------------------------------------------
# The report artefact
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Report:
    """What gets committed. JSON for the gate, Markdown for a human."""

    generated: str
    corpus_id: str
    corpus_fingerprint: str
    corpus_description: str
    template: str
    calibration_source: str
    latency_budget_seconds: float
    holdout: EvaluationResult
    calibration_split_size: int
    #: The same held-out examples scored with the *document defaults* instead of
    #: the fitted curves. Reported beside the headline number because "the
    #: calibration fit was worth doing" is a claim, and a claim with no control
    #: measurement beside it is a preference. It is also the number a tenant
    #: sees on day one, before anybody has approved a fitted document.
    baseline: EvaluationResult | None = None
    #: The same measurement over the **calibration** split. It exists for one
    #: purpose and it is not the headline: it is the §43.2 prompt-pass work list.
    #:
    #: Reading which categories to rewrite prompts for off the *held-out*
    #: confusions is the most natural mistake available here, and it quietly
    #: turns the held-out set into a development set — the re-measured number
    #: then reports how well the prompts were tuned to the examples they were
    #: tuned on. So the work list comes from the split the curves were already
    #: fitted on, which is already spent, and the held-out number stays a number
    #: about examples nothing has been tuned against.
    worklist: EvaluationResult | None = None
    #: One entry per model, timed independently. The held-out ``latency`` above
    #: covers the text encoder only, because the corpus is text — so a budget
    #: checked against it alone is a budget checked against the cheapest of the
    #: three models. See ``measure_inference_latency``.
    per_model_latency: tuple[LatencySummary, ...] = ()
    fitted: tuple[FittedCategory, ...] = ()
    face_recall: FaceRecallResult | None = None
    #: Free text recorded by the runner: what was measured, what was not, and
    #: what a reader must not conclude from it. Never generated — a caveat a
    #: program wrote is a caveat nobody reads.
    caveats: tuple[str, ...] = ()
    prompt_pass: tuple[str, ...] = field(default_factory=tuple)

    @property
    def meets_gate(self) -> bool:
        """Every measured category at or above the floor, and latency in budget.

        The gate clause is *not* "the number is good". It is "the number is
        published, reproducible, and any category below the floor triggered a
        prompt pass and a re-measure" — so a report that fails this property is
        still a valid artefact, and the gate script is what decides whether the
        §43.2 work was done. This property answers only the first half.
        """
        return (
            not self.holdout.below_floor
            and self.holdout.latency.p95 <= self.latency_budget_seconds
            and all(entry.p95 <= self.latency_budget_seconds for entry in self.per_model_latency)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated": self.generated,
            "phase": 9,
            "f1_floor": F1_FLOOR,
            "corpus": {
                "id": self.corpus_id,
                "fingerprint": self.corpus_fingerprint,
                "description": self.corpus_description,
                "template": self.template,
                "holdout_examples": len(self.holdout.predictions),
                "calibration_examples": self.calibration_split_size,
            },
            "models": list(self.holdout.model_ids),
            "modality": self.holdout.modality,
            "calibration_source": self.calibration_source,
            "latency": {
                "budget_seconds": self.latency_budget_seconds,
                "operation": self.holdout.latency.operation,
                "count": self.holdout.latency.count,
                "p50_seconds": round(self.holdout.latency.p50, 4),
                "p95_seconds": round(self.holdout.latency.p95, 4),
                "max_seconds": round(self.holdout.latency.max, 4),
                "within_budget": self.holdout.latency.p95 <= self.latency_budget_seconds,
                "per_model": [
                    {
                        "operation": entry.operation,
                        "count": entry.count,
                        "p50_seconds": round(entry.p50, 4),
                        "p95_seconds": round(entry.p95, 4),
                        "max_seconds": round(entry.max, 4),
                        "within_budget": entry.p95 <= self.latency_budget_seconds,
                    }
                    for entry in self.per_model_latency
                ],
            },
            "totals": {
                "macro_f1": round(self.holdout.macro_f1, 4),
                "micro_f1": round(self.holdout.micro_f1, 4),
                "forced_macro_f1": round(self.holdout.forced_macro_f1, 4),
                "coverage": round(self.holdout.coverage, 4),
            },
            "baseline_totals": (
                None
                if self.baseline is None
                else {
                    "macro_f1": round(self.baseline.macro_f1, 4),
                    "micro_f1": round(self.baseline.micro_f1, 4),
                    "forced_macro_f1": round(self.baseline.forced_macro_f1, 4),
                    "coverage": round(self.baseline.coverage, 4),
                }
            ),
            "per_locale_macro_f1": {
                locale: round(value, 4) for locale, value in self.holdout.per_locale.items()
            },
            "per_category": [
                {
                    "category": entry.category,
                    "support": entry.support,
                    "precision": round(entry.precision, 4),
                    "recall": round(entry.recall, 4),
                    "f1": round(entry.f1, 4),
                    "coverage": round(entry.coverage, 4),
                    "abstentions": entry.abstentions,
                    "forced_f1": round(entry.forced_f1, 4),
                    "meets_floor": entry.meets_floor,
                }
                for entry in self.holdout.per_category
            ],
            "confusions": [
                {"truth": truth, "predicted": predicted, "count": count}
                for truth, predicted, count in self.holdout.confusions
            ],
            "prompt_pass_worklist": (
                []
                if self.worklist is None
                else [
                    {"truth": truth, "predicted": predicted, "count": count}
                    for truth, predicted, count in self.worklist.confusions
                ]
            ),
            "below_floor": [entry.category for entry in self.holdout.below_floor],
            "prompt_pass": list(self.prompt_pass),
            "fitted_calibration": [
                {
                    "category": entry.category,
                    "temperature": entry.temperature,
                    "bias": entry.bias,
                    "abstain_below": entry.abstain_below,
                    "sample_size": entry.sample_size,
                    "mean_positive_similarity": entry.mean_positive_similarity,
                    "mean_negative_similarity": entry.mean_negative_similarity,
                }
                for entry in self.fitted
            ],
            "face_recall": (
                None
                if self.face_recall is None
                else {
                    "detector_id": self.face_recall.detector_id,
                    "iou_threshold": self.face_recall.iou_threshold,
                    "smallest_reliable_px": self.face_recall.smallest_reliable,
                    "buckets": [
                        {
                            "face_pixels": bucket.face_pixels,
                            "faces_present": bucket.faces_present,
                            "faces_found": bucket.faces_found,
                            "recall": round(bucket.recall, 4),
                            "mean_confidence": bucket.mean_confidence,
                        }
                        for bucket in self.face_recall.buckets
                    ],
                }
            ),
            "caveats": list(self.caveats),
        }


__all__ = [
    "F1_FLOOR",
    "FACE_IOU_THRESHOLD",
    "CategoryMetrics",
    "EvaluationResult",
    "FaceRecallBucket",
    "FaceRecallResult",
    "FittedCategory",
    "LatencySummary",
    "Prediction",
    "PromptSpec",
    "Report",
    "calibration_document",
    "calibration_from",
    "embed_specs",
    "evaluate",
    "fit",
    "measure_face_recall",
    "measure_inference_latency",
    "prompt_specs_from_template",
]
