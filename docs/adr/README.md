# Architecture Decision Records

An ADR records a decision that was **not obvious at the time it was made**, so
that six months later nobody has to reverse-engineer the reasoning from the code
— or, worse, silently undo it.

## When to write one

Write an ADR when a choice:
- constrains future work (a datastore, a tenancy model, a protocol),
- was contested, or had a credible alternative that was rejected,
- trades one desirable property for another,
- or would look like a mistake to someone who lacks the context.

Do **not** write one for choices with a single obvious answer. An ADR corpus
padded with formalities is one nobody reads.

## Status values

| Status | Meaning |
|---|---|
| `Proposed` | Under discussion, not yet binding |
| `Accepted` | Binding. Code is expected to comply |
| `Superseded by NNNN` | Replaced. Kept for the reasoning trail, never deleted |
| `Deprecated` | No longer applies, with no direct replacement |

Superseded ADRs are never removed. The record of *why we changed our minds* is
usually more valuable than the decision itself.

## Template

```markdown
# NNNN — <short decision title>

- **Status:** Proposed | Accepted | Superseded by NNNN | Deprecated
- **Date:** YYYY-MM-DD
- **Owner:** <function code — PLT / DATA / PROD / SEC / SRE / BIZ>
- **Blueprint:** §<section>

## Context
What forced a decision. Include the constraint that made it non-obvious.

## Decision
What we are doing, stated so compliance is checkable.

## Alternatives considered
Each with the reason it was rejected. An ADR with no rejected alternative is
documentation, not a decision record.

## Consequences
What this makes easy, what it makes hard, and what it commits us to.

## Revisit when
The concrete signal that should reopen this decision.
```

## Index

| # | Decision | Status | Owner |
|---|---|---|---|
| [0001](0001-single-postgres-for-relational-geospatial-vector.md) | One Postgres for relational, geospatial, vector, and the event log | Accepted | PLT |
| [0002](0002-cpu-inference-gpu-reserved-for-ollama.md) | CPU-only inference; the GPU is reserved for Ollama | Accepted | DATA |
| [0003-multilingual-text-embeddings.md](0003-multilingual-text-embeddings.md) | Multilingual text embeddings over the blueprint's MiniLM | Accepted | DATA |
| [0004](0004-celery-worker-split-by-memory-profile.md) | Split Celery workers by memory profile | Accepted | PLT |
| [0005](0005-explicit-container-environment-over-yaml-merge.md) | Explicit container environment, never YAML merge inheritance | Accepted | SRE |
| [0006](0006-configuration-as-data-not-code.md) | Domain configuration is tenant-scoped data, never code | Accepted | PLT |
| [0007](0007-observability-as-an-opt-in-compose-profile.md) | Observability runs as an opt-in compose profile | Accepted | SRE |
| [0008](0008-prometheus-scrapes-the-app-collector-carries-traces.md) | Prometheus scrapes the app; the collector carries traces only | Accepted | SRE |
| [0009](0009-feature-flags-mutate-by-cli-not-api.md) | Feature flags mutate through the CLI, not an HTTP API | Accepted | SRE |
