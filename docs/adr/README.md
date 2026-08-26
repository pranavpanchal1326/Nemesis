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
| [0010](0010-widened-event-hash-preimage.md) | The event hash preimage is widened and structured, not §9.3's concatenation | Accepted | PLT |
| [0011](0011-partition-the-event-log-by-month.md) | The event log is range-partitioned by month from the first migration | Accepted | PLT |
| [0012](0012-halfvec-for-image-embeddings.md) | `halfvec(512)` for image embeddings, full `vector(384)` for text | Accepted | PLT · DATA |
| [0013](0013-rfc-8785-canonical-json.md) | Event payloads are hashed through RFC 8785 canonical JSON | Accepted | PLT |
| [0014](0014-tenancy-enforced-at-three-layers.md) | Tenant isolation is enforced at three layers, none of them convention | Accepted | PLT · SEC |
| [0015](0015-transactional-outbox-with-a-dedicated-relay.md) | Realtime events publish from a transactional outbox, drained by a dedicated relay | Accepted | PLT |
| [0016](0016-realtime-payloads-are-default-deny.md) | A published event payload is empty unless a shape is declared for it | Accepted | PLT · SEC |
| [0017](0017-the-rate-limiter-fails-open.md) | The submission rate limiter fails open, and counts every time it does | Accepted | PLT · SEC |
| [0018](0018-two-hierarchies-responsibility-and-place.md) | Two organisation hierarchies: responsibility and place | Accepted | PLT |
| [0019](0019-taxonomy-keys-are-immutable-contracts.md) | A taxonomy key is an immutable contract; the display name is a translation | Accepted | PLT |
| [0020](0020-control-plane-writes-carry-a-shared-token.md) | Control-plane writes carry a shared token, and every one of them writes an event | Accepted | PLT · SEC |
| [0021](0021-the-public-api-is-opt-in-and-k-anonymous.md) | The public API is opt-in per tenant, and its aggregates are k-anonymous | Accepted | PLT · SEC |
| [0022](0022-api-versions-are-locked-by-a-contract-file.md) | A published API version is locked by a contract file, and v2 ships to prove it | Accepted | PLT |
| [0023](0023-webhook-secrets-are-derived-never-stored.md) | Webhook signing secrets are derived, never stored; targets re-validated at delivery | Accepted | PLT · SEC |
| [0024](0024-bulk-export-ships-csv-and-ndjson-parquet-waits-for-phase-23.md) | Bulk export ships CSV and NDJSON; Parquet waits for Phase 23 | Accepted | PLT · DATA |
| [0025](0025-policy-conditions-run-in-an-ast-interpreter-never-eval.md) | Policy conditions run in a hand-written AST interpreter, never `eval` | Accepted | PLT · SEC |
| [0026](0026-policy-rollback-moves-forward-never-backward.md) | Policy rollback creates a new revision; it never re-activates an old row | Accepted | PLT |
| [0027](0027-policy-reads-are-cached-with-a-stated-reload-interval.md) | Policy reads are cached per process, and the reload interval is published | Accepted | PLT |
| [0028](0028-the-activation-guardrail-is-a-row-not-a-call.md) | The activation guardrail is evidence in a table, never a call into the simulation package | Accepted | DATA |
| [0029](0029-a-backtest-replays-the-log-never-the-projections.md) | A backtest folds the event log for observations; it never reads the projections it is measuring | Accepted | DATA |
| [0030](0030-shadow-mode-is-read-only-by-construction.md) | Shadow mode runs on its own transaction under two independent read-only layers | Accepted | DATA |
| [0031](0031-the-raw-photograph-persists-unreachably.md) | The raw photograph persists for §22.4's window, unreachable rather than absent | Accepted | DATA · SEC |
| [0032](0032-a-missing-face-detector-halts-the-pipeline.md) | A missing face detector halts the complaint; §22.1 fails closed and has no kill switch | Accepted | SEC · DATA |
| [0033](0033-abuse-detection-flags-and-cannot-block.md) | Coordinated-abuse detection flags, and the blocking path does not exist to be re-enabled | Accepted | DATA · SEC |
| [0034](0034-the-published-f1-runs-the-shipped-decision-rule.md) | The published F1 runs the shipped decision rule, on a split the corpus computes | Accepted | DATA |
| [0035](0035-per-category-temperature-requires-a-per-category-centre.md) | A per-category temperature requires a per-category centre, in similarity space | Accepted | DATA |
| [0036](0036-dedup-stage-2-is-exact-not-approximate.md) | Dedup Stage 2 is exact, not approximate — the HNSW indexes are deliberately unused | Accepted | DATA |
| [0037](0037-shaders-are-authored-in-tsl-not-glsl.md) | Shaders are authored in TSL against WebGPURenderer, never as GLSL strings | Accepted | PROD |
| [0038](0038-the-press-is-two-implementations-and-text-is-exempt.md) | The press is one token source in two implementations, and text is exempt from it | Accepted | PROD |
| [0039](0039-an-unverified-flag-is-never-rendered-in-red.md) | An unverified flag is fluorescent pink and hatched, and is never rendered in red | Accepted | PROD · SEC |
| [0040](0040-the-browser-talks-to-a-bff-the-websocket-does-not.md) | The browser talks to a BFF; the WebSocket connects directly | Accepted | PROD · PLT |
| [0041](0041-characters-are-event-driven-state-machines-not-timelines.md) | Characters are event-driven state machines, not timelines | Accepted | PROD |
| [0042](0042-next-16-ships-typescript-7-waits.md) | Next.js 16 ships, amending §E15; the TypeScript 7 native compiler waits on the tooling the gates need | Accepted | PROD |
| [0043](0043-a-complaints-history-is-readable-by-whoever-holds-its-id.md) | A complaint's history is readable by whoever holds its id, and every row is disclosed as a row | Accepted | PLT · SEC · PROD |
| [0044](0044-the-receipt-carries-the-chain-head-the-polled-read-does-not.md) | The receipt carries the chain head; the polled read deliberately does not | Accepted | PLT · PROD |
| [0045](0045-what-a-browser-may-learn-about-an-exif-check-and-a-redaction.md) | What a browser may learn about an EXIF check and a face redaction | Accepted | SEC · PLT · PROD |
| [0046](0046-publishing-is-an-act-somebody-takes-not-a-flag-a-template-sets.md) | Publishing is an act somebody takes, not a flag a template sets | Accepted | PLT · LEGAL |
| [0047](0047-the-clay-city-is-generated-not-modelled.md) | The clay city is generated from the tenant's own origin, not modelled | Accepted | PROD |
| [0048](0048-the-character-contract-is-the-state-machine-not-the-file-format.md) | The character contract is the state machine, not the file format | Accepted | PROD |
| [0049](0049-tier-cs-prints-are-drawn-as-a-deliverable-not-rendered-from-the-scene.md) | Tier C's prints are drawn as a deliverable, not rendered from the scene | Accepted | PROD |
| [0050](0050-the-sound-library-is-synthesised-at-runtime-not-recorded.md) | The sound library is synthesised at runtime, not recorded and not committed | Accepted | PROD |
| [0051](0051-the-memory-budget-is-a-checked-artefact-not-a-table.md) | The memory budget is a checked artefact, not a table | Accepted | SRE |
| [0052](0052-the-public-surface-negotiates-a-locale-and-names-who-owns-the-words.md) | The public surface negotiates a locale, and names who owns the words | Accepted | PLT · LEGAL · PROD |
| [0053](0053-a-fallback-face-is-adjusted-only-by-what-can-be-measured.md) | A fallback face is adjusted only by what this repository can measure | Accepted | PROD |
| [0054](0054-a-pool-child-that-loads-models-is-given-time-to-say-it-is-alive.md) | A pool child that loads models is given time to say it is alive | Accepted | SRE |
| [0055](0055-the-films-camera-is-authored-as-keys-and-generated-into-theatre.md) | The film's camera is authored as keys and generated into Theatre.js | Accepted | PROD |
| [0056](0056-the-offline-queue-is-a-durable-outbox-keyed-by-the-idempotency-key.md) | The offline queue is a durable outbox keyed by the idempotency key | Accepted | PROD · PLT |
| [0057](0057-compression-preserves-exif-by-transplanting-the-original-segment.md) | Client-side compression preserves EXIF by transplanting the original segment, and strips nothing | Accepted | PROD · SEC |
| [0058](0058-product-copy-is-authored-not-imported-and-the-third-tier-goes.md) | Product copy is authored, not imported, and the third string tier goes | Accepted | PROD · PLT · LEGAL |
| [0059](0059-the-product-has-two-front-doors-and-every-surface-links-back.md) | The product has two front doors, and every surface links back to one | Accepted | PROD |
| [0060](0060-an-unreachable-api-is-an-answer-not-an-exception.md) | An unreachable API is an answer, not an exception | Accepted | PROD · SRE |
| [0061](0061-a-run-is-printed-at-an-exposure-and-the-story-run-needs-one.md) | A run is printed at an exposure, and the story run needs one | Accepted | DESIGN · PROD |

## Reserved numbers

**None.** `docs/FRONTEND-PHASE-PLAN.md` §4 reserved 0048–0050 for three of the
four asset decisions Stage 2 onward depends on; all four are now written.

| Number | Decision | Taken at |
|---|---|---|
| 0047 | The clay city kit — generated, not modelled | F9 |
| 0048 | The four characters — a state machine, not a file format | F15 |
| 0049 | Tier C's nine prints — drawn, not rendered from the scene | F14, **recorded at F15** |
| 0050 | The sound library — synthesised at runtime, not recorded | F16 |

**0049 was written a stage late, and that is worth saying rather than
smoothing over.** The reservation's own rule was that each would be written
*before* the phase that consumed it; F14 shipped Tier C's prints and did not
write it. The record was reconstructed from what F14 actually built and from
the reasoning `docs/FRONTEND-PHASE-PLAN.md` records for it — which is a weaker
provenance than an ADR written at the time, and the file says so in its own
status line.
