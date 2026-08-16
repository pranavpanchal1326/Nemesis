# Database connection pool exhausted

- **Severity:** critical
- **Owner:** PLT (pool sizing) · SRE (database)
- **Alerts:** `NemesisDatabasePoolExhausted`, `NemesisDependencyDown`

**Dependency:** database

**Blueprint scenario:** Database connection pool exhausted

> §27.3 is explicit that **no data is lost** in this scenario: new submissions
> get a 503 with `Retry-After`, the client holds them in a visible "retrying"
> state, and the Celery queue drains against the same pool once connections
> free up. Your job is to free connections, not to rescue data. Confirm the
> first claim before assuming the second.

## Symptoms

- `/ready` returning 503; `nemesis_dependency_up{dependency="database"}` at 0.
- 5xx ratio climbing on write endpoints while reads still work — the read path
  gives connections back faster.
- Celery tasks retrying with `TimeoutError` acquiring a connection.
- Postgres logs showing `FATAL: sorry, too many clients already`.

## How to confirm

```bash
docker compose exec postgres psql -U nemesis -d nemesis -c \
  "SELECT state, count(*), max(now()-state_change) AS oldest
     FROM pg_stat_activity WHERE datname='nemesis' GROUP BY state ORDER BY 2 DESC;"
```

Read the `idle in transaction` row first. A large count there is not a capacity
problem — it is a **leaked transaction**, and adding connections would only
delay the same failure while making the eventual one larger.

Current ceilings: `max_connections=60` (compose), `database_pool_size=10` plus
`database_max_overflow=5` per process, across `api` + `worker-io` (concurrency
4) + `worker-ml` + `beat`.

## Immediate mitigation

1. **If `idle in transaction` dominates**, find the offender before killing
   anything:

   ```bash
   docker compose exec postgres psql -U nemesis -d nemesis -c \
     "SELECT pid, now()-state_change AS age, left(query,120)
        FROM pg_stat_activity
       WHERE state='idle in transaction' ORDER BY age DESC LIMIT 10;"
   ```

   Terminate only sessions idle beyond a few minutes:

   ```bash
   docker compose exec postgres psql -U nemesis -d nemesis -c \
     "SELECT pg_terminate_backend(pid) FROM pg_stat_activity
       WHERE state='idle in transaction' AND now()-state_change > interval '5 minutes';"
   ```

2. **If genuinely saturated**, shed load at the edge rather than raising limits
   under pressure. Restarting `worker-io` returns its connections immediately
   and is safe: `task_acks_late` plus `task_reject_on_worker_lost` mean in-flight
   tasks are redelivered, and every task is idempotent by contract.

   ```bash
   docker compose restart worker-io
   ```

3. **Do not raise `max_connections` during the incident.** Each Postgres backend
   costs memory, and this database is capped at 1536 MB on a machine that is also
   running Ollama. Trading a connection error for an OOM kill is a downgrade.

## Root cause investigation

- **A leaked transaction** — a `session_scope()` that did not exit, or an
  exception path that skipped the context manager. This is the most likely cause
  and the only one where the fix is code.
- **Sizing arithmetic**: `worker-io` at concurrency 4 with pool 10 + overflow 5
  can alone reach 60 connections. Recompute against `max_connections` before
  changing any concurrency setting.
- **A long-running analytical query** holding connections. Phase 23 moves
  analytics onto a separate store precisely so this cannot happen.
- **Health checks with too short an interval** creating and discarding
  connections faster than the pool recycles them.

## Prevention

- `pool_pre_ping` is on; connection *recycling* limits are a Phase 28 capacity
  item and should not be tuned reactively.
- Phase 25 exercises pool exhaustion as an automated fault-injection test.
- Phase 28 produces a documented capacity model, which is what turns these
  numbers from "what fit on a laptop" into a stated limit per tier.
