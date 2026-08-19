# ADR-0028 — The activation guardrail is a row, not a call

**Status:** Accepted
**Date:** 2026-08-19
**Phase:** 7 — Configuration simulation & backtesting
**Owning function:** DATA

## Context

Phase 7's exit gate contains the sentence *a policy that regresses the labelled
evaluation set cannot be activated*. "Cannot" is not "should not". It has to
hold at `policy.service.activate`, which Phase 6 established as the single
mutation path — there is exactly one function that can move a document to
`active`, and that is the property the whole lifecycle rests on.

The obvious implementation is for `activate` to call a checker in the simulation
package: `if not await simulation.is_certified(...): raise`. It reads well and
it is wrong in two independent ways.

**It inverts the dependency.** `nemesis.simulation` imports `nemesis.policy` —
it reads documents, resolves bundles, and drafts revisions through the policy
service. A call in the other direction closes the loop, and a cycle between two
packages this size surfaces as a partial-initialisation error three modules
away, at import time, for reasons nobody can trace.

**It fails open.** The guarantee would depend on the call still being there.
Deleting it, wrapping it in a feature flag, or moving it inside a branch that
some code path skips all produce the same outcome: every activation succeeds,
exactly as it did before the guardrail existed, and *nothing looks different*.
A control whose absence is invisible is a control that will eventually be
absent.

## Decision

**The evidence is data.** `nemesis.simulation` writes a row to
`policy_certificates`; `policy.service.activate` reads `evaluation_sets` and
`policy_certificates` directly and imports nothing from the simulation package.

Concretely:

- A tenant gates a policy kind by **publishing** an evaluation set for it. There
  is no `require_certification` flag — publication *is* the switch, so there is
  no second source of truth to fall out of step with the first.
- `activate` asks one question: *is there a published set for this kind?* If not,
  it behaves exactly as it did in Phase 6. If so, it requires a certificate with
  `verdict = 'pass'` over the candidate's **content hash**, issued against that
  set, carrying that set's `labels_hash`.
- The certificate is keyed by content hash rather than revision number, because a
  hash cannot be wrong about which bytes were tested.
- A test parses the AST of every module in `nemesis/policy` and asserts that none
  of them imports `nemesis.simulation`.

One exemption exists and it is not reachable over HTTP: `rollback` activates with
a `certification_waiver`, because the content it restores was previously live and
therefore already passed whatever gate existed when it was activated. Taking the
waiver appends `policy_certification_waived` to the tenant chain.

## Consequences

**Good**

- The guardrail cannot be disabled by editing the policy package, and an auditor
  with a `psql` session can answer "was this candidate checked" without reading
  any Python.
- "Which activations bypassed the evaluation set, and on what grounds" is a
  query over one event type rather than an inference from a free-text field.
- Publishing a set is a single, visible act with a single, visible effect. So is
  retiring one, which is why retiring emits an event too — removing the control
  that stops a bad rubric reaching production is at least as consequential as
  changing the rubric.

**Costs, accepted**

- `policy.service` now imports `db.models.simulation`. That is a models-layer
  dependency, not a service one, and the dependency graph stays acyclic — but it
  does mean the policy package knows the *shape* of Phase 7's tables. The
  alternative was a hook registry, which reintroduces exactly the fail-open
  problem this ADR exists to avoid.
- Two extra queries on every activation of a gated kind. Activation is a
  reviewed, approved, human-initiated act measured in minutes; two indexed reads
  are not the cost that matters.
- A tenant that publishes a set and never runs an evaluation has blocked its own
  activations. That is the control working, and the refusal names the set and
  tells the operator to run one.

## Alternatives considered

**A hook registry in `policy.service`, populated by `simulation` at import.**
Rejected: the guarantee then depends on the import happening, and a missing
import fails open and silently. It is the fail-open problem with an extra layer.

**A `requires_certification` column on the policy kind.** Rejected: a second
source of truth about the same fact. Retire a set without clearing the flag and
activations are refused with no exam to pass; set the flag without publishing a
set and the screen says "gated" while nothing is.

**Enforcing at the HTTP layer instead.** Rejected outright. The service is called
by the provisioner, by rollback, by the tuning path, and by tests; a check at one
of those doors is not a check.

## References

- `nemesis/db/models/simulation.py` — the tables and their constraints
- `nemesis/policy/service.py` — `_require_certification`, `ROLLBACK_WAIVER`
- `nemesis/simulation/evaluation.py` — issuing the certificate
- ADR-0026 — rollback moves forward, which is why the waiver is safe
- `docs/runbooks/policy-certification-blocked.md`
