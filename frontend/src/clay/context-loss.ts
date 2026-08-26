/**
 * Context loss, and what a second one means — M8.9, §E13, §E26.
 *
 * > **WebGL context loss** reconstructs the scene without a page reload (a
 * > Phase 19 gate). A second loss in one session drops permanently to Tier C
 * > and says so calmly.
 *
 * A lost context is not an error. A driver reset, a laptop switching from the
 * discrete GPU to the integrated one on battery, another tab taking the device,
 * Ollama claiming VRAM (ADR-0002 — the shared GPU is the *design*, so this will
 * happen to this product on purpose) — all of these take a context away from a
 * page that did nothing wrong. The browser then hands it back, and a scene that
 * reloaded the page in response would have thrown away the officer's selection,
 * their scroll position and their filter over a two-hundred-millisecond blip.
 *
 * So the first loss rebuilds. **The second gives up**, and that is the harder
 * decision to defend: rebuilding is the good behaviour, so why stop? Because a
 * context that is lost twice in one session is not blipping, it is a machine
 * that cannot hold this scene — and a page that rebuilds forever presents as a
 * flickering map with no explanation, which is the §E3.3 failure ("a confidently
 * wrong screen is worse than an honest empty one") in its most literal form.
 * Tier C is a designed, reviewed edit of the same content (§E3.2), so dropping
 * to it costs the visitor the animation and none of the information.
 *
 * `<DegradedBanner>`'s contract governs how that is said: *named degradation
 * with an honest cause, calm register, secondary ink, never an error colour*.
 * This module supplies the cause as a **locale key** — the same rule the
 * realtime store follows, and for the same Phase 18 reason.
 *
 * A pure state machine, so the Phase 19 gate ("forced context loss recovers to
 * a correct scene without a page reload") is asserted twice: once here on the
 * policy, in microseconds, and once in the browser on the actual rebuild.
 */

/** How many losses a session absorbs before the ladder moves. One. */
export const REBUILD_BUDGET = 1;

export const LOSS_CAUSE_KEY = "degraded.contextLostTwice";

export interface ContextLossState {
  /** Losses seen in this session, whether or not the context came back. */
  readonly losses: number;
  /** True once the scene has stopped trying. Never returns to false: the
   *  session is the unit, and a reload is what starts a new one. */
  readonly permanent: boolean;
  /** True between a loss and its restore — the window in which the scene has
   *  no GPU resources and must not try to draw. */
  readonly lost: boolean;
}

export const INITIAL_LOSS_STATE: ContextLossState = {
  losses: 0,
  permanent: false,
  lost: false,
};

export type LossResponse =
  /** Wait for `webglcontextrestored`, then rebuild every resource. */
  | { readonly action: "rebuild"; readonly attempt: number }
  /** Stop. Drop to Tier C and say so, once, in secondary ink. */
  | { readonly action: "degrade"; readonly cause: string };

/**
 * Record a `webglcontextlost`.
 *
 * The caller must also have called `preventDefault()` on the event — without
 * it the browser never fires `webglcontextrestored` and the rebuild path below
 * is unreachable. That is a fact about the DOM API rather than about this
 * policy, so it is enforced where the listener is attached, not here.
 */
export function noteLoss(state: ContextLossState): {
  readonly state: ContextLossState;
  readonly response: LossResponse;
} {
  const losses = state.losses + 1;
  if (losses > REBUILD_BUDGET) {
    return {
      state: { losses, permanent: true, lost: true },
      response: { action: "degrade", cause: LOSS_CAUSE_KEY },
    };
  }
  return {
    state: { losses, permanent: false, lost: true },
    response: { action: "rebuild", attempt: losses },
  };
}

/** Record a `webglcontextrestored`. Does not clear the count: the budget is
 *  per session, and a context that comes back is exactly the case the second
 *  loss has to be counted against. */
export function noteRestore(state: ContextLossState): ContextLossState {
  return { ...state, lost: false };
}

/** Whether the scene should be drawing at all right now. */
export function canRender(state: ContextLossState): boolean {
  return !state.lost && !state.permanent;
}
