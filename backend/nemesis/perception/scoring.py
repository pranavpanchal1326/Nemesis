"""Zero-shot scoring: cosine similarities in, a decision out. No models here.

**Why this module holds no model and touches no database.** Everything below is
arithmetic over vectors somebody else produced, which makes the whole scoring
policy — pooling, contrast, temperature, the abstain rule — assertable exactly,
by a test that hands it numbers. The parts that need weights are in ``encoders``
and the parts that need a tenant are in ``prompts``; what is left is the part
that decides what a citizen's report is about, and that part should be the
easiest thing in the phase to read and to argue with.

**The four decisions this file makes, and why each is the one it is.**

*Pooling is max, not mean.* A category with eight prompts covering eight ways a
pothole can look would be punished by a mean — every prompt that does not match
this particular photograph drags the average down, so the *better described*
category loses to the thinly described one. Max asks "does any description of
this category fit", which is the question zero-shot classification actually is.

*Negatives are competitors, not a subtraction.* ``TaxonomyPromptSet`` carries
negative prompts because CLIP is a comparison and not a detector: with no
contrast set, every image is whichever category was listed first, at a
confidence that looks entirely credible. Subtracting a negative score from a
positive one would let a strong negative push a category *below* an unrelated
one it never competed with, so instead each negative pool enters the same
softmax as an extra competitor.

**What that does and does not do, stated precisely, because an earlier version of
this paragraph got it wrong.** A softmax denominator is shared, so a negative
that matches strongly suppresses the confidence of *every* category, not only the
one whose prompt set it belongs to. It therefore does two things: it stops a
report that matches nothing from being assigned the least-bad category at a
credible-looking confidence (which is what it is for), and it lowers confidences
across the board, which pushes borderline reports toward the §24.2 abstain path.
It does **not** selectively penalise its own category relative to the others —
the ranking among positives is untouched by any negative. A design where a
negative penalises only its owner is a per-category contrast rather than a shared
softmax; it is a real alternative, it is not what ships today, and the two have
not been measured against each other. Recorded here rather than implied, because
the difference is invisible in the output and a reader would reasonably assume
the stronger property.

*Temperature is per category and comes from a governed document.* Raw CLIP
cosines live in a narrow band (roughly 0.15 to 0.35 for ViT-B-32), so an
un-temperatured softmax over them returns near-uniform probabilities for
everything — 0.21 for the right answer and 0.19 for the wrong one, which is
useless as a confidence and worse than useless as a routing threshold. Dividing
by a temperature spreads the band; doing it *per category* is what makes a
category whose prompts happen to sit high in CLIP space comparable to one whose
prompts sit low. The numbers come from ``PerceptionCalibration`` rather than from
this file, because they are measured from a tenant's own data and must be
approvable, effective-dated, and simulatable — which is Phase 6 and Phase 7's
whole point.

*Bias is applied in similarity space, before the temperature, and it is what
makes a per-category temperature legal at all.* ``logit = (cosine + bias) /
temperature``. A softmax is invariant to a shift applied to every logit, so a
single global temperature needs no offset — but the moment two categories divide
by different temperatures, their logits are on different scales and the one with
the smaller temperature wins everything regardless of the similarities. The bias
is the per-category centre that puts them back on a common scale, which is why it
is a *cosine* (bounded, readable by an approver, in the model's own units) rather
than a raw logit offset in the hundreds.

*Abstention is an outcome, not a failure.* Below the floor, or with too small a
margin over the runner-up, this returns no category. §24.2's degraded path then
parks the report as ``pending_classification`` for a human. A perception layer
that always answers is a perception layer that routes a gas smell to Roads at
0.22 confidence, and nothing downstream can tell that apart from knowledge.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from nemesis.perception.encoders import cosine

#: Guard on the temperature divisor. A calibration document already bounds
#: temperature above zero, so this only catches a float that arrived through a
#: path the validator did not see — and dividing a cosine by ~0 produces an
#: infinity that softmaxes to a confidence of exactly 1.0, which is the most
#: convincing wrong answer this system could produce.
MIN_TEMPERATURE: Final = 1e-3


@dataclass(frozen=True, slots=True)
class CategoryVectors:
    """One taxonomy node's prompts, already embedded.

    ``positives`` is never empty — ``taxonomy_prompt_sets`` has a CHECK
    constraint saying so, because a category with no prompt is a category that
    can never win and would sit in every score dict at negative infinity,
    looking like a model failure rather than a configuration gap.
    """

    category: str
    positives: tuple[tuple[float, ...], ...]
    negatives: tuple[tuple[float, ...], ...] = ()


@dataclass(frozen=True, slots=True)
class Calibration:
    """The per-category knobs, flattened out of the governed document.

    A plain dataclass rather than the policy body itself, so this module stays
    importable by anything — the validation harness runs outside a request and
    outside a tenant, and making it construct a ``PolicyBody`` to score a fixture
    would put the policy package in the dependency path of a measurement.
    """

    temperature: float
    #: Added to the cosine *before* the temperature divides it. See the module
    #: docstring: it is the per-category centre, in similarity units.
    bias: float = 0.0
    #: Confidence below which no category is claimed.
    abstain_below: float = 0.0
    #: Required lead over the runner-up. Zero disables the check.
    min_margin: float = 0.0


@dataclass(frozen=True, slots=True)
class ScoreResult:
    """What the scorer concluded, with enough detail to re-argue it later.

    ``alternatives`` is the whole distribution minus the winner, and it is
    carried into ``classification_scored`` because Phase 11's active learning
    ranks review candidates by *margin* — which cannot be reconstructed after the
    fact from the winner alone. ``raw_similarities`` is kept separately from the
    calibrated probabilities so a calibration change can be re-evaluated against
    old submissions without re-running the model.
    """

    category: str | None
    confidence: float
    margin: float
    alternatives: dict[str, float]
    raw_similarities: dict[str, float]
    abstained: bool
    abstain_reason: str | None = None
    #: The highest-ranked category, **whether or not it was claimed**.
    #:
    #: Carried separately from ``category`` because an abstention has a winner it
    #: declined to name, and ``alternatives`` is everything *except* that winner —
    #: so a caller reading the top of ``alternatives`` on an abstained result
    #: gets the runner-up while believing it has the winner. That is not a
    #: hypothetical: it is the defect the validation harness found on its first
    #: real run, where it turned a 70%-correct ranking into a forced-choice
    #: accuracy indistinguishable from chance, in a number nobody would have
    #: known to disbelieve.
    top_category: str | None = None

    @property
    def decided(self) -> bool:
        return self.category is not None


def score_against(
    embedding: Sequence[float],
    categories: Sequence[CategoryVectors],
    *,
    calibration: Mapping[str, Calibration],
    default: Calibration,
) -> ScoreResult:
    """Rank ``categories`` for one embedding and decide whether to claim a winner.

    ``calibration`` is looked up per category with ``default`` as the fallback,
    rather than requiring an entry per category. A tenant that adds a category
    and has not yet measured a curve for it must still be able to classify —
    Phase 9's gate says a new category is classifiable by adding prompts alone,
    and requiring a calibration row first would make that false.
    """
    if not categories:
        raise ValueError(
            "scoring was asked to rank zero categories, which can only return "
            "'no category' — a result indistinguishable from a confident model "
            "abstention. The caller must handle an empty prompt bundle instead."
        )

    pooled: dict[str, float] = {}
    raw: dict[str, float] = {}
    # Negative pools are keyed by the category they contradict and never appear
    # in the output. They exist to take mass in the softmax; naming them in
    # `alternatives` would put a phrase like "a clean dry road" in a payload
    # where every other key is a taxonomy node key.
    contrast: dict[str, float] = {}

    for entry in categories:
        settings = calibration.get(entry.category, default)
        temperature = max(settings.temperature, MIN_TEMPERATURE)

        best_positive = max(cosine(embedding, vector) for vector in entry.positives)
        raw[entry.category] = round(best_positive, 6)
        pooled[entry.category] = (best_positive + settings.bias) / temperature

        if entry.negatives:
            best_negative = max(cosine(embedding, vector) for vector in entry.negatives)
            # **The same affine transform as the positive pool, bias included.**
            # A category's negative prompts describe the same category from the
            # other side; they live on its similarity scale and nowhere else. An
            # earlier version applied the temperature here and not the bias, on
            # the reasoning that a correction should not move what it is measured
            # against — which is a coherent argument and is wrong once the bias is
            # a *centring* rather than a nudge. At a fitted temperature of 0.006
            # an uncentred contrast logit is ~140 while the centred positives sit
            # near zero, so every category's contrast takes the entire softmax
            # and the layer abstains on everything. That is what it did, on the
            # harness's first real run, and the arithmetic reason is exactly this
            # line.
            contrast[entry.category] = (best_negative + settings.bias) / temperature

    probabilities = _softmax({**pooled, **{f"\0{k}": v for k, v in contrast.items()}})
    # Drop the contrast entries after the normalisation they exist to affect.
    # The NUL prefix cannot collide with a taxonomy key: keys are validated
    # against a slug pattern by Phase 5 and cannot contain a control character.
    distribution = {key: value for key, value in probabilities.items() if not key.startswith("\0")}

    ranked = sorted(distribution.items(), key=lambda item: (-item[1], item[0]))
    winner, confidence = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
    margin = confidence - runner_up

    settings = calibration.get(winner, default)
    alternatives = {key: round(value, 6) for key, value in ranked[1:]}

    if confidence < settings.abstain_below:
        return ScoreResult(
            category=None,
            confidence=round(confidence, 6),
            margin=round(margin, 6),
            alternatives=alternatives,
            raw_similarities=raw,
            abstained=True,
            abstain_reason=(
                f"top category {winner!r} reached {confidence:.3f}, below the "
                f"{settings.abstain_below:.3f} floor its calibration sets"
            ),
            top_category=winner,
        )
    if settings.min_margin > 0.0 and margin < settings.min_margin:
        return ScoreResult(
            category=None,
            confidence=round(confidence, 6),
            margin=round(margin, 6),
            alternatives=alternatives,
            raw_similarities=raw,
            abstained=True,
            abstain_reason=(
                f"top category {winner!r} led the runner-up by {margin:.3f}, under the "
                f"{settings.min_margin:.3f} margin its calibration requires; two "
                f"categories this close is a question for a human, not a coin flip"
            ),
            top_category=winner,
        )

    return ScoreResult(
        category=winner,
        confidence=round(confidence, 6),
        margin=round(margin, 6),
        alternatives=alternatives,
        raw_similarities=raw,
        abstained=False,
        top_category=winner,
    )


def combine(
    image: ScoreResult | None,
    text: ScoreResult | None,
    *,
    image_weight: float,
) -> ScoreResult:
    """Fuse the image and text verdicts into one.

    **Fusion is over the distributions, not over the winners.** Taking the more
    confident of the two would discard the case this exists for: a photograph
    that is ambiguous between two categories and a description that clearly names
    one of them should produce that one, and neither modality alone does. A
    weighted sum of the probability distributions is the smallest rule with that
    property, and it degrades gracefully — a submission with only a photo, or
    only text, reduces to exactly that modality's result rather than to a
    half-strength version of it.

    ``image_weight`` comes from the calibration document. It is not 0.5 by
    default because the two modalities are not equally informative here: a
    citizen's own description names the problem, while a street photograph
    contains a road, a sky, and some rubbish regardless of what is being
    reported.
    """
    if image is None and text is None:
        raise ValueError("combine() needs at least one modality; the caller checked neither")
    if image is None:
        assert text is not None
        return text
    if text is None:
        return image

    weight = min(max(image_weight, 0.0), 1.0)
    keys = set(image.alternatives) | set(text.alternatives)
    keys.update(key for key in (image.category, text.category) if key is not None)
    # An abstention still carries a full distribution in `alternatives` plus its
    # own top key, so no category is lost by either side declining to decide.
    image_dist = _distribution(image)
    text_dist = _distribution(text)

    fused = {
        key: weight * image_dist.get(key, 0.0) + (1.0 - weight) * text_dist.get(key, 0.0)
        for key in keys
    }
    ranked = sorted(fused.items(), key=lambda item: (-item[1], item[0]))
    winner, confidence = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0

    return ScoreResult(
        category=winner,
        confidence=round(confidence, 6),
        margin=round(confidence - runner_up, 6),
        alternatives={key: round(value, 6) for key, value in ranked[1:]},
        raw_similarities={**text.raw_similarities, **image.raw_similarities},
        abstained=False,
        top_category=winner,
    )


@dataclass(frozen=True, slots=True)
class Decision:
    """The fused verdict plus whether the tenant's calibration permits claiming it.

    **Why this is a return value rather than an exception.** The pipeline stage
    turns an abstention into ``StageAbstainedError`` because that is what §24.2's
    degraded path is wired to; the validation harness has to *count* abstentions
    across a corpus and cannot do that against a control-flow exception without
    catching one per example. Returning the decision and letting the stage raise
    keeps one rule in one place with two callers, which is the only arrangement
    where the published F1 number is a measurement of the shipped behaviour
    rather than of a re-implementation that agrees with it today.
    """

    category: str | None
    confidence: float
    margin: float
    abstained: bool
    abstain_reason: str | None
    #: The full fused distribution, kept so a caller can report the runner-up of
    #: an abstention — which is the single most useful thing to know about one.
    fused: ScoreResult


def decide(
    image: ScoreResult | None,
    text: ScoreResult | None,
    *,
    calibration: Mapping[str, Calibration],
    default: Calibration,
    image_weight: float,
) -> Decision:
    """Fuse the modalities and apply the tenant's abstain rule to the result.

    **The abstain decision is taken on the fused result, against the winner's own
    calibration entry.** Taking it per modality would abstain on a photograph
    that is ambiguous alone and decisive alongside the description — which is the
    case fusion exists for.
    """
    fused = combine(image, text, image_weight=image_weight)

    # **A single-modality result passes through ``combine`` unchanged, abstention
    # and all, so its own reason has to survive.** Without this branch, a report
    # that abstained on the *margin* is re-reported here as having failed the
    # *floor* — because ``fused.category`` is ``None`` and the floor branch below
    # tests exactly that. The wrong message is worse than no message: "reached
    # 0.500, below the 0.150 floor" is arithmetic nobody can reconcile, and the
    # person reading it is looking at a report the system declined to route.
    if fused.abstained:
        return Decision(
            category=None,
            confidence=fused.confidence,
            margin=fused.margin,
            abstained=True,
            abstain_reason=fused.abstain_reason,
            fused=fused,
        )

    entry = calibration.get(fused.category or "", default)

    if fused.category is None or fused.confidence < entry.abstain_below:
        return Decision(
            category=None,
            confidence=fused.confidence,
            margin=fused.margin,
            abstained=True,
            abstain_reason=(
                f"the best category reached {fused.confidence:.3f}, below the "
                f"{entry.abstain_below:.3f} floor this tenant's approved calibration sets. "
                f"Parking the report for a human is the designed outcome, not a failure"
            ),
            fused=fused,
        )
    if entry.min_margin > 0.0 and fused.margin < entry.min_margin:
        return Decision(
            category=None,
            confidence=fused.confidence,
            margin=fused.margin,
            abstained=True,
            abstain_reason=(
                f"{fused.category!r} led the runner-up by {fused.margin:.3f}, under the "
                f"{entry.min_margin:.3f} margin required. Two categories this close is a "
                f"question for a human, not a coin flip"
            ),
            fused=fused,
        )
    return Decision(
        category=fused.category,
        confidence=fused.confidence,
        margin=fused.margin,
        abstained=False,
        abstain_reason=None,
        fused=fused,
    )


def _distribution(result: ScoreResult) -> dict[str, float]:
    """A result's full probability map, winner included."""
    full = dict(result.alternatives)
    if result.category is not None:
        full[result.category] = result.confidence
    elif result.raw_similarities:
        # An abstention has no `category`, but its top entry is still part of the
        # distribution and dropping it would let the *other* modality win a
        # fusion by default rather than on evidence.
        top = max(result.raw_similarities, key=lambda key: result.raw_similarities[key])
        full.setdefault(top, result.confidence)
    return full


def _softmax(logits: Mapping[str, float]) -> dict[str, float]:
    """Numerically stable softmax.

    The maximum is subtracted before exponentiating — with a temperature of 0.01
    a cosine of 0.3 becomes a logit of 30, and ``exp(30)`` is fine while a
    slightly colder temperature overflows to ``inf`` and produces NaNs that
    propagate silently into a confidence field the event log will keep forever.
    """
    if not logits:
        return {}
    highest = max(logits.values())
    exponentials = {key: math.exp(value - highest) for key, value in logits.items()}
    total = sum(exponentials.values())
    if total <= 0.0 or not math.isfinite(total):  # pragma: no cover - guarded above
        raise ValueError("softmax denominator is not finite; the logits are unusable")
    return {key: value / total for key, value in exponentials.items()}


__all__ = [
    "MIN_TEMPERATURE",
    "Calibration",
    "CategoryVectors",
    "Decision",
    "ScoreResult",
    "combine",
    "decide",
    "score_against",
]
