# 0040 — The browser talks to a BFF; the WebSocket connects directly

- **Status:** Accepted
- **Date:** 2026-08-24
- **Owner:** PROD · PLT
- **Blueprint:** §18.4, §26.1, §26.3 · §E14

## Context

`backend/nemesis/api/deps.py` resolves the tenant from an `X-Tenant-ID` header
and says plainly what that is: a name, not a proof. Phase 13 owns identity, and
until it lands there is no token to read a tenant claim from. The module's own
docstring commits to the migration being one function body — resolve from
verified claims instead of from a header — with every downstream route
unaffected.

That promise holds only if the *client* does not also encode the header. A
browser calling FastAPI directly must put `X-Tenant-ID` in client code, which
does three things: it ships a trust boundary that is not one, it teaches every
future contributor a shape Phase 13 deletes, and it moves the migration from one
server module to every fetch call in the application.

The counter-argument is real. A BFF is a hop of latency on every read and a
process to operate, and for a local-only deployment "just call the API" is the
cheaper thing that works today.

## Decision

**All browser-to-API HTTP traffic goes through Next.js route handlers.** The
server holds `X-Tenant-ID` today and the bearer token after Phase 13. No
application code running in the browser constructs a tenant header.

**The WebSocket connects directly.** `/ws/pipeline-events` is not proxied.

## Alternatives considered

**Direct browser-to-FastAPI for everything.** Rejected. It puts a
client-controlled tenant header in shipped code, which is the fake trust boundary
`deps.py` went out of its way to avoid inventing. It also forfeits server-side
rendering for the public transparency pages, which §16.2 wants bookmarkable by
journalists and RTI applicants and therefore indexable, and it leaves locale
negotiation and response caching with no natural home.

**Proxy the WebSocket through the BFF too, for consistency.** Rejected. The
socket is unauthenticated and one-directional **by construction** — ADR-0016
makes realtime payloads default-deny, and `realtime.py` discards anything the
client sends because accepting commands over that socket would be a control
surface Phase 13 cannot yet protect. Proxying adds a hop with no security benefit
it does not already have, doubles the connection count, and interposes our code in
the hub's backpressure path, which is the one part of that subsystem specifically
built and tested to shed slow clients on schedule. Consistency is not worth
degrading a mechanism that already works.

**Wait for Phase 13 and build the client directly against the API in the
meantime.** Rejected as the most expensive ordering. It writes the client twice
and guarantees that the second write happens under deadline.

## Consequences

**Easy:** Phase 13 changes one server module, exactly as `deps.py` promised.
Public pages get SSR. Locale negotiation, response caching, and request-level rate
limiting have somewhere to live. The browser never sees a tenant identifier it
could tamper with.

**Hard:** one hop of latency on every read, and a BFF layer to operate and
observe. Correlation IDs must be propagated through it or the OpenTelemetry trace
breaks at the boundary — the frontend must forward, not originate, the correlation
header.

**Commits us to:** the generated TypeScript client living on the server side of
the seam, and to the rule that a new browser-side fetch to the API is a review
failure rather than a shortcut.

## Revisit when

Phase 13 lands — at which point the seam's *function* changes and this ADR is
updated rather than superseded; or the WebSocket gains a control surface, at which
point it needs authentication and therefore needs the proxy this ADR currently
declines.
