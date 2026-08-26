/**
 * The press, as data — §E6, ADR-0038.
 *
 * ADR-0038 is titled "the press is one token source in two implementations".
 * This module is the *one token source* half: it resolves a surface, a quality
 * tier and an optional severity into a concrete plan — which inks, at which
 * angles, offset by how much — and both implementations read the same plan.
 *
 *   press-filter.ts   the 2D implementation (SVG filter + screened overlays)
 *   press-tsl.ts      the 3D implementation (a TSL post-processing pass)
 *
 * Neither is allowed a parameter of its own. A number that appears in one and
 * not the other is exactly the drift the ADR exists to prevent, and the parity
 * test in `tests/press-parity.test.ts` asserts both consume this plan and
 * nothing else.
 */

import {
  INK,
  INK_LINEAR,
  INK_SET,
  PRESS,
  SEVERITY,
  type InkName,
  type InkSetName,
  type PressQuality,
  type SeverityLevel,
} from "@/design/generated/tokens";
import { mulberry32 } from "@/lib/stepped-clock";

/**
 * One row of the separation, in absorbance space.
 *
 * Multiplied against `log(stock) − log(target)` per channel, it gives the
 * coverage this plate must carry. See `separationRows()` for the derivation.
 */
export type SeparationRow = readonly [number, number, number];

/** One separation: an ink, its screen angle, and where the sheet slipped. */
export interface Plate {
  /** The token name, or `severity:<level>` when this is the third pass. */
  readonly id: string;
  readonly hex: string;
  /** Linear-sRGB, which is what the TSL pass wants (§E24). */
  readonly linear: readonly [number, number, number];
  /** Classic screen angle — 15° / 45° / 75° — so the rosette reads as print. */
  readonly angleDeg: number;
  /** §E6.1 stage 3. Zero when the quality tier prints in register. */
  readonly offsetPx: readonly [number, number];
  /** This plate's row of the separation (§E6.1 stage 1). */
  readonly separation: SeparationRow;
}

export interface PressPlan {
  readonly quality: PressQuality;
  readonly plates: readonly Plate[];
  /** Halftone cell in device-independent pixels; 0 means no screen (flat). */
  readonly cellPx: number;
  readonly dotSoftness: number;
  /** §E6.1 stage 3 re-jitters at 12 Hz; `false` on the reduced and flat tiers. */
  readonly animated: boolean;
  readonly jitterHz: number;
  readonly inkDensity: {
    readonly amplitude: number;
    readonly frequency: number;
    readonly leadingEdgeBias: number;
  };
  readonly paper: { readonly amplitude: number; readonly deckleWidthPx: number };
  readonly seed: number;
}

export interface PressRequest {
  readonly surface: InkSetName;
  readonly quality: PressQuality;
  /**
   * §E9.2 — several ink sets name `severity` as their third pass, which means
   * "whichever severity ink this entity carries". Omitting it does not fall
   * back to a colour; it prints one ink shorter, which is what a press does.
   */
  readonly severity?: SeverityLevel;
  /** Fixed seed ⇒ reproducible misregistration ⇒ golden images (§E24). */
  readonly seed?: number;
}

function isInkName(value: string): value is InkName {
  return Object.hasOwn(INK, value);
}

/**
 * Resolve an ink set's members to concrete plates.
 *
 * The quality dial (§E6.4) is applied by *dropping passes*, not by fading them.
 * That is why reduced quality "reads as a bolder print, not a worse one" and
 * why §E2 defect #8 dissolves: there is no downgrade to re-frame.
 */
export function planPress(request: PressRequest): PressPlan {
  const { surface, quality, severity, seed = 1 } = request;
  const tier = PRESS.quality[quality];
  const rng = mulberry32(seed);

  const members = INK_SET[surface].inks;
  const resolved: { id: string; hex: string; linear: readonly [number, number, number] }[] = [];

  for (const member of members) {
    if (member === "severity") {
      if (severity === undefined) continue; // one ink shorter, deliberately
      const row = SEVERITY[severity];
      resolved.push({
        id: `severity:${severity}`,
        hex: row.glaze,
        linear: linearOf(row.glaze),
      });
      continue;
    }
    if (!isInkName(member)) continue;
    resolved.push({ id: member, hex: INK[member], linear: INK_LINEAR[member] });
  }

  const kept = resolved.slice(0, tier.inks);
  const registered = tier.misregistration === "none";
  const { minPx, maxPx } = PRESS.misregistration;

  const separation = separationRows(kept.map((ink) => ink.linear));

  const plates: Plate[] = kept.map((ink, i) => {
    const angle = PRESS.screenAngles[i % PRESS.screenAngles.length] ?? 45;
    const row = separation[i] ?? ([0, 0, 0] as SeparationRow);
    if (registered) {
      return { ...ink, angleDeg: angle, offsetPx: [0, 0] as const, separation: row };
    }
    // A slip is a direction and a distance, not two independent numbers — a
    // sheet moves through a press, it does not jitter on two axes.
    const theta = rng() * Math.PI * 2;
    const distance = minPx + rng() * (maxPx - minPx);
    return {
      ...ink,
      angleDeg: angle,
      offsetPx: [
        Number((Math.cos(theta) * distance).toFixed(3)),
        Number((Math.sin(theta) * distance).toFixed(3)),
      ] as const,
      separation: row,
    };
  });

  const cellPx =
    tier.halftone === "none"
      ? 0
      : tier.halftone === "coarse"
        ? PRESS.halftone.cellCoarse
        : PRESS.halftone.cellFine;

  return {
    quality,
    plates,
    cellPx,
    dotSoftness: PRESS.halftone.dotSoftness,
    animated: tier.misregistration === "animated",
    jitterHz: PRESS.misregistration.jitterHz,
    inkDensity: {
      amplitude: PRESS.inkDensity.amplitude,
      frequency: PRESS.inkDensity.frequency,
      leadingEdgeBias: PRESS.inkDensity.leadingEdgeBias,
    },
    paper: {
      amplitude: PRESS.paperGrain.amplitude,
      deckleWidthPx: PRESS.paperGrain.deckleWidthPx,
    },
    seed,
  };
}

/**
 * §E6.1 stage 1 — ink separation, solved once per plan.
 *
 * **The formula this replaces, and why it was wrong.** Both implementations
 * used to compute a plate's density as `1 − (source · ink) / (ink · ink)` — the
 * projection of the frame onto the ink, subtracted from one. That is a
 * defensible-looking expression with a fatal property: it only produces ink
 * where the source is *darker than the ink itself*. Against `riso-black`, whose
 * linear value is about 0.008, a mid-tone pixel scores roughly thirteen and the
 * plate lays down nothing at all.
 *
 * On the 2D surfaces that went unnoticed, because there the separation is one
 * of several layers and the screened overlays carry the picture. In 3D the
 * separation **is** the picture, and the result was a blank sheet: the clay
 * rendered correctly, the lens photographed it correctly, and the press printed
 * a perfectly clean piece of paper. The Phase 19 golden image is what caught
 * it, which is the entire argument for having one.
 *
 * **What replaces it.** Overprint is multiplicative (§E6.1 stage 5, §E6.3), so
 * the arithmetic belongs in log space. Writing `A[c][i] = −ln(inkᵢ,c)` for how
 * much channel *c* a full covering of ink *i* absorbs, and `d[c] = ln(stock_c)
 * − ln(target_c)` for how much absorbance the sheet still needs, the coverages
 * are the least-squares solution of `A · coverage ≈ d`. Its rows —
 * `(AᵀA)⁻¹Aᵀ` — are what this function returns, so the shader spends one dot
 * product per plate and no matrix maths at all.
 *
 * A ridge term keeps the solve stable when two inks in a set are nearly the
 * same colour, which is a real configuration (`riso-black` beside a dark
 * severity glaze) and would otherwise produce enormous, cancelling coverages
 * that clamp to garbage.
 */
export function separationRows(
  inks: readonly (readonly [number, number, number])[],
): readonly SeparationRow[] {
  const n = inks.length;
  if (n === 0) return [];

  // Absorbance per ink per channel. Floored away from zero: a perfectly opaque
  // ink has infinite absorbance and no press has one.
  const a = inks.map((ink) => ink.map((c) => -Math.log(Math.max(c, MIN_TRANSMITTANCE))));

  // AᵀA, plus the ridge.
  const m: number[][] = [];
  for (let i = 0; i < n; i += 1) {
    const row: number[] = [];
    for (let j = 0; j < n; j += 1) {
      let sum = i === j ? RIDGE : 0;
      for (let c = 0; c < 3; c += 1) sum += (a[i]?.[c] ?? 0) * (a[j]?.[c] ?? 0);
      row.push(sum);
    }
    m.push(row);
  }

  const inverse = invert(m);
  if (inverse === null) {
    // Singular even with the ridge: every ink in the set is the same colour.
    // One plate carries it and the rest print nothing, which is the honest
    // outcome — and is what a printer would do with three tins of one ink.
    return inks.map((_, i) =>
      i === 0 ? scaleRow(a[0] ?? [0, 0, 0]) : ([0, 0, 0] as SeparationRow),
    );
  }

  return inks.map((_, i) => {
    const out: number[] = [0, 0, 0];
    for (let c = 0; c < 3; c += 1) {
      let sum = 0;
      for (let j = 0; j < n; j += 1) sum += (inverse[i]?.[j] ?? 0) * (a[j]?.[c] ?? 0);
      out[c] = Number(sum.toFixed(6));
    }
    return [out[0] ?? 0, out[1] ?? 0, out[2] ?? 0] as SeparationRow;
  });
}

/** A perfectly opaque ink would absorb infinitely. Clamped so the solve stays
 *  finite for `riso-black`, whose blue channel is 0.005. */
const MIN_TRANSMITTANCE = 0.002;

/** Tikhonov regularisation. Small against typical absorbances (2–5), large
 *  enough that two near-identical inks cannot produce a singular normal
 *  matrix. */
const RIDGE = 0.05;

function scaleRow(absorbance: readonly number[]): SeparationRow {
  const magnitude = absorbance.reduce((sum, c) => sum + c * c, 0) || 1;
  return [
    Number(((absorbance[0] ?? 0) / magnitude).toFixed(6)),
    Number(((absorbance[1] ?? 0) / magnitude).toFixed(6)),
    Number(((absorbance[2] ?? 0) / magnitude).toFixed(6)),
  ];
}

/** Gauss-Jordan, for n ≤ 3. Returns `null` on a singular matrix rather than
 *  producing infinities that would reach a shader as a black frame. */
function invert(m: readonly (readonly number[])[]): number[][] | null {
  const n = m.length;
  const a = m.map((row, i) => [...row, ...identityRow(n, i)]);

  for (let col = 0; col < n; col += 1) {
    let pivot = col;
    for (let r = col + 1; r < n; r += 1) {
      if (Math.abs(a[r]?.[col] ?? 0) > Math.abs(a[pivot]?.[col] ?? 0)) pivot = r;
    }
    const pivotRow = a[pivot];
    const target = a[col];
    if (pivotRow === undefined || target === undefined) return null;
    a[pivot] = target;
    a[col] = pivotRow;

    const head = a[col];
    const value = head?.[col] ?? 0;
    if (head === undefined || Math.abs(value) < 1e-12) return null;
    for (let k = 0; k < 2 * n; k += 1) head[k] = (head[k] ?? 0) / value;

    for (let r = 0; r < n; r += 1) {
      if (r === col) continue;
      const row = a[r];
      if (row === undefined) continue;
      const factor = row[col] ?? 0;
      if (factor === 0) continue;
      for (let k = 0; k < 2 * n; k += 1) row[k] = (row[k] ?? 0) - factor * (head[k] ?? 0);
    }
  }

  return a.map((row) => row.slice(n));
}

function identityRow(n: number, i: number): number[] {
  return Array.from({ length: n }, (_, k) => (k === i ? 1 : 0));
}

function linearOf(hex: string): readonly [number, number, number] {
  const h = hex.replace("#", "");
  const channel = (i: number) => {
    const c = parseInt(h.slice(i, i + 2), 16) / 255;
    return Number((c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)).toFixed(5));
  };
  return [channel(0), channel(2), channel(4)];
}

/**
 * Re-jitter the plates for a given 12 Hz step (§E6.1 stage 3).
 *
 * "This one stage is the majority of why does this look printed." It is also
 * the only part of the press that moves, which is why it is a pure function of
 * (plan, step) rather than state: a golden image at step *n* is reproducible
 * without running a clock.
 */
export function jitterAt(plan: PressPlan, step: number): readonly (readonly [number, number])[] {
  if (!plan.animated) return plan.plates.map((p) => p.offsetPx);
  const rng = mulberry32(plan.seed + step * 977);
  const { minPx, maxPx } = PRESS.misregistration;
  return plan.plates.map(() => {
    const theta = rng() * Math.PI * 2;
    const distance = minPx + rng() * (maxPx - minPx);
    return [
      Number((Math.cos(theta) * distance).toFixed(3)),
      Number((Math.sin(theta) * distance).toFixed(3)),
    ] as const;
  });
}
