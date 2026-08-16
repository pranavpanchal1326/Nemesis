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

- initial release of NEMESIS blueprint and documentation

### Changed

- organize logo assets into assets/ directory and update README image links

### Documentation

- finalize full aesthetic README
- finalize complete aesthetic README
- modernize README with aesthetic layout, manifesto narrative, and new logo assets

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
