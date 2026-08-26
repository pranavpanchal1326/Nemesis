# 0057 — Client-side compression preserves EXIF by transplanting the original segment, and strips nothing

- **Status:** Accepted
- **Date:** 2026-08-26
- **Owner:** PROD · SEC
- **Blueprint:** §E21, §E17.1 · §11.1, §22.1, §26.3
- **Related:** ADR-0045 (what a *browser* may learn), ADR-0056 (the queue)
- **Consumed by:** F17

## Context

§E21 asks for *"camera-first capture with client-side compression and EXIF
preservation"*. Those two requirements are in direct mechanical conflict, and
the conflict is not obvious until you try it.

**Every browser path that compresses an image destroys its metadata.** Drawing
to a `<canvas>` and calling `toBlob` produces a *new* JPEG built from decoded
pixels; `createImageBitmap` and `OffscreenCanvas` do the same. There is no flag.
The comment in `citizen/Viewfinder.tsx` has said so since M5:

> a canvas re-encode *removes* EXIF, which is why §11.1 treats absent metadata
> as reduced trust rather than as a rejection, and why live-capture-only mode is
> a tenant switch rather than a default.

So the citizen surface has been shipping stripped photographs and taking the
trust penalty for it. That is survivable for a citizen report. It is **not**
survivable for §E21's actual job: field staff uploading **closure evidence**,
where the metadata is the difference between a photograph of the repair and a
photograph of something else, and where §11.1's reduced trust lands on the
municipality's own contractor verification rather than on a member of the public.

## Decision

**Compress the pixels, then transplant the original file's metadata segments
into the result, byte for byte.**

A JPEG is a sequence of marker segments. The encoder's output begins `FFD8`
(SOI) and is followed by its own segments; the camera's original begins `FFD8`
and carries `APP1` (EXIF, and a second `APP1` for XMP if present). The
compressed file is assembled as: `SOI` + **the original's `APP1` segments** +
the encoder's remaining segments. `src/lib/media/exif.ts` does exactly this, in
about sixty lines, with no dependency.

**Three rules govern what is carried:**

1. **`APP1` only.** EXIF and XMP. Not `APP0` (JFIF — the encoder writes its own
   and two would be malformed), not `APP2` (ICC profiles, which describe the
   *original's* colour space and would mis-describe the re-encoded pixels), and
   not `APP13` (Photoshop IRB), which carries editing history nobody asked to
   send.
2. **Nothing is edited on the way through.** The segment is copied, not parsed
   and rewritten. A rewriter is a second implementation of a format whose
   pathological cases are what make EXIF parsing a CVE genre; a copier cannot
   introduce a field, cannot lose one, and cannot disagree with the server about
   what the file says.
3. **A file that is not a JPEG, or whose segments do not parse, is sent
   uncompressed and intact.** Never compressed-and-stripped. If the choice is
   between *smaller* and *verifiable*, §11.1 says verifiable — and the
   pathological case must not be the one that silently loses evidence.

**GPS is preserved along with everything else, and that is the decision, not an
oversight.** EXIF `GPSInfo` is the most sensitive thing in the segment, and the
argument for carrying it is specific rather than general:

- **It is not new disclosure.** §26.1 already takes `latitude` and `longitude`
  as required fields of the submission. The person is sending their location in
  the request body; the same location in the file's header discloses nothing
  further to the recipient.
- **It is what the check is *for*.** `exif_check_completed` compares the
  camera's own coordinate against the stated one (ADR-0045's `distance_meters`).
  Stripping GPS does not protect the sender — it converts an evidence check into
  a permanent *EXIF absent* verdict and a permanent trust penalty for everyone
  who uses this app.
- **The server is where redaction belongs, and it already is.** §22.1's
  redaction, the face blur (`media_redacted`), and ADR-0045's rules about what
  *leaves* the server all operate downstream. A client that pre-stripped would
  be a second, weaker, unauditable redaction policy in a place with no audit log.

ADR-0045 governs what a **browser may learn** about that check. This ADR governs
what a **file carries into the system**. They are different questions and neither
weakens the other.

## Alternatives considered

**Upload the original, uncompressed.** Correct on evidence and wrong on §E21's
premise: the people uploading have the worst connectivity in the system, and a
3 MB closure photo over a throttled 2G link is the failure this phase exists to
fix. Rejected, but note that it is what the fallback path does when the
transplant cannot be performed safely — because *then* the trade runs the other
way.

**A library (`piexifjs`, `exifr`, …).** Rejected on §6 Principle #6 and on
surface area: a full parser/serialiser is a dependency, a bundle cost on the
lowest-end device in the fleet, and a parser where a copier suffices. What is
needed here is not "read EXIF" — nothing on the client reads it — it is "move
these bytes", and that is a segment walk.

**Strip GPS and keep the rest.** Considered seriously, and rejected under the
three points above. It is the option that *feels* most privacy-preserving and
actually degrades the system's own integrity check while disclosing nothing less
— the location is in the request body either way.

**Server-side compression.** Does not help. The bytes have already crossed the
link that is the problem.

## Consequences

**Easy:** closure evidence keeps its provenance and still fits down a bad link.
The EXIF check starts passing for photographs taken *through this application*,
which it never has. No dependency, no parser, no format knowledge beyond a
marker walk.

**Hard:** the transplant is format-specific — JPEG and nothing else. HEIC from
an iPhone is transcoded by the browser to JPEG on capture and arrives without
the original's metadata regardless, so on that path the fallback rule applies
and the photograph is sent as-is. That is stated in `compress.ts` rather than
discovered.

**Commits us to:** the client never editing evidence. Compression changes the
pixels — visibly, and by an amount the tenant configures — and it changes nothing
else. Any future client-side operation on a photograph (rotation, cropping,
redaction) is a change to *evidence* and needs its own decision, not this one's
precedent.

## Revisit when

A tenant needs client-side redaction before upload — a real requirement in some
jurisdictions — at which point the decision is not reversed but *bounded*: the
edit becomes an event, and what the client did to the file is recorded in the
same log that records what the server did.
