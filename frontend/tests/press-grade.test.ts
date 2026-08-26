import { describe, expect, it } from "vitest";

import { INK_SET, PAPER_LINEAR } from "../src/design/generated/tokens.ts";
import { planPress } from "../src/press/press-model.ts";

/**
 * ADR-0061 — a run is printed at an exposure.
 *
 * The film printed as a flat brown field with the city invisible in it, and the
 * cause was arithmetic: §E9.2 gives the story run no black plate, so one plate
 * carries the whole model and §E7.1 makes the clay body *that plate's own ink*.
 * Everything from the body tone down solved past full coverage and printed
 * solid.
 *
 * These assertions are the ones that would have caught it, and they are written
 * as measurements rather than as a snapshot: a golden image of a flat sheet
 * looks exactly as much like "a decision" as a golden image of a city.
 */

const MIN_CHANNEL = 1e-4;

/** The clay body across its rendered range — the samples the report measured. */
const CLAY = {
  lit: "#B98A78",
  body: "#925F52",
  shadow: "#6B4238",
} as const;

function linearOf(hex: string): readonly [number, number, number] {
  const n = Number.parseInt(hex.slice(1), 16);
  const channel = (v: number) => {
    const c = v / 255;
    return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  };
  return [channel((n >> 16) & 255), channel((n >> 8) & 255), channel(n & 255)];
}

/**
 * The shader's stage 1, in TypeScript.
 *
 * `press-tsl.ts` computes `clamp(dot(row, needed · gamma), 0, 1)` per plate,
 * and this is that expression with the clamp left off — because the whole
 * question is *how far past 1* the ungraded run went, and a clamped number
 * cannot answer it.
 */
function rawCoverage(surface: keyof typeof INK_SET, hex: string): readonly number[] {
  const plan = planPress({ surface, quality: "full", severity: "high", seed: 1 });
  const sheet = PAPER_LINEAR[INK_SET[surface].sheet];
  const sample = linearOf(hex);
  const needed = sheet.map(
    (s, c) =>
      Math.log(Math.max(s, MIN_CHANNEL)) -
      Math.log(Math.max(sample[c] ?? MIN_CHANNEL, MIN_CHANNEL)),
  );
  return plan.plates.map((plate) =>
    plate.separation.reduce((sum, r, c) => sum + r * (needed[c] ?? 0) * plan.gradeGamma, 0),
  );
}

/** The plate that carries the clay is the one with the most coverage on it. */
const carrier = (surface: keyof typeof INK_SET, hex: string) =>
  Math.max(...rawCoverage(surface, hex));

describe("ADR-0061 — the story run carries the model", () => {
  it("prints the clay's whole rendered range below full coverage", () => {
    // The defect, stated as the assertion that fails against it: ungraded, the
    // shadow tone solved at 1.27 and the body at 0.91, so the model's lower two
    // thirds printed as one solid.
    expect(carrier("story", CLAY.shadow)).toBeLessThan(1);
    expect(carrier("story", CLAY.body)).toBeLessThan(1);
    expect(carrier("story", CLAY.lit)).toBeLessThan(1);
  });

  it("keeps a real tonal ladder between lit, body and shadow", () => {
    const lit = carrier("story", CLAY.lit);
    const body = carrier("story", CLAY.body);
    const shadow = carrier("story", CLAY.shadow);

    expect(lit).toBeLessThan(body);
    expect(body).toBeLessThan(shadow);
    // A separation that merely stays off the clamp can still be a flat wash.
    // This is the number that makes it a picture: a third of the plate's range
    // spent across the subject, which is the shape a black-plate run gets free.
    expect(shadow - lit).toBeGreaterThan(0.3);
  });

  it("still prints a solid where the subject is genuinely black", () => {
    // The grade compresses the subject into the run's range; it does not lift
    // the shadows off the paper. A print with no solid in it is not a print.
    expect(carrier("story", "#0A0605")).toBeGreaterThanOrEqual(1);
  });
});

describe("ADR-0061 — gamma 1 is exactly the identity", () => {
  it("every run but the film is generated ungraded", () => {
    const graded = Object.entries(INK_SET)
      .filter(([, set]) => set.gradeGamma !== 1)
      .map(([name]) => name);
    // The ADR's central claim: this is one knob, turned once. If a second run
    // ever states a grade, that is a decision and it fails here until the ADR
    // says so.
    expect(graded).toEqual(["story"]);
  });

  it("leaves an ungraded run's separation untouched, bit for bit", () => {
    for (const surface of [
      "public",
      "citizen",
      "console-day",
      "console-night",
      "document",
    ] as const) {
      const plan = planPress({ surface, quality: "full", severity: "high", seed: 1 });
      expect(plan.gradeGamma).toBe(1);
      for (const plate of plan.plates) {
        // `needed · 1` is the term that was already there — not a rounding of
        // it. Multiplying by the plan's gamma must return the same double.
        for (const r of plate.separation) expect(r * plan.gradeGamma).toBe(r);
      }
    }
  });
});
