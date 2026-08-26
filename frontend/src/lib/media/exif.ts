/**
 * Moving a JPEG's metadata from one file to another — ADR-0057, F17.
 *
 * §E21 asks for *"client-side compression with EXIF preservation"*, and those
 * two are in mechanical conflict: every browser path that compresses an image
 * builds a new JPEG out of decoded pixels and keeps none of the original's
 * metadata. There is no flag. So the metadata has to be carried across by hand.
 *
 * **This module copies bytes and never parses them.** It walks the marker
 * segments of a JPEG, finds the `APP1` segments — EXIF, and XMP where a camera
 * wrote one — and splices them into another JPEG immediately after the SOI.
 * Nothing is decoded, nothing is rewritten, and no field is understood. A
 * rewriter would be a second implementation of a format whose pathological
 * cases are an entire CVE genre; a copier cannot invent a field, cannot lose
 * one, and cannot disagree with the server about what the file says.
 *
 * **`APP1` only, and ADR-0057 argues each exclusion.** Not `APP0` (JFIF — the
 * encoder writes its own and two would be malformed), not `APP2` (an ICC
 * profile describing the *original's* colour, which would mis-describe
 * re-encoded pixels), not `APP13` (Photoshop's block, which carries editing
 * history nobody asked to send).
 *
 * **A file that does not parse is refused rather than mangled.** Every function
 * here returns `null` on anything it does not fully understand, and the caller's
 * rule (`compress.ts`) is that a refusal means *send the original, intact*.
 * Never compressed-and-stripped: if the choice is between smaller and
 * verifiable, §11.1 says verifiable.
 */

const SOI = 0xd8;
const EOI = 0xd9;
const SOS = 0xda;
const APP1 = 0xe1;
const MARKER = 0xff;

/** Where one marker segment sits in the file. */
interface Segment {
  readonly marker: number;
  /** Index of the `0xFF` that starts the marker. */
  readonly start: number;
  /** Index one past the end of the segment's payload. */
  readonly end: number;
}

/**
 * Walk a JPEG's segments, stopping at the start of the compressed scan.
 *
 * Everything this module cares about lives before `SOS`; after it the file is
 * entropy-coded data in which `0xFF` bytes are stuffed rather than markers, and
 * a walker that kept going would find markers that are not there. Returns
 * `null` for anything that is not a JPEG or whose lengths do not add up.
 */
export function segments(bytes: Uint8Array): readonly Segment[] | null {
  if (bytes.length < 4 || bytes[0] !== MARKER || bytes[1] !== SOI) return null;

  const found: Segment[] = [];
  let at = 2;

  while (at + 3 < bytes.length) {
    if (bytes[at] !== MARKER) return null;

    // Fill bytes: a run of 0xFF before a marker is legal padding.
    let cursor = at;
    while (cursor < bytes.length && bytes[cursor] === MARKER) cursor += 1;
    const marker = bytes[cursor];
    if (marker === undefined) return null;

    if (marker === SOS || marker === EOI) {
      found.push({ marker, start: at, end: bytes.length });
      return found;
    }

    const high = bytes[cursor + 1];
    const low = bytes[cursor + 2];
    if (high === undefined || low === undefined) return null;
    // The length field includes its own two bytes and excludes the marker.
    const length = (high << 8) | low;
    if (length < 2) return null;

    const end = cursor + 1 + length;
    if (end > bytes.length) return null;

    found.push({ marker, start: at, end });
    at = end;
  }

  return null;
}

/** The `APP1` segments of a JPEG, as raw byte ranges, or `null`. */
export function metadataSegments(bytes: Uint8Array): readonly Uint8Array[] | null {
  const walked = segments(bytes);
  if (walked === null) return null;
  return walked
    .filter((segment) => segment.marker === APP1)
    .map((segment) => bytes.subarray(segment.start, segment.end));
}

/**
 * Put `original`'s metadata into `encoded`.
 *
 * Both must be JPEGs this module can walk; anything else answers `null`, and
 * the caller sends the original. An original with no `APP1` at all also answers
 * `null` — there is nothing to preserve, so there is no reason to rebuild the
 * file, and a rebuild that changed nothing would still be a rebuild somebody
 * would have to reason about later.
 */
export function transplantMetadata(original: Uint8Array, encoded: Uint8Array): Uint8Array | null {
  const carried = metadataSegments(original);
  if (carried === null || carried.length === 0) return null;

  const walked = segments(encoded);
  if (walked === null) return null;

  // Everything the encoder wrote, minus any APP1 of its own — a browser does not
  // write one, and if some future one does, the original's must win rather than
  // sit beside it.
  const encoderOwn = walked.filter((segment) => segment.marker === APP1);
  const total =
    2 +
    carried.reduce((sum, segment) => sum + segment.length, 0) +
    (encoded.length - 2 - encoderOwn.reduce((sum, s) => sum + (s.end - s.start), 0));

  const out = new Uint8Array(total);
  let at = 0;

  // SOI first: a JPEG that does not begin FFD8 is not a JPEG.
  out.set(encoded.subarray(0, 2), at);
  at += 2;

  for (const segment of carried) {
    out.set(segment, at);
    at += segment.length;
  }

  // Then the encoder's own bytes, skipping any APP1 it wrote.
  let cursor = 2;
  for (const segment of encoderOwn) {
    out.set(encoded.subarray(cursor, segment.start), at);
    at += segment.start - cursor;
    cursor = segment.end;
  }
  out.set(encoded.subarray(cursor), at);
  at += encoded.length - cursor;

  return at === total ? out : null;
}

/** Does this file carry any EXIF or XMP at all? Used to report honestly. */
export function hasMetadata(bytes: Uint8Array): boolean {
  const carried = metadataSegments(bytes);
  return carried !== null && carried.length > 0;
}
