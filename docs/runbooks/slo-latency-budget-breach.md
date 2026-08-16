# §27.1 latency budget breached

- **Severity:** warning
- **Owner:** DATA (inference stages) · PLT (transport stages)
- **Alerts:** `NemesisClassificationLatencyBudgetBreached`,
  `NemesisDedupLatencyBudgetBreached`, `NemesisEndToEndBudgetBreached`

> Read the **per-stage** panel before anything else. End-to-end latency is a sum;
> the stage panel says which term grew. Acting on the sum means guessing, and the
> guess is usually "the model is slow" when the actual answer is a query plan.

## Symptoms

One of the §27.1 budgets has been exceeded at p95 for ten minutes:

| Stage | Budget | Alert |
|---|---|---|
| Submission acknowledgment | 2 s | (API panel) |
| Safety check | 5 s p99 | see [safety-path-degraded.md](safety-path-degraded.md) |
| Classification | 8 s | `NemesisClassificationLatencyBudgetBreached` |
| Dedup decision | 10 s | `NemesisDedupLatencyBudgetBreached` |
| End-to-end, no agent | 30 s | `NemesisEndToEndBudgetBreached` |
| End-to-end with agent | 90 s | `NemesisAgentInvestigationBudgetBreached` |

## How to confirm

Open **NEMESIS — §41 Product Health KPIs** → *Pipeline latency against §27.1
budgets*. The budgets are drawn as dashed lines, so a breach is visible without
holding a number in your head.

Then take a trace. This is what the collector and Tempo exist for, and the
exemplar link on the latency panels goes straight to a slow request:

```bash
curl -s 'localhost:3200/api/search?tags=service.name%3Dnemesis-api&limit=5'
```

A trace answers "which span grew" in one look. A metric can only tell you that
something did.

## Immediate mitigation

**Classification (8 s)** — CPU-bound CLIP inference, ~150 ms per image on this
CPU when healthy. An order of magnitude above that means one of:
- Model weights were evicted and are reloading. `worker-ml` runs
  `--max-tasks-per-child=100`; each child restart pays a cold load.
- CPU contention with host-side Ollama. `torch_num_threads` is capped at 4
  below core count precisely so inference cannot starve Postgres and Redis;
  confirm it has not been raised.
- `worker-ml` at its 3 GB limit and swapping.

```bash
docker stats --no-stream
docker compose logs --tail=100 worker-ml | grep -i -E 'load|oom|evict'
```

**Dedup (10 s)** — Stage 1 must eliminate ≥ 90 % of candidates before any
embedding comparison. If it is not, this is a query-plan problem, not a
throughput problem, and adding workers will make it worse rather than better:

```bash
docker compose exec postgres psql -U nemesis -d nemesis \
  -c "EXPLAIN (ANALYZE, BUFFERS) <the Stage 1 query>;"
```

Look for a sequential scan where a GiST index should be used.

**End-to-end (30 s)** — if no individual stage is over budget but the sum is,
the time is being spent *between* stages: queue wait, not work. Check queue
depth (`redis-cli llen io`) and worker concurrency.

## Root cause investigation

- **Cold model cache after a restart.** Transient by definition. Confirm it is
  recovering before investigating further — ten minutes of `for:` should have
  covered a cold start, but a restart loop would not be covered.
- **Memory pressure.** Everything on this machine is RAM-bound, not CPU-bound.
  This is the first hypothesis, not the last.
- **A lost index.** After a migration, after a restore, after a `nem nuke`.
- **Genuine volume growth.** The honest answer sometimes. Phase 28 produces the
  capacity model that turns this from a judgement call into a number.

## Prevention

- Phase 9's gate requires inference latency within the §27.1 budget on this
  hardware, *measured not estimated*.
- Phase 10's gate requires Stage 1 to eliminate ≥ 90 % of candidates, verified by
  query plan rather than by assertion.
- Phase 28 asserts every §27.1 budget as a `k6` CI threshold, at which point a
  regression fails a build instead of firing an alert.
