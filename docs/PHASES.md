# NEMESIS — Engineering Program Plan

Execution plan for NEMESIS as a **multi-tenant product company**, not a single
deployment. Work runs in nine tracks owned by named functions. Within a track,
phases are sequential; across tracks they parallelise on the stated dependencies.

Every phase declares an **exit gate**: an objective, machine-checkable condition.
A phase is not complete until its gate passes. Gates are never waived to make
progress look faster — a skipped gate is technical debt with a false receipt.

Section references (§) point at `NEMESIS-Blueprint-v2.md`.

---

## Critique log — where the previous revision of this plan was wrong

Kept in the repository deliberately. A plan that hides its own revision history
teaches nobody, and the same mistakes get made again by the next person.

| # | Defect | Consequence if shipped | Corrected by |
|---|---|---|---|
| 1 | **Domain model hardcoded.** Five fixed categories, a closed role enum, safety keywords in source, languages pinned to hi/mr/en | Ships one vertical for one city. A campus has no potholes — it has elevator faults, lab spills, and HVAC failures. Every new customer is a code change | Track B (Control Plane) |
| 2 | **No control plane.** Every tuning knob was a constant, a config field, or an env var | An admin cannot change a severity weight without an engineer and a deploy. §13.3's "rubric improves as data accumulates" was unimplementable | Phases 5–7 |
| 3 | **No way to evaluate a config change before it goes live** | Someone retunes a dedup threshold and discovers the damage in production, on real citizen reports | Phase 7 (simulation & backtesting) ✅ |
| 4 | **One environment.** No dev/staging/prod, no IaC, no promotion pipeline | "It works on the laptop" is not a release process. First real customer has nowhere to run | Phase 1 |
| 5 | **ML without an ML platform.** A single validation run is not MLOps | No drift detection, no model registry, no champion/challenger. Accuracy rots silently and nobody finds out until a citizen does | Phase 11 |
| 6 | **The feedback loop was thrown away.** Human review decisions and citizen disputes are free, high-quality labels | The system never gets smarter. §4.3's entire moat thesis — the accumulating proprietary dataset — was asserted but never built | Phase 11 |
| 7 | **Authorization was RBAC over a fixed enum** | Real orgs need custom roles, ward-scoped grants, delegation during leave, contractor sub-users, and audited impersonation for support | Phase 15 (ABAC/ReBAC) |
| 8 | **Compliance was a checklist, not a program** | DPDP requires a consent registry, data-subject-request fulfilment, automated retention, DPIA, and a breach runbook — as *running systems*, not policy prose | Phase 26 |
| 9 | **No commercial layer at all.** §28 states pricing with no implementation anywhere | Cannot onboard a tenant, meter usage, invoice, or support a customer. The business model existed only on a slide | Track H |
| 10 | **No analytics.** §41 KPIs were "instrumented" with nothing to instrument into | Metrics with no warehouse, no metrics layer, and no experimentation cannot drive a decision | Phase 24 |
| 11 | **Event schema had no evolution story** | An append-only log lives forever. The first payload field change breaks replay permanently, and event sourcing becomes a liability | Phase 2 (schema registry + upcasting) |
| 12 | **No API versioning or deprecation policy** | §16.3 promises journalists and civil society a public API, then breaks it silently on the next deploy | Phase 3 |
| 13 | **Design was tokens and Storybook, not a design practice** | No research, no usability testing, no content design, no localisation. A government tool that field staff find confusing is a shelf-ware purchase | Phase 17 |
| 14 | **No field reality.** PWA/offline was deferred for demo reasons | Field staff work in basements, back lanes, and dead zones. Offline is not a nice-to-have for the people expected to upload closure evidence | Phase 21 |
| 15 | **No support or operations tooling** | Every customer question becomes an engineer running SQL against production | Phase 27 |

---

## Architectural principles

These resolve design arguments. When two options both look reasonable, the one
that satisfies more of these wins.

1. **Configuration over code.** Anything a customer could plausibly want
   different is *data* — tenant-scoped, versioned, effective-dated, auditable —
   never a constant, an enum, or an env var. The test: could a solutions
   engineer onboard a new campus without opening an editor?
2. **Deterministic does not mean hardcoded.** §11.2's safety fail-safe stays
   deterministic and non-probabilistic — that property is about *predictability*,
   not about living in source. It becomes a versioned, approved, hot-reloadable
   ruleset that still executes as a hard rule.
3. **Multi-tenant from row zero.** Tenant isolation is designed in, never
   retrofitted. Retrofitting tenancy is a rewrite.
4. **Every human decision is training data.** Review-queue outcomes, merge
   overrides, and citizen disputes flow back into evaluation sets and threshold
   tuning. This is the mechanism behind the §4.3 moat, not a metaphor for it.
5. **Evolvability is a correctness property.** Event schemas version and upcast;
   APIs version and deprecate on a published clock.
6. **Prove, don't log** (§6.1). Every claim carries an evidence trail. Applied to
   the system itself: every gate in this document is a test, not an opinion.
7. **Nothing ships without an owning function and an on-call.** A component
   nobody owns is a component nobody fixes at 2am.
8. **Fair to both sides** (§6.5). Accountability features ship with their appeal
   path in the same phase, never "later".

---

## Owning functions

| Code | Function | Accountable for |
|---|---|---|
| **PLT** | Platform Engineering | Event store, APIs, orchestration, control plane |
| **DATA** | Data & ML | Perception, dedup, ML platform, analytics, experimentation |
| **PROD** | Product & Design | UX research, design system, 3D experience, content, i18n |
| **SEC** | Security, Privacy & Compliance | Threat model, authz, DPDP program, audit, pen test |
| **SRE** | Reliability & Infrastructure | Environments, IaC, CI/CD, observability, DR, on-call |
| **BIZ** | Commercial & Operations | Tenancy, metering, billing, support tooling, customer SLAs |

---

## Track map

| # | Phase | Track | Owner | Depends on |
|---|---|---|---|---|
| 0 | Foundation & guardrails ✅ | 0 · Operating System | PLT | — |
| 1a | Engineering operating system (local-first) ✅ | 0 · Operating System | SRE | 0 |
| 1b | Cloud environments & promotion pipeline | 0 · Operating System | SRE | 1a + a chosen deploy target |
| 2 | Event store, schema registry & tenancy ✅ | A · Platform | PLT | 1a |
| 3 | Ingestion, orchestration & realtime transport ✅ | A · Platform | PLT | 2 |
| 4 | Public API, versioning & integration platform ✅ | A · Platform | PLT | 3 |
| 5 | Tenant, taxonomy & organisation service ✅ | B · Control Plane | PLT | 2 |
| 6 | Policy & rules engine ✅ | B · Control Plane | PLT · DATA | 5 |
| 7 | Configuration simulation & backtesting ✅ | B · Control Plane | DATA | 6 |
| 8 | Trust & safety spine ✅ | C · Intelligence | DATA · SEC | 3, 6 |
| 9 | Perception layer & model registry | C · Intelligence | DATA | 3, 5 |
| 10 | Deduplication & clustering engine | C · Intelligence | DATA | 2, 9 |
| 11 | ML platform: labelling, drift & feedback loop | C · Intelligence | DATA | 9, 10 |
| 12 | Severity, routing & SLA engine | C · Intelligence | DATA | 6, 10 |
| 13 | Identity, authorization & org modelling | D · Accountability | SEC | 5 |
| 14 | Department workflow & work orders | D · Accountability | PROD · PLT | 12, 13 |
| 15 | Closure loop & evidence verification | D · Accountability | PLT | 14 |
| 16 | Investigation Agent & agent platform | D · Accountability | DATA | 9, 10 |
| 17 | Contractor transparency, fraud & equity | D · Accountability | DATA · SEC | 15 |
| 18 | Design system, application shell & i18n | E · Experience | PROD | 4, 13 |
| 19 | Geospatial 3D engine | E · Experience | PROD | 18 |
| 20 | Signature scenes & shader layer | E · Experience | PROD | 19, 10 |
| 21 | Temporal replay — event-log time machine | E · Experience | PROD · PLT | 19, 2 |
| 22 | Field & offline experience | E · Experience | PROD | 18 |
| 23 | Analytics platform & metrics layer | F · Data | DATA | 2, 12 |
| 24 | Experimentation & continuous improvement | F · Data | DATA | 23, 7 |
| 25 | Security hardening & threat verification | G · Trust | SEC | 13, 17 |
| 26 | Privacy & DPDP compliance program | G · Trust | SEC | 13, 23 |
| 27 | Tenant operations, metering & support console | H · Commercial | BIZ | 5, 13 |
| 28 | Performance, resilience & disaster recovery | H · Commercial | SRE | 1a, 25 |
| 29 | Seed, E2E & release certification | I · Release | all | all |

---

## Engineering standards — enforced in every phase

### Correctness
| Standard | Enforcement |
|---|---|
| Static typing | `mypy --strict` (Python), `tsc --strict` + `noUncheckedIndexedAccess` (TS) |
| Lint / format | `ruff`, `eslint`, `prettier` — CI-gated, zero warnings tolerated |
| Schema evolution | Alembic migrations only, forward *and* backward tested. Never `create_all()` |
| Event evolution | Every event type is registered, versioned, and has an upcaster. A payload change that breaks replay fails CI |
| Boundary validation | Pydantic v2 on every request, response, task payload, agent tool result |
| Write path | State change + event row in **one transaction** (§9.1). No state mutation without an event |
| Idempotency | Every ingest and task carries an idempotency key; redelivery is a provable no-op |
| Concurrency | Optimistic version columns on mutable aggregates; `FOR UPDATE` on the event chain tail |
| API contract | OpenAPI generated from code, then generating the TypeScript client. Frontend and backend cannot drift |
| Tenancy | Every table carries `tenant_id`; every query is tenant-scoped by construction, verified by a static check |
| No magic values | A literal that affects behaviour must resolve from the control plane. CI greps for banned inline constants in domain modules |

### Verification
| Standard | Enforcement |
|---|---|
| Unit + integration | `pytest` against real Postgres + PostGIS + pgvector. Never a mocked datastore |
| Property-based | `hypothesis` on the hash chain, severity arithmetic, dedup bands, and authz decisions |
| Mutation testing | `mutmut` on scoring, dedup, authz, and chain modules — coverage that survives mutation, not line count |
| Contract testing | Consumer-driven contracts between API and clients; a breaking change fails the provider build |
| Load / SLO | `k6` scenarios asserting §27.1 budgets as CI thresholds |
| Fault injection | `toxiproxy` between services; every degradation path exercised, not assumed |
| Frontend | Playwright E2E, Storybook, golden-image visual regression, `axe`, Lighthouse budgets |
| ML | Held-out evaluation, drift monitors, champion/challenger, and a labelled regression set that grows with every production error |

### Operations
| Standard | Enforcement |
|---|---|
| Environments | dev → staging → prod, provisioned by IaC, promoted by pipeline, never by hand |
| Tracing | OpenTelemetry across API → queue → agent → DB, correlation ID as trace baggage |
| Metrics | Prometheus; the §41 KPI set instrumented from day one, surfaced in Phase 23 |
| Feature flags | Every risky path ships behind a flag with a documented kill switch |
| Failure policy | Every external call has timeout + retry budget + fallback + a `system_degradation` event |
| Supply chain | SBOM per image (`syft`), vulnerability scan (`grype`), base images pinned by digest |
| Decisions | ADR in `docs/adr/` for every non-obvious choice; RFC for anything cross-track |
| On-call | Every shipped phase names an owning function and enters the on-call runbook |
| Done means | The five gates in §37.4, including reconciling the §44 REAL/SIMULATED/ROADMAP table |

---

# Track 0 — Engineering Operating System

## Phase 0 — Foundation & guardrails ✅ · PLT

**Shipped**
- Compose stack: Postgres 17.5 + PostGIS 3.5.2 + pgvector 0.8.6, Redis 7.4, `api`, `worker-io`, `worker-ml`, `beat` — healthchecked, memory-capped to ~6 GB
- Typed configuration where rubric weights and dedup bands are *validated invariants*, plus production safety guards that refuse to boot a pilot with the development signing key or a wildcard CORS policy
- `structlog` JSON with correlation IDs propagated via `ContextVar`, emitted through the stdlib logger so a replaced stream cannot permanently break logging
- OpenTelemetry tracing instrumented across FastAPI, SQLAlchemy, asyncpg, Redis, and Celery — exporter-optional, so no collector is a startup dependency
- Prometheus `/metrics` with domain metric families for the §41 KPI set, and label cardinality bounded to route templates
- RFC 9457 Problem Details error contract with no internal disclosure, plus security headers and CORS
- Liveness/readiness split where **readiness returns 503** on dependency failure
- Async SQLAlchemy engine, transactional `session_scope`, declarative base with stable constraint naming
- Celery three-queue topology (`io`, `ml`, `safety`) split by memory profile
- Alembic on the async driver, sourcing its URL from `Settings`
- pytest harness creating a throwaway database per session, with property-based tests over configuration invariants
- Zero-install task runner (`nem`), pre-commit hooks with secret scanning, and six ADRs
- CI in six jobs: static → tests → migration reversibility → stack boot & probe contract → SBOM/vulnerability scan → secret scan
- **All model weights fetched and verified into an offline cache** (3.0 GB): CLIP ViT-B-32, multilingual-e5-small, faster-whisper small, BlazeFace, plus the host-side `llama3.1:8b`. Every model is loaded and executed, not merely checked for existence — see `docs/MODELS.md`

**Gate:** ✅ ruff clean · ruff format clean · mypy --strict clean (17 modules) · **86 tests passing** · **92.27% coverage** against an 85% floor · `alembic check` clean · 13 pre-commit hooks passing · 6/6 services healthy in ~16 s · readiness verified returning 503 with the database stopped, recovering within 2 s · **air-gap verified under `--network none`**: all four cached models load and infer, only the host Ollama service fails (correctly)

**Defects the gate caught** (each now covered by a regression test or a documented control):
1. `/ready` returned 200 while reporting `degraded` — every orchestrator routes on the status code, so a broken instance kept taking traffic
2. YAML merge keys replaced the compose `environment` map wholesale, silently pointing `worker-ml` at `localhost` (ADR-0005)
3. `beat` inherited an HTTP healthcheck it could never satisfy
4. `PrintLoggerFactory` bound `sys.stdout` at configuration time, so a replaced stream broke logging permanently
5. Starlette's deprecation of `HTTP_422_UNPROCESSABLE_ENTITY` raised *inside* the validation handler under `filterwarnings = ["error"]`, turning every 422 into a 500
6. RFC 9457 `instance` reflected the resolved path, echoing user-controlled input into the response body
7. Session-layer tests silently exercised the application database instead of the throwaway one
8. The Dockerfile restated the dev dependency list instead of reading `pyproject.toml` — the same hardcoding antipattern the plan condemns, and it had already drifted
9. MediaPipe 1.x removed `mp.solutions` entirely; the blueprint's face-blur approach targets a deleted API. Rewritten against the Tasks API, whose `.tflite` bundle is now an explicitly fetched, configured artefact
10. MediaPipe then failed at *runtime* on `libEGL.so.1` — its Tasks runtime links a GPU delegate at load even for CPU inference. The image built cleanly and only broke at first use
11. `trailing-whitespace` silently rewrote 12 lines of the hand-tuned `README.md`, including the centred navigation block. Reverted and the hook scoped away from it — presentation whitespace is an authoring decision, not lint

10. **The read endpoint replayed the whole event log on every 200**, while its
    own docstring claimed it read the projection. §27.3 turns that endpoint into
    a 5-second poll per client whenever the WebSocket is unavailable, so the
    cost the entire Phase 2 projection layer exists to avoid was being paid on
    the hottest read in the system — and the comment above it said the opposite,
    which is the part that would have kept anyone from looking. Fixed by
    materialising the two projected degradation fields as columns and serving
    the response from one query; `§26.2`'s `work_order_id`, which had been
    permanently `null`, is now resolved by a correlated subquery. The test
    breaks `replay_entity` outright, so the handler reaching for it again fails
    rather than merely getting slower
11. **The submission handler blocked the event loop on task dispatch.**
    `apply_async` is synchronous Redis I/O; called directly from an async
    handler it stalls not just that request but every other request the process
    is serving, against a §27.1 budget of two seconds for the acknowledgment. A
    merely slow broker would have become a process-wide latency spike
12. **A dead realtime listener was never restarted.** If the Redis read loop
    died — a restart, a dropped connection — nothing recreated it until the
    *next* client connected. Already-connected clients kept receiving heartbeats
    from a separate task and stopped receiving events, so the socket looked
    healthy from both ends and was deaf, which is strictly worse than a dropped
    connection a client knows to reconnect from. The heartbeat now supervises
    the listener and re-subscribes before restarting it, because a read loop
    resumed on a pub/sub object whose socket is gone yields nothing forever —
    the same deafness with a log line claiming it was fixed
13. **`get_limiter` ignored its arguments after the first call**, so a process
    building a second application with different limits silently used the
    first's budgets. Invisible in production, where there is one configuration,
    and wrong in exactly the case that catches it: a test asserting against a
    configuration nothing is running
14. **The tenancy guard refused the read endpoint's outer join**, correctly: a
    column-to-column `tenant_id` comparison in an ON clause scopes the join, not
    the table. Recorded because it is the guard earning its keep on new code
    written by someone who had just spent a day inside ADR-0014 — and the fix
    (a correlated subquery carrying its own explicit predicate) is better SQL
    than the join was, since a cluster can carry more than one work order and
    the join needed an ordering and a limit on the outer query to survive it

**Carried forward, not silently absorbed:** `blaze_face_short_range` detects faces within ~2 m, but street photography contains small distant bystanders — exactly the population §22.1 requires blurring. MediaPipe 1.x ships no full-range alternative. Phase 8 must measure distant-face recall and remediate; `face_detector_min_confidence` is already biased to 0.4 on the reasoning that a missed face is a privacy breach while a false positive only blurs pavement.

## Phase 1a — Engineering operating system (local-first) ✅ · SRE

Everything that makes the next 28 phases repeatable. Built before feature work,
because retrofitting a release process is how startups acquire permanent scar
tissue.

> **Scoped local-first (decision, 2026-08-16).** No cloud provider or deploy
> target has been chosen. Terraform written for an unselected provider gets
> rewritten the moment one is picked, so the cloud half of this phase is
> deferred to **Phase 1b**, triggered by choosing a deploy target rather than by
> a date. Everything in 1a pays off immediately on a local stack; none of it is
> throwaway.

**Shipped**
- **Observability stack** as an opt-in compose profile (ADR-0007): OpenTelemetry
  collector, Tempo, Prometheus, Alertmanager, and Grafana with provisioned
  datasources and dashboards. Prometheus scrapes the application directly and
  the collector carries traces only (ADR-0008)
- **§41 KPIs defined exactly once**, as Prometheus recording rules — a
  deliberate precursor to Phase 23's governed metrics layer. Every panel and
  every alert reads a `nemesis:kpi_*` rule, never a raw histogram, so a
  dashboard and an alert cannot disagree about the same number
- **17 alert rules** over the §27.1 budgets, the §27.3 failure scenarios, and
  KPI drift, with Alertmanager grouping and inhibition so one stopped dependency
  produces one alert rather than four. A dead-man's-switch heartbeat proves the
  alerting path itself is alive — no conditionally-firing alert can
- **Three §41 KPIs deliberately have no panel**, and say so on the dashboard:
  dedup precision, dedup recall, and safety false-negative rate are audit
  results, not runtime observations. A proxy that looks like the KPI is worse
  than a visible gap
- **Feature flag service** with kill switches, tenant targeting, salted
  stable-hash staged rollout, and a `remove_by` date per flag that CI enforces.
  Mutation is CLI-only until Phase 13 ships authorization (ADR-0009); read-only
  listing is exposed at `/ops/flags`. Under a failed store the last snapshot is
  **retained**, never cleared — clearing would silently release every kill
  switch at the moment the system is already unhealthy
- **Secrets rotation procedures** per secret, with blast radius stated. CI fails
  if a secret in the deployment contract has no procedure, and if a secret
  appears in compose as a literal rather than a substitution
- **Release policy**: semantic versioning, conventional commits, a changelog
  generated from history, and a published deprecation clock — 12 months for the
  §16.3 public API, never for event payload versions
- **RFC process** alongside the existing ADR practice; **incident process** with
  severity definitions, a blameless post-mortem template, and a tracked action
  register
- **16 runbook pages** covering every §27.3 scenario and every declared
  dependency. Coverage is checked by parsing §27.3 **from the blueprint**, so a
  scenario added later fails CI rather than being noticed never
- **Dependency-update automation**, grouped so a weekly PR gets read rather than
  rubber-stamped, plus a weekly re-scan of unchanged images — Dependabot reports
  new versions, not new CVEs against pinned ones
- **Environment parity check** over a deployment contract enumerating what a
  deployed environment must supply, verified from both directions: against
  `Settings` in the test suite, and against `.env.example` and compose by a
  standard-library script

**Gate — met**
- ✅ `nem obs-verify` walks the full chain: application metric → Prometheus
  scrape → recording rules → alert rule → Alertmanager → provisioned Grafana
  dashboard → datasource query. Ten steps, all green
- ✅ Kill switch exercised end to end: `nem flag kill` from a separate process
  changed the API's resolved state within one reload interval, **no restart**,
  and `nem flag clear` restored it
- ✅ Traces reach Tempo across the FastAPI → SQLAlchemy boundary, verified by query
- ✅ 3/3 §27.3 scenarios have a runbook page; every alert's `runbook_url`
  resolves to a file that exists
- ✅ ruff clean · ruff format clean · mypy --strict clean (23 modules) ·
  **158 tests passing** · **95.05% coverage** against an 85% floor · promtool,
  amtool, and collector configs all validate
- ⚠️ **Incident rehearsal not run.** The process, template, and action register
  are in place and the runbooks are written, but no drill has been performed —
  a rehearsed incident needs a deliberately induced failure and a second person
  to be useful. Carried as the one open item of this gate rather than marked
  complete; Phase 25's fault-injection suite is the natural moment to run it

**Defects the gate caught** (each now covered by a regression test):
1. **`/ready` was excluded from metrics as well as logs.** Phase 0 used one
   quiet-path set for both, justified only by log volume. The reasoning does not
   transfer: `NemesisReadinessFailing` counts 503s on `/ready`, and the
   middleware refused to create that series — so the alert could never fire. An
   alert that cannot fire is worse than no alert, because it reads as coverage
2. **The flag CLI bound `sys.stdout` at import time** — the identical defect
   Phase 0 hit with `structlog`'s `PrintLoggerFactory`, and it recurred in new
   code written by someone who had read that note
3. **A latent suite-wide flake.** The `client` fixture created a process-global
   engine and Redis client that nothing disposed, because `ASGITransport` does
   not run lifespan events. The GC finalised them at arbitrary points and
   `filterwarnings = ["error"]` turned the `ResourceWarning` into a failure
   attributed to whichever unrelated test was running. Intermittent, and it
   blamed the wrong test every time
4. **`service_version` was a hardcoded literal** restating `pyproject.toml`, so
   the running service could report a version the artefact did not have — at
   exactly the moment somebody is trying to work out what changed
5. **The observability checker flagged its own documentation.** The header of
   `alerts.yml` quotes `stage="..."` while explaining the rule, and the scanner
   reported `...` as an undeclared stage. Comment-stripping added
6. **Check scripts crashed while printing the failure they found** — U+2717 on a
   cp1252 Windows console, the same encoding trap `tasks.py` already handled

## Phase 1b — Cloud environments & promotion pipeline · SRE

**Deferred — trigger: a deploy target is chosen**
- Infrastructure as code (Terraform/OpenTofu) for dev, staging, production
- CI/CD promotion: build once, deploy the same artefact through environments
- Ephemeral preview environments per pull request
- Automated rollback on a failed deploy
- On-call rotation and escalation routing — meaningless without a production
  system to be paged about. Alertmanager's routing tree, grouping, and
  inhibition already work; Phase 1b adds notification integrations to receivers
  that exist, rather than building a new tree
- A secret manager, chosen with the target because the two decisions are coupled
- Always-on observability. "Opt-in observability" is a reasonable trade on a
  laptop and a bug in a deployed environment; ADR-0007 is superseded, not amended

**Gate — Phase 1b**
- An environment is destroyed and rebuilt from code alone, verified by doing it
- A commit reaches staging automatically and production behind an approval, with a proven rollback
- A deliberately broken deploy auto-rolls-back with no human action

---

# Track A — Platform Spine

## Phase 2 — Event store, schema registry & tenancy ✅ · PLT

The spine everything writes through. The phase worth over-engineering.

**Shipped**
- Full §9.2 schema as one reviewed Alembic migration with a tested `downgrade()`,
  `tenant_id` **NOT NULL on every domain table from the first migration** —
  there is no migration in this repository's future that adds tenancy
- **`events` RANGE-partitioned by month from row zero** (ADR-0011), with a
  DEFAULT partition as the safety net so a missed maintenance run is a warning
  rather than a total outage of the write path. Partitioning forces three
  companion tables — `event_chain_heads`, `event_idempotency`,
  `archived_partitions` — because Postgres requires the partition key in every
  unique constraint, which makes per-entity sequence uniqueness unenforceable
  inside the partitioned table
- **RFC 8785 canonical JSON** (ADR-0013) with ECMAScript number formatting, NFC
  normalisation, and UTF-16 code-unit key ordering. §9.3's
  `json.dumps(sort_keys=True)` is not canonical in four separate ways, each of
  which makes verification a coin flip
- `EventStore.append()` with a **widened, structured hash preimage** (ADR-0010)
  that includes `tenant_id`, `event_version`, `sequence`, and `entity_type` —
  §9.3's omits all four, so a cross-tenant row move passes verification.
  Serialised per entity by an upsert-and-lock on `event_chain_heads`, which also
  avoids the `ON CONFLICT DO NOTHING` + `SELECT` race that only appears under
  the exact concurrency it is meant to survive
- **Event schema registry**: 13 of the §9.4 types registered as versioned
  Pydantic payloads, 9 explicitly deferred to their owning phase, 1 renamed and
  recorded as such. A committed `schema_lock.json` fingerprints every released
  schema; editing one in place fails CI with the remediation in the message
- **Tenancy enforced at three layers, none of them convention** (ADR-0014): an
  AST check in CI, a `before_execute` interceptor installed on the test engine
  as well as the application one, and Postgres RLS scheduled for Phase 25 —
  named honestly rather than implied
- Projectors rebuilding current state from the log, with snapshotting so replay
  stays O(1) in history length, and a `projector_version` so a snapshot from an
  older build is discarded rather than trusted
- **The materialiser that makes those projections queryable**: idempotent,
  tenant-scoped upserts into `complaints`, `complaint_clusters`, and
  `work_orders`, with the projected log position stored as the row's `version`
  so a stale writer is refused by the database rather than by an ordering
  assumption the caller cannot actually make. `rebuild_tenant()` reconstructs
  every row for a tenant from the log alone — which is both the gate and the
  real recovery procedure, since a corrupted projection is repairable precisely
  because it was never the source of truth
- `verify_chain()` reporting the **exact offset and kind** of a break, plus the
  hourly integrity sweep §17.4 leaves ROADMAP — **closing that gap rather than
  disclosing it**. It never repairs: a chain that repairs itself has no
  evidentiary value, because the repair is indistinguishable from the tamper
- Retention and archival on `DETACH PARTITION`, reported by the daily task and
  **never acted on automatically** — retention on an append-only civic record is
  a decision with legal weight (Phase 26 owns the approval path)
- `python -m nemesis.events.inspect`, read-only, so an on-call answers the
  question with a command instead of improvising SQL against the event log —
  which is the most common cause of the break it is used to investigate
- HNSW parameters chosen against a **measured recall curve**
  (`docs/reports/hnsw-recall.md`), two runbook pages, and five ADRs

**Gate — met**
- ✅ **1000 concurrent appends across 50 entities → zero chain forks**, each in
  its own transaction on its own connection, verified chain by chain afterwards
- ✅ Full replay from an empty projection reproduces current state
  **byte-identically** — asserted twice, and the second one is the claim §9.1
  actually makes: the current-state *tables* are truncated, rebuilt from the log
  alone, and compared column by column. The projection-level version is a
  canonical-hash equality; snapshotted replay is proven equal to unsnapshotted
- ✅ A deliberately tampered row is detected **at the exact offset**, and its
  neighbours are not — the verifier continues from the stored hash rather than
  the recomputed one, so one altered row does not cascade into a whole-chain
  false positive
- ✅ An event payload change without an upcaster fails CI — **demonstrated** by
  editing a released model in place and watching the lock check fail
- ✅ A domain query without tenant scoping fails the static check —
  **demonstrated** with both an ORM and a raw-SQL probe
- ✅ Every migration applies, reverts, and re-applies cleanly; `alembic check`
  reports no drift with partitions filtered out of autogenerate
- ✅ ruff clean · ruff format clean · **mypy --strict clean (50 modules)** ·
  **267 tests passing** · **94.17% coverage** against an 85% floor · all seven
  check scripts green

**Defects the gate and the post-implementation audit caught** (each now
covered by a regression test or a fix):
1. **The canonicaliser rejected strings it had just produced.** Integers were
   bounded by `MAX_SAFE_INTEGER`, but `2**53` as a float canonicalises to
   `9007199254740992`, which `json.loads` reads back as an `int` one past that
   bound. The second attempt — testing exact double representability — failed
   too, because shortest-round-trip formatting prints the shortest string that
   *parses back* to the double, not the double's exact value. Precision policy
   belongs at the Pydantic boundary; canonicalisation must be total and stable
2. **`text()` bind parameters do not work in DDL.** `CREATE TABLE … PARTITION OF
   … FOR VALUES FROM (:range_start)` fails with "the server expects 0
   arguments". The runtime partition maintainer could never have created a
   partition; only the migration's `format()`-based path worked, so this would
   have surfaced as a mysterious default-partition fill months later
3. **`:parent::regclass` mis-parses.** SQLAlchemy's bind-parameter regex reads a
   parameter followed by the `::` cast operator as undefined, and raises for a
   parameter that is plainly there
4. **The static tenancy check accepted a mention instead of a comparison**, so
   `select(Complaint.tenant_id, Complaint.id)` passed while returning every
   tenant's rows. The two enforcement layers must agree on what counts as
   scoping, or the weaker one silently defines the policy
5. **The Phase 1a environment-parity check produced a false positive** the
   moment this phase landed: it substring-matched the literal `admin` (the local
   Grafana password) against source, and the §9.4 event type `admin_action`
   contains it. Rewritten to match whole string literals via AST — a check that
   flags an event name as a leaked credential is one people learn to ignore
6. **A circular import between models and events**, reported by Python as a
   partial-initialisation error three modules away from its cause. Resolved by
   moving the shared constant to a leaf module rather than by a local import
7. **`geoalchemy2` emitted a duplicate spatial index** alongside every explicitly
   declared one — two identical GiST indexes on the hottest geospatial column in
   the system, doubling write cost, and both invisible in the model
8. **The Postgres container has no `shm_size`**, so it gets Docker's 64 MB
   default and a parallel HNSW build fails with `DiskFullError` at 20 000 rows.
   Nothing builds an HNSW index today; the first real build at Phase 9 or 10
   volume would have hit it, most likely during a migration
9. **The scheduled integrity tasks registered with Celery as nothing at all.**
   `autodiscover_tasks(["nemesis.pipeline"])` only looks for a module named
   `tasks.py`, so `pipeline/integrity.py` contributed zero tasks and zero beat
   schedules — `celery inspect registered` reported "empty" and no error
   appeared anywhere. The sweep that detects tampering would never have run, and
   the only thing that would eventually have said so is the dead-man's-switch
   alert written to catch exactly that. Task modules are now listed explicitly
   so a missing one fails at worker startup, and the beat schedule moved to
   `celery_app` so it no longer depends on something having imported the task
   module first
10. **`.gitignore` excluded the entire ORM package.** An unanchored `models/`
    rule, added for model weights that actually live in a Docker volume, also
    matched `backend/nemesis/db/models/`. The whole §9.2 schema would have been
    absent from the commit with no error anywhere, because `git add` reports
    nothing for an ignored path. Every runtime-data rule is now anchored — the
    same pattern would also have swallowed `docs/reports/hnsw-recall.json`
11. **The `worker-io` image was stale**, missing `prometheus-client` despite it
    being a base dependency since Phase 0. Invisible while `nemesis/pipeline/`
    was empty and nothing worker-side imported metrics. Found only because
    defect #9's fix made the import explicit and the worker refused to start —
    the loud failure mode doing exactly what it was chosen for
12. **The phase was reported complete while the projection layer wrote nothing
    to the current-state tables.** The projectors, snapshots, and replay were
    built and proven deterministic, but no code materialised them into
    `complaints`, `complaint_clusters`, or `work_orders` — the tables were
    migrated, indexed, and permanently empty. The gate clause had been proven
    against the projection rather than against the tables §9.1 defines as
    current state, which is a weaker claim wearing the same words. Caught by
    re-reading the ships-list against the code rather than by any test, which is
    the uncomfortable part: **no gate can catch scope that was never
    implemented.** The gate now truncates the tables and rebuilds them from the
    log

**Carried forward, not silently absorbed:** the HNSW parameters are measured but
**provisional**. The benchmark uses synthetic clustered vectors, and the low
id-recall it reports is an artefact of near-ties in that distribution — the
distance-ratio column (1.0105 at the chosen setting) is what shows the index is
returning answers as good as exact search. Phase 9 must re-measure against real
CLIP output before these are considered settled. The `shm_size` fix belongs with
Phase 10, which knows how large the index needs to be.

**Ships**
- Full §9.2 schema as reviewed Alembic migrations, each with a tested `downgrade()`
- **`tenant_id` on every table from the first migration**, with a query-construction guard that makes an unscoped domain query fail at import time rather than at runtime
- `halfvec(512)` CLIP embeddings, `vector(384)` text — HNSW parameters chosen against a *measured recall curve*, not defaults
- `EventStore.append()` implementing §9.3 SHA-256 chaining, serialised per entity via `SELECT … FOR UPDATE` on the chain tail so concurrent appends cannot fork history
- **Event schema registry**: every event type versioned, with a declared upcaster from every prior version, and a compatibility check that fails CI on a breaking payload change
- **Deterministic JSON canonicalisation** — key order, unicode normalisation, float formatting pinned, because a chain is only tamper-evident if identical payloads hash identically
- Projectors rebuilding current state from the log, with snapshotting so replay stays O(1) as history grows
- `verify_chain()` walker plus the scheduled integrity sweep the blueprint left ROADMAP (§17.4) — closing that gap rather than disclosing it
- Retention and archival strategy for an append-only log that must live for years

**Gate**
- 1000 concurrent appends across 50 entities → **zero** chain forks, under `hypothesis`-generated interleavings
- Full replay from an empty projection reproduces current state byte-identically
- A deliberately tampered row is detected at the exact offset
- An event payload change without an upcaster fails CI
- A domain query without tenant scoping fails a static check
- Every migration applies, reverts, and re-applies cleanly

## Phase 3 — Ingestion, orchestration & realtime transport ✅ · PLT

The path from a citizen's phone to a pin on a map, and the guarantees that make
each hop survivable.

**Shipped**
- `POST /api/v1/complaints` (§26.1) with the size cap enforced **while the body
  streams** — an ASGI-level counter over `receive`, because a check on
  `UploadFile` runs once the body is already spooled to disk and enforces the
  limit precisely up to the point where enforcing it would have mattered. Content
  type comes from **sniffed magic bytes**, per field; the `Content-Type` the
  client sends is ignored entirely (§25.1), and so is the filename — stored media
  is content-addressed by its own SHA-256, so a path traversal has nothing to
  traverse with and two identical uploads are one file
- **Media lands in quarantine and Phase 3 serves nothing.** §22.1 requires face
  blur before any persistence including temp paths, and the blur is Phase 8's —
  it needs MediaPipe, which lives in `worker-ml`. So there is deliberately no
  media endpoint in this phase and the reference recorded in the event is an
  internal URI a browser cannot follow. A stated constraint rather than a gap
  glossed over: Phase 8 inserts blur-and-promote between quarantine and a served
  store, and its repository-level guard test has exactly one code path to police
  because of this
- `GET /api/v1/complaints/{id}` (§26.2), **ETag-aware** — the validator is the
  projection's `version`, which is the log position the row reflects rather than
  a hash of the body, so it is exactly as strong as the representation and free
  to compute. §27.3's polling fallback is 5 seconds per client, and this is what
  makes that affordable. The response carries no citizen data at all until Phase
  13 can say who is asking
- **Celery task graph as declarations, not control flow.** Five stages, each with
  its own queue, retry budget, backoff, declared fallback, and a
  `continue_on_degrade` flag — because whether a missing dedup pass should stop
  everything is a property of dedup (§14.3: an unmerged duplicate is the safe
  error), not of the error handling. Each stage enqueues its successor by name
  rather than running inside a Celery `chain`, so §11.2's "bypasses the scoring
  pipeline entirely" means the successors are never enqueued instead of four
  no-ops holding queue slots behind a danger signal
- **The perception, dedup, and scoring providers are deliberately absent.** Phase
  3 owns orchestration — transactions, idempotency, ordering, retry budgets, and
  what happens when a stage cannot run. Shipping a placeholder classifier would
  be Phase 2's twelfth defect repeated on purpose. The unregistered stage takes
  its declared degradation path, which is not a stub: §24.2 requires the degraded
  path to be real shipped behaviour, and a report parked because no classifier
  exists behaves exactly as one will the day the classifier is down
- **Redelivery is a provable no-op at the stage, not at the event.** Every event
  a stage emits is keyed `pipeline:<stage>:<complaint>:<index>`, and a redelivery
  detected on the *first* append abandons the whole transaction. Checking only
  the first is the point: letting each slot no-op independently means a provider
  that emits three events this time and two last time appends a brand-new event
  onto a chain that has already moved on — the stage would have half-run twice
- **Transactional outbox** (ADR-0015) written in the same transaction as the
  event, drained by a **dedicated relay process** under a Postgres advisory lock.
  The row is a *pointer*, not a payload copy: a denormalised copy would double
  what Phase 26 must erase and Phase 4 must scrub, and could drift from the row
  whose hash was signed
- **WebSocket hub `/ws/pipeline-events`** (§26.3) with a bounded queue and its
  own writer task per connection, so **no fan-out path ever awaits a socket**. A
  client that stops reading is shed — closed with a code that means "you fell
  behind" and a resume cursor in the close frame — rather than being allowed to
  stall the process serving everyone else. Heartbeats go through the same queue,
  so a dead tab fills its buffer on heartbeats alone and is shed on schedule
  instead of lingering as a healthy-looking connection
- **A published payload is empty unless a shape is declared for it** (ADR-0016).
  Strip-the-sensitive-fields fails on the next field somebody adds;
  declare-what-is-allowed fails safe on the same change. Coordinates are coarsened
  at the source to ~110 m, and §26.3's own example is knowingly not followed —
  its `merged_complaint_ids` are citizen identifiers §26.4 forbids on a public
  surface
- **Tiered rate limiting** on an atomic Redis token bucket, resolved per tenant
  plan, that **fails open and counts every time it does** (ADR-0017). The thing
  being limited is a citizen reporting a hazard; a fail-open indistinguishable
  from a normal allow is a control nobody can prove is working
- **Worker metrics export — the Phase 1a carry-forward, landed.**
  `prometheus_client` multiprocess mode, a shared `PROMETHEUS_MULTIPROC_DIR`, an
  explicit `multiprocess_mode` on every gauge, an exporter per worker container,
  stale mmap files cleared before the children fork, and Prometheus scrape jobs.
  The §41 pipeline KPI panels and alert rules already existed and would have shown
  no data indefinitely, which reads as "no traffic" rather than "no export"
- Four new alert rules and a runbook page for the failure this phase introduces:
  a relay that is alive, healthy, and hours behind
- Three ADRs, and `nem gate-phase3` — a live drill against the running stack,
  because two of the gate clauses are about *processes* and are not reachable
  from a test process

**Gate — met**
- ✅ **One submission emits the full §10 sequence in order, on chains that
  recompute.** `complaint_submitted → exif_check_completed →
  classification_scored → severity_scored` on the complaint chain,
  `cluster_created` on the cluster chain, `work_order_created` on the work-order
  chain — each verified by `verify_chain`, and each projection materialised into
  its current-state table rather than only into a projected dict
- ✅ **A redelivered stage appends nothing**, enqueues no second realtime
  publish, and reports it. A stage that fails partway leaves no partial write
- ✅ **A rolled-back transaction never emits a WebSocket event** — structurally,
  not by arrangement: the relay reads committed rows, so there is no code path
  from an uncommitted event to a socket
- ✅ **A client that stops reading is shed without stalling the hub** — asserted
  against a sink whose `send` never returns, with a healthy peer proving the hub
  kept delivering throughout
- ✅ **Rate limit tiers verified per tenant plan**, against real Redis, including
  continuous refill and the fail-open path
- ✅ **`SIGKILL` on the worker loses nothing**, drilled live: a submission made
  while the worker is dead is durably queued, the replacement completes it
  exactly once, and an untouched complaint gains no duplicate
- ✅ ruff clean · ruff format clean · **mypy --strict clean (74 modules)** ·
  **367 tests passing** · **88.54% coverage** against an 85% floor ·
  `alembic check` clean · all five check scripts green · promtool validates 24
  rules · **14/14 live gate checks passed** on three consecutive runs against
  the running seven-service stack

**Defects the gate and the post-implementation audit caught** (each now
covered by a regression test or a fix):
1. **`record_system_degradation` could never have worked.** `events.tenant_id`
   carries a foreign key to `tenants`, and Phase 2's reserved `SYSTEM_TENANT_ID`
   was a Python constant with no row behind it — so every degradation record
   would have failed on the foreign key. The failure mode is the bad kind: it
   raises *inside* the handler recording some other failure, so the second error
   masks the first, and the only code path that would ever have exercised it is
   the one that runs when the system is already broken. Phase 2 shipped it
   because a test created the row itself, which made the test pass while
   production could not
2. **Every Celery worker child processed exactly one task and then failed
   everything after it.** The task bodies called `asyncio.run`, which creates a
   *new* event loop per task, while `nemesis.db.session` caches a module-global
   engine whose asyncpg connections bind to the loop that created them. So the
   first task built the engine on loop A and succeeded, and the second was handed
   a pooled connection owned by loop A on loop B: `got Future attached to a
   different loop`. The shape is what makes it dangerous — on a quiet system,
   with tasks arriving one at a time and restarts in between, it looks like it
   works; under load it degrades every complaint behind the first one into the
   dead-letter queue, correctly and uselessly, with a stack trace that names
   asyncio rather than anything in this codebase. Fixed with one persistent loop
   per process (`nemesis/worker/loop.py`) rather than by disposing the engine per
   task, which would trade the bug for a fresh TCP connection and prepared-
   statement cache on every pipeline stage.

   **The suite could not have caught it**, and that is the uncomfortable part
   twice over. pytest-asyncio gives every test a fresh loop, so a test process
   reproduces the *working* case by construction — the live gate found it, on the
   second submission of a re-run. `tests/conftest.py` had documented this exact
   hazard for the test engine since Phase 1; the same reasoning was never applied
   to the worker. The regression test now does what a worker does rather than
   what a test normally does, plus a grep that fails if any task module reaches
   for `asyncio.run` again
3. **Starlette's deprecated `HTTP_413_REQUEST_ENTITY_TOO_LARGE` turned a 413 into
   a 500.** Phase 0 hit exactly this with 422 and fixed *that constant*; the
   class was still open, so the first oversized upload raised the deprecation
   under `filterwarnings = ["error"]` from inside the error handler. Every status
   code this API returns from an error path is now a named literal
4. **The metrics tmpfs was root-owned against a uid-10001 image.**
   `tmpfs: [/tmp/prometheus]` mounts 0755 root, and the image runs
   `USER nemesis`, so `prometheus_client` raised `PermissionError` while creating
   its mmap file — at *import* of `metrics.py`, before any application code runs,
   taking the worker into a crash loop. Nothing in Python can repair it: the
   directory has to be writable before the process starts
5. **Celery's Redis `visibility_timeout` was never set, so it was one hour.**
   Redis has no acknowledgement; `task_acks_late` is implemented by restoring a
   message after the visibility timeout, and there is no connection-drop signal
   that returns it sooner. A worker killed mid-task therefore did not have its
   work redelivered "on restart" — it had it redelivered 3600 seconds later, with
   nothing anywhere saying so. Found by the SIGKILL drill, which is the only
   thing that could have found it
6. **The static tenancy check scanned four packages and Phase 3 added four
   more.** A domain package outside `DOMAIN_PACKAGES` is not "unchecked", it is
   *silently exempt* — the newest and least reviewed query code in the system
   would have gone unread while the check reported clean
7. **`AppendedEvent` carried no `correlation_id`**, so the outbox would have had
   to re-read the event it had just written in order to stamp the published
   envelope — a query existing only because a dataclass field was missing
8. **A `text()` bind parameter for a UUID.** asyncpg sends a string and Postgres
   refuses to coerce varchar to uuid, so the migration's seed failed with a
   datatype mismatch. The same family as Phase 2's DDL and `::regclass` findings:
   a parameter that reads correctly and does not run
9. **The first backpressure test sheds every client, not just the slow one.** A
   burst broadcast with no scheduling yield fills every bounded queue. That is an
   artefact of the test rather than the hub — the production listener consumes
   the Redis subscription with `async for`, so each message is its own scheduling
   turn — but it pinned down an invariant the hub silently depends on, and the
   test now documents it rather than working around it

**Carried forward, not silently absorbed:**
- **Tenant resolution is an `X-Tenant-ID` header, and it is not authentication.**
  Phase 13 owns identity. Every consequence is confined to one function, so that
  phase's change is a function body rather than a refactor
- **Rate-limit tiers are typed configuration keyed by `tenants.plan`**, not
  control-plane policy. Phase 6 makes them versioned and effective-dated
- **In-flight `SIGKILL` recovery is bounded at 360 seconds, not instant.** The
  queued case recovers immediately and is drilled; the in-flight case is a Redis
  visibility timeout and is stated rather than implied
- **`HALTED_FOR_REVIEW` deliberately does not invent a status.** A halted
  complaint keeps a truthful status — it genuinely has not been classified — and
  the projection and the API response carry `degraded_stage` and
  `degraded_fallback` so a client can tell "still processing" from "stopped,
  waiting for a human". Adding a lifecycle state is a migration plus an upcaster,
  which `domain/lifecycle.py` argues is the correct cost for changing the shape
  of the pipeline and the wrong cost for describing a degradation
- **The human review queue that works the dead-letter table is Phase 8's.** Phase
  3 ships the table, the events, and the metrics; §11.4's reviewer UI is not here
- **A rejected submission can orphan an already-stored file in quarantine.**
  Left deliberately: the store is content-addressed, so deleting "the file
  this request wrote" can delete the file a *different* complaint
  references, because identical bytes are one file. Trading an orphan for the
  possible deletion of another citizen's evidence is not a trade worth making
  at the request layer. Phase 8 already walks quarantine to blur and promote,
  and reclaiming what the log does not reference belongs in that sweep, where
  the log is the authority on what is referenced

## Phase 4 — Public API, versioning & integration platform ✅ · PLT

§16.3 promises civil society and journalists a durable public interface. That is
an API product with a compatibility obligation, not an endpoint.

**Shipped**
- **The §26.4 public API, opt-in per tenant and k-anonymous** (ADR-0021). Two
  things §26.4 does not say, because it was written for a single-tenant
  deployment, and both are disclosure decisions rather than engineering ones.
  The tenant slug is in the path — a UUID would make every URL uncitable, and
  §16.2's "bookmark-able by journalists" is the requirement. And
  `public_api_enabled` **defaults to false**: the code being *capable* of
  publishing a customer's figures is not the customer having agreed to, and a
  permissive default would have made the first silently mean the second for
  every tenant already provisioned
- **Suppression below a floor, because "privacy-scrubbed" cannot mean anything
  else.** A ward summary over two complaints is not an aggregate — it is one
  citizen's report with a category, a place, and a time, every field
  individually scrubbed and the row still identifying a person to anyone who was
  on that street. Buckets below the floor are withheld **and say so**: zero
  reports is a real public fact and between one and the floor is not, and a
  consumer that cannot tell a withheld bucket from an absent one will read the
  second from the first. The withheld *count* is published for the same reason.
  The floor is clamped up, never down — a tenant configured at 1 has turned an
  aggregate endpoint into a per-complaint feed, and failing the request instead
  would take a transparency page offline over a configuration mistake
- **Budgets deliberately not suppressed, contractors deliberately are.** A
  budget line is public finance about a municipality; withholding it because one
  scheme funded a ward hides what an RTI applicant came for. A contractor with
  two jobs and one dispute has a published "33% dispute rate" that is noise
  presented as a finding about a named company — §16.4 ships the appeal path in
  the same phase, and the honest first line of that defence is not publishing a
  figure that cannot mean anything
- **The scrub is default-deny, extending ADR-0016 from the socket to the
  world.** A public field exists because a shape declared it, never because a
  column was forwarded. `public/policy.py` holds the allow-list with a reason
  per field; `coarsen()` is imported from the realtime envelope rather than
  reimplemented, so there is one definition of "coarse" in the system
- **API versioning as executable policy** (ADR-0022). `docs/RELEASE.md`
  published a twelve-month notice period, and a policy nobody can execute is
  prose. The registry refuses a deprecation shorter than that **at import
  time** — the realistic mistake is somebody shortening it because a v2 is ready
  and twelve months feels theoretical, which it is not to the newsroom that
  integrated last year. RFC 9745 `Deprecation` and RFC 8594 `Sunset` headers,
  and a sunset version answers **410 computed from the date rather than the
  status field**, so a deployment nobody updated still stops serving on schedule
- **`api_contract_lock.json`, the outward counterpart of `schema_lock.json`.**
  Removing, renaming, retyping, or un-requiring a published response field fails
  CI with the consumer harm in the message; additive changes pass, because
  forcing a version bump for every addition produces a v7 nobody migrated to and
  a v1 everybody is still on. Nine tests verify the *checker*, not only the
  lock — one nobody has watched fail might be comparing two empty dicts
- **v2 ships, and genuinely breaks.** Counts move under `totals`, `/ward/`
  becomes `/zone/` (ADR-0018's accurate noun). The gate clause is that a v1
  consumer survives v2, and that cannot be proven against a v2 which does not
  exist. Both versions share `public/aggregates.py`, so two shapes cannot
  disagree about a resolution rate — which is the failure a reader comparing two
  URLs would find before we did
- **API keys with a scanner-visible marker.** `nem_<prefix>_<secret>`, 256 bits
  from `secrets.token_bytes`, stored as an unsalted SHA-256 — deliberately not
  bcrypt: password hashing makes a *low-entropy* secret expensive to guess, and
  there is nothing here to guess. What a slow KDF would buy is a hundred
  milliseconds on the hottest auth path against an attack that is already
  impossible. The `nem_` prefix makes our own credentials greppable by the
  secret scanners that would otherwise never flag one committed to a customer's
  public repository
- **Usage as a daily rollup, not a request log.** A row per request is an
  unbounded write on the read path, duplicates what Prometheus holds at better
  resolution, and would retain a client address §22 has no reason to keep in
  order to count requests
- **Webhooks whose signing secrets are derived, never stored** (ADR-0023).
  `HMAC(deployment_key, endpoint_id || version)`: a database dump contains no
  signing material, rotation is an integer increment, and there is no
  key-management problem invented to protect a recomputable value. The
  timestamp is **inside** the signed string — a signature over the body alone is
  replayable forever — and the scheme is versioned in the header so replacing
  SHA-256 is not a flag day across every tenant simultaneously
- **The SSRF guard resolves rather than pattern-matches**, and re-checks before
  *every* delivery. `http://spoof.example.com/` resolving to `127.0.0.1` is the
  whole attack and a regex over the URL cannot see it; DNS answers changing
  between registration and delivery *is* the rebinding attack. Redirects are not
  followed — a 302 to an internal address is the guard walked around one hop at
  a time
- **Fan-out reads the outbox from a durable cursor**, so the submission
  transaction never acquires a subscription lookup and realtime stays decoupled
  from webhooks. The cursor advances in the same transaction as the rows it
  produces, so a crash re-reads rather than skips — and the unique constraint
  makes the re-read a no-op. **The retention interaction is the sharp edge and
  it is closed**: `purge_dispatched` now takes a `safe_below` floor, because
  deleting a row the fan-out had not read means events no subscriber ever
  receives, with no failed row anywhere to show it
- **A retry schedule that is data, not a loop.** Nine attempts spanning ~10.9
  hours. Written as `2 ** attempt` it is a property nobody can state without
  doing arithmetic, and the arithmetic is where "exponential backoff" usually
  turns out to top out at four minutes. Jitter only ever *shortens*, so the
  published span stays true
- **Bulk export streaming CSV and NDJSON; Parquet deferred with an ADR**
  (ADR-0024) rather than dropped quietly — Phase 2's twelfth defect is that no
  gate can catch scope that was never implemented. The export goes through the
  same allow-list as the API, because a bulk download is the most attractive way
  to exfiltrate this dataset and "the export writes a different serialiser" is
  how a scrub gets bypassed by accident. `reported_date` is a **date**: a
  second-resolution timestamp beside a coarse location re-identifies, because
  two people do not photograph the same corner in the same second
- **A developer portal generated from the running build**, with no external
  asset of any kind — a public accountability page that fetches a font leaks
  every reader's address to a third party. It renders the verification function
  every integrator writes and most write with `==` instead of
  `compare_digest`; the shipped example is one this repository's own suite
  executes
- **A sandbox that is a real tenant with synthetic data**, provisioned through
  the ordinary control plane. A mock proves the shape and nothing about whether
  the query works, and one zone is seeded deliberately thin so an integrator
  meets a *suppressed* response during development rather than when a real quiet
  ward hits it
- Five metric families, five alert rules, two runbooks, four ADRs, a dedicated
  `webhooks` dispatcher service, and `nem gate-phase4` / `nem api-lock` /
  `nem sandbox`

**Gate — met**
- ✅ **A v1 consumer keeps working after v2 ships.** Proven twice and both are
  needed: `test_api_contract.py` pins every published response shape against the
  running build, and the live gate replays a *recorded consumer's* access
  pattern — the literal field reads, not a description of them — against v1
  while v2 serves the breaking reshape from the same process. `/ward/` still
  404s on v2, which is what makes the v1 assertion mean something
- ✅ **Webhook delivery survives an endpoint being down for an hour, then
  drains** — drilled live against a real listener on a real socket that really
  refuses connections. Attempts accumulate without giving up, the endpoint
  recovers, the backlog drains, and the payload that lands **verifies against
  its HMAC** and carries `attempt=3` — proving it is the delivery that was
  retried rather than a fresh one that happened to succeed. The hour itself is
  asserted separately as the shipped schedule's 39 160-second span, because
  waiting sixty minutes inside a gate is how a gate stops being run
- ✅ **Every public field is provably free of exact GPS and citizen
  identifiers**, proven in three layers because each catches what the others
  structurally cannot: the declared response models against the allow-list, the
  rendered bodies of a real response, and the OpenAPI document across every
  published path. The live gate seeds a complaint carrying a device fingerprint,
  citizen prose, and a full-precision coordinate, then asserts none of the four
  public endpoints, the webhook payload, or the bulk extract contains any of them
- ✅ ruff clean · ruff format clean · **mypy --strict clean (110 modules)** ·
  **591 tests passing** · **86.33% coverage** against an 85% floor ·
  `alembic check` clean · migration applies, reverts, and re-applies on a clean
  database · all six check scripts green · promtool validates 29 rules ·
  **25/25 live gate checks passed** on three consecutive runs

**Defects the gate and the post-implementation audit caught** (each now covered
by a regression test or a fix):
1. **`allow_private_network_targets` was an off switch, not a relaxation — and
   the gate found it, not review.** The SSRF guard returned early when the flag
   was set, so it disabled *every* address check rather than the two it exists
   for. The flag is only ever set on a local stack, which is exactly where the
   gate registered `https://169.254.169.254/latest/meta-data/` and **got a
   201**. The flag exists so a developer can point a webhook at localhost or the
   Docker gateway; link-local is never a legitimate target on any machine — on a
   laptop nothing listens on it, and on a cloud instance it is the credential
   endpoint. The relaxation is now scoped to loopback and RFC 1918, and
   link-local, multicast, reserved, and unspecified stay refused unconditionally.
   **A test asserting the guard works would not have caught this**, because the
   guard did work in the configuration the tests ran under
2. **The outbox retention sweep could delete events no webhook subscriber would
   ever receive.** The relay was the only reader when `purge_dispatched` was
   written; the fan-out is a second one with its own cursor, and a row the relay
   dispatched hours ago but the fan-out had not read was eligible for deletion.
   The failure is the worst kind: the deliveries were never *created*, so there
   is no failed row to find afterwards and nothing anywhere reports a gap.
   Closed by a `safe_below` floor the retention task reads from the cursor
3. **The privacy scanner reported every clean response as a GPS leak.** The
   coordinate pattern matched the fractional seconds of every ISO-8601 timestamp
   the API emits — `:45.123456` reads as "a number with six decimal places" to a
   regex that only knows digits — so `generated_at` failed on every correct
   body. A privacy check that cries wolf on its own output is one people switch
   off, which would have cost the real finding it exists for (Phase 1a defect #5,
   in a new place)
4. **ORM attribute assignment cannot be used by the cross-tenant dispatcher.**
   A flush emits `UPDATE ... WHERE id = ?` with no tenant predicate, which the
   tenancy guard refuses and is right to — it is indistinguishable from the
   unscoped write the guard exists to catch. Every dispatcher write is now an
   explicit, exempted statement carrying its reason, and `create_endpoint`
   assigns its own id so creation is one INSERT rather than insert-then-update
5. **The migration restated the naming convention's prefix**, producing
   `ck_api_keys_ck_api_keys_quota_is_positive` and, on the longer ones, a name
   Postgres truncated at 63 characters — which a later migration cannot
   reference. `alembic check` caught it by reporting drift between the model and
   the migrated schema, which is that check earning its place
6. **The production safety tests inherited a developer's local `.env`.** They
   passed explicit values for every guarded field *except* the new one, so
   setting `allow_private_network_targets` locally to run the gate tripped an
   earlier guard and made two tests report a wildcard-CORS failure that never
   happened. Pre-existing fragility that the new field exposed
7. **A 404 named the resource kind, and `check_domain_literals` was right to
   flag it.** Chasing why the check was unhappy about `'contractor'` showed the
   distinction was worthless — the kind is already in the path the caller sent —
   and every extra branch in a 404 message is one more place for the
   404-not-403 discipline to erode by somebody being helpful. One message now
8. **The `worker-io` and `beat` images went stale the moment `httpx` became a
   base dependency**, and both entered a crash loop on
   `ModuleNotFoundError: No module named 'httpx'`. Phase 2's eleventh defect
   exactly, in a new place — and the loud failure is the design working:
   `TASK_MODULES` is an explicit list precisely so a task module that cannot
   import kills the worker at startup rather than registering zero tasks and
   reporting nothing. A rebuild fixes it; the reason it is recorded is that the
   *quiet* version of this failure is the one that ships
9. **The test helper wrote `events` by hand and named a column that does not
   exist** (`payload_hash` rather than `event_hash`). Rewritten to go through
   `EventStore` and the outbox writer, so the fan-out reads exactly the rows the
   pipeline produces — hash chain included — and a schema change breaks the
   helper loudly instead of quietly testing something else

**Carried forward, not silently absorbed:**
- **k-anonymity here is a floor, not a proof.** Suppressing small cells does not
  defend against an adversary querying the same ward daily and differencing the
  counts. Real differential privacy is a Phase 23 conversation with the analytics
  platform, and pretending this is that would be the overclaim §17's own framing
  warns against
- **`auto_confirmed_resolutions` counts a proxy, and says so.** The
  auto-confirmation flag lives on the `citizen_confirmed` event, and Phase 15
  owns the closure loop and the projector that would materialise it. Until then
  the figure counts closures with no recorded SSIM verification — narrower than
  the real §44 question, stated rather than presented as the real question
- **`complaints.ward` is matched to `zones.code` by value.** Phase 5 shipped
  `zones` as the supersession of the ward label and Phase 12 is what makes
  routing write the zone reference. Doing the label match now and saying so
  beats inventing a foreign key the pipeline does not populate, which would
  produce a public page permanently empty for reasons no reader could diagnose
- **`PUBLISHED_CURRENCY` is a constant.** Phase 27 owns commercial configuration
  and is where it becomes tenant data; named in one place so the day a tenant
  outside India onboards, the failure is a grep hit rather than a silently wrong
  currency symbol on a public budget page
- **The public rate limiter is keyed on client address**, which is the weakest
  identity available and the only one an unauthenticated endpoint has. Behind a
  shared NAT it throttles an institution; behind a rotating proxy it throttles
  nobody. The real answer for a serious consumer is a key with a real quota,
  which is why `api_keys` exists and why the runbook says so rather than
  crediting a control that did not act
- **The hour-long outage is drilled against a mock transport in the suite and a
  real socket in the gate, but never against a genuinely lossy network.**
  Phase 25's `toxiproxy` suite is where the dispatcher meets packet loss and
  half-open connections rather than a clean refusal

---

# Track B — Control Plane

The anti-hardcoding track. This is what turns NEMESIS from a deployment into a
product, and it is the single largest correction to the previous plan.

## Phase 5 — Tenant, taxonomy & organisation service ✅ · PLT

The anti-hardcoding phase. Critique-log defect #1 is a domain model compiled into
the artefact; this is where it becomes data.

**Shipped**
- **Custom defect taxonomies as data** — `taxonomy_nodes` with a tenant-defined
  key, an arbitrary hierarchy, display names, translations, icons, severity
  semantics, and routing hints. **The key is an immutable contract and the
  display name is a translation** (ADR-0019): `classification_scored.category`
  lives in a log that must stay readable for years, so a rename would orphan
  history — and "we cannot fix the label because the log would break" is not an
  answer anybody outside engineering accepts. Two columns, and neither problem
- **A materialised path, and the two ways it silently goes wrong.** The subtree
  read is one index-backed prefix scan because Phase 6 walks ancestors per scored
  complaint against an eight-second §27.1 budget. `roads` is not an ancestor of
  `roadside_waste`, which a bare `startswith` would claim; and `_` is both the
  recommended word separator and a `LIKE` wildcard, so the pattern escapes it
- **Two organisation hierarchies, not one** (ADR-0018): `departments` is
  *responsibility*, `zones` is *place*, both arbitrary and tenant-`kind`ed. One
  table reads tidier and cannot express Phase 6's routing rule, which is
  `(category × place) → responsible unit` — a rule that cannot name both sides
  cannot be written, and a Public Works division covering eleven wards each
  worked by four divisions is not a tree
- **`taxonomy_prompt_sets`** keyed by node, locale, *and* encoder — Phase 9's
  gate is that a new category is classifiable by adding prompts alone, and a CLIP
  prompt and the Marathi text prompt that scores the same category cannot share a
  row without one of them being wrong. Prompts in an undeclared locale are
  refused, because nothing ever asks for that locale and the category would
  appear configured while classifying nothing
- **Business calendars with SLA arithmetic as a pure function.** `resolve_deadline`
  takes a plain `WorkingWeek` and no session, no ORM, and no clock, because it is
  the piece most likely to be subtly wrong. Three failure modes are refused by
  construction: adding hours and *then* checking for a holiday (removes at most
  one), doing it in UTC (a working day is a wall clock), and treating §13.4's
  monsoon as non-working (it is harder, not impossible — so it stretches the
  budget by a stated multiplier and the adjustment is reported with the deadline)
- **`contractor_certifications` replaces §9.2's `categories_certified` array.**
  An array cannot carry a validity window, so a certification that lapsed last
  March is indistinguishable from a current one — and §15.3 would rank on it
  anyway. `certified_contractors(on=...)` takes the date as a parameter because
  §17's audit asks retrospectively, and a function that can only answer for today
  cannot audit an assignment made in March
- **Translations as tenant data**, with `coverage()` as the honest half: importing
  strings is easy, and knowing a tenant declared Marathi and translated 40% of its
  taxonomy is what stops a half-localised deployment reaching a citizen
- **Tenant provisioning in one transaction**, with a seeded template library
  (municipality / campus / industrial park) as **JSON data files**. A template
  written as a Python module would fail this phase's own test in the most
  embarrassing way. Templates parse through the same Pydantic models the API
  binds, and `all_templates()` in the suite makes a broken one fail CI rather
  than the first onboarding that reaches for it
- **Three new event types on the tenant chain** — `tenant_provisioned`,
  `taxonomy_published`, `organisation_changed` — so a tenant's configuration
  history is hash-chained like everything else. `taxonomy_published` carries the
  content hash of the *whole* taxonomy rather than of the change, so "what was the
  taxonomy when this complaint was classified" is one comparison instead of a
  replay. Provisioning appends exactly two: a template creating eleven
  departments is one operator action, and eleven events would bury it
- **A control-plane API guarded by a shared token** (ADR-0020), with reads
  token-free and tenant-scoped. ADR-0009's CLI-only answer was reconsidered and
  rejected for this surface: the gate is *onboard a customer without opening an
  editor*, and "shell into the container" is a worse version of the thing the
  phase removes. The token is not identity and does not pretend to be — which is
  why every mutation also writes an event, and why a compromise is investigated
  by reading the chain rather than by asking the token who used it
- `check_domain_literals.py` as the standing defence, reading the category list
  **from the seeded templates** so a category added to a template is covered
  without touching the check; a CLI (`python -m nemesis.control_plane`); a
  runbook; and three ADRs

**Gate — met**
- ✅ **A brand-new tenant with a completely different taxonomy, onboarded end to
  end with no code change and no deploy** — proven three ways because they fail
  differently: at the service layer, over HTTP in-process, and live against the
  running stack by `nem gate-phase5`, which invents a vocabulary
  (`micrometeorite_strike`, `co2_scrubber_fault`) that appears nowhere in this
  repository, in no template and no fixture
- ✅ **The new tenant then accepts a citizen complaint** through the Phase 3
  ingest path. "Provisioning returned 201" and "the tenant is real to the rest of
  the system" are different claims and only the second one matters
- ✅ **No domain module contains a category, role, ward, or language literal** —
  50 modules clean against 33 seeded categories, 7 role names, and ward/language
  patterns, wired into `nem check` and CI
- ✅ **Two tenants with conflicting taxonomies operate simultaneously without
  leakage**, asserted in its sharpest form: the *same key* under different
  parents with different severity semantics, read back through separate requests
  against one process and one database. Another tenant's category is **404, never
  403** — a distinguishable rejection is an enumeration oracle
- ✅ Provisioning wrote `tenant_provisioned` then `taxonomy_published` on a chain
  that `verify_chain` recomputes, with no forked sequence
- ✅ An SLA deadline reports the seasonal adjustment that moved it
- ✅ ruff clean · ruff format clean · **mypy --strict clean (89 modules)** ·
  **446 tests passing** · **87.32% coverage** against an 85% floor ·
  `alembic check` clean · migration applies, reverts, and re-applies · all six
  check scripts green · **15/15 live gate checks passed**

**Defects the gate and the post-implementation audit caught** (each now covered
by a regression test or a fix):
1. **The subtree rewrite matched nothing, and the tree read as intact.**
   `update_node` reparents a node and then rewrites every descendant's path — and
   it read `node.path` *after* the `UPDATE`. `session.execute(update(...))`
   synchronises the session by default, so that attribute already held the *new*
   path; the rewrite searched for descendants of a prefix nothing was under,
   matched zero rows, and left every descendant pointing at a path that no longer
   existed. The moved node itself was correct, so the tree looked right at the
   level anyone would have checked. Caught only by asserting on the *grandchild* —
   a test that checked direct children would have passed
2. **A constraint name Postgres cannot store.** The naming convention's
   column-concatenation rule produced
   `uq_contractor_certifications_tenant_id_contractor_id_taxonomy_node_id` — 69
   characters against a 63-character limit. SQLAlchemy refuses outright, which is
   the good outcome; a silently truncated name is one a later migration cannot
   reference, and that is how a migration chain quietly becomes non-reversible
3. **The autogenerated migration could not run at all.** Alembic emits
   `geoalchemy2.types.Geography(...)` and does not add the import, so the file
   raises `NameError` on a fresh database — which is every CI run and every new
   developer, and invisible on the machine where the migration was generated
4. **`departments.path` was added `NOT NULL` with no default.** Autogenerate's
   single-statement form fails on any table that already has rows, which is every
   deployment that has onboarded a customer. Rewritten as add-nullable, backfill,
   set-NOT-NULL
5. **The first hardcoding check reported twenty findings on a clean codebase.**
   Matching any two- or three-letter lowercase literal as a possible BCP-47 tag
   flagged `pk` and `fk` from the constraint naming convention, `lat`/`lng` from
   the realtime envelope, `ok` from a health response, and `jpg` from the upload
   allow-list. Every one was wrong. A check that is wrong twenty times out of
   twenty is one people learn to silence — Phase 1a defect #5 in a new place — so
   it was narrowed to what the critique log actually describes: a *collection* of
   two or more recognised language codes, which is what a pinned locale set looks
   like in source
6. **`tenant_scope` in an `async with` list.** It is a synchronous context
   manager, so `async with session_scope() as s, tenant_scope(t):` fails at
   runtime with a message about the asynchronous context manager protocol, from a
   line that reads correctly. Present in both the CLI and the first test file;
   the tests now go through one helper so the mistake is unrepeatable
7. **The pilot safety validator had a new hole and the existing tests found it.**
   Adding `control_plane_token` to the boot guard broke three tests that
   construct a pilot `Settings` — correctly, because they assert the guards are
   real. The token is now in the deployment contract with a rotation procedure,
   which `check_env_parity.py` demanded before it would pass

**Carried forward, not silently absorbed:**
- **`departments` keeps its §9.2 name** even though `kind` now makes it general.
  Renaming it renames `work_orders.department_id`, whose name is mirrored in the
  `work_order_created` payload — so the rename costs a payload version bump plus
  an upcaster for no behavioural change. That belongs to Phase 14, which owns
  work-order workflow and will be editing that payload anyway
- **`contractors.categories_certified` was dropped and its data cannot be carried
  forward.** The column held taxonomy *keys* and the new table references
  `taxonomy_nodes.id`, which this migration creates empty — so there is no row for
  any key to resolve to. Inventing placeholder nodes to preserve strings nothing
  can yet interpret would be worse. Stated in the migration rather than discovered
  from a diff
- **`severity_semantics` and `routing_hints` are day-one defaults, not policy.**
  Phase 6 replaces both with versioned, effective-dated, approved documents.
  Shipping them now is what lets a tenant be useful before that phase lands;
  shipping them as if they were the final shape would be pre-empting it
- **Taxonomy structure is not versioned.** Phase 6 owns draft→approve→activate,
  and building half of it now would fix a shape before the phase that has to live
  with it exists. What Phase 5 carries is a monotonic revision counter and a
  content hash, which is enough to answer "which taxonomy scored this complaint"
- **The control-plane token is a shared secret, and Phase 13 deletes it.** It
  must not acquire scopes, per-tenant variants, or an expiry — each is a step
  toward reimplementing authorization badly in the phase that does not own it

## Phase 6 — Policy & rules engine ✅ · PLT · DATA

Every behavioural knob becomes governed data. Critique-log defect #2 is that
every tuning knob was a constant, a config field, or an env var, so §13.3's
promise that "the rubric improves as data accumulates" was unimplementable by
anyone without a deploy pipeline. This is where that stops being true.

**Shipped**
- **Six governed structures as versioned, effective-dated, tenant-scoped
  documents** — severity rubrics (§13.5), dedup thresholds (§14.3), safety
  rulesets (§11.2), SLA matrices (§27.2), routing rules (§15.2), and rate cards
  (§17.2). One table, not six: they share a lifecycle, a chain, and a set of
  columns, and what differs is the *body*, which is JSONB validated by the
  Pydantic model registered for the kind. Adding a seventh structure is a model
  plus a registry line and touches neither the service, the API, nor a migration
- **A sandboxed condition language that never calls `eval`** (ADR-0025).
  `policy.expressions` parses to an AST, refuses every node type not on a short
  allowlist, and walks the survivors itself — no code object is ever built, so
  the standard `__class__` → `__subclasses__` escape has nothing to start from.
  Conditions compile against a **declared fact schema**, which buys the property
  the module exists for: **a compiled condition cannot raise.** Evaluation is
  total, does no I/O, reads no clock, and consults no randomness
- **Draft → review → approve → activate, with every transition an event.** One
  transition table, one mutation path, one place that appends
  `policy_transitioned` — so "every transition is in the chain" is structural
  rather than a convention four functions have to remember. Two new event types
  (`policy_drafted`, `policy_transitioned`) on the tenant chain beside
  `taxonomy_published`, because the question is always "the taxonomy changed,
  then the rubric changed, then everything scored differently"
- **Hot reload with a published interval** (ADR-0027). A per-process snapshot on
  a 30-second TTL, and the number is stated in the module, in the activation
  response body, and in the runbook — because the alternative is not a different
  number, it is silence, and every system that stays silent produces the same
  incident: somebody changes a threshold three times and then goes looking for
  the "real" configuration in an environment variable
- **Rollback that moves forward** (ADR-0026). Restoring revision 3 creates
  revision 8 carrying revision 3's body. Re-activating the old row would overlap
  the effective-date intervals and turn "what was live on 14 March" from a query
  into a reconstruction — possible, and exactly the kind of possible-but-nobody-
  will that makes an evidence trail decorative
- **Baseline documents seeded at provisioning**, walking the full lifecycle
  rather than being inserted as `active`. A seeding shortcut would be a second
  way for a document to become live, and "does one exist" is the first thing an
  auditor asks. `policy.baselines` is the single source for both the seed and
  the resolver's fallback, so the two cannot drift
- **Cross-entity validation at draft time** — a routing rule naming a department
  the tenant does not have, or a rubric override on a category it never defined,
  is refused when it is written. A rule pointing nowhere routes work into a void,
  which on a queue is indistinguishable from a backlog
- **The full HTTP surface**, mounted beside the control plane and following its
  conventions exactly: reads tenant-scoped and token-free, writes behind
  `X-Control-Plane-Token`, 404-never-403 for another tenant's revision, and a
  `/facts` endpoint so an author can discover the condition vocabulary without
  reading the source
- **Three ADRs, one runbook** (`policy-rollback.md`), an idempotent backfill
  endpoint for tenants provisioned before this phase, and `nem gate-phase6`

**Gate — met**
- ✅ **Changing a severity weight, an SLA, a safety keyword, or a routing rule
  requires no deploy** — and "no deploy" is *measured*, not asserted.
  `nem gate-phase6` records the API container's id and `State.StartedAt` before
  the run, changes all four knobs over HTTP, and compares them after. A test
  process never restarts, so it cannot demonstrate the absence of a restart;
  the live gate can
- ✅ **Takes effect within one reload interval** — 30 seconds, stated in three
  places, with the activating process invalidating its own cache so an operator
  who re-reads immediately sees their own change
- ✅ **Every scored complaint records the exact policy version that scored it** —
  the resolver returns the document and its stamp as *one* object, so a caller
  physically cannot stamp a decision with a version other than the one that
  decided it. Two reads across a reload boundary could disagree; one cannot
- ✅ **An unapproved draft can never influence a production decision** — proved
  at three layers, because they fail differently. The service's only read path
  filters on `active` plus the effective date; a **partial unique index** makes
  `active` additionally mean *exactly one*, which a service check would not
  survive two operators pressing Activate in the same second; and a CHECK
  constraint refuses any row reaching `active` without an `approved_at`, so a
  psql session cannot activate anonymously either
- ✅ **The safety fail-safe remains provably deterministic under policy control**
  — a Hypothesis property over generated text and locales asserts identical
  outcomes across repeated evaluation, and the matching contains no regular
  expressions anywhere, deliberately: a tenant-authored regex is a
  catastrophic-backtracking denial of service against the stage with the
  *highest* retry budget in the pipeline
- ✅ Every published sandbox escape technique is a named parametrised test case,
  so a widening of the allowlist fails CI by technique rather than by a general
  "sandbox test"
- ✅ ruff clean · ruff format clean · **mypy --strict clean (119 modules)** ·
  **789 tests passing, 209 of them new** · **87.39% coverage** against an 85%
  floor · `alembic check` clean · migration applies, reverts, and re-applies ·
  all eight check scripts green · API contract re-locked (47 operations,
  additive only) · **22/22 live gate checks passed**, and Phases 3, 4, and 5
  re-run green against the same stack

**Defects the implementation and the gate caught** (each now covered by a
regression test or a fix):
1. **`session.refresh` is unusable in this codebase, and the tenancy guard said
   so.** It emits `SELECT ... WHERE id = ?` with no tenant predicate, which the
   runtime guard correctly refuses — from a stack frame nowhere near the call,
   because it fires on whatever statement the session flushes next. Eighteen
   tests failed at once on it. Replaced with an expire-then-scoped-read helper,
   and the tests use the same route rather than being exempted
2. **The first version of that helper tripped the guard it existed to satisfy.**
   It read `version.kind` *after* `session.expire(version)` — and touching an
   attribute of an expired instance is itself a lazy load, unscoped, from inside
   an attribute access. The identity has to be captured before expiring
3. **`/seed-baselines` was parsed as a policy kind.** FastAPI matches routes in
   registration order and `POST /{kind}` was declared first, so the enum rejected
   "seed-baselines" as an unknown kind and the backfill endpoint was unreachable.
   Caught by the test written to catch exactly that. The same class of bug
   applied to `/{kind}/{revision}/{verb}` swallowing `activate` — which would
   have made the endpoint that changes what production does reachable by varying
   a path segment on the review endpoint
4. **A bare `category` compiled as a routing condition.** The compiler checked
   the operands of each connective but never the expression as a whole, so a
   non-boolean at the top level passed — and it would have matched every
   classified complaint, because a non-empty string is truthy, while reading on
   the page like a rule about categories
5. **Rollback skipped two transitions.** The first implementation inherited the
   original approval with a direct `UPDATE`, which meant the restored revision
   went draft → active with no events for the states in between. The gate clause
   is that *every* transition is an event; a fast path with no events is exactly
   the exception that makes the claim untrue, and it was in the emergency path
   an auditor asks about first. Now it walks the real lifecycle, and the approver
   recorded is whoever pressed rollback — they are the one putting the content
   into production, and attributing it to someone who has not been near the
   system since March would be a comfortable fiction in the one record that
   exists to prevent them
6. **`update_draft` shipped with no test at all.** Found by reading the coverage
   report rather than by a failure — the endpoint worked, and nothing exercised
   it. Six tests now cover the edit path, including that the revision survives an
   edit while the content hash does not

**Carried forward, not silently absorbed:**
- **Existing tenants are not seeded by the migration**, and the reason is
  structural rather than lazy: every policy write must append to the tenant's
  hash chain, and a migration cannot do that correctly — appending needs the
  chain-tail lock, the previous hash, the canonical encoding, and the schema
  registry. Reimplementing any of it in raw SQL would produce rows that look
  like chain entries and fail `verify_chain`. Instead the resolver falls back to
  the same baseline objects, those decisions are stamped `baseline` rather than
  with a plausible-looking revision, every fallback logs `policy_baseline_used`
  so the gap is a log query rather than an audit, and an idempotent endpoint
  does the real backfill through the real service
- **`config.py` keeps `SeveritySettings` and `DedupSettings`**, now read only for
  their *declared defaults* when building baselines. The env-var override path is
  superseded: after this phase the way to change a weight is to draft a rubric.
  Deriving baselines from a live `Settings` instance was rejected because an env
  var that shifted a baseline weight would make the same complaint score
  differently on two workers with different environments, which defeats the point
  of a version stamp
- **Routing rules and rate cards have no platform baseline**, and that is not an
  omission. They name departments and negotiated prices the platform cannot
  invent. A tenant with no routing document leaves complaints *unrouted* — a
  state the triage queue shows and an operator fixes — rather than falling back
  to a default department, which is where misrouted work goes to be ignored
- **`RoutingRules` has no `fallback_department_code` field**, for the same
  reason, and a test asserts its absence so it cannot be added as a convenience
- **Severity overrides do not compose.** Most-specific wins and the walk stops
  there. A parent floor combining with a child multiplier reads reasonably and
  becomes impossible to predict once the tree is four levels deep, which is
  precisely when somebody needs to predict it
- **Absent facts compare `False`, including under `!=` and `not in`**, so
  `not (severity > 7)` and `severity <= 7` are not complements. The wart is
  documented and pinned by a named test. The alternative — raising mid-route on
  a fact that legitimately does not exist yet — would drop a citizen's report
  because their photo had no EXIF
- **Nothing here consumes the policies yet.** Phases 8, 10, and 12 are the
  consumers; this phase ships the governance, the resolver, and the arithmetic
  those phases will call. Wiring the pipeline stages to it now would mean
  building the stages, which is three phases' work being done under this phase's
  gate
- **Phase 7 is what makes this safe to use at scale, and it has since
  shipped.** Activation depended on an approver reading a document; it now
  additionally depends on a passing certificate whenever the tenant has
  published an evaluation set for the kind. `policy.service.activate` grew one
  guard, `rollback` grew the one waiver, and the rest of this phase is unchanged
  — which was the point of leaving the seam where it was

## Phase 7 — Configuration simulation & backtesting ✅ · DATA

Phase 6 made every behavioural knob governed data and named the hole it left in
its own notes: *activation currently depends on an approver reading a document.*
Somebody reads forty weights, forms an opinion, and presses a button that changes
how every future citizen report is scored. This is where the opinion is replaced
by a measurement.

**Shipped**
- **A decision engine that is pure, total, and clock-free** — `decide(bundle,
  case)` takes every input by value, has no session parameter so it cannot
  query, and derives every instant from the case's own `reported_at` so two runs
  of the same comparison cannot disagree. It calls production's own arithmetic
  (`score_severity`, `evaluate_safety`, `evaluate_routing`, `resolve_deadline`)
  rather than reimplementing it, because a simulator that reimplements what it
  simulates measures its reimplementation
- **A corpus built from the event log, never from the projections** (ADR-0029).
  The `complaints` table is right there, indexed, one query away — and it is
  current state that every policy change since has already rewritten, so a
  backtest built on it reports "nothing changed" for a change that would in fact
  have moved thousands of reports. Chains are folded with `projections.project`
  — production's own projectors — and an **allow-list** takes observations out
  of the result. `OBSERVATION_KEYS` and `DECISION_KEYS` are both named, and a
  test asserts `DecisionCase` declares no field from the second
- **The guardrail is a row, not a call** (ADR-0028). `policy.service.activate`
  reads `evaluation_sets` and `policy_certificates` and imports nothing from
  `nemesis.simulation`; a test parses the AST of every policy module to prove
  it. A hook registry would have failed *open* the day the wiring changed, and
  failing open is indistinguishable from having no guardrail. Publishing a set
  is the switch — there is no `require_certification` flag to fall out of step
  with it — and a certificate is keyed by **content hash**, which cannot be
  wrong about which bytes were tested
- **Shadow mode that is read-only by construction** (ADR-0030). Two independent
  layers: Postgres's own `SET TRANSACTION READ ONLY` and a `before_execute`
  statement guard, each tested with the other absent. It runs on a transaction
  of its own — read-only is a one-way door in Postgres, so marking the caller's
  transaction would poison it permanently, which the tests for the recording
  half discovered
- **A report designed against its own failure modes.** Tier movement is a matrix
  rather than a net ("40 up, 40 down" and "0 moved" are the same net and very
  different changes); extremes sit beside means; the changed-case sample leads
  with the largest movement rather than with January; and a corpus below
  `MINIMUM_CASES` is **refused** rather than reported, because "no regressions"
  over three complaints is the exact shape of an answer with none of the content
- **Coverage gaps are findings, not warnings.** `zone_code` and `tags` have no
  source in the log yet, and an absent fact compares `False` under every
  operator — so a candidate rule turning on one would backtest as *"0 complaints
  affected"*, which is identical output to a genuinely inert change. Every such
  rule is named in `coverage_gaps`, and a report carrying one is not certifiable
- **Auto-tuning that can only ever be more conservative.** The single human
  dedup signal in the catalog is `cluster_merge_reverted`; nothing records a
  merge that *should* have happened. So proposals only raise thresholds, the API
  says so in every response, and a test asserts no path lowers one. §14.3
  already establishes the asymmetry — a false merge suppresses a citizen's
  report; an unmerged duplicate costs an operator time
- **Ten endpoints** under `/api/v1/control-plane/simulations`, following the
  policy router's conventions exactly, plus a shadow-mode kill switch, three
  ADRs, a runbook (`policy-certification-blocked.md`), and `nem gate-phase7`

**Gate — met**
- ✅ **A rubric change is backtested over 12 months of seeded history, producing
  a quantified impact report before activation.** The live gate seeds **400 real
  complaint chains over 365 days** through the real `EventStore` — real hashes,
  real chain tails, no SQL inserts — then replays a candidate over all 400 and
  asserts the report *moves*: a backtest reporting "nothing changed" for a
  rubric that inverts every weight is one that is not reading its corpus. It
  also asserts the candidate is still not live afterwards
- ✅ **A policy that regresses the labelled evaluation set cannot be activated** —
  proved at the service layer through the single mutation path, and over HTTP in
  the gate. Four bypass routes are closed by test: no certificate, a *failing*
  certificate, a certificate issued against different bytes, and editing the
  labels after the fact. The gate then certifies the same candidate and
  activates it, because a guardrail that refuses everything is not one either
- ✅ **Shadow mode provably cannot mutate state or emit domain events** — two
  layers, each tested with the other disabled, plus a live check that captures
  the tenant's event count *and every chain head*, runs shadow mode over 25 real
  complaints, and finds both unchanged while the observations are non-empty, so
  the check cannot pass by doing nothing
- ✅ **22/22 live gate checks passed**, and Phases 4, 5 and 6 re-run green
  against the same stack
- ✅ ruff clean · ruff format clean · **mypy --strict clean (132 modules)** ·
  **905 tests passing, 116 of them new** · **87.59% coverage** against an 85%
  floor · **`alembic check` clean**, migration applies, reverts, and re-applies ·
  all eight check scripts green · **API contract re-locked (60 operations, 13
  added, additive only)**

**Defects the implementation and the gate caught** (each now covered by a
regression test or a fix):
1. **`SET TRANSACTION READ ONLY` is a one-way door, and the first version of
   `read_only` locked the caller out of its own transaction.** Postgres refuses
   `SET TRANSACTION READ WRITE` after the first query — *"transaction read-write
   mode must be set before any query"* — so marking the caller's transaction
   read-only poisoned it permanently, and the failure surfaced at some unrelated
   statement much later. The scope now runs on a session of its own, borrowed
   from the caller's engine. Caught by the tests for the *recording* half, not by
   the tests for the guarantee
2. **A failed run left no trace.** The row was written into the caller's
   transaction, so a refused backtest propagated its exception, the handler
   rolled back, and "we tried and the window was empty" was indistinguishable
   from "nobody tried". Failures are now recorded in their own committed
   transaction
3. **A bare `except Exception` around that bookkeeping hid a programming error** —
   a synchronous `tenant_scope` in an `async with` list — and the only symptom
   was a row that never appeared. The handler now logs the exception it swallows
4. **The seeder placed one complaint outside the window it claimed to fill.** An
   even spread with a few random hours subtracted puts the first report a
   fraction of a day early, and the gate found 399 of 400 complaints in a
   365-day window. The jitter now stays inside each report's own slot. A
   boundary that lies about itself in a *seeder* is worse than one in a query:
   every downstream number inherits it
5. **`nemesis/policy` was silently exempt from two CI checks.** Phase 6 shipped
   without adding it to `DOMAIN_PACKAGES` in either `check_tenant_scoping.py` or
   `check_domain_literals.py`, and both kept reporting "clean" for a package
   they were not reading — the exact failure the first of those scripts warns
   about in its own comments. Both packages are listed now: tenant scoping went
   from 68 modules to 86, domain literals from 64 to 81. `policy/baselines.py`
   is exempted *by name* rather than by leaving the package out, because it
   holds the platform's starting safety keywords — the same class of artefact as
   `control_plane/templates` — which means the rest of the package is checked.
   Everything was in fact clean, which is the point: a check that would not have
   told us otherwise is not evidence
6. **The sandbox seeded projection rows with no event log at all.** Correct for
   the public API, which reads projections, and useless to a backtest, which
   folds the log by design. `nemesis.sandbox` now has a `--history` path that
   writes real chains

**Carried forward, not silently absorbed:**
- **Three declared routing facts have no source in the log** — `zone_code`
  (Phase 19), `tags` (Phase 14), and the visual half of the safety ruleset
  (Phase 9). They are listed in `UNAVAILABLE_FACTS`, a rule referencing one is a
  named coverage gap, and a report carrying a gap cannot back a certificate.
  Fabricating them from the classifier's output would make a visual rule appear
  to fire on evidence nothing produced
- **SLA breach counts are not reported**, only budget changes. A breach needs a
  resolution time, and Phase 14 owns work orders. The report says which
  complaints get a *shorter* budget, which is the new risk a shortened SLA
  creates against work already in flight
- **Runs are synchronous.** A twelve-month window over a real city would exceed
  an HTTP timeout long before it exceeds `ABSOLUTE_MAX_CASES`; the honest fix is
  a Celery task and a polled run row, which is Phase 23's shape. `SimulationRun`
  already carries `running`/`completed`/`failed` so the async runner has
  somewhere to land without a migration
- **Auto-tuning covers dedup thresholds only.** Severity and SLA tuning need
  outcome data — did "urgent" complaints actually resolve faster — which needs
  Phase 14's closure loop and Phase 23's metrics
- **The corpus is sampled above 20,000 cases**, systematically across submission
  order rather than truncated to the most recent N. The report carries both
  `case_count` and `population` so "12,000 complaints" and "12,000 of 480,000"
  stay different claims
- **Nothing consumes the policies yet, still.** Phases 8, 10 and 12 remain the
  consumers. What changed is that a candidate can now be measured before it
  becomes what they consume

---

# Track C — Intelligence

## Phase 8 — Trust & safety spine ✅ · DATA · SEC

Highest credibility-per-hour in the system (§11), now policy-driven — and the
first phase whose stages actually *consume* the documents Phase 6 made governed
and Phase 7 made measurable. Phase 7 closed with the note *"nothing consumes the
policies yet, still."* This is the first consumer.

**Shipped**
- **The §11.2 fail-safe, on a queue that is a different container.** The stage
  resolves the tenant's approved ruleset and runs `evaluate_safety` — document
  order, first match wins, no regular expressions, linear in submitter-controlled
  text. "A saturated `ml` queue cannot delay a danger signal" is therefore not a
  scheduling promise a prefetch setting could break: `QUEUE_SAFETY` is served by
  `worker-io`, an image that has never imported torch, and the only way to break
  the separation is to move one line in `stages.py`, which a test asserts. When
  it fires it **halts** — the successor stages are never enqueued, rather than
  dispatched and declining to act, which would be four no-ops holding queue slots
  behind a gas leak
- **§22.1 face blur, failing closed** (ADR-0032). `active_detector()` raises
  rather than returning a stand-in, because the alternative is the single worst
  line this phase could contain: a detector that finds no faces makes every run
  succeed, records `faces_detected: 0`, ships a copy pixel-identical to the
  original, and hides the breach from the log as well as from the outside. The
  blur is Gaussian and applied twice over a box expanded 25% on each side —
  pixelation was rejected outright, because mosaic redaction is reversible at
  small block counts and there are published attacks that do it. The output is
  **re-encoded from decoded pixels**, so the EXIF GPS, the XMP packet and the
  embedded thumbnail — a second unblurred copy of the whole scene — cannot
  survive; the strip is a consequence of the design rather than a step
- **The raw photograph persists, unreachably** (ADR-0031). `docs/PHASES.md` said
  blur before *any* persistence; §22.4 retains the raw photo for 30 days for the
  dispute window. Both cannot be literally true and the ADR records which one
  won and why. What is enforced instead: two named readers of quarantine, one
  writer of the served root, no route that can express a quarantine path, and a
  stamped expiry on every artefact from the tenant's own retention policy
- **§11.1's three EXIF outcomes, kept apart.** `PRESENT_MATCHED`,
  `PRESENT_MISMATCHED` and `ABSENT` are different facts: a contradiction, a
  confirmation, and a silence. Collapsing the third into the first is exactly how
  "absent EXIF reduces trust rather than rejecting" becomes a system that
  penalises every WhatsApp share. `exifread` was **dropped** in favour of twenty
  lines over Pillow's GPS IFD — not for elegance, but because `exifread` lives in
  the `ml` extra, which would have put the §11.1 check in an image the test suite
  does not run in
- **§11.1's re-upload check as a 64-bit dHash**, chosen over aHash (collapses
  under a brightness change) and pHash (needs a DCT nobody can review). Gradients
  are invariant to monotonic brightness change *by construction*, which is the
  property §11.1's claim actually rests on, and the tests measure the claim —
  quality-40 recompression and 0.5×/2.0× resize both stay within tolerance while
  unrelated images stay outside it. Searched with `bit_count(a # b)` over a
  partial index, tenant-scoped and window-bounded, with the honest note that the
  fix at ten million rows is a banded index and that is Phase 23's shape
- **§11.3's two detectors, which are deliberately not one.** Velocity is *one
  device, many reports*; clustering is *many devices, one place*. A single
  detector counting submissions in a window fires on both and distinguishes
  neither, so a reviewer is handed "suspicious activity" with no way to tell a
  bot farm from a street that genuinely flooded. Both **flag and cannot block**
  (ADR-0033): no field for it in the finding, none in the payload, no write in
  either detector, and no status change in either projector
- **§11.4's queue, with the bundle frozen at queueing.** Recomputing evidence on
  read would show a reviewer today's numbers for a flag raised against last
  week's thresholds — and Phase 11 would then learn from a label attached to
  evidence that never produced it. A repeat raises `occurrences` rather than a
  second row, enforced by a partial unique index on `status = 'open'`; partial,
  so the same reason can legitimately be raised again months later
- **Every decision is a Phase 11 label by construction.** Architectural principle
  4 and critique-log defect #6, as a table rather than an intention:
  `review_decisions` carries the outcome *and* the hash of the evidence the human
  saw, written in the same transaction as the event, indexed for the query Phase
  11 will run — and `labels_for_training` is written now, so the index decision
  is checkable today instead of justified by a query nobody has written
- **A seventh governed kind, `trust_thresholds`.** Every §11 knob — EXIF radius,
  Hamming tolerance, velocity limits, cluster radius, §22.4's two retention
  clocks, and §11.1's live-capture switch — is an approved, effective-dated,
  hash-chained document. There is deliberately **no field that disables face
  blur**: §22.1 is an obligation, not a tuning parameter, and a policy field for
  it would be a documented, approvable path to a breach
- **§11.1's live-capture-only mode, at the boundary.** §11.1 calls it *the real
  control* for stripped EXIF, so it refuses the submission where the citizen is
  still listening — a 422 with an explanation — rather than in the pipeline,
  which would acknowledge with a 202 and discard the report where they cannot see
- Four HTTP endpoints, three ADRs, two runbooks, three alerts,
  `scripts/check_media_redaction.py`, and `nem gate-phase8`

**Gate — met**
- ✅ **The safety bypass provably fires before any scoring stage.** The live gate
  submits a hazard report *carrying a photograph* and asserts on what the
  orchestrator says runs next: the chain is
  `complaint_submitted → safety_trigger_fired → review_queued`, and it contains
  no classification, no severity score, and **no trust-verification event at
  all** — so the claim is about work that was available and never dispatched,
  not about a stage that ran and declined
- ✅ **No code path can persist an unblurred image**, by three independent
  routes. `check_media_redaction.py` parses every module and fails on a second
  reader of quarantine, a second writer of the served root, or a redactor that
  stopped calling the detector accessor — and it **self-tests those three rules
  against synthetic violations before it scans**, because a guard that reports
  clean and a guard whose rules stopped matching are indistinguishable. The unit
  tests measure the *pixels*: a high-frequency checkerboard under the detection
  loses its variance while a region outside it survives, so "the bytes differ"
  cannot pass for a blur. The live gate proves the deployment: the real MediaPipe
  detector in `worker-ml` found and blurred a face, the served bytes contain no
  EXIF segment, and the upload's own content address returned 404
- ✅ **Safety-queue latency is unaffected by a saturated `ml` queue, proven under
  load.** 25 image reports were pushed onto the ml queue and a hazard report
  submitted while it was draining: **0.6 s to `FLAGGED` with 16 reports still
  queued**, against a 30 s budget — and the gate separately asserts the backlog
  was still working when the danger signal landed, so the number cannot pass by
  the queue having drained first
- ✅ **A tenant with custom safety keywords gets correct behaviour with no code
  change.** The gate first `git grep`s its invented hazard to prove no module
  contains it, activates a ruleset naming it through the ordinary
  draft → review → approve → activate path over HTTP, and shows the report
  bypassed — with `rule_id = exotic_oxidiser`, the tenant's own rule
- ✅ **25/25 live gate checks passed**, and Phases 3, 4, 5, 6 and 7 re-run green
  against the same stack
- ✅ ruff clean · ruff format clean · **mypy --strict clean (147 modules)** ·
  **1038 tests passing, 133 of them new** · **87.72% coverage** against an 85%
  floor · **`alembic check` clean**, migration applies, reverts, and re-applies ·
  all nine check scripts green · **API contract re-locked (64 operations, 4
  added, additive only)**

**Defects the implementation and the gate caught** (each now covered by a
regression test or a fix):
1. **The partial unique index could not be inferred, and it failed on the second
   flag rather than the first.** `ON CONFLICT ... WHERE status = 'open'` rendered
   the predicate as a *bound parameter*, and Postgres cannot match a parameter
   against an index predicate — so the escalation path failed at plan time with
   "no unique or exclusion constraint matching the ON CONFLICT specification",
   while raising a *new* flag worked fine. The literal has to match the index's
   own predicate exactly
2. **The tenancy guard refused the review queue's own status update, correctly.**
   Mutating a loaded ORM object emits `UPDATE ... WHERE id = :id` with no tenant
   predicate, and a primary key is not a tenant boundary. Replaced with an
   explicit tenant-scoped `UPDATE`; the same class of problem in `trust.rebuild`
   was fixed by making the rebuild **insert-only** — it reads the decisions
   first so an item can be *constructed* already decided rather than inserted
   open and then updated
3. **Three queries were correctly scoped and unverifiably so.**
   `check_tenant_scoping.py` reads the AST at the call site, and a tenant filter
   assembled into a `filters` list is one it cannot see. The queries were right;
   the check would have reported "clean" for a package it could not read, which
   is the exact failure Phase 7's defect #5 describes. All three now write the
   predicate inline
4. **The face detector looked in the wrong directory.** `fetch_models.py` writes
   `<cache>/mediapipe/<file>` and the detector read `<cache>/<file>`. The symptom
   would have been "model absent" → every complaint carrying a photograph halts,
   which is *correct behaviour for a genuinely missing model* and a very
   confusing way to discover a path typo. The gate now asserts the detector id
   in the stored record starts with `mediapipe:`
5. **`worker-io` logged a WARNING about a model it has no use for, on every pool
   child.** `install_mediapipe_detector` checked the model path before checking
   whether MediaPipe was importable, so the four images that are *supposed* not
   to redact each produced an alarming line at startup — which is how a
   genuinely important warning stops being read. The import check comes first
   now, and the two states log at different levels on purpose
6. **A misleading conflict hid a missing migration.** Adding a seventh
   `PolicyKind` without widening five CHECK constraints produced a violation that
   `policy.service.draft` announced as *"created concurrently; re-read and
   retry"* — a phantom race on a single-threaded seeding call. The handler now
   distinguishes a `kind_is_known` violation and says the enum and the schema
   have drifted apart
7. **Pillow's `getdata` is deprecated and this suite runs
   `filterwarnings = ["error"]`.** The §11.1 hash would have started failing on a
   Pillow upgrade rather than on a code change. Replaced with `tobytes`, which is
   also the layout the indexing already assumed
8. **The Phase 3 gate's degradation clause was pinned to a stage name.** Phase 8
   registering two providers moved the first stop from `safety_check` to
   `trust_verification` — because the gate uploads a JPEG magic number with 2 KB
   of zeroes behind it, which §22.1 correctly refuses to let through. The clause
   now asserts the *property* (the pipeline stopped at a stage declaring
   `HALTED_FOR_REVIEW`, the API says so, there is a dead letter) rather than the
   name, which survives Phase 9 too
9. **`visual_only_rules` could never return anything.** It looked for rules with
   visual prompts and no keywords, and `SafetyRule.terms` requires at least one —
   so it was dead code with a docstring claiming a guarantee. Replaced with
   `rules_with_unscored_visual_prompts`, which reports every rule whose visual
   half this build cannot score, and a test pins the constraint that makes a
   wholly-inert rule impossible

**Carried forward, not silently absorbed:**
- **Distant-face recall is not measured, and Phase 0 asked this phase to measure
  it.** Phase 0's carried-forward note is explicit: `blaze_face_short_range`
  detects faces within roughly two metres, street photography contains small
  distant bystanders — exactly the population §22.1 requires blurring — and
  MediaPipe 1.x ships no full-range alternative. **That gap is unchanged and is
  the largest honest weakness in this phase.** What shipped around it: the
  confidence threshold stays biased to 0.4, every detection is expanded 25%
  before blurring, and `media_redacted` records `faces_detected` and
  `faces_blurred` as *separate* fields so a future change that starts dropping
  boxes shows up as a divergence rather than as an unchanged boolean. What did
  not ship is the measurement, because a recall number needs a labelled set of
  real street photographs with annotated faces, and building one is Phase 9's
  validation-harness work rather than something to improvise here. Phase 9's
  gate — which already publishes a reproducible per-category F1 — is where this
  belongs, and it is named there rather than left as a note nobody owns
- **The visual half of §11.2 does not fire.** §11.2 names a CLIP zero-shot
  trigger prompt set alongside the keywords, and `SafetyRule` carries both so
  they are approved together as one danger definition. Scoring prompts is Phase
  9. `rules_with_unscored_visual_prompts` names every rule whose visual half is
  inert, so the shortfall is reportable rather than silent — and no rule can be
  *wholly* visual, because `terms` requires at least one keyword
- **A voice-only report reaches the safety check with nothing to match.**
  Transcription is Phase 9 and runs *after* this stage. A second safety pass
  after transcription is the honest fix and it needs Phase 9's output to exist
  before it can be tested. This is a real gap for exactly the submission path
  §8.4 says the least-served citizens use, and it is stated rather than hidden
  behind a re-run that nothing could verify
- **`trust_thresholds` is not backtestable**, and `runs.run_backtest` refuses it
  by name rather than producing a report about it. Phase 7's decision engine
  reads pixels, EXIF distances and device fingerprints nowhere — the log records
  what each check *concluded*, not what it ran on — so a comparison would report
  "0 affected" for a candidate that inverts every value, which is the exact shape
  of an answer with none of the content. `DECIDABLE_KINDS` names the five kinds
  that can be compared, and `rate_card` is excluded for the same reason
- **`submission_media` is not rebuildable from the log**, and the model docstring
  says so with the argument. It holds the EXIF coordinates, capture time and
  perceptual hash — precisely the values §22.4 requires purged after 90 days —
  and an append-only chain is the one place a value can never be expired from.
  Putting them in a payload would be choosing, permanently and for every tenant,
  that the retention schedule cannot be honoured. The two review tables *are*
  rebuildable and `trust.rebuild` proves it field-for-field
- **Retention is stamped, not swept.** Every artefact carries `purge_raw_after`
  and `purge_exif_after` from the tenant's own policy, indexed for the sweep.
  The sweep is Phase 26's; what this phase owed it is something to find
- **Quarantine orphans are not reclaimed.** A submission whose audio part was
  rejected after its photo stored leaves an unreferenced file, and the tempting
  fix is worse — content addressing means deleting "the file this request wrote"
  can delete a file another complaint references. The reclaim belongs in the same
  sweep, where the log is the authority on what is referenced
- **A backlog in the review queue has no owner.** §11.4's queue is reachable over
  HTTP and nothing assigns it. Phase 13 gives operators identities and Phase 27
  gives them a console; until then `review-queue-backlog.md` is explicit that
  "nobody is working it" is the most common cause and the least technical one

---

## Phase 9 — Perception layer & model registry ✅ · DATA

The phase that turns a citizen's photograph, voice note and description into a
category and an honest confidence — or into a refusal to guess. It is also the
first phase whose deliverable is a *number*, and the number is disappointing;
the section below is mostly about the machinery that makes it trustworthy enough
to be disappointing on purpose.

**Shipped**
- **CLIP zero-shot and multilingual-e5 text scoring, driven entirely by tenant
  prompt sets.** Nothing in `perception/` decides what a category is, what
  describes one, how sure is sure enough, or what to do with a report that
  cannot be classified. That line is what makes the phase's third gate clause
  true rather than aspirational: adding a category touches tenant data and no
  module. The template's `text` prompt family across `en`/`hi`/`mr` shipped with
  it, along with CLIP prompts for the three `municipality` categories that had
  none — a category with no prompt can never be scored, so those three were
  permanently unclassifiable in every municipality tenant
- **CLIP and Whisper both execute in the gate against real bytes** — added
  after the first version of this gate passed 26/26 without either of them ever
  running. See defect 7
- **`faster-whisper` transcription across the tenant's declared locales**, with
  the tenant's list used as a *tie-break* on detection rather than a filter:
  constraining detection would mistranscribe the citizen who speaks something
  the tenant did not list, which is exactly the population §8.4 exists for. The
  detected language wins over the submitted locale when detection was confident,
  and loses when it was not, with `language_uncertain` recording which happened
- **The model registry: single-flight, bounded, and a refusal rather than a
  thrash.** Four Celery children asking for CLIP in the same second get one load
  and the same object; a load that cannot fit within `max_resident_bytes` after
  evicting everything idle raises `ModelCapacityError` with the numbers in it,
  because the tempting alternative — evict whatever is least recently used, in
  use or not — produces a worker where CLIP evicts Whisper, the next voice
  complaint reloads Whisper which evicts CLIP, and throughput collapses to the
  reload time with nothing in any log naming the cause. Prompt matrices are
  registry entries too, keyed on the prompt set's **content hash**, which is what
  makes it a registry of *model and prompt-set pairs*: an edit is a new key by
  construction, so a published taxonomy change cannot be served yesterday's
  matrix
- **Per-tenant, per-category calibration derived from measured curves**
  (ADR-0036), fitted by the harness on the calibration split and written out in
  the shape the policy API accepts — so the harness *proposes* and an approver
  decides, with Phase 6 keeping the trail. Temperature and centre are fitted per
  category; the abstain floor is fitted once for the tenant, and the
  `provenance` on every entry says which is which, because an approver told a
  per-category measurement was made when it was not is being shown a conclusion
  instead of evidence
- **The validation harness, and a committed report artefact** —
  `docs/reports/perception-f1.md`, reproduced by `nem f1`. It runs
  `scoring.decide`, the same function the pipeline stage calls, over a
  stratified held-out split the corpus computes from its own contents
  (ADR-0034). **Macro F1 0.595, micro 0.629, coverage 0.72** across nine
  categories and three locales. Four categories are below the 65% floor and the
  §43.2 prompt pass against them is recorded in the artefact
- **§27.1 measured per model, not once.** `encode_image` 129 ms, `encode_text`
  34 ms, `transcribe` **2461 ms** for a two-second clip — Whisper runs at about
  1.15x real time and is 70x the text encoder. All three are inside the 10 s
  stage budget and the transcriber is inside it *only for short clips*: a voice
  note beyond roughly eight seconds breaches the budget, while
  `MAX_AUDIO_SECONDS` permits 300. Nothing breaks — the stage degrades and a
  human plays the clip — but the practical ceiling is set by recording length,
  and that was unmeasured until now
- **The fitted calibration round-trips through the policy API into the stage**,
  proven by a test that activates a harness-shaped document and asserts the same
  submission then abstains. "The harness proposes and an approver decides" was an
  architecture diagram until that test existed
- **The second §11.2 pass, which Phase 8 named and could not perform.** The
  first safety pass runs before transcription on a queue served by a container
  that has never imported torch — that separation is what makes "a saturated
  `ml` queue cannot delay a danger signal" a fact about two operating-system
  processes. So the deterministic pass stays where it is, and this one adds what
  only the ml worker can know: a hazard in a *transcript* or matched by a
  §11.2 visual prompt now halts the report before any category is claimed
- **Distant-face recall for §22.1, measured** — Phase 0's carried-forward
  question, explicitly not discharged by Phase 8's "a face was blurred". The
  answer is sharp and unwelcome: against the real `blaze_face_short_range` in a
  640×480 frame, **recall is 1.00 at 80 px of face width and 0.00 at 72 px.**
  There is no gradual falloff. That is a cliff, not a curve, and it means a
  bystander more than a few metres away is not blurred at all

**Defects found and fixed during the phase** — the harness found the first two
on its first real run against multilingual-e5, which is the strongest available
argument for it calling the shipped rule rather than its own copy:

1. **`ScoreResult` had no way to report the category it declined to claim.**
   `alternatives` is everything *except* the winner, so a caller reading the top
   of it on an abstained result gets the **runner-up** while believing it has the
   winner. It turned a ranking that was 70% correct into a forced-choice accuracy
   indistinguishable from chance, in a number nobody would have known to
   disbelieve. `top_category` is now populated on every return path
2. **A per-category temperature with no per-category centre is arithmetic, not
   evidence** (ADR-0036). Different temperatures put categories on different
   logit scales and the smallest temperature wins everything. Worse, the contrast
   pool received the temperature and *not* the bias, so at a fitted temperature
   of 0.006 an uncentred contrast logit sat ~140 above the centred positives,
   took the entire softmax, and the layer abstained on **100%** of a corpus it
   was ranking correctly. `logit = (cosine + bias) / temperature` now, with the
   same affine transform on both pools, and `bias` respecified in similarity
   units so the document's ±10 bound means something
3. **The shipped default calibration classified nothing.** `abstain_below = 0.35`
   reads like a sensible threshold and is compared against a confidence whose
   ceiling falls as the taxonomy grows; on nine categories it abstained on every
   held-out example. A new tenant would have classified nothing at all until
   somebody approved a fitted document, and the symptom would have been an empty
   work list rather than an error. Lowered to 0.15 on the measurement — above the
   1/9 a nine-way coin flip reaches, below the 0.164 the fit lands on
4. **A tenant with text prompts and no CLIP prompts had every photographed report
   retried three times and degraded.** `PromptSetUnavailableError` propagated out
   of the image path while the text path caught its own version — the asymmetry
   was backwards, since the image modality is the optional one when a tenant has
   not configured it. Reports whose description would have classified correctly
   were being parked. Found by the live gate, not by a test, because every test
   fixture happened to configure both
5. **`scoring`'s docstring claimed negatives "take probability mass away from the
   category they contradict, and from nothing else".** A softmax denominator is
   shared: a strong negative suppresses *every* category's confidence, not only
   its owner's. The claim was corrected rather than the design changed — a
   per-category contrast is a real alternative, it is not what ships, and the two
   have not been measured against each other, which is now stated instead of
   implied
6. **The first version of the abstain-floor fit swept for maximum F1 on the
   calibration split** — the textbook rule, and on a few dozen examples it lands
   on a knife edge one example wide. Per-category floors fitted that way ranged
   from 0.095 to 0.773 on the same corpus and took two categories from a usable
   ranking to a held-out F1 of exactly zero. Replaced with a quantile operating
   point, pooled across categories, which is stable under small corpus changes
   and is a decision somebody can argue with in words
7. **This gate passed 26/26 while two of the three models had never run.** The
   sandbox tenant ships no `clip` prompt sets, so every photographed report took
   the image path's "no prompts for this encoder" branch and CLIP was never
   invoked; Whisper had never been handed audio by anything except
   `fetch_models.py`'s load check. The gate submitted a photograph on every
   request and proved nothing about either. **The clauses were written by the
   same person who wrote the code, and they avoided exactly the two things that
   were hardest to prove** — which is Phase 2's critique-log defect 12 in a new
   place: a gate clause proven against a weaker claim wearing the same words.
   Clause 3b now attaches CLIP prompts over HTTP and asserts
   `model_ids.image` names `open_clip`, and submits an audio-only report and
   asserts `perception_transcriptions_total` moved. Both are existence proofs,
   not accuracy claims, and they are labelled as such
8. **The §27.1 latency clause measured the cheapest of the three models.** The
   corpus is text, so the harness's per-example timing is the e5 encoder — 34 ms
   — while the clause said "inference latency". CLIP is 4x that and Whisper is
   70x. `measure_inference_latency` now times each model separately and the gate
   asserts all three
9. **The prompt-pass work list was being read off the held-out confusions**,
   which silently converts the held-out set into a development set. The harness
   now measures the work list on the calibration split and the report says, in
   the section where a reader would otherwise reach for the wrong table, why the
   held-out one is published and not used

**Carried forward, not silently absorbed:**
- **The image modality is unmeasured.** The published F1 is the text modality's.
  There is no licence-clean corpus of photographed civic defects in this
  repository, and rendering synthetic street scenes to score CLIP against would
  measure the renderer. The CLIP prompt sets therefore ship unmeasured. Phase 10
  needs image embeddings for dedup Stage 2 anyway, and is where that corpus has
  to arrive
- **§22.1 does not hold for distant bystanders, and now there is a number for
  it.** 80 px of face width in a 640×480 frame is roughly a person within two
  metres — which is exactly what `blaze_face_short_range` says on the tin, and
  exactly what Phase 0 warned about. The measurement does not fix it: the fix is
  a second detector or a tiled pass, and it is a §22 obligation rather than a
  perception feature, so it belongs with the phase that can schedule the extra
  inference. What this phase owed was the number, and the number ships
- **Transcription *quality* is unmeasured.** The gate proves faster-whisper
  decodes real audio and runs its front end — the clip is a synthesised tone, and
  the VAD filter correctly discards it as non-speech, so what executes is ffmpeg
  plus the voice-activity stage and not the acoustic model. Proving §8.4's
  multilingual promise needs spoken audio with a licence, which this repository
  does not have. Recorded here rather than implied by a passing gate
- **Marathi is measurably worse than English and Hindi** (macro F1 0.385 against
  0.638 and 0.630) and the corpus cannot say why — encoder coverage, prompt
  wording and corpus size are all plausible and are not separable from four
  examples per category per locale. The honest statement is the gap, not a cause,
  and separating them needs a native-speaker prompt review
- **Nine held-out examples per category is a small sample**, so one example is
  0.125 of a category's recall and a category can move more than a tenth on a
  single sentence. The macro figure is the number; the per-category column is an
  indication. Growing the corpus is the highest-value next piece of work on this
  layer and it is worth more than another prompt pass
- **The corpus is authored, not field data.** Written in citizen voice from §8's
  defect vocabulary and deliberately not paraphrased from the prompts it is
  scored against — but a real intake queue carries misspellings, code-switching
  mid-sentence, dictation artefacts and reports naming two defects at once. Treat
  the number as an upper bound on the text modality
- **The HNSW parameters were not re-measured against real CLIP output, and Phase
  2 asked this phase to do it.** Phase 2's carried-forward note is explicit: the
  recall curve was measured on synthetic clustered vectors, the low id-recall it
  reports is an artefact of near-ties in that distribution, and the numbers stay
  *provisional* until they are measured against real embeddings. **That did not
  happen and the reason is the bullet three above this one** — re-measuring needs
  a corpus of real photographs to embed, which is precisely what does not exist.
  The obligation moves to Phase 10 rather than being quietly dropped: Phase 10
  builds the first real index, needs the photo corpus for dedup Stage 2 anyway,
  and is the last phase that can change these parameters cheaply

**Gate** ✅ — `nem gate-phase9`, 32/32 against the running stack
- A **published per-category F1 number** in the repo, reproducible by one
  command — and the gate reproduces it, comparing the re-run's numbers and corpus
  fingerprint against the committed artefact rather than trusting that it exists
- Any category below 65% F1 triggers the §43.2 prompt pass and a re-measure; the
  honest number ships either way. **Four categories are below the floor**, the
  pass is recorded, and the gate checks that the work list came from the
  calibration split rather than from the measurement
- A new tenant category is classifiable by adding prompts alone — proven with a
  key the gate greps the repository to confirm no module knows, created and
  classified over HTTP with the API container's identity compared across the run
- Inference latency within the §27.1 budget on this hardware, measured not
  estimated, **per model**: `encode_image` 129 ms, `encode_text` 34 ms,
  `transcribe` 2461 ms, and 16.7 s from HTTP accept to `classification_scored`
  through the real pipeline
- And a fifth clause the phase did not ask for, added because its absence let
  the gate pass while two models had never executed: **the image and audio paths
  actually run**, against real bytes, in the real container


## Phase 10 — Deduplication & clustering engine · DATA

The moat (§14). Never cut, never simplified.

**Ships**
- Stage 1: PostGIS `ST_DWithin`, radius and window from policy, index-backed
- Stage 2: pgvector cosine over image + text embeddings, with 0.8 iterative scans so filtered searches cannot silently under-return
- **Per-category decision bands** resolved from Phase 6 policy
- Cluster centroid, confidence, and severity recomputation on merge, emitting `cluster_match_found`
- **Merge reversibility** — an incorrect merge is undone by a compensating event, never by mutating history
- Repeat-defect mode: the same engine filtered by contractor (§17.5)

**Gate**
- Measured precision/recall against a labelled fixture set of true-duplicate and true-distinct pairs
- **Zero false-positive merges** on the fixture set — a wrong merge suppresses a real citizen report (§14.3)
- Stage 1 eliminates ≥ 90% of candidates before any embedding comparison, verified by query plan
- Decision latency within the §27.1 budget at seeded volume

**Shipped**
- **Stage 1** — `ST_DWithin` against the GiST index on `complaint_clusters.centroid`, with the radius and window taken from the *band* rather than from platform settings, plus two predicates the plan does not name and the engine is wrong without: the time window excludes a defect that was fixed and reopened, and an `EXISTS` over cluster members excludes a different category however close it sits
- **Stage 2** — exact cosine over the members of the surviving candidates, scored as the *best* matching member rather than a centroid or a mean. **This deviates from the plan's "0.8 iterative scans" wording and ADR-0036 records why**: iterative scan is the fix for a filtered ANN search under-returning, the candidate set after Stage 1 is a handful of clusters, and an exact scan is both cheaper at that size and reproducible — which `docs/reports/hnsw-recall.md` shows HNSW is not, since it reorders near-ties
- **Per-category bands from Phase 6**, including the tie rule the bands imply but do not state: two candidates both above the merge threshold and within `ambiguous_margin` of each other are *not* merged, because picking the higher is a coin-flip and §14.3 forbids coin-flips in that direction
- **Merge reversibility** as three appended events — the old cluster records losing a member, a fresh cluster records gaining one, the complaint records its new home. Nothing is deleted, so the log shows the system was wrong and corrected itself
- **`complaint_clustered`** on the complaint chain. A gap rather than a feature: `Complaint.cluster_id` is read by the projection writer and no event on the complaint's own chain ever set it, so before this the column was structurally guaranteed to stay NULL
- The **ambiguous band does something** — a review item under a new `ambiguous_dedup` reason, which is the Phase 16 Investigation Agent's work done by a human until the agent exists

**Gate — NOT MET.** Three clauses of four pass. `nem gate-phase10`:
- ✅ Precision **0.600**, recall **0.600** on `municipality-dedup-v1` (24 reports, 11 incidents), reproduced exactly by one command
- ❌ **Four false-positive merges.** One root error and three cascades: `pothole-jm-road-r1` joined the FC Road cluster, and every later report of either pothole then found a cluster already containing both
- ✅ Stage 1 eliminated 98% of 200 seeded clusters, asserted from `EXPLAIN (ANALYZE)` output naming `ix_complaint_clusters_centroid` — not merely "an index"
- ✅ p95 **15.5 ms** against §27.1's 10 s budget

**Why the gate was not met, and why no threshold was moved.** The true-duplicate
and false-merge confidence distributions **interleave** — highest true duplicate
0.8781, lowest false merge 0.8661 — so *no* value of `merge_threshold` separates
them. This is a statement about the modality, not the tuning: two citizens
describing two different potholes thirty metres apart write nearly the same
sentence, and `multilingual-e5` compresses same-domain civic complaints into a
0.82–0.88 band in which that difference is smaller than the noise. Two remedies
exist — the image modality, and a tighter radius for point defects — and neither
was applied, because the corpus has no held-out split and tuning against the only
measurement available publishes a number about the tuning. `docs/reports/dedup-precision-recall.md`
carries the full diagnosis.

**Carried forward, and now overdue.** Phase 9's F1 report named Phase 10 as where
the photograph corpus had to arrive, because dedup Stage 2 needs image embeddings.
It has not arrived; there is still no licence-clean set of photographed civic
defects in this repository. `image_weight` therefore ships unmeasured for a second
phase running, and the false merges above are the first concrete cost of that gap
rather than a hypothetical one. Closing Phase 10's gate most likely requires
closing this first.

**Defects the gate caught**
1. **Adding a `ReviewReason` member passed every static check and failed at
   runtime.** The models build their `CHECK` constraint from `REVIEW_REASONS`, so
   the enum, `ruff`, `mypy --strict` and every test that did not insert a row all
   agreed the new value was legal — while the database, holding the list Phase 8's
   migration wrote out literally, rejected it. Found by an integration test
   inserting a real review item; fixed by a migration that alters both
   `review_queue_items` and `review_decisions`, because a reason the queue can
   hold and the decision table cannot is an item that can be raised and never
   resolved.
2. **The first dedup fixtures could not tell two reports apart.** The vector
   helper built components from `sin(seed·k + i·c)`, which looks like it scatters
   and does not — every vector was the same wave at a different phase, and two
   deliberately unrelated seeds came out at cosine 0.997. A test asserting "these
   are different reports" was asserting nothing. Replaced with normalised Gaussian
   components, which are near-orthogonal in high dimensions the way the real
   encoders are.
3. **The ambiguous-band test proved the wrong thing.** Its two "neighbours" were
   close enough to merge with *each other* during setup, so only one cluster ever
   existed and the test passed a report through an unambiguous merge while
   claiming to exercise the ambiguous band. Fixed by geometry: both incidents 30 m
   from the subject and 60 m from each other.

## Phase 11 — ML platform: labelling, drift & feedback loop · DATA

The phase that makes §4.3's moat real rather than asserted. Without it, accuracy
decays quietly and the "system that gets better" claim is marketing.

**Ships**
- **Labelling pipeline** fed automatically by human review decisions, merge overrides, citizen disputes, and closure confirmations — the labels the previous plan discarded
- Versioned datasets with lineage, so any reported metric is reproducible from an exact snapshot
- **Drift detection** on input distributions and confidence distributions, alerting when production diverges from the evaluation set
- Champion/challenger with shadow evaluation, and promotion gated on measured improvement
- Active learning: surface the most informative uncertain cases to reviewers first, so limited human attention buys the most accuracy
- A regression set that grows with every production error and is never allowed to shrink
- Per-tenant model performance reporting, since accuracy is not uniform across geographies

**Gate**
- A month of review decisions demonstrably improves a measured metric on a held-out set
- Injected drift triggers an alert within one detection window
- A challenger cannot be promoted without beating the champion on the frozen regression set
- Every published accuracy number is reproducible from a versioned dataset snapshot

## Phase 12 — Severity, routing & SLA engine · DATA

**Ships**
- Rubric evaluation from Phase 6 policy, writing a complete `severity_breakdown` with the policy version recorded per complaint
- OSM road-class and POI-proximity enrichment, locally cached with a cold-start fallback
- Cluster-density re-scoring when report count crosses threshold (§12.5), implemented as an explicit rule and **labelled as a rule, never blurred into the agent claim**
- Routing rule evaluation against the tenant organisation model
- Calendar- and season-aware SLA computation (§13.4), with a Beat-driven sweeper and auto-escalation

**Gate**
- Any scored complaint reproduces its score from its logged breakdown alone, proven by a property test over random inputs
- A policy version change never mutates previously scored complaints
- SLA escalation fires within one sweep interval of the deadline, clock-controlled
- Holiday and monsoon windows measurably shift deadlines as configured

---

# Track D — Accountability

## Phase 13 — Identity, authorization & org modelling · SEC

**Ships**
- OIDC-capable identity with JWT, refresh rotation, revocation, and optional SSO for institutional tenants
- **Attribute/relationship-based authorization**, not a fixed role enum: custom roles composed from permissions, scoped by department, zone, site, or taxonomy node
- Delegation and temporary grants with expiry, for leave and shift coverage
- Contractor organisations with their own sub-users and scoped access
- **Audited impersonation** for support, requiring justification and producing an event visible to the tenant
- Default-deny posture with a centralised decision point and a decision log

**Gate**
- A negative-authorisation test **per role pair**: department head A provably cannot read department B's rows through any endpoint, including error messages and pagination counts
- Adding an endpoint without a declared permission fails CI
- Authorization decisions are property-tested against a generated role/scope matrix
- Every impersonation session is visible to the impersonated tenant

## Phase 14 — Department workflow & work orders · PROD · PLT

**Ships**
- Triage queue sorted by SLA and severity, role-scoped automatically (§15.1)
- Assignment to staff or contractor, with workload- and rating-ranked contractor selection (§15.3)
- Rate-card budget entry against the effective-dated card from Phase 6
- Milestone evidence workflow; fund release is **SIMULATED**, labelled in the data model, the API response, and the UI
- Kanban with SLA countdown badges in the shared severity colour language (§15.7)
- Bulk operations and saved views for high-volume departments

**Gate**
- A work order's full evidence trail is scoped correctly to every role that can see it
- Contractor assignment distribution is visible and auditable, so silent favouritism is detectable
- Every workflow transition is an event; no status is directly editable

## Phase 15 — Closure loop & evidence verification · PLT

The fix for the trust-collapse mechanism (§3.1) — the reason the product exists.

**Ships**
- Before/after photo pairing with SSIM structural verification gating `pending_verification` (§21.2)
- **Perceptual-hash guard so a "before" photo cannot be resubmitted as the "after"**
- Citizen confirm / dispute / auto-confirm-**unconfirmed** on a policy-configured window, visibly distinguished from real confirmation
- Notification fallback chain on a decoupled worker, so a notification failure never blocks a work order
- Dispute reopening and escalation
- Resolution-streak retention mechanic (§21.3), promoted from ROADMAP because it targets the exact decay this product exists to prevent

**Gate**
- A closure cannot reach `resolved` without a passing SSIM delta — enforced in the state machine, not the UI
- An identical or near-identical after-photo is rejected
- Auto-confirmed closures are distinguishable from citizen-confirmed in the API, the UI, and the contractor's computed rating

## Phase 16 — Investigation Agent & agent platform · DATA

The single genuinely agentic component (§12.4), built to survive "walk me
through what it decided."

**Ships**
- LangGraph state machine over local `llama3.1:8b`, with a **tool registry** — tools declared with typed schemas, permissions, timeouts, and cost budgets rather than wired inline
- Tools: request additional photo, OSM utility-proximity query, location complaint history — sequencing decided from prior results, which is the actual difference between an agent and a function with an LLM call in it
- Structured conclusion with a logged natural-language justification in exactly the §12.4 payload shape
- Full trace persisted as events, checkpointed and replayable
- **Agent evaluation harness**: a fixed scenario suite scoring decision quality, so a model or prompt change is measured rather than vibed
- Guardrails: output schema validation, tool-call allow-lists, budget ceilings, and **prompt-injection hardening on citizen-supplied text** — closing the gap §25.1 honestly lists as unmitigated

**Gate**
- Any invocation is replayable step-by-step from the event log alone
- Ollama-down and tool-timeout both degrade to human review with a `system_degradation` event, never a hang
- Adversarial complaint text cannot redirect tool calls — covered by an explicit injection suite
- A prompt or model change is accepted only on a measured improvement against the scenario suite
- End to end within the §27.1 budget

## Phase 17 — Contractor transparency, fraud & equity · DATA · SEC

**Ships**
- Public contractor profiles with computed reputation metrics — never a collapsed star rating (§16.1)
- Rate-card deviation anomaly detection (REAL); quantity-vs-photo (SIMULATED, labelled)
- Entity resolution over shared address / phone / director with `pg_trgm` fuzzy matching (§17.1), kept internal per the defamation reasoning
- Fund-source and scheme tagging with ward budget transparency (§17.6)
- Underreporting-zone equity flag from OSM infrastructure proxies versus complaint density (§23.2)
- **Contractor dispute/appeal workflow shipped in this phase, not deferred** — §6.5 requires the appeal path to arrive with the accountability feature, not after it
- Positive-framed default metrics with raw exposure metrics as a drill-down (§23.3)

**Gate**
- A serializer-level test proves **no unreviewed flag can reach a public response body** — the §22.2 defamation control enforced in code, not in a template
- Every public metric is derived from the event log and traceable to its source events
- GPS coarsening and PII scrubbing verified on every public endpoint
- A contested flag provably disappears from public view pending review

---

# Track E — Experience

Three.js is a first-class engineering workstream, not decoration. §6 Design
Principle #9 governs: **every 3D element maps to a real pipeline event**, and
every scene ships a documented fallback, because a crashed WebGL context in
front of a buyer is worse than no 3D.

> **Hardware note.** The browser's WebGL context shares the RTX 5060's 8 GB with
> Ollama. Scene VRAM is budgeted at ≤ 512 MB and asserted in CI, so the 3D layer
> can never starve the Investigation Agent.

**This track has its own blueprint.**
[`NEMESIS-Frontend-Blueprint.md`](../NEMESIS-Frontend-Blueprint.md) is the Track E
peer of `NEMESIS-Blueprint-v2.md`: art direction, design system, frontend
architecture, and the surface-by-surface specification. It supersedes the main
blueprint's §8.1, §19 and §20 and records in its §E2 exactly where those sections
were wrong. The gates below are restated and extended in its §E25 — where the two
disagree on a Track E gate, the frontend blueprint governs, because it is the
newer document and it states its reasoning.

[`docs/FRONTEND-EXECUTION-PLAN.md`](FRONTEND-EXECUTION-PLAN.md) is the build
sequence for this track: milestones M0–M12, each with its entry condition, its
deliverables, and the gate above that it closes. The blueprint governs direction;
the execution plan governs order.

## Phase 18 — Design system, application shell & i18n · PROD

**Ships**
- Next.js 15 App Router, TypeScript strict, on host Node 24 with Turbopack
- **Design practice, not just components**: usability testing with real field staff and department users, task-success measurement, and a content/tone system for a government context
- Tailwind + shadcn/ui with severity-semantic tokens shared by the 2D UI *and* the shader uniforms — one vocabulary defined once (§19.3)
- **Full internationalisation**: locale-driven copy, Devanagari typography, RTL-ready layout primitives, and translation workflow tied to the Phase 5 locale registry
- Accessibility program: WCAG 2.2 AA target, keyboard paths, screen-reader labels, contrast verified against the severity palette
- Generated TypeScript API client; drift fails CI
- Zustand store fed by the WebSocket, with a subscription path bypassing React re-render for high-frequency updates
- Storybook, `axe` gating, Lighthouse budgets

**Gate**
- Lighthouse ≥ 90 performance and accessibility on citizen and department routes
- WCAG 2.2 AA verified by audit, not only by automated scan
- Zero `any` in application source
- A locale added in the control plane appears in the UI with no code change
- Measured task-success rate from a usability session, with findings tracked

## Phase 19 — Geospatial 3D engine · PROD

The reusable Three.js core, built as a tested library rather than page code.

**Ships**
- ~~`WebGLRenderer` configured properly: WebGL2~~ — **superseded by §E15 and ADR-0037: `WebGPURenderer`**, which selects a WebGPU or
  WebGL 2 backend itself, so §E13's Tiers S and A are the renderer's own
  property rather than a branch we maintain. The rest of the row stands:
  `high-performance` power preference, correct colour management
  (`SRGBColorSpace`), ACES Filmic tone mapping, adaptive device pixel ratio
- **Web Mercator → local ENU-metre projection**, so geometry is in real units and floating-point precision holds at city scale
- OSM building-footprint extrusion via `ExtrudeGeometry`, tiled with distance-based LOD and frustum culling
- `InstancedMesh` complaint pins with `InstancedBufferAttribute` per-instance severity, state, and animation phase — **one draw call for the entire city**
- GPU picking and `three-mesh-bvh` raycasting for interaction at scale
- Camera rig with damped `MapControls` plus scripted, data-driven camera moves
- Render loop instrumented via `renderer.info`, with an adaptive quality manager that degrades *effects* before it degrades *frame rate*
- `webglcontextlost` / `webglcontextrestored` handling with full scene reconstruction
- Headless WebGL harness (Playwright + SwiftShader) with golden-image visual regression

**Gate**
- 60 fps sustained with 5 000 instanced pins plus extruded buildings on this laptop, measured
- Draw calls under budget; VRAM ≤ 512 MB asserted in CI
- Forced context loss recovers to a correct scene without a page reload
- `prefers-reduced-motion` and a no-WebGL device both render a correct, usable map

## Phase 20 — Signature scenes & shader layer · PROD

Each scene is driven by a real pipeline event and has a fallback path.

> **Two rows below carry a supersession.** They were written against a
> WebGL-only three.js and a Leaflet map, and §E15 changed both. The
> original text is struck rather than deleted, per the same practice §E2
> applies to itself: a document that quietly rewrites its own history
> teaches nobody, and the next person makes the same choice again.

**Ships**
- **Cluster-merge (hero, §19.2)** — instance positions interpolated toward the merged centroid on a shared `uMergeProgress` uniform, height and colour tracking recomputed severity, driven by the live `cluster_match_found`. ~~custom GLSL~~ — **superseded by ADR-0037: authored in TSL**, compiled once to WGSL and GLSL. ~~*Fallback: CSS transform on Leaflet DOM markers*~~ — **superseded by §E15: Leaflet is not used**; the 2D and heavy-layer path is MapLibre GL + deck.gl, and §E16 Act 6 adds the beats §19.2 omitted — the second ink overprinting, the thumbprint, and the registration rings that say deduplication is not deletion.
- **Severity field** — GPGPU ping-pong FBO simulation advecting a density field, so severity reads as terrain rather than scattered dots. *Fallback: precomputed heatmap texture.*
- **Safety-flag pulse** — selective bloom via render layers on `safety_trigger_fired`, making the deterministic fail-safe visible the instant it bypasses the queue. *Fallback: CSS keyframe halo.*
- **Closure dissolve** — a resolved incident dissolving on `citizen_confirmed`, closing the loop visually the way the system closes it operationally. *Fallback: opacity transition.*
- `EffectComposer` with SMAA and selective bloom, budgeted per frame
- Shader uniforms bound to the Phase 18 design tokens — severity colour defined once

**Gate**
- Every scene is triggered by a genuine backend event in an E2E test — **a scene that can only be fired by a button fails the gate**
- Every fallback is exercised in CI by forcing the flag
- Golden-image regression passes per scene at fixed seed and camera
- Frame budget held with all effects enabled

## Phase 21 — Temporal replay: the event-log time machine · PROD · PLT

The feature the event-sourced architecture uniquely earns (§9.1), and the one no
competitor can copy without rebuilding their foundation.

**Ships**
- A time scrubber replaying a zone's full history in 3D — complaints appearing, clustering, routing, resolving — **reconstructed from the append-only log, not from a recording**
- Server-side windowed event streaming with snapshot seeking, so scrubbing to any date is fast at any history length
- Variable-speed playback assembled through the same projectors the live system uses
- Overlays: budget spend accumulating, SLA breaches igniting, contractor attribution
- Export to a shareable deep link at a specific timestamp and camera

**Gate**
- Replayed state at timestamp *T* is byte-identical to the projection computed independently at *T*
- Scrubbing 12 months of seeded history holds frame budget
- Replay is provably read-only — it cannot emit events or mutate projections

## Phase 22 — Field & offline experience · PROD

Field staff work in basements, back lanes, and dead zones. The people expected to
upload closure evidence have the worst connectivity in the system.

**Ships**
- PWA with installability, service worker, and an **offline submission and evidence queue** that survives app restart
- Conflict-free sync on reconnect, with server-side idempotency making replays safe
- Camera-first capture flow with client-side compression and EXIF preservation
- Low-bandwidth mode and degraded-map tiles
- Background upload with visible per-item state, so nothing fails silently

**Gate**
- A complaint and a closure photo captured fully offline sync correctly on reconnect
- A killed app mid-upload resumes without duplicating or losing the submission
- The flow is usable end to end on a throttled 2G profile

---

# Track F — Data & Analytics

## Phase 23 — Analytics platform & metrics layer · DATA

**Ships**
- Analytical store fed from the event log by CDC, cleanly separated from the operational database
- **Governed metrics layer** where every §41 KPI has one definition, so a number means the same thing in a dashboard, an API, and a customer report
- Product telemetry with a privacy-respecting event taxonomy
- Operational dashboards for internal use and tenant-facing dashboards for customers
- Data quality monitoring with freshness, volume, and schema assertions

**Gate**
- Every §41 KPI is computed from the metrics layer, and two surfaces reporting the same metric provably agree
- A data quality regression alerts before it reaches a customer dashboard
- Analytics carries no citizen-identifying data, verified by test

## Phase 24 — Experimentation & continuous improvement · DATA

**Ships**
- Experiment framework with assignment, exposure logging, and guardrail metrics
- Applied first to the questions that matter: does the resolution-streak mechanic (§21.3) actually reduce reporting decay, and does positive-framed metric ordering (§23.3) affect department adoption
- Closed loop into Phase 7, so a winning configuration becomes a proposed policy change with evidence attached

**Gate**
- One experiment runs end to end with a pre-registered hypothesis and a decision recorded
- Guardrail metrics can halt an experiment automatically
- Experiment assignment is provably stable and leak-free across sessions

---

# Track G — Trust, Security & Compliance

## Phase 25 — Security hardening & threat verification · SEC

**Ships**
- **Postgres Row-Level Security on the highest-risk tables**, closing the §18.3 gap rather than documenting it
- Every §25.1 threat row mapped to a test that proves the mitigation, or an explicitly labelled open item
- Fault injection via `toxiproxy`, with every §27.3 runbook scenario exercised as an automated test
- Input hardening, SSRF protection on outbound enrichment calls, upload sandboxing, and content sniffing
- Third-party penetration test with tracked remediation
- Security headers, CSP, and dependency/secret scanning enforced in CI

**Gate**
- Every §27.3 runbook scenario passes as an automated test
- No high-severity vulnerability in shipped images
- Pen test findings are remediated or formally accepted with a documented rationale
- RLS proven to block a direct database query that the application layer would have refused

## Phase 26 — Privacy & DPDP compliance program · SEC

Compliance as running systems, not policy prose.

**Ships**
- **Consent registry** with purpose, version, and withdrawal, recorded as events
- **Data-subject request fulfilment** — access, correction, and erasure — reconciled with an append-only log via cryptographic erasure and tombstoning, since deletion and immutability must be made to coexist deliberately
- Automated retention enforcement on the §22.4 schedule, with proof of deletion
- DPIA, records of processing, and a data-flow map maintained as living artefacts
- Breach detection and notification runbook, rehearsed
- Data-residency enforcement per tenant configuration
- Minors' data handling per §22.3

**Gate**
- A data-subject erasure request completes within the statutory window, verifiably, without breaking chain integrity
- Retention deletion is automated and proven, not manual
- A breach simulation completes the notification runbook within its required window

---

# Track H — Commercial & Operations

## Phase 27 — Tenant operations, metering & support console · BIZ

§28 states a pricing model. This is where it becomes a business rather than a slide.

**Ships**
- Self-serve and assisted tenant provisioning from templates, with a trial path
- **Usage metering** per §28's dimensions, with per-tenant COGS attribution so unit economics are measured rather than assumed
- Subscription and invoicing integration
- **Support console**: audited impersonation, tenant health, queue depth, error surfacing, and configuration inspection — so a customer question does not become an engineer running SQL against production
- Customer-facing SLA reporting derived from the same event log as everything else
- Onboarding and migration tooling for importing an incumbent system's history

**Gate**
- A new tenant is provisioned, configured, and live without engineering involvement
- Metered usage reconciles exactly with the event log
- A support engineer resolves a seeded customer issue using only the console, with every action audited

## Phase 28 — Performance, resilience & disaster recovery · SRE

**Ships**
- `k6` load scenarios asserting every §27.1 budget as CI thresholds
- Capacity model and documented scaling limits per tier
- Backup, point-in-time recovery, and a **timed restore drill against stated RTO/RPO**
- Multi-AZ posture and a documented failover procedure
- Error budgets enforced with a policy that actually halts feature work when exhausted
- Game-day exercise against the production-shaped environment

**Gate**
- Every SLO met under load at target volume
- A full restore completes within the stated RTO, drilled and timed
- A game day runs with the on-call responding through documented runbooks

---

# Track I — Release

## Phase 29 — Seed, E2E & release certification · all

**Ships**
- Deterministic seed: 300–500 complaints with realistic ratios, planted anomalies, seeded abuse accounts, and 12 months of backdated history to feed Phase 21
- Multiple tenant profiles seeded — municipality, campus, industrial park — proving the control plane rather than asserting it
- Full Playwright E2E: submit → classify → cluster → score → route → assign → execute → SSIM verify → citizen confirm → public record
- Kill-and-restart resilience across every service (§37.4 gate 4)
- **§44 REAL/SIMULATED/ROADMAP table reconciled line by line against reality**
- Operator documentation, one-command bootstrap, recorded fallback demo, and a release certification checklist

**Gate**
- One command, air-gapped, from clean checkout to a fully working system
- Full E2E passes on the demo laptop, on battery, with WiFi disabled
- The same E2E passes against **three structurally different tenants**
- Every claim in `README.md` and the §44 table traces to a passing test or a shipped artefact
