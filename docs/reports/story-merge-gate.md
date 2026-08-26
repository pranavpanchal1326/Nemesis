# Act 6's merge — the one gate clause this checkout cannot take, and why

**Date:** 2026-08-26 · **Phase:** 20 (Track E, F14) · **Owner:** PROD
**Reproduce:** `nem up`, `nem seed-demo`, then
`npx playwright test tests/story.spec.ts -g "cluster_match_found"`.

Phase 20's exit gate opens with the sentence the whole film is built against:

> **Every scene is triggered by a genuine backend event in an E2E test. A scene
> that can only be fired by a button fails.**

Nine of the film's nine acts satisfy it. **One clause of it — Act 6's merge —
is written, wired and unexercised on this machine**, and this report is that
statement made in numbers rather than left as a green suite with a hole in it
(Rule 7: a skipped gate is technical debt with a false receipt).

---

## What is asserted, and what is skipped

`tests/story.spec.ts` takes the gate in two halves, and the first half is the
one that actually protects the claim:

| Assertion | State |
|---|---|
| Act 6 renders **nothing** until a real `cluster_match_found` arrives — no stamp, no rings, `data-merge="waiting"` | ✅ asserted |
| Act 5's five gates are stamped from a complaint's own ledger, filed through the film's own Act 4 | ✅ asserted |
| Act 4 mounts the citizen loop's **own** `<ReportFlow>`, compared render against render | ✅ asserted |
| Act 6 stamps the merge event's own report count, confidence, distance and timestamp | ⏭️ **skipped — see below** |

The skip is conditional and it decides itself from the ledger, not from a
constant: the test files two reports through Act 4, polls
`GET /complaints/{id}/events`, and takes the gate if `cluster_match_found` is
there. It skips **by name** only when the pipeline parked the report first.

## Why it parks

The pipeline is behaving correctly. `frontend/tests/fixtures/media/pothole.jpg`
is procedurally drawn (`scripts/demo_imagery.py`, fixed seed) rather than
photographed, and CLIP does not recognise it well enough to clear the floor its
own calibration sets:

```
pipeline_stage_abstained  stage=classification
  reason="top category 'roads.pothole' reached 0.142, below the 0.150 floor its
          calibration sets"
  note='the stage declined to answer; the report is parked for a human'
pipeline_stage_degraded   stage=classification  fallback=pending_classification
```

0.142 against a 0.150 floor — **eight thousandths short.** §24.2's third
outcome fires, the report parks at `pending_classification`, and deduplication
is never reached. No cluster is formed, so no `cluster_match_found` is ever
published, so there is no merge for Act 6 to render.

This is Phase 9 doing the thing Phase 9 was built to do. It published four
categories below its F1 floor rather than tuning them (`docs/reports/perception-f1.md`),
and the abstention here is the same discipline one layer down: the classifier
declines to guess, and the film declines to invent a merge it was never told
about.

## What this is not

**It is not a defect in Act 6, and it is not an untested code path.** The act's
reducer is asserted against envelopes shaped exactly as
`nemesis/realtime/envelope.py` shapes them — `new_confidence`, `report_count`,
`geo_distance_meters`, the event's own timestamp — including the cases where a
field legitimately does not arrive (`tests/story-live.test.ts`, seven
assertions). What is unexercised is the last hop: a real socket frame reaching a
real browser and moving `data-merge` from `waiting` to `live`.

**It is also not fixable by pushing an envelope onto the bus from the test.** A
test that published a synthetic `cluster_match_found` would be firing the scene
itself — a button in a costume — and would pass the gate by violating it. The
gate's own words are the reason the skip exists instead.

## What would take it

Any one of these, in order of honesty:

1. **A photographed fixture the classifier recognises.** The cheapest and the
   most faithful: two real photographs of one pothole, committed beside
   `pothole.jpg` with the same README discipline. This is the recommended fix
   and it belongs to whoever next touches `tests/fixtures/media/`.
2. **A demo seed that plants a cluster.** `nem seed-demo` could publish a
   two-report cluster on the demo tenant, which would let the film's Act 6 be
   exercised without a classifier at all. Cheap, but it moves the assertion one
   step away from the citizen path the film actually walks.
3. **A calibration change.** Rejected. Tuning a threshold against the only
   corpus you have produces a number about the tuning (Rule 8), and doing it to
   make a landing page's animation play would be the worst possible reason.

## The state of the rest of the Phase 20 gate

| Clause | State |
|---|---|
| Every scene fired by a genuine backend event | ✅ except the merge, above |
| Every fallback tier exercised by forcing its trigger | ✅ Tier C by `prefers-reduced-motion` **and** by `?tier=C`; Tier D with JavaScript disabled |
| Golden-image regression per scene at fixed seed and camera | ✅ ten baselines — nine acts and the storyboard |
| Frame budget held with all effects enabled | ⚠️ inherited from Phase 19 — `docs/reports/clay-frame-rate.md` holds the measured number and the recorded deviation |
| Tier C prints reviewed as a design deliverable | ✅ nine frames plus the receipts, drawn rather than captured, with their own baseline |
