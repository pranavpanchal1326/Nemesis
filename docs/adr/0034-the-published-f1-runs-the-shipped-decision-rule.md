# ADR-0034 — The published F1 runs the shipped decision rule, on a split the corpus computes

**Status:** Accepted
**Date:** 2026-08-23
**Phase:** 9 — Perception layer & model registry
**Owning function:** DATA

## Context

Phase 9's gate is "a **published per-category F1 number** in the repo,
reproducible by one command". That sentence is easy to satisfy dishonestly, and
the ways it can be satisfied dishonestly are not exotic — they are the default
outcome of writing a validation harness the obvious way:

- **The harness re-implements the decision.** Fusing two modalities, applying a
  temperature and deciding whether the winner clears its abstain floor are
  fifteen lines, and a harness that writes its own copy agrees with production on
  the day it is written. The drift afterwards is one-directional: a harness is
  simpler than a pipeline, so the copy is the one that works, and the published
  number is *better* than reality.
- **The split moves.** A held-out set taken as `examples[:n]` inherits the order
  the file was written in, and the author wrote the easy ones first. A split
  keyed on a shuffle seed reshuffles when anybody adds a sentence, so two
  consecutive numbers differ for a reason nothing recorded.
- **The abstain floor is fitted on the set it is measured on.** This produces
  numbers in the high nineties and means precisely nothing.
- **Prompts are rewritten against the held-out confusions.** The most natural
  mistake here, because the held-out confusion table is the most useful-looking
  thing in the report. It silently converts the held-out set into a development
  set, and the re-measured number then reports how well the prompts were tuned to
  the examples they were tuned on.
- **The number is measured against a deterministic fake.** It has exactly the
  same shape as a real one.

None of these produce a wrong-looking artefact. All of them produce a *flattering*
one, which is worse than no artefact, because it is quotable.

## Decision

**One decision rule, two callers, and a corpus that computes its own split.**

1. **`scoring.decide` is the rule, and both the pipeline stage and the harness
   call it.** The stage turns an abstention into `StageAbstainedError` because
   that is what §24.2's degraded path is wired to; the harness counts
   abstentions and cannot do that against a control-flow exception. So the rule
   returns a `Decision` and the *stage* raises. There is no second copy to
   drift.

2. **The split is `Corpus.split()`, stratified by (category, locale), ordered
   within a stratum by `sha256(corpus_id + example_id)`.** No seed — a seed is
   one more thing that must travel with the report for the number to be
   reproducible, and it is the thing most likely not to. Adding an example to one
   stratum cannot move another stratum's assignment, so two reports are
   comparable.

3. **Calibration curves are fitted on the calibration third and never on the
   held-out two thirds.** The report publishes both the fitted number and the
   *baseline* at the tenant template's document defaults, so "the fit was worth
   doing" is a measurement rather than a preference.

4. **The §43.2 prompt-pass work list is computed on the calibration split**, and
   the report says in words that the held-out confusion table is published for
   the reader and is not the work list. The calibration split is already spent;
   acting on it leaves the held-out number measuring examples nothing has been
   tuned against.

5. **There is no `--fake` flag on the runner.** Deterministic encoders are the
   right instrument for asserting the scoring *rule* and the test suite uses them
   for exactly that. The report records `model_ids` for every run, and
   `gate_phase9.py` refuses a report whose model ids do not name the checkpoints
   `docs/MODELS.md` declares.

6. **An abstention is a false negative for the true category and a false
   positive for nobody**, because §24.2 sends the report to a human rather than
   to the wrong department. That treatment is gameable — raise every floor and
   precision goes to 1.0 while the system classifies nothing — so every table
   carries **coverage** and a **forced** F1 computed with abstention disabled.
   The counting rule is only safe because the two columns beside it make the
   trade visible.

## Consequences

**The published number is low, and it ships.** Macro F1 0.595 with four of nine
categories under the 65% floor. The gate does not require the number to be good;
it requires the number to be published, reproducible, and accompanied by the
prompt-pass record whenever a category is below the floor. A gate that failed on
the value would delete the incentive to publish an inconvenient one — which is
the failure this whole ADR is about, arriving through the door marked "quality
bar".

**The harness found two defects in the shipped scorer on its first real run**,
which is the strongest available argument that calling the real rule was the
right choice. Both are recorded in `scoring` at the line that caused them:
`ScoreResult.top_category` did not exist, so a caller reading the top of
`alternatives` on an abstained result got the runner-up; and the negative pool
did not receive its category's bias, so at a fitted temperature the contrast
logit sat ~140 above the centred positives and took the entire softmax. Neither
would have been visible in a report the harness generated for itself.

**A prompt author is slower.** The useful table — the held-out confusions — is
deliberately not the one they work from, and the calibration-split work list is
smaller and noisier. That is the cost of the number meaning anything, and it is
paid by the person best placed to understand why.

**Per-category floors are not fitted, and the document still supports them.** On
a corpus this size a quantile over a category's six correct calibration wins is
noise with three decimal places; measured, it swung between 0.095 and 0.773 and
took two categories from a usable ranking to a held-out F1 of exactly zero. The
fit therefore derives one shared operating point and says so in the `provenance`
on every entry, so an approver is not told a per-category measurement was made
when it was not. Phase 11's labelling loop is what makes per-category floors
honest.

## Alternatives considered

**Let the harness call the encoders and do its own arithmetic.** Simpler, faster
to write, and the version that has been shipped by everybody who later discovered
their offline metric did not predict their online one.

**Publish only the forced-choice number and drop abstention from the report.**
Removes the gaming surface entirely and measures a system that does not exist:
this one abstains, and a number that pretends otherwise describes a different
product.

**Require ≥65% F1 to pass the gate.** Rejected for the reason in the
consequences: it converts an instrument for finding out into an obstacle to be
managed, and the first thing managed would be the corpus.

**Grow the corpus until the per-category numbers are stable.** Correct, and not
an alternative to any of the above — it is the next piece of work, and the report
says so in the caveat about nine held-out examples per category.
