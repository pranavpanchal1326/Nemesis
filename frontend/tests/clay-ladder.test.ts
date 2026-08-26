import { describe, expect, it } from "vitest";

import { BUDGET } from "../src/design/generated/tokens.ts";
import {
  advance,
  DEGRADATION_LADDER,
  INITIAL_QUALITY,
  MAX_POSITION,
  planFor,
} from "../src/clay/quality.ts";
import {
  canRender,
  INITIAL_LOSS_STATE,
  LOSS_CAUSE_KEY,
  noteLoss,
  noteRestore,
} from "../src/clay/context-loss.ts";
import {
  capabilitiesFor,
  forcedTier,
  ladderRung,
  pressQualityFor,
  rendersClay,
  SERVER_SIGNALS,
  tierFor,
  TIERS,
  type DeviceSignals,
} from "../src/clay/tier.ts";

/**
 * §E13's ladder and §E23's budgets — F8's gate, asserted where it is cheap.
 *
 * The Phase 19 gate clause *"forced context loss recovers to a correct scene"*
 * is a browser assertion and lives in `tests/clay.spec.ts`. What is here is the
 * **policy** underneath it, which is a pure state machine on purpose: a rule
 * about when to give up is a rule that must be arguable without a GPU, because
 * the machine where it is wrong is never the machine you have.
 */

const CAPABLE: DeviceSignals = {
  scripting: true,
  webgpu: true,
  webgl2: true,
  deviceMemoryGb: 16,
  reducedMotion: false,
  measuredFps: null,
};

describe("§E13 — every trigger reaches its documented tier", () => {
  it("takes WebGPU where there is an adapter, and WebGL 2 where the renderer fell back", () => {
    expect(tierFor(ladderRung(CAPABLE), "webgpu")).toBe("S");
    expect(tierFor(ladderRung(CAPABLE), "webgl2")).toBe("A");
  });

  it("drops to the lite tier below the published memory and frame-rate thresholds", () => {
    expect(ladderRung({ ...CAPABLE, deviceMemoryGb: BUDGET.liteMemoryGb - 1 })).toBe("B");
    expect(ladderRung({ ...CAPABLE, measuredFps: BUDGET.liteFps - 1 })).toBe("B");
    expect(ladderRung({ ...CAPABLE, deviceMemoryGb: BUDGET.liteMemoryGb })).toBe("gpu");
  });

  it("treats reduced motion as a consent boundary that outranks a fast machine", () => {
    // The ladder is a consent boundary at its top and a performance policy
    // below it. No amount of headroom moves this one.
    expect(ladderRung({ ...CAPABLE, reducedMotion: true })).toBe("C");
    expect(planFor(0, "C", "webgpu").tier).toBe("C");
  });

  it("renders text where nothing can run, including on the server", () => {
    expect(ladderRung({ ...CAPABLE, scripting: false })).toBe("D");
    expect(ladderRung(SERVER_SIGNALS)).toBe("D");
  });

  it("falls to the storyboard rather than erroring when a renderer never arrived", () => {
    expect(tierFor("gpu", null)).toBe("C");
  });

  it("puts a canvas on screen for exactly S, A and B", () => {
    expect(TIERS.filter(rendersClay)).toEqual(["S", "A", "B"]);
  });

  it("gives every tier a press quality, and the lite tier a bolder print", () => {
    for (const tier of TIERS) expect(pressQualityFor(tier)).toBeTypeOf("string");
    expect(pressQualityFor("B")).toBe("reduced");
    expect(pressQualityFor("C")).toBe("flat");
  });

  it("accepts a forced tier from the URL and ignores a typo", () => {
    // Phase 20's gate forces every trigger by navigating, and a typo in a URL
    // must not silently pin the product to a rung.
    expect(forcedTier("?tier=B")).toBe("B");
    expect(forcedTier("?tier=Z")).toBeNull();
    expect(forcedTier("")).toBeNull();
    expect(forcedTier(null)).toBeNull();
  });
});

describe("§E23 — the quality manager degrades effects before it degrades frame rate", () => {
  const slow = { fps: BUDGET.fps * 0.5, elapsedMs: BUDGET.fpsSampleMs };
  const fast = { fps: BUDGET.fps, elapsedMs: BUDGET.fpsSampleMs };

  it("turns the press's dial first — the one degradation that improves the picture", () => {
    // §E6.4, and it is position 1 of 5 rather than a footnote.
    expect(DEGRADATION_LADDER[1].cause).toBe("degraded.pressReduced");
    expect(planFor(1, "gpu", "webgpu").press).toBe("reduced");
    expect(planFor(1, "gpu", "webgpu").capabilities.depthOfField).toBe(true);
  });

  it("waits a full sample window before giving anything up", () => {
    const half = { fps: slow.fps, elapsedMs: BUDGET.fpsSampleMs / 2 };
    expect(advance(INITIAL_QUALITY, half).position).toBe(0);
    expect(advance(advance(INITIAL_QUALITY, half), half).position).toBe(1);
  });

  it("does not walk itself down a ladder on a machine that is meeting its budget", () => {
    // A vsynced display never reports 60.00. A manager that degraded on
    // "below 60" would end up at the bottom of the ladder on a healthy machine.
    const vsynced = { fps: BUDGET.fps - 0.6, elapsedMs: BUDGET.fpsSampleMs * 10 };
    expect(advance(INITIAL_QUALITY, vsynced).position).toBe(0);
  });

  it("climbs back only after consecutive healthy windows, and a stutter resets them", () => {
    let state = advance(INITIAL_QUALITY, slow);
    expect(state.position).toBe(1);

    for (let i = 0; i < BUDGET.recoverWindows - 1; i += 1) state = advance(state, fast);
    expect(state.position).toBe(1);

    const stuttered = advance(state, { fps: BUDGET.fps * 0.5, elapsedMs: 10 });
    expect(stuttered.healthyWindows).toBe(0);

    for (let i = 0; i < BUDGET.recoverWindows; i += 1) state = advance(state, fast);
    expect(state.position).toBe(0);
  });

  it("stops at the bottom rather than running off the end", () => {
    let state = INITIAL_QUALITY;
    for (let i = 0; i < 20; i += 1) state = advance(state, slow);
    expect(state.position).toBe(MAX_POSITION);
  });

  it("never switches bloom off to save frames", () => {
    // §E7.3 reserves bloom for `safety_trigger_fired` and §E3.4 audits that it
    // is used nowhere else. Turning it off as a quality setting would switch off
    // the only visual the deterministic fail-safe has.
    for (let position = 0; position <= MAX_POSITION; position += 1) {
      const plan = planFor(position, "gpu", "webgpu");
      if (plan.capabilities.clay && (plan.tier === "S" || plan.tier === "A")) {
        expect(plan.capabilities.bloom).toBe(true);
      }
    }
  });

  it("gives up depth of field and gate weave before it gives up the scene", () => {
    expect(planFor(2, "gpu", "webgpu").capabilities.depthOfField).toBe(false);
    expect(planFor(2, "gpu", "webgpu").capabilities.gateWeave).toBe(false);
    expect(planFor(2, "gpu", "webgpu").capabilities.clay).toBe(true);
    expect(planFor(4, "gpu", "webgpu").tier).toBe("B");
    expect(planFor(5, "gpu", "webgpu").tier).toBe("C");
  });

  it("names what it gave up, as a locale key rather than a sentence", () => {
    // The banner's copy has to be reachable by the Phase 5 locale registry.
    for (const entry of DEGRADATION_LADDER) {
      if (entry.cause === null) continue;
      expect(entry.cause.startsWith("degraded.")).toBe(true);
    }
  });

  it("never lets the manager climb above the device's own ceiling", () => {
    expect(planFor(0, "B", "webgpu").tier).toBe("B");
    expect(planFor(0, "D", "webgpu").tier).toBe("D");
  });
});

describe("Phase 19 — context loss recovers once, then drops calmly", () => {
  it("rebuilds on the first loss", () => {
    const first = noteLoss(INITIAL_LOSS_STATE);
    expect(first.response).toEqual({ action: "rebuild", attempt: 1 });
    expect(canRender(first.state)).toBe(false);
    expect(canRender(noteRestore(first.state))).toBe(true);
  });

  it("stops after the second, and says why", () => {
    const first = noteLoss(INITIAL_LOSS_STATE);
    const second = noteLoss(noteRestore(first.state));
    expect(second.response).toEqual({ action: "degrade", cause: LOSS_CAUSE_KEY });
    expect(second.state.permanent).toBe(true);
    expect(canRender(second.state)).toBe(false);
  });

  it("counts a context that came back — which is the case the budget is for", () => {
    // A restore that cleared the count would make an alternating
    // lose/restore/lose device rebuild for ever.
    const first = noteLoss(INITIAL_LOSS_STATE);
    expect(noteRestore(first.state).losses).toBe(1);
  });

  it("never returns from permanent, because the session is the unit", () => {
    const first = noteLoss(INITIAL_LOSS_STATE);
    const second = noteLoss(noteRestore(first.state));
    expect(noteRestore(second.state).permanent).toBe(true);
  });
});

describe("§E13 — the capabilities table matches the tier column it came from", () => {
  it("gives the full lens to S and A, and none of it to C and D", () => {
    for (const tier of ["S", "A"] as const) {
      const capabilities = capabilitiesFor(tier);
      expect(capabilities.depthOfField).toBe(true);
      expect(capabilities.bloom).toBe(true);
      expect(capabilities.gateWeave).toBe(true);
      expect(capabilities.movingSun).toBe(true);
    }
    for (const tier of ["C", "D"] as const) {
      const capabilities = capabilitiesFor(tier);
      expect(capabilities.clay).toBe(false);
      expect(capabilities.bloom).toBe(false);
      expect(capabilities.weather).toBe(false);
    }
  });

  it("keeps the lite tier's sun real and simply stops moving it", () => {
    const lite = capabilitiesFor("B");
    expect(lite.clay).toBe(true);
    expect(lite.weather).toBe(true);
    expect(lite.movingSun).toBe(false);
    expect(lite.depthOfField).toBe(false);
  });
});
