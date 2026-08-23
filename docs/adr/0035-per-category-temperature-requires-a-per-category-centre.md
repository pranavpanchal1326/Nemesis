# ADR-0035 — A per-category temperature requires a per-category centre, in similarity space

**Status:** Accepted
**Date:** 2026-08-23
**Phase:** 9 — Perception layer & model registry
**Owning function:** DATA

## Context

Zero-shot classification here is a softmax over cosine similarities between one
embedding and every category's prompts. Raw similarities live in a narrow band —
roughly 0.15–0.35 for CLIP ViT-B-32, and much narrower for multilingual-e5, whose
cosines on real complaint text cluster around 0.85 with in-class/out-of-class
gaps of about 0.02. An un-temperatured softmax over numbers that close returns
near-uniform probabilities: 0.115 for the right answer and 0.112 for the wrong
one, which is useless as a confidence and worse than useless as a routing
threshold.

So the calibration document carries a **temperature per category**, and the
argument for per-category rather than global is sound: "a pothole in a road" and
"an overflowing garbage bin" sit at different places in the model's similarity
band, and one temperature makes one category systematically over-confident and
the other systematically under-confident.

The document also carried a `bias`, specified as *additive on the logit, after
temperature* — Platt scaling's shape. That specification was written before
anything fitted it, and it was wrong in a way that only a fit could reveal.

**A softmax is invariant to a shift applied to every logit, and to nothing
less.** With one global temperature, every category's logit is `cos / T` and the
absolute magnitude cancels — an offset would be decoration. The moment two
categories divide by *different* temperatures, their logits are on different
scales: a category at `T = 0.004` produces logits around 212 while one at
`T = 0.008` produces 106, and the first wins every comparison on arithmetic
rather than on evidence. A per-category temperature is only meaningful alongside
a per-category centre.

Fitting one exposed the second half of the problem. Centring in *logit* space
means `bias = -mean_negative / T`, which for e5 is about −140 — outside the
document's `[-10, 10]` bound, and a number no approver can sanity-check against
anything. And `score_against` applied the temperature to a category's negative
(contrast) pool but deliberately *not* the bias, on the reasoning that a
correction should not move the thing it is measured against. That reasoning is
coherent for a small nudge and catastrophic for a centring: the contrast logit
stayed near 140 while the centred positives sat near zero, so every category's
contrast entry took the entire softmax and the layer abstained on **100%** of a
corpus it was ranking 70% correctly.

## Decision

**`logit = (cosine + bias) / temperature`, with the same affine transform applied
to a category's positive and negative pools.**

Three parts, and each fixes one of the failures above:

1. **The offset is applied before the temperature**, which makes it the
   per-category centre a per-category temperature needs. This is what puts the
   categories back on a common scale.

2. **It is expressed in similarity units, not logit units.** A cosine lives in
   `[-1, 1]`, so the document's `[-10, 10]` bound is generous and meaningful, and
   the value an approver reads (`-0.81`) can be checked directly against the
   `mean_negative_similarity` column printed beside it. The same centring as a
   logit offset is in the hundreds and no bound on it would mean anything.

3. **A category's negative pool receives its category's temperature *and* bias.**
   The negatives describe the same category from the other side; they live on its
   similarity scale and nowhere else.

**The harness fits temperature and centre per category and the abstain floor
once for the tenant.** Temperature maps the measured in-class/out-of-class gap
onto a fixed logit separation; the centre is the measured out-of-class mean; the
floor is a quantile over every correct calibration win, pooled, because a
quantile over one category's six wins is not a measurement. Every entry's
`provenance` says which of the three was fitted per category and which was
shared.

**What this is not, recorded in the document and in the report:** it is scale
normalisation plus an operating point, not a calibrated posterior. A true
calibration fits against observed correctness with a proper scoring rule and
needs far more labelled data than a bootstrap corpus has. A confidence of 0.7
from this system does not mean seven in ten, and an approver told otherwise would
reasonably read it that way.

## Consequences

**`bias` changed meaning, and the change is a schema change to a governed
document.** It is safe to make now only because `perception_calibration` is
introduced by this phase and no tenant has an approved revision. Making it later
would have required a policy migration and an upcaster, for a field whose old
values were unusable anyway.

**The contrast pool's behaviour is now correctly described, and it is weaker than
the original docstring claimed.** A softmax denominator is shared, so a negative
that matches strongly suppresses *every* category's confidence, not only its
owner's. It does the job it exists for — stopping a report that matches nothing
from being assigned the least-bad category at a credible-looking confidence — and
it does not selectively penalise its own category. A per-category contrast would;
it is a real alternative, it is not what ships, and the two have not been measured
against each other. That is now stated in `scoring` rather than implied.

**The defaults still abstain more than they classify.** With the corrected
arithmetic and no approved document, the `municipality` template's nine
categories score macro F1 0.114 at coverage 0.07, against 0.595 at 0.72 with the
fitted curves. The shipped `default_abstain_below` was lowered from 0.35 to 0.15
on the evidence — above the 1/9 ≈ 0.111 a nine-way coin flip reaches, below the
0.164 the fit lands on — which turns "classifies nothing" into "classifies
badly", and the real fix is a fitted document per tenant. A default floor is
inherently taxonomy-size dependent and no constant makes it otherwise.

## Alternatives considered

**Widen the `bias` bound to ±1000 and keep it in logit space.** Preserves the
original specification and destroys the field's reviewability: the bound stops
being a check and the value stops being something a human can reconcile with the
evidence printed next to it.

**Add a separate `centre` field and leave `bias` as a logit offset.** Two knobs
that do the same thing, one of which is always zero in practice, on a document
somebody has to approve.

**Drop the per-category temperature and use one global one.** Removes the whole
problem and returns the failure it was introduced for: one category that never
reaches its abstain floor and another that never leaves it, with the two errors
not cancelling.

**Subtract the negative from the positive instead of entering it in the
softmax.** The classic zero-shot contrast. Rejected here as out of scope rather
than as wrong — it changes what a confidence *means*, it would need re-measuring
end to end, and Phase 9 has one measurement instrument and no comparative
evidence. Recorded in `scoring`'s docstring as an unmeasured alternative rather
than dismissed.
