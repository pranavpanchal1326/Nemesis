/**
 * The camera track, as source — §E16, M9.4.
 *
 * §E16 gives the camera nine instructions in prose: *"locked side-tracking
 * shot"*, *"pushes to ankle height"*, *"the camera lifts and pans down the
 * road"*, *"pull back to the model at dusk"*, *"one last pull"*. This file is
 * those sentences in ENU metres, and it is the **only** place the film's camera
 * is authored.
 *
 * **Why the keyframes are TypeScript and the Theatre.js project state is
 * generated from them.** §E15 names `@theatre/core` + `@theatre/studio`, and
 * Theatre is what actually interpolates this at runtime — `camera.ts` builds a
 * real project, sets `sequence.position` from the spine's `t`, and reads the
 * values back. But Theatre's own authoring artefact is a project-state JSON
 * that studio writes: a flat map of generated ids and bezier handles, correct
 * and completely unreviewable. A camera move that lands wrong in a golden image
 * has to be *readable* to be fixed, and a diff of `"kf_7hd2": {"value": 1400}`
 * tells a reviewer nothing about which shot moved.
 *
 * So the pipeline is the one this repository already uses for every other
 * generated artefact — `design/tokens.json` → CSS custom properties and TSL
 * constants, `openapi.json` → the client, the two blueprints → §44: **one
 * authored source, a generator, and a drift check that fails CI.**
 * `scripts/generate-camera-track.ts` writes `generated/walk-camera.json` and
 * `--check` regenerates and diffs it, so the shipped Theatre state cannot
 * silently stop being what this file says.
 *
 * **Studio, therefore, is an inspector.** It is loaded dev-only by
 * `camera.ts` and it will show and scrub the real sequence; what it will not do
 * is persist an edit back here, and pretending otherwise would be claiming a
 * round trip that does not exist. A move found in studio is transcribed into
 * this file, which is the same discipline as every other generated artefact in
 * the repository.
 *
 * **The axes.** East and north in ground metres from the frame's origin
 * (`clay/projection.ts`), up in metres above the ground plane. Not three.js
 * axes: the conversion (north is −z) belongs to the one module that already
 * owns it, `clay/camera.ts`, and a film authored in renderer coordinates would
 * be a film that has to be re-authored if that convention ever changes.
 */

import { SPINE } from "./spine";
import { atAct, type ActId } from "./acts";

/** Where the camera is and what it is looking at, in ENU metres. */
export interface CameraPose {
  readonly eyeEast: number;
  readonly eyeNorth: number;
  readonly eyeUp: number;
  readonly targetEast: number;
  readonly targetNorth: number;
  readonly targetUp: number;
  /**
   * The distance the tilt-shift's focal plane sits at (§E7.3). Normally the
   * rig's own subject distance; the film overrides it because Act 2's rack
   * focus and Act 6's snap-to-miniature are camera moves rather than
   * selections.
   */
  readonly focusMetres: number;
  /**
   * How deep the plane of focus is, in metres. Small is a miniature — §E16 Act
   * 6's *"tilt-shift snaps it to miniature"* is this number falling, and
   * nothing else. `LENS.tiltShift.apertureMetres` is the engine's resting
   * value and the film returns to it.
   */
  readonly apertureMetres: number;
}

/** The eight numbers above, as the prop names the Theatre object carries. */
export const POSE_PROPS = [
  "eyeEast",
  "eyeNorth",
  "eyeUp",
  "targetEast",
  "targetNorth",
  "targetUp",
  "focusMetres",
  "apertureMetres",
] as const satisfies readonly (keyof CameraPose)[];

export type PoseProp = (typeof POSE_PROPS)[number];

export interface CameraKey {
  /** Where on the spine, `[0,1]`. */
  readonly at: number;
  /** Which shot this is, for the generated file's debug names and for the
   *  reviewer reading a diff. Never used at runtime. */
  readonly shot: string;
  readonly pose: CameraPose;
}

/** Half the walk, either side of the origin — Act 1 crosses the frame's centre
 *  so the pothole in Act 2 is at the origin and every later act can pull back
 *  from the same place. */
const HALF_WALK = SPINE.walkMetres / 2;

/** The default depth of field: the engine's own resting aperture. Named so the
 *  three keys that deliberately narrow it read as departures. */
const OPEN = 44;

/** Act 6's miniature. A quarter of the resting aperture is the number that
 *  makes a 900 m-high shot read as a table-top model rather than as an
 *  aerial photograph — the whole point of the shot §E16 calls THE SHOT. */
const MINIATURE = 11;

function key(shot: string, at: number, pose: CameraPose): CameraKey {
  return { shot, at, pose };
}

/**
 * The film, shot by shot.
 *
 * One key per act boundary, plus the interior keys a shot needs to be a move
 * rather than a cut. Theatre interpolates between them on `--ease-cine`, which
 * is the token the rest of the product already uses for a camera-weight move
 * (§E11); the generator reads it from `design/tokens.json` rather than
 * restating it here, so the film and the chrome ease identically.
 */
export const CAMERA_KEYS: readonly CameraKey[] = [
  // Act 0 · Cold open — black, grain, distant city. The camera is a long way
  // out and slightly above the skyline, so what little is visible through the
  // grain reads as a city seen from a road into it.
  key("cold-open · distant city", atAct("cold-open", 0), {
    eyeEast: 0,
    eyeNorth: -4200,
    eyeUp: 1400,
    targetEast: 0,
    targetNorth: 0,
    targetUp: 60,
    focusMetres: 4400,
    apertureMetres: OPEN,
  }),

  // Act 1 · The walk — a locked side-tracking shot. The camera holds its
  // offset from the figure exactly (same north, same height, same distance)
  // and translates east with it. "Locked" is the whole instruction: any change
  // of height or distance across this act would turn a tracking shot into a
  // crane move, and the beat is meant to be the most ordinary shot in the film.
  key("walk · enters", atAct("walk", 0), {
    eyeEast: -HALF_WALK,
    eyeNorth: -150,
    eyeUp: 46,
    targetEast: -HALF_WALK,
    targetNorth: 0,
    targetUp: 9,
    focusMetres: 150,
    apertureMetres: OPEN,
  }),
  key("walk · tracks", atAct("walk", 1), {
    eyeEast: HALF_WALK,
    eyeNorth: -150,
    eyeUp: 46,
    targetEast: HALF_WALK,
    targetNorth: 0,
    targetUp: 9,
    focusMetres: 150,
    apertureMetres: OPEN,
  }),

  // Act 2 · The stop — "the camera pushes to ankle height. The pothole fills
  // the lower third. Rack focus." The push is the eye dropping from 46 m to
  // 2.4 m while closing to nine metres out; the rack is `focusMetres` falling
  // with it and the aperture narrowing, which is what a rack focus physically
  // is rather than a blur that fades in.
  key("stop · ankle height", atAct("stop", 1), {
    eyeEast: HALF_WALK,
    eyeNorth: -9,
    eyeUp: 2.4,
    targetEast: HALF_WALK,
    targetNorth: 0,
    targetUp: 0.4,
    focusMetres: 9,
    apertureMetres: 6,
  }),

  // Act 3 · The silence — "the camera lifts and pans down the road." Lifts:
  // 2.4 m to 130 m. Pans: the target runs east along the road the nine ghost
  // flags stand on, so the flags arrive in shot one after another rather than
  // all at once in a frame that was already looking at them.
  key("silence · lifts", atAct("silence", 0.45), {
    eyeEast: HALF_WALK,
    eyeNorth: -220,
    eyeUp: 130,
    targetEast: HALF_WALK * 0.4,
    targetNorth: 0,
    targetUp: 6,
    focusMetres: 260,
    apertureMetres: OPEN,
  }),
  key("silence · pans down the road", atAct("silence", 1), {
    eyeEast: HALF_WALK * 0.25,
    eyeNorth: -240,
    eyeUp: 130,
    targetEast: -HALF_WALK,
    targetNorth: 0,
    targetUp: 6,
    focusMetres: 300,
    apertureMetres: OPEN,
  }),

  // Act 4 · The report — "the figure raises a phone; the camera pushes through
  // the screen." The push ends *inside* the phone: 1.6 m up, 1.1 m out, focused
  // at arm's length with the aperture almost shut. The DOM takes the frame from
  // here (`acts/TheReport.tsx`) and the camera is behind a real
  // `<ReportCapture>` for the rest of the act — so this key is where the clay
  // stops being the picture, and its aperture is what dissolves it.
  key("report · raises the phone", atAct("report", 0.34), {
    eyeEast: HALF_WALK,
    eyeNorth: -14,
    eyeUp: 14,
    targetEast: HALF_WALK,
    targetNorth: 0,
    targetUp: 11,
    focusMetres: 14,
    apertureMetres: 9,
  }),
  key("report · through the screen", atAct("report", 1), {
    eyeEast: HALF_WALK,
    eyeNorth: -1.1,
    eyeUp: 11.6,
    targetEast: HALF_WALK,
    targetNorth: 0.9,
    targetUp: 11.4,
    focusMetres: 2,
    apertureMetres: 2.5,
  }),

  // Act 5 · The pipeline — the card travels through physical gates on a table.
  // The camera is back outside, low and square onto the run of gates, and it
  // holds: the movement in this act belongs to the card, and a camera that
  // drifted through it would make five stamps read as one continuous motion.
  key("pipeline · the table", atAct("pipeline", 0.12), {
    eyeEast: HALF_WALK,
    eyeNorth: -190,
    eyeUp: 96,
    targetEast: HALF_WALK,
    targetNorth: 0,
    targetUp: 8,
    focusMetres: 210,
    apertureMetres: 26,
  }),
  key("pipeline · holds", atAct("pipeline", 1), {
    eyeEast: HALF_WALK,
    eyeNorth: -190,
    eyeUp: 96,
    targetEast: HALF_WALK,
    targetNorth: 0,
    targetUp: 8,
    focusMetres: 210,
    apertureMetres: 26,
  }),

  // Act 6 · THE SHOT — "pull back to the model at dusk; tilt-shift snaps it to
  // miniature." The pull is 96 m to 900 m — `WORLD.camera.heightMetres`, the
  // engine's own resting altitude, so the film arrives at exactly the shot the
  // console and the public map already draw. The snap is `apertureMetres`
  // falling to a quarter of resting, which is the entire miniature effect.
  key("merge · pulls back to dusk", atAct("merge", 0.55), {
    eyeEast: 0,
    eyeNorth: -700,
    eyeUp: 900,
    targetEast: 0,
    targetNorth: 0,
    targetUp: 0,
    focusMetres: 1140,
    apertureMetres: MINIATURE,
  }),
  key("merge · holds on the merge", atAct("merge", 1), {
    eyeEast: 0,
    eyeNorth: -700,
    eyeUp: 900,
    targetEast: 0,
    targetNorth: 0,
    targetUp: 0,
    focusMetres: 1140,
    apertureMetres: MINIATURE,
  }),

  // Act 7 · The city awake — the survey frame draws in and the film becomes
  // the public dashboard. The camera opens its aperture back to resting,
  // because a survey document is not a miniature: it is a drawing, and
  // everything on a drawing is in focus.
  key("city-awake · the survey frame", atAct("city-awake", 1), {
    eyeEast: 0,
    eyeNorth: -1500,
    eyeUp: 1500,
    targetEast: 0,
    targetNorth: 0,
    targetUp: 0,
    focusMetres: 2120,
    apertureMetres: OPEN,
  }),

  // Act 8 · The table — "one last pull. The model is revealed on a workbench."
  // The final frame is the console's establishing shot, so the camera ends
  // where a photograph of a work surface would be taken from: further back,
  // higher, and looking slightly down-frame at a model that is now an object
  // among other objects.
  key("table · the workbench", atAct("table", 1), {
    eyeEast: 0,
    eyeNorth: -3400,
    eyeUp: 2600,
    targetEast: 0,
    targetNorth: 260,
    targetUp: 0,
    focusMetres: 4300,
    apertureMetres: 30,
  }),
];

/** The first key at or after an act's start. A convenience for the proof route
 *  and for the reviewer: `shotOf("merge")` names the shot a golden image is of. */
export function shotOf(id: ActId): string {
  const from = atAct(id, 0);
  const found = CAMERA_KEYS.find((candidate) => candidate.at >= from);
  return found?.shot ?? "";
}
