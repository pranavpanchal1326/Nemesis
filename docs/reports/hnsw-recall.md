# HNSW parameter selection — measured recall curve

**Date:** 2026-08-16 · **Phase:** 2 · **Owner:** PLT
**Reproduce:** `docker compose exec -e PYTHONPATH=/app api python scripts/bench_hnsw_recall.py --vectors 20000 --queries 200 --json /app/hnsw_bench.json`
**Raw data:** [`hnsw-recall.json`](hnsw-recall.json)

The program plan requires HNSW parameters "chosen against a *measured* recall
curve, not defaults". This is the measurement, the decision it supports, and —
more importantly — what it does **not** yet establish.

---

## Result

20 000 half-precision 512-dimensional vectors in 100 tight clusters, 200 queries,
recall@10 against exact sequential-scan ground truth.

| `m` | `ef_construction` | `ef_search` | recall@10 | distance ratio | p95 | index | build |
|---|---|---|---|---|---|---|---|
| 8 | 64 | 40 | 0.320 | 1.0245 | 3.52 ms | 26.1 MB | 3.7 s |
| 8 | 64 | 100 | 0.408 | 1.0169 | 2.39 ms | 26.1 MB | 3.5 s |
| 16 | 64 | 40 | 0.298 | 1.0249 | 3.27 ms | 26.1 MB | 5.9 s |
| 16 | 64 | 100 | 0.453 | 1.0146 | 3.10 ms | 26.1 MB | 5.5 s |
| **16** | **128** | **40** | 0.416 | 1.0169 | 2.90 ms | 26.1 MB | 8.6 s |
| **16** | **128** | **100** | **0.556** | **1.0105** | 2.78 ms | 26.1 MB | 8.3 s |
| 24 | 128 | 40 | 0.457 | 1.0156 | 2.43 ms | 31.3 MB | 11.9 s |
| 24 | 128 | 100 | 0.610 | 1.0086 | 4.58 ms | 31.3 MB | 12.8 s |
| 32 | 200 | 40 | 0.521 | 1.0117 | 5.15 ms | 31.3 MB | 27.1 s |
| 32 | 200 | 100 | 0.716 | 1.0049 | 5.14 ms | 31.3 MB | 25.9 s |

**Chosen: `m = 16`, `ef_construction = 128`, with `ef_search = 100` at query time.**

## Reading these numbers correctly

The recall column looks alarming. HNSW routinely reports >0.95 recall@10 in
published benchmarks, and nothing here exceeds 0.72.

**The recall figure is an artefact of the test distribution, and the distance
ratio is what proves it.** Distance ratio is the mean of (10th-nearest distance
returned) ÷ (10th-nearest distance under exact search). At the chosen setting it
is **1.0105** — the index returns neighbours 1% further away than the perfect
answer. It is not missing neighbours; it is picking different members of a set
of near-ties.

That is inherent to the synthetic data. 20 000 vectors in 100 clusters puts
roughly 200 near-equidistant vectors around each query, so which ten come back
is close to arbitrary and swapping the 7th nearest for the 9th costs recall while
changing nothing any caller can observe.

This measurement was very nearly reported as recall alone. It would have shown a
false problem and invited exactly the wrong fix — pushing `m` to 32 for a 20%
larger index and a 3× build time, buying 0.5% of distance quality. **Adding the
distance-ratio column changed the decision.** An index tuned against a metric
that does not reflect what the caller experiences is tuned against an artefact.

## Why this setting

- **Against the pgvector default** (`m=16, ef_construction=64`): +23% recall and
  a better distance ratio for **identical index size**, costing 2.8 s of build
  time at this volume. `ef_construction` is a build-time-only knob, so this is
  close to free at read time.
- **Against `m=24`/`m=32`**: those improve distance ratio from 1.0105 to
  1.0086/1.0049 — a fraction of a percent — for a 20% larger index and up to 3×
  the build. `m` drives resident graph size, and this stack shares 16 GB with
  Ollama, WSL2, and a browser. An index that pushes the graph out of the page
  cache turns a 3 ms lookup into a disk read, which costs far more than 1% of
  distance quality.
- **`ef_search = 100` rather than the default 40**: consistently the larger
  effect of the two search-side knobs, at no measurable latency cost here. It is
  a *runtime* setting, so Phase 10 must set it explicitly on the dedup query —
  the index parameters in the migration do not imply it.

## Recall is the right thing to buy generously

Phase 10's gate is **zero false-positive merges**, and the two error directions
are asymmetric in a way that is easy to get backwards:

- A **missed candidate** means a duplicate is not detected, and a citizen sees
  their report tracked separately. Annoying, visible, self-correcting.
- A **false merge** means a report disappears into another incident and the
  citizen is told a problem is being handled that nobody has looked at. That is
  the §3.1 trust-collapse mechanism operating inside the product built to
  prevent it.

So the index is asked to return candidates generously, and the decision about
whether two candidates are the same defect belongs to the Phase 6 policy
thresholds — where it is versioned, approvable, and backtestable (Phase 7).

## What this does not establish

Stated plainly, because a measured number carries more authority than it has
earned here:

1. **These are not CLIP embeddings.** Real image embeddings of civic defects have
   local structure that synthetic Gaussian clusters do not. **Phase 9 must re-run
   this against real encoder output** before the parameters are considered
   settled. The choice is defensible now and provisional.
2. **20 000 vectors is a small index.** At a city's annual volume the graph no
   longer fits comfortably in cache, and the `m` trade-off shifts. Re-measure at
   Phase 29's seeded volume.
3. **Build times are relative, not absolute.** The benchmark forces
   `max_parallel_maintenance_workers = 0`, because the Postgres container's
   `/dev/shm` is the Docker default of 64 MB and a parallel HNSW build fails on it
   with `DiskFullError` (see below). Production build times will be lower.
4. **The text index (`vector(384)`) was not separately measured.** It shares these
   parameters on the reasoning that it is smaller and lower-dimensional, so this
   measurement is a conservative bound. That reasoning should be checked, not
   trusted, in Phase 9.

## Defect found while benchmarking

**The Postgres container has no `shm_size` configured**, so it gets Docker's
64 MB default. A parallel HNSW index build allocates a shared-memory segment
sized for the graph and fails with `DiskFullError: could not resize shared memory
segment` — at 20 000 rows, which is a trivial index. Nothing in the stack builds
an HNSW index today, so this has been invisible; the first real index build at
Phase 9 or 10 volume would have hit it, most likely during a migration.

Carried as an open item for Phase 10 rather than fixed here: raising `shm_size`
changes the memory budget of a container in a stack deliberately capped at ~6 GB,
and that trade belongs with the phase that actually builds the index and knows
how large it needs to be.

## Related

- [`docs/adr/0012-halfvec-for-image-embeddings.md`](../adr/0012-halfvec-for-image-embeddings.md)
- `backend/scripts/bench_hnsw_recall.py`
