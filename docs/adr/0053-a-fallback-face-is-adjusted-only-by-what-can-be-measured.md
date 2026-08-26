# 0053 — A fallback face is adjusted only by what this repository can measure

- **Status:** Accepted
- **Date:** 2026-08-25
- **Owner:** PROD
- **Blueprint:** §E10, §E15, §E23; `docs/FRONTEND-PHASE-PLAN.md` F2, register row A1

## Context

A1 names four descriptors: *"no `size-adjust` / `ascent-override` /
`descent-override` / `line-gap-override` is declared on a fallback
`@font-face`."* F2 ships three of them and deliberately does not ship the
fourth. That is a deviation from a named blueprint line, so it is argued here
rather than left as an omission somebody later reads as an oversight and
"fixes".

The three that ship are **absolute**: each states, as a percentage of the em,
what the *real* face's line box is. `scripts/fetch_fonts.py` reads them out of
the `head`, `hhea` and `OS/2` tables of the woff2 already committed under
`frontend/public/fonts/`, so the numbers are derived from the same bytes the
browser will load, with no network and no second source.

`size-adjust` is **relative**. It is a ratio between the real face's average
advance width and the *fallback's*, and the fallback is whatever face happens
to be installed on the reader's machine. This repository has no way to measure
that face. The standard workaround is a hard-coded table of metrics for Arial,
Georgia, Courier New and the rest, which is what the framework-level
implementations of this technique carry.

Two things make that table the wrong answer here.

1. **It is a number nobody in this repository can check.** Every other
   generated artefact in `frontend/src/design/generated/` is regenerated and
   compared in `nem check` — `tokens:check`, `honesty:check`,
   `api:types:check`, and now `fonts:check` recomputing each override from the
   font file. A hard-coded metric for a face that is not on disk cannot be
   recomputed by anything, which makes it exactly the *"number that silently
   stops matching"* F2's own ship line refuses.
2. **The face it describes is frequently not the face that renders.** The
   development host is Windows and CI is a Linux container. `local("Arial")`
   resolves on one and not the other; `local("Liberation Sans")` the reverse.
   A `size-adjust` computed against Arial and applied to whatever DejaVu the
   container actually picked does not merely fail to help — it scales the text
   *away* from the real face's width, and it does so silently, because the
   descriptor is applied whether or not the face it was computed for was the
   one that matched.

## Decision

**A fallback face declares only the descriptors that hold regardless of which
local face resolves.** In practice that is `ascent-override`,
`descent-override` and `line-gap-override`: each is stated in em units and
each is copied from the real face, so an adjusted fallback occupies the real
face's line box whichever installed face fills it. `size-adjust` is not
declared.

Three consequences, stated rather than discovered:

- **`local()` lists become an availability question, not a metrics question.**
  Each adjusted face names several system candidates across Windows, macOS and
  the Playwright container, and correctness does not depend on which one wins.
  That is why the list can be long.
- **Line-box height is the term that actually moves a page.** A width mismatch
  re-wraps one line; an ascent mismatch moves every line below it. The residual
  the decision accepts — reflow from a differing average width — is the smaller
  of the two, and `tests/type-metrics.spec.ts` measures what is left of it as
  CLS on the citizen route rather than assuming it away.
- **The claim is asserted in the engine, not only in the generator.**
  `fonts:check` proves the CSS agrees with the font tables; the Playwright
  spec proves the browser agrees with the CSS, by measuring the real face and
  its adjusted fallback at `line-height: normal` on the same page and requiring
  them to agree within a pixel at a 100 px em. Both were watched to fail before
  being called gates (F1's rule).

## Alternatives considered

**Ship the hard-coded metrics table.** Rejected above: unverifiable in this
repository, and wrong on the machine where the named face is absent — which is
half the machines this project runs on.

**Ship a metrically compatible face ourselves** — Liberation Sans for Arial,
Gelasio for Georgia — so both sides of the ratio are measurable. Rejected on
weight and on purpose. These faces exist to cover the seconds before the real
face arrives; downloading a second family to cover the download of the first is
a contradiction, and the ten roles would add megabytes to a repository whose
§E23 budget is a 2G profile.

**Measure the resolved fallback at runtime and set the ratio from script.**
Rejected: it moves type metrics into the render path, cannot run before first
paint, and would introduce the layout shift it was written to prevent.

## Consequences

- `scripts/fetch_fonts.py --fallbacks` regenerates ten adjusted faces offline
  from the committed woff2, and `--verify` recomputes and compares them.
- `src/design/tokens.json` lists each adjusted face directly after its real
  family in the `--font-*` stack; `--verify` fails if a stack omits it or
  orders it wrongly, because a generated face nothing references would fail
  silently and look done.
- §E10's own-errors record gains one line: A1 is closed with three of its four
  named descriptors, and this ADR is the reason.
- If a future requirement makes width matching necessary — a print surface,
  most plausibly — the way in is a measurable fallback, not a table.
