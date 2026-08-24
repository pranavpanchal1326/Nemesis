# Perception layer — per-category precision, recall and F1

**Generated:** 2026-08-24T04:18:10+00:00 · **Phase:** 9 · **Owner:** DATA  
**Reproduce:** `nem f1`  
**Raw data:** [`perception-f1.json`](perception-f1.json) · **Proposed calibration:** [`perception-calibration-proposed.json`](perception-calibration-proposed.json)

Phase 9's gate is *a published per-category F1 number in the repo, reproducible by one command*. This is that number. It is measured by `nemesis.perception.harness`, which calls `scoring.decide` — the same function the pipeline stage calls — so the table below describes shipped behaviour rather than a re-implementation of it.

---

## What was measured

| | |
|---|---|
| Corpus | `municipality-v1` (bfe038141079) |
| Held-out examples | 72 |
| Calibration examples | 54 |
| Modality | `text` |
| Models | `sentence_transformers:intfloat/multilingual-e5-small` |
| Calibration | fitted on the 54-example calibration split of municipality-v1 |

Citizen-voice complaint descriptions for the nine selectable categories of the `municipality` tenant template, in the three locales that template declares. Authored by the NEMESIS team from the defect vocabulary in Blueprint §8, deliberately without reference to the prompt sets they are scored against — a corpus paraphrased from its own prompts measures the paraphrase. Text only: no field photographs exist yet, so the image modality is unmeasured and the report says so rather than substituting rendered pixels for evidence.

## Result

| Category | Support | Precision | Recall | **F1** | Coverage | Abstained | Forced F1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| `dead_animal` | 8 | 0.500 | 0.625 | **0.556** ⚠ | 0.62 | 3 | 0.700 |
| `exposed_cable` | 8 | 1.000 | 0.875 | **0.933** | 1.00 | 0 | 0.933 |
| `footpath_damage` | 8 | 1.000 | 0.375 | **0.545** ⚠ | 0.50 | 4 | 0.769 |
| `garbage_pile` | 8 | 0.714 | 0.625 | **0.667** | 1.00 | 0 | 0.625 |
| `open_manhole` | 8 | 0.800 | 0.500 | **0.615** ⚠ | 0.88 | 1 | 0.588 |
| `pothole` | 8 | 0.714 | 0.625 | **0.667** | 0.62 | 3 | 0.700 |
| `streetlight_out` | 8 | 1.000 | 0.500 | **0.667** | 0.62 | 3 | 0.857 |
| `water_leak` | 8 | 0.667 | 0.750 | **0.706** | 0.75 | 2 | 0.667 |
| `waterlogging` | 8 | 0.000 | 0.000 | **0.000** ⚠ | 0.50 | 4 | 0.364 |

**Macro F1 0.595** · micro F1 0.629 · coverage 0.72 · forced macro F1 0.689

At the tenant template's **document defaults**, the same held-out examples score macro F1 0.114 at coverage 0.07. The fitted curves are what the difference buys, and they are a proposal for an approver rather than a deployment.

### Reading the columns

**Coverage** is the share of a category's held-out examples that got any answer at all. It is beside F1 in every row because an abstention is counted as a false negative and never as a false positive — which is correct, since §24.2 sends the report to a human rather than to the wrong department, and which is also gameable: raise every abstain floor and precision goes to 1.0 while the system classifies nothing. **Forced F1** is the same model judged with abstention disabled. A row where F1 is high, coverage is low, and forced F1 is much lower is a category the system is declining to answer rather than answering well.

### Per locale

| Locale | Macro F1 |
|---|---:|
| `en` | 0.638 |
| `hi` | 0.630 |
| `mr` | 0.385 |

Reported separately because §8.4's promise is that a complaint in the citizen's own language works, and a single number over a mixed-language corpus hides a language that does not. Hindi and Marathi are the rows ADR-0003 chose multilingual-e5 for, and the ones nobody would notice were broken.

## Where it goes wrong

| Truth | Called | Count |
|---|---|---:|
| `open_manhole` | `dead_animal` | 2 |
| `waterlogging` | `water_leak` | 2 |
| `exposed_cable` | `garbage_pile` | 1 |
| `footpath_damage` | `pothole` | 1 |
| `garbage_pile` | `dead_animal` | 1 |
| `garbage_pile` | `pothole` | 1 |
| `garbage_pile` | `water_leak` | 1 |
| `open_manhole` | `garbage_pile` | 1 |
| `streetlight_out` | `dead_animal` | 1 |
| `waterlogging` | `dead_animal` | 1 |
| `waterlogging` | `open_manhole` | 1 |

Held-out confusions, published for the reader. They are **not** the prompt-pass work list: rewriting prompts against these would turn the held-out set into a development set, and the next number would report how well the prompts were tuned to the examples they were tuned on.

### The §43.2 work list (calibration split)

| Truth | Called | Count |
|---|---|---:|
| `footpath_damage` | `pothole` | 2 |
| `exposed_cable` | `dead_animal` | 1 |
| `garbage_pile` | `dead_animal` | 1 |
| `open_manhole` | `dead_animal` | 1 |
| `open_manhole` | `pothole` | 1 |
| `streetlight_out` | `dead_animal` | 1 |

This is what a prompt author works from. *Category X scored 0.4* says something is wrong; *X was called Y five times* says which two prompts to contrast. It is measured on the split the calibration curves were already fitted on — already spent — so acting on it leaves the held-out number measuring examples nothing has been tuned against.

## The 65% floor

The following categories are below the gate's floor. Each one triggers the §43.2 prompt pass and a re-measure; the honest number ships either way, and this section is where the work done on them is recorded.

| Category | F1 | Coverage | Confused with |
|---|---:|---:|---|
| `dead_animal` | 0.556 | 0.62 | — |
| `footpath_damage` | 0.545 | 0.50 | `pothole` x1 |
| `open_manhole` | 0.615 | 0.88 | `dead_animal` x2, `garbage_pile` x1 |
| `waterlogging` | 0.000 | 0.50 | `water_leak` x2, `dead_animal` x1, `open_manhole` x1 |

**Prompt pass:**

- **Pass 1 (2026-08-23, template `municipality` 1.1.0 → 1.2.0).** The calibration-split work list showed two patterns, neither of them about the categories being hard. (a) `dead_animal` absorbed everything — four other categories leaked into it — because its prompts said "lying in a public place" and "on the roadside", which describes every street complaint in the taxonomy; it was rewritten around the carcass itself with the leaking categories as explicit negatives. (b) The three road-surface categories (`pothole`, `footpath_damage`, `open_manhole`) had no contrast against each other at all, so a sentence about a hole in the ground competed on generic road vocabulary; each now names the other two as negatives. Held-out macro F1 moved 0.574 → 0.595 and `pothole` moved 0.000 → 0.667.
- **Not done in pass 1, deliberately: `waterlogging`.** It is the worst held-out category (F1 0.000, confused with `water_leak`) and it does not appear in the calibration-split work list at all — it scores fine there. Editing it would be tuning against the measurement. It is carried to the next pass, which needs calibration examples that actually exercise the `waterlogging`/`water_leak` boundary rather than more prompt text.


## Latency (§27.1)

| Operation | n | p50 | p95 | max | Budget |
|---|---:|---:|---:|---:|---:|
| `classify_one` | 72 | 41.1 ms | 56.1 ms | 82.6 ms | 10000 ms |

One example end to end — encode, score every category, fuse, decide — on this hardware, measured rather than estimated. Model *load* is excluded and reported by `nemesis_perception_model_load_seconds`: a cold start is a deployment property and no complaint after the first pays it.

### Per model

| Model pass | n | p50 | p95 | max | In budget |
|---|---:|---:|---:|---:|---|
| `encode_image` | 5 | 109 ms | 129 ms | 129 ms | yes |
| `encode_text` | 5 | 31 ms | 34 ms | 34 ms | yes |
| `transcribe` | 5 | 2318 ms | 2461 ms | 2461 ms | yes |

**Reported separately because the table above times the text encoder and nothing else.** The corpus is text, so a budget checked against it alone is a budget checked against the cheapest of the three models, while CLIP encode and Whisper transcribe are what a photographed or spoken report actually costs. These are single forward passes over one fixed input, with the first call discarded so the model load is not counted.

## Distant-face recall (§22.1)

Detector `mediapipe:blaze_face_short_range@1`, IoU ≥ 0.3.

| Face width | Faces present | Found | Recall | Mean confidence |
|---:|---:|---:|---:|---:|
| 12 px | 4 | 0 | **0.00** | 0.000 |
| 16 px | 4 | 0 | **0.00** | 0.000 |
| 24 px | 4 | 0 | **0.00** | 0.000 |
| 32 px | 4 | 0 | **0.00** | 0.000 |
| 48 px | 4 | 0 | **0.00** | 0.000 |
| 64 px | 4 | 0 | **0.00** | 0.000 |
| 72 px | 4 | 0 | **0.00** | 0.000 |
| 80 px | 4 | 4 | **1.00** | 0.629 |
| 88 px | 4 | 4 | **1.00** | 0.749 |
| 96 px | 4 | 4 | **1.00** | 0.792 |
| 128 px | 4 | 4 | **1.00** | 0.805 |

**Smallest face size at full recall: 80 px.**

This is Phase 0's carried-forward question and it is *not* discharged by Phase 8's "a face was blurred". `blaze_face_short_range` is a two-metre model; street photography is full of small bystanders, and small bystanders are exactly the population §22.1 protects. A shortfall here means a second detector or a tiled pass, not a footnote — and the number ships either way.

## What this does not establish

- The corpus is **authored**, not field data. The sentences are written in citizen voice from the defect vocabulary in Blueprint §8 and deliberately not paraphrased from the prompt sets they are scored against — but they were still written by people who know what the categories are. A real intake queue carries misspellings, code-switching mid-sentence, dictation artefacts and reports that name two defects at once, and none of that is here. Treat this number as an upper bound on the text modality, and re-measure the day there are labelled field submissions.
- **The image modality is not measured at all.** There is no licence-clean corpus of photographed civic defects in this repository, and rendering synthetic street scenes to score CLIP against would measure the renderer. So the published F1 is the text modality's, the CLIP prompt sets ship unmeasured, and Phase 10 — which needs image embeddings for dedup Stage 2 anyway — is where that corpus has to arrive. This is the same shape of carried-forward gap the HNSW report records, stated in the same place rather than discovered later.
- The fitted calibration is **scale normalisation, not a calibrated posterior**. It maps each category's measured in-class/out-of-class similarity gap onto a common logit gap so the softmax can compare categories at all. A confidence of 0.7 from this system does not mean seven in ten, and the document's `provenance` field says so on every entry an approver will read.
- **Marathi is measurably worse than English and Hindi here, and the corpus cannot say why.** The per-locale table is the finding; the explanation is not in it. It could be the encoder (multilingual-e5's Marathi coverage is thinner than its Hindi), the prompts (written in Marathi by the same hand that wrote the corpus, so a systematic vocabulary mismatch would be invisible), or the corpus (four examples per category per locale is a small number to conclude anything from). Separating those three needs a native-speaker review of the prompts and more Marathi examples, and until that happens the honest statement is the gap and not a cause.
- **Nine held-out examples per category is a small sample and the per-category numbers move accordingly.** One example is 0.125 of a category's recall, so a category's F1 can shift by more than a tenth on a single sentence. Read the per-category column as an indication and the macro figure as the number, and treat a category moving between two runs of a prompt pass as noise unless the calibration-split work list moved with it.
- **The per-model latency table meets the budget and the transcriber only meets it for short clips.** Whisper runs at roughly 1.15x real time on this CPU — a two-second clip costs ~2.3 s, against 116 ms for a CLIP image encode and 36 ms for a text encode. The §27.1 budget is per *stage*, so a voice note longer than about eight seconds breaches it, and `NEMESIS_PERCEPTION__MAX_AUDIO_SECONDS` currently permits 300. Nothing breaks — the stage degrades to `pending_classification` and a human plays the clip — but the practical ceiling is set by the recording length rather than by the budget, and the number to watch is `nemesis_perception_inference_seconds{operation="transcribe"}` against the audio duration recorded on `media_transcribed`. This is a measurement, not a regression: it is what the model costs, and it was previously unmeasured.
- The distant-face recall curve uses a **controlled synthetic stimulus**, not photographs. That is the right instrument for the question — recall as a function of face size in pixels is geometric — and it is the wrong instrument for absolute recall on real bystanders, who have hats, hair, angles and motion blur. What the curve establishes is the size at which this detector stops finding a face it can otherwise find; what it does not establish is the fraction of real bystanders protected in a real photograph.
