# 0067 — Impersonation is read-only, and lands in the tenant's own chain

- **Status:** Accepted
- **Date:** 2026-11-24
- **Owner:** SEC · BIZ
- **Blueprint:** §9.3, §17.7, §22, §25, §27; ADR-0016, ADR-0066
- **Taken at:** Phase 13 (identity, authorization & org modelling)

## Context

Critique-log defect #15 is that there was no support tooling, so "every customer
question becomes an engineer running SQL against production". The fix is for a
support engineer to be able to see what the customer sees — which is a
capability that reads a citizen's redacted photograph, their complaint text, and
the evidence behind a stopped safety gate, on an account that is not the support
engineer's.

§17.7 asks "who audits the system itself", and this is the sharpest version of
that question: the vendor can read the customer's data, and the customer has no
way to know unless we build one.

The Phase 13 gate clause is therefore *"every impersonation session is visible
to the impersonated tenant"*, and the obvious implementations do not meet it. A
row in an internal support table is visible to us. A line in our application log
is visible to us. An email to the customer is visible to them and unverifiable.

## Decision

**Three mechanisms, deliberately overlapping, because each fails in a way the
others cover.**

1. **A row scoped to the impersonated tenant.** `impersonation_sessions.tenant_id`
   is the **customer's**, not the operator's, and it is readable through
   `impersonation:read` — a *tenant* permission that no platform role holds.
   Support cannot read it; the customer can, with the justification attached.
2. **An event in the customer's own hash chain.** `impersonation_started` and
   `impersonation_ended` are appended with the tenant as the entity. The row
   could be deleted by somebody with database access; the chain is hash-linked,
   so a removal is detectable by the integrity sweep that already runs (§9.3).
3. **Every decision the session makes, in the decision log**, carrying
   `operator_subject`. The row says access was granted; this says what was done
   with it. `impersonation_sessions.decisions_allowed/denied` are maintained from
   the same path, so the customer's view can say "read 14 things, changed
   nothing" without a join.

**Read-only by default.** `allows_writes` is false unless the request asked for
it, and `identity.scopes.evaluate` refuses a write on a read-only impersonation
*before* the scope is considered — even where the impersonated user could
perform it. Most support work is looking at what the customer is looking at; a
session that can also act is a different request with a different justification.

**Capped at start and not extendable.** `ends_at` is written once, two hours
maximum, thirty minutes by default. A longer session is a *new* session with a
new justification. An extendable window is an unbounded window with extra steps,
and the extension is exactly the step that gets skipped at 03:00. The refresh
token's lifetime is clamped to the remaining window, so a thirty-minute session
cannot hand out a fortnight's refresh.

**A justification of at least thirty characters, enforced in the database as
well as the service.** "debugging" is non-empty and answers nothing; the person
this field is written for is a customer reading the row six months later.

**The customer can end a session they did not ask for.** `DELETE
/access/impersonation/{id}` accepts either the operator inside it or a tenant
principal holding `impersonation:read`, and ending it revokes every session
minted under it — a refresh carries the impersonation forward, so ending it has
to reach the whole lineage.

## Consequences

**Support cannot work while a customer's tenant is suspended**, because
`require_tenant` resolves only active tenants. That is the right way round: an
offboarded customer's data should not be readable by us either.

**The platform tenant cannot be impersonated.** Support engineers may not become
each other, so there is no path from one operator's session to another's
authority.

**A support engineer with `impersonation:start` may support any customer**, and
the control on that is the audit rather than a grant per tenant. A per-tenant
grant was considered and rejected: it would mean a provisioning step on every
onboarding that nobody would remember, and an on-call engineer at 03:00 blocked
by a missing row. The honest statement is that the capability is broad and every
use of it is attributable, which is what makes it reviewable.

**The gap, stated because the gate is not fully met:** no *screen* renders any of
this yet. A tenant administrator can call the endpoint; an officer whose session
is being driven by support is **not told so by the product they are looking at**,
because the banner is a Track E surface and no console screen owns it.
`scripts/check_surface_traceability.py` records both events as unsurfaced with
this reason, and `docs/PHASES.md` carries it as Phase 13's open clause rather
than letting an API read pass for a user-visible disclosure.
