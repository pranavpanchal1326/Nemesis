import { MOTION } from "@/design/generated/tokens";

/**
 * The five signature motions, audited as five — §E11.1, M9's F14 ship line.
 *
 * > **Five signature motions, and only five.**
 *
 * "And only five" is the part that needs a mechanism. A motion vocabulary is
 * not eroded by somebody adding a sixth on purpose; it is eroded by a scene
 * needing *something* and reaching for a 300 ms ease-out because it was two
 * characters away. By the time anybody notices, the product has fourteen
 * motions and a document claiming five.
 *
 * So this file is the register, and `tests/story-motions.test.ts` is the audit:
 * it reads every stylesheet in `src/` and requires that each `transition` and
 * `animation` declaration spend a duration from `MOTION.durationMs` and a curve
 * from `MOTION.easing` — both through their generated custom properties, never
 * as a literal — and that every motion named here is actually used by name.
 * An unused motion fails too, because a vocabulary of five with one nobody
 * reaches for is a vocabulary of four with a footnote.
 *
 * **`data-motion` is how a motion is claimed.** An element that performs one
 * carries the attribute, which makes the vocabulary visible in the DOM, in a
 * screenshot, and to a test — the same trick `data-tier` and `data-gate`
 * already use on the surfaces they belong to.
 */

export const MOTION_IDS = ["stamp", "merge", "rule-draw", "lift", "settle"] as const;

export type MotionId = (typeof MOTION_IDS)[number];

export interface SignatureMotion {
  readonly id: MotionId;
  /** The token duration, in milliseconds. §E11's six durations are all
   *  multiples of the 12 fps step, and a motion that is not is not one of the
   *  five. */
  readonly durationMs: number;
  /** The token curve. */
  readonly easing: keyof typeof MOTION.easing;
  /** Where it is used, in §E11.1's own words. Read by the audit, which
   *  requires each id to appear in the film or in the components it drives. */
  readonly usedFor: string;
}

export const SIGNATURE_MOTIONS: readonly SignatureMotion[] = [
  {
    id: "stamp",
    durationMs: MOTION.durationMs.fast,
    easing: "stamp",
    usedFor:
      "Confirmations land; they do not fade. Complaint accepted, evidence verified, " +
      "closure confirmed, policy activated, case approved.",
  },
  {
    id: "merge",
    durationMs: MOTION.durationMs.cine,
    easing: "cine",
    usedFor:
      "The hero (§E16 Act 6). Flags lean, pins bend toward the centroid, the survivor " +
      "grows, the second ink overprints, a thumbprint presses in, registration rings remain.",
  },
  {
    id: "rule-draw",
    durationMs: MOTION.durationMs.fast,
    easing: "out",
    usedFor: "Borders and dividers draw left to right instead of fading. Drafting-table language.",
  },
  {
    id: "lift",
    durationMs: MOTION.durationMs.base,
    easing: "out",
    usedFor: "Panels rise 8 px and fade. That is the entire chrome vocabulary.",
  },
  {
    id: "settle",
    durationMs: MOTION.durationMs.slow,
    easing: "clay",
    usedFor:
      "Clay objects arriving drop with a single 2 px overshoot — the only overshoot " +
      "permitted anywhere in the product, and only in the 3D world, because clay has mass.",
  },
];

/**
 * The merge's hold, in milliseconds — §E11.1 motion 2.
 *
 * > 900 ms on `--ease-cine`, a **168 ms hold**, then the count stamps.
 *
 * Named because Act 6 has to place the stamp after it, and a scene that
 * hard-coded 168 would be a scene that stops agreeing with `--t-fast` the day
 * the step changes.
 */
export const MERGE_HOLD_MS = MOTION.durationMs.fast;

const BY_ID = new Map<MotionId, SignatureMotion>(
  SIGNATURE_MOTIONS.map((motion) => [motion.id, motion]),
);

export function motion(id: MotionId): SignatureMotion {
  const found = BY_ID.get(id);
  if (found === undefined) throw new Error(`unknown motion: ${id}`);
  return found;
}
