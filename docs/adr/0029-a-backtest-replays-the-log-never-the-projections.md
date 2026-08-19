# ADR-0029 — A backtest replays the log, never the projections

**Status:** Accepted
**Date:** 2026-08-19
**Phase:** 7 — Configuration simulation & backtesting
**Owning function:** DATA

## Context

A backtest answers *"what would this candidate policy have done to last year?"*
To answer it you need, for every historical complaint, the inputs the pipeline
had when it decided. There are two places to get them, and only one of them is
correct.

The `complaints` table is right there. It has the category, the trust score, the
severity breakdown, the cluster. It is one query, it is indexed, and it is
**current state** — a projection that every policy change since has already
rewritten.

That is the trap, and it is a quiet one. A backtest built on the projection
compares a candidate against *the accumulated results of every change made since
the window closed*. Its most likely output is "no complaints affected", because
the projection has already absorbed the effect being measured. And "no
complaints affected" is exactly the answer somebody looking for permission to
activate is hoping to see.

## Decision

**A `DecisionCase` is folded from the complaint's own event chain, through
production's own projectors, and only *observations* are read out of the result.**

The mechanism:

- `simulation.corpus` batch-reads every event for the selected complaints and
  folds each chain with `projections.registry.project` — the same function the
  pipeline uses. A corpus builder that reimplemented the fold would be measuring
  its reimplementation.
- Out of the folded state it takes an **allow-list** of observation fields:
  `reported_at`, `category`, `description_text`, `locale`, `trust_score`, and
  `severity_breakdown.components`. Never `severity_score`, `severity_policy_version`,
  `is_safety_flagged`, `cluster_id`, or `department_id`.
- Snapshots are deliberately not used. They are keyed to `PROJECTOR_VERSION` and
  exist to make a hot read cheap; a backtest is a cold batch, and folding from
  sequence 1 removes "the snapshot was written by a different build" from a
  report somebody is about to make a decision on.

The line is **observation versus decision**, and it is worth stating exactly.
An observation is what a stage *measured*: the CLIP confidence, the EXIF
distance, the encoder's cosine similarity, the component values in
`severity_scored.components`. None of these move when a policy changes, which is
what makes them a fair input to a replay. A decision is what a policy
*concluded*: the score, the tier, the SLA, the department, the merge. Every one
of those is the thing under test.

Two named constants carry the distinction — `OBSERVATION_KEYS` and
`DECISION_KEYS` — and a test asserts that `DecisionCase` declares no field from
the second set. A deny-list was rejected: it would let the next field added to
the projection default into the corpus.

Two consequences follow and are accepted rather than hidden:

**The dedup candidate comes from `cluster_match_found`, not from re-embedding.**
Phase 3 shaped that payload to record both stage similarities and the geo
distance for exactly this purpose. Re-running the encoder over a year of
photographs would fold *model* drift into a report about a *policy* change, and
the two would be indistinguishable in the output.

**The lineage is resolved against today's taxonomy.** The classifier's output is
an observation; where that key sits in the tree today is not. Re-parenting last
year's complaints under this year's tree would measure two changes at once. The
corpus reports which categories no longer resolve rather than dropping them.

## Consequences

**Good**

- The report measures the candidate and nothing else.
- The corpus is reproducible: the same window twice produces the same cases,
  because event history does not change.
- A complaint whose category was later corrected by an operator still replays
  under the category the classifier actually assigned, which is what the pipeline
  saw.

**Costs, accepted**

- It is much more expensive than one indexed query. A twelve-month window is
  hundreds of thousands of chains, which is why `DEFAULT_MAX_CASES` bounds a run
  and why the excess is **systematically sampled** across submission order rather
  than truncated to the most recent N — truncation would silently turn "twelve
  months" into "three weeks", and seasonality is the largest source of variation
  in a civic complaint stream.
- Three declared routing facts have no source in the log yet (`zone_code`,
  `tags`, and the visual half of the safety ruleset). They are listed in
  `UNAVAILABLE_FACTS` and a candidate rule referencing one is reported as a
  **coverage gap** rather than backtested as inert — see ADR-0030.
- `submitted_via` is read from the raw `complaint_submitted` payload rather than
  from the projection, which legitimately drops it. Changing the projector would
  bump `PROJECTOR_VERSION` and invalidate every snapshot in the system to serve
  a batch job.

## Alternatives considered

**Read the `complaints` projection.** Rejected — the whole subject of this ADR.

**Reuse the decisions production actually made as the baseline column.**
Tempting: it costs nothing and it is "what really happened". Rejected because
those decisions were made by a pipeline that has since changed in ways unrelated
to policy, so every one of those changes would land in the candidate's column.
Both bundles are run over the same cases instead, so a difference in the output
is a difference in the policy and nothing else.

**Replay through `replay_entity` per complaint.** Correct, and twenty thousand
round trips. The batch read folds the same events with the same function.

## References

- `nemesis/simulation/corpus.py` — `OBSERVATION_KEYS`, `DECISION_KEYS`, `UNAVAILABLE_FACTS`
- `nemesis/simulation/engine.py` — the pure decision, calling production's arithmetic
- `nemesis/projections/registry.py` — the fold
- ADR-0030 — what the report does about facts it cannot supply
