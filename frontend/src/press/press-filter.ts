/**
 * The press, 2D — the CSS/SVG half of ADR-0038.
 *
 * §E6.1 lists six stages in order. The DOM cannot run all six the way a shader
 * can, and this module states plainly where it differs rather than implying a
 * parity it does not have:
 *
 *   1 · ink separation      SVG filter. A per-ink colour matrix projects the
 *                           frame onto the ink and writes the density it needs
 *                           into alpha.
 *   2 · halftone            **Approximated.** A DOM filter has no per-channel
 *                           ordered dither, so each plate carries its own
 *                           screened overlay at its own classic angle, blended
 *                           multiply. The rosette is real; the dither is not
 *                           inside the separation. The TSL pass does the true
 *                           stage order, and that is the difference ADR-0038
 *                           means by "two implementations".
 *   3 · misregistration     SVG filter, `feOffset` per plate, re-jittered at
 *                           12 Hz off the stepped clock.
 *   4 · ink density         SVG turbulence, one low-frequency field, composited
 *                           per plate.
 *   5 · overprint           `feBlend mode="multiply"`, never alpha. Overlapping
 *                           inks produce a genuine third colour, exactly as
 *                           they do on paper (§E6.3).
 *   6 · paper               A grain overlay and a deckle at the frame edge.
 *
 * Every number below comes from `press-model.ts`, which comes from
 * `design/tokens.json`. This module invents nothing.
 */

import type { CSSProperties } from "react";
import type { Plate, PressPlan } from "./press-model";

/**
 * §E6.1 stage 1 — the separation matrix for one ink.
 *
 * The plate's density at a pixel is how much of *this ink* the pixel is
 * missing: `1 − (source · ink) / (ink · ink)`, clamped by the filter. Written
 * into alpha, so `feFlood` + `feComposite operator="in"` can lay the ink down
 * at that density. RGB is zeroed because the plate carries one colour — that is
 * what a plate *is*.
 */
export function separationMatrix(linear: readonly [number, number, number]): string {
  const [r, g, b] = linear;
  const magnitude = r * r + g * g + b * b || 1;
  const w = [r / magnitude, g / magnitude, b / magnitude] as const;
  // prettier-ignore
  return [
    0, 0, 0, 0, 0,
    0, 0, 0, 0, 0,
    0, 0, 0, 0, 0,
    -w[0], -w[1], -w[2], 0, 1,
  ].map((n) => Number(n.toFixed(5))).join(" ");
}

/** Stable id for a plan, so two presses with the same plan share one filter. */
export function filterId(plan: PressPlan): string {
  const key = `${plan.quality}-${String(plan.seed)}-${plan.plates.map((p) => p.id).join("_")}`;
  return `nemesis-press-${key.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
}

/**
 * §E6.1 stage 2, approximated — one screened overlay per plate, at that
 * plate's classic angle. Rotating a tiled grid means over-sizing it, or the
 * corners of the frame come up bare.
 */
export function screenStyle(plate: Plate, plan: PressPlan, index: number): CSSProperties {
  const dotRadius = (plan.cellPx / 2) * (1 - plan.dotSoftness);
  return {
    "--press-ink": plate.hex,
    "--press-angle": `${String(plate.angleDeg)}deg`,
    "--press-cell": `${String(plan.cellPx)}px`,
    "--press-dot": `${String(Number(dotRadius.toFixed(3)))}px`,
    "--press-dot-soft": `${String(Number((dotRadius * (1 + plan.dotSoftness)).toFixed(3)))}px`,
    "--press-plate-index": String(index),
  } as CSSProperties;
}

/**
 * §E6.1 stages 4 and 6 — ink density variation and paper fibre, as one
 * self-contained turbulence field.
 *
 * Inline, as a data URI: §6 Principle #6 is zero-cost, self-hosted and
 * offline-capable, and `scripts/check-guards.ts` fails the build on any asset
 * fetched from a CDN. A texture the product generates is a texture the product
 * cannot fail to have.
 */
export function grainDataUri(plan: PressPlan, kind: "ink" | "paper"): string {
  const frequency = kind === "ink" ? plan.inkDensity.frequency * 0.1 : 0.8;
  const octaves = kind === "ink" ? 2 : 3;
  const opacity = kind === "ink" ? plan.inkDensity.amplitude : plan.paper.amplitude;
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="240" height="240">` +
    `<filter id="g"><feTurbulence type="fractalNoise" baseFrequency="${String(frequency)}" ` +
    `numOctaves="${String(octaves)}" seed="${String(plan.seed)}" stitchTiles="stitch"/>` +
    `<feColorMatrix type="saturate" values="0"/></filter>` +
    `<rect width="240" height="240" filter="url(#g)" opacity="${String(opacity)}"/></svg>`;
  return `url("data:image/svg+xml;utf8,${encodeURIComponent(svg)}")`;
}

/**
 * The variables the press root publishes to its own layers.
 *
 * `--press-plates` is how the CSS knows whether it is printing one ink or
 * three without a class per tier — §E6.4's dial is a count, not a mode.
 */
export function rootStyle(plan: PressPlan): CSSProperties {
  return {
    "--press-plates": String(plan.plates.length),
    "--press-deckle": `${String(plan.paper.deckleWidthPx)}px`,
    "--press-ink-grain": grainDataUri(plan, "ink"),
    "--press-paper-grain": grainDataUri(plan, "paper"),
  } as CSSProperties;
}
