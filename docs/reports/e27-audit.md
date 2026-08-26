# §E27 audited — the event-to-surface table, executed in both directions

- **Claimed by:** F18 / **M12.2**
- **Gate:** `scripts/check_surface_traceability.py`, wired into `nem check`
- **Written at:** F18, 2026-08-26

---

## What was checked, and against what

§E27 states a rule about itself:

> §6 Principle #9 requires every visual element to map to a real pipeline event.
> This table is the audit. **A visual element not on this list, and not
> classifiable as chrome, is a defect.**

A table that says that and is never executed is a list. `check_surface_traceability.py`
executes it against three sources that cannot be edited together by accident:

| Source | Read as | Answers |
|---|---|---|
| §E27, in `NEMESIS-Frontend-Blueprint.md` | markdown table | what is claimed |
| `backend/nemesis/events/catalog.py` | Python AST — never imported, for the same reason `check_event_catalog.py` never imports it | what exists |
| `frontend/src/**`, excluding `generated/` and excluding comment lines | line by line | what the browser actually reacts to |

Four findings are possible. Three of them fired on the first run, and all three
were in the direction that flatters.

---

## Finding 1 — two registered events appear on no row

`evaluation_set_retired` and `policy_certification_waived` are registered,
shipped, and on no §E27 row. Their own docstrings say what they are for:

> *"which activations bypassed the evaluation set, and on what grounds"*
> — `PolicyCertificationWaivedV1`

> *"Removing the control that stops an unevaluated rubric reaching production is
> at least as consequential as changing the rubric, and a chain that recorded
> the second and not the first would let the interesting half of an incident
> happen off the record."*
> — `EvaluationSetRetiredV1`

Both are audit questions. Both are on the chain. **Neither reaches any screen.**
`grep` over `frontend/src/` finds no reference to either outside the generated
client, and the policy studio — which is REAL over REAL, and which is the screen
these facts belong to — renders the activation guardrail's *refusal* and says
nothing about a guardrail that was switched off, or an activation that went
ahead without one.

That is not a rendering bug. It is a **gap between what the log records and what
any human can see**, on the one screen in this product where the difference
matters most: the log's whole argument is that the interesting half of an
incident cannot happen off the record, and today it can happen off the *screen*.

**Disposition.** §E27 gains a row naming the gap explicitly, with the phase that
owns the surface. A new register row **A18** carries it, dispositioned *Owned by
Phase 17* — the integrity room and case file are where an audit view lands, and
Track E cannot claim a screen no F-phase was scoped to build. It is recorded as
a gap rather than closed by drawing something, because drawing an audit view at
F18 would be building product in the reconciliation phase and calling it
reconciliation.

---

## Finding 2 — three facts render today under a note saying they do not

Two rows carried a bare phase note:

| Row, as it read | What it implied | What is true |
|---|---|---|
| `work_order_created` / `work_order_assigned` → *Console kanban, tracking* → **Assignment row *(Phase 14)*** | nothing renders yet | `<EvidenceTrail>` has shown the assignment row to **citizen, officer and public** since M5. What waits on Phase 14 is the *kanban board* |
| `ssim_verification_completed` → *Closure* → **The printed SSIM score *(Phase 15)*** | nothing renders yet | The verification row is on the trail today, visible to all three audiences. What waits on Phase 15 is the *closure screen's printed score* |

The rows were not wrong about the phase; they were wrong about the **scope** of
the phase. One note covering a surface that ships and a surface that does not
reads as *neither ships*.

This is the finding the gate's fourth check exists for, and it is the direction
nobody looks in — an audit table is normally checked for overclaiming. It was
found because the classifier declared both rows `unbuilt`, and `unbuilt` asserts
the event is **not** bound anywhere. It was.

**Disposition.** Both rows now name both surfaces and scope the note to the half
it applies to.

---

## Finding 3 — none. Every event §E27 names exists

The first check — a row naming an event that is neither registered nor
explicitly deferred — found nothing. All 31 events §E27 named were real. This is
worth stating rather than omitting: the failure §6 Principle #9 is actually
written against, *a visual with no event behind it*, is not present in this
product.

---

## What the gate asserts now

```
surface traceability: 24 §E27 rows over 33 event types —
19 bound in the browser, 1 declared unbuilt with a phase,
33 registered types all accounted for
```

- Every registered event type is on a row, or exempted in `UNSURFACED` **with a
  written reason**. `UNSURFACED` is empty and should stay that way.
- Every row declares how its visual is driven — `stream`, `read` or `unbuilt` —
  and **the declaration is itself checked**: a `stream` row nothing binds fails,
  and an `unbuilt` row something binds fails.
- An `unbuilt` row must carry its phase in the visual cell, so an unbuilt surface
  cannot read as a shipped one.

The classification table lives in the script rather than in the blueprint, and
that is a deliberate limit worth naming: it is a statement about *mechanism*, not
about *claim*, and stating it wrongly in either direction fails the run. The
events and the surfaces still come from the blueprint, which is the only place
they may come from.

---

## What this audit does not cover

The rule's other half — *a visual element not on this list, and not classifiable
as chrome, is a defect* — is enforced here in the direction a machine can read:
**an event type named in the frontend must be on the table**. The direction it
cannot read is a *visual* with no event at all, because "is this element chrome"
is a judgement about meaning. That half is still a review pass, and §E3.4's
three single-meaning guards (`bloom`, `stamp`, `severity`) are the only part of
it that has been made mechanical.

Named here so it is not mistaken for covered.
