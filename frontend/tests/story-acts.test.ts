import { describe, expect, it } from "vitest";

import { ACTS, ACT_IDS, actAt, atAct, FILM_ACTS, localT } from "../src/story/acts.ts";

/**
 * §E16's table, asserted — F12.
 *
 * The `t` column of the blueprint's own table is transcribed into `acts.ts`,
 * and a transcription is exactly the kind of thing that is right on the day it
 * is written and wrong six months later. So the properties that make the film
 * coherent are checked here rather than trusted: the acts tile `[0,1]` with no
 * gap and no overlap, every position belongs to exactly one act, and the
 * boundaries are the numbers §E16 prints.
 *
 * A gap would be a `t` with no scene — a blank screen a reader can scroll to.
 * An overlap would be two scenes claiming the same frame, which is how a film
 * ends up playing two acts at once on one machine and one on another.
 */

/** §E16, transcribed a second time — deliberately, and from the blueprint
 *  rather than from `acts.ts`. A test that read its expectations out of the
 *  module under test would assert that the module equals itself. */
const BLUEPRINT: readonly (readonly [string, number, number])[] = [
  ["cold-open", 0.0, 0.05],
  ["walk", 0.05, 0.22],
  ["stop", 0.22, 0.32],
  ["silence", 0.32, 0.43],
  ["report", 0.43, 0.55],
  ["pipeline", 0.55, 0.7],
  ["merge", 0.7, 0.83],
  ["city-awake", 0.83, 0.93],
  ["table", 0.93, 1.0],
];

describe("§E16 — the nine acts", () => {
  it("is nine acts, and the ninth is not on the spine", () => {
    expect(ACT_IDS).toHaveLength(10);
    expect(ACTS).toHaveLength(10);
    // Ten ids and nine shots: §E16's Act 9 is *"below fold"* — deliberately
    // boring, deliberately not a scene — so it has no `t` and no camera.
    expect(FILM_ACTS).toHaveLength(9);
    expect(ACTS.at(-1)?.id).toBe("receipts");
    expect(ACTS.at(-1)?.range).toBeNull();
  });

  it("carries the blueprint's own boundaries", () => {
    for (const [id, from, to] of BLUEPRINT) {
      const found = FILM_ACTS.find((act) => act.id === id);
      expect(found, id).toBeDefined();
      expect(found?.range.from, `${id} from`).toBeCloseTo(from, 10);
      expect(found?.range.to, `${id} to`).toBeCloseTo(to, 10);
    }
  });

  it("tiles the spine with no gap and no overlap", () => {
    expect(FILM_ACTS[0]?.range.from).toBe(0);
    expect(FILM_ACTS.at(-1)?.range.to).toBe(1);
    for (let index = 1; index < FILM_ACTS.length; index += 1) {
      // The previous act's end *is* the next act's start. Not "close to": a
      // half-open interval that starts a hair after the last one ends is a gap,
      // and a gap in a scroll film is a frame of nothing.
      expect(FILM_ACTS[index]?.range.from).toBe(FILM_ACTS[index - 1]?.range.to);
    }
  });

  it("gives every position exactly one act, boundaries included", () => {
    for (const act of FILM_ACTS) {
      // The boundary belongs to the act that is starting.
      expect(actAt(act.range.from).id, `start of ${act.id}`).toBe(act.id);
      expect(actAt(act.range.to - 1e-9).id, `end of ${act.id}`).toBe(act.id);
    }
    // The film's own end, and past both edges — a rubber-banding browser hands
    // out positions outside `[0,1]` on every trackpad in the world.
    expect(actAt(1).id).toBe("table");
    expect(actAt(1.4).id).toBe("table");
    expect(actAt(-0.2).id).toBe("cold-open");
    expect(actAt(Number.NaN).id).toBe("cold-open");
  });

  it("normalises position within an act", () => {
    expect(localT(atAct("merge", 0))).toBeCloseTo(0, 6);
    expect(localT(atAct("merge", 0.5))).toBeCloseTo(0.5, 6);
    // Not 1: the act's closing boundary belongs to the next act, so a fraction
    // of exactly 1 is the first frame of `city-awake` and reads as 0 there.
    expect(localT(atAct("merge", 1))).toBeCloseTo(0, 6);
  });

  it("refuses to place an act that is not on the spine", () => {
    // Asking for `t` of the receipts is a category error — they are below the
    // fold — and it throws rather than answering 0, because answering would
    // put a golden image of act 9 at the cold open and nobody would notice.
    expect(() => atAct("receipts")).toThrow(/not on the spine/);
  });
});
