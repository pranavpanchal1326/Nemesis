# NEMESIS — Engineering Handover

**Audience:** the engineer picking up backend work on this repository.
**Status date:** 2026-08-24 · **Commit:** `02df9b7` · **Branch:** `main`

This document is the entry point. Read it once, end to end, before writing any
code. It will take twenty minutes and will save you a week — this codebase has
strong opinions that are not obvious from the file tree, and most of them exist
because somebody already made the mistake they prevent.

---

## 1. Ownership split

| Track | Owner | Scope |
|---|---|---|
| **E · Experience** (Phases 18–22) | **Project owner** | Next.js app shell, design system, i18n, Three.js geospatial engine, shader scenes, temporal replay, PWA/offline. **Not covered by this handover.** |
| **Everything else** | **Adi** | Platform, control plane, intelligence, accountability, analytics, security, commercial, release. This document and [BACKLOG.md](BACKLOG.md) are yours. |

Where a backend phase has a UI counterpart, the backend contract (API, events,
policy) is still backend scope. Phase 14 "Department workflow" is the main
example: the work orders, state machine, and assignment rules are backend; the
kanban that renders them is not.

---

## 2. Where you are

Ten phases of thirty are complete and gated. One is implemented with a
documented gate failure. Nineteen have not started.

| # | Phase | State |
|---|---|---|
| 0 | Foundation & guardrails | ✅ gated |
| 1a | Engineering operating system | ✅ gated |
| 1b | Cloud environments & promotion | ⛔ deferred — trigger: a deploy target is chosen |
| 2 | Event store, schema registry, tenancy | ✅ gated |
| 3 | Ingestion, orchestration, realtime | ✅ gated |
| 4 | Public API, versioning, integrations | ✅ gated |
| 5 | Tenant, taxonomy, organisation | ✅ gated |
| 6 | Policy & rules engine | ✅ gated |
| 7 | Simulation & backtesting | ✅ gated |
| 8 | Trust & safety spine | ✅ gated |
| 9 | Perception & model registry | ✅ gated 32/32 |
| **10** | **Deduplication & clustering** | ⚠️ **implemented, gate 3/4** — see §6 |
| 11–17, 23–29 | Intelligence, accountability, data, trust, commercial, release | ❌ not started |
| 18–22 | Experience | ❌ not started — **project owner's** |

### Health at this commit

| Metric | Value |
|---|---|
| Tests | **1186 passed**, 2 skipped |
| Coverage | **86.09%** against an 85% floor |
| `ruff` / `ruff format` / `mypy --strict` | clean, 172 source modules |
| CI checkers (7) | all clean |
| Migrations | 9, up/down reversible, `alembic check` clean |
| ADRs | 36 |
| Runbooks | 27 |
| Registered event types | 34 |
| Backend source | ~45k LOC across 21 packages |

**Reproduce all of it with one command:** `nem check`.

---

## 3. What the system does today

A citizen submits a report. It is accepted synchronously, then processed by an
event-sourced pipeline of stages, each of which appends events rather than
mutating rows.

```
ingest → safety_check → trust_verification → classification → dedup → [severity_scoring] → [routing] → [agent_investigation]
   ✅         ✅                ✅                  ✅            ✅          Phase 12          Phase 12         Phase 16
```

Working end to end today:

- **Submission** with media quarantine, idempotency keys, and a transactional outbox.
- **Deterministic safety fail-safe** (§11.2). Runs on its own queue served by a container that has never imported torch, so a saturated ML queue cannot delay a gas-leak report. A trigger halts the pipeline entirely.
- **Trust verification** — EXIF cross-check, perceptual hashing, coordinated-abuse detection, and §22.1 face redaction that *fails closed*.
- **Perception** — CLIP zero-shot against tenant taxonomy prompt sets, multilingual-e5 text embeddings, faster-whisper transcription, a model registry with a single-flight load guard and memory ceiling, and per-category confidence calibration.
- **Deduplication** — PostGIS candidate elimination then exact vector comparison, per-category policy bands, merge reversibility by compensating event.
- **Control plane** — tenants, taxonomy, organisation hierarchy, calendars, translations. A solutions engineer can onboard a new vertical without opening an editor.
- **Policy engine** — severity rubrics, dedup bands, SLA matrices, safety rulesets, routing rules and rate cards as versioned, effective-dated, approvable documents evaluated by an AST interpreter (never `eval`).
- **Simulation & backtesting** — replay the log against a proposed policy before it goes live; shadow mode is read-only by construction.
- **Public API** — versioned, k-anonymous, with webhooks, API keys, rate limiting, and a locked OpenAPI contract.
- **Observability** — OpenTelemetry traces, Prometheus metrics for the §41 KPI set, Grafana dashboards, Alertmanager rules, and a runbook per failure scenario.

**Not built yet:** severity scoring, routing, SLA, work orders, closure
verification, the Investigation Agent, contractor transparency, identity/authz,
analytics, DPDP compliance, billing, and the entire frontend.

---

## 4. The nine rules

These are not style preferences. Every one is enforced by a test, a CI check, or
a gate, and violating one will fail your build.

1. **State changes and events land in one transaction.** No row mutates without an event explaining it. `projections/writer.py` is the only module that writes current-state tables — with exactly one documented exception (`perception/embeddings.py`, for the two vector columns), guarded by an AST walk in `tests/test_perception_embeddings.py`.

2. **Event schemas are immutable once locked.** `events/schema_lock.json` fingerprints every payload. Changing a released schema fails CI; register `v2` with an upcaster instead. After adding an event: `python -m nemesis.events.schema_fingerprint --write`.

3. **Every query is tenant-scoped, visibly.** `scripts/check_tenant_scoping.py` reads the AST at each `select()` call site. A tenant predicate hidden behind a variable is one it cannot see, and it will fail you — this happened during Phase 10 and the fix is to write the predicate inline in `.where()`.

4. **Configuration over code.** Anything a customer could plausibly want different is tenant data — versioned, effective-dated, auditable. `scripts/check_domain_literals.py` fails the build on a category, role, ward, or language tag hardcoded in a domain package. **Add your new package to `DOMAIN_PACKAGES` in both checkers on its first commit.**

5. **Deterministic ≠ hardcoded.** The safety fail-safe is a governed, hot-reloadable ruleset that still executes as a hard rule, in document order, first match wins, no regular expressions.

6. **Every external call degrades on the record.** Timeout, retry budget, fallback, and a `pipeline_stage_degraded` or `system_degradation` event. `StageOutcome.DEGRADED` is deliberately distinct from `FAILED`: correct degradation must not inflate the failure ratio that pages a human.

7. **Prove, don't log.** Every phase has a machine-checkable exit gate. A gate is never waived to make progress look faster — a skipped gate is technical debt with a false receipt.

8. **The honest number ships either way.** Phase 9 published four categories below its F1 floor. Phase 10 published a failing gate. Do the same. Tuning a threshold against the only corpus you have produces a number about the tuning, not about the system.

9. **Every non-obvious decision gets an ADR.** `docs/adr/`. Check the highest existing number before you claim one — Phase 10 collided with Phase 9 by not doing this.

---

## 5. Working here

### Setup

```bash
docker compose up -d
```

Then verify:

```bash
nem check
```

`nem` is a zero-install task runner (`./nem` on POSIX, `nem.cmd` on Windows). Run
`nem help` for the full list. The ones you will use constantly:

| Command | What it does |
|---|---|
| `nem up` / `nem down` | Start / stop the stack |
| `nem check` | Every quality gate CI runs — lint, format, types, tests, coverage, CI checkers |
| `nem test -- -k pattern` | Run a subset of the suite |
| `nem types` | `mypy --strict` |
| `nem migrate` / `nem makemigration` / `nem rollback` | Alembic |
| `nem doctor` | Diagnose a broken stack |
| `nem obs` / `nem obs-verify` | Observability profile, and prove a metric reaches Grafana |
| `nem gate-phase<N>` | Run a phase's exit gate against the live stack |
| `nem f1` | Reproduce the Phase 9 perception report |
| `nem dedup-eval` | Reproduce the Phase 10 dedup report |

### A trap that will cost you an afternoon

Running `pytest` inside the container **without** `NEMESIS_TEST_ADMIN_DSN` silently
skips ~400 database tests and exits 0. The container's `localhost` is not
Postgres. Always go through `nem test`, which injects it. If you see a suspiciously
fast green run, this is why.

### Tests that need the app's own engine

`execute_stage` opens its own transaction through `session_scope()`, which
resolves its engine from process-global settings. A test that drives a pipeline
stage must request the `bound_session` fixture or it will silently exercise the
*application* database and then assert against the throwaway one. The symptom is
`complaint … has no events`, which sends you looking at the event store instead
of the fixture.

---

## 6. Phase 10's open gate — read before touching dedup

Phase 10 is implemented, tested, and **fails one gate clause on purpose.**

| Clause | Result |
|---|---|
| Measured precision/recall on a labelled fixture set | ✅ precision 0.600, recall 0.600, reproducible |
| **Zero false-positive merges** | ❌ **4 found** |
| Stage 1 eliminates ≥90% before embedding comparison, by query plan | ✅ 98%, `EXPLAIN` names the GiST index |
| Decision latency within §27.1 | ✅ p95 15.5 ms against 10 s |

The four false merges are **one root error and three cascades** — once a cluster
holds two incidents, every later report of either merges into it correctly by its
own lights. That is exactly the §14.3 mechanism the gate exists to catch.

**Do not fix this by moving a threshold.** The true-duplicate and false-merge
confidence distributions interleave completely (highest true 0.8781, lowest false
0.8661); no threshold separates them. It is proven not to be an engine defect:
`tests/test_dedup_harness.py` shows the same engine and harness reach precision
1.0 / recall 1.0 when the encoder can separate incidents. The limitation is that
`multilingual-e5` compresses same-domain civic complaints into a 0.82–0.88 band,
inside which "the same pothole" and "another pothole on this street" are closer
than the noise.

Two remedies, in [BACKLOG.md](BACKLOG.md) as **B-10.1** and **B-10.2**. Full
diagnosis in [`reports/dedup-precision-recall.md`](reports/dedup-precision-recall.md).

---

## 7. Carried-forward gaps

Debts the system currently owes. Each one is real, disclosed, and inherited by
whoever touches the relevant area next.

| # | Gap | Owed since | Consequence |
|---|---|---|---|
| **G1** | **No photograph corpus.** The image modality is completely unmeasured — CLIP prompt sets ship unvalidated, and dedup `image_weight` has never been exercised. | Phase 9 | Directly causes Phase 10's gate failure. **The single highest-value item in the backlog.** |
| **G2** | **§22.1 distant-face recall is 0.00 below 80 px.** `blaze_face_short_range` is a two-metre model; street photography is full of small bystanders, who are exactly the population §22.1 protects. | Phase 0 → 9 | Bystanders in the background of a street photo are not being blurred. This is a privacy obligation, not a feature. |
| **G3** | **Transcription quality unmeasured.** The gate proves faster-whisper decodes real audio and runs its front end, but the clip is a synthesised tone and the VAD correctly discards it, so the acoustic model never runs. | Phase 9 | §8.4's multilingual promise is unproven. Needs licensed spoken audio. |
| **G4** | **Marathi scores materially worse** than English and Hindi (macro F1 0.385 vs 0.638 / 0.630) and the corpus cannot say why — encoder coverage, prompt wording and corpus size are not separable at four examples per category. | Phase 9 | A language the product promises is measurably worse and nobody knows the cause. |
| **G5** | **Whisper breaches the §27.1 stage budget past ~8 seconds of audio** (≈1.15× real time on this CPU) while `MAX_AUDIO_SECONDS` permits 300. Degrades correctly; the practical ceiling is recording length. | Phase 9 | Not a break — a measurement nobody had taken. |
| **G6** | **No cluster-to-cluster merge.** `ComplaintCluster.superseded_by_id` is filtered on but never written. Reversal removes one member; retiring a whole cluster in favour of another has no path. | Phase 10 | A reviewer resolving an ambiguous dedup has no action to take. Needed by Phase 14. |
| **G7** | **Engineering standards declared but not enforced:** no `mutmut` (mutation testing), no `k6` (load/SLO thresholds), no `toxiproxy` (fault injection), no consumer-driven contract tests. | Phase 0 | §27.1 latency budgets are unproven end to end after ten phases. |
| **G8** | **§44 REAL/SIMULATED/ROADMAP table** in the blueprint has not been reconciled since Phase 3 and drifts further each phase. | ongoing | Deferred to Phase 29 by plan, but the drift compounds. |
| **G9** | **`CHANGELOG.md` `[Unreleased]` is stale** — it lists only README/logo commits. Phases 3–10 are absent from generated output. | Phase 3 | The document itself says a wrong changelog is worse than none, because it is read as a record during an incident. |

---

## 8. Where the truth lives

| Question | File |
|---|---|
| What is the plan, and what is a phase's exit gate? | [`PHASES.md`](PHASES.md) — **the plan of record** |
| What remains, task by task? | [`BACKLOG.md`](BACKLOG.md) |
| What should we improve or upgrade? | [`UPGRADES.md`](UPGRADES.md) |
| Why was this designed this way? | [`adr/`](adr/) — 36 decision records |
| It is 2am and something is broken | [`runbooks/`](runbooks/) — 27 pages, one per failure scenario |
| What did we actually measure? | [`reports/`](reports/) — F1, dedup precision/recall, HNSW recall |
| What is the product supposed to be? | `NEMESIS-Blueprint-v2.md` — §-numbers throughout the code point here |
| How do I get the stack running? | [`GETTING-STARTED.md`](GETTING-STARTED.md) |
| Which models, and how are they cached? | [`MODELS.md`](MODELS.md) |
| How do we release? | [`RELEASE.md`](RELEASE.md) |
| How are secrets handled and rotated? | [`SECRETS.md`](SECRETS.md) |

Code comments carry the reasoning. This codebase writes long docstrings on
purpose — if a module explains why it exists and what it refuses to do, read that
before changing it. Several of them record a defect that was found the hard way.

---

## 9. Definition of done

A phase is complete when **all** of these hold. Not four of five.

1. `nem check` passes — lint, format, `mypy --strict`, tests, coverage floor, all CI checkers.
2. The phase's exit gate in `PHASES.md` passes as a script, against the running stack.
3. Migrations are reversible: `alembic downgrade -1 && alembic upgrade head && alembic check`.
4. An ADR exists for every non-obvious decision (check the highest number first).
5. Any new external dependency has a runbook and an alert.
6. `PHASES.md` records what shipped, what the gate measured, and **what the gate caught** — the defect log is the most useful part of that document.
7. Any shortfall is published as a number, not omitted.

If the gate cannot pass, ship the honest measurement and say so — Phase 10 is the
worked example of how to do that well.

---

## 10. Recommended order

Reasoning, not just sequence:

1. **G1 — build the photograph corpus.** It unblocks Phase 10's gate, retires the largest carried-forward gap, and is a prerequisite for Phase 11's evaluation sets. Everything in Track C is downstream of it.
2. **Phase 12 — severity, routing, SLA.** No dependency on the image gap. Phases 6 and 7 currently govern a rubric that *nothing evaluates* — the control plane can approve a severity policy that changes no behaviour whatsoever. This is the largest gap between what the system claims and what it does.
3. **Phase 13 — identity & authorization.** The longest-lead blocker in the repository: Track D and the entire frontend are behind it, and today's auth is API keys plus a shared control-plane token.
4. **Phase 11 — ML platform.** Deliberately after 12 and 13, because its premise is that human decisions become training labels, and the richest label sources (merge overrides, review outcomes, closure confirmations) only start accumulating once 12–15 exist.
5. **Phases 14 → 15 → 16 → 17**, then Tracks F, G, H, and finally 29.

**Phase 1b stays deferred** until a deploy target is chosen. Do not start it
speculatively; the secret manager and the IaC target are coupled decisions.
