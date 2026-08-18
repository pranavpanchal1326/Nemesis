# 0024 — Bulk export ships CSV and NDJSON; Parquet waits for Phase 23

- **Status:** Accepted
- **Date:** 2026-08-17
- **Owner:** PLT · DATA
- **Blueprint:** §16.3
- **Program plan:** Phase 4 ships "Bulk export (CSV/Parquet) for RTI applicants
  and researchers"

## Context

The Phase 4 ships-list names CSV *and* Parquet. Parquet requires `pyarrow`: a
~40 MB dependency added to the image that serves citizen submissions, for a
columnar format whose value — column pruning and predicate pushdown — is
realised by a query engine reading it.

The consumers §16.3 names are RTI applicants, journalists, and civil-society
researchers. They open an extract in a spreadsheet or read it with a script.
Neither has a query engine, and neither benefits from column pruning on a file
they downloaded whole.

Phase 2's twelfth defect is the reason this is an ADR rather than a shrug: the
phase was reported complete while a layer it listed wrote nothing, and the
uncomfortable conclusion was that **no gate can catch scope that was never
implemented**. A dropped item has to be recorded where a reader will find it, or
it is indistinguishable from an oversight.

## Decision

**Ship CSV and NDJSON. Do not ship Parquet in Phase 4. Record it as Phase 23's,
and make the API say so.**

`resolve_format("parquet")` raises with the reason and this ADR's number, rather
than a generic "unsupported format".

## Consequences

**The format list is honest at the boundary.** A consumer asking for Parquet
gets an explanation and a pointer, not a 422 that reads as a typo. The developer
portal states it too.

**NDJSON was added, and is not a substitute dressed up as one.** It exists
because a streaming JSON-per-line extract is what a script actually wants and
what CSV handles badly — nested values, nulls distinguishable from empty
strings, and no quoting ambiguity. It is a better answer for the scripted
consumer than Parquet would have been.

**Phase 23 owns the columnar extract**, next to the warehouse and the metrics
layer that make it worth having. Adding it there costs a dependency in an
analytics image rather than in the ingest path.

**If a real consumer asks for Parquet before Phase 23, this decision is
revisited rather than defended.** The reasoning above is about the consumers we
know of; it is not a claim that Parquet is never useful.

## What was built instead, and why it matters more

The effort went into properties a format choice cannot provide:

- **Streaming.** Rows are yielded from a server-side cursor, so a
  hundred-thousand-row extract is not a hundred-thousand-row allocation in a
  process serving everything else.
- **`reported_date`, not a timestamp.** A second-resolution timestamp beside a
  coarse location is a re-identifier — two people do not photograph the same
  corner in the same second — so truncating to the day is what makes the
  coarsening actually hold. This is the single most important line in the export
  module and it has nothing to do with file format.
- **No complaint id.** A stable handle would let two extracts taken a month
  apart be joined into a per-reporter history, which is the reconstruction the
  aggregates exist to prevent.
- **No assignee on the work-order extract.** §16.1 publishes a contractor's
  aggregate record through its own endpoint, where it arrives with the §22.2
  disclaimer and the §16.4 appeal path attached. A per-job extract naming the
  contractor is the same accusation without either, in a format designed for
  automated republication.
- **The same allow-list as the API.** Export columns are declared in
  `public.policy.PUBLIC_FIELDS`, so an extract cannot carry a field the API
  would refuse to serve. A bulk download is the most attractive way to
  exfiltrate this dataset, and "the export writes a different serialiser" is how
  a scrub gets bypassed by accident.
- **An announced row cap.** `X-Export-Row-Limit` on every response: a truncated
  extract always says it was truncated, because otherwise it is a dataset a
  researcher will publish conclusions from.

## Alternatives rejected

**Add `pyarrow` to the base dependencies.** Delivers the ships-list item
literally, and puts a large analytics dependency into the image that accepts
citizen media uploads — the image whose attack surface §25 cares most about.

**An optional `export` extra, with the endpoint returning 501 when absent.** A
feature that exists in the documentation and not in the deployment, which is a
worse answer than a stated decision.

**Say nothing and ship CSV.** The Phase 2 defect above, repeated deliberately.
