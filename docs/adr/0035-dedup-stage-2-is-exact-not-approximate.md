# ADR-0035 — Dedup Stage 2 is exact, not approximate

**Status:** Accepted · **Date:** 2026-08-24 · **Phase:** 10 · **Owner:** DATA

## Context

§14.1 specifies a two-stage dedup engine, and the program plan describes Stage 2
as "pgvector cosine over image + text embeddings, **with 0.8 iterative scans so
filtered searches cannot silently under-return**". That wording assumes Stage 2
performs an approximate-nearest-neighbour search against the HNSW indexes on
`complaints.text_embedding` and `complaints.image_embedding`, and that the
search is filtered — by tenant, and by the candidate clusters Stage 1 returned.

A filtered ANN search is the case `hnsw.iterative_scan` exists for. Without it,
pgvector walks a fixed number of graph nodes, discards the ones the filter
rejects, and returns fewer rows than asked for — silently. With it, the scan
continues until it has enough surviving rows.

We verified the mechanics against the running database rather than the
documentation: pgvector 0.8.6, `hnsw.iterative_scan` defaults to `off` and
accepts `off | relaxed_order | strict_order`, and the GUC is only registered once
the extension's library is loaded into the backend — so on a fresh pooled
connection `SET hnsw.iterative_scan` fails unless something has touched a vector
type first, or `LOAD 'vector'` is issued.

## Decision

**Stage 2 computes exact cosine similarity over the members of the candidate
clusters, and does not use the HNSW indexes at all.** `hnsw.iterative_scan` is
consequently not set on this path.

Stage 1 reduces the search to the clusters within the band's radius and time
window that share the report's category. That is a handful of clusters, and
their members are reachable through the existing
`ix_complaints_tenant_id_cluster_id` B-tree. An exact scan of a few dozen rows is
both cheaper than a graph traversal and, more importantly, *right*.

## Rationale

**The decision must be reproducible from the event log.** §14.3 requires that a
disputed merge can be re-argued, and Phase 7 backtests threshold changes by
replaying history. Both need the same inputs to yield the same decision. HNSW
does not promise that: `docs/reports/hnsw-recall.md` measured a distance ratio of
1.0105 at the chosen parameters and concluded, correctly, that the index "is not
missing neighbours; it is picking different members of a set of near-ties."
Reordering near-ties is harmless in a search box and unacceptable in a decision
about whether to suppress a citizen's report.

**The performance argument for ANN does not apply here.** HNSW earns its
approximation when the candidate set is the whole table. After Stage 1 it is not.
The measured p95 for Stage 1 and Stage 2 together is ~16 ms against a §27.1
budget of 10 s, so there is no latency pressure to trade correctness against.

**The concern the plan raised is still addressed, by removal rather than
configuration.** "Filtered searches must not silently under-return" is satisfied
absolutely by a query that examines every candidate, which is a stronger
guarantee than `iterative_scan` provides. The setting belongs with a query that
needs it; this one does not have one to configure.

## Consequences

- Stage 2 cost is linear in candidate members rather than logarithmic in table
  size. Bounded deliberately by `DedupSettings.max_candidates` (50 clusters) and
  `max_members_per_cluster` (25, most recent first), so one pathological cluster
  cannot consume the budget. When a cap binds, the engine reports truncation
  rather than absorbing it.
- The two HNSW indexes remain, unused by this path. They are not dead: Phase 17's
  repeat-defect mode (§17.5) searches by contractor across the whole table, which
  is a genuine global ANN query, and that is where `hnsw.iterative_scan` will be
  set — on the query that needs it.
- **This ADR is revisited if Stage 1 ever stops being selective.** The trigger is
  concrete: if `nemesis_dedup_stage1_candidates` starts tracking a tenant's open
  incident count, or truncation stops being rare, the candidate set is no longer
  small and the exactness argument no longer comes for free.

## Alternatives considered

**Follow the plan literally and use a filtered ANN search with
`hnsw.iterative_scan = relaxed_order`.** Rejected: it buys nothing at this
candidate-set size and costs the reproducibility that §14.3 and Phase 7 both
depend on. Deviating from the plan's wording is worth an ADR, which is this one.

**Store a centroid vector on `complaint_clusters` and compare against it.**
Rejected for a different reason, recorded in `similarity.py`: the mean of two
photographs of one pothole from opposite kerbs resembles neither, the centroid
drifts with every merge, and the rule that admitted the tenth member is then not
the rule that admitted the second.

**Compare against cluster members using the mean similarity rather than the
best.** Rejected: it makes large clusters progressively harder to join, which is
backwards — a cluster with twenty reports is *more* likely to be the right home
for the twenty-first, not less.
