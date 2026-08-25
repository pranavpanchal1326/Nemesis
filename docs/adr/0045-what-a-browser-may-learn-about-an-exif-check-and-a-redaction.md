# 0045 — What a browser may learn about an EXIF check and a face redaction

- **Status:** Accepted
- **Date:** 2026-08-25
- **Owner:** SEC · PLT · PROD
- **Blueprint:** §11.1, §22.1, §26.3 · §E16.1, §E17.2, §E27
- **Extends:** ADR-0016 · **Related:** ADR-0031, ADR-0043

## Context

§E16.1's pipeline theatre stages six gates on the citizen's phone while their
report goes through, each one driven by a real event and captioned with what it
found. Two of those captions could not be produced:

> **EXIF INTACT · DEVICE NOT ON WATCHLIST**
>
> *…a face visibly blurs on the photograph itself.*

`exif_check_completed` and `media_redacted` are both registered, both emitted,
and both reached the browser with `payload: {}` — because ADR-0016 is default-deny
by construction and neither had a declared shape. §E27's traceability table maps
twenty-four event types to visuals and calls a visual element not on that list a
defect; here the defect ran the other way, and it is the one the frontend
execution plan recorded as its most consequential finding: **not a bug, a design
collision.** Adding a shaper is a small change and a real privacy decision, so it
was scheduled rather than made silently.

The question the shapers answer: **what may a browser learn about an EXIF check,
or about where a face was?**

The answer has to be given twice, because there are two audiences and ADR-0043
established the second one. `/ws/pipeline-events` is a broadcast — unauthenticated,
tenant-scoped, carrying every report in the city. `GET /complaints/{id}/events` is
capability-scoped: one report, addressed by an unguessable id its submitter holds.

## Decision

### The EXIF check

| Field | Broadcast | History | Why |
|---|---|---|---|
| `exif_present` | ✅ | ✅ | The caption is one bit. *EXIF INTACT* is `true`; *METADATA STRIPPED* is `false`, and §11.1 is explicit that absence reduces trust rather than rejecting, so the false branch is a legitimate thing to render calmly. |
| `distance_meters` | ❌ | ✅ | **The one worth arguing.** Every coordinate on the stream is coarsened to `GPS_DECIMALS = 3` (~110 m) precisely so a pin lands on a street and not at a house. A metre-precise distance from the citizen's *stated* location to their *camera's* is a second geometric constraint on the same point, and two constraints is how a coarsening gets undone. Published to the id-holder, where it describes the reader's own report and the reader already knows both endpoints. |
| `trust_delta` | ❌ | ❌ | **Not a privacy withholding**, which is why it is stated separately. The trust score is the §11.3 control surface. Publishing what each behaviour costs publishes the gradient an abuser descends, and that is as true for the report's own holder as for a stranger. |
| `reason` | ❌ | ✅ | System-authored prose, and it embeds the distance — so it inherits that row's answer exactly. |

### The redaction

| Field | Broadcast | History | Why |
|---|---|---|---|
| `media_kind` | ✅ | ✅ | The theatre needs to know whether it is animating a photograph or a clip. |
| `faces_detected`, `faces_blurred` | ✅ | ✅ | See below. |
| `exif_stripped` | ❌ | ✅ | A fact about the served copy, useful to the person whose copy it is, noise to the city. |
| `detector_id` | ❌ | ✅ | The transparency argument (§E17.4's `why? →` opens the model that decided, not only the rubric) against the evasion argument (naming the detector on a public firehose names the thing to defeat). The id-holder gets the first; the broadcast gets neither. |
| `source_sha256`, `redacted_sha256` | ❌ | ❌ | Working content addresses. `redacted_sha256` resolves to an image on `/api/v1/review/media/{sha}`; `source_sha256` addresses the **unblurred** original, which ADR-0031 keeps deliberately unreachable. A hash in a JSON body that resolves to an image is a URL with extra steps. |

### Why two counts on an unauthenticated stream, and not one boolean

This is the hardest line in this ADR and it went the less conservative way.

§22.1's promise is about **every** face. The event catalog already keeps
`faces_detected` and `faces_blurred` as separate required fields, with a stated
reason: a future change that blurs only the largest face, or drops a box for
being too small, must show up as a *divergence* rather than as an unchanged "we
blurred it" flag. A single `redaction_applied: true` cannot express failing the
promise — and the surface where failing it most needs to be visible is a
citizen's own phone, watching their own submission go through in real time.

The exposure, stated rather than waved past: a reader of the whole stream learns
*"a photograph attached to some report contained n faces"*. The envelope for this
type carries no position at all. Associating it with a place requires correlating
on `entity_id` with a `cluster_created`, whose centroid is already coarsened to
~110 m. The photograph is never published. The capture time is not in this
payload. `n` identifies nobody, places nobody, and does not distinguish three
pedestrians from three faces on a poster.

That residual is accepted. It is strictly smaller than the alternative, which is
a product that claims to blur every face and publishes no number anybody could
use to check.

**The conflict is recorded rather than resolved by taste** (§E3.5): a stricter
reading of ADR-0016 would publish the boolean. If the count is ever judged too
much, the fallback is the boolean on the broadcast and the counts on the history
— which costs §E16.1 gate 5 its animation and costs §22.1 its public
verifiability, and both of those costs should be paid deliberately.

## Alternatives considered

**Publish nothing and let the theatre poll the history endpoint.** Rejected on
the same ground ADR-0016 rejected envelope-only delivery: it turns a shader
animation into a request waterfall, and here it would be a waterfall running
during the thirty seconds §27.1 budgets for the pipeline, on a phone.

**Bucket the face count (`0`, `1`, `2+`).** Rejected. It does not remove the
exposure — `2+` still says people were in frame — and it destroys the divergence
property, which is the entire reason the two fields exist apart.

**Derive an `outcome` enum on the EXIF event** (`absent` / `matched` /
`mismatched`) so the browser gets the finding without the distance. Genuinely
attractive, and rejected on cost: the outcome is not in the stored payload, only
the distance and the threshold that produced it, and the threshold is tenant
policy that is not carried on the event. Adding it means `ExifCheckCompletedV2`
plus an upcaster, for a change that invalidates nothing already written.
`exif_present` plus §E16.1's actual caption text covers the gate; the enum is
worth revisiting the next time that payload is versioned for another reason.

## Consequences

- `RealtimeShapedEventType` grows from eight members to ten. Every frontend
  surface that switched on it re-checks exhaustively at compile time, which is
  what publishing the union was for.
- §E16.1's gates 2 and 5 become drivable from a genuine backend event, which is
  the Phase 20 gate's standard: *a scene that can only be fired by a button
  fails*.
- `test_a_partial_redaction_is_visible_on_the_wire` exists specifically to keep
  the two counts from being collapsed into one by a later tidy-up.
- The two shaper tables now differ on four types. That divergence is the thing
  ADR-0043 refused to let a shared "audience flag" hide.

## Revisit when

- **Phase 13 lands.** An authenticated submitter is a third audience, and the
  `distance_meters` withholding on the broadcast is the row most likely to move.
- **`ExifCheckCompletedV2` is written** for any reason — carry the outcome, and
  drop the derivation argument above.
- **A face-box coordinate is ever proposed for the wire.** It is not on either
  table and it should not be. Blurring a face and publishing where it was is not
  redaction.
