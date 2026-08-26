# 0060 — An unreachable API is an answer, not an exception

- **Status:** Accepted
- **Date:** 2026-08-26
- **Owner:** PROD · SRE
- **Blueprint:** §E13, §E14.1, §E3.3; ADR-0040, ADR-0058
- **Taken at:** after M12, driving the console with the stack down

## Context

Every BFF handler in `app/api/` reads `{ data, error, response }` from the
generated client and renders a designed message when `error` is set — *"That
report's history could not be read."*, and so on for twelve routes.

None of them survived the upstream being **unreachable**. `fetch` does not
resolve with a status when there is no server to answer: it rejects. The
rejection escaped the handler, and Next answered `500` with a framework error
page. So a deployment whose API was stopped — a restart, a failed deploy, a
laptop demo where docker had not come up — showed an officer a browser-level
failure inside a console screen, instead of the sentence this product wrote for
exactly that moment.

**This is the same fault F12 found in `loadStrings`**, which caught the control
plane's error *response* and not the thrown connection, so a deployment with the
control plane down rendered a 500 for every non-source locale and rendered
perfectly in English. That one was fixed by removing the call (ADR-0058). This
one cannot be: reading the upstream is the console's whole job.

Found by loading `/console/review` with the API down and reading the network
panel, which is a thing no test in this repository does — every existing gate
either runs against a live stack or stubs the client, and neither shape can see
a transport failure reach the browser.

## Decision

**The seam converts a transport failure into a response, once, in
`server/upstream.ts`.**

An `onError` middleware on the typed client returns a synthetic `503` shaped as
the backend's own problem+json (`nemesis/api/errors.py`), and `upstreamFetch`
— the raw path §26.1's multipart submission needs — does the same in a
`try`/`catch`. Every existing handler's `error !== undefined` branch then
handles an outage with no edit at all, which is the point: twelve handlers that
each had to remember is twelve chances to forget.

`503` rather than `502`: the upstream did not produce a bad response, it
produced none, and that distinction is the first thing an operator reads in a
log.

**Only `TypeError` is converted.** That is what `fetch` rejects with when a
request never completes. Anything else thrown inside the client is this
application's own bug, and it is re-thrown unchanged — a bug disguised as a
well-formed 503 would be rendered by every surface as *"the service is not
answering"*, and a bug that reads as an outage is a bug nobody looks for.

## Consequences

- A stack with the API stopped now renders the designed degradation on every
  surface rather than a framework error page. §E13's ladder is about *designed*
  degradation, and this is the rung nobody had built.
- The conversion is invisible to callers, so no handler, screen or test changed
  shape.
- It is one more thing that is true at the seam rather than at the edges, which
  is the argument ADR-0040 made for having a seam at all.
- **The cost, stated:** a genuine bug that happens to be a `TypeError` inside
  the fetch path is now reported as an outage. The narrowing is deliberate and
  it is not airtight; `instanceof TypeError` is the strongest signal available
  without parsing platform-specific error causes, and pretending otherwise would
  be the kind of confidence §E3.3 rules out.
