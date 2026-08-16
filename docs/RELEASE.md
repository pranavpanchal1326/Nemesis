# Release & versioning policy

NEMESIS is a multi-tenant product, and §16.3 promises journalists and civil
society a durable public API. Both mean compatibility is a commitment with a
clock on it, not a courtesy — so the versioning rules are written down before
there is anything to break.

## Versioning

**Semantic versioning** on the artefact: `MAJOR.MINOR.PATCH`.

| | When |
|---|---|
| `MAJOR` | A breaking change to a published contract — an API version removed past its deprecation window, an event payload that will not upcast, a configuration key removed |
| `MINOR` | New capability, backward compatible. New endpoints, new event types, new policy fields with defaults |
| `PATCH` | Fixes and internal changes with no contract effect |

Pre-1.0 while the platform spine is being built. That is not a licence to break
things casually — it means the *product* API surface is not yet published, and
the internal contracts (event schemas, migrations) already follow the rules
above because retrofitting them later is what item 11 of the critique log is
about.

**Version is stated in exactly two places** and a test asserts they agree:
`backend/pyproject.toml` and `nemesis.__version__`. `Settings.service_version`
reads from the package, so the number in the `/health` response, the OpenAPI
document, the OTel resource attributes, and the built image cannot disagree.

## Conventional commits

Enforced by a `commit-msg` pre-commit hook. Allowed types: `feat`, `fix`,
`docs`, `refactor`, `test`, `chore`, `perf`, `build`, `ci`, `revert`.

```
<type>(<scope>): <subject>

<body — the WHY, not the what; the diff already says what>

BREAKING CHANGE: <what breaks, and what to do instead>
```

`feat` implies MINOR, `fix` implies PATCH, and a `BREAKING CHANGE:` footer
implies MAJOR. This is not ceremony: it is what lets the changelog be generated
rather than written, and a generated changelog is one that is actually accurate.

Scope is the track or component — `flags`, `obs`, `dedup`, `api`, `compose`.

## Changelog

`CHANGELOG.md` is **generated from commit history**, never hand-edited:

```bash
nem changelog
```

Hand-maintained changelogs drift, and a changelog that is wrong is worse than
none — during an incident it is read as a record of what changed, and a missing
entry sends the investigation the wrong way.

## Deprecation clock

Published contracts are removed on a schedule, announced in advance, and the
schedule is the promise:

| Contract | Notice before removal | Announced in |
|---|---|---|
| Public API version (§16.3, §26.4) | **12 months** | Changelog, `Deprecation` + `Sunset` headers, developer portal |
| Partner/webhook payload field | 6 months | Changelog, delivery log notice |
| Event payload version | Never removed | Upcasters are permanent — replay must work for the life of the log |
| Configuration key | 3 months | Changelog, startup warning |
| Feature flag | Its `remove_by` date | CI failure |

Two of these deserve their reasoning stated:

**Event payload versions are never removed.** An append-only log lives for
years, and a replay that cannot read a 2026 event is a log that has silently
stopped being a durable record. Every event type keeps an upcaster from every
prior version, forever, and CI fails on a payload change without one. The cost
is a growing set of small upcasters; the alternative is event sourcing as a
liability rather than an asset (critique log item 11).

**Public API notice is 12 months**, which is long. §16.3 promises civil society
and journalists a public interface, and those consumers are not engineering
organisations who can re-integrate on a quarter's notice. A shorter window would
make the promise conditional in a way it was not stated to be.

## Release process

Local-first, and honest about it: there is no promotion pipeline until Phase 1b,
because there is nowhere to promote to.

1. `nem check` — lint, format, types, tests with coverage, migrations.
2. `nem parity` and `nem docs-check` — the deployment contract and runbook
   coverage.
3. Bump the version in `pyproject.toml` and `nemesis/__init__.py`.
4. `nem changelog` and review the generated output.
5. Commit as `chore(release): vX.Y.Z`, tag `vX.Y.Z`.
6. CI builds, produces an SBOM, and scans for vulnerabilities.

**What Phase 1b adds:** build once and promote the *same artefact* through
environments, ephemeral preview environments per pull request, and automated
rollback on a failed deploy. Steps 1–6 do not change; what changes is where the
artefact goes afterwards.

## Rollback

Today, rollback is `git revert` plus a rebuild, and a migration rollback is
`alembic downgrade`. Migration reversibility is CI-enforced on every commit —
upgrade, downgrade to base, upgrade again — because a downgrade that has never
been executed is a rollback plan nobody has tested.

**A rollback is not free once state has moved.** A downgrade that drops a column
loses the data written since the upgrade. When that is the case, the correct
response is usually to fix forward, and the decision belongs to the incident
lead rather than to whoever is at the keyboard.

Automated rollback on a failed deploy is Phase 1b, with a gate requiring a
deliberately broken deploy to roll back with no human action.

## Supply chain

Every image gets an SBOM (`syft`) and a vulnerability scan (`grype`) in CI,
failing the build at high severity. Base images are pinned by digest.
Dependency updates are automated — see `.github/dependabot.yml` — grouped so
that routine patches arrive as one reviewable change rather than fifteen.

The weekly scheduled scan matters more than it looks: it re-scans **unchanged**
images against an updated vulnerability database. Dependabot only tells you when
a dependency has a new version; it does not tell you when yesterday's pinned
version acquired a CVE overnight.
