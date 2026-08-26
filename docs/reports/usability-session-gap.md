# A16 — the usability session with real users, and why there is no number

- **Register row:** A16, `docs/FRONTEND-EXECUTION-PLAN.md` §3b, cross-cutting gates
- **Claimed by:** F18 / M12
- **Written at:** F18, 2026-08-26
- **State:** **not run. No participant has ever touched this product.**

---

## The claim

§E25 Phase 18's gate is not a component library:

> the plan calls this a **design practice, not a component library**, and that
> distinction is the gate

and the register states the measurable form of it: *measured task-success rate
from a usability session with real field staff and department users, findings
tracked.*

**There is no such number and there is no such session.** Not a small one, not
an informal one, not a colleague clicking through. Every design decision in
Track E — the whole of it — was validated against the blueprint, against a test,
or against the author's judgement. None of it was validated against somebody who
had to use it.

That is the single largest unquantified risk in this frontend, and it is larger
than any technical row in the register, because every technical row is a thing
somebody knows is missing.

---

## Why there is no number, stated plainly

There are no participants. A solo build with no pilot partner has no field staff
and no department users, and the three ways to produce a number anyway were all
available and all rejected:

- **Run it with people who are not the users.** Five colleagues completing
  *file a pothole report* produces a task-success rate. It measures whether a web
  form is legible, which was never in doubt. The screens whose usability is
  actually uncertain — the review queue, the policy studio's revision diff,
  `/field` in gloves — are unusable *and* unjudgeable by someone who does not
  hold the job.
- **Run it on the author.** A rehearsal, not a measurement.
- **Report the E2E suite's pass rate as task success.** The suite asserts that a
  submission reaches the log and that a decision is written from the browser. It
  asserts nothing about whether a person could work out how. Reporting it as task
  success would be the exact category error §E28 was rewritten to remove — *has
  Track E built it* answered in place of *does it work for the person*.

A number produced any of those three ways would be worse than this document,
because it would close the row.

---

## The protocol, written so the session is a booking

### Participants

Six to eight, in two groups, recruited from one pilot ward:

- **4–5 field staff** — the people who receive a job, go to the location and
  upload closure evidence. Recruit for the actual working conditions: outdoors,
  one hand, gloves, a mid-range Android, patchy data. At least two who do not
  read English comfortably.
- **2–3 department users** — a review officer and whoever signs off policy or
  closure. At least one who currently does this work on paper or WhatsApp,
  because the comparison that matters is *against what they do today*, not
  against nothing.

Sessions are 45–60 minutes, moderated, one participant at a time, think-aloud,
recorded with consent. Field-staff sessions run **outdoors**, on the
participant's own phone, on cellular. A field app assessed at a desk on office
wifi is assessed on the wrong axis.

### Tasks, and what each is really asking

Every task has a **binary success criterion fixed before the session**, because a
success rate whose definition moves is a rate about nothing.

| # | Who | Task | Success is | The question underneath |
|---|---|---|---|---|
| 1 | Citizen proxy | Report a broken streetlight with a photo and keep proof you did | A receipt is on screen and the participant can say what it is for | Does the receipt read as proof, or as a confirmation screen? |
| 2 | Citizen proxy | Find out what happened to it | Reaches `/t/[id]` and states the current stage in their own words | Does the pipeline theatre communicate, or decorate? |
| 3 | Citizen proxy | Find out **why** it was scored the severity it was | Opens `why? →` and names one factor | §E17's central bet: that showing the rubric builds trust rather than confusion |
| 4 | Field staff | Capture a closure photo **with the network off**, then get it sent | Item visible in the queue offline; sent after reconnect; participant is confident it sent | ADR-0056's whole argument, asked of a person |
| 5 | Field staff | Same, in bright sun, gloves on, one hand | Completed without removing gloves | Outdoor mode is 7:1 by construction; 7:1 is not the same as usable |
| 6 | Field staff | Say what you are meant to do next | Names the top job unprompted | §E21's *field staff never see a kanban* — the claim that three jobs beat a board |
| 7 | Officer | Work the review queue with **no mouse** | Three items decided by keyboard | Gated in CI. Gating is not evidence a person will do it |
| 8 | Officer | Read one complaint's evidence trail and say what the system did and did not disclose | Names one withheld row and why | ADR-0043's *every row disclosed as a row* |
| 9 | Officer | Change a severity rubric, see what it would have changed, and activate it | Reaches the backtest; is refused by the guardrail; **can say why** | §E19.4. The refusal is rendered in the server's own words — the question is whether those words are actionable |
| 10 | Officer | Say which figures on this screen are real and which are placeholders | Correctly classifies 4 of 5 | **The most important task in this protocol.** The §E24 chip is the mechanism the entire honesty discipline rests on. If a user cannot tell fixture from measurement, the chip does not work and every ROADMAP screen is a lie told politely |

### Measures

- **Task success**, binary, per task, per participant — the headline the register
  asks for, reported per task and never as a single average.
- **Time to first correct action**, task 6 and task 7.
- **Errors requiring moderator rescue**, counted — a rescued task is a failure.
- **Confidence**, 1–5, asked immediately after tasks 1 and 4 (*did it send?*),
  because on those two the participant's belief is itself the product outcome.
- Verbatims for every failure.

### Reporting

A findings list, each item severity-rated and traced to a §E-section, filed into
the outstanding register exactly like a defect row — the register is where
findings are *tracked*, which is the half of A16 people forget. Task 10's result
is reported separately and prominently, whatever it says.

### Threshold

**No pass mark is set here, deliberately.** A threshold chosen by the person who
built the thing, before any data exists, is a threshold chosen to be cleared.
The first session's numbers are the baseline; the second session is measured
against the first.

---

## What would close A16

1. The sessions above, run with real participants.
2. Task-success reported per task, with the failures.
3. Findings filed into the outstanding register as rows, with owners.
4. This document replaced by the results, and §E28's row moved.

Until then the row reads **ROADMAP**, `docs/FRONTEND-EXECUTION-PLAN.md` §3a
carries it as open, and no document in this repository claims a usability
measurement exists. `docs/FRONTEND-PHASE-PLAN.md` §5 named this a lead-time item
to start at F3 and land at F18. It was not started, and F18 is where that gets
written down rather than absorbed.
