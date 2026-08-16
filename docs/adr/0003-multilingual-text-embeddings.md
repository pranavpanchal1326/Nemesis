# 0003 — Multilingual text embeddings instead of the blueprint's MiniLM

- **Status:** Accepted
- **Date:** 2026-08-16
- **Owner:** DATA
- **Blueprint:** §8.4, §14.1

## Context

Blueprint §8.4 specifies `all-MiniLM-L6-v2` (384-dim) for the text embeddings
that drive Stage 2 of deduplication (§14.1).

The same section requires `faster-whisper` transcription of **Hindi and Marathi**
voice complaints.

These two requirements are in direct conflict, and the conflict is silent.
`all-MiniLM-L6-v2` is trained on English. Given Devanagari input it does not
error — it returns a vector. That vector carries little usable semantic signal,
so cosine similarity between two Hindi complaints describing the same pothole
would land near the similarity of two unrelated ones.

The failure mode is the dangerous kind: dedup Stage 2 quietly stops working for
non-English reports. Because Stage 1 (PostGIS radius) still fires, the system
appears to function. The users who lose deduplication are precisely the
non-English-speaking citizens the product exists to serve — and §23's equity
safeguards would be undermined by the very pipeline meant to enforce them.

## Decision

Use **`intfloat/multilingual-e5-small`** for text embeddings.

- 384 dimensions — **identical to MiniLM**, so the `vector(384)` column, the
  HNSW index, and every downstream contract are unchanged.
- Trained across 100+ languages including Hindi and Marathi.
- e5 models require asymmetric prefixes (`query: ` / `passage: `); omitting them
  measurably degrades retrieval, so the prefix is part of the configuration
  rather than an implementation detail.

## Alternatives considered

**Keep MiniLM, translate to English first.** Rejected: adds a translation model
to the latency budget and to the failure surface, and translation error
compounds into the similarity score.

**`bge-m3`** (already present on the developer's machine). Rejected for now:
1024 dimensions, changing the schema, index size, and memory profile for a
quality gain not yet demonstrated at this data scale. A strong candidate to
re-evaluate once Phase 11 can measure it.

**Ship MiniLM and document the limitation.** Rejected: §6.8 requires honesty
about what is built, not shipping a component known to fail for a target user
group.

## Consequences

- No schema change, no migration, no downstream contract change.
- Hindi and Marathi complaints deduplicate correctly.
- Slightly larger model than MiniLM (~118 M parameters), well inside the
  `worker-ml` memory budget.
- A guardrail test asserts the configured model is multilingual, so a future
  "optimisation" back to MiniLM fails CI rather than silently regressing.

## Revisit when

Phase 11's evaluation harness can measure embedding quality per language against
real labelled duplicates. At that point `bge-m3` and larger e5 variants should be
compared on evidence rather than reasoning.
