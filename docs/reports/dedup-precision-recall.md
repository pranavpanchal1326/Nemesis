# Deduplication — precision, recall and the false-merge count

**Generated:** 2026-08-24T07:16:34.137028+00:00 · **Phase:** 10 · **Owner:** DATA  
**Reproduce:** `nem dedup-eval`  
**Raw data:** [`dedup-precision-recall.json`](dedup-precision-recall.json)

Phase 10's gate asks for measured precision and recall against a labelled fixture set of true-duplicate and true-distinct pairs, and for **zero false-positive merges**. This is that measurement. It is produced by `nemesis.dedup.harness`, which calls `engine.evaluate` — the same function the pipeline stage calls, against a real PostGIS and a real pgvector — so the table below describes shipped behaviour rather than a re-implementation of it.

---

## What was measured

| | |
|---|---|
| Corpus | `municipality-dedup-v1` (8fdea61e9ede) |
| Incidents | 11 |
| Reports | 24 |
| Modality | `text` |
| Encoder | `sentence_transformers:intfloat/multilingual-e5-small` |
| Policy | `baseline` |

Labelled duplicate and distinct report pairs for the `municipality` tenant template, expressed as incidents with multiple citizen reports. Two reports of the same incident are a true duplicate; two reports of different incidents are truly distinct, however close together they sit. Authored by the NEMESIS team in citizen voice, deliberately without reference to each other's wording: the point of a dedup corpus is that two people describe one problem differently, so a corpus whose duplicates are paraphrases of one sentence measures paraphrase detection instead.

## Result

| Metric | Value |
|---|---:|
| **Precision** | **0.600** |
| **Recall** | **0.600** |
| F1 | 0.600 |
| **False-positive merges** | **4** |
| True positives | 6 |
| False negatives | 4 |
| True negatives | 10 |
| Sent to the ambiguous band | 4 |

**Precision is the gate's number and recall is its cost.** §14.3 makes the two errors incomparable: a false merge tells a citizen their problem is already being handled when it is not, while a missed merge costs an operator the time to reconcile two work orders. The engine is tuned so the error it makes is the second one, and a recall below precision is that tuning working rather than a defect.

### False merges — the gate has failed

| Report | Merged with reports of |
|---|---|
| `pothole-jm-road-r1` | pothole-fc-road-r1, pothole-fc-road-r2 |
| `pothole-fc-road-r3` | pothole-jm-road-r1 |
| `pothole-jm-road-r2` | pothole-fc-road-r1, pothole-fc-road-r2, pothole-fc-road-r3 |
| `pothole-fc-road-r4` | pothole-jm-road-r1, pothole-jm-road-r2 |

**Roughly 1 root error and 3 cascades.** Once a cluster holds two incidents, every later report of either finds a cluster that already contains both and merges into it correctly by its own lights. This is the mechanism §14.3 is about: a false merge is not one wrong decision, it is a permanently contaminated cluster that makes every subsequent decision wrong for free. Cascades are counted separately on purpose — the citizen whose fourth report vanished into the wrong incident is no less suppressed for the error having been made earlier.

### Could any threshold have separated them?

| | Combined confidence |
|---|---|
| True-duplicate merges | 0.8535, 0.8608, 0.8695, 0.8765, 0.8776, 0.8781 |
| False merges | 0.8661, 0.8672, 0.8777, 0.8835 |

The classes **interleave**: the highest true duplicate (0.8781) sits above the lowest false merge (0.8661). **No value of `merge_threshold` separates them.** Raising it to exclude the false merges excludes most of the true ones, and the gate would then be met by a system that deduplicates nothing.

That is a statement about the modality, not about the thresholds. Two citizens describing two different potholes on one street write nearly the same sentence, because the sentence is nearly the same. The text encoder compresses same-domain civic complaints into a narrow similarity band, and inside that band the distance between *the same defect* and *another defect of the same kind nearby* is smaller than the noise.

**Two remedies exist, and neither is applied in this pass.**

1. **The image modality.** Two photographs of two different potholes are not nearly the same image. This is the signal that separates the classes, and it is the one Phase 9 shipped unmeasured while naming Phase 10 as where the photo corpus had to arrive. It is still absent.
2. **A tighter radius for point defects.** `DedupBand` is per-category precisely so a pothole and a flooded junction can have different radii, and the baseline currently gives them the same 50 m. A pothole is a metre across; fifty metres of road holds many of them.

Neither is applied because **this corpus has no held-out split.** Tuning against the only measurement available produces a number describing the tuning, which is the mistake Phase 9's F1 report documents at length and declines to repeat. The corpus needs a calibration split before either remedy can be adopted and honestly re-measured.

### Missed merges

Reports whose incident was already known and which did not join it.

| Report | Incident | Outcome | Confidence | Candidates |
|---|---|---|---:|---:|
| `streetlight-baner-r2` | `streetlight-baner` | investigate | 0.841 | 1 |
| `garbage-model-colony-r2` | `garbage-model-colony` | investigate | 0.824 | 1 |
| `cable-camp-r2` | `cable-camp` | investigate | 0.822 | 1 |
| `garbage-model-colony-r3` | `garbage-model-colony` | investigate | 0.859 | 2 |

## Latency (§27.1)

| Operation | n | p95 | Budget |
|---|---:|---:|---:|
| `evaluate` | 24 | 25.9 ms | 10000 ms |

Stage 1 and Stage 2 end to end, against a database holding the corpus. It excludes the encoder, which §27.1 budgets under classification and Phase 9 measures there.

## What this does not establish

- **The image modality is not measured.** Phase 9's F1 report carried this forward and named Phase 10 as where the photo corpus had to arrive; it has not. There is still no licence-clean set of photographed civic defects in this repository, and rendering synthetic street scenes would measure the renderer. So `image_weight` ships unmeasured, every number here is the text side alone, and the carried-forward gap is now carried forward again — stated here rather than quietly dropped, and it is the most significant limitation on this page.
- **The corpus is authored, not field data.** The reports are written in citizen voice and deliberately not paraphrased from each other, but they were still written by people who knew which incident each belonged to. A real intake queue carries misspellings, code-switching, and reports that name two defects at once.
- **One label encodes a policy decision rather than a physical fact.** The stale pothole re-reported outside its time window is counted as distinct because a fixed defect that reopens is a new work order, not an addition to a closed one. That is a defensible rule and it is a rule, not an observation.
- **The corpus is small.** A handful of incidents means one report moving changes recall by a visible fraction. Read precision and the false-merge count as the gate; read recall as an indication.
- **Zero false merges on this corpus is not zero false merges in production.** It is the strongest claim a fixture set can support, and the measurement that matters next is `nemesis_dedup_merge_reversions_total` — operators undoing merges is the real false-positive rate.
