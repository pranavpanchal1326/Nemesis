# 0043 — A complaint's history is readable by whoever holds its id, and every row is disclosed as a row

- **Status:** Accepted
- **Date:** 2026-08-25
- **Owner:** PLT · SEC · PROD
- **Blueprint:** §9.1, §9.3, §11.3, §11.4, §26.2, §26.4 · §E17.4, §E26, §E28
- **Supersedes nothing. Extends:** ADR-0016

## Context

§E17.4 asks for a tracking screen that is *"a paper ledger of events"*, and it
argues the requirement rather than merely stating it: **"'In Progress' is the
enemy."** A status is the system's opinion about now. What a citizen is owed is
the sequence of things that happened to their report, in order, with times, and
with `why? →` opening the rubric that produced the number beside it.

That sequence exists. It is the append-only log, hash-chained per entity (§9.3),
and it has existed since Phase 1. **Nothing served it.** `GET /complaints/{id}`
returns the projection — what is true now — and the two bulk-export datasets are
`complaints` and `work-orders`. §E28 marks *"Tracking ledger from the event log"*
as **REAL**, which was not true: `<EvidenceTrail>` could be built against the
published envelope type, and was, but it could only ever show the events that
arrived while the page was open. A ledger that starts when you open it is a
status badge with extra steps.

So a read path is needed, and the decision is not whether to build it. It is
**what a citizen may see of their own complaint's history**, which is a different
question from the one ADR-0016 answers.

ADR-0016 governs a *broadcast*: `/ws/pipeline-events` is an unauthenticated,
tenant-scoped stream carrying every report in the city to anyone who knows a
tenant id. Its answer — declare per event type what may be published, and publish
`{}` for everything else — is right for that audience and is deliberately
conservative. Applied unchanged to a single complaint addressed by its own id, it
would produce a ledger of thirteen rows saying nothing, which is not a ledger.

ADR-0016 anticipated exactly this and said what the answer had to look like:

> Phase 13 ships authorization, at which point an authenticated department user
> may legitimately receive more than an anonymous map viewer. **That is a second
> shape per event type, not a removal of the first.**

## Decision

**`GET /api/v1/complaints/{complaint_id}/events` returns every event on the
complaint's chain, in sequence, with its hash links — and a second shaper table,
`nemesis/events/disclosure.py`, decides what each row's payload may carry.**

Four parts, each argued separately.

### 1. The complaint id is the capability, and it is stated as such

The id is a UUIDv4. The system hands it to exactly one person — the submitter, in
the 202 body and on §E17.3's receipt — and to the officers who work the report.
It is unguessable, it is not derived from anything, and it is not enumerable.

That makes this a **capability-scoped read**, and it is worth being precise about
what that is and is not. It is not authentication: this endpoint cannot tell the
submitter from someone the submitter forwarded their receipt to. It is a strictly
stronger position than the broadcast, which requires only a tenant id, and a
strictly weaker one than Phase 13's session claims. The disclosure table is
calibrated to that middle position and **is expected to widen when Phase 13
lands**, not to be replaced.

### 2. Every row is disclosed as a row; only payloads are shaped

This is the part that was argued longest, because concealment is the obvious
move. Two of the fourteen types on a complaint's chain are ones a bad actor would
like to know about: `abuse_pattern_flagged` (§11.3) and
`perceptual_duplicate_detected` (§11.1). Hiding those rows was the first design.

It was rejected on three grounds, in increasing order of weight.

**It does not work.** Both types *always* also append a `review_queued` on the
same chain, because §11.4's rule is that no flag is a dead end — a detector that
fires routes to a human. Concealing the type name leaves the review row in place
one line later. The reader learns they were flagged either way.

**It breaks the property the ledger exists for.** Sequences are 1-based and
contiguous by construction; `event_chain_heads` exists to enforce that. Removing
rows leaves gaps, and a gap is either invisible — in which case a *deleted* event
and a *suppressed* one are indistinguishable, and §E17.3's claim that "this
record cannot be edited" becomes unverifiable on the one surface built to display
it — or it is visible, in which case it announces itself and buys nothing.

**It costs the wrong person.** The party harmed by an opaque hole in their own
record is overwhelmingly the false positive: a ward volunteer logging reports for
neighbours who trips the device-velocity detector, watching their report stall
with no explanation. The party protected is a coordinated campaign, which
ADR-0033 has already declined to let the flag block.

So: the row is disclosed, and the *gradient* is not. `abuse_pattern_flagged`
publishes `{}` — no pattern name, no observation count, no window, no threshold,
no trust delta. A campaign can observe a binary outcome it cannot attribute; it
cannot A/B-test its way under a number it cannot read.

### 3. Two empties are distinguished

`payload_disclosed` is `false` when a shaper returned nothing for an event that
stored something. §E3.3's rule is that an omission is shown rather than faked,
and a bare `{}` with no marker makes "this event carries no data" and "this event
carries data you are not being shown" look identical.

### 4. The hash links are published per row

`previous_hash` and `event_hash` on every row, plus the live `chain_head`
separately (ADR-0044). A reader can verify that each row links to the one before
it and that the last links to the head, without holding a single preimage.

They leak nothing. The preimage carries `tenant_id`, `entity_type`, `entity_id`,
`sequence`, the payload, and a microsecond-precision `occurred_at` — so no
payload here, including a short free-text description, is recoverable by search.

This is what turns §E17.3's sentence from a slogan into a property. *"Nobody
reads the hash. Everybody feels that this system keeps records."* The feeling is
the product; the chain is what makes the feeling true.

## What each type discloses

The table lives in `nemesis/events/disclosure.py` with an argument per entry.
The shape of the argument, in four lines:

| Withheld from everyone | Because |
|---|---|
| `description_text`, `latitude`/`longitude`, `photo_url`, `audio_url`, `device_fingerprint`, `transcript`, `matched_terms` | The citizen's own submission and §11.3 abuse data. The id is a capability, not proof of who is holding it. |
| `trust_delta`, `trust_score`, `pattern`, `observation_count`, `window_hours`, `hamming_distance`, `threshold` | The §11.3 control surface. Publishing what a behaviour costs publishes the gradient an abuser descends. Not a privacy reason. |
| `matched_complaint_id` | §26.4: no citizen identifier on a published surface, and this is a *different* citizen's report. |
| `source_sha256`, `redacted_sha256`, `evidence_hash` | Working content addresses. A hash that resolves to an image is a URL with extra steps (ADR-0031). |
| `rationale`, `decided_by_label`, `reason` | An operator writing for operators; and a shared control-plane token is not a person until Phase 13. `reason` is withheld because disclosing it for an EXIF mismatch and not for an abuse flag would make the omission itself informative. |

Everything else is disclosed, including the whole of `pipeline_stage_degraded` —
§24.2's third outcome is operational fact about the system, not about a person,
and §E16.1's *CLASSIFIER UNAVAILABLE · PARKED FOR HUMAN REVIEW* stamp cannot say
which stage failed without it.

## Alternatives considered

**Serve the ledger from the projection instead.** Rejected: the projection is
current state by construction, and reconstructing "what happened" from it is
exactly the status-badge-with-extra-steps §E17.4 objects to.

**Reuse `realtime/envelope.py`'s table with an audience flag.** Rejected, and the
two files say so in their own docstrings. One table with a flag means widening
what a citizen may see about their own report silently widens what the whole city
sees about everyone's. The tables duplicate a handful of shapes that agree today
precisely so they can diverge tomorrow without anyone noticing the coupling.

**Filter `FORBIDDEN_FIELDS` at run time rather than asserting them.** Rejected: a
run-time filter lets a careless shaper *work*. The field is declared, quietly
removed, and the mistake lives in the source until somebody widens the filter.
Asserting means the shaper fails in the pull request that added it.

**Wait for Phase 13.** Rejected on the same sequencing ground ADR-0016 rejected
it: the read path has to exist before the identity layer that will refine it, and
shipping nothing means §E28's honesty table stays wrong in the meantime.

## Consequences

- §E28's *"Tracking ledger from the event log: REAL"* becomes true — for the
  component and for the data. The row was corrected as part of this change.
- Every new event type on the complaint chain must now be decided, explicitly.
  `test_every_complaint_chain_type_has_a_stated_position` fails on an absent
  entry; `return {}` is a valid answer and a missing entry is not.
- `<EvidenceTrail>` (§E26) gets real rows, and the citizen / officer / public
  split stays *"row filtering, never different code"* — because the filtering the
  server does is on fields, and the filtering the component does is on rows.
- The response is `Cache-Control: no-store`. It is the one representation whose
  correctness is its currency.

## Revisit when

- **Phase 13 lands.** An authenticated submitter can be told apart from a
  forwarded receipt, and an officer from a citizen. That is a third shape per
  event type, and it should widen `_HISTORY_SHAPERS` rather than replace it.
- **A detector becomes consequential on its own** — if anything ever blocks on an
  abuse flag, ADR-0033 has been reversed and part 2 above must be re-argued, because
  its whole weight rests on the flag routing to a human.
