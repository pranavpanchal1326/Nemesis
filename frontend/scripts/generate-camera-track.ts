/**
 * The camera track — §E16, M9.4, and the same pipeline every other generated
 * artefact in this repository uses.
 *
 * One source (`src/story/camera-keys.ts`) generates one artefact:
 *
 *   src/story/generated/walk-camera.json   a Theatre.js project state, which
 *                                          `@theatre/core` loads and
 *                                          interpolates at runtime
 *
 * Theatre's own authoring artefact is this JSON, written by studio: generated
 * keyframe ids, raw bezier handles, no shot names. It is correct and it is
 * unreviewable, and a camera move that lands wrong in a golden image has to be
 * readable to be fixed. So the keys are authored in TypeScript with the shot
 * each one belongs to written beside it, and this script turns them into the
 * shape Theatre wants — exactly as `generate-tokens.ts` turns one JSON file
 * into a stylesheet and a shader constant.
 *
 * The easing comes from `design/tokens.json` through `controlsOf("cine")`, so
 * the film's camera and the product's chrome ease on the same curve by
 * construction rather than by two people typing the same four numbers.
 *
 * Usage:
 *   node scripts/generate-camera-track.ts            write
 *   node scripts/generate-camera-track.ts --check    fail if the output is stale
 */

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { createJiti } from "jiti";
import { format } from "prettier";

const ROOT = join(fileURLToPath(new URL(".", import.meta.url)), "..");
const SOURCE = join(ROOT, "src", "story", "camera-keys.ts");
const OUT_DIR = join(ROOT, "src", "story", "generated");
const OUT = join(OUT_DIR, "walk-camera.json");

/**
 * The application's own modules, loaded with the `@/` alias resolved.
 *
 * `node` runs this file directly and strips its types, which is what the other
 * generators rely on — but they read JSON. This one reads *source*, and source
 * in this project imports through the TypeScript path alias. `jiti` is already
 * a dependency (Next uses it for the config) and resolving the alias here is
 * three lines; the alternative was to make `camera-keys.ts` the one module in
 * `src/` that imports relatively, which trades a readable generator for an
 * unexplainable inconsistency in application code.
 */
const jiti = createJiti(import.meta.url, { alias: { "@": join(ROOT, "src") } });

type CameraPose = Readonly<Record<string, number>>;
interface CameraKey {
  readonly at: number;
  readonly shot: string;
  readonly pose: CameraPose;
}
interface BezierControls {
  readonly x1: number;
  readonly y1: number;
  readonly x2: number;
  readonly y2: number;
}

/** The project and sheet names `story/camera.ts` asks Theatre for. Changing
 *  one without the other yields a project with no sequence and a camera that
 *  sits at its static defaults for the whole film — silent, and the reason
 *  they are constants shared through the generated file rather than two
 *  literals. */
export const PROJECT_NAME = "NEMESIS · The Walk";
export const SHEET_NAME = "Walk";
export const OBJECT_NAME = "Camera";

/**
 * Theatre positions are quantised to `subUnitsPerUnit`. The sequence is one
 * unit long and `t` is normalised, so this is the resolution of the whole film:
 * one thousandth is well under a pixel of camera movement at any point in it,
 * and it matches the spine's own deadband so a `t` the spine calls "arrived" is
 * a position Theatre calls "arrived" too.
 */
const SUB_UNITS = 1000;

async function main(): Promise<void> {
  const check = process.argv.includes("--check");

  // Whole-module imports. `jiti.import`'s options type only describes the
  // default-export form, so the namespace is asserted here — the shape is
  // checked for real by `tests/story-camera.test.ts`, which imports the same
  // two modules through the compiler.
  const keys: {
    readonly CAMERA_KEYS: readonly CameraKey[];
    readonly POSE_PROPS: readonly string[];
  } = await jiti.import(SOURCE);
  const easing: { readonly controlsOf: (name: string) => BezierControls } = await jiti.import(
    join(ROOT, "src", "lib", "easing.ts"),
  );

  const body = JSON.stringify(
    buildState(keys.CAMERA_KEYS, keys.POSE_PROPS, easing.controlsOf("cine")),
  );
  const formatted = await format(body, { parser: "json", printWidth: 100 });

  mkdirSync(OUT_DIR, { recursive: true });

  if (!check) {
    writeFileSync(OUT, formatted, "utf8");
    console.log(`  wrote ${OUT.replace(ROOT, ".")}`);
    return;
  }

  let current: string | null = null;
  try {
    current = readFileSync(OUT, "utf8");
  } catch {
    current = null;
  }
  if (current === formatted) {
    console.log("camera: the Theatre track matches src/story/camera-keys.ts");
    return;
  }
  console.error(`✗ stale: ${OUT.replace(ROOT, ".")}`);
  console.error(
    "\ncamera: the generated Theatre state does not match src/story/camera-keys.ts.\n" +
      "Run `npm run camera`. §E16's camera is authored in one readable file; a hand-\n" +
      "edited project state is a camera move nobody can review.",
  );
  process.exitCode = 1;
}

/**
 * Build the project state.
 *
 * One `BasicKeyframedTrack` per pose prop, and every keyframe carries the same
 * handles because every segment of this film eases the same way. A keyframe's
 * `handles` are `[inX, inY, outX, outY]`: the *outgoing* pair of the left
 * keyframe and the *incoming* pair of the right one describe the segment
 * between them. `cubic-bezier(x1, y1, x2, y2)` therefore maps to
 * `[x2, y2, x1, y1]` — the second control point is the one arriving, the first
 * is the one leaving. Getting that backwards produces a curve that is
 * subtly wrong everywhere and obviously wrong nowhere, which is why it is
 * written down here and asserted in `tests/story-camera.test.ts`.
 */
function buildState(
  keys: readonly CameraKey[],
  props: readonly string[],
  cine: BezierControls,
): unknown {
  const handles = [cine.x2, cine.y2, cine.x1, cine.y1] as const;
  const ordered = [...keys].sort((a, b) => a.at - b.at);

  const trackData: Record<string, unknown> = {};
  const trackIdByPropPath: Record<string, string> = {};

  for (const prop of props) {
    const trackId = `track_${prop}`;
    trackIdByPropPath[JSON.stringify([prop])] = trackId;
    trackData[trackId] = {
      type: "BasicKeyframedTrack",
      __debugName: `${OBJECT_NAME}:["${prop}"]`,
      keyframes: ordered.map((keyframe, index) => ({
        id: `kf_${prop}_${String(index)}`,
        position: round(keyframe.at),
        connectedRight: index < ordered.length - 1,
        handles: [...handles],
        type: "bezier",
        value: round(keyframe.pose[prop] ?? 0),
      })),
    };
  }

  return {
    sheetsById: {
      [SHEET_NAME]: {
        staticOverrides: { byObject: {} },
        sequence: {
          type: "PositionalSequence",
          subUnitsPerUnit: SUB_UNITS,
          length: 1,
          tracksByObject: {
            [OBJECT_NAME]: { trackData, trackIdByPropPath },
          },
        },
      },
    },
    definitionVersion: "0.4.0",
    revisionHistory: [],
  };
}

/** Six decimals. Metres to the micron is already absurd for a camera; what it
 *  buys is a file whose bytes do not change because a floating-point sum came
 *  out one ulp different on another machine, which would fail `--check` for
 *  nobody's benefit. */
function round(value: number): number {
  return Number(value.toFixed(6));
}

if (process.argv[1]?.endsWith("generate-camera-track.ts")) await main();
