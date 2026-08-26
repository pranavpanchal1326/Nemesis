import { describe, expect, it } from "vitest";

import { atAct } from "../src/story/acts.ts";
import {
  advanceT,
  SPINE,
  spineStateAt,
  StorySpine,
  walkMetres,
  type ScrollProxy,
} from "../src/story/spine.ts";

/**
 * The spine — F12's gate, in the only place it can be asserted honestly.
 *
 * > **Gate:** golden image per act at fixed `t`, seed and camera. **Stopping
 * > the scroll stops the walk — asserted, because it is the property that makes
 * > this a walk rather than a video.**
 *
 * The golden half is `tests/story.spec.ts`, which needs a browser. This half is
 * the property, and it is a *unit* test on purpose: "stopping the scroll stops
 * the walk" is a statement about the relationship between two functions, and
 * asserting it by driving a real page would test the browser's scroll
 * restoration as much as the film.
 */

/** A scroll position a test can move by hand. */
function proxy(at: { value: number }): ScrollProxy {
  return {
    progress: () => at.value,
    destroy: () => undefined,
  };
}

describe("§E16 — the spine", () => {
  it("converts Lenis' per-frame lerp into a per-second rate", () => {
    // The property, not the number: closing `lerp` per frame for one second of
    // 60 Hz frames must equal closing `ratePerSecond` once. A hand-typed
    // constant would agree with this on the day it was typed.
    let byFrame = 0;
    for (let frame = 0; frame < SPINE.referenceHz; frame += 1) {
      byFrame += (1 - byFrame) * SPINE.lerp;
    }
    const bySecond = 1 - Math.exp(-SPINE.ratePerSecond);
    expect(bySecond).toBeCloseTo(byFrame, 6);
  });

  it("damps identically at 60 Hz and at 144 Hz", () => {
    // The reason the conversion exists. A film whose camera moved further per
    // second on a faster display would make every golden image a property of
    // the runner's refresh rate.
    const run = (hz: number): number => {
      let t = 0;
      for (let frame = 0; frame < hz; frame += 1) t = advanceT(t, 1, 1 / hz);
      return t;
    };
    expect(run(144)).toBeCloseTo(run(60), 4);
    expect(run(30)).toBeCloseTo(run(60), 4);
  });

  it("arrives rather than approaching forever", () => {
    // Without the deadband a damped proxy is asymptotic, and the film would
    // report a walk that advances by a millionth of a metre per frame for as
    // long as the tab is open.
    let t = 0;
    for (let frame = 0; frame < 600; frame += 1) t = advanceT(t, 1, 1 / 60);
    expect(t).toBe(1);
  });

  it("clamps an absurd frame time instead of teleporting", () => {
    // The first frame after a backgrounded tab is minutes long.
    const oneHour = advanceT(0, 1, 3600);
    const quarterSecond = advanceT(0, 1, 0.25);
    expect(oneHour).toBe(quarterSecond);
  });

  it("makes the walk a pure function of position and nothing else", () => {
    // Called twice with the same `t` and a whole test suite in between; if any
    // time term crept in, these would differ.
    const first = walkMetres(atAct("walk", 0.5));
    const second = walkMetres(atAct("walk", 0.5));
    expect(first).toBe(second);
    expect(first).toBeCloseTo(SPINE.walkMetres / 2, 6);
  });

  it("stops the walk where §E16 stops it", () => {
    // Nothing before Act 1, the whole distance across it, and then the figure
    // stands still — Acts 2 and 3 are the stop and the silence, and a figure
    // still strolling through them would undo the disappointment beat.
    expect(walkMetres(atAct("cold-open", 0.5))).toBe(0);
    expect(walkMetres(atAct("walk", 0))).toBe(0);
    expect(walkMetres(atAct("walk", 1) - 1e-9)).toBeCloseTo(SPINE.walkMetres, 3);
    expect(walkMetres(atAct("stop", 0.5))).toBe(SPINE.walkMetres);
    expect(walkMetres(atAct("table", 1))).toBe(SPINE.walkMetres);
  });

  it("stops the walk when the scroll stops — the gate", () => {
    const at = { value: 0 };
    const spine = new StorySpine();
    spine.attach(proxy(at));

    // Scroll to the middle of the walk and let the damping settle.
    at.value = atAct("walk", 0.5);
    for (let frame = 0; frame < 600; frame += 1) spine.step(1 / 60);

    const settled = spine.state.walked;
    expect(settled).toBeGreaterThan(0);

    // Now stop scrolling — the proxy does not move — and run a further ten
    // seconds of frames. A film that advanced on time rather than on distance
    // would walk the rest of the road here.
    for (let frame = 0; frame < 600; frame += 1) spine.step(1 / 60);
    expect(spine.state.walked).toBe(settled);
    expect(spine.state.moving).toBe(false);

    spine.detach();
  });

  it("holds exactly where it is seeked, ignoring the proxy", () => {
    // What the proof route relies on: a pinned spine is a fixed frame, or the
    // golden image is a photograph of an easing curve.
    const at = { value: 0.1 };
    const spine = new StorySpine();
    spine.attach(proxy(at));
    spine.seek(atAct("merge", 0.55));

    const pinned = spine.state.t;
    at.value = 0.99;
    for (let frame = 0; frame < 120; frame += 1) spine.step(1 / 60);

    expect(spine.state.t).toBe(pinned);
    expect(spine.state.act.id).toBe("merge");
    spine.detach();
  });

  it("dresses a listener for where the reader already is", () => {
    // A scene that mounts mid-film must not animate in from the top of the
    // page. The subscription fires immediately with current state.
    const spine = new StorySpine();
    spine.seek(atAct("table", 0.5));
    let seen: string | null = null;
    const off = spine.subscribe((state) => {
      seen = state.act.id;
    });
    expect(seen).toBe("table");
    off();
  });

  it("describes a position without a scroll container at all", () => {
    const state = spineStateAt(atAct("pipeline", 0.25));
    expect(state.act.id).toBe("pipeline");
    expect(state.local).toBeCloseTo(0.25, 6);
    expect(state.walked).toBe(SPINE.walkMetres);
  });
});
