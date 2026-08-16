# Safety fail-safe path degraded

- **Severity:** critical
- **Owner:** DATA · SEC
- **Alerts:** `NemesisSafetyCheckLatencyBudgetBreached`

> This is the highest-priority latency alert in the system and it has a much
> shorter `for:` than its siblings. §11.2's deterministic fail-safe only means
> anything if it is **fast**: a safety bypass that arrives after the complaint
> has already been scored and routed has bypassed nothing. Treat a slow safety
> path as a broken safety path.

## Symptoms

- `nemesis:kpi_safety_check_latency_p99_seconds > 5` for two minutes.
- Safety-flagged complaints appearing in the normal queue rather than bypassing it.
- `safety` queue depth growing while `io` and `ml` drain normally — or, worse,
  the reverse, which means safety work is being routed to the wrong queue.

## How to confirm

```bash
docker compose exec redis redis-cli llen safety
docker compose exec redis redis-cli llen ml
docker compose exec worker-io celery -A nemesis.worker.celery_app:celery_app inspect active_queues
```

The last command is the one that matters. `worker-io` must serve `io,safety`
and `worker-ml` must serve `ml` only. The whole point of the three-queue
topology is that a backlog of routine classification work can never delay a
danger signal — if a worker is serving both `ml` and `safety`, that guarantee is
gone and the queue split has been silently defeated.

## Immediate mitigation

1. **Confirm the queue topology first.** If `safety` is being served by a worker
   that also serves `ml`, restore the split before anything else:

   ```bash
   docker compose restart worker-io worker-ml
   ```

   The `--queues` arguments are set in `docker-compose.yml`; a drifted override
   is the most likely cause and the fastest fix.

2. **If `worker-io` is saturated**, the safety path is competing with I/O work
   at concurrency 4. Raising concurrency is the wrong reflex on a RAM-bound
   machine. Shed the competing load instead — pausing the `io` queue leaves
   `safety` served:

   ```bash
   docker compose exec worker-io celery -A nemesis.worker.celery_app:celery_app control cancel_consumer io
   ```

   Restore with `add_consumer io` once latency recovers.

3. **Do not disable the safety check to relieve latency.** There is deliberately
   no kill switch for it. §41.1 sets the safety fail-safe false-negative rate at
   a hard 0 % — "not a target range" — and a disabled check is a 100 % false-
   negative rate. If the safety path cannot run, the correct degradation is to
   stop accepting submissions, not to accept them unchecked.

## Root cause investigation

- **Queue misrouting.** A task decorated onto the wrong queue, or a `--queues`
  argument edited during unrelated work. Most likely cause.
- **`worker-io` saturation** from a burst of I/O work.
- **A slow ruleset.** From Phase 6 the safety ruleset is hot-reloadable data; a
  pathological rule (a catastrophically backtracking regex) would show here
  first. The ruleset stays deterministic, but deterministic does not mean fast.
- **Prefetching.** `worker_prefetch_multiplier=1` exists so a worker cannot hold
  a queue of `io` tasks ahead of a `safety` task. Confirm it has not been raised.

## Prevention

- Phase 8's gate requires that safety-queue latency is unaffected by a saturated
  `ml` queue, *proven under load* — that gate is the durable fix.
- Phase 8 also requires the safety bypass to provably fire **before** any scoring
  stage, enforced in the state machine rather than by ordering convention.
- Any change to queue routing should be treated as a change to a safety control,
  and reviewed as one.
