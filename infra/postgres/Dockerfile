# NEMESIS datastore: one Postgres holding relational + geospatial + vector +
# the append-only event log (Blueprint §7.3 — one backup strategy, one
# connection pool, one thing that can fail).
#
# Postgres 17 / PostGIS 3.5 / pgvector 0.8+. pgvector 0.8 matters on a 16GB
# machine specifically: it adds iterative index scans (correct results when a
# filtered HNSW search under-returns) and `halfvec`, which halves index memory
# for the 512-dim CLIP column.
FROM postgis/postgis:17-3.5

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-17-pgvector \
    && rm -rf /var/lib/apt/lists/*
