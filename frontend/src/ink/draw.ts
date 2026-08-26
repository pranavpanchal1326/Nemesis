/**
 * Ink on a 2D canvas — §E8, §E5, F15.
 *
 * §E5's three-material law puts people in **ink**: the clay is sculpted, the
 * paper is printed, and the figure is drawn. This module is that third
 * material, and it is deliberately the smallest of the three — a stroke list, a
 * varying width, and one warm pass that does not quite register.
 *
 * **Why a 2D canvas rather than SVG or the WebGPU renderer.** SVG would put
 * eight states of figure animation into the stylesheet, where §E11.1's audit
 * (`tests/story-motions.test.ts`) reads every duration and curve and would
 * either fail on them or, worse, not see them. The clay renderer is ruled out
 * by §E5 — a figure in the 3D scene is a figure made of clay. A 2D context is
 * what is left, it is available on every rung of §E13's ladder that runs
 * JavaScript at all, and it costs nothing when the machine's state has not
 * changed because nothing redraws.
 *
 * **The line weight varies along the stroke, and that is the whole drawing.**
 * A constant-width path reads as a diagram; §E8 asks for a hand. Each segment
 * is stroked at the width its two endpoints average, which is a cheap
 * approximation of a brush and is visibly not a CAD line at 96 px.
 *
 * **The warm fill is offset by the press's own misregistration token.** §E8:
 * *"one warm fill that does not quite register with the line, which is free
 * because the press does it (§E6.1 stage 3)"*. Free in the 3D pipeline, where
 * the press separates and offsets the passes; here the press is not in the
 * path, so the offset is applied directly — from `PRESS.misregistration`, so
 * the figure misregisters by the same distance as everything else printed in
 * this product rather than by a number somebody liked.
 */

import { INK, PRESS } from "@/design/generated/tokens";
import { mulberry32 } from "@/lib/stepped-clock";

import type { FigureDrawing, Stroke } from "./figures";

/**
 * How the drawing is placed in the canvas.
 *
 * The figure's own space runs `y` up from the ground at 0; a canvas runs `y`
 * down from the top. The flip lives here and nowhere else.
 */
export interface Placement {
  readonly widthPx: number;
  readonly heightPx: number;
  /** Device pixel ratio. A figure drawn at 1× on a 3× phone is a smudge. */
  readonly dpr: number;
  /** Fraction of the canvas height the figure's crown reaches. */
  readonly fill: number;
}

export interface InkStyle {
  /** The line. §E9.2's base ink: *all text, all rules* — and all figures. */
  readonly line: string;
  /** The one warm pass. §E9.2's clay ink is MITTI's body colour. */
  readonly warm: string;
  /** Base stroke width in CSS pixels, before a point's own weight. */
  readonly nib: number;
  /**
   * Misregistration distance in CSS pixels, or 0 to print in register.
   *
   * Tier C and Tier D print flat (`pressQualityFor`), and a flat print has no
   * second pass to misregister — §E6.4's dial applies to the figure exactly as
   * it applies to everything else the press touches.
   */
  readonly offsetPx: number;
}

export const DEFAULT_STYLE: InkStyle = {
  line: INK["riso-black"],
  warm: INK["riso-brown"],
  nib: 2.4,
  offsetPx: PRESS.misregistration.maxPx,
};

/**
 * The misregistration offset for one frame.
 *
 * Seeded on the step, so a golden image taken at a fixed step gets the same
 * offset every run — the same reason `stepped-clock.ts` carries a PRNG at all,
 * and the same PRNG, so the figure jitters on the same clock as the press.
 */
export function offsetAt(step: number, distancePx: number): readonly [number, number] {
  if (distancePx <= 0) return [0, 0];
  const random = mulberry32(step + 1);
  const angle = random() * Math.PI * 2;
  const magnitude =
    PRESS.misregistration.minPx + random() * (distancePx - PRESS.misregistration.minPx);
  return [Math.cos(angle) * magnitude, Math.sin(angle) * magnitude];
}

function path(
  ctx: CanvasRenderingContext2D,
  stroke: Stroke,
  map: (x: number, y: number) => [number, number],
): void {
  ctx.beginPath();
  stroke.points.forEach((point, index) => {
    const [x, y] = map(point.x, point.y);
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  if (stroke.closed) ctx.closePath();
}

/**
 * Draw one figure.
 *
 * Clears first: a character is redrawn on every step the machine or the clock
 * changes, and a canvas that is not cleared accumulates a walk cycle into a
 * centipede. Found exactly that way.
 */
export function drawInk(
  ctx: CanvasRenderingContext2D,
  drawing: FigureDrawing,
  place: Placement,
  step: number,
  style: InkStyle = DEFAULT_STYLE,
): void {
  const { widthPx, heightPx, dpr, fill } = place;

  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, widthPx, heightPx);

  const unit = heightPx * fill;
  const originX = widthPx / 2;
  const originY = heightPx - (heightPx - unit) / 2;
  const map = (x: number, y: number): [number, number] => [originX + x * unit, originY - y * unit];

  const [dx, dy] = offsetAt(step, style.offsetPx);

  // The warm pass first and underneath. Riso ink is translucent and the passes
  // stack; the line has to sit *on* the fill, not beside it.
  ctx.save();
  ctx.translate(dx, dy);
  ctx.fillStyle = style.warm;
  // §E6.3: overprint is multiplicative. `multiply` is the 2D context's own
  // spelling of that, which keeps this figure's two passes obeying the same
  // rule as the press's three.
  ctx.globalCompositeOperation = "multiply";
  path(ctx, drawing.fill, map);
  ctx.fill();
  ctx.restore();

  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.strokeStyle = style.line;

  for (const stroke of drawing.strokes) {
    const points = stroke.points;
    const segments = stroke.closed ? points.length : points.length - 1;
    for (let i = 0; i < segments; i += 1) {
      const a = points[i];
      const b = points[(i + 1) % points.length];
      if (a === undefined || b === undefined) continue;
      ctx.beginPath();
      const [ax, ay] = map(a.x, a.y);
      const [bx, by] = map(b.x, b.y);
      ctx.moveTo(ax, ay);
      ctx.lineTo(bx, by);
      ctx.lineWidth = ((a.w + b.w) / 2) * style.nib;
      ctx.stroke();
    }
  }
}
