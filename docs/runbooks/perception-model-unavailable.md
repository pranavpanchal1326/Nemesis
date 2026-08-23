# Reports are not being classified: the perception layer cannot load a model

- **Severity:** high — no data is lost and nothing is blocked, but every report
  is landing in the review queue instead of being routed to a department. A
  human is doing the classifier's job, one report at a time.
- **Owner:** DATA
- **Alerts:** `nemesis_pipeline_stage_degraded_total{stage="classification"}`
  rising, `nemesis_perception_model_loads_total{outcome="failed"}` non-zero, or
  `nemesis_perception_classifications_total{outcome="abstained"}` far above its
  usual share.

> **Read this before doing anything.** The system is behaving as designed.
> §24.2's degraded path is real shipped behaviour: a report the perception layer
> cannot classify is parked as `pending_classification` and reaches a human, with
> its photograph, its description and its transcript intact. Nothing is lost and
> nothing is silently mis-routed — which is the whole reason the layer abstains
> rather than guessing (ADR-0034).
>
> **Safety is unaffected.** §11.2's keyword pass runs on `worker-io` against the
> `safety` queue and needs no models at all, so a hazard report still bypasses
> the pipeline and reaches a reviewer at top priority. What is lost while this is
> broken is the *second* safety pass — the one that reads transcripts and
> photographs — so a gas leak reported only by voice will not be caught until a
> human plays the clip. That is the part to weigh when deciding how urgent this
> is.

## Symptoms

- Complaint status stuck at `pending_classification`; the projection shows
  `degraded_stage = "classification"`.
- `worker-ml` logs `perception_model_load_failed`, or
  `perception_encoder_warm_failed`, or `perception_stage_registered` with the
  note *"this worker cannot classify"*.
- `nemesis_perception_models_resident` at zero on a worker that is taking `ml`
  queue work.
- `nemesis_perception_model_evictions_total` climbing steadily — a different
  problem with the same symptom; see "The ceiling is below the working set".

Not this page:

- `failure_mode = "abstained"` on a **small** share of reports. That is the
  layer working: two categories were too close, or the winner did not clear the
  tenant's approved floor. Look at `docs/reports/perception-f1.md` and the
  coverage column before treating it as an incident.
- `PromptSetUnavailableError` in the degradation reason, or an abstention whose
  message says *"this tenant has authored no prompts for …"*. **That is a
  control-plane gap, not a model problem** — the tenant has categories with no
  prompt sets, and an operator fixes it in a minute through the taxonomy API. See
  `taxonomy-misconfiguration.md`.
- `stage = "trust_verification"` — face redaction, which fails closed rather than
  degrading. See `media-redaction-unavailable.md`.

## How to confirm

Which worker is serving the `ml` queue, and what did it manage to load:

```bash
docker compose logs worker-ml --tail 200 | grep -E "perception_(stage_registered|model_load|encoder)"
```

A healthy worker logs `perception_stage_registered` with `image_encoder=true`,
`text_encoder=true` and a `warm` map naming three model ids. A worker that came
up without the `ml` extra logs `perception_encoders_not_installed` — which is
correct and expected on `api`, `worker-io`, `relay` and `webhooks`, and is a
misrouted queue if you see it on `worker-ml`.

What the registry currently holds:

```bash
docker compose exec worker-ml python -c "from nemesis.perception.registry import REGISTRY; print(REGISTRY.snapshot())"
```

Whether the weights are actually on the cache volume, executed rather than
listed — a present file proves nothing:

```bash
docker compose exec worker-ml python scripts/fetch_models.py --verify
```

## Immediate mitigation

**1. If the models are missing from the cache volume**, fetch them. This needs
network access; an air-gapped deployment restores the `modelcache` volume from
backup instead.

```bash
docker compose exec worker-ml python scripts/fetch_models.py
```

**2. If a load is failing with `ModelCapacityError`**, the registry is refusing
rather than thrashing, and the number is in the message. Either raise the
ceiling — only if the container has the headroom, because the OOM killer does
not produce a log line anybody can act on:

```bash
NEMESIS_PERCEPTION__MAX_RESIDENT_MB=2560 docker compose up -d worker-ml
```

— or reduce the working set. Transcription is the largest model and the only one
with a kill switch, because it is by far the most expensive operation on this
worker (seconds per report against milliseconds for an embedding):

```bash
docker compose exec api python -m nemesis.flags disable perception_audio_transcription
```

Voice reports then park for a human who can play the clip, and photograph and
text reports classify normally.

**3. If the worker is up and the models load but nothing classifies**, check that
`worker-ml` is actually consuming the `ml` queue and that no other worker is:

```bash
docker compose exec worker-ml celery -A nemesis.worker.celery_app inspect active_queues
```

**4. Replay the parked reports once it works.** Nothing was lost; the reports are
in `pipeline_dead_letters` and in `pending_classification`, and both are
replayable. Do this *after* confirming a fresh submission classifies, or the
replay lands in the same hole.

## Root cause investigation

The five causes seen so far, in the order they are worth checking:

1. **The `modelcache` volume was recreated or never populated.** A fresh
   `docker compose down -v` takes the weights with it. The symptom is
   `ModelLoadError` naming a missing file, and `fetch_models.py` fixes it.
2. **The image was rebuilt without the `ml` extra.** `INSTALL_ML` is a build
   arg; a build that omits it produces a `worker-ml` that starts cleanly, logs
   `perception_encoders_not_installed`, and degrades every report it accepts.
   The log line is the tell, and it is `info` rather than `error` because on the
   other five images it is the correct state.
3. **The ceiling is below the working set.** Sustained
   `nemesis_perception_model_evictions_total` means CLIP evicts Whisper, the next
   voice report reloads Whisper which evicts CLIP, and throughput collapses to
   the reload time. Nothing errors. Raise `MAX_RESIDENT_MB`, or shed
   transcription, or split the queues across two workers.
4. **A checkpoint was changed without the column being changed with it.** The
   loaders assert the embedding width against `halfvec(512)` and `vector(384)`
   and refuse a mismatch, because storing the wrong width corrupts dedup Stage 2
   for every complaint written afterwards. The message names both numbers.
5. **The load is genuinely slow rather than broken.** A cold Whisper load from an
   unwarmed page cache takes tens of seconds and
   `nemesis_perception_model_load_seconds` reaches to 120 s for exactly that
   reason. A worker that has not finished warming reports unready; give it the
   time before treating it as an outage.

## Prevention

- **`warm_load_on_start` is on by default**, so a broken model fails at worker
  startup rather than on the first citizen's report — where it would race a
  retry against the load it is waiting for.
- **`nem models-verify` runs the models with the network disabled**, which is
  what catches a truncated download or an incompatible weight format before a
  deploy rather than at inference time.
- **The registry declares footprints and enforces a ceiling below the
  container's limit**, because the registry can only refuse what it knows about
  — torch's allocator, the decoded image and Python's own heap are all outside
  its accounting, and a ceiling equal to the container's is one the OOM killer
  reaches first.
- **`nem f1` is the regression test for the layer as a whole.** It publishes
  per-category precision, recall and F1 against a committed corpus, so "the
  models load" and "the models are still any good" are separate checkable
  claims. Run it after any checkpoint change and compare against
  `docs/reports/perception-f1.md`.
