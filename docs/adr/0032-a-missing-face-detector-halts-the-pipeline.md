# ADR-0032 — A missing face detector halts the complaint; it never redacts nothing

**Status:** Accepted
**Date:** 2026-08-19
**Phase:** 8 — Trust & safety spine
**Owning function:** SEC · DATA

## Context

§24.2 is one of this system's better ideas: every pipeline stage declares what
happens when it cannot do its job, the degraded path is real shipped behaviour
rather than a stub, and a complaint is never lost. Phase 3 built it, and four of
the five stages have a sensible fallback — dedup skips, classification parks the
report as `pending_classification`.

Phase 8 adds a stage whose job is a legal obligation. The question this ADR
settles is what §24.2 means when the thing that cannot run is §22.1's face blur.

Three things can go wrong, and they are not the same:

- **No detector is registered in this process.** Correct and expected in four of
  the six images: the API, `worker-io`, `beat` and the relay have never carried
  MediaPipe. It is a *fault* only when it happens on a worker serving the `ml`
  queue.
- **The image will not decode.** A truncated JPEG, a file that is not an image,
  a declared size that would exhaust the worker's memory cap.
- **The detector loaded and inference failed.** Rare, and the only one of the
  three a retry might fix.

The tempting design — the one that makes every graph look healthy — is a
detector that finds no faces when none is registered. It is worth being explicit
about how bad that is: the pipeline succeeds, `media_redacted` records
`faces_detected: 0`, the redacted copy is pixel-identical to the original, the
review queue serves it, and the §22.1 breach is invisible from inside the
system, from the event log, and from outside. **A privacy control whose failure
mode is silence is not a control.**

## Decision

**§22.1 fails closed, at three layers, and none of them is a flag.**

**`active_detector()` raises.** There is no null detector, no default, and no
`or` clause. `scripts/check_media_redaction.py` asserts positively that
`redact_image` still calls the accessor — phrased as an assertion rather than as
"no `NullDetector` appears", because a fallback spelled any other way is the
same breach.

**The stage's declared fallback is `HALTED_FOR_REVIEW`, never `SKIPPED_STAGE`.**
A skipped trust stage would let a complaint reach classification and the review
queue with no redacted artefact at all — at which point the only image that
exists is the unredacted original, and the pressure to "just show that one" is a
design decision made under incident conditions by whoever is on call.

**There is no kill switch for face blur or for the §11.2 safety fail-safe.**
`flags/registry.py` gains `trust_abuse_detection` — the §11.3 detectors are
heuristics that will be noisy somewhere, and the handle to stop a review queue
filling with one genuine flood has to be reachable without a deploy. It does not
gain a switch for redaction. A documented, one-command way to turn off a legal
obligation is a documented way to cause a breach, and a switch that exists gets
pulled on the afternoon the queue is backed up.

**The two unretryable causes skip the budget.** A missing detector and an
undecodable file both raise `StagePermanentError`, which goes straight to the
fallback: the detector will still be missing in thirty seconds, and so will the
malformed JPEG. Spending five attempts on either is five attempts not spent on
the complaints behind it.

## Consequences

**A misrouted stage is loud and expensive, deliberately.** If the trust stage is
ever dispatched to a worker without MediaPipe, every complaint carrying a photo
halts. That is the correct behaviour and it is a very confusing thing to
diagnose from a queue-depth graph, so `trust.providers.install_trust_workers`
logs the asymmetry explicitly at startup — "this worker cannot redact" — and
`docs/runbooks/media-redaction-unavailable.md` is the destination.

**Complaints accumulate in the dead-letter table during a redaction outage
rather than flowing through degraded.** They are queryable, they are not lost,
and the §24.2 contract holds — but the operational shape is a *stall*, not a
degradation, and the runbook says so rather than letting somebody discover it.

**A tenant cannot trade privacy for throughput.** Stated as a consequence
because it is a real product constraint: there is no configuration in which a
customer under load processes reports faster by blurring less.

## Alternatives considered

**Blur unconditionally with a solid rectangle when no detector is available.**
Fails safe on the privacy axis and destroys the photograph, which makes the
report useless for the purpose it was submitted for. Also indistinguishable, six
months later, from a report where the whole frame genuinely was a face.

**Serve the original to reviewers only, never to the public.** Rejected because
"reviewers only" is an authorization boundary that Phase 13 has not built yet,
and because §22.1 does not carve out an audience. The review queue is exactly
where the blurred copy is most needed: it is the surface where a human looks at
strangers' photographs all day.

**A kill switch that a runbook forbids using.** A control whose safety depends
on nobody using it is not a control, and its existence would show up in the
first compliance review as a documented bypass.
