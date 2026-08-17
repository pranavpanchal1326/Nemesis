# 0020 — Control-plane writes carry a shared token, and every one of them writes an event

- **Status:** Accepted
- **Date:** 2026-08-17
- **Owner:** PLT · SEC
- **Blueprint:** §18.2, §25.1
- **Supersedes for this surface:** nothing. Superseded by Phase 13.

## Context

Phase 5 adds an API that creates tenants and redefines what a complaint means.
Phase 13 owns identity, and until it lands there is no token to read an operator
claim from. ADR-0009 faced the same gap for feature flags and answered it by
making mutation CLI-only.

That answer was reconsidered here and rejected, because the two surfaces have
different jobs. A feature flag is pulled by an engineer during an incident, and
"shell into the container" is an acceptable cost for an operation that happens
rarely and is already being performed by someone with production access. The
Phase 5 gate is that **a solutions engineer onboards a customer without opening
an editor** — and an onboarding path that requires a container shell is a worse
version of the thing this phase exists to remove.

Leaving the endpoints open was never a candidate: an unauthenticated caller
could mint tenants, or deactivate a live tenant's entire taxonomy.

## Decision

**Control-plane writes require `X-Control-Plane-Token`; reads do not. Every
write appends an event to the tenant's hash chain.**

- The token is `Settings.control_plane_token`, compared with
  `hmac.compare_digest`. A short-circuiting `==` leaks the shared prefix length
  to a network attacker, and closing that costs one function call.
- `app_env=pilot` refuses to boot while the published development default is
  still set, exactly as it does for the JWT secret. The token is in the
  deployment contract and has a rotation procedure in `docs/SECRETS.md`.
- **Reads are token-free and tenant-scoped.** Reading your own taxonomy is the
  same class of operation as reading your own complaint, and it goes through the
  same `X-Tenant-ID` resolution with the same honest caveat about what that
  header is.

## This is not authentication, and the events are why that is survivable

A shared secret says *someone authorised* did this. It cannot say *who*, and
after an exposure it cannot say what they did. So the second half of this
decision is load-bearing rather than decorative: every control-plane mutation
appends `tenant_provisioned`, `taxonomy_published`, or `organisation_changed` to
the tenant's chain, in the same transaction as the change.

That is what makes a token compromise investigable. Rotating is the first step;
reading the chain is the second, and it answers exactly what was changed and
when — which the token never could, and which a per-operator identity would only
answer in addition to, not instead of.

The events also outlive this ADR. When Phase 13 replaces the token with real
operator identity, the chain gains an `actor_id` that is finally meaningful, and
nothing else about the write path changes.

## Consequences

- One shared secret guards a high-value surface. Rotation is cheap — nothing is
  invalidated except the token, no sessions drop — so there is no reason to
  delay it, which is stated in the runbook because "cheap to rotate" is only
  useful if the person holding the incident knows it.
- The token is a Phase 13 dependency to *delete*, not to extend. It must not
  acquire scopes, per-tenant variants, or an expiry; each of those is a step
  toward reimplementing authorization badly, and Phase 13 is where authorization
  is designed.
- A provisioning request that fails partway rolls back the events with the rows,
  because both are written through the request's single transaction. There is no
  code path from an abandoned onboarding to a chain entry claiming it happened.
