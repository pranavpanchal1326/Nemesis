# Hardware Profile & Resource Budget

The NEMESIS development stack is tuned for one specific machine. This document
records that machine, the budget derived from it, and what to change on
different hardware — so the tuning constants scattered across
`docker-compose.yml` and `backend/nemesis/config.py` are traceable to a reason
rather than looking arbitrary.

## Reference machine

| Component | Spec |
|---|---|
| CPU | AMD Ryzen 7 250 (8-core Zen, 3.30 GHz) |
| RAM | 16 GB (15.3 GB usable) |
| dGPU | NVIDIA GeForce RTX 5060 Laptop, 8 GB VRAM — **Blackwell, sm_120** |
| iGPU | AMD Radeon 780M, 512 MB |
| Storage | 954 GB, ~551 GB free |
| OS | Windows 11 Home Single Language 26200 |

## The two constraints that drive every decision

**1. RAM, not compute, is binding.** 16 GB has to hold WSL2 (Postgres, Redis,
API, three Celery workers), an Ollama 8B model on the host, a browser, and an
editor. Nothing here is CPU-starved; everything here can be RAM-starved.

**2. The RTX 5060 is Blackwell (sm_120).** Stable PyTorch CUDA 12.4 wheels do
not emit sm_120 kernels — GPU torch requires cu128 builds and a ~6 GB image.
Combined with only 8 GB of VRAM already largely consumed by `llama3.1:8b`
(~5.5 GB under context), GPU inference for CLIP/Whisper is not worth its cost.

### Resulting allocation

```
RTX 5060 (8 GB VRAM)  ->  Ollama only, on the Windows host.
Ryzen 7 (8 cores)     ->  CLIP, faster-whisper, e5 embeddings, SSIM (CPU, in-container).
```

Blueprint §27.1 already budgets classification as "CPU-bound at demo scale"
(< 8 s), which CLIP ViT-B/32 meets on this CPU at roughly 150 ms per image.

## Memory budget

| Process | Limit | Note |
|---|---|---|
| `postgres` | 1536 MB | `shared_buffers=384MB`, `maintenance_work_mem=192MB` for HNSW builds |
| `redis` | 256 MB | `maxmemory 192mb`, `noeviction` — silently evicting queue state is unacceptable |
| `api` | 640 MB | |
| `worker-io` | 640 MB | concurrency 4, never imports torch |
| `worker-ml` | 3072 MB | concurrency **1** — the only process holding model weights |
| `beat` | 192 MB | |
| **Application subtotal** | **6336 MB** | started by `nem up` |

### Observability profile (`nem obs`) — opt-in, +992 MB

Not started by default, and the reason is this arithmetic rather than taste
(ADR-0007). Adding it lands the stack at **7328 MB** inside the 8192 MB cap,
leaving under 900 MB of headroom on a machine also running a browser, an editor,
and host-side Ollama. `worker-ml` is the largest consumer and the first thing
the OOM killer reaches for — and its symptom is inference failing mid-pipeline
for reasons that look like a model problem.

| Process | Limit | Note |
|---|---|---|
| `prometheus` | 256 MB | 7-day retention, 15s scrape |
| `grafana` | 256 MB | provisioned dashboards and datasources, read-only |
| `tempo` | 256 MB | 24h trace retention — traces are for debugging *now*; the durable record is the event log (§9.1) |
| `otel-collector` | 128 MB | `memory_limiter` sheds spans under pressure rather than being OOM-killed |
| `alertmanager` | 96 MB | |
| **Observability subtotal** | **992 MB** | |
| **WSL2 total, both profiles** | **7328 MB** | inside the 8192 MB cap, ~860 MB headroom |

## Required: cap WSL2

Docker Desktop's WSL2 backend will otherwise claim up to ~50–80% of RAM and is
slow to release it, starving host-side Ollama. Create
`C:\Users\<you>\.wslconfig`:

```ini
[wsl2]
memory=8GB
processors=6
swap=4GB
# Reclaim freed page cache back to Windows instead of holding it.
autoMemoryReclaim=gradual
sparseVhd=true
networkingMode=mirrored
```

Apply with:

```bash
wsl --shutdown
```

then restart Docker Desktop. Verify with `free -h` inside any container.

## Scaling to different hardware

| If you have | Change |
|---|---|
| 32 GB+ RAM | Raise WSL cap to 16 GB, `shared_buffers=1GB`, `worker-io --concurrency=8` |
| A non-Blackwell NVIDIA GPU with ≥ 12 GB | Drop `--index-url .../whl/cpu` in `backend/Dockerfile`, add `gpus: all` to `worker-ml` |
| An Ada/Blackwell GPU with ≥ 16 GB | Same as above but use the cu128 wheel index (`https://download.pytorch.org/whl/cu128`) |
| No NVIDIA GPU | Swap `NEMESIS_OLLAMA__MODEL` to `llama3.2:3b`; expect Investigation Agent latency near the §27.1 90 s ceiling |
