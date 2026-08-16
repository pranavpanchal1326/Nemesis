# 0004 — Split Celery workers by memory profile

- **Status:** Accepted
- **Date:** 2026-08-16
- **Owner:** PLT
- **Blueprint:** §8.2, §11.2, §27.3

## Context

Celery's prefork pool imports the task module **once per worker process**. With
torch, CLIP, faster-whisper, and an embedding model reachable from that module,
each process carries roughly 1.5 GB resident. A conventional
`--concurrency=4` worker therefore costs ~6 GB — on a 16 GB machine that also
runs WSL2, Postgres, Redis, Ollama, a browser, and an editor.

A second, independent problem: §11.2 requires danger-flagged reports to bypass
the queue entirely. On a single shared queue, a backlog of routine
classification work delays a "gas leak" report behind it. That is a safety
property, not a performance preference.

## Decision

Three queues served by two worker deployments:

| Queue | Worker | Concurrency | Holds models |
|---|---|---|---|
| `ml` | `worker-ml` | **1** | Yes — the only process in the system that does |
| `io` | `worker-io` | 4 | No — image never installs torch |
| `safety` | `worker-io` | 4 | No |

`worker-io` and `beat` build with `INSTALL_ML=false`, so torch is not merely
unused there — it is absent.

`worker-ml` runs `--concurrency=1` and `--max-tasks-per-child=100`, bounding
both peak memory and gradual leakage.

## Alternatives considered

**One worker with a threads pool.** Rejected: Python inference releases the GIL
unevenly, and a thread pool shares one address space, so a single OOM takes down
every in-flight task rather than one.

**`--max-memory-per-child`.** Rejected as the primary control: it recycles a
worker *after* it has already grown, which on a 16 GB machine can mean the OOM
killer arrives first. Bounding process count is preventative rather than
reactive.

**Separate queues without separate images.** Rejected: the `io` worker would
still import torch and pay ~1.5 GB for tasks that never use it.

## Consequences

- ML memory is bounded to one process, permanently and by construction.
- Safety-flagged work cannot queue behind classification backlog.
- `worker-io` and `beat` images are 714 MB; `worker-ml` is 827 MB.
- ML throughput is serialised at concurrency 1. Acceptable at pilot volume, and
  the scaling path is horizontal `worker-ml` replicas rather than raising
  concurrency inside one.
- Task routing must be explicit. A task sent to the wrong queue fails on a
  missing import in `worker-io`, which is a loud, immediate failure — the
  intended behaviour.

## Revisit when

Sustained `ml` queue depth exceeds what one worker drains within the §27.1
budget. The response is more `worker-ml` replicas, not higher concurrency.
