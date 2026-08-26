# A15 — the WCAG 2.2 AA audit that is a person, and has not happened

- **Register row:** A15, `docs/FRONTEND-EXECUTION-PLAN.md` §3b, cross-cutting gates
- **Claimed by:** F18 / M12
- **Written at:** F18, 2026-08-26
- **State:** **the automated half is done and clean; the audit half is unbooked**

---

## The claim, and what is actually true

§E25 Phase 18 and §E22 both ask for WCAG 2.2 AA *verified by audit, not only by
automated scan*. The distinction is the whole point of the clause: `axe` is a
rule engine, it reports **violations it can compute**, and its own documentation
puts that at roughly a third of the standard. The remaining two thirds are
judgements — *is this the right heading level for what this section means*, *does
this error message tell a person what to do*, *is this focus order the order of
the task* — and a machine cannot hold an opinion about them.

So the honest statement of this row has two halves, and both are stated:

| | State |
|---|---|
| **The scan** | **Done, clean, and broad.** `axe` at `wcag2a, wcag2aa, wcag21a, wcag21aa, wcag22aa` across the console at three densities × two scripts and over the open `⌘K` palette; over all seven fixture screens; over the citizen, public and story surfaces in both scripts; over the §E26 contract matrix at all twelve combinations; and over the clay peer list in the tier that also has a canvas. Plus 48 token-level contrast assertions before and after the press, a third role ground whose every text floor is **7:1**, and Lighthouse asserting **100 accessibility** on `/console`, `/report` and `/pune-demo/honesty` |
| **The audit** | **Has not happened.** No person has walked this product with a screen reader, a switch, magnification, or their own cognition, and no report exists that says they did |

**Nothing below closes it.** This document is the instrument — what to test, in
what order, against what — so that the session is a booking rather than a
project. A prepared, unbooked audit is a different state from an unprepared one,
and it is the state F18 leaves this row in.

---

## Why it is not being faked

Three tempting substitutes were considered and each would have made the row read
closed while leaving a disabled person's experience exactly as unexamined:

- **Widening the `axe` tag set and calling it an audit.** Already at the widest
  ruleset that maps to AA. Turning on `best-practice` would add findings and not
  add coverage of a single success criterion the engine cannot compute.
- **Having the author walk the product with NVDA and writing that up.** Useful,
  and it is *not* an audit: the person who chose the heading structure is the
  worst possible reader of whether the heading structure communicates. The value
  of the clause is a reader who does not already know what the screen means.
- **Buying a badge.** An accessibility statement generated from a scan is the
  artefact §6 Principle #8 exists to refuse.

---

## Criterion dispositions — all 56 AA success criteria

`automated` = a rule `axe` computes and this repository runs across every
surface. `gated` = not `axe`, but a written check in this repository that fails
the build; the gate is named. `person` = requires the audit.

A criterion marked `gated` is still in the auditor's scope — a gate proves a
property holds, not that the property was the right one to hold.

### 1. Perceivable

| SC | Level | Disposition | Where |
|---|---|---|---|
| 1.1.1 Non-text Content | A | automated + **person** | `axe` catches a missing `alt`; whether the text is a *description* is judgement. The clay canvas's accessible peer list is the high-value case |
| 1.2.1 Audio-only / Video-only | A | **person** | The §E12 sound layer is decorative and muted by default; an auditor should confirm nothing carries meaning by sound alone |
| 1.2.2 Captions (Prerecorded) | A | n/a | No prerecorded audio or video ships |
| 1.2.3 Audio Description or Alternative | A | n/a | As above |
| 1.2.4 Captions (Live) | AA | n/a | As above |
| 1.2.5 Audio Description | AA | n/a | As above |
| 1.3.1 Info and Relationships | A | automated + **person** | `axe` catches structural mistakes; the Walk's nine acts and the console's rail are where semantics and meaning can diverge without a rule firing |
| 1.3.2 Meaningful Sequence | A | **person** | The film's scroll-driven acts are the risk: DOM order and read order must agree at every `t` |
| 1.3.3 Sensory Characteristics | A | **person** | §E13's tier ladder and the severity glaze — see *Flagged for the auditor* #2 |
| 1.3.4 Orientation | AA | gated | No orientation lock anywhere; `/field` is designed portrait-first and works in both |
| 1.3.5 Identify Input Purpose | AA | automated | `axe` autocomplete rules over the report and control-plane forms |
| 1.4.1 Use of Color | A | gated + **person** | ADR-0039: an unverified flag is fluorescent pink **and hatched**, never colour alone; the `single-meaning-severity` guard keeps severity ink to one meaning |
| 1.4.2 Audio Control | A | gated | The sound graph is muted by default and the unmute is designed rather than hidden (§E2 defect #9), asserted in `tests/sound.test.ts` |
| 1.4.3 Contrast (Minimum) | AA | gated | 48 assertions across every role × ground, before and after the press; the outdoor ground at 7:1 |
| 1.4.4 Resize Text | AA | **person** | Three density modes are not the same thing as 200% browser zoom |
| 1.4.5 Images of Text | A A | gated | The share card is the only rendered-text image and it is generated from the shipped faces, not a picture of a page |
| 1.4.10 Reflow | AA | **person** | Console density and the print stylesheet are gated; 320 px reflow of the dense mode is not |
| 1.4.11 Non-text Contrast | AA | gated + **person** | Token contrast covers the palette; whether every *control boundary* clears 3:1 in every state is a sweep a person does |
| 1.4.12 Text Spacing | AA | **person** | Per-script leading is gated at the type scale; the user-stylesheet override case is not tested |
| 1.4.13 Content on Hover or Focus | AA | **person** | The `⌘K` palette and the `why? →` rubric are the two surfaces this applies to |

### 2. Operable

| SC | Level | Disposition | Where |
|---|---|---|---|
| 2.1.1 Keyboard | A | gated | `console.spec.ts` drives F3's shell and the full keyboard path **with no mouse at all** |
| 2.1.2 No Keyboard Trap | A | automated + **person** | The palette is the candidate; a modal that traps correctly and releases wrongly is a person's finding |
| 2.1.4 Character Key Shortcuts | A | gated | The keyboard model refuses every key while somebody is typing — the defect M7 found and fixed |
| 2.2.1 Timing Adjustable | A | gated | Nothing in this product expires. The pipeline theatre reads the log rather than a timer, and a gate the log will never reach says *held* |
| 2.2.2 Pause, Stop, Hide | A | gated + **person** | `prefers-reduced-motion` is honoured across every bus and the whole scene; the 12 fps stepped world under a moving camera is the case an auditor should judge |
| 2.3.1 Three Flashes | A | gated | Bloom is reserved for `safety_trigger_fired` and held for two seconds on the 12 fps clock — asserted by the `single-meaning-bloom` guard and `tests/ladder.spec.ts` |
| 2.4.1 Bypass Blocks | A | gated | The console skip link, and the M7 defect where it was unreachable because a mount-time `scrollIntoView` moved the focus start |
| 2.4.2 Page Titled | A | automated | — |
| 2.4.3 Focus Order | A | **person** | Gated for the console's no-mouse path; the film and `/field` are not |
| 2.4.4 Link Purpose (In Context) | A | automated + **person** | — |
| 2.4.5 Multiple Ways | AA | **person** | — |
| 2.4.6 Headings and Labels | AA | automated + **person** | — |
| 2.4.7 Focus Visible | AA | automated | — |
| **2.4.11 Focus Not Obscured (Min)** | AA *(2.2)* | **person** | New in 2.2. The console's sticky chrome over a scrolled item pane is the exact shape this criterion was written for |
| 2.5.1 Pointer Gestures | A | **person** | **See *Flagged for the auditor* #1** |
| 2.5.2 Pointer Cancellation | A | **person** | The `/field` camera button is the one to check |
| 2.5.3 Label in Name | A | automated | — |
| 2.5.4 Motion Actuation | A | gated | Nothing is actuated by device motion |
| **2.5.7 Dragging Movements** | AA *(2.2)* | **person** | **See *Flagged for the auditor* #1** |
| **2.5.8 Target Size (Minimum)** | AA *(2.2)* | **person** | The 60 px place pin and `/field`'s large camera button clear it by design; the console's dense mode at three densities is the case nobody has measured |

### 3. Understandable

| SC | Level | Disposition | Where |
|---|---|---|---|
| 3.1.1 Language of Page | A | gated | `lang` and `dir` are derived from the negotiated locale and set on every surface; `tests/rtl.spec.ts` asserts the frame **by geometry** |
| 3.1.2 Language of Parts | AA | **person** | Devanagari inside an English page, and the reverse — routine on these surfaces and unasserted |
| 3.2.1 On Focus | A | automated + **person** | — |
| 3.2.2 On Input | A | **person** | — |
| 3.2.3 Consistent Navigation | AA | gated | The console rail, the palette and the route guard all read one `SCREENS` list |
| 3.2.4 Consistent Identification | AA | **person** | — |
| **3.2.6 Consistent Help** | A *(2.2)* | **person** | New in 2.2 |
| 3.3.1 Error Identification | A | automated + **person** | — |
| 3.3.2 Labels or Instructions | A | automated + **person** | — |
| 3.3.3 Error Suggestion | AA | **person** | The activation guardrail's refusal is rendered *in the server's own words* — whether those words tell an officer what to do next is precisely a judgement |
| 3.3.4 Error Prevention (Legal, Financial, Data) | AA | **person** | Publication (ADR-0046) and policy activation are the two irreversible-feeling acts |
| **3.3.7 Redundant Entry** | A *(2.2)* | **person** | New in 2.2. The report flow and the offline queue's replay |
| **3.3.8 Accessible Authentication (Min)** | AA *(2.2)* | n/a → **person** | No end-user authentication ships on these surfaces; an auditor should confirm that is still true of the console at the time of audit |

### 4. Robust

| SC | Level | Disposition | Where |
|---|---|---|---|
| 4.1.2 Name, Role, Value | A | automated + **person** | The clay canvas and its peer list are the case: the list is asserted *synchronised by digest* in every tier, which is a data claim, not an announcement claim |
| 4.1.3 Status Messages | AA | **person** | The degraded banner, the queue's per-item upload state, and the pipeline theatre's six gates all change without a navigation |

---

## Flagged for the auditor — three the author expects to fail

Written down in advance, because a limitation stated afterwards is an excuse
(§6 Principle #8).

**1. The clay camera is a drag, and 2.5.7 is new in WCAG 2.2.** The 3D scene is
navigated by pointer drag. `dragRotate` is disabled on the 2D MapLibre path, and
the accessible peer list is present and synchronised in **every** tier — which is
a genuine equivalent for *reading* the map and is not obviously an equivalent for
*navigating* it. Whether the peer list discharges 2.5.7 and 2.5.1, or whether the
camera needs a single-pointer alternative, is an auditor's call and the author's
expectation is that it needs one.

**2. Tier C and Tier D are the accessibility story and are described as a
performance ladder.** §E13's fallbacks were designed for weak hardware. They are
also, in practice, the reduced-motion and no-JavaScript experience — headless
Chromium reporting `prefers-reduced-motion: reduce` by default is how M8
discovered every browser test had been running at Tier C. An auditor should
judge the tiers as *alternatives*, not as degradations, and 1.3.3 is where that
lands.

**3. `/field` is the least-swept surface and has the highest stakes.** Outdoor
mode's 7:1 floor is generated and gated; the rest of the screen — target sizes
with gloves on, focus order under one thumb, error recovery when a submission
fails to send — is asserted by eight browser cases written by the person who
built it. It is the surface used by the people with the least room to work
around a defect.

---

## What would close A15

1. An audit by a practitioner who did not build this, against WCAG 2.2 AA, with
   at least one screen-reader pass (NVDA or JAWS on Windows, VoiceOver on macOS)
   and one keyboard-only pass, covering: `/report`, `/t/[id]`, `/console` and one
   fixture screen, one public place page in both scripts, `/field` in outdoor
   mode, and the Walk at Tier S and at Tier C.
2. A findings list with a severity per finding and a criterion reference.
3. Each finding either fixed, or recorded as a deviation with the argument
   written down — the same disposition ADR-0053 and the frame-rate report take.
4. This document replaced by the audit, and §E28's row moved.

**Cost and lead time:** a scoped external AA audit of this surface area is
commonly two to four days of a practitioner's time and two to four weeks of
scheduling. `docs/FRONTEND-PHASE-PLAN.md` §5 already said so — *"lead-time
items, start at F3, land at F18"* — and it did not happen, which is the fact this
document exists to record rather than soften.
