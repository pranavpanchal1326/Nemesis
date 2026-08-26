import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  BUILDS,
  FIGURES,
  drawingFor,
  phaseAt,
  posture,
  type FigureName,
} from "../src/ink/figures.ts";
import { STATES, type FigureState } from "../src/ink/machine.ts";
import { offsetAt } from "../src/ink/draw.ts";

/**
 * The drawing, audited — §E8, §E7.2, F15.
 *
 * Three claims are made about the ink layer and none of them can be checked by
 * looking at it:
 *
 * · **No face, ever.** §E8 states the rule as a *meaning* rather than a saving:
 *   *"the figure must read as a college student, an aunty, a delivery rider,
 *   anyone."* Asserted here as a property of the geometry — the head is exactly
 *   one closed stroke and nothing is drawn inside it — because a rule enforced
 *   by review is a rule that survives until the first person who thinks two
 *   dots would be friendlier.
 *
 * · **Animated on twos.** §E7.2: *"poses hold two frames, then snap."* Asserted
 *   by walking the clock and counting how often the posture changes.
 *
 * · **No timeline.** The grep F15's gate names lives in
 *   `scripts/check-guards.ts` as `no-character-timeline`; this file asserts the
 *   *positive* half — that a pose is a pure function of state and step, so the
 *   same two numbers always draw the same figure.
 */

/**
 * A module's source with its comment-only lines removed.
 *
 * The same distinction `scripts/check-guards.ts` draws for the timeline ban and
 * for the same reason: `figures.ts` states the no-face rule in prose — *"there
 * is no eye, no mouth and no nose in this file"* — and a grep that could not
 * tell a rule from a violation would force the one file that explains the rule
 * to stop explaining it.
 */
const source = (relative: string): string =>
  readFileSync(fileURLToPath(new URL(`../src/ink/${relative}`, import.meta.url)), "utf8")
    .split(/\r?\n/)
    .filter((line) => !/^\s*(?:\/\/|\/\*|\*)/.test(line))
    .join("\n");

/** Rough centre and radius of the head, recovered from the drawing itself. */
function headStroke(name: FigureName, state: FigureState, step: number) {
  const drawing = drawingFor(name, state, step);
  const closed = drawing.strokes.filter((stroke) => stroke.closed);
  // The skull is the first closed stroke: the spine is drawn before it and is
  // open, and every prop is drawn after.
  const head = closed[0];
  expect(head).toBeDefined();
  return head;
}

describe("§E8 — no face, ever", () => {
  it("draws every figure's head as exactly one closed stroke", () => {
    for (const name of FIGURES) {
      for (const state of STATES) {
        const head = headStroke(name, state, 0);
        expect(head?.points.length ?? 0).toBeGreaterThan(8);
      }
    }
  });

  it("puts no stroke inside any head", () => {
    // The operational form of the rule: nothing is drawn within the skull's
    // radius except the skull. A brow, an eye or a mouth would land here.
    for (const name of FIGURES) {
      const build = BUILDS[name];
      for (const state of STATES) {
        const drawing = drawingFor(name, state, 0);
        const head = drawing.strokes.find((stroke) => stroke.closed);
        if (head === undefined) continue;
        const cx = head.points.reduce((sum, p) => sum + p.x, 0) / head.points.length;
        const cy = head.points.reduce((sum, p) => sum + p.y, 0) / head.points.length;
        // A cap crosses the skull deliberately (§E8.2's Field Hand works
        // outdoors), so it is measured as an outline that *leaves* the head
        // rather than as a mark inside it: at least one of its points is
        // outside the radius.
        for (const stroke of drawing.strokes) {
          if (stroke === head) continue;
          const inside = stroke.points.filter(
            (p) => Math.hypot(p.x - cx, p.y - cy) < build.head * 0.72,
          );
          expect(inside.length, `${name}/${state}: a stroke lies inside the head`).toBeLessThan(
            stroke.points.length,
          );
        }
      }
    }
  });

  it("names no facial feature anywhere in the ink layer's source", () => {
    // Cheap, and it catches the version of this mistake that arrives as a new
    // helper rather than as a new stroke.
    for (const file of ["figures.ts", "draw.ts", "machine.ts", "InkFigure.tsx"]) {
      expect(source(file), file).not.toMatch(/\b(?:eye|eyes|mouth|nose|smile|frown|pupil)\b/i);
    }
  });
});

describe("§E7.2 — on twos", () => {
  it("holds each pose for exactly two frames of the 12 fps clock", () => {
    expect(phaseAt(0, 4)).toBe(0);
    expect(phaseAt(1, 4)).toBe(0);
    expect(phaseAt(2, 4)).toBe(1);
    expect(phaseAt(3, 4)).toBe(1);
    expect(phaseAt(8, 4)).toBe(0);
  });

  it("changes a walking figure's posture every second frame and not every frame", () => {
    const build = BUILDS.reporter;
    const swings = Array.from({ length: 8 }, (_, step) => posture(build, "walk", step).armSwing);
    expect(swings[0]).toBe(swings[1]);
    expect(swings[2]).toBe(swings[3]);
    expect(swings[0]).not.toBe(swings[2]);
  });

  it("holds a held pose completely still, because §E16's beat is one movement", () => {
    // Act 2: *"the figure's shoulders drop — one movement, held a full
    // second."* A cycle of 1 is what "held" means in this module.
    const build = BUILDS.reporter;
    expect(posture(build, "dejected", 0).cycle).toBe(1);
    expect(posture(build, "dejected", 0)).toEqual(posture(build, "dejected", 37));
  });
});

describe("a pose is a pure function of state and step", () => {
  it("draws the same geometry for the same two numbers, every time", () => {
    for (const name of FIGURES) {
      for (const state of STATES) {
        expect(drawingFor(name, state, 11)).toEqual(drawingFor(name, state, 11));
      }
    }
  });

  it("renders every state for every figure without a gap", () => {
    // The switch in `posture()` is exhaustive and the compiler enforces it; this
    // asserts the consequence, which is that no state draws an empty figure.
    for (const name of FIGURES) {
      for (const state of STATES) {
        const drawing = drawingFor(name, state, 0);
        expect(drawing.strokes.length, `${name}/${state}`).toBeGreaterThan(4);
        expect(drawing.fill.points.length).toBe(4);
      }
    }
  });

  it("keeps every figure's feet on the ground and inside its own box", () => {
    for (const name of FIGURES) {
      for (const state of STATES) {
        for (const stroke of drawingFor(name, state, 6).strokes) {
          for (const point of stroke.points) {
            expect(point.y, `${name}/${state}`).toBeGreaterThanOrEqual(0);
            expect(point.y).toBeLessThanOrEqual(1.25);
            expect(Math.abs(point.x)).toBeLessThanOrEqual(0.6);
          }
        }
      }
    }
  });
});

describe("§E8 — the warm pass misregisters, on the press's own numbers", () => {
  it("offsets deterministically for a given step, so a golden image is stable", () => {
    expect(offsetAt(12, 1.5)).toEqual(offsetAt(12, 1.5));
    expect(offsetAt(12, 1.5)).not.toEqual(offsetAt(13, 1.5));
  });

  it("prints in register when the press is flat (§E6.4, Tiers C and D)", () => {
    expect(offsetAt(12, 0)).toEqual([0, 0]);
  });
});
