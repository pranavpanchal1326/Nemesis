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

## Known limitation, carried into Phase 8

`blaze_face_short_range` is tuned for faces within roughly 2 m of the camera.
Street photography of infrastructure defects often contains **small, distant
bystanders**, which is precisely the population §22.1 requires be blurred.
MediaPipe 1.x's Tasks API no longer ships the legacy full-range detector.

Recorded here rather than discovered later. Phase 8 must measure recall on
distant faces and, if it is inadequate, either add a tiled multi-scale detection
pass or substitute a general face detector. `face_detector_min_confidence` is
already biased low (0.4 rather than 0.5) on the reasoning that a missed face is
a privacy breach while a false positive only blurs some pavement.

## Changing a model

1. Update `nemesis.config.ModelSettings` — never a call site.
2. Write an ADR if the change alters embedding dimensions, licensing, or device
   allocation. A dimension change is a schema migration, not a config tweak.
3. Run `nem models`; the smoke tests will reject a model whose dimensions
   disagree with the database columns.
4. From Phase 11, re-run the evaluation harness — a model swap without a
   measured comparison is a guess.
