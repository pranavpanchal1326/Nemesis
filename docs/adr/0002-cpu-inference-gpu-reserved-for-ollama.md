# 0002 — CPU-only inference; the GPU is reserved for Ollama

- **Status:** Accepted
- **Date:** 2026-08-16
- **Owner:** DATA
- **Blueprint:** §8.4, §12.4, §27.1

## Context

The development and demo machine has an NVIDIA RTX 5060 Laptop GPU with 8 GB of
VRAM, and 16 GB of system RAM shared with WSL2, a browser, and an editor.

Two hardware facts forced the decision:

1. **The RTX 5060 is Blackwell (sm_120).** Stable PyTorch CUDA 12.4 wheels emit
   no sm_120 kernels. GPU inference requires cu128 builds, which pull a CUDA
   runtime and produce an image around 6 GB.
2. **`llama3.1:8b` already consumes roughly 5.5 GB of VRAM under context.**
   Adding CLIP, faster-whisper, and an embedding model to the same 8 GB device
   means contention, and the failure mode is the Investigation Agent (§12.4)
   stalling or OOM-ing mid-demo.

Blueprint §27.1 independently budgets classification as "CPU-bound at demo
scale" with an 8-second allowance.

## Decision

Inference splits by device:

- **GPU (RTX 5060), exclusively:** Ollama, on the Windows host.
- **CPU (Ryzen 7, 8 cores), in-container:** CLIP, faster-whisper, sentence
  embeddings, SSIM.

The `ml` extra pins the PyTorch CPU wheel index in `backend/Dockerfile`. Torch
thread count is capped at 4 so inference cannot starve Postgres and Redis.

## Alternatives considered

**GPU inference via cu128 wheels.** Rejected: a ~6 GB image, WSL2 GPU
passthrough complexity, and direct VRAM contention with the component whose
failure is most visible. The measured CPU latency does not justify the cost.

**A smaller LLM to free VRAM for CV.** Rejected: it degrades the one genuinely
agentic component (§12.4) to protect a stage that already meets its budget on
CPU.

**Hosted inference APIs.** Rejected outright — §6.6 requires zero-cost,
offline-capable operation, and §22.1's data-localisation posture is a sales
asset, not a checkbox.

## Consequences

- Worker image is **827 MB** instead of ~6 GB; rebuilds are fast.
- CLIP ViT-B/32 measures ~150 ms per image on this CPU, inside the §27.1 budget.
- No CUDA/driver coupling, so CI runs the same wheels as the demo machine.
- Batch throughput is materially lower than GPU. This is acceptable at pilot
  scale and is the first thing to revisit under load.

## Revisit when

Sustained classification throughput exceeds what the CPU path delivers within
the §27.1 budget, **or** deployment moves to a server with a non-Blackwell GPU
or ≥ 16 GB of VRAM. `docs/HARDWARE.md` documents the exact switch.
