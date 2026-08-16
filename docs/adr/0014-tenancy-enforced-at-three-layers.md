# 0014 — Tenant isolation is enforced at three layers, and none of them is convention

- **Status:** Accepted
- **Date:** 2026-08-16
- **Owner:** PLT · SEC
- **Blueprint:** §18.3, §25.1

## Context

The program plan asks for "a query-construction guard that makes an unscoped
domain query fail at import time rather than at runtime". Taken literally that is
one mechanism, and one mechanism cannot deliver it — import-time analysis cannot
see a predicate assembled at runtime, and runtime interception cannot fail at
import time.

The stakes set the bar. A cross-tenant read is a data breach, not a bug. It is
also the *easiest* mistake to make in this codebase: `select(Complaint)` is
correct-looking, correct-compiling, and wrong.

## Decision

Three layers, each covering the previous one's blind spot, and each named
honestly for what it cannot do.

**1 · Static — `scripts/check_tenant_scoping.py`, in CI**
Walks the AST of every domain module. Fails the build on a `select`/`update`/
`delete` over a tenant-scoped model with no `tenant_id` *comparison*, and on raw
`text()` SQL naming a scoped table. Standard-library only, so it runs without the
stack — a check that needs a database to say the code is wrong gets skipped on
the day it matters.
*Blind spot:* predicates built at runtime.

**2 · Runtime — `nemesis.tenancy.guard`, on the engine**
A `before_execute` interceptor that inspects the compiled statement and raises
`CrossTenantQueryError` before the database is touched. Sees the statement
actually about to run, however it was built. Installed in the **test** engine too,
so the suite runs under the same restriction production does.
*Blind spot:* raw `text()` SQL.

**3 · Database — Postgres Row-Level Security, Phase 25**
The only layer that also binds a human with a `psql` session. Explicitly
scheduled rather than implied, because §18.3 currently lists it as a gap.

Two supporting decisions:

- **The scoped-table set is derived, never listed.** The runtime registry reads
  `Base.metadata` for a `tenant_id` column; the static check reads the model
  source for `TenantScopedMixin`. A hand-maintained list is a second thing to
  remember, and the failure mode of forgetting is that a new table is silently
  exempt from every check.
- **Exemptions are explicit and auditable.** Cross-tenant work is legitimate —
  the integrity sweep, partition maintenance — so it declares itself with
  `execution_options(nemesis_tenant_scope_exempt=True)` and a
  `# tenant-scope-exempt:` comment the static check requires. An implicit bypass
  would be neither reviewable nor greppable.

## Alternatives considered

**Auto-inject the predicate** from the tenant `ContextVar`. Rejected, and it is
the tempting one: it is friendlier and it is much worse. It hides the bug, so the
developer never learns the query was wrong, and the same omission ships in the
next code path where auto-scoping does not apply — a raw query, a migration, a
support script. Failing loudly teaches; silently correcting does not.

**RLS alone, from Phase 2.** Rejected on sequencing: RLS needs per-tenant
database roles or a session variable set on every checkout, which couples
connection pooling to tenancy before there is an identity layer (Phase 13) to
decide what a tenant session even is. Doing it now would be rework.

**Rely on code review.** Rejected. Every cross-tenant leak in every multi-tenant
product was reviewed by somebody.

## Consequences

- A legitimately cross-tenant query is more work to write, which is the intent.
- The runtime guard adds a per-statement AST traversal. Measured as noise against
  query execution; it does not touch the network.
- The static check's "filtering position" rule matters and was itself a defect:
  the first version accepted any mention of `tenant_id`, so
  `select(Complaint.tenant_id, Complaint.id)` passed while returning every
  tenant's rows. The two layers must agree on what counts as scoping, or the
  weaker one silently defines the policy.
- `text()` SQL is a known hole until Phase 25. Saying so plainly is better than
  implying a guarantee that stops at the ORM boundary.

## Revisit when

- Phase 25 lands RLS, at which point this ADR should be amended to describe the
  three layers as shipped rather than two-shipped-one-scheduled.
- Phase 13 introduces impersonation, which needs nested tenant scopes to unwind
  correctly — already supported by `tenant_scope`, but it becomes load-bearing.
