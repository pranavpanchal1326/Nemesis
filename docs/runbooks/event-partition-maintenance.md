# Runbook — Event partition maintenance

**Alert:** `NemesisEventDefaultPartitionNonEmpty`
**Severity:** warning
**Owning function:** PLT (Platform Engineering)
**Blueprint:** §22.4 retention

---

## Symptoms

- `NemesisEventDefaultPartitionNonEmpty` firing —
  `nemesis_event_default_partition_rows` is above zero.
- `event_default_partition_non_empty` or `event_partition_attach_blocked` in the
  `worker-io` log.
- Gradually slower queries against `events` that do not constrain `recorded_at`.

**Nothing is broken and no data is lost.** That is the entire purpose of the
default partition: without it, an insert matching no declared range is
*rejected*, and the write path of an append-only log fails completely — with no
way to record that it happened. Converting that outage into a warning is a trade
this design makes deliberately (ADR-0011).

It still needs attention, because attaching a partition for a range that already
contains default rows requires Postgres to scan `events_default` and take an
`ACCESS EXCLUSIVE` lock on the parent. On a hot append-only table that stalls
every writer for the duration, and **the cost grows every day the rows
accumulate.**

## How to confirm

```bash
docker compose exec postgres psql -U nemesis -d nemesis -c "SELECT count(*), min(recorded_at), max(recorded_at) FROM ONLY events_default"
```

List what exists now:

```bash
docker compose exec postgres psql -U nemesis -d nemesis -c "SELECT c.relname, pg_get_expr(c.relpartbound, c.oid) FROM pg_class c JOIN pg_inherits i ON i.inhrelid = c.oid WHERE i.inhparent = 'events'::regclass ORDER BY 1"
```

Then find out why maintenance did not run. The daily task keeps a three-month
window, so this needs roughly twelve weeks of missed runs to happen at all:

```bash
docker compose logs beat | grep -i partition | tail -20
docker compose logs worker-io | grep event_partition | tail -20
```

`event_partition_attach_blocked` means the task ran, found rows in the default
for the range it wanted to create, and **refused** rather than blocking the
write path with a scan it did not schedule. That refusal is the task working
correctly; it is handing you the decision about when to take the lock.

## Immediate mitigation

**First, restore the window** so the problem stops growing. Future months are
empty in `DEFAULT`, so these attach instantly and take no meaningful lock:

```bash
docker compose exec worker-io python -c "import asyncio; from nemesis.pipeline.integrity import _maintain_event_partitions; print(asyncio.run(_maintain_event_partitions()))"
```

That alone stops the bleeding. Moving the stranded rows can wait for a
low-traffic window — see below.

## Root cause investigation

The cause is almost always that `beat` stopped running, not partitioning itself.
Confirm against `NemesisEventIntegritySweepStalled`, which shares that cause: if
both are firing, the scheduler is the problem and partitions are a symptom.

**Then move the stranded rows.** This takes an exclusive lock — pick a
low-traffic window. The safe sequence, in one transaction:

```sql
BEGIN;

-- 1. Detach the default so writes to it stop mid-operation.
ALTER TABLE events DETACH PARTITION events_default;

-- 2. Create the month that should have held the rows.
CREATE TABLE events_2026_09 PARTITION OF events
    FOR VALUES FROM ('2026-09-01 00:00:00+00') TO ('2026-10-01 00:00:00+00');

-- 3. Move them. INSERT ... SELECT rather than ATTACH, because the detached
--    table may span several months and only some are being reclaimed.
INSERT INTO events
SELECT * FROM events_default
WHERE recorded_at >= '2026-09-01 00:00:00+00' AND recorded_at < '2026-10-01 00:00:00+00';

DELETE FROM events_default
WHERE recorded_at >= '2026-09-01 00:00:00+00' AND recorded_at < '2026-10-01 00:00:00+00';

-- 4. Put the safety net back. Never leave this step out.
ALTER TABLE events ATTACH PARTITION events_default DEFAULT;

COMMIT;
```

Repeat steps 2–3 per affected month. **Step 4 is not optional** — running
without a default partition means the next gap in the window is a write outage
instead of a warning.

### Verify

```bash
docker compose exec postgres psql -U nemesis -d nemesis -c "SELECT count(*) FROM ONLY events_default"
```

Then confirm the chain is still intact, because rows were physically moved:

```bash
docker compose exec api python -m nemesis.events.inspect --sweep --limit 2000
```

`chains broken` must be `0`. An `INSERT ... SELECT` preserves every column, so a
break here means the copy was not faithful — see
[`event-chain-integrity.md`](event-chain-integrity.md), and **do not repair it**.

### Retention and archival (§22.4)

Partitions older than `RETENTION_MONTHS` (12, matching what Phase 21's replay
advertises) are reported as eligible for archival by the daily task and are
**never detached automatically**. Retention on an append-only civic record is a
decision with legal weight; Phase 26 adds the approval step and the proof of
deletion that make acting on it defensible.

When that decision is made:

```sql
ALTER TABLE events DETACH PARTITION events_2025_08;
```

Then record it in `archived_partitions` with the row count, destination, and
digest. Without that record, "the event is not in the table" is
indistinguishable from "the event never existed" — precisely the ambiguity an
append-only log exists to eliminate.

## Prevention

- The daily task keeps three months ahead. If it is being missed for a quarter
  at a time, fix `beat`, not partitioning.
- The first migration creates a five-month window, so a freshly provisioned
  environment that is never maintained still has months of headroom.
- Never drop the default partition "because it should always be empty". Empty is
  the goal; present is the safety net.

## Related

- [`docs/adr/0011-partition-the-event-log-by-month.md`](../adr/0011-partition-the-event-log-by-month.md)
- [`event-chain-integrity.md`](event-chain-integrity.md)
