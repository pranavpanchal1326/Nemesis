/**
 * The adaptive quality manager — M8.8, §E6.4, §E23.
 *
 * > Adaptive quality degrades effects before it degrades frame rate, **and the
 * > first thing it turns is the press's quality dial** (§E6.4) — which is the
 * > one degradation in this product that improves the picture.
 *
 * That is a stronger claim than "reduce quality when slow", and the order below
 * is where it either holds or quietly stops holding. Six positions, and the
 * first move a struggling machine makes is to print in two inks at a coarse
 * screen: §E13 requires that to read *"as a bolder print, not a worse one"*,
 * and it is genuinely cheaper — a dropped plate is a whole separation the
 * fragment shader no longer computes.
 *
 * Only at position 4 does the scene itself get smaller, and only at 5 does the
 * ladder move. A visitor on a struggling laptop meets a bolder print before
 * they meet a lesser one, and they meet a lesser one before they meet a
 * stuttering one.
 *
 * **Written as a pure reducer.** `advance()` takes a state and a frame sample
 * and returns the next state. No timers, no renderer, no React. The whole
 * degradation policy is therefore assertable in a Node test in milliseconds,
 * which matters because the thing being asserted is an *order*, and an order is
 * exactly what gets silently rearranged by a later performance fix.
 *
 * **Both directions.** A manager that only degrades is a manager that
 * permanently punishes a machine for one bad three-second window — a garbage
 * collection, a background tab waking up, Ollama answering a question
 * (ADR-0002, and the reason §E23's frame budget says *with Ollama running*).
 * Recovery is deliberately slower than degradation: four consecutive healthy
 * windows to climb one position, one bad window to drop one.
 */

import { BUDGET, type PressQuality } from "@/design/generated/tokens";
import type { Backend, Rung, Tier, TierCapabilities } from "./tier";
import { capabilitiesFor, tierFor } from "./tier";

/**
 * The six positions, in the order §E23 requires them to be taken.
 *
 * `cause` is a **locale key**, never a sentence — the same rule
 * `realtime/store.ts` follows for degradation, and for the same reason: the
 * banner that says what the machine gave up is exactly the copy the Phase 5
 * locale registry must be able to reach (Phase 18's gate).
 */
export const DEGRADATION_LADDER = [
  { position: 0, cause: null, note: "everything on" },
  {
    position: 1,
    cause: "degraded.pressReduced",
    note: "§E6.4's dial, first — two inks, coarse screen, registration held still",
  },
  {
    position: 2,
    cause: "degraded.lensReduced",
    note: "depth of field and gate weave off; the frame stops being a photograph of a table",
  },
  {
    position: 3,
    cause: "degraded.pressFlat",
    note: "one ink, no screen, poster register; weather stops",
  },
  {
    position: 4,
    cause: "degraded.sceneLite",
    note: "Tier B — the lite scene, on whichever backend the renderer already chose",
  },
  {
    position: 5,
    cause: "degraded.storyboard",
    note: "Tier C — the storyboard. The last position, and it is still a designed edit",
  },
] as const;

export const MAX_POSITION = DEGRADATION_LADDER.length - 1;

export interface QualityPlan {
  readonly position: number;
  /** The concrete rung this plan renders as, backend included. */
  readonly tier: Tier;
  readonly press: PressQuality;
  readonly capabilities: TierCapabilities;
  /** The rung the manager has pulled the scene down to, never above `ceiling`. */
  readonly rung: Rung;
  /** A locale key naming what was given up, or `null` at full quality. */
  readonly cause: string | null;
}

export interface QualityState {
  readonly position: number;
  /** Milliseconds spent below budget since the last decision. */
  readonly belowMs: number;
  /** Consecutive healthy windows completed since the last decision. */
  readonly healthyWindows: number;
  /** Milliseconds accumulated toward the current healthy window. */
  readonly aboveMs: number;
}

export interface FrameSample {
  readonly fps: number;
  readonly elapsedMs: number;
}

export const INITIAL_QUALITY: QualityState = {
  position: 0,
  belowMs: 0,
  healthyWindows: 0,
  aboveMs: 0,
};

/** Below this, a frame loop is genuinely missing its budget rather than being
 *  vsynced a fraction under it. */
export const DEGRADE_FPS = BUDGET.fps * BUDGET.degradeBelowFraction;
/** At or above this, the machine has headroom to take an effect back. */
export const RECOVER_FPS = BUDGET.fps * BUDGET.recoverAboveFraction;

/**
 * One frame sample in; the next state out.
 *
 * The two accumulators are reset on every decision rather than carried, so a
 * scene that has just degraded starts its next assessment from zero. Carrying
 * them would let three slow seconds spread over a minute stack up into a second
 * degradation, which is how a manager ends up at the bottom of the ladder on a
 * machine that is fine.
 */
export function advance(state: QualityState, sample: FrameSample): QualityState {
  const elapsed = Math.max(0, sample.elapsedMs);

  if (sample.fps < DEGRADE_FPS) {
    const belowMs = state.belowMs + elapsed;
    if (belowMs >= BUDGET.fpsSampleMs && state.position < MAX_POSITION) {
      return { position: state.position + 1, belowMs: 0, healthyWindows: 0, aboveMs: 0 };
    }
    // A slow sample resets recovery: "four healthy windows" means four in a
    // row, and a stutter in the middle of them is the evidence that the
    // headroom was not there.
    return { position: state.position, belowMs, healthyWindows: 0, aboveMs: 0 };
  }

  if (sample.fps < RECOVER_FPS) {
    // The dead band between the two thresholds. Neither struggling nor
    // comfortable: hold, and forget the accumulated below-budget time, because
    // whatever caused it has passed.
    return {
      position: state.position,
      belowMs: 0,
      healthyWindows: state.healthyWindows,
      aboveMs: 0,
    };
  }

  const aboveMs = state.aboveMs + elapsed;
  if (aboveMs < BUDGET.fpsSampleMs) {
    return { position: state.position, belowMs: 0, healthyWindows: state.healthyWindows, aboveMs };
  }

  const healthyWindows = state.healthyWindows + 1;
  if (healthyWindows >= BUDGET.recoverWindows && state.position > 0) {
    return { position: state.position - 1, belowMs: 0, healthyWindows: 0, aboveMs: 0 };
  }
  return { position: state.position, belowMs: 0, healthyWindows, aboveMs: 0 };
}

/**
 * What a position means, given the rung the device is allowed to reach.
 *
 * `ceiling` is `ladderRung()`'s answer, and the manager can only move *down*
 * from it. A reduced-motion visitor on a fast machine stays on the storyboard
 * no matter how much headroom the frame loop reports — the ladder is a consent
 * boundary at its top and a performance policy below it, and this is the line
 * where the two meet.
 */
export function planFor(position: number, ceiling: Rung, backend: Backend | null): QualityPlan {
  const clamped = Math.min(MAX_POSITION, Math.max(0, Math.trunc(position)));
  const entry = DEGRADATION_LADDER[clamped] ?? DEGRADATION_LADDER[0];

  const rung: Rung =
    ceiling === "D" || ceiling === "C"
      ? ceiling
      : clamped >= 5
        ? "C"
        : clamped >= 4 || ceiling === "B"
          ? "B"
          : "gpu";

  // The backend is passed straight through, never inferred. ADR-0037 again:
  // which of S and A this is remains the renderer's answer, and the quality
  // manager's job is the rung, not the backend.
  const tier = tierFor(rung, backend);
  const base = capabilitiesFor(tier);

  return {
    position: clamped,
    tier,
    press: pressFor(clamped, tier === "B" || tier === "C" || tier === "D"),
    capabilities: {
      clay: base.clay,
      depthOfField: base.depthOfField && clamped < 2,
      // Bloom is not a quality setting. §E7.3 reserves it for
      // `safety_trigger_fired` and §E3.4 audits that it is used nowhere else;
      // switching it off to save frames would switch off the one visual the
      // deterministic fail-safe has. It goes only when the tier itself has no
      // lens stack at all.
      bloom: base.bloom,
      gateWeave: base.gateWeave && clamped < 2,
      movingSun: base.movingSun && clamped < 3,
      weather: base.weather && clamped < 3,
    },
    rung,
    cause: entry.cause,
  };
}

function pressFor(position: number, tierAlreadyReduced: boolean): PressQuality {
  if (position >= 3) return "flat";
  if (position >= 1 || tierAlreadyReduced) return "reduced";
  return "full";
}
