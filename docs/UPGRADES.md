# NEMESIS — Upgrades, Training & Technical Debt

**Companion to [HANDOVER.md](HANDOVER.md) and [BACKLOG.md](BACKLOG.md).**

The backlog is *what the plan says we owe*. This document is *what we would
change if we were being honest about the system rather than about the plan* —
model training, architectural upgrades, and debt worth paying down.

Nothing here is required to complete a phase gate. Everything here is an argued
proposal with a stated cost. Items are ranked by **value ÷ effort**, and each one
says what it would fix and how you would know it worked.

---

## Part 1 — Model & training work

### The honest position on ML today

Every model in this system is **off-the-shelf and zero-shot**. Nothing has been
fine-tuned, and that was the right call for Phases 0–10: you cannot train without
labelled data, and the labelled data arrives from the review queue and the merge
overrides that Phases 11–15 create. But it means the accuracy numbers we publish
are a floor, not a ceiling, and two of them are poor.

| Model | Role | Trained? | Measured | Verdict |
|---|---|---|---|---|
| `multilingual-e5-small` | Text embeddings, dedup Stage 2 | No | macro F1 0.595 (text classification); dedup precision 0.600 | **The weak link.** Compresses same-domain civic complaints into a 0.82–0.88 similarity band. |
| CLIP ViT-B-32 | Zero-shot category, dedup image side | No | **Not measured at all** | Unknown. The largest blind spot in the system. |
| `faster-whisper` small/int8 | Transcription | No | Decodes real audio; **quality unmeasured** | Unknown for Hindi/Marathi. |
| `blaze_face_short_range` | §22.1 face redaction | No | Recall 0.00 below 80 px, 1.00 above | **Insufficient for street photography.** |
| `llama3.1:8b` | Investigation Agent (Phase 16) | No | Not built | — |

---

### U-1 · Fine-tune the text encoder on civic complaints · **High value / High effort**

**The problem, precisely.** `multilingual-e5-small` is trained for general
retrieval. Every civic complaint occupies a tiny region of its embedding space,
so unrelated reports score ~0.82 and true duplicates ~0.88. Phase 10's gate fails
inside that 6-point band — the diagnosis in
[`reports/dedup-precision-recall.md`](reports/dedup-precision-recall.md) proves
*no threshold* separates the classes.

**The fix.** Contrastive fine-tuning with a triplet or multiple-negatives-ranking
objective, where positives are reports of the same incident and negatives are
same-category reports of *different nearby* incidents. Those hard negatives are
the entire point — random negatives are already easy and teach nothing.

**Where the data comes from.** This is why it is sequenced after Phase 11: merge
confirmations are positives, merge reversals
(`nemesis_dedup_merge_reversions_total`) are hard negatives, and both accumulate
for free once operators use the system. The moat thesis in §4.3 is *literally
this loop*.

**Cost.** A LoRA or full fine-tune of a 118 M-parameter encoder fits on the RTX
5060 in minutes-to-hours. The expense is labelled data, not compute.

**Prove it worked:** dedup precision and recall both rise on the held-out split,
and — the number that matters — the true/false confidence distributions stop
interleaving.

---

### U-2 · Measure CLIP before deciding anything about it · **High value / Low effort** (after B-10.1)

We are shipping a vision model whose accuracy nobody knows. It may be fine. It
may be much worse than the text side. Both are plausible and neither is
actionable.

`perception/harness.py` already supports the image modality — it takes an
`ImageEncoder` and the corpus format has a slot for media. Once B-10.1 delivers
photographs this is **a day's work**, and it converts the single largest unknown
in the system into a number.

**Do this before U-3.** Fine-tuning a model you have not measured is how you
spend a fortnight improving something that was already adequate.

---

### U-3 · Fine-tune or replace CLIP for civic defects · **Value unknown until U-2 / High effort**

Options in ascending cost:

1. **Better prompts** (§43.2 pass). Free. Phase 9 did this for text and moved macro F1 0.574 → 0.595.
2. **A linear probe** on frozen CLIP features. Cheap, often most of the benefit, and needs only a few hundred labelled images per category.
3. **LoRA on the image tower.** More capable, needs more data.
4. **A different backbone** — SigLIP or an EVA-CLIP variant. Better zero-shot on fine-grained categories, at a larger memory footprint that must fit the §19 VRAM budget alongside Ollama.

**Constraint that decides this:** the browser's WebGL context shares the RTX
5060's 8 GB with Ollama, and Track E budgets scene VRAM at ≤512 MB. A larger
vision backbone competes with the Investigation Agent for the same card. Measure
before you commit.

---

### U-4 · Fix distant-face recall · **High value / Medium effort** — see **B-G2**

Listed in the backlog because it is a **privacy obligation**, not an optimisation.
Repeated here because the options are model choices:

- **Tiled inference** with the existing detector — no new weights, multiplies cost per image.
- **`blaze_face_full_range`** — a different MediaPipe bundle, designed for exactly this.
- **A general detector** (YOLO-family, face-tuned) — most capable, heaviest, new dependency.

Try them in that order. The measurement harness already exists, so comparing
three detectors is a morning's work once one of them is wired.

---

### U-5 · Measure transcription quality · **Medium value / Medium effort** — closes **G3**

The gate proves faster-whisper *runs*; it has never been given speech. §8.4
promises a citizen can report in their own language and nothing verifies it —
particularly for Marathi, which is already the worst-performing language on the
text side (**G4**).

Needs licensed spoken audio in en/hi/mr. Report WER per locale. If Marathi is
poor here *and* poor in text, **G4**'s cause is likely the language coverage of
the models rather than the prompts — which is a decision about model selection,
not prompt authoring, and worth knowing before anyone rewrites prompts.

---

### U-6 · Retire the `[0.82, 0.88]` problem with calibration, not thresholds · **Medium value / Low effort**

Phase 9 already solved this shape of problem for classification: fitted
per-category curves mapped raw similarity onto a comparable scale, moving macro
F1 from 0.114 (document defaults) to 0.595.

**Dedup has no equivalent.** It compares raw cosine directly against a band
threshold. A `dedup_calibration` policy document — same governance, same
approver flow as `perception_calibration` — would let the *shape* of the
similarity distribution be corrected without re-training anything.

This is cheaper than U-1 and may make U-1 unnecessary. **Try it first.** It does,
however, need the calibration split from B-10.1, for the same reason everything
else does.

---

## Part 2 — Architecture & platform upgrades

### U-7 · Prove the §27.1 budgets under load · **High value / Medium effort** — closes part of **G7**

Ten phases in, **no load test has ever run.** Every latency number in the
repository is a single-threaded measurement on an idle laptop. The §27.1 budgets
are a customer-facing promise and they are unverified under concurrency, where
connection pools, queue depth and lock contention actually live.

`k6` is named in the engineering standards and is not in the repository. Phase 28
formally owns this; doing a thin version now would de-risk everything before it.

**Prove it worked:** a CI job that fails when p95 for any stage crosses its §27.1
budget at target volume.

---

### U-8 · Fault injection with `toxiproxy` · **High value / Medium effort** — closes part of **G7**

Every degradation path is asserted by unit tests that *simulate* failure. None
has been exercised against a real network fault — a slow socket, a half-open
connection, a dependency that accepts and never responds. Those are the failures
that actually page people, and the readiness/degradation logic is exactly where
optimistic assumptions hide.

27 runbooks describe scenarios; a `toxiproxy` harness would turn each into an
automated test, which is what the Phase 25 gate demands anyway.

---

### U-9 · Mutation testing on the modules where correctness is the product · **Medium value / Medium effort** — **B-S2**

Line coverage is 86%, which says the code ran, not that the assertions would
notice if it were wrong. A declared standard that has never executed.

Start narrow, where a silent wrong answer is most costly:
`dedup/decide.py`, `policy/resolver.py`, `events/` (the hash chain),
and Phase 13's authorization decisions when they exist.

---

### U-10 · Retire the `image_weight` / `text_weight` split when one side is unmeasured · **Low effort / prevents a real error**

`DedupBand` weights the two modalities 0.6 / 0.4 by default. The image side has
never been measured, so **the default gives 60% of every merge decision to a
model whose accuracy is unknown.** Today it is inert only because most test
reports carry no photograph.

Until U-2 lands, either default `image_weight` to 0.0 with a comment saying why,
or surface a loud startup warning when a band weights an unvalidated modality.
The current state — a confident-looking number resting on an unmeasured model —
is the kind of thing this codebase is otherwise careful about.

---

### U-11 · Snapshot strategy for long chains · **Medium value / Medium effort**

`write_snapshot_if_due` exists and works. Nothing has yet replayed a chain long
enough to stress it. Phase 21 (temporal replay — project owner's) will scrub 12
months of history and is the first real consumer.

Worth a synthetic benchmark **before** the frontend depends on it: generate a
100k-event entity and measure replay with and without snapshots. Cheap to do now,
expensive to discover during a frontend integration.

---

### U-12 · Split the `io` queue before Phase 12 lands on it · **Low effort / prevents a real incident**

Dedup, severity scoring and routing are all specced onto `QUEUE_IO`. That queue
is already served by the container that also runs the safety stage's siblings.
Phase 12 adds OSM enrichment — a **network-bound** external call — to the same
queue as **CPU-bound** dedup.

One slow OSM lookup will then add queue latency to every dedup decision behind
it. ADR-0004 already split workers by memory profile; this is the same argument
by *blocking* profile. Cheapest possible fix, and much cheaper before Phase 12
than after.

---

### U-13 · Postgres Row-Level Security · **High value / Medium effort** — Phase 25

§18.3 currently documents a gap rather than closing it. Tenant isolation is
enforced at three layers (ADR-0014) — all of them application-side. A raw SQL
console, a misconfigured read replica, or one missed predicate bypasses every one.

`check_tenant_scoping.py` is a good static net and it is still a net. RLS makes
the isolation a property of the database.

---

### U-14 · Version and pin the model artefacts like code · **Medium value / Low effort**

`fetch_models.py` pulls weights into an offline cache and verifies them, which is
already better than most. But the model registry keys on model *id*, not on a
content hash of the weights.

Once anything is fine-tuned (U-1, U-3), "which weights produced this number"
becomes a reproducibility question that the current design cannot answer. Add a
weight digest to the registry and stamp it onto `classification_scored.model_ids`
— the field is already a map and already accepts this without a schema change.

---

## Part 3 — Debt worth paying now

| # | Debt | Effort | Why now |
|---|---|---|---|
| **U-15** | `CHANGELOG.md` is stale from Phase 3 onward (**G9**) | S | The file says a wrong changelog is worse than none because it is read as a record during an incident. Fix the generator or the commit convention — find out which drifted. |
| **U-16** | ~400 tests skip silently without `NEMESIS_TEST_ADMIN_DSN` and the run exits 0 | S | The most dangerous kind of green build. Fail the run if the integration suite was skipped wholesale outside an explicitly unit-only invocation. |
| **U-17** | ADR numbering has already collided once | S | Phases 9 and 10 both claimed 0035. Add a pre-commit check that ADR numbers are unique and contiguous. |
| **U-18** | `superseded_by_id` is filtered on and never written (**G6**) | M | A predicate no code satisfies is a predicate nobody can reason about. Either Phase 14 writes it (**B-14.6**) or it should be removed. |
| **U-19** | No dedup runbook (**B-S4**) | S | Phase 10 shipped five metrics with no on-call page. |
| **U-20** | §44 REAL/SIMULATED/ROADMAP table unreconciled since Phase 3 (**G8**) | S per phase | Compounding. Cheaper per phase than once at Phase 29, and it is a gate clause there. |

---

## Suggested sequencing

**Do now, cheap, prevents future pain**
U-12 (split the queue) · U-10 (unmeasured modality weight) · U-16 (silent skips) · U-17 (ADR numbering) · U-15 (changelog)

**Do next, unlocks the most**
B-10.1 (photo corpus) → U-2 (measure CLIP) → U-6 (dedup calibration) → B-10.2 (radius)

**Do alongside Phase 12–13**
U-7 (`k6`) · U-8 (`toxiproxy`) · U-9 (mutation testing on `decide` and `resolver`)

**Do once labels accumulate (post Phase 11)**
U-1 (fine-tune the text encoder) · U-3 (CLIP, if U-2 says so) · U-14 (weight digests)

**Do with the phase that owns it**
U-4 → **B-G2** · U-13 → Phase 25 · U-11 → before Phase 21 integration

---

## One thing to keep

The most valuable property of this codebase is not its coverage or its type
strictness. It is that **it publishes its own bad numbers** — four categories
below the F1 floor, a face detector that fails below 80 px, a dedup gate that
does not pass. Every one of those was findable only because somebody built the
measurement before they needed the answer, and reported it when they did not like
it.

Keep doing that. It is worth more than any item on this page.
