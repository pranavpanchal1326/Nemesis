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

<!-- budget:usable-vm-mb = 7587 -->
<!-- budget:min-headroom-mb = 768 -->

**This table is executed, not decorative.** `scripts/check_memory_budget.py`
reads every `mem_limit` in `docker-compose.yml`, asserts each one has a row here
with the same number, re-adds both subtotals, and fails when the totals no
longer fit the VM. It runs in `nem check` and in CI.

It is checked because it drifted. `relay` and `webhooks` were added to the
compose file with 192 MB each and never added here, so the documented total sat
384 MB below the declared one — and with the observability profile up the stack
declared **7712 MB against a 7587 MB VM**. The first process the kernel reaches
for in that condition is `worker-ml`'s fork child at model load, and its symptom
is inference failing for reasons that look like a model problem. ADR-0051 has
the argument; the two comment markers above are the contract this check reads.

**7587 MB, not 8192.** The `.wslconfig` cap below is what WSL2 is *asked* for;
`docker info` reports 7956238336 bytes of it as usable, the difference being the
guest kernel and its own structures. Budgeting against the number that was asked
for rather than the number that arrived is how a budget passes review and fails
at runtime.

| Process | Limit | Note |
|---|---|---|
| `postgres` | 1536 MB | `shared_buffers=384MB`, `maintenance_work_mem=192MB` for HNSW builds |
| `redis` | 256 MB | `maxmemory 192mb`, `noeviction` — silently evicting queue state is unacceptable |
| `api` | 640 MB | measured ~106 MB idle; the headroom is for request bursts and `satori` rasterising a share card |
| `worker-io` | 640 MB | concurrency 4, never imports torch. Measured ~316 MB under load |
| `worker-ml` | 3072 MB | concurrency **1** — the only process holding model weights. Measured ~2130 MB with CLIP resident. **Not trimmed**: this is the process whose OOM this budget exists to prevent, and squeezing it is squeezing the symptom |
| `beat` | 128 MB | measured ~77 MB — a scheduler, not a worker |
| `relay` | 128 MB | measured ~66 MB. Absent from this table until ADR-0051 |
| `webhooks` | 128 MB | measured ~74 MB. Absent from this table until ADR-0051 |
| **Application subtotal** | **6528 MB** | started by `nem up`; leaves 1059 MB against the 768 MB floor above |

### Observability profile (`nem obs`) — opt-in, +736 MB

Not started by default, and the reason is this arithmetic rather than taste
(ADR-0007). Every limit below was cut in ADR-0051 so that both profiles fit the
VM at all; the profile is a debugging tool a developer turns on knowingly, so it
is held to *fitting* rather than to the application set's headroom floor.

| Process | Limit | Note |
|---|---|---|
| `prometheus` | 192 MB | 7-day retention, 15s scrape |
| `grafana` | 192 MB | provisioned dashboards and datasources, read-only |
| `tempo` | 192 MB | 24h trace retention — traces are for debugging *now*; the durable record is the event log (§9.1) |
| `otel-collector` | 96 MB | `memory_limiter` sheds spans under pressure rather than being OOM-killed |
| `alertmanager` | 64 MB | |
| **Observability subtotal** | **736 MB** | |
| **WSL2 total, both profiles** | **7264 MB** | inside the 7587 MB VM, 323 MB spare |

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
