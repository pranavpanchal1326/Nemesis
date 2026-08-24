# Model Inventory

Every model NEMESIS runs, why it was chosen, what it costs, and what its licence
permits. Cached into the `modelcache` Docker volume so the stack runs with no
internet (§6.6, §38.1).

```bash
nem models          # fetch and verify
nem models-verify   # verify from cache with the network disabled
```

## Inventory

| Model | Purpose | Size | Device | Licence |
|---|---|---|---|---|
| **CLIP ViT-B-32** (`laion2b_s34b_b79k`) | Zero-shot classification (§43.1) + image embeddings for dedup Stage 2 (§14.1) | ~600 MB | CPU | MIT (code), CC-BY-4.0 (LAION weights) |
| **multilingual-e5-small** (`intfloat`) | Text embeddings for dedup Stage 2 across Hindi/Marathi/English (ADR-0003) | ~470 MB | CPU | MIT |
| **faster-whisper small** (int8) | Voice-complaint transcription (§8.4) | ~500 MB | CPU | MIT |
| **BlazeFace short-range** (`.tflite`) | Face blurring before any persistence (§22.1) | 224 KB | CPU | Apache-2.0 |
| **llama3.1:8b** (Ollama, host) | Investigation Agent reasoning (§12.4) | ~4.9 GB | **GPU** | Llama 3.1 Community Licence |

Cached weights total **~3.0 GB**; the Ollama model lives in the host's own store.

## Licence note for commercialisation

Four of the five are permissive (MIT / Apache-2.0 / CC-BY). **`llama3.1:8b` is
not.** The Llama 3.1 Community Licence requires attribution, carries an
acceptable-use policy, and requires a separate licence from Meta above 700 M
monthly active users. That threshold is irrelevant at pilot scale but is a real
term to record before a B2G procurement review, where licence provenance gets
audited.

If a fully permissive stack is ever required, the swap point is
`NEMESIS_OLLAMA__MODEL` — Qwen2.5 (Apache-2.0) is the blueprint's own stated
alternative (§8.4) and needs no code change.

## Device allocation (ADR-0002)

```
RTX 5060, 8 GB VRAM  ->  Ollama only
Ryzen 7, 8 cores     ->  CLIP, Whisper, e5, MediaPipe, SSIM
```

The dGPU is Blackwell (sm_120), unsupported by stable CUDA 12.4 torch wheels,
and `llama3.1:8b` already consumes ~5.5 GB of its VRAM under context. §27.1
budgets classification as CPU-bound, which this CPU meets.

## Verification is not a file check

`fetch_models.py` loads and executes every model rather than checking that files
exist. A present file proves nothing — truncated downloads, incompatible weight
formats, and missing companion files all surface only at inference time.

| Model | What the smoke test proves |
|---|---|
| CLIP | Both towers run a forward pass and agree on 512 dimensions, matching the `halfvec(512)` column the dedup index is built on |
| e5 | **Cross-lingual ordering**: the same complaint in Hindi and English scores closer than an unrelated complaint. This is the exact check `all-MiniLM-L6-v2` fails, and failing it silently is what would break dedup for Hindi/Marathi reporters |
| Whisper | Full CTranslate2 load path executes; a broken int8 conversion fails here |
| MediaPipe | The Tasks runtime initialises and detects on a real frame |
| Ollama | The configured model is actually present on the host |

Measured on the reference machine: cross-lingual similarity **0.884** vs
unrelated **0.814** — correct ordering, and the margin is a useful baseline to
watch if the model is ever changed.

## Air-gap proof

The meaningful test is not "offline flag set" but "no network interface":

```bash
docker run --rm --network none \
  -e HF_HOME=/models/hf -e TORCH_HOME=/models/torch -e XDG_CACHE_HOME=/models/cache \
  -v aicivicoperationsagent_modelcache:/models -w /app \
  aicivicoperationsagent-worker-ml python scripts/fetch_models.py --verify
```

Expected result: **all four cached models pass, Ollama fails.** That failure is
correct — Ollama is a host network service, not a cached weight. Anything else
passing would mean a hidden network dependency.

## Measured performance (Phase 9)

Two of these models now have numbers rather than reputations. Both are
reproduced by one command and both are published whether or not they flatter the
choice:

```bash
nem f1
```

| Model | What was measured | Result |
|---|---|---|
| **multilingual-e5-small** | Per-category precision/recall/F1 over a stratified held-out set of authored citizen-voice complaints in `en`/`hi`/`mr` | macro **F1 0.595**, micro 0.629, coverage 0.72; p95 44 ms per example against §27.1's 10 s budget |
| **BlazeFace short-range** | §22.1 distant-face recall as a function of face width, on a controlled stimulus in a 640×480 frame | **recall 1.00 at 80 px, 0.00 at 72 px** — a cliff, not a curve |
| **CLIP ViT-B-32** | *Accuracy not measured.* No licence-clean corpus of photographed civic defects exists here, and scoring against rendered scenes would measure the renderer. The gate does prove the tower runs end to end on a real submission | — |

### Inference cost, per model

Measured on the reference CPU, single forward pass, model load excluded:

| Pass | p50 | p95 | Note |
|---|---:|---:|---|
| `encode_text` (e5) | 34 ms | 34 ms | one complaint |
| `encode_image` (CLIP) | 112 ms | 129 ms | one 64×64 RGB frame |
| `transcribe` (Whisper) | 2222 ms | 2461 ms | a **two-second** clip |

**Whisper runs at roughly 1.15× real time and is ~70× the text encoder.** §27.1
budgets the classification stage at 10 s, so a voice note beyond about eight
seconds breaches it while `NEMESIS_PERCEPTION__MAX_AUDIO_SECONDS` permits 300.
Nothing breaks — the stage degrades to `pending_classification` and a human
plays the clip — but the practical ceiling on voice intake is set by recording
length rather than by the budget, and it is the first thing to shed when the ml
queue backs up (`perception_audio_transcription` is a kill switch for exactly
this reason).

Full report, per-locale breakdown, confusion pairs and the caveats that matter:
[`docs/reports/perception-f1.md`](reports/perception-f1.md).

## Known limitation, now quantified

`blaze_face_short_range` is tuned for faces within roughly 2 m of the camera.
Street photography of infrastructure defects often contains **small, distant
bystanders**, which is precisely the population §22.1 requires be blurred.
MediaPipe 1.x's Tasks API no longer ships the legacy full-range detector.

Recorded here rather than discovered later, and **Phase 9 measured it**: the
detector finds the stimulus at 80 px of face width and misses it entirely at
72 px, with no gradual falloff in between. In a 640×480 frame that is roughly
the two metres the model documents, so the limitation is exactly as advertised
and the consequence is now stated in pixels instead of adjectives — a bystander
beyond a few metres is not blurred at all.

The measurement does not fix it. The remedy is a tiled multi-scale detection
pass or a general face detector, which is scheduled work rather than a
configuration change, and it is a §22 obligation rather than a perception
feature. `face_detector_min_confidence` remains biased low (0.4 rather than 0.5)
on the reasoning that a missed face is a privacy breach while a false positive
only blurs some pavement — and the curve above shows that bias buys nothing at
all below the cliff, because the misses are not low-confidence detections, they
are no detections.

## Changing a model

1. Update `nemesis.config.ModelSettings` — never a call site.
2. Write an ADR if the change alters embedding dimensions, licensing, or device
   allocation. A dimension change is a schema migration, not a config tweak.
3. Run `nem models`; the smoke tests will reject a model whose dimensions
   disagree with the database columns.
4. Re-run `nem f1` and compare against `docs/reports/perception-f1.md`. A
   model swap without a measured comparison is a guess, and this is the
   comparison — the report records the `model_ids` every number was produced
   with, so the two runs are attributable rather than merely adjacent.
