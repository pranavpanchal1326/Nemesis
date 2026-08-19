# ADR-0031 — The raw photograph persists, unreachably, for the retention window

**Status:** Accepted
**Date:** 2026-08-19
**Phase:** 8 — Trust & safety spine
**Owning function:** DATA · SEC

## Context

Two requirements in this repository disagree with each other, and Phase 8 is
where the disagreement has to be settled rather than inherited.

`docs/PHASES.md` summarises the phase's fifth deliverable as **"MediaPipe face
blur applied *before* any persistence, including temp paths"**.

Blueprint §22.4 states a retention schedule as a table of concrete commitments,
the first row of which is **"Raw uploaded photo — 30 days — sufficient for
dispute/verification window; minimizes exposure"**. §22.1 states the obligation
itself more loosely: *"Faces blurred via MediaPipe before storage"*.

Both cannot be literally true. If the original is destroyed the moment it
arrives, there is no raw photograph to retain for thirty days and nothing to
re-examine when a citizen disputes a redaction, when a redaction is alleged to
have removed evidence, or when a court asks what was actually photographed. If
the original is retained, then something has been persisted unblurred.

There is a second, harder constraint. The `api` container is deliberately built
without the `ml` extra — that split is Phase 0's, driven by a 16 GB machine that
also runs Ollama, and it is what keeps the API image around 200 MB instead of
carrying torch. MediaPipe lives only in `worker-ml`. So a blur performed *during
the upload request*, before the bytes touch disk, would mean putting MediaPipe
(and its GPU-delegate link, which Phase 0's gate caught failing at runtime on
`libEGL.so.1`) into the process that has to answer within the §27.1 two-second
acknowledgment budget.

Phase 3 anticipated all of this and wrote the intended resolution into
`ingest/media.py`'s own docstring: quarantine now, blur-and-promote in Phase 8,
and *"the guard test that phase's gate requires has exactly one code path to
police because of this."*

## Decision

**The raw upload persists in quarantine for the tenant's retention window, and
is made unreachable rather than absent.**

Four properties, each structural rather than conventional:

1. **No route can reach it.** Quarantine URIs use the scheme
   `nemesis+quarantine`, which no browser can follow — Phase 3 chose a non-HTTP
   scheme deliberately, for this phase. There is exactly one media route in the
   API and it resolves `RedactedStore`, which refuses a quarantine URI *by name*
   rather than by falling through a prefix test.
2. **Two callers read it, both named.** `MediaStore.resolve` — the only way to
   turn a stored URI into a path — is called from `trust/verification.py` (the
   redaction stage) and from the ingest handler's §11.1 live-capture check,
   which reads EXIF off the just-stored upload to decide whether to refuse the
   submission. `scripts/check_media_redaction.py` fails the build on a third.
3. **One writer produces anything servable.** `RedactedStore` exposes no public
   write. The only path into the served root is `trust.redaction.redact_image`,
   which cannot return without `active_detector()` having run — and
   `active_detector()` raises rather than returning a stand-in when no detector
   is registered.
4. **It expires.** Every artefact is stamped at processing time with
   `purge_raw_after` and `purge_exif_after`, resolved from the tenant's own
   `TrustThresholds.retention` document and indexed for the sweep. §22.4 stops
   being a paragraph and becomes a row a query can find. The sweep itself is
   Phase 26's; what this phase owes it is something to sweep.

**And the served copy is a re-encode, not a patch.** Blurring a JPEG in place
would leave every APP segment intact — including the embedded thumbnail, which
is a second, smaller copy of the *original* image, faces and all. Re-encoding
from decoded pixels cannot carry one, so the metadata strip is a consequence of
the design rather than a step that can be forgotten.

## Consequences

**What is now provable, and what is not.**

Provable: no unblurred image is reachable over HTTP; no unblurred image is read
by any code outside two named functions; no image reaches the served store
without a face detector having run; and every raw upload has a stated expiry.

Not provable, and stated plainly: an operator with a shell on the worker, or a
backup of the `uploads` volume, has the originals for up to thirty days. That is
the same exposure §22.4 accepts when it commits to retaining them, and closing
it is an infrastructure control — encrypted volumes, restricted shell access —
which belongs to Phase 25 rather than to application code.

**The literal words in `docs/PHASES.md` are not met, and the phase notes say
so.** This ADR is the record of why, and the alternative was worse in both
directions: destroying the original abandons §22.4's dispute window, and blurring
in-request puts a 500 MB dependency and a CPU-bound decode inside a two-second
budget on the container that must stay small.

**A future change that needs a second reader of quarantine is a design
conversation, not a second call site.** The guard will refuse it, and the
refusal names this ADR.

## Alternatives considered

**Blur in the API process, before the first write.** Meets the literal
requirement. Rejected on three counts: it puts MediaPipe in the image Phase 0
deliberately kept clean, it moves a CPU-bound decode of up to 15 MB into the
§27.1 acknowledgment budget, and it leaves §22.4's thirty-day dispute window with
nothing to point at. Worth revisiting if the deployment shape changes — a
dedicated redaction sidecar reachable from the API would satisfy every
requirement at once, and is the shape to reach for if Phase 1b's cloud
environments make it cheap.

**Delete the original as soon as redaction succeeds.** Simpler, and it satisfies
the summary line exactly. Rejected because it makes §22.4's first row
unimplementable and, more concretely, because the first disputed redaction has
no evidence on either side. A citizen who says "you blurred out the licence
plate that proves my case" would be unanswerable.

**Encrypt quarantine at rest with a key only `worker-ml` holds.** Genuinely
better than what is shipped here, and deferred rather than dismissed: it needs
key management, key rotation, and a story for the backup path, all of which are
Phase 25's and none of which is improved by being half-built now. The retention
stamps this phase writes are what a later encryption-at-rest change would key
its purge off.
