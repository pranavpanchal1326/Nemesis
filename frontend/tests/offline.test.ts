import { describe, expect, it } from "vitest";

import { ROLE, ROLE_GROUNDS } from "../src/design/generated/tokens.ts";
import { contrastRatio } from "../scripts/generate-tokens.ts";
import {
  hasMetadata,
  metadataSegments,
  segments,
  transplantMetadata,
} from "../src/lib/media/exif.ts";
import { fitWithin } from "../src/lib/media/compress.ts";
import {
  MAX_ATTEMPTS,
  drainable,
  resumeState,
  type QueuedSubmission,
} from "../src/lib/offline/queue.ts";

/**
 * F17's properties, asserted where they can be — §E21, §E25 Phase 22, M11.
 *
 * The Phase 22 gate has four clauses and three of them can only be taken in a
 * browser with a real network to switch off (`tests/field.spec.ts`). What is
 * here is the part that is *policy* rather than plumbing, and that is worth
 * separating: **whether a killed app resumes correctly is a decision about how
 * to read a row**, not a fact about IndexedDB, and a decision is better
 * asserted as arithmetic than as a browser dance.
 *
 * Three groups:
 *
 * · **The queue's policy** — which rows a drain touches, and how a restart
 *   reads a row it finds mid-flight. This is the gate's second clause reduced
 *   to the one line that actually decides it.
 * · **The EXIF transplant** (ADR-0057) — over JPEGs built here byte by byte,
 *   because a fixture photograph would make this a test of that photograph.
 * · **Outdoor mode's 7:1 floor**, restated as its own case so the Phase 22
 *   clause has somewhere obvious to be read, even though
 *   `tests/contrast.test.ts` already fails the build when it drifts.
 */

// --------------------------------------------------------------------------
// The queue's policy (ADR-0056)
// --------------------------------------------------------------------------

function row(overrides: Partial<QueuedSubmission> = {}): QueuedSubmission {
  return {
    idempotencyKey: "k",
    draft: {
      idempotencyKey: "k",
      latitude: 18.5,
      longitude: 73.8,
      deviceFingerprint: "d",
    },
    state: "queued",
    attempts: 0,
    createdAt: 0,
    complaintId: null,
    replayed: false,
    failure: null,
    ...overrides,
  };
}

describe("§E25 Phase 22 — a killed app resumes without duplicating or losing", () => {
  it("retries a row that was mid-flight when the process died", () => {
    // **The subtle one, and the whole clause.** A row left in `sending` is not
    // evidence that the request failed — the 202 may have been written on the
    // server and lost on the way back. Retrying is safe *because* the
    // idempotency key goes with it: the server answers the repeat with the
    // original complaint and `Idempotent-Replay: true`.
    expect(resumeState(row({ state: "sending" }))).toBe("retrying");
  });

  it("leaves a sent row alone, so nothing is filed twice", () => {
    expect(resumeState(row({ state: "sent" }))).toBe("sent");
  });

  it("does not quietly un-park a row that was refused", () => {
    expect(resumeState(row({ state: "parked" }))).toBe("parked");
  });

  it("drains what is waiting and nothing else", () => {
    const rows = [
      row({ idempotencyKey: "a", state: "queued" }),
      row({ idempotencyKey: "b", state: "retrying" }),
      row({ idempotencyKey: "c", state: "sending" }),
      row({ idempotencyKey: "d", state: "sent" }),
      row({ idempotencyKey: "e", state: "parked" }),
    ];
    expect(drainable(rows).map((r) => r.idempotencyKey)).toEqual(["a", "b"]);
  });

  it("stops retrying rather than spinning forever", () => {
    // §E3.3 — a spinner over a permanent failure is a lie told to somebody
    // standing in a basement.
    expect(drainable([row({ attempts: MAX_ATTEMPTS })])).toHaveLength(0);
    expect(drainable([row({ attempts: MAX_ATTEMPTS - 1 })])).toHaveLength(1);
  });
});

// --------------------------------------------------------------------------
// The EXIF transplant (ADR-0057)
// --------------------------------------------------------------------------

/** A minimal but structurally valid JPEG, assembled here so the assertions are
 *  about the walker rather than about somebody's holiday photograph. */
function jpeg(options: { readonly exif?: string; readonly jfif?: boolean } = {}): Uint8Array {
  const bytes: number[] = [0xff, 0xd8];

  if (options.jfif === true) {
    const payload = [0x4a, 0x46, 0x49, 0x46, 0x00, 0x01, 0x01];
    bytes.push(0xff, 0xe0, 0x00, payload.length + 2, ...payload);
  }

  if (options.exif !== undefined) {
    // `split("")` rather than a spread: this is deliberately a byte-oriented
    // fixture — ASCII marker payloads — and the lint rule's concern (spreading
    // a string decomposes emoji) is exactly the wrong shape of care here.
    const payload = Array.from(options.exif, (c) => c.charCodeAt(0));
    const length = payload.length + 2;
    bytes.push(0xff, 0xe1, (length >> 8) & 0xff, length & 0xff, ...payload);
  }

  // A quantisation table, then the scan. Everything after SOS is entropy-coded
  // and the walker must stop there rather than hunting for markers in it —
  // including this deliberate 0xFFD8 in the middle of the "scan".
  bytes.push(0xff, 0xdb, 0x00, 0x04, 0x00, 0x00);
  bytes.push(0xff, 0xda, 0x00, 0x08, 0x01, 0x01, 0x00, 0x00, 0x3f, 0x00);
  bytes.push(0x12, 0xff, 0xd8, 0x34, 0x56);
  bytes.push(0xff, 0xd9);

  return new Uint8Array(bytes);
}

describe("ADR-0057 — compression keeps what makes a photograph evidence", () => {
  it("walks a JPEG's segments and stops at the scan", () => {
    const walked = segments(jpeg({ exif: "Exif\0\0DATA", jfif: true }));
    expect(walked).not.toBeNull();
    const markers = (walked ?? []).map((s) => s.marker);
    expect(markers).toEqual([0xe0, 0xe1, 0xdb, 0xda]);
  });

  it("refuses anything that is not a JPEG rather than guessing", () => {
    expect(segments(new Uint8Array([0x89, 0x50, 0x4e, 0x47]))).toBeNull();
    expect(segments(new Uint8Array([0xff, 0xd8]))).toBeNull();
  });

  it("refuses a JPEG whose lengths do not add up", () => {
    // A length field claiming more bytes than the file holds. This is the
    // shape of half the JPEG parser CVEs, and the answer is `null` — which the
    // caller turns into "send the original intact" rather than into a crash.
    const truncated = new Uint8Array([0xff, 0xd8, 0xff, 0xe1, 0x7f, 0xff, 0x00]);
    expect(segments(truncated)).toBeNull();
  });

  it("finds the metadata segment and only the metadata segment", () => {
    const found = metadataSegments(jpeg({ exif: "Exif\0\0GPS-HERE", jfif: true }));
    expect(found).toHaveLength(1);
    expect(String.fromCharCode(...(found?.[0] ?? []).slice(4))).toContain("GPS-HERE");
  });

  it("carries the original's metadata into a re-encoded file, byte for byte", () => {
    const original = jpeg({ exif: "Exif\0\0GPS-HERE", jfif: true });
    const encoded = jpeg({ jfif: true });

    const merged = transplantMetadata(original, encoded);
    expect(merged).not.toBeNull();
    if (merged === null) return;

    // The metadata survived…
    expect(hasMetadata(merged)).toBe(true);
    const carried = metadataSegments(merged)?.[0] ?? new Uint8Array();
    const source = metadataSegments(original)?.[0] ?? new Uint8Array();
    expect(Array.from(carried)).toEqual(Array.from(source));

    // …and the file is still a JPEG the walker can read, with the encoder's own
    // segments intact behind it. A transplant that produced a malformed file
    // would be worse than no transplant at all.
    const walked = segments(merged);
    expect(walked).not.toBeNull();
    expect((walked ?? []).map((s) => s.marker)).toEqual([0xe1, 0xe0, 0xdb, 0xda]);
  });

  it("declines when there is nothing to preserve, rather than rebuilding a file for nothing", () => {
    expect(transplantMetadata(jpeg({ jfif: true }), jpeg({ jfif: true }))).toBeNull();
  });

  it("declines when either file will not walk", () => {
    const notJpeg = new Uint8Array([1, 2, 3, 4]);
    expect(transplantMetadata(jpeg({ exif: "Exif\0\0X" }), notJpeg)).toBeNull();
    expect(transplantMetadata(notJpeg, jpeg({ exif: "Exif\0\0X" }))).toBeNull();
  });

  it("scales the long edge and keeps the aspect ratio", () => {
    expect(fitWithin(4000, 3000, 1600)).toEqual({ width: 1600, height: 1200 });
    expect(fitWithin(3000, 4000, 1600)).toEqual({ width: 1200, height: 1600 });
    // Never upscales: a small photograph is already small, and enlarging it
    // would cost bytes and add nothing a reviewer can see.
    expect(fitWithin(800, 600, 1600)).toEqual({ width: 800, height: 600 });
  });
});

// --------------------------------------------------------------------------
// Outdoor mode
// --------------------------------------------------------------------------

describe("§E25 Phase 22 — outdoor mode passes contrast at 7:1 for primary text", () => {
  it("clears 7:1 for every role that carries words, on its own stock", () => {
    // *"for primary text"* is the gate's own scope, and this loop reads it
    // exactly: the roles that set type. A rule is a boundary rather than words
    // — WCAG asks 3:1 of one and the outdoor theme's own `min` says 3, which
    // `tests/contrast.test.ts` enforces — and the flag hatch never carries the
    // text (ADR-0039), which is the whole point of hatching it.
    const ground = ROLE.outdoor.ground.value;
    const carriesWords = Object.entries(ROLE.outdoor).filter(
      ([role]) => role.startsWith("text-") || role === "flag-text",
    );

    expect(carriesWords.length, "no text roles found").toBeGreaterThan(2);
    for (const [role, def] of carriesWords) {
      const ratio = contrastRatio(def.value, ground);
      expect(ratio, `${role} (${def.value}, from ${def.derivation})`).toBeGreaterThanOrEqual(7);
    }
  });

  it("keeps a rule visible in glare, which AA does not ask and glare does", () => {
    // 4.17:1 against a 3:1 floor. WCAG asks nothing of a decorative rule at
    // all; a hairline below 3:1 on a phone at noon is a rule nobody can see,
    // and this surface is entirely made of bordered boxes.
    const ratio = contrastRatio(ROLE.outdoor.rule.value, ROLE.outdoor.ground.value);
    expect(ratio).toBeGreaterThanOrEqual(3);
  });

  it("prints on one stock, and it is the brightest sheet §E9.1 has", () => {
    // On a screen fighting the sun the ground has to be the strongest thing in
    // the frame. Anything else is a design that was only ever seen indoors.
    expect(ROLE_GROUNDS.outdoor).toEqual(["chalk"]);
  });

  it("is near-monochrome rather than monochrome", () => {
    // §E21 says *near*. One thing on the screen is allowed to be a colour, and
    // it is the thing you tap — SIGNAL, at 11.65:1.
    expect(ROLE.outdoor["text-signal"].value).not.toBe(ROLE.outdoor["text-primary"].value);
  });
});
