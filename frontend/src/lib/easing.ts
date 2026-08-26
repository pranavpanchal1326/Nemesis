/**
 * The motion tokens, evaluated — §E11, §E24.
 *
 * `MOTION.easing` holds four curves as CSS `cubic-bezier()` strings, because
 * that is what the 2D layer needs. The clay layer needs the same curves as
 * *numbers*: a pin settling into clay is not a CSS transition, and neither is a
 * camera damping toward a selection.
 *
 * The obvious move is to write the 3D easing by hand and pick something that
 * looks close. That is precisely the drift §E24 exists to prevent — the same
 * failure as a hand-typed severity colour, one axis over. The Settle motion is
 * one of §E11.1's five signature motions and the Stamp is another; if the clay
 * and the paper disagree about what "settle" means, the product has two motion
 * languages and a style guide that describes neither.
 *
 * So the token string is parsed and evaluated here, and `cubicBezier("stamp")`
 * is the same curve the CSS transition runs. No new numbers are introduced by
 * this file, which is the property that makes it worth having.
 *
 * **Newton, then bisection.** Inverting a cubic Bézier's x for a given t has no
 * closed form. Newton-Raphson converges in three or four iterations for the
 * curves in use; it is not guaranteed to, so a bounded bisection catches the
 * cases where the derivative is near zero — which is exactly what happens at
 * the flat start of `cine` and would otherwise return a wrong value silently.
 */

import { MOTION } from "@/design/generated/tokens";

export type EasingName = keyof typeof MOTION.easing;

const CUBIC_BEZIER =
  /^cubic-bezier\(\s*([-\d.]+)\s*,\s*([-\d.]+)\s*,\s*([-\d.]+)\s*,\s*([-\d.]+)\s*\)$/;

export interface BezierControls {
  readonly x1: number;
  readonly y1: number;
  readonly x2: number;
  readonly y2: number;
}

/** Parse one token. Throws rather than falling back to linear: a silently
 *  linear Settle is a motion bug nobody would trace to a typo in a token. */
export function controlsOf(name: EasingName): BezierControls {
  const raw = MOTION.easing[name];
  const match = CUBIC_BEZIER.exec(raw);
  if (match === null) throw new Error(`motion: ${name} is not a cubic-bezier — "${raw}"`);
  return {
    x1: Number(match[1]),
    y1: Number(match[2]),
    x2: Number(match[3]),
    y2: Number(match[4]),
  };
}

/** A ready-to-call easing function for a named token. */
export function easing(name: EasingName): (t: number) => number {
  const controls = controlsOf(name);
  return (t: number) => evaluate(controls, t);
}

/**
 * `y` at progress `x`, for a CSS cubic Bézier with endpoints (0,0) and (1,1).
 *
 * `y` may leave 0…1 — `stamp` overshoots past 1 on purpose, which is what
 * makes it a stamp rather than a fade — so nothing is clamped here. `x` is
 * clamped, because progress outside the transition is not a curve question.
 */
export function evaluate(controls: BezierControls, x: number): number {
  const clamped = Math.min(1, Math.max(0, x));
  if (clamped === 0 || clamped === 1) return clamped;
  return bezier(controls.y1, controls.y2, solveT(controls, clamped));
}

function bezier(a: number, b: number, t: number): number {
  const inverse = 1 - t;
  return 3 * inverse * inverse * t * a + 3 * inverse * t * t * b + t * t * t;
}

function slope(a: number, b: number, t: number): number {
  const inverse = 1 - t;
  return 3 * inverse * inverse * a + 6 * inverse * t * (b - a) + 3 * t * t * (1 - b);
}

const NEWTON_ITERATIONS = 5;
const TOLERANCE = 1e-6;

function solveT(controls: BezierControls, x: number): number {
  let t = x;
  for (let i = 0; i < NEWTON_ITERATIONS; i += 1) {
    const error = bezier(controls.x1, controls.x2, t) - x;
    if (Math.abs(error) < TOLERANCE) return t;
    const derivative = slope(controls.x1, controls.x2, t);
    if (Math.abs(derivative) < TOLERANCE) break;
    t -= error / derivative;
  }

  let low = 0;
  let high = 1;
  t = x;
  for (let i = 0; i < 32; i += 1) {
    const value = bezier(controls.x1, controls.x2, t);
    if (Math.abs(value - x) < TOLERANCE) return t;
    if (value > x) high = t;
    else low = t;
    t = (low + high) / 2;
  }
  return t;
}

/**
 * Frame-rate-independent damping toward a target.
 *
 * §E7.2's camera is *"uncapped, damped, filmic"*, and the naive
 * `current += (target - current) * 0.1` is none of those: it damps faster on a
 * 144 Hz display than on a 60 Hz one, so the same camera move feels different
 * on two machines and the golden image depends on how many frames elapsed.
 *
 * `rate` is the fraction closed per second, which is a number somebody can
 * reason about and a token can hold.
 */
export function damp(
  current: number,
  target: number,
  ratePerSecond: number,
  dtSeconds: number,
): number {
  const factor = 1 - Math.exp(-ratePerSecond * dtSeconds);
  return current + (target - current) * factor;
}
