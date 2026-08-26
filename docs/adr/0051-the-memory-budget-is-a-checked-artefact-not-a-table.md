# 0051 — The memory budget is a checked artefact, not a table

- **Status:** Accepted
- **Date:** 2026-08-25
- **Owner:** SRE
- **Blueprint:** §27.1, §6 Principle #6; `docs/FRONTEND-PHASE-PLAN.md` F1

## Context

> **Amended by ADR-0054.** The budget work below is correct and stands. Its
> *account of the SIGKILL loop* does not: the killer is billiard's four-second
> `worker_proc_alive_timeout`, not the kernel, and the measurement that
> separates them is `worker-ml` sitting at 821 MiB against its 3 GiB limit while
> the loop runs. Read this ADR for the budget and ADR-0054 for the symptom.

`worker-ml`'s fork children were SIGKILLed at model load. The symptom is a
Celery `WorkerLostError` immediately after CLIP starts loading weights, and it
reads as a model problem — a bad checkpoint, a torch version, a corrupted
cache — which is why it survived several passes. It is not a model problem.

The arithmetic, measured on the reference machine rather than recalled:

| | |
|---|---|
| WSL2 `.wslconfig` cap | 8192 MB |
| Usable, per `docker info` | **7587 MB** (7 956 238 336 bytes) |
| Declared `mem_limit` total, application services | 6720 MB |
| Declared `mem_limit` total, application + `obs` profile | **7712 MB** |

7712 against 7587 is an over-subscription of 125 MB before the guest kernel,
the page cache, or a single byte of burst above any cgroup limit. When the
kernel has to reclaim, it reaches for the largest RSS in the largest cgroup,
which is `worker-ml`'s forked child at exactly the moment it is materialising a
few hundred megabytes of weights.

Two things made this invisible:

1. **`docs/HARDWARE.md`'s budget table was missing two services.** `relay` and
   `webhooks` were added to `docker-compose.yml` with 192 MB each and never
   added to the table, so the document's subtotal was 384 MB below the compose
   file's. The document said the stack fit. It was reading a smaller stack.
2. **The budget was compared against 8192 MB**, the number `.wslconfig` is
   asked for, rather than 7587 MB, the number that arrives. A budget checked
   against the wrong denominator passes review and fails at runtime.

F1 named three candidate fixes and required that they be argued rather than
picked.

## Decision

**Three parts, in the order they matter.**

**1. The budget becomes executable.** `scripts/check_memory_budget.py` reads
every `mem_limit` in `docker-compose.yml`, asserts each has a row in
`docs/HARDWARE.md` with the same number, re-adds both subtotals, and asserts
two different claims about fit:

- the **application set** — what `nem up` starts, and what a demo runs on —
  must leave at least the headroom the document declares (768 MB);
- the **application set plus the `obs` profile** must fit the VM at all.

The VM size and the headroom floor are read from two comment markers in
`docs/HARDWARE.md`, so a machine with a different `.wslconfig` edits one file
and the check follows it. It runs in `nem check` and in CI's `governance` job,
on a bare interpreter, because it has to run on a machine where the stack will
not start *because* of what it is measuring.

**2. The limits are trimmed to fit, against measured RSS.** `beat`, `relay` and
`webhooks` fall from 192 MB to 128 MB (measured 77, 66 and 74 MB); the five
`obs` services fall from 992 MB to 736 MB in total. Application 6528 MB,
observability 736 MB, total 7264 MB against 7587 MB. Every trimmed limit is at
least 1.7× its measured resident size, and each measurement is recorded beside
its row.

**3. `worker-ml` keeps `--pool=prefork --concurrency=1`, and keeps its 3072 MB.**
It is the process whose OOM this budget exists to prevent, and trimming it is
trimming the symptom.

## Alternatives considered

**Raise the WSL2 allocation.** The cheapest change and the one that fixes
nothing that matters. §27.1's whole hardware argument is *"RAM, not compute, is
binding"* on a 16 GB laptop that also runs host-side Ollama with `llama3.1:8b`
resident, a browser, and an editor. Giving the VM another gigabyte takes it from
the host processes the product depends on, and it leaves the *actual* defect —
a budget document that does not describe the stack — in place, where it will
mis-state the next total too. Rejected as a fix; noted in `docs/HARDWARE.md` as
what a 32 GB machine should do.

**Lower the non-ML limits for local runs only.** A second set of limits for
"local" means the numbers CI asserts are not the numbers a developer runs, which
is the exact drift this repository refuses in `check_env_parity.py`. The limits
are lowered — but for everybody, on measured evidence, in one file.

**Move `worker-ml` to `--pool=solo`.** The most tempting: with
`--concurrency=1` the fork buys no parallelism, and removing it removes the
second copy of the weights that COW does not fully share once torch starts
writing into its arenas. It was rejected on two specific losses, both of which
are load-bearing here rather than theoretical:

- **`--max-tasks-per-child=100` stops working.** The solo pool has no child to
  recycle, so the periodic reclamation of whatever torch, tokenizers and PIL
  have fragmented never happens. On a long-lived worker that is a slow leak
  with no ceiling — trading a fast, legible OOM for a slow, mysterious one.
- **Task-level isolation goes.** A segfault in a native extension currently
  kills one child; Celery raises `WorkerLostError`, the task retries, and the
  worker keeps serving. Under `solo` the same segfault takes the worker down and
  the container with it, turning a retried task into a restart loop.

Both would be acceptable prices if the fork were the cause. It is not: with the
`obs` profile down and the corrected budget in place, `worker-ml` runs at
~2130 MB against its 3072 MB limit with CLIP resident and a fork child alive.
The fork was never the problem; the profile that pushed the VM past its size
was. Revisit if the measured figure ever approaches the limit with `obs` down.

## Consequences

- A service added to `docker-compose.yml` without a row in `docs/HARDWARE.md`
  now fails the build. This is deliberate friction on exactly the action that
  caused this defect.
- `docs/HARDWARE.md` becomes load-bearing: it is no longer only documentation,
  and editing its numbers changes what CI enforces. The two comment markers make
  that explicit at the point of edit.
- The `obs` profile now runs with 323 MB of VM headroom rather than −125 MB. It
  is still the profile to turn off first, and the document says so.
- `nem doctor` reports the VM's actual size against the documented figure, so a
  machine configured smaller than the budget assumes says so at setup time
  rather than as a SIGKILL two minutes into a demo.

## Revisit when

- `docker info` reports a usable VM materially different from 7587 MB — a new
  machine, or a `.wslconfig` change. Update the marker; the check follows.
- `worker-ml`'s measured RSS with `obs` down passes ~2600 MB, at which point the
  fork's second copy of the weights *is* the binding constraint and `--pool=solo`
  becomes the argument it currently is not.
- Whisper and e5 become simultaneously resident with CLIP in normal operation
  rather than only in the F1 evaluation runs.
