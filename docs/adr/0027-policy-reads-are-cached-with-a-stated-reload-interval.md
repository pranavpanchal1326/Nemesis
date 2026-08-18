# 0027 — Policy reads are cached per process, and the reload interval is published

- **Status:** Accepted
- **Date:** 2026-08-18
- **Owner:** PLT
- **Blueprint:** §13.5, §27.1
- **Related:** ADR-0009 (feature flags), ADR-0006 (configuration as data)

## Context

Phase 6's gate says a policy change "takes effect within one reload interval",
which is a deliberately weaker promise than "immediately", and the weakness is
the decision this ADR records.

Every governed decision in the pipeline reads a policy: the safety check reads a
ruleset, classification reads a taxonomy, severity reads a rubric, dedup reads
thresholds, routing reads rules. Reading each from Postgres per complaint adds
five round trips to the hottest path in the system, on a budget §27.1 states in
seconds end to end. The database would serve it — these are tiny indexed reads —
but it is five round trips bought for nothing, since a policy changes a handful
of times a year and a complaint arrives every few seconds.

Redis was considered as a middle ground and rejected: it moves the round trip
rather than removing it, and adds a second source of truth for something whose
authoritative version already has to be resolvable from Postgres for the audit
query to work.

## Decision

**`policy.resolver` holds an in-process snapshot per (tenant, kind), refreshed
on a TTL of 30 seconds. The interval is documented as the real propagation
latency, stated in the module, and returned in the activation API response.**

- **30 seconds, not the feature flags' 5.** A flag is an emergency handle pulled
  during an outage, where the latency of the switch is part of the incident. A
  policy revision is a reviewed, approved, scheduled change. Both numbers are
  honest about their latency; they are honest about different things.
- **Activation invalidates the local cache** and the response says so
  explicitly: this process serves the new version now, other workers pick it up
  within the interval. That is a courtesy, not the mechanism — but without it an
  operator activates a rubric, immediately re-reads it, sees the old one, and
  concludes the button is broken.
- **The resolver has exactly one read path**, through `service.active_version`,
  which filters on `status = 'active'` *and* on the effective date. There is no
  parameter that widens it, so a caller cannot ask this resolver for a draft
  even by accident — which is what makes the gate clause "an unapproved draft can
  never influence a production decision" structural rather than conventional.
- **`reload_seconds=0` disables caching**, for the tests and for the Phase 7
  backtester. A backtest that read a stale snapshot would report a delta against
  a policy that was not the one it claimed to compare.

## Saying the latency out loud is the actual decision

The tempting alternative is not a different number, it is silence — cache for 30
seconds and describe the feature as "hot reload". Every system that does this
produces the same incident: somebody changes a threshold, watches production not
change, changes it again, changes it a third time, and then goes looking for the
"real" configuration in an environment variable.

So the interval appears in three places on purpose: the resolver's docstring,
the activation response body, and the runbook. An operator who knows the number
waits 30 seconds. An operator who does not know it edits the database.

## Consequences

- **A stale read is possible for up to one interval, per process, and is
  correct.** A complaint scored during that window is scored by the previous
  version *and stamped with it*, because the stamp comes from the same
  `Resolved` object as the body. The record is accurate; it just is not the
  newest policy. This is the property that makes the window survivable — a
  window where the score and the stamp could disagree would not be.
- **The cache is keyed by tenant**, which is the only thing standing between a
  shared worker process and one customer's rubric scoring another's complaints.
  A test asserts a second tenant does not inherit a cached document.
- **Anything that must be instant is not a policy.** It is a feature flag with a
  kill switch, or a code path with a runtime argument. That boundary is stated
  here so the reload interval is not shortened, one incident at a time, until it
  is a database read again.
- **A tenant with no active document falls back to `policy.baselines`**, the same
  objects provisioning seeds, and those decisions are stamped `baseline` rather
  than with a plausible-looking revision number. Every such resolution logs
  `policy_baseline_used`, so "which tenants have not been seeded" is a log query
  rather than an audit.
