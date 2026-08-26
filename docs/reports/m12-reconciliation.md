# M12 — reconciliation. §E28 and §44, read line by line against what runs

- **Phase:** F18 · **Milestone:** M12 · **Stage 5 — Close**
- **Written at:** 2026-08-26
- **Gate:** `frontend/tests/reconciliation.test.ts` (98 assertions), `scripts/check_surface_traceability.py`, `scripts/check_phase_coverage.py`

---

## What this phase was for

M12 ships no screen. Its entire subject is the difference between what this
repository **says** and what it **does** — and the reason it is a phase rather
than a review pass is that four milestones' worth of experience says the
difference is not found by reading.

It was not found by reading at M5, where every citizen row read ROADMAP for the
length of a milestone after the work shipped. It was not found by reading at M6,
where two of §E28's own rows were wrong and the *generated* page is what exposed
them. And it was not found by reading here: **five wrong claims survived every
previous review of these documents**, and all five were found by writing a check
that failed.

---

## The five findings

### 1. §44: *Underreporting-zone equity flag* read **REAL**, and is a fixture

**The one M12.5 is a gate against**, sitting in the master table since v2.0.

§23.2 called it *"arguably the single most senior-level design decision in the
whole system"* and stamped it **Status: REAL**, *a few hours of GIS work*. There
is no OSM ingestion, no complaint-density overlay, no flag on any endpoint, and
`grep -r underreport backend/` returns one comment.

The signal appears exactly once in this product: `console/roadmap/AreaView.tsx`,
behind the §E24 chip, beside a `<ContractGap>` naming the endpoint that does not
exist. **The screen was behaving correctly and the status label was wrong** —
which is the specific shape M12.5 names, and the reason the gate is worded as
*no REAL row is backed by a fixture* rather than *no fixture is undisclosed*.

§E28 has said *component REAL, data ROADMAP · Phase 12 · Phase 23* since M7.
§44 was never reconciled to it. Both §44's row and §23.2's status line are now
corrected, and both say what they used to say.

### 2. §44: *Public read-only transparency API* read **ROADMAP**, and shipped at M6

Understating, which is still drift and is the direction nobody audits for. All
three endpoints §26.4 describes answer today — tenant-scoped, read-only,
unauthenticated, rate-limited, k-anonymous, publication logged as an act with a
justification (ADR-0021, ADR-0046). §26.4's own heading read *(ROADMAP, schema
described)* over a section describing shipped routes, and defect row 38 read
*schema described (roadmap for implementation)*.

Three places, one fact, corrected in all three. The rows are kept rather than
deleted: the plan and the delivery are two different facts and erasing the first
erases the record.

### 3. §E28: five clauses under one label, three of them shipped

*Golden images, Storybook diffs, Lighthouse, WCAG audit, usability session* was
**one row** reading **ROADMAP**. F1 shipped golden baselines — each verified to
fail against a perturbed render — and the Storybook diff; F1 and F3 together
landed Lighthouse on three routes. A15 and A16 had not happened.

One label over five clauses understated the three that shipped for five
milestones **and** hid the two that had not, which is the worst of both
directions at once. Split into two rows.

### 4. §E27: two registered events reach no surface at all

`evaluation_set_retired` and `policy_certification_waived` — the events that
answer *who switched the guardrail off* and *which activation bypassed it*. Both
ship, both are on the chain, neither reaches a screen. Full write-up:
[`e27-audit.md`](e27-audit.md). Carried as register row **A18**, dispositioned
*Owned by Phase 17*.

### 5. §E27: three facts render today under a note saying they do not

The work-order pair and the SSIM row carried bare *(Phase 14)* / *(Phase 15)*
notes over rows `<EvidenceTrail>` has shown to citizen, officer and public since
M5. The phase was right; the *scope* of the note was not. Also in
[`e27-audit.md`](e27-audit.md).

---

## And one decision, not a finding: A17

The string tier that could never resolve is **removed** — ADR-0058. `loadStrings`
resolved base → seed → the Phase 5 locale registry, and the third tier fetched
namespaces `db/models/i18n.py` refuses to hold. The reader does not validate its
path parameter, so it answered `200 {}` rather than refusing, which is exactly
why it lived three milestones: nothing rendered wrong and nothing ever could.

The other honest answer — widening the registry to carry product copy — was
available and is argued and rejected in the ADR: it reopens *"a tenant could
overwrite the wording of a legal notice"*, weakened by ADR-0052 and not answered
by it.

Phase 18's gate is untouched. It governs the **tenant-authored** half — a ward's
name, a category's display name — and `nem gate-phase18-locale` asserts that end
to end over HTTP against `zone`. Product copy is authored by NEMESIS and reviewed
like code, which is the line `db/models/i18n.py` drew and which the frontend had
been contradicting.

Three things now hold it: a ninth guard in `check-guards.ts` fails the build on
the endpoint literal, `tests/strings.test.ts` asserts the loader makes **no
upstream call at all**, and the deleted BFF proxy is deleted.

> **The first version of that test was decoration, and finding out is the part
> worth recording.** It stubbed `globalThis.fetch` inside the case and passed —
> *including with the removed tier deliberately pasted back in*. `openapi-fetch`
> reads `globalThis.fetch` once, at `createClient()`, which `server/upstream.ts`
> calls at module scope, so the client had closed over the real `fetch` before
> any test body ran. Resetting the module registry and importing inside the
> stub's lifetime is what makes it load-bearing. Every gate in this repository is
> required to be watched failing; this one was, and it did not.

---

## What the gates assert now

| Gate | Asserts |
|---|---|
| `frontend/tests/reconciliation.test.ts` | Every §E28 row names evidence; every named path exists and is non-empty; **every REAL row traces to a test or a shipped artefact**; **no finished row is drawn by `<NotWired>`, `<FixtureNotice>` or `<ContractGap>`, or lives in the roadmap directory**; every console screen's `wiring` and its row's `data` column say the same thing; every fixture screen is claimed by a row that does not call it REAL |
| `scripts/check_surface_traceability.py` | §E27 in both directions against the event catalog and against `frontend/src/` — 24 rows, 33 event types, all accounted for |
| `scripts/check_phase_coverage.py` | 54 ship lines and every open register row claimed by exactly one of 18 phases |
| `check-guards.ts` | Nine bans, the ninth being ADR-0058's |

The reconciliation ledger is long on purpose. **A row nobody had to write
evidence for is a row nobody checked** — which is, in one sentence, how findings
1 through 3 survived this long.

---

## What M12 does not close, and will not

**A15 — the WCAG 2.2 AA audit by a person.** Not done. The automated half is done
and broad, and it is roughly a third of the standard by `axe`'s own account. The
instrument is written, with all 56 AA criteria dispositioned and three
expected-to-fail items flagged in advance — including WCAG 2.2's new **2.5.7
Dragging Movements** against the clay camera, which the author expects to need a
single-pointer alternative. [`wcag-audit-gap.md`](wcag-audit-gap.md).

**A16 — the measured usability session.** Not run. No participant has ever
touched this product. The protocol is written — ten tasks with binary success
criteria fixed in advance, field sessions outdoors on the participant's own
phone — and no pass mark is set, deliberately, because a threshold chosen by the
builder before any data exists is a threshold chosen to be cleared.
[`usability-session-gap.md`](usability-session-gap.md).

Both were named at plan time as lead-time items to start at F3 and land at F18.
Neither was started. `docs/FRONTEND-PHASE-PLAN.md` §3 recorded that risk in
advance — *"A15 and A16 are the two clauses no amount of code closes; they need
people booked"* — and the risk landed exactly as written. Recording that the
foreseen risk was foreseen and still not mitigated is the honest close, and it
is why §E28's row for them reads **ROADMAP** rather than moving.

**A18 — two audit events with no surface.** Found here, owned by Phase 17.

---

## The state of the register at the close of Track E

| Group | Rows | State |
|---|---|---|
| A — debt inside completed milestones | A1–A14 | **All closed or dispositioned.** A12 accepted as a recorded deviation (the generated paper texture); the rest landed across F1–F8 |
| A — the clauses that are people | A15, A16 | **Open. Unbooked. Instrumented.** |
| A — decided at F18 | A17 | **Closed** — ADR-0058 |
| A — found at F18 | A18 | **Owned by Phase 17** |
| C — backend work | C1–C8 | Landed as ADRs 0043–0046, 0052 |

Thirteen milestones, M0 through M12. Twelve of them shipped a surface. This one
shipped the argument that the other twelve told the truth about themselves — and
found five places where they did not.
