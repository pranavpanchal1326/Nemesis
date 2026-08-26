# 0054 — A pool child that loads models is given time to say it is alive

- **Status:** Accepted
- **Date:** 2026-08-25
- **Owner:** SRE
- **Blueprint:** §27.1; ADR-0004; `docs/FRONTEND-PHASE-PLAN.md` F1

## Context

`worker-ml` enters a loop that reads exactly like an out-of-memory kill, and
ADR-0051 diagnosed it as one. Every iteration is three lines:

```
[WARNING/ForkPoolWorker-231] trust_stages_registered  face_detector=True …
[ERROR/MainProcess] Timed out waiting for UP message from <ForkProcess(…)>
[ERROR/MainProcess] Process 'ForkPoolWorker-231' pid:7281 exited with 'signal 9 (SIGKILL)'
```

A `SIGKILL` on a process that had just been materialising a few hundred
megabytes of CLIP weights is a very good OOM impression, and the arithmetic
ADR-0051 found was real: the compose file did over-subscribe the VM by 125 MB,
`docs/HARDWARE.md` was missing two services, and the budget was being compared
against the wrong denominator. **That work stands.** The budget is now
executable and the document is load-bearing, which is worth having on its own.

It is not what was killing the child.

The measurement that separates the two was taken while the loop was running:

| | |
|---|---|
| `worker-ml` resident | **821 MiB** against a 3 GiB `mem_limit` |
| All application containers | ~1.8 GiB against a 7.5 GiB VM |
| VM headroom at the moment of the SIGKILL | ~5.7 GiB |

Nothing is out of memory. The killer is named in the line above the kill:
billiard's parent process gives a new pool child **`worker_proc_alive_timeout`
seconds** to report UP, and the default is **4.0**. The child logs
`trust_stages_registered` about two seconds in and is still importing torch,
CLIP and mediapipe when the parent gives up and terminates it. The replacement
child inherits the same four seconds and the same imports, so the loop does not
converge — and because `task_acks_late` is on, the task it was killed under is
redelivered into the same loop.

Two things made this look like memory rather than time:

1. **It is load-dependent, not state-dependent.** On an idle machine the import
   finishes inside four seconds and the worker runs for hours. Under a browser
   suite — a Next dev server, Chromium, and a Playwright runner on the host —
   it does not. The same commit therefore passes when run alone and fails when
   run beside the tests that need it, which is the signature everyone reads as
   memory pressure.
2. **`--max-tasks-per-child=100` makes a fresh child routine.** A window that
   would be entered twice a day is entered every hundred tasks, so a demo or a
   seeded backlog walks straight into it.

## Decision

**`worker_proc_alive_timeout = 120.0`,** set in `nemesis/worker/celery_app.py`
beside the other pool settings, with the measurement above recorded next to it.

120 seconds, matching this service's compose `start_period`, so the two numbers
that describe "how long this worker may take to become useful" agree. It is long
by the standards of a healthy fork, deliberately: **the cost of waiting too long
is a slow start somebody can see, and the cost of waiting too little is a worker
that never serves a task and reports the wrong reason for it.** A start that
genuinely hangs is still caught — by the healthcheck, which is the mechanism for
that, rather than by a pool timeout that cannot tell a hang from an import.

**ADR-0051 is not superseded.** Its budget work is independently correct and its
checked artefact stays. What is superseded is its account of *this* symptom, and
that account is amended in place rather than deleted, because "we thought it was
memory, and here is the measurement that said otherwise" is the more useful half
of the record.

## Alternatives considered

**Leave it, and treat the loop as a machine problem.** The tempting reading:
the laptop is small, the models are large, buy more RAM. It is wrong on the
evidence — 821 MiB against 3 GiB is not a machine problem — and acting on it
would have meant a hardware purchase that changed nothing, which is the specific
waste an ADR is supposed to prevent.

**Raise `--max-tasks-per-child` so children are recycled less often.** Reduces
how often the window is entered without closing it, and pays for that with the
thing the recycling buys: periodic reclamation of whatever torch, tokenizers and
PIL have fragmented. Trading a leak for a slightly rarer restart loop is a bad
trade in both directions.

**Move to `--pool=solo`, removing the fork.** Rejected in ADR-0051 on two
losses — `--max-tasks-per-child` stops working, and task-level isolation goes —
and both still hold. It is now also unnecessary: with no fork there is no UP
message, so it would fix this symptom by deleting the mechanism rather than by
configuring it, which is how a repository ends up unable to explain its own
worker topology.

**Preload the models at import time in the parent, before the fork.** The
genuinely interesting alternative, and the one to revisit: a parent that has
CLIP resident forks children that inherit it copy-on-write, so a child is alive
almost immediately. It is rejected *now* rather than on principle, because it
changes what a fork costs in memory in a way that needs measuring against
ADR-0051's budget, and this ADR exists to stop a symptom being fixed by a change
nobody has measured.

## Consequences

- A pool child that is genuinely stuck now takes two minutes to be noticed by
  the pool rather than four seconds. The healthcheck's `start_period` is the
  same 120 s, so the container-level signal is unchanged.
- The M5 pipeline gate — *"the theatre's stamps come from the log, not from a
  timer"* — becomes runnable beside the rest of the browser suite rather than
  only on a quiet machine. F1 asked for this failure to be *"fixed or formally
  accepted"*; this is the fixed half, and ADR-0051 was the accepted half of a
  different problem found on the way.
- Anyone reading ADR-0051 alone would still believe the memory account. The
  cross-reference in both directions is therefore part of this decision, not
  housekeeping.

## Revisit when

- A child is observed timing out at 120 s. That is a hang and not a slow import,
  and it wants a different answer from a larger number.
- The parent-side preload above is measured against the memory budget. If a
  forked child inherits resident weights cheaply, the timeout can come back down
  and this ADR becomes the reason it was ever raised.
- `--max-tasks-per-child` is removed or the pool changes, either of which makes
  the UP window rare enough to re-examine.
