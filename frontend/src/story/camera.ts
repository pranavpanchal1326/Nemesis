"use client";

import { getProject, onChange, types, val, type ISheetObject } from "@theatre/core";

import { LENS, WORLD } from "@/design/generated/tokens";

import { POSE_PROPS, type CameraPose } from "./camera-keys";
import state from "./generated/walk-camera.json";

/**
 * The Theatre.js camera — §E16, M9.4.
 *
 * > `t` drives a Theatre.js camera and uniform sequence.
 *
 * This is that sentence, and the whole of the module is three moves: build the
 * project from the generated state, set `sequence.position` to the spine's `t`,
 * read the eight numbers back. Theatre does the interpolation, which is the
 * reason it is here at all — a sequencer with a real keyframe model, bezier
 * segments and a studio that can scrub them is worth considerably more than the
 * forty lines of lerp it replaces, and §E15 named it before any of this existed.
 *
 * **Read synchronously with `val()`, not through `onChange`.** Theatre's
 * subscription path is scheduled, and a camera that received its pose a
 * microtask after the frame that asked for it would trail the type by one frame
 * — the exact artefact the damped spine exists to avoid. So the film sets the
 * position and reads the values in the same statement, inside its own
 * `requestAnimationFrame`. `onChange` is still subscribed once and deliberately
 * does nothing: Theatre's values are lazily computed and a prop nobody is
 * listening to stays *cold*, returning its static default forever. That is not
 * a bug and it is not a workaround — it is the library's evaluation model, and
 * discovering it by watching a camera sit at the origin for a whole film is a
 * morning nobody needs to spend twice.
 *
 * **One project per document.** `getProject()` is keyed by name and a second
 * call with a different state is an error inside Theatre rather than a second
 * project. React's development double-render would do exactly that, so the
 * project is a module singleton and the film asks for it rather than owning it.
 */

/** Names shared with `scripts/generate-camera-track.ts`. They must agree with
 *  the generated file's keys or Theatre silently yields a sequence with no
 *  tracks — see the note on `PROJECT_NAME` in the generator. */
const PROJECT_NAME = "NEMESIS · The Walk";
const SHEET_NAME = "Walk";
const OBJECT_NAME = "Camera";

/**
 * The pose the film starts from, and the pose it falls back to.
 *
 * `WORLD.camera` is the engine's own resting shot — the one the console and the
 * public map already draw — so a film whose track failed to load renders the
 * product's normal establishing frame rather than a camera at the origin
 * looking at the inside of the ground plane.
 */
export const RESTING_POSE: CameraPose = {
  eyeEast: 0,
  eyeNorth: -(WORLD.camera.heightMetres / Math.tan((WORLD.camera.pitchDegrees * Math.PI) / 180)),
  eyeUp: WORLD.camera.heightMetres,
  targetEast: 0,
  targetNorth: 0,
  targetUp: 0,
  focusMetres: WORLD.camera.heightMetres,
  apertureMetres: LENS.tiltShift.apertureMetres,
};

type CameraObject = ISheetObject<{
  readonly [K in keyof CameraPose]: ReturnType<typeof types.number>;
}>;

interface Track {
  readonly seek: (t: number) => CameraPose;
  /** The sheet, for the dev-only studio. Nothing in the product reads it. */
  readonly sheet: ReturnType<ReturnType<typeof getProject>["sheet"]>;
}

let track: Track | null = null;

/**
 * The camera track, built once.
 *
 * **Not guarded on `window`,** deliberately. Theatre's core is a plain
 * evaluation graph with no DOM in it — `tests/story-camera.test.ts` builds this
 * track in Node and reads poses out of it — and a `typeof window` guard here
 * would make the film's camera the one part of the product that can only be
 * asserted in a browser. Nothing calls this during a server render anyway: the
 * only caller is the spine's subscription, inside an effect.
 *
 * Returns `null` if Theatre refuses the state. That is a real possibility with
 * a generated artefact and a versioned schema, and the film's answer to it is
 * `RESTING_POSE` — the product's own establishing shot — rather than a blank
 * canvas or a thrown error on the landing page.
 */
export function cameraTrack(): Track | null {
  if (track !== null) return track;

  const project = getProject(PROJECT_NAME, { state });
  const sheet = project.sheet(SHEET_NAME);

  const props = Object.fromEntries(
    POSE_PROPS.map((prop) => [prop, types.number(RESTING_POSE[prop])]),
  ) as { readonly [K in keyof CameraPose]: ReturnType<typeof types.number> };

  const object = sheet.object(OBJECT_NAME, props) as CameraObject;

  // The subscription that makes the props hot. See the module note — without a
  // listener Theatre never computes the sequence and every read is the static
  // default. Kept for the lifetime of the document on purpose: the film is the
  // document, and unsubscribing would silently freeze the camera.
  onChange(object.props, () => {
    /* the value is read synchronously in `seek`; this only keeps it warm */
  });

  track = {
    sheet,
    seek: (t) => {
      sheet.sequence.position = t;
      return readPose(object);
    },
  };
  return track;
}

function readPose(object: CameraObject): CameraPose {
  const current = val(object.props) as Record<string, number>;
  return {
    eyeEast: current["eyeEast"] ?? RESTING_POSE.eyeEast,
    eyeNorth: current["eyeNorth"] ?? RESTING_POSE.eyeNorth,
    eyeUp: current["eyeUp"] ?? RESTING_POSE.eyeUp,
    targetEast: current["targetEast"] ?? RESTING_POSE.targetEast,
    targetNorth: current["targetNorth"] ?? RESTING_POSE.targetNorth,
    targetUp: current["targetUp"] ?? RESTING_POSE.targetUp,
    focusMetres: current["focusMetres"] ?? RESTING_POSE.focusMetres,
    apertureMetres: current["apertureMetres"] ?? RESTING_POSE.apertureMetres,
  };
}

/** The pose at `t`, or the resting pose where there is no track. The one call
 *  the film makes per frame. */
export function poseAt(t: number): CameraPose {
  return cameraTrack()?.seek(t) ?? RESTING_POSE;
}

/**
 * Theatre's studio, dev-only and opt-in — §E15, and §E24's rule that a proof
 * surface is not a public URL.
 *
 * Opt-in rather than always-on in development because studio installs a
 * full-screen editor over the page, and the film is also what a developer looks
 * at while building an *act*. `?studio=1` on the story route or the proof route
 * brings it up.
 *
 * It is an inspector here, not an authoring round trip: the shipped track is
 * generated from `camera-keys.ts` and studio cannot write back to it. That is
 * stated in the keys file and it is the reason this function does not offer to
 * save.
 */
export async function openStudio(): Promise<void> {
  if (process.env.NODE_ENV === "production") return;
  const studio = (await import("@theatre/studio")).default;
  studio.initialize();
}
