"""Measure HNSW recall against exact search, and pick `m` / `ef_construction` from it.

The program plan requires these parameters to be "chosen against a *measured*
recall curve, not defaults". This is that measurement. It builds indexes at
several parameter settings over synthetic vectors shaped like the real ones,
runs the same queries against each index and against exact search, and reports
recall@k with build time and index size.

**Why this matters more here than in a typical vector search.** Phase 10's gate
is *zero false-positive merges*: a wrong merge suppresses a real citizen report.
That makes the two error directions asymmetric in an unusual way. A missed
candidate (low recall) means a duplicate is not detected and a citizen sees their
report tracked separately — annoying, visible, self-correcting. A false merge
means a report vanishes into another incident and the citizen is told a problem
is being handled that nobody has looked at. So recall is bought generously here,
and the *decision* about whether two candidates are the same defect is left to
the Phase 6 thresholds where it can be tuned and backtested.

Recall is also not the only axis. `m` drives index memory, and this stack shares
16 GB with Ollama, a browser, and WSL2 — an index that pushes the graph out of
the page cache turns a 5 ms lookup into a disk read.

The synthetic vectors are **clustered, not uniform**, because uniform random
vectors in 512 dimensions are all nearly equidistant and every ANN index scores
near-perfect recall on them. That benchmark would be easy, fast, and worthless:
real complaint embeddings are tightly clustered (many photographs of the same
kind of defect), and clustering is precisely what makes graph traversal miss
neighbours.

    python scripts/bench_hnsw_recall.py --vectors 20000 --queries 200
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from nemesis.config import get_settings

IMAGE_DIM = 512
TEXT_DIM = 384

#: The grid. pgvector's defaults are m=16, ef_construction=64; both directions
#: are sampled around them so the report shows what the default costs and what
#: the alternatives buy.
PARAMETER_GRID: tuple[tuple[int, int], ...] = (
    (8, 64),
    (16, 64),
    (16, 128),
    (24, 128),
    (32, 200),
)

#: Search-time breadth. Recall at a given `m` is meaningless without saying what
#: `ef_search` produced it — the two trade against each other at query time, and
#: quoting one without the other is how ANN benchmarks flatter themselves.
EF_SEARCH_VALUES: tuple[int, ...] = (40, 100)

TOP_K = 10


@dataclass
class Measurement:
    m: int
    ef_construction: int
    ef_search: int
    #: Fraction of exact top-k ids the index also returned.
    recall_at_k: float
    #: Mean of (k-th distance returned) / (k-th distance exact). 1.0 means the
    #: index found answers exactly as close as the exact search did, even where
    #: it picked different rows.
    #:
    #: Reported alongside recall because id-overlap alone is misleading when
    #: neighbours are near-tied: swapping the 7th nearest for the 9th costs
    #: recall and changes nothing a caller can observe. Judging an index by
    #: recall on tied data is how a parameter gets tuned against an artefact.
    distance_ratio: float
    p50_latency_ms: float
    p95_latency_ms: float
    build_seconds: float
    index_bytes: int


@dataclass
class BenchmarkReport:
    vectors: int
    queries: int
    dimension: int
    clusters: int
    measurements: list[Measurement] = field(default_factory=list)


def make_clustered_vectors(
    count: int, dimension: int, clusters: int, rng: random.Random
) -> list[list[float]]:
    """Vectors drawn around a set of centroids, then unit-normalised.

    Normalised because both encoders produce direction-carrying vectors and the
    indexes use cosine opclasses; benchmarking un-normalised vectors would
    measure a distance function the system never uses.
    """
    centroids = [[rng.gauss(0.0, 1.0) for _ in range(dimension)] for _ in range(clusters)]
    vectors: list[list[float]] = []
    for index in range(count):
        centroid = centroids[index % clusters]
        # Tight spread: photographs of the same defect type are close together,
        # which is the regime where graph traversal actually loses neighbours.
        raw = [value + rng.gauss(0.0, 0.35) for value in centroid]
        norm = sum(component * component for component in raw) ** 0.5 or 1.0
        vectors.append([component / norm for component in raw])
    return vectors


def to_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{value:.6f}" for value in vector) + "]"


async def prepare(engine: AsyncEngine, vectors: list[list[float]], dimension: int) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS hnsw_bench"))
        await conn.execute(
            text(f"CREATE TABLE hnsw_bench (id bigserial PRIMARY KEY, v halfvec({dimension}))")
        )
        # Batched inserts: one round trip per vector dominates the wall clock at
        # 20k rows and tells you nothing about the index.
        batch = 500
        for start in range(0, len(vectors), batch):
            chunk = vectors[start : start + batch]
            values = ",".join(f"('{to_literal(vector)}')" for vector in chunk)
            await conn.execute(text(f"INSERT INTO hnsw_bench (v) VALUES {values}"))


async def exact_neighbours(
    engine: AsyncEngine, queries: list[list[float]], k: int
) -> list[tuple[list[int], float]]:
    """Ground truth by sequential scan, with the index explicitly disabled.

    Returns the ids *and* the k-th distance, because the k-th distance is what
    makes ``distance_ratio`` computable — and that metric is what distinguishes
    "the index missed a neighbour" from "the neighbours were tied".
    """
    results: list[tuple[list[int], float]] = []
    async with engine.connect() as conn:
        # Session-level, not SET LOCAL: outside an explicit transaction block
        # SET LOCAL warns and does nothing, which would silently let the index
        # answer the query that is supposed to be the ground truth for it.
        await conn.execute(text("SET enable_indexscan = off"))
        await conn.execute(text("SET enable_bitmapscan = off"))
        for query in queries:
            rows = (
                await conn.execute(
                    text(
                        f"SELECT id, v <=> '{to_literal(query)}'::halfvec AS d FROM hnsw_bench "
                        f"ORDER BY d LIMIT {k}"
                    )
                )
            ).all()
            results.append(([int(row[0]) for row in rows], float(rows[-1][1])))
    return results


async def measure(
    engine: AsyncEngine,
    queries: list[list[float]],
    truth: list[list[int]],
    *,
    m: int,
    ef_construction: int,
    ef_search: int,
    k: int,
) -> Measurement:
    async with engine.begin() as conn:
        await conn.execute(text("DROP INDEX IF EXISTS hnsw_bench_idx"))
        # Serial build. A parallel HNSW build allocates a shared memory segment
        # sized for the whole graph, and the Postgres container's /dev/shm is
        # the Docker default of 64 MB — so the parallel path fails with
        # DiskFullError long before the machine is short of memory. Building
        # serially is slower and measures the same index; the build_seconds
        # column below is therefore a *relative* signal across the grid, not a
        # production build-time estimate.
        await conn.execute(text("SET LOCAL max_parallel_maintenance_workers = 0"))
        started = time.perf_counter()
        await conn.execute(
            text(
                f"CREATE INDEX hnsw_bench_idx ON hnsw_bench USING hnsw (v halfvec_cosine_ops) "
                f"WITH (m = {m}, ef_construction = {ef_construction})"
            )
        )
        build_seconds = time.perf_counter() - started
        size = await conn.execute(text("SELECT pg_relation_size('hnsw_bench_idx')"))
        index_bytes = int(size.scalar_one())

    hits = 0
    latencies: list[float] = []
    ratios: list[float] = []
    async with engine.connect() as conn:
        await conn.execute(text(f"SET hnsw.ef_search = {ef_search}"))
        for query, (expected_ids, exact_kth) in zip(queries, truth, strict=True):
            started = time.perf_counter()
            rows = (
                await conn.execute(
                    text(
                        f"SELECT id, v <=> '{to_literal(query)}'::halfvec AS d FROM hnsw_bench "
                        f"ORDER BY d LIMIT {k}"
                    )
                )
            ).all()
            latencies.append((time.perf_counter() - started) * 1000)
            hits += len({int(row[0]) for row in rows} & set(expected_ids))
            if rows and exact_kth > 0:
                ratios.append(float(rows[-1][1]) / exact_kth)

    latencies.sort()
    return Measurement(
        m=m,
        ef_construction=ef_construction,
        ef_search=ef_search,
        recall_at_k=hits / (len(queries) * k),
        distance_ratio=statistics.mean(ratios) if ratios else float("nan"),
        p50_latency_ms=statistics.median(latencies),
        p95_latency_ms=latencies[int(len(latencies) * 0.95) - 1],
        build_seconds=build_seconds,
        index_bytes=index_bytes,
    )


async def run(vectors_count: int, queries_count: int, dimension: int, seed: int) -> BenchmarkReport:
    rng = random.Random(seed)
    clusters = max(8, vectors_count // 200)
    vectors = make_clustered_vectors(vectors_count, dimension, clusters, rng)
    queries = make_clustered_vectors(queries_count, dimension, clusters, rng)

    engine = create_async_engine(get_settings().database_url)
    report = BenchmarkReport(
        vectors=vectors_count, queries=queries_count, dimension=dimension, clusters=clusters
    )
    try:
        print(f"loading {vectors_count} vectors across {clusters} clusters...", flush=True)
        await prepare(engine, vectors, dimension)

        print("computing exact ground truth (sequential scan)...", flush=True)
        truth = await exact_neighbours(engine, queries, TOP_K)

        for m, ef_construction in PARAMETER_GRID:
            for ef_search in EF_SEARCH_VALUES:
                result = await measure(
                    engine,
                    queries,
                    truth,
                    m=m,
                    ef_construction=ef_construction,
                    ef_search=ef_search,
                    k=TOP_K,
                )
                report.measurements.append(result)
                print(
                    f"  m={m:<3} ef_construction={ef_construction:<4} ef_search={ef_search:<4} "
                    f"recall@{TOP_K}={result.recall_at_k:.4f} "
                    f"dist_ratio={result.distance_ratio:.5f} "
                    f"p95={result.p95_latency_ms:6.2f}ms "
                    f"index={result.index_bytes / 1024 / 1024:6.1f}MB "
                    f"build={result.build_seconds:5.1f}s",
                    flush=True,
                )
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS hnsw_bench"))
        await engine.dispose()

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vectors", type=int, default=20_000)
    parser.add_argument("--queries", type=int, default=200)
    parser.add_argument("--dimension", type=int, default=IMAGE_DIM)
    parser.add_argument("--seed", type=int, default=20260816)
    parser.add_argument("--json", type=Path, help="write raw measurements here")
    args = parser.parse_args()

    report = asyncio.run(run(args.vectors, args.queries, args.dimension, args.seed))

    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "vectors": report.vectors,
                    "queries": report.queries,
                    "dimension": report.dimension,
                    "clusters": report.clusters,
                    "top_k": TOP_K,
                    "measurements": [vars(m) for m in report.measurements],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"\nwrote {args.json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
