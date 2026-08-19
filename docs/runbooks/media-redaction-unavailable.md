# Complaints with photographs are halting: §22.1 redaction cannot run

- **Severity:** critical — no data is lost, and no work is moving. Every report
  carrying a photograph stops before classification and stays stopped.
- **Owner:** DATA · SEC
- **Alerts:** `nemesis_pipeline_stage_degraded_total{stage="trust_verification"}`
  rising, and `nemesis_media_redactions_total{outcome="unavailable"}` non-zero.

> **Read this before doing anything.** The system is behaving exactly as
> designed. §22.1 requires faces blurred before an image is served, and Phase 8
> makes that **fail closed**: when no face detector is available, the trust stage
> refuses rather than passing the photograph through unblurred. Nothing is lost —
> every affected complaint is in `pipeline_dead_letters`, and every one of them
> can be replayed the moment redaction works again.
>
> There is deliberately no kill switch for this (ADR-0032). If you find yourself
> looking for a flag to turn face blur off, the answer is that turning it off
> would serve strangers' faces to every reviewer, and the fix is below.
>
> **Audio-only and text-only reports are unaffected** and are still flowing.

## Symptoms

- Complaint status stuck at `submitted` for reports with a photo; the projection
  shows `degraded_stage = "trust_verification"` and
  `degraded_fallback = "halted_for_review"`.
- `worker-ml` logs `stage_provider_registration_failed`, or
  `trust_stages_registered` with `face_detector=false` and the note *"this worker
  cannot redact"*.
- `nemesis_media_redactions_total{outcome="unavailable"}` climbing.
- Open dead letters for `trust_verification` in `nemesis_pipeline_dead_letters_open`.

Not this page:

- `outcome="failed"` rather than `"unavailable"` — the detector loaded and the
  *image* was the problem. That is one malformed upload per complaint, not an
  outage. See "A single upload will not decode" below.
- `stage="classification"` degradations — Phase 9's, unrelated.
- `stage="safety_check"` — the §11.2 fail-safe, which runs on `worker-io` and
  needs no models at all. See `safety-path-degraded.md`.

## How to confirm

Which worker is serving the `ml` queue, and does it have the model:

```bash
docker compose exec -T worker-ml python -c "from nemesis.trust.detectors import detector_is_registered; print('detector registered:', detector_is_registered())"
```

Is the pinned `.tflite` bundle actually in the cache:

```bash
docker compose exec -T worker-ml ls -l /models/blaze_face_short_range.tflite
```

Does MediaPipe import at all in that image — Phase 0 hit a runtime link failure
on `libEGL.so.1` here, which builds cleanly and only breaks at first use:

```bash
docker compose exec -T worker-ml python -c "import mediapipe; print(mediapipe.__version__)"
```

How many complaints are waiting:

```bash
docker compose exec -T postgres psql -U nemesis -d nemesis -tAc "SELECT count(*), min(created_at) FROM pipeline_dead_letters WHERE stage = 'trust_verification' AND resolved_at IS NULL"
```

## Immediate mitigation

**Do not turn redaction off. There is no way to, and adding one is a breach.**

If the model file is missing — the most common cause after a fresh volume:

```bash
python tasks.py models
```

Then restart the worker so the detector is registered in each pool child:

```bash
docker compose restart worker-ml
```

If MediaPipe itself will not import, the image is wrong rather than the
configuration. Rebuild it explicitly with the ML extra:

```bash
docker compose build --no-cache worker-ml
```

Confirm the worker can redact before releasing the backlog:

```bash
docker compose exec -T worker-ml python -c "from nemesis.trust.providers import install_trust_workers; install_trust_workers()"
```

Replaying the halted complaints is the ordinary dead-letter path; see
`pipeline-stage-failures.md` for the replay command. Nothing here needs a
special one — the stage is idempotent, and a complaint that already has a
`media_redacted` event is a provable no-op on redelivery.

## Root cause investigation

Three causes, in the order they actually occur:

**1. The model cache is empty.** `nem nuke`, a fresh clone, or a rebuilt
`modelcache` volume. The `.tflite` bundle is a fetched artefact rather than
something MediaPipe carries — MediaPipe 1.x removed the legacy `mp.solutions`
API entirely, and the Tasks API loads an explicit bundle. Check
`docs/MODELS.md`.

**2. The stage was routed to a worker that cannot redact.** Check
`SPECS["trust_verification"].queue` is still `ml`. If somebody moved it to `io`
to "make it faster", every complaint with a photo will halt on `worker-io`,
which has never carried MediaPipe. That is one line in `pipeline/stages.py` and
a test asserts it.

**3. `worker_process_init` did not fire in the pool children.** The providers
are registered per child, because on the prefork pool the parent forks before
`worker_ready`. A worker whose parent logged the registration and whose children
did not will report `provider_unavailable` for every task. Restarting the
container is the fix; the cause is a change to `celery_app.py`'s signal wiring.

**A single upload will not decode** is a different problem with the same shape
from a distance. It affects one complaint, `outcome="failed"`, and the message
names the reason — a truncated file, or one whose declared dimensions exceed the
50-megapixel decode limit. That complaint needs a human, not an intervention.

## Prevention

- **Phase 1b** provisions the model cache as part of the environment rather than
  as a manual step, which removes cause 1 entirely.
- **Phase 9** puts the face detector behind the same model registry as CLIP and
  Whisper, with a warm-load and a single-flight guard — at which point "is the
  model present" becomes a readiness check rather than a discovery made when the
  first photograph arrives.
- **Phase 25**'s fault-injection suite exercises this path deliberately, so the
  halt-and-recover behaviour stops being something we assert and becomes
  something that ran last night.
- The `nem doctor` check for the model cache already covers the local case; the
  gap it does not cover is a *deployed* worker, which is Phase 1b's readiness
  contract.
