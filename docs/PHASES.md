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
| 3 | **No way to evaluate a config change before it goes live** | Someone retunes a dedup threshold and discovers the damage in production, on real citizen reports | Phase 7 (simulation & backtesting) |
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
| 7 | Configuration simulation & backtesting | B · Control Plane | DATA | 6 |
| 8 | Trust & safety spine | C · Intelligence | DATA · SEC | 3, 6 |
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
- **Phase 7 is what makes this safe to use at scale.** Activation currently
  depends on an approver reading a document. Backtesting a candidate against
  historical events, quantifying the affected population before anyone approves,
  and blocking activation on a regression against a labelled set are all Phase 7,
  and the runbook says so where an operator will read it

## Phase 7 — Configuration simulation & backtesting · DATA

The capability that makes Phase 6 safe to use, and the mechanism behind §13.3's
promise that the rubric improves as resolution data accumulates.

**Ships**
- **Replay a candidate policy against historical events** and report the delta: which complaints would have changed severity, which merges would have flipped, which SLAs would have breached
- Side-by-side diff of current versus candidate, with the affected population quantified before anyone approves
- Shadow mode: run a candidate policy alongside production, recording what it *would* have decided without acting on it
- Guardrails that block activation on a regression against a labelled evaluation set
- Auto-tuning proposals for dedup thresholds derived from human merge/split decisions, surfaced as drafts for approval — never applied automatically

**Gate**
- A rubric change is backtested over 12 months of seeded history, producing a quantified impact report before activation
- A policy that regresses the labelled evaluation set cannot be activated
- Shadow mode provably cannot mutate state or emit domain events

---

# Track C — Intelligence

## Phase 8 — Trust & safety spine · DATA · SEC

Highest credibility-per-hour in the system (§11), now policy-driven.

**Ships**
- EXIF/GPS cross-check; absent EXIF *reduces trust* rather than rejecting, with live-capture-only mode as the real control (§11.1)
- Perceptual hashing against submission history, tolerant of recompression and resize
- Device-velocity and geographic-clustering coordinated-abuse detection (§11.3)
- **Safety fail-safe (§11.2)** executing the Phase 6 ruleset as a hard deterministic rule, on its dedicated queue
- MediaPipe face blur applied *before* any persistence, including temp paths
- Human review queue with the full evidence bundle, and every decision captured as a label for Phase 11 (§11.4)

**Gate**
- The safety bypass provably fires **before** any scoring stage
- No code path can persist an unblurred image — enforced by a repository-level guard test, not convention
- Safety-queue latency is unaffected by a saturated `ml` queue, proven under load
- A tenant with custom safety keywords gets correct behaviour with no code change

## Phase 9 — Perception layer & model registry · DATA

**Ships**
- CLIP zero-shot driven by **tenant taxonomy prompt sets** from Phase 5, versioned as events
- `faster-whisper` transcription across the tenant's configured locales, with language detection
- `multilingual-e5-small` embeddings with correct asymmetric prefixes
- **Model registry**: versioned model + prompt-set pairs, warm-loaded with a single-flight guard so concurrent tasks never double-load weights, and a bounded memory ceiling
- Per-tenant, per-category confidence calibration derived from measured curves
- Validation harness computing per-category precision/recall/F1 on a stratified held-out set, emitting a committed report artefact

**Gate**
- A **published per-category F1 number** in the repo, reproducible by one command
- Any category below 65% F1 triggers the §43.2 prompt pass and a re-measure; the honest number ships either way
- A new tenant category is classifiable by adding prompts alone
- Inference latency within the §27.1 budget on this hardware, measured not estimated

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
- `WebGLRenderer` configured properly: WebGL2, `high-performance` power preference, correct colour management (`SRGBColorSpace`), ACES Filmic tone mapping, adaptive device pixel ratio
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

**Ships**
- **Cluster-merge (hero, §19.2)** — custom GLSL interpolating instance positions toward the merged centroid on a shared `uMergeProgress` uniform, height and colour tracking recomputed severity, driven by the live `cluster_match_found`. *Fallback: CSS transform on Leaflet DOM markers, same contract, one flag apart (§20.3).*
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
