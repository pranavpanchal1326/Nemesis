import { describe, expect, it } from "vitest";

import { controlsOf } from "../src/lib/easing.ts";
import { atAct } from "../src/story/acts.ts";
import { CAMERA_KEYS, POSE_PROPS } from "../src/story/camera-keys.ts";
import { poseAt, RESTING_POSE } from "../src/story/camera.ts";
import track from "../src/story/generated/walk-camera.json" with { type: "json" };

/**
 * The camera — M9.4, and the drift check's other half.
 *
 * `npm run camera:check` asserts that the generated Theatre state *is* what
 * `camera-keys.ts` says. This asserts the thing that matters one level up: that
 * loading that state into `@theatre/core` and asking it for a position
 * reproduces the shot that was authored. A generated file can be perfectly
 * in sync with its source and still be the wrong shape for the library reading
 * it, and the failure mode is silent — Theatre yields the static defaults and
 * the camera sits at its resting pose for the entire film.
 *
 * This runs in Node, with no DOM, because Theatre's core is an evaluation graph
 * rather than a renderer. That is the whole reason `cameraTrack()` carries no
 * `typeof window` guard.
 */

interface Keyframe {
  readonly position: number;
  readonly value: number;
  readonly handles: readonly number[];
}

function keyframes(prop: string): readonly Keyframe[] {
  const tracks = track.sheetsById.Walk.sequence.tracksByObject.Camera;
  const id = (tracks.trackIdByPropPath as Record<string, string>)[JSON.stringify([prop])];
  expect(id, `no track for ${prop}`).toBeDefined();
  const data = (tracks.trackData as Record<string, { keyframes: Keyframe[] }>)[id ?? ""];
  return data?.keyframes ?? [];
}

describe("§E16 — the Theatre camera track", () => {
  it("carries a track for every prop the film reads", () => {
    for (const prop of POSE_PROPS) {
      expect(keyframes(prop), prop).toHaveLength(CAMERA_KEYS.length);
    }
  });

  it("eases on the product's own cine curve", () => {
    // `cubic-bezier(x1, y1, x2, y2)` maps to `[x2, y2, x1, y1]`: the second
    // control point is the one arriving at a keyframe, the first is the one
    // leaving it. Reversed, the film would ease subtly wrong everywhere and
    // obviously wrong nowhere — the generator's note says so, and this is what
    // makes the note load-bearing.
    const cine = controlsOf("cine");
    for (const keyframe of keyframes("eyeUp")) {
      expect(keyframe.handles).toEqual([cine.x2, cine.y2, cine.x1, cine.y1]);
    }
  });

  it("actually interpolates — it does not sit at its defaults", () => {
    // The silent failure this file exists for. If the state's shape is wrong,
    // every read is `RESTING_POSE` and the film has no camera.
    const cold = poseAt(atAct("cold-open", 0));
    const bench = poseAt(atAct("table", 1));
    expect(cold.eyeUp).not.toBeCloseTo(bench.eyeUp, 3);
    expect(bench).not.toEqual(RESTING_POSE);
  });

  it("lands on the authored pose at every keyed position", () => {
    for (const key of CAMERA_KEYS) {
      const pose = poseAt(key.at);
      for (const prop of POSE_PROPS) {
        // Three decimals of a metre. Theatre quantises positions to
        // `subUnitsPerUnit`, so a keyed position is reproduced to well within a
        // millimetre — and a millimetre of camera at 900 m is nothing.
        expect(pose[prop], `${key.shot} · ${prop}`).toBeCloseTo(key.pose[prop], 3);
      }
    }
  });

  it("holds the shots §E16 says are locked", () => {
    // "Locked side-tracking shot": the camera keeps its offset from the figure
    // exactly across Act 1. Height or distance drifting would turn a tracking
    // shot into a crane move, which is a different beat entirely.
    const early = poseAt(atAct("walk", 0.1));
    const late = poseAt(atAct("walk", 0.9));
    expect(early.eyeUp).toBeCloseTo(late.eyeUp, 6);
    expect(early.eyeNorth).toBeCloseTo(late.eyeNorth, 6);
    expect(late.eyeEast).toBeGreaterThan(early.eyeEast);
    // And the offset from the subject is constant, which is what "locked"
    // means and what the two assertions above only imply.
    expect(early.eyeEast - early.targetEast).toBeCloseTo(late.eyeEast - late.targetEast, 6);
  });

  it("pushes to ankle height for the stop", () => {
    // §E16 Act 2. The number that makes the beat work is the eye height, and
    // an act that quietly drifted back up to a establishing shot would lose
    // the whole disappointment beat without failing anything else.
    const stop = poseAt(atAct("stop", 1));
    expect(stop.eyeUp).toBeLessThan(4);
    expect(stop.focusMetres).toBeLessThan(20);
  });

  it("snaps the city to a miniature for THE SHOT, and opens up again after", () => {
    // §E16 Act 6: *"tilt-shift snaps it to miniature"* — the aperture falling
    // is the entire effect. Act 7 is a survey drawing, and everything on a
    // drawing is in focus.
    const merge = poseAt(atAct("merge", 1));
    const survey = poseAt(atAct("city-awake", 1));
    expect(merge.apertureMetres).toBeLessThan(survey.apertureMetres / 2);
    expect(merge.eyeUp).toBeGreaterThan(500);
  });

  it("ends where the console's establishing shot begins", () => {
    // §E16 Act 8: *"the film's last frame is the console's establishing
    // shot."* The camera pulls back past the engine's resting altitude onto
    // the bench.
    const bench = poseAt(1);
    expect(bench.eyeUp).toBeGreaterThan(poseAt(atAct("city-awake", 1)).eyeUp);
  });
});
