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

  const plates: Plate[] = kept.map((ink, i) => {
    const angle = PRESS.screenAngles[i % PRESS.screenAngles.length] ?? 45;
    if (registered) {
      return { ...ink, angleDeg: angle, offsetPx: [0, 0] as const };
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
