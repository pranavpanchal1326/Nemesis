# 0059 — The product has two front doors, and every surface links back to one

- **Status:** Accepted
- **Date:** 2026-08-26
- **Owner:** PROD
- **Blueprint:** §E14.4, §E17, §E18, §E19, §E21, §E3.3, §E13 Tier D; ADR-0043, ADR-0046, ADR-0056
- **Taken at:** after M12, on a walk through the running product

## Context

Track E closed with every surface in §E14.4 built: the film, the citizen
capture-and-track flow, the public transparency pages, the twelve-screen
console, the developer portal, the field app. Thirteen milestones, eighteen
phases, 554 unit assertions and 234 browser assertions green.

**And the only way to reach any of it was to know the URL.**

A sweep of all twenty-five routes found no crash, no missing string, no
horizontal overflow — and the numbers that mattered were these: `/report` had
**zero** links on it. `/field` had zero. `/developers` was an `<h1>` and nothing
else. The console's thirteen links all pointed inside the console. A resident
who filed a report could not get from the receipt to the ward page that explains
what the figures mean; an officer could not get from the console to the field
app without typing an address; somebody who landed on a ward page from a search
engine — which §16.3 explicitly wants to happen — had reached a dead end.

Every surface was finished. The product was not, because a product is also how
somebody gets from one part of it to the next, and nothing in eighteen phases
was scoped to own the connections *between* route groups. §E14.4 describes the
groups and their postures; it does not say who links them, and so nobody did.

This is a plainer failure than the ones M12 went looking for. It needed no
audit — it needed somebody to open the thing and try to use it.

## Decision

**Two front doors, one per audience the blueprint already separates, and every
surface links back to the door it belongs to.**

- `/citizen` — §E17 and §E18. Report a problem, follow a report you filed, the
  city's published places, the honesty table.
- `/staff` — §E19 and §E21. The console's five sections and the field app.

Both are server-rendered, have no client bundle, and are built from
`<PortalHome>` — one component, because two would be two chances for the staff
door to quietly become the better-designed one, and the door that would lose
that race is the one a resident sees first.

**The staff door is generated from `console/screens.ts`.** That registry already
feeds the navigation rail, the `⌘K` palette, the route guard and the §E24 chip,
and its own docstring gives the argument: *"a hand-written rail and a `⌘K`
palette are two lists of the same screens, and the day they disagree is the day
an officer cannot reach something the palette says exists."* A hand-written door
would be the fifth list to disagree. Every card carries the same chip its screen
carries, so the staff door is also the shortest honest answer to *what is
actually wired* — four real screens, and nine naming the phase that populates
them.

**The citizen door is written out, because there is no registry to read**, and
it offers nothing it cannot honour: without a published slug the two public
cards are **absent** rather than pointed at a guess (§E3.3).

**The receipt field is a form, not a list.** A complaint id is a capability
(ADR-0043) and `/t/{id}` is `noindex` for that reason, so a door that listed
reports would be a door that knows which reports exist — the one thing this
design refuses to know. The resident brings the id. It validates the shape here,
so a typo is answered by the field that produced it rather than by an upstream
404, and it submits through a Server Action so it works at §E13's Tier D, with
JavaScript switched off.

**The links back** are one line each, in chrome that already existed: the
console's masthead wordmark, the field app's title, the citizen surface above
the viewfinder, the public footer beside the honesty link, and a two-item nav on
the landing beside the skip link — first in the tab order, ahead of nine acts of
scroll, for the same reason the skip link is.

## Consequences

**The film keeps its argument and loses its dead end.** §E16's landing is a film
and not a menu, and that stands: the two doors are chrome in the corner
opposite the unmute, fixed, not on the reel, and not a beat. Nine golden images
change, and they were regenerated deliberately rather than tolerated.

**A sixth route group.** `(portal)` carries no shell of its own — a door is not
a posture, it is the choice of one — so the layout does what the other five do:
negotiate the locale on the server, put `lang` and `dir` on one element (A11),
name the surface. Both doors ground on paper, including the staff one: §E9.3's
dark ground is a working condition, and an officer at a door has not started a
shift yet.

**`NEMESIS_STORY_TENANT` is now read through `publishedTenant()`.** The citizen
door needs the *published slug* and the first draft used `resolveTenant()`,
which is the tenant **id** — an opaque UUID the BFF holds as a trust boundary
(ADR-0040) and which is never in an address a person reads. The result linked
residents at `/{uuid}`, a page that answered, in the sense that it rendered
*"this city does not publish"* about a city that does. Three surfaces ask that
question now; they ask it in one function, so the variable's story-era name is a
rename away instead of a grep away.

**What this does not do.** It adds no capability and no data: every destination
existed, every chip is the one its screen already carried, and no §E28 row moves
because nothing new is claimed. §E27's rule that a visual element maps to a
pipeline event is unaffected — navigation is chrome, and
`check_surface_traceability.py` still passes untouched.

**The gates.** `tests/portal.test.ts` asserts the staff door carries every
console screen, at the same href, with the same chip, and that every card on
both doors has a label and a hint in the bundles it loads.
`tests/portal.spec.ts` drives it: every card answers, the doors reach each
other, the receipt field refuses a typo and hands off a well-formed id, and each
of the five surfaces links to a door. The last of those is the one that would
have caught this in the first place.
