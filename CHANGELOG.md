# Changelog

Generated from conventional commits by `nem changelog`. **Do not hand-edit** —
edits are overwritten on the next run, and a changelog that is wrong is worse
than none: during an incident it is read as a record of what changed, and a
missing or invented entry sends the investigation the wrong way.

Policy, deprecation clocks, and the release process: [docs/RELEASE.md](docs/RELEASE.md).

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

<!-- generated:start -->

## [Unreleased]

### Added

- generate comprehensive product screenshots, prototype slide deck, and typography enhancements
- implement field operator mobile app, synthesized foley sound engine, character ink state machines, and ADRs 0048-0061
- implement clay 3D city generator, console operational suite, story landing film, visual regression snapshots, and ADRs 0047-0055
- implement public portal, tenant open-data endpoints, honesty breakdowns, and ADR 0046
- M5 citizen loop, the four backend decisions it needed, and a demo city
- implement frontend components, realtime stream client, OpenAPI generation, and i18n subsystem
- bootstrap frontend architecture, design system tokens, press filter, and ADRs 0037-0042
- implement Phase 10 deduplication and clustering pipeline
- complete Phase 9 perception layer, calibration pipeline, and gate suite
- implement Phase 9 perception and calibration pipeline
- implement Phase 7 simulation engine and Phase 8 trust & safety spine
- implement Phase 6 policy and rules engine
- implement Phase 4 public API, webhooks, versioning, and developer portal
- implement Phase 5 multi-tenant control plane, provisioning, taxonomy, and calendars
- implement Phase 3 transactional outbox, pipeline orchestration, realtime hub, and rate limiting
- implement projection writer service and environment parity checks
- implement Phase 2 event store, schema registry, projections, and tenancy model
- add NEMESIS core architecture, backend services, docs, and infrastructure setup
- initial release of NEMESIS blueprint and documentation

### Fixed

- **assets:** restore binary images destroyed by a UTF-8 text round-trip

### Changed

- organize logo assets into assets/ directory and update README image links

### Documentation

- relocate product preview images to assets/ and fix README rendering
- update frontend blueprint, execution plan, and token schema
- add handover guide, backlog, upgrades roadmap, and renumber ADR-0036
- add ADRs 0018-0020 and taxonomy misconfiguration runbook
- finalize full aesthetic README
- finalize complete aesthetic README
- modernize README with aesthetic layout, manifesto narrative, and new logo assets

> 2 commit(s) did not parse as conventional commits and are not listed. If this number is not zero, the commit-msg hook is being bypassed.

<!-- generated:end -->

## [0.1.0] — 2026-08-16

The first two phases. Listed by hand because they predate this file and the
commit history that generates it; every entry below is reconciled against a
passing gate rather than a memory.

### Phase 1a — Engineering operating system (local-first)

- **Observability stack** in an opt-in compose profile: OpenTelemetry collector,
  Tempo, Prometheus, Alertmanager, and Grafana with provisioned dashboards and
  datasources. §41 KPIs defined once as Prometheus recording rules; alert rules
  over the §27.1 budgets and the §27.3 failure scenarios.
- **Feature flags** with kill switches, tenant targeting, stable-hash staged
  rollout, and a `remove_by` date per flag that CI enforces.
- **Runbooks** for every §27.3 scenario and every declared dependency, with CI
  checking that the coverage is real and that no alert links to a missing page.
- **Incident process** with severity definitions, a blameless post-mortem
  template, and a tracked action register.
- **RFC process** alongside the existing ADR practice.
- **Secrets rotation procedures**, with CI enforcing that every secret in the
  deployment contract has one.
- **Environment parity check** — the list of what a deployed environment must
  supply, verified against `.env.example` and compose so the Phase 1b migration
  is mechanical rather than exploratory.
- **Dependency update automation**, grouped, plus a weekly re-scan of unchanged
  images against a fresh vulnerability database.

### Phase 0 — Foundation & guardrails

- Compose stack: Postgres 17.5 + PostGIS 3.5.2 + pgvector 0.8.6, Redis 7.4,
  `api`, `worker-io`, `worker-ml`, `beat` — healthchecked and memory-capped.
- Typed configuration with rubric weights and dedup bands as validated
  invariants; production safety guards refusing to boot a pilot with the
  development signing key or a wildcard CORS policy.
- `structlog` JSON logging with correlation IDs propagated via `ContextVar`.
- OpenTelemetry tracing across FastAPI, SQLAlchemy, asyncpg, Redis, and Celery.
- Prometheus `/metrics` with the §41 domain metric families and bounded label
  cardinality.
- RFC 9457 Problem Details error contract, security headers, CORS.
- Liveness/readiness split where readiness returns 503 on dependency failure.
- Async SQLAlchemy engine, transactional `session_scope`, Alembic on the async
  driver.
- Celery three-queue topology split by memory profile.
- Zero-install task runner (`nem`), pre-commit hooks with secret scanning, six
  ADRs, and CI in six jobs.
- All model weights fetched and verified into an offline cache (3.0 GB),
  air-gap verified under `--network none`.

[Unreleased]: https://github.com/pranavpanchal1326/Nemesis/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/pranavpanchal1326/Nemesis/releases/tag/v0.1.0
