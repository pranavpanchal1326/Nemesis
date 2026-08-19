# ADR-0033 — Coordinated-abuse detection flags, and cannot express a block

**Status:** Accepted
**Date:** 2026-08-19
**Phase:** 8 — Trust & safety spine
**Owning function:** DATA · SEC

## Context

§11.3 is unusually direct for a blueprint section: coordinated-abuse detection
*"flags, does not auto-block; routes to human review queue with the evidence
bundle attached."* §11.1 is equally direct about the neighbouring case: absent
EXIF *"reduces trust rather than auto-rejecting."*

Both sentences are easy to agree with and easy to lose. The way they get lost is
never a deliberate decision to start blocking; it is a `status = FLAGGED` added
to a projector during an incident, or a `if trust_score < x: return` added to a
handler to reduce load, or a boolean called `blocked` added to a payload because
the shape looked incomplete.

The cost of losing them is asymmetric and concrete. A false positive on the
velocity detector suppresses a real citizen's report about a real hazard — and
the citizens most likely to trip a velocity limit are the most engaged ones,
while the ones most likely to have no EXIF are those using the share flows that
strip it. §23's equity safeguards and §22.2's defamation exposure both point the
same way: a system flag presented as a settled fact is a legal and a moral
problem, not just an accuracy one.

## Decision

**The blocking path is not disabled; it does not exist.** Five places, and each
one is a place somebody would otherwise add it:

**The finding type has no slot for it.** `AbuseFinding` carries a pattern, a
count, a window, a trust delta, a reason and evidence. There is no `blocked`,
no `action`, no `enforcement`. A test asserts the absence by name, because a
schema with a slot for an enforcement action invites one.

**The event payload has no slot for it either.** `AbusePatternFlaggedV1` records
what was observed and what it cost in trust. A closed field list around an open
`evidence` map, so a new detector needs no version bump — but the *shape* stays
"here is what we saw", never "here is what we did about it".

**The detectors do not write.** `assess_device_velocity` and
`assess_geographic_cluster` are queries that return findings. Neither takes a
mutation path, neither raises to stop a pipeline, and neither is reachable from
anything that could.

**The projectors do not move the status.** `abuse_pattern_flagged` and
`perceptual_duplicate_detected` set `is_fraud_flagged` — the column §9.2 already
reserves — and leave `status` alone. `exif_check_completed` moves only the trust
score. The only §11 projector that changes status is the §11.2 safety trigger,
which is the one check the blueprint *does* say bypasses the pipeline, and it
has its own projector saying so.

**The stage never halts.** `trust_stage` returns `StageResult(emitted=...)` with
no `halt`, on every path. A flagged complaint continues to classification,
dedup, scoring and routing exactly as an unflagged one does; what changes is
that a human is now looking at it.

**The one control that does block is at the boundary, is off by default, and is
a tenant's decision.** §11.1 names live-capture-only mode as *the real control*
for stripped EXIF, and it is implemented where a citizen is still listening —
the submission handler returns 422 with an explanation. Rejecting in the
pipeline would have accepted the report with a 202, told the citizen it was
received, and discarded it somewhere they cannot see, which is worse than not
having the control. It defaults **off** because turning it on excludes everyone
whose phone or browser cannot capture in-app.

## Consequences

**The review queue can be flooded, and that is the accepted failure mode.** A
genuine street-level incident — a burst water main, a protest, a power cut —
produces exactly the pattern the geographic-cluster detector looks for. The
handle for that is `trust_abuse_detection`, a kill switch that stops the
detectors from firing without changing a single decision, because they never
made one. It exists precisely because the alternative under pressure would be to
add a block.

**Trust scores drift downward and nothing acts on them alone.** Recorded as a
consequence because it looks like a bug: a report can accumulate three negative
deltas and continue through the pipeline untouched. `review_trust_floor` is what
makes the total mean something — it queues a human when the sum crosses a line
and nothing else queued — and it queues, it does not block.

**Phase 11 inherits a labelled dataset with no enforcement bias in it.** Every
`review_decisions` row is a human judgement on a report the system did *not*
act on, which is the only kind of label that can teach a model what a false
positive looks like. A system that blocked would only ever collect labels on
reports it let through.

## Alternatives considered

**Auto-reject above a threshold, with an appeal path.** Architectural principle
8 requires the appeal path to ship in the same phase, and an appeal path needs
operator identity and a citizen notification channel — Phase 13 and Phase 14.
Building the rejection now and the appeal later is exactly the sequencing the
principle forbids.

**Rate-limit the submitter instead of flagging the submission.** Already
shipped, and it is a different control: `api.ratelimit` is the Redis token
bucket §11.3 mentions, and it protects the *service* from a flood. This protects
the *record*, and the two want different memories — a bucket holds a count and
forgets, while the question a reviewer asks is "show me the other nineteen".

**A `blocked` field defaulting to `false`.** Would document the intent and keep
the option open. Rejected on the grounds this ADR opens with: the field is the
invitation.
