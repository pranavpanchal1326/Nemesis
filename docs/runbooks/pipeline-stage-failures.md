# Pipeline stage failing

- **Severity:** warning
- **Owner:** DATA · PLT
- **Alerts:** `NemesisPipelineStageFailing`

> §24.2 requires a failing stage to **degrade the complaint's status, never lose
> it**. The first job on this page is not to fix the stage — it is to confirm the
> affected complaints are in a recoverable state. A stage that fails loudly and
> preserves work is a very different incident from one that fails loudly and
> drops it, and they look identical on the alert.

## Symptoms

- `nemesis:pipeline_stage_failure_ratio > 0.02` for one stage over ten minutes.
- Complaints stuck in an intermediate status rather than progressing.
- Celery retries climbing; dead-letter volume rising.

## How to confirm

```bash
curl -s 'localhost:9090/api/v1/query?query=nemesis:pipeline_stage_failure_ratio' | python -m json.tool
```

Note the `outcome` label distinction, because it decides whether this is an
incident at all:

- `outcome="degraded"` — the stage took its documented fallback path. It
  **succeeded at its contract** and does not count toward this ratio.
- `outcome="failed"` — the stage did not complete and did not fall back. Only
  this counts.

If the ratio is driven by `degraded`, the metric is being computed wrongly, not
the pipeline.

## Immediate mitigation

1. **Establish the blast radius first** — how many complaints are affected and
   what state are they in. Until Phase 2's event store lands, this means reading
   worker logs by correlation ID; from Phase 2 it is a query against the event log.

2. **Identify the stage** and route accordingly:
   - `safety_check` → [safety-path-degraded.md](safety-path-degraded.md). Highest priority.
   - `classification` / `dedup` → [slo-latency-budget-breach.md](slo-latency-budget-breach.md) if the failures are timeouts.
   - `agent_investigation` → [ollama-unreachable.md](ollama-unreachable.md).

3. **If a stage is failing consistently rather than intermittently**, and it is
   behind a flag, kill it and let the documented fallback take over. A stage
   failing 100 % of the time is retrying at full cost for zero benefit and is
   consuming worker capacity that other stages need.

4. **Do not purge the queue.** Every task is idempotent and redelivery is a
   provable no-op; the queue is the record of work not yet done. Purging it
   converts a recoverable incident into data loss, which is precisely the
   outcome §24.2 is written to prevent.

## Root cause investigation

- **A downstream dependency failing**, with the stage correctly reporting it. The
  stage is the messenger.
- **A malformed payload** that boundary validation should have rejected earlier.
  If a task payload is failing validation *inside* the task, the validation is in
  the wrong place.
- **A retry budget too small for the actual failure duration**, so work exhausts
  its retries during a transient outage and lands in the dead-letter queue.
- **A non-idempotent task.** Redelivery is supposed to be safe. If retries are
  producing duplicate side effects, this is a correctness bug and outranks the
  latency symptom that surfaced it.

## Prevention

- Phase 3's gate requires that `SIGKILL` mid-pipeline loses nothing on restart —
  the durable answer to "is work preserved".
- Every ingest and task carries an idempotency key by engineering standard, with
  redelivery provably a no-op.
- Phase 25 exercises every degradation path with `toxiproxy` rather than
  assuming it.
