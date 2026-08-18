# 0022 — A published API version is locked by a contract file, and v2 ships to prove it

- **Status:** Accepted
- **Date:** 2026-08-17
- **Owner:** PLT
- **Blueprint:** §16.3, §26.4
- **Related:** ADR-0013 (canonical JSON), the Phase 2 event schema lock

## Context

Critique-log defect #12: §16.3 promises journalists and civil society a durable
public interface, and the previous plan then broke it silently on the next
deploy. `docs/RELEASE.md` fixed the *policy* — twelve months' notice before a
public API version is removed — and a policy nobody can execute is prose.

Phase 2 faced the same class of problem for the event log and solved it with
`schema_lock.json`: a fingerprint of every released payload shape, checked in
CI, so editing a shipped model fails the build rather than silently invalidating
every hash already written. The outward contract needs the same mechanism, and
review is not it. "Does this change break a consumer?" is a question a reviewer
answers correctly most of the time, and the failure mode of the remaining cases
is a newsroom's integration breaking on a Tuesday.

There is a second, quieter failure available: over-correcting. A rule that every
response change requires a version bump produces a v7 nobody has migrated to and
a v1 everybody is still on, which is worse for compatibility than having no
versions at all.

## Decision

**`nemesis/api/api_contract_lock.json` fingerprints every operation of every
non-preview version, and CI compares it against the shape the running code
actually serves.**

Breaking — a consumer that worked yesterday fails today:

- a response field removed, renamed, or type-changed
- a required response field made optional
- a path removed
- a request parameter added as required

Not breaking — a consumer that ignores what it does not know keeps working:

- a response field added
- a new path, or a new optional parameter
- documentation and summaries

**`PREVIEW` versions are exempt**, read from the version registry rather than
from a list in the checker — so promoting a version to active brings it under
the lock automatically.

**And v2 ships, with a genuinely breaking reshape.** The Phase 4 gate is that a
v1 consumer keeps working after v2 ships, and that cannot be proven against a v2
which does not exist: a test asserting v1 still works while nothing changed
asserts nothing.

## Consequences

**The lock compares against generated output, never a committed spec.** The
checker constructs the app and reads its OpenAPI document. A committed
`openapi.json` would be a third artefact that drifts, and the one it drifts away
from is the one consumers receive.

**`--update` is a separate, deliberate action.** A check that rewrote its own
lock on failure would enforce nothing. Re-locking is a commit somebody has to
justify, which is the same standard `schema_lock.json` sets.

**The checker lives in `nemesis/api/`, not in `scripts/`.** It has to construct
the app, which needs the full dependency set, and the api container mounts only
`./backend` — a root-level script could never import it. Same split the event
schema fingerprint check already uses.

**The tests verify the checker, not only the lock.** Nine cases assert that
removals, renames, type changes, and newly-required parameters are detected, and
that additive changes are not. A lock check nobody has watched fail is one that
might be comparing two empty dicts.

**v2 exists as a maintenance obligation, and that is accepted.** It shares the
query layer with v1 (`public.aggregates`) and differs only in presentation, so
the arithmetic cannot diverge between them — which is the failure a reader
comparing two URLs would find before we did.

**A version past its sunset answers 410, computed from the date rather than from
the status field.** A deployment nobody has updated still stops serving on
schedule: the promise made to consumers was a date, not a promise that somebody
would remember it. 410 rather than 404 because "gone" sends an integrator to the
changelog and "not found" sends them to re-read their own URL construction.

**The registry refuses a deprecation shorter than the published notice, at
import time.** The mistake this prevents is the realistic one — somebody
deprecating v1 with a three-month sunset because a v2 is ready and twelve months
feels theoretical. It is not theoretical to the newsroom that integrated last
year.

## Alternatives rejected

**Consumer-driven contract testing (Pact or similar).** The right long-term
answer once there are real consumers to drive the contracts. Today there are
none, so the "consumer" would be a fixture we wrote — which tests our own
assumptions about what consumers read, in a heavier framework.

**Semantic diffing of the OpenAPI document by an off-the-shelf tool.** Several
exist and most classify changes correctly. None of them can be taught that
`suppressed` becoming optional is a disclosure decision rather than a schema
relaxation, and the failure messages name JSON pointers rather than the harm.

**Versioning every router in lockstep.** Bumping the control plane and the
ingest endpoints to v2 because the public surface changed would force a
migration on consumers whose contract is identical — a version number pretending
to be a release.
