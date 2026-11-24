# 0066 — A session is read on every request, and the shared token is deprecated

- **Status:** Accepted
- **Date:** 2026-11-24
- **Owner:** SEC · PLT
- **Blueprint:** §18.2, §18.3, §25; ADR-0009, ADR-0020, ADR-0062, ADR-0063
- **Taken at:** Phase 13 (identity, authorization & org modelling)
- **Retires:** ADR-0062 (the console's bridge session), and ADR-0020's token —
  on the schedule below, not on this commit

## Context

Two decisions in one ADR because they are the same trade seen from two sides:
what it costs to be able to **stop** access, and how a deployment gets from a
shared secret to real identity without a flag day.

**Half one: revocation.** A JWT verified by signature alone cannot be revoked
before it expires. "Refresh rotation, revocation" is a Phase 13 ship line, and
the options were:

1. A short TTL and hope. "Revoked" then means "within ten minutes", which is not
   what an administrator pressing the button believes it means.
2. A Redis denylist. A second datastore in the authentication path, and an
   outage that must choose between failing open (no revocation) and locking
   everybody out.
3. **One indexed primary-key read per request**, against `user_sessions`.

**Half two: the shared control-plane token.** Phase 5 guarded every
control-plane write with `X-Control-Plane-Token` and said in its own docstring
that Phase 13 would replace it with a real operator identity. ADR-0062 then
built a console bridge — a shared passphrase and a signed cookie — explicitly as
"the smallest construction that closes the exposure and survives being
replaced". Both now have a replacement available. Removing them in the same
commit that introduces identity would leave every running deployment, and the
console BFF that holds that token server-side, unable to configure anything
until somebody had run the bootstrap CLI.

## Decision

**The access token carries a session id, and that session row is read on every
authenticated request.**

The request is already opening a database connection and already resolving a
tenant (`api.deps.require_tenant` has done a tenant lookup per request since
Phase 3). `sessions.resolve` is one statement joining `user_sessions` to
`users`, on indexed primary keys, and it buys:

- revocation that takes effect on the **next request**, not within the TTL;
- deactivation that ends a shift immediately;
- a suspended tenant whose officers stop working at once rather than at expiry;
- an impersonation cap (`impersonation_sessions.ends_at`) that is enforced
  rather than merely recorded — the `imp` claim is checked against the row.

The access TTL is then free to be fifteen minutes for ergonomics rather than for
containment, and the refresh TTL a fortnight for the field app.

**The shared token is deprecated, not removed.**
`api.identity_deps.requires_or_control_plane_token` accepts **either** a session
carrying the declared permission **or** the Phase 5 token. The session path is
tried first, so a migrated deployment never takes the deprecated branch. The
token path logs `control_plane_token.deprecated` at **warning** on every use,
which is how a deployment discovers it has not migrated, and
`check_endpoint_permissions.py` prints the count of routes still accepting it so
the number cannot quietly grow.

The removal schedule, in order:

1. **This release.** Both work. Every route declares a permission. The review
   queue — the one operator surface carrying citizen data — is session-only and
   scoped; there is no token path to it any more.
2. **Next release.** The console BFF signs in as an operator instead of
   attaching the token; ADR-0062's passphrase and cookie are deleted and that
   ADR moves to Superseded. The remaining control-plane *reads* that are
   currently `public()` become `requires(...)` at the same time — they keep the
   Phase 4–7 posture in this release deliberately, because tightening a dozen
   published reads in the same commit as an identity system is an undeclared
   contract change wearing a security improvement.
3. **The release after.** `requires_or_control_plane_token` is deleted,
   `Settings.control_plane_token` goes, and `deployment.py` drops it from the
   contract.

## Consequences

**The database is now in the authentication path**, and that is the cost. A
Postgres outage means nobody can sign in *or* act — where a stateless check
would have let existing sessions keep working against a degraded system. We
accept it: every endpoint behind that check needs the database anyway, so the
set of requests that would have survived is close to empty.

**It is one more read on the hottest path.** `last_seen_at` is therefore written
at most once a minute per session (a conditional `UPDATE`, not a read-then-write
that two concurrent requests both win), and `requires()` takes the principal as
a FastAPI sub-dependency so the token verify, the session read and the grant load
happen **once** per request even when a handler also declares `PrincipalDep`.
Resolving twice would also have been a correctness bug: two loads can return
different grants if a revocation lands between them.

**A deployment running on the token has no attribution.** The dual-path guard
returns `Authorized(principal=None)` and handlers record
`"control-plane token (deprecated)"` rather than inventing an actor id — a name
on an action nobody can be held to is worse than an honest blank, and it is also
the line that makes the migration visible in the audit trail.

**Until step 2 lands, the console is still gated by ADR-0062's passphrase.** The
backend no longer needs it, and the frontend has not changed in this phase.
`docs/PHASES.md` carries that as Phase 13's open clause rather than leaving a
reader to infer it from two ADRs that both sound finished.
