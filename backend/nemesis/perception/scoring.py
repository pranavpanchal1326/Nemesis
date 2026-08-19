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
one it never competed with. Entering them into the same softmax makes them do
what they are for — take probability mass away from the category they contradict,
and from nothing else.

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
        pooled[entry.category] = best_positive / temperature + settings.bias

        if entry.negatives:
            best_negative = max(cosine(embedding, vector) for vector in entry.negatives)
            # The negative pool uses the same temperature and *no* bias. Bias is
            # a correction on a category's measured curve; applying it to the
            # contrast pool would move the thing the correction is measured
            # against, which makes the calibration self-referential.
            contrast[entry.category] = best_negative / temperature

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
        )

    return ScoreResult(
        category=winner,
        confidence=round(confidence, 6),
        margin=round(margin, 6),
        alternatives=alternatives,
        raw_similarities=raw,
        abstained=False,
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
    "ScoreResult",
    "combine",
    "score_against",
]
