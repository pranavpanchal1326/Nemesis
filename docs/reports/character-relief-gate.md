# The character's `relief` clause is unexercised, and this is why

**Phase:** F15 · **Milestone:** M9.6 · **Date:** 2026-08-26
**Gate clause:** *"a real `citizen_confirmed` event moves a character, asserted E2E"*
**Status:** **not taken.** Skipped by name in `frontend/tests/ink.spec.ts`.

This report exists for the same reason
[`story-merge-gate.md`](story-merge-gate.md) exists: a gate clause that cannot
be taken on this checkout is published, not quietly dropped, and not passed by
faking its input.

---

## What the clause asks for

§E8.1 states the character's binding to the log in one sentence:

> Because these are inputs and not timelines, the character **reacts to real
> backend events**. When `citizen_confirmed` arrives on the WebSocket, `relief`
> fires.

F15's gate turns that into an end-to-end assertion: a figure on a real surface
must be seen moving to `confirmed` because that event arrived.

## Why it cannot be taken

**Nothing in this system appends `citizen_confirmed`.**

Everything around the event exists:

| Piece | Where | State |
|---|---|---|
| The event schema | `backend/nemesis/events/catalog.py` — `CitizenConfirmedV1` | Registered, version 1, entity `work_order` |
| The schema lock | `backend/nemesis/events/schema_lock.json` | Locked |
| The projection | `backend/nemesis/projections/handlers.py` | Projects it, including `auto_confirmed` |
| The public aggregate | `backend/nemesis/public/aggregates.py` | Counts auto-confirmed resolutions from it |
| The realtime shaper | `backend/nemesis/realtime/envelope.py` | Shapes it for the wire, carrying `auto_confirmed` |
| The frontend binding | `frontend/src/ink/live-ink.ts` | `citizen_confirmed → relief` |
| **The thing that writes it** | — | **Does not exist** |

A grep for `CitizenConfirmedV1` outside the catalog and the schema lock returns
nothing. There is no route, no worker, no scheduled job and no seeder that
appends it. The door it needs is §E17.5 — *close the loop*, the before/after
slider where a citizen answers **"is it actually fixed?"** — and that is
**ROADMAP (Phase 15)**, unbuilt on the backend and unbuilt on the frontend.

The same is true of its sibling `citizen_disputed`, and of
`citizen_confirmation_requested`.

## What was refused

**Publishing a synthetic envelope from the test.** It would have taken about
four lines: build a `RealtimeEnvelope` with `event_type: "citizen_confirmed"`,
push it through `publishEnvelope`, watch the figure change. Every assertion in
`tests/ink.spec.ts` would then be green.

It was refused because it fails the gate it would appear to pass. The Phase 20
gate's own words are *"a scene that can only be fired by a button fails"*, and a
test-authored envelope is a button with a JSON payload. Worse, this specific
clause is the one that proves the character is not a mascot — faking its input
would leave the only surviving evidence for §E8.1's central claim being a test
that manufactured its own evidence.

**Building the Phase 15 door as part of F15.** F2 set the precedent for
building a missing door when a Track E gate is *unmeetable*: `PUT
/control-plane/tenants/{slug}/locales` was one endpoint over a model that
already existed. This is not that. Citizen confirmation needs a work-order
write path, an SSIM verification result to confirm *against*, a confirmation
window, and the milestone fund release §E17.5 attaches to the "yes" — that is
Phase 14 and Phase 15 of the backend, not a door.

## What *is* asserted instead

The mechanism is exercised end to end with the two other rows of the same
binding table, both of which this deployment genuinely emits:

- `exif_check_completed` → `shutter` — the first thing the pipeline says about a
  report that has actually arrived, and what moves the figure from `report` to
  `wait`.
- `pipeline_stage_degraded` → `disappointed` — §24.2's third outcome, which on
  this checkout is the *common* path (see `story-merge-gate.md`).

`tests/ink.spec.ts` files a report through the film's own `<ReportFlow>`, then
touches nothing, and asserts the figure's `data-ink-transitions` count
increases and its state lands in `wait` or `dejected`. Both arrive over the same
socket, through the same `subscribeToEvents` seam, into the same
`bindMachineToBus` call, and move the same machine.

**What the skip leaves untested is one row of a table, not the mechanism.**

## The three routes that would take it

1. **Build §E17.5 and Phase 15's closure loop.** The intended route. The clause
   is then taken for free — the binding, the figure and the assertion are
   already written, and `CAN_CONFIRM_A_CLOSURE` in `tests/ink.spec.ts` becomes
   `true`.
2. **A backend integration test that appends the event through the real event
   store**, and a frontend E2E that watches the socket deliver it. This does not
   need the UI, only a legitimate writer — and it would still be a writer that
   exists solely for the test, which is the thing this repository keeps
   refusing.
3. **A seeded demo closure in `nem seed-demo`.** Cheapest, and the most
   dangerous: it would put a confirmed closure into a demo tenant's ledger with
   no work order behind it, which is a fabricated record in an append-only log
   that the public surface aggregates. Rejected on §6 Principle #8 grounds
   before it was costed.

## Where this is recorded

- `frontend/tests/ink.spec.ts` — the skip, by name, with the reason on the line.
- `docs/FRONTEND-EXECUTION-PLAN.md` — M9.6's row.
- `docs/FRONTEND-PHASE-PLAN.md` — Stage 4's completion note.
