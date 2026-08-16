# 0001 — One Postgres for relational, geospatial, vector, and the event log

- **Status:** Accepted
- **Date:** 2026-08-16
- **Owner:** PLT
- **Blueprint:** §7.3, §8.3, §14

## Context

NEMESIS needs four storage capabilities: relational records, geospatial radius
queries (§14.1 Stage 1), vector similarity (§14.1 Stage 2), and an append-only
hash-chained event log (§9). The obvious industry reflex is a specialised store
for each — Postgres, PostGIS, a dedicated vector database, and an event store.

The constraint that makes this non-obvious: the system must run entirely offline
on a single 16 GB laptop (§6.6), and the deduplication engine needs a geo filter
and a vector filter **in the same query path**, in under 10 seconds (§27.1).

## Decision

A single PostgreSQL 17 instance carrying PostGIS, pgvector, `pgcrypto`, and
`pg_trgm`, holding all four concerns.

pgvector 0.8 specifically, for iterative index scans — which return correct
results when a filtered HNSW search under-returns, the exact situation Stage 2
creates by restricting to Stage 1 candidates.

## Alternatives considered

**A dedicated vector database (Qdrant / Weaviate / Pinecone).** Rejected: at
well under 1 M vectors, pgvector with HNSW is competitive, and a second store
means a second thing to sync, back up, secure, and keep consistent with the
event log. Cross-store consistency is the failure mode, and it has no cheap fix.

**A purpose-built event store (EventStoreDB).** Rejected: the hash chain is
~30 lines against a Postgres table, and keeping events in the same database as
projections lets a state change and its event commit in **one transaction** —
the §9.1 invariant this entire architecture rests on. Across two stores, that
invariant needs distributed transactions or an outbox for something that did not
need to be distributed.

**Separate Postgres instances per concern.** Rejected: same consistency problem,
four times the operational surface, no benefit at this scale.

## Consequences

- One backup strategy, one connection pool, one thing that can fail.
- Stage 1 and Stage 2 of dedup join in a single query plan — no network hop
  between a geo filter and a vector filter.
- A state change and its event row are atomic without an outbox.
- Vertical scaling only. Read replicas and partitioning are the escape path.
- Requires a custom Postgres image, since no official image ships both PostGIS
  and pgvector. Built in `infra/postgres/`.

## Revisit when

Vector count approaches ~10 M per tenant, or `p95` dedup latency approaches the
§27.1 10-second budget on representative hardware. Measure before splitting.
