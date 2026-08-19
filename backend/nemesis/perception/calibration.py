"""The governed calibration document, flattened into what the scorer reads.

Twenty lines of translation, kept in their own module for one reason: it is the
seam between *policy* and *arithmetic*, and both sides need to be usable without
the other. ``scoring`` must be importable by the validation harness, which runs
outside any tenant and has no policy document; ``documents`` must stay free of
anything that knows what a softmax is. This file is the only place that knows
both.

**Calibration does not inherit down the taxonomy, deliberately.** Severity
overrides walk the tree — an override on ``electrical`` covers every child a
tenant adds later — and the temptation to do the same here is strong and wrong.
A severity override is a *judgement* ("live cables are dangerous"), which
genuinely generalises to children. A calibration entry is a *measurement* of how
one category's prompts sit in one model's similarity space, fitted on that
category's own examples. A child category's prompts are different text and land
somewhere else, so inheriting the parent's temperature would apply a number
measured on evidence that has nothing to do with it — and it would do so
invisibly, because the resulting confidences look entirely plausible.

So an unlisted category gets the tenant's declared defaults, which are honest
about being defaults, and ``sample_size`` on the entries that exist tells an
approver which numbers are actually backed by anything.
"""

from __future__ import annotations

from collections.abc import Mapping

from nemesis.perception.scoring import Calibration
from nemesis.policy.documents import PerceptionCalibration


def default_of(document: PerceptionCalibration) -> Calibration:
    """The tenant's fallback curve, for every category with no measured entry."""
    return Calibration(
        temperature=document.default_temperature,
        bias=0.0,
        abstain_below=document.default_abstain_below,
        min_margin=document.default_min_margin,
    )


def per_category(document: PerceptionCalibration) -> Mapping[str, Calibration]:
    """Category key → measured curve, for the categories that have one.

    Returns only what the document states. A caller that wants "the curve for
    category X, whatever it is" asks this mapping and falls back to
    ``default_of`` — which is exactly what ``scoring.score_against`` does, and
    why it takes both rather than a pre-merged dict: merging here would require
    knowing the taxonomy, and this module deliberately does not.
    """
    return {
        entry.category: Calibration(
            temperature=entry.temperature,
            bias=entry.bias,
            abstain_below=entry.abstain_below,
            min_margin=entry.min_margin,
        )
        for entry in document.categories
    }


def measured_categories(document: PerceptionCalibration) -> tuple[str, ...]:
    """Categories whose curve was fitted on at least one labelled example.

    Used by the review surface and by the harness report to distinguish "this
    tenant has calibrated three of its nine categories" from "this tenant has
    nine entries, six of which were typed in by hand". An entry with
    ``sample_size == 0`` is a manual override, which is legitimate — somebody may
    know a category is noisy before there is data — but it is not a measurement
    and the two must not be counted together.
    """
    return tuple(sorted(entry.category for entry in document.categories if entry.sample_size > 0))


__all__ = ["default_of", "measured_categories", "per_category"]
