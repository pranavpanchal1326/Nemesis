# 0012 — `halfvec(512)` for image embeddings, full `vector(384)` for text

- **Status:** Accepted
- **Date:** 2026-08-16
- **Owner:** PLT · DATA
- **Blueprint:** §9.2, §14.2

## Context

Blueprint §9.2 specifies `VECTOR(512)` for CLIP image embeddings and
`VECTOR(384)` for text. pgvector 0.8 also offers `halfvec`, which stores IEEE
half precision — half the storage, and half the HNSW graph size.

The constraint that makes this a decision rather than a detail: this stack runs
on a 16 GB machine shared with Ollama, WSL2, and a browser (ADR-0002). An HNSW
graph that does not stay in the page cache turns a 3 ms lookup into a disk read,
and Phase 10's dedup decision is on the critical path of every submission.

## Decision

- **`image_embedding halfvec(512)`** — CLIP ViT-B-32 output.
- **`text_embedding vector(384)`** — multilingual-e5-small, full precision.
- Both indexed with HNSW, cosine opclasses, `m = 16`, `ef_construction = 128`,
  chosen against the measured curve in
  [`docs/reports/hnsw-recall.md`](../reports/hnsw-recall.md).

The asymmetry is deliberate. The useful signal in a CLIP embedding is a cosine
*direction*, and half precision preserves direction to well within the margin
any similarity threshold operates at. The text vector is already small enough
that halving it buys little, and text similarity is the signal that
disambiguates near-identical *photographs* of different problems — the case
where the image vector is least informative and the text vector carries the
decision.

Cosine opclasses because both encoders produce direction-carrying vectors whose
magnitude is not meaningful. Using L2 would make vector length — an artefact of
the encoder — affect a dedup decision.

## Alternatives considered

**`vector(512)` for images, per §9.2 literally.** Rejected: double the index
memory for a precision the downstream threshold cannot resolve. §9.2 predates
`halfvec` being available.

**`halfvec` for both.** Rejected: the saving on a 384-dimensional vector is
small, and text is the tie-breaker signal in exactly the ambiguous cases Phase 10
routes to the Investigation Agent. Precision is worth more where the decision is
closest.

**Binary quantisation (`bit`) with a re-ranking pass.** Rejected as premature:
it is a meaningful win at millions of vectors and pure complexity at a city's
annual volume. Revisit at Phase 29's seeded scale.

**IVFFlat instead of HNSW.** Rejected: IVFFlat needs a representative training
set to build its lists, and the index must work from the first complaint — a
cold-start problem HNSW does not have.

## Consequences

- The index is ~26 MB at 20 000 vectors, comfortably resident.
- `halfvec` requires pgvector ≥ 0.7. The stack pins 0.8.6 (ADR-0001).
- **Phase 9 must re-measure against real CLIP output.** The recall benchmark uses
  synthetic clustered vectors, which are a rough proxy — see the "what this does
  not establish" section of the report. The parameters are defensible now and
  explicitly provisional.
- `ef_search` is a *runtime* setting and is not implied by the index. The
  measurement supports 100 rather than the default 40, and Phase 10 must set it
  on the dedup query.
- Half precision is lossy. If a future feature needs exact reconstruction of an
  embedding from the database, it will need its own full-precision column —
  storing them at half precision is a one-way decision per column.

## Revisit when

- Phase 9 produces real per-category F1 numbers and can measure whether half
  precision moves them at all.
- Vector count exceeds ~1 M, where binary quantisation and re-ranking start to
  pay for their complexity.
