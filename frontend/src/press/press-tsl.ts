/**
 * The press, 3D — the TSL half of ADR-0038.
 *
 * Authored once in TSL and compiled by `WebGPURenderer` to WGSL on a WebGPU
 * backend and GLSL on a WebGL 2 backend (ADR-0037). There is no second shader
 * source to keep in step, which is exactly why the ADR refused hand-written
 * GLSL: two sources for one effect is a drift surface with no test that can
 * catch a divergence in appearance, only in compilation.
 *
 * Unlike the DOM implementation (`press-filter.ts`), this one runs §E6.1's six
 * stages in their real order, per pixel, per ink:
 *
 *   1 · ink separation   how much of this ink the pixel is missing
 *   2 · halftone         a rotated dot grid at the plate's classic angle,
 *                        thresholded against the separation density
 *   3 · misregistration  the plate is sampled at an offset, re-jittered at
 *                        12 Hz from the same stepped clock the DOM uses
 *   4 · ink density      low-frequency noise per channel, plus the leading-edge
 *                        bias a roller actually produces
 *   5 · overprint        multiply, never alpha — a genuine third colour
 *   6 · paper            the stock, with fibre grain
 *
 * Every parameter arrives in a `PressPlan`. This module declares no constant of
 * its own; `scripts/check-guards.ts` fails the build on a colour literal here
 * the same as anywhere else in `src/`.
 *
 * **Why no `Fn()` wrappers.** TSL's `Fn` emits a reusable shader function; a
 * plain TypeScript function that composes nodes inlines instead. At three
 * plates the inlined graph is small, and the plain form keeps the node types
 * legible — which matters more here than shader-source size, because this is
 * the module a reviewer has to be able to check against §E6.1 line by line.
 */

import { Vector2 } from "three";
import {
  clamp,
  cos,
  dot,
  float,
  fract,
  length,
  mix,
  mx_noise_float,
  oneMinus,
  radians,
  sin,
  smoothstep,
  uniform,
  vec2,
  vec3,
  vec4,
} from "three/tsl";
import type { Node } from "three/webgpu";

import { jitterAt, type PressPlan } from "./press-model";

type F = Node<"float">;
type V2 = Node<"vec2">;
type V3 = Node<"vec3">;
type V4 = Node<"vec4">;

export interface PressPassHandles {
  /** The composited frame. Bind this as a post-processing pass's output node. */
  readonly node: V4;
  /**
   * Advance the misregistration to a 12 Hz step (§E6.1 stage 3).
   *
   * Called from the stepped clock, outside React — §E14.2. It writes uniform
   * values, never React state, so the press animates without a re-render.
   */
  readonly setStep: (step: number) => void;
  /** §E6.4's dial as a uniform: 1 = printing, 0 = the sheet came out blank. */
  readonly setStrength: (value: number) => void;
}

export interface PressPassSource {
  /** Sample the frame being printed, at a (possibly offset) uv. */
  readonly sample: (uvNode: V2) => V4;
  readonly uv: V2;
  /** Pixel dimensions, so the halftone cell and the slip are in real pixels. */
  readonly resolution: V2;
}

/**
 * §E6.1 stage 2 — a round dot, slightly soft at the edge, on a grid rotated to
 * a classic screen angle so the rosette reads as real print.
 *
 * The dot *grows with density*. That is what a halftone is, and it is why a
 * coarser screen reads as a bolder print rather than a worse one (§E6.4) —
 * which is the whole reason §E2 defect #8 has nothing left to re-frame.
 */
function halftoneCoverage(
  coordPx: V2,
  angleDeg: number,
  cellPx: number,
  softness: number,
  density: F,
): F {
  const a = radians(float(angleDeg));
  const rotated = vec2(
    coordPx.x.mul(cos(a)).sub(coordPx.y.mul(sin(a))),
    coordPx.x.mul(sin(a)).add(coordPx.y.mul(cos(a))),
  );
  const cell = fract(rotated.div(float(cellPx))).sub(0.5);
  const radius = density.mul(0.72);
  const edge = float(softness * 0.5 + 0.001);
  return smoothstep(radius.add(edge), radius.sub(edge), length(cell));
}

/**
 * Build the press for a plan and a source.
 *
 * The source is supplied by the caller rather than constructed here, so the
 * same pass composites the live scene in the console, an offscreen buffer for
 * "Your Ward's Month" (§E17.6), and a still for a Tier C storyboard print,
 * without knowing which it is.
 */
export function createPressPass(
  source: PressPassSource,
  plan: PressPlan,
  stockLinear: readonly [number, number, number],
): PressPassHandles {
  const strength = uniform(1);
  const offsets = plan.plates.map(
    (plate) => uniform(new Vector2(plate.offsetPx[0], plate.offsetPx[1])),
  );

  const coordPx = source.uv.mul(source.resolution) as V2;

  // Stage 6 is the ground, and every plate multiplies onto it. Printing on
  // black stock is not a real thing (§E9.3) — the ground is always the sheet.
  let sheet: V3 = vec3(stockLinear[0], stockLinear[1], stockLinear[2]);

  plan.plates.forEach((plate, i) => {
    const offset = offsets[i];
    if (!offset) return;

    // Stage 3 — this plate is sampled where the sheet was when it printed.
    const sampled = source.sample(source.uv.add(offset.div(source.resolution)) as V2);

    // Stage 1 — the density this ink must carry at this pixel.
    const ink = vec3(plate.linear[0], plate.linear[1], plate.linear[2]);
    const magnitude = plate.linear.reduce((acc, c) => acc + c * c, 0) || 1;
    const density = clamp(oneMinus(dot(sampled.rgb, ink).div(float(magnitude))), 0, 1);

    // Stage 4 — risograph ink is uneven, roller-streaked, and denser at the
    // leading edge of a pass. One low-frequency field per plate, shifted per
    // plate so two inks never streak identically.
    const noise = mx_noise_float(
      vec3(coordPx.mul(float(plan.inkDensity.frequency * 0.01)), float(i * 13.7)),
    );
    const unevenness = float(1)
      .add(noise.mul(float(plan.inkDensity.amplitude)))
      .add(oneMinus(source.uv.y).mul(float(plan.inkDensity.leadingEdgeBias)));

    const carried = clamp(density.mul(unevenness), 0, 1);

    // Stage 2 — screened, unless the tier prints flat (§E6.4).
    const coverage =
      plan.cellPx === 0
        ? carried
        : halftoneCoverage(coordPx, plate.angleDeg, plan.cellPx, plan.dotSoftness, carried);

    // Stage 5 — overprint. Multiply, never alpha.
    sheet = sheet.mul(mix(vec3(1, 1, 1), ink, coverage.mul(strength)));
  });

  // Paper fibre. Faint, and it is the only thing in the frame that is not ink:
  // it is the sheet showing through.
  const fibre = mx_noise_float(vec3(coordPx.mul(0.35), float(plan.seed)));
  const grained = sheet.mul(float(1).add(fibre.mul(float(plan.paper.amplitude))));

  return {
    node: vec4(grained, 1),
    setStep: (step: number) => {
      jitterAt(plan, step).forEach((offset, i) => {
        offsets[i]?.value.set(offset[0], offset[1]);
      });
    },
    setStrength: (value: number) => {
      strength.value = value;
    },
  };
}
