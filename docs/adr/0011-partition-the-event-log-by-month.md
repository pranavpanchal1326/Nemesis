# 0011 — The event log is range-partitioned by month from the first migration

- **Status:** Accepted
- **Date:** 2026-08-16
- **Owner:** PLT
- **Blueprint:** §9.1, §22.4

## Context

`events` is append-only, is the system of record, and must live for years —
Phase 21 advertises a 12-month replay and §22.4 defines a retention schedule.
It is also the highest-volume table in the system by a wide margin.

Converting a large append-only table to a partitioned one later means a full
table rewrite under lock. Doing it in the first migration costs one design
decision and no data movement. The decision is therefore *when*, not *whether*,
and "when" only has one cheap answer.

## Decision

`events` is `PARTITION BY RANGE (recorded_at)`, one partition per calendar
month, plus a `DEFAULT` partition. The first migration creates a five-month
window; `nemesis.events.partitions.ensure_partitions` keeps three months ahead
on a daily schedule.

Partitioning on `recorded_at` (system time) rather than `occurred_at` (business
time), because an offline field submission (Phase 22) can occur months before it
is recorded, and partitioning on a value the writer does not control would
scatter one batch across arbitrary partitions.

**A `DEFAULT` partition always exists.** Without it, an insert matching no
declared range is *rejected* — so a maintenance job that failed to run would
become a total outage of the write path of an append-only log, with no way to
record that it happened. The default converts that into a warning
(`NemesisEventDefaultPartitionNonEmpty`) and a performance problem.

## Consequence: three companion tables

Postgres requires the partition key in every unique constraint on a partitioned
table. This has consequences that are not obvious until you hit them, and they
are the real cost of this decision:

`UNIQUE (tenant_id, entity_type, entity_id, sequence)` **cannot be enforced.** It
would have to include `recorded_at`, and would then permit a duplicate sequence
as long as the two rows landed in different months. Since per-entity ordering
*is* the tamper-evidence property, it cannot be left to convention:

- **`event_chain_heads`** (unpartitioned, one row per entity) holds the tail hash
  and sequence, and is the row `EventStore.append` locks. This turned out to be
  better than the alternative anyway: a primary-key lookup instead of an ordered
  `MAX(sequence)` scan across every monthly partition.
- **`event_idempotency`** (unpartitioned, PK `(tenant_id, idempotency_key)`)
  enforces redelivery-is-a-no-op. A unique index inside `events` would be
  per-partition, so a task retried across a month boundary would silently append
  a second copy.
- **`archived_partitions`** records what was detached and when. Without it, "the
  event is not in the table" is indistinguishable from "the event never existed"
  — the exact ambiguity an append-only log exists to eliminate.

Foreign keys **into** `events` are also impossible; `causation_event_id` is
therefore an unenforced reference, checked by the integrity sweep rather than by
the database.

## Alternatives considered

**Single table, partition later.** Rejected: "later" means a rewrite under lock
on the largest table in the system, which in practice means never, which means
the log degrades as the product succeeds.

**Partition by tenant.** Rejected: tenant count is unbounded and unknown at
schema time, retention is calendar-based not customer-based, and it would make
the cross-tenant integrity sweep scan every partition. Tenant isolation is
enforced by the guard and by RLS in Phase 25 — partitioning is the wrong tool
for it.

**`pg_partman`.** Rejected for now: a 60-line maintenance task with a runbook is
less operational surface than an extension that must be present in every
environment, and the logic is unlikely to grow. Reconsider if retention
automation gets meaningfully more complex.

## Consequences

- Retention becomes `DETACH PARTITION` — instant, and it does not fight
  autovacuum the way a bulk `DELETE` from a hot table does.
- The primary key is `(id, recorded_at)`. `id` alone is still unique in practice
  (one sequence feeds it); the composite exists to satisfy the partitioning rule.
- Queries that do not constrain `recorded_at` scan every partition. The
  idempotency lookup therefore carries `recorded_at` explicitly, and
  `event_idempotency` stores it for that purpose.
- Alembic autogenerate sees partitions as unknown tables and wants to drop them;
  `alembic/env.py` filters them by name pattern. Without that filter,
  `alembic check` reports permanent drift, which is worse than no check because
  it trains people to ignore it.
- **The default partition must stay empty.** Attaching a partition over existing
  default rows needs a full scan and an `ACCESS EXCLUSIVE` lock. This is why
  maintenance runs three months ahead, why the row count is a gauge, and why
  `ensure_partitions` refuses to attach over a non-empty default rather than
  stalling the write path with a scan nobody scheduled.

## Revisit when

- Monthly partitions exceed roughly 50 GB, at which point weekly is worth
  measuring.
- A deployment needs partitions on a different boundary for a data-residency or
  legal-hold reason (Phase 26).
