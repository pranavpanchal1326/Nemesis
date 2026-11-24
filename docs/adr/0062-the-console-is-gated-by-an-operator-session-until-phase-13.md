# 0062 — The console is gated by an operator session until Phase 13

- **Status:** Accepted
- **Date:** 2026-11-24
- **Owner:** PROD · SEC
- **Blueprint:** §E14.1, §E19, §25; ADR-0020, ADR-0040, ADR-0043
- **Taken at:** the audit in `docs/reports/senior-audit-2026-11-24.md`, finding F5
- **Retired by:** Phase 13 (identity, authorization & org modelling)

## Context

**`/console` had no authentication of any kind.** No `middleware.ts`, no
session, no login, no role check. `app/(console)/console/page.tsx` guarded
nothing; the only guard anywhere in the console was `devOnly()`, which is a
build-mode check and not an authorization one.

That alone would be an access-control gap. What made it worse is the shape of
the seam in front of it, which is otherwise one of the better pieces of design
in this repository. `server/upstream.ts` holds `X-Control-Plane-Token`
server-side and attaches it per call through `controlPlaneHeaders()`, so the
browser cannot send it and could not steal it — `import "server-only"` makes a
client bundle that reaches for it a build error. The token is well protected.

But with no session in front, **the BFF signed every anonymous visitor's write
with the privileged token.** Six route handlers call `controlPlaneHeaders()`:

| Route | What an anonymous caller could do |
|---|---|
| `POST /api/review/queue/{itemId}/decision` | record the one permitted judgement on a citizen's report, forever |
| `POST /api/policy/{kind}/{revision}/activate` | make a policy revision live |
| `POST /api/policy/tuning/dedup` | change the dedup bands |
| `POST /api/control/taxonomy` | redefine what a complaint *is* |
| `POST /api/control/tenants` | provision a tenant |
| `PUT /api/control/tenants/{slug}/publication` | change what a city publishes about itself |

The review queue also renders the redacted photograph and the evidence behind
each stopped gate, so the read side was a disclosure surface too (§25).

**The backend's half was never the hole.** `control_plane.py` demands the token
and answers `403` without it — and `test_control_plane_api.py` records *why* it
is 403 and not 401: *"there is no authentication scheme to challenge with"*.
`deployment.py` and `docs/SECRETS.md` both say plainly that a deployment keeping
the repository default has no authentication at all. The backend disclosed its
situation accurately. The frontend was the component that took that shared
secret and lent its authority to the public.

**Nothing disclosed the console half.** No README line, no
`CONNECTING-A-BACKEND.md` line, no honesty-table row said *this console must not
be exposed to a network*. §E28 marks console screens `REAL`, the README invites
`nem web`, and a reader was never told. For a product whose entire premise is
labelling its own claims, that was the gap worth closing first.

## Decision

**An operator session, verified on the server, is required to render any console
screen and to attach the control-plane token to any request.**

Deliberately **not** an identity system. There are no users here yet — Phase 13
owns that, `HANDOVER.md` calls it the longest-lead blocker in the repository,
and Track D plus the rest of the frontend sit behind it. A half-built identity
model now would have to be torn out when the real one lands, which is a worse
outcome than a bridge that says what it is.

So this is the smallest construction that closes the exposure and survives being
replaced:

1. **A shared operator passphrase**, `NEMESIS_CONSOLE_PASSPHRASE`, compared in
   constant time. The same category of secret as `NEMESIS_CONTROL_PLANE_TOKEN`
   (ADR-0020) and governed by the same rotation procedure, so this introduces no
   new *kind* of secret — only a second one of a kind already in use.
2. **A signed session cookie**, not a bearer of the passphrase. HMAC-SHA256 over
   a payload carrying only issue and expiry times, keyed by
   `NEMESIS_CONSOLE_SECRET`, `HttpOnly`, `SameSite=Strict`, `Secure` outside
   local, verified with `timingSafeEqual`. The cookie proves *a session was
   granted*; it carries no identity, because there is none to carry and a field
   named `user` would be a lie that outlived this ADR.
3. **The gate lives in two places, and only two.** `(console)/layout.tsx`
   redirects an unauthenticated request to `/sign-in`, and
   `controlPlaneHeaders()` **refuses to return the token** without a valid
   session.

The second one is the load-bearing half, and the asymmetry is the point.

### Why the refusal belongs inside `controlPlaneHeaders()`

The obvious implementation is a check at the top of each of the six handlers.
That is six places to be right and one place to forget, and the seventh handler
— written in six months by someone reading the other six — is the one that
ships open.

Putting the requirement inside the function that *produces the secret* inverts
that. The token cannot be attached to a request that has no session, because
the only code path that can produce the header asks for the session first. A new
privileged route gets the protection by using the same helper every existing one
uses, and a route that tries to bypass it has to reimplement the header by hand
— which `check-guards.ts` can see, and a reviewer certainly can.

This is the same argument `projections/writer.py` makes about being the only
module that writes current-state tables: put the invariant where the capability
lives, not in every caller.

### Why not Next middleware

A `middleware.ts` matching `/console/:path*` would be one line and is the
conventional answer. It is rejected here for the reason ADR-0040 already gives
about the tenant header: middleware applies to *every* request it matches, which
makes the set of protected routes a regex in a file far from the thing being
protected. A layout gate is read by whoever opens the console's layout, and the
`controlPlaneHeaders()` refusal is read by whoever writes a privileged route —
both at the site where the mistake would otherwise be made. Middleware also runs
in a separate runtime where the HMAC verification would need a second
implementation.

### Fail closed, and no development-only hole

If `NEMESIS_CONSOLE_PASSPHRASE` or `NEMESIS_CONSOLE_SECRET` is missing, the
console does not render and the token is not attached — **in every environment,
including local.** There is deliberately no "open when `NODE_ENV` is not
production" branch, because that branch is how this finding returns: the
developer path and the deployed path stop being the same path, and the one
nobody exercises is the one that is wrong.

The cost is that a fresh checkout must set two variables before `/console`
works. `frontend/.env.example` carries both with local development values, and
the repository already tells people to copy that file whole rather than the
lines somebody quoted (`HANDOVER.md` §5, which documents this exact mistake
being made with `NEMESIS_CONTROL_PLANE_TOKEN`).

## Consequences

**What this closes.** An anonymous visitor can no longer reach a console screen,
read the review queue, or cause the BFF to sign a control-plane write. The six
writes above now require a session before any upstream call is made.

**What it does not close, stated plainly so nobody reads more into it:**

- **It is one shared passphrase.** Everybody who signs in is "an operator".
  There is no per-person identity, no audit trail of *who* decided, no roles and
  no least privilege. The `review_decided` event records a decision with no
  actor, and that remains true until Phase 13. A console protected this way is
  appropriate for a pilot with a small named team; it is not appropriate for a
  deployment where "who approved this" has to be answerable — which is the
  question a municipal accountability product will eventually be asked about
  itself.
- **It protects the BFF, not the API.** Anyone holding
  `NEMESIS_CONTROL_PLANE_TOKEN` can still call the backend directly; that is
  ADR-0020's model and unchanged here.
- **`/field` is still ungated.** It writes job capture and closure and is the
  same class of exposure. It is excluded from this ADR on purpose: the field app
  is offline-first with a durable outbox keyed by idempotency key (ADR-0056),
  and a session that expires mid-shift must not strand queued work. That needs
  its own decision about offline session lifetime, and guessing at it here would
  put a plausible answer in front of an unexamined question.
- **No rate limiting on the sign-in route.** The passphrase is high-entropy by
  configuration rather than by enforcement, and `ADR-0017`'s limiter is an
  upstream concern. A brute-force control belongs with Phase 13's real
  authentication, not bolted to a bridge.

**What Phase 13 changes.** `readOperatorSession()` is replaced by a verified
session carrying real claims, and `controlPlaneHeaders()` becomes a per-request
authorization check rather than a presence check. The two call sites do not move.
That is the same property ADR-0040 gave `resolveTenant()`, and for the same
reason: name the seam now so the eventual change is a function body rather than
an archaeology exercise.

**The honesty table owes a row.** §E28 marks the console screens `REAL` and says
nothing about who may open them. Until it does, this ADR is the only place the
limitation is written down, which is precisely the drift §E28 exists to prevent.
