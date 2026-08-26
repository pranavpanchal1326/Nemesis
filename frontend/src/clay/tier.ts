/**
 * The fallback ladder — §E13, M8.
 *
 * Five rungs, and the two at the top are **not ours to choose**. ADR-0037:
 *
 * > The renderer is `WebGPURenderer` in every tier. Tier S and Tier A of the
 * > fallback ladder are the renderer's own backend selection, not our branch.
 *
 * That sentence is the shape of this module. `ladderRung()` decides how far up
 * the ladder a device is *allowed* to go and returns `"gpu"` for the top —
 * deliberately not `"S"`, because at that moment nobody knows yet. The renderer
 * initialises, reports which backend it got, and `tierFor()` turns the pair into
 * the concrete tier that names the picture. A codebase that guessed S from
 * `navigator.gpu` would be maintaining a second backend-selection policy beside
 * three.js's, and the two would disagree on exactly the machines nobody has.
 *
 * **The rungs, and why they are ordered this way** (§E13):
 *
 *   D — text        no scripting: JS off, a crawler, a 2G connection that
 *                   never ran the bundle. Server-rendered semantic article.
 *   C — storyboard  `prefers-reduced-motion`, or no WebGL at all.
 *   B — lite        `deviceMemory < 4`, or under 40 fps measured for 3 s.
 *   gpu             everything else; the renderer picks WebGPU or WebGL 2.
 *
 * C outranks B, and that ordering is a decision rather than an accident. B is a
 * *performance* rung and C is a *consent* rung: somebody who has asked their
 * operating system for reduced motion has said something, and a machine with
 * 3 GB of RAM has not. Letting a fast machine's measurements pull a
 * reduced-motion visitor back up the ladder would override a stated preference
 * with a benchmark.
 *
 * **Everything here is a pure function of a signal object**, because §E25's
 * Phase 20 gate is *"every fallback tier is exercised in CI by forcing its
 * trigger"*. A trigger that can only be forced by owning the hardware is a
 * trigger nobody tests; `readSignals()` reads the browser once, at the edge,
 * and every decision after that is data.
 */

import { BUDGET } from "@/design/generated/tokens";

/** The five rungs §E13 names. */
export type Tier = "S" | "A" | "B" | "C" | "D";

/** What the ladder decides before the renderer has spoken. */
export type Rung = "gpu" | "B" | "C" | "D";

/** What the renderer reports back once it has initialised. */
export type Backend = "webgpu" | "webgl2";

export const TIERS = ["S", "A", "B", "C", "D"] as const satisfies readonly Tier[];

export interface DeviceSignals {
  /** Did the bundle run at all? `false` is Tier D, and is the only state this
   *  module can describe but never observe from inside itself. */
  readonly scripting: boolean;
  /** `navigator.gpu` present. Necessary for WebGPU, never sufficient — the
   *  adapter request can still fail, which is why this only gates the rung. */
  readonly webgpu: boolean;
  /** A WebGL 2 context was actually obtained, not merely advertised. */
  readonly webgl2: boolean;
  /** `navigator.deviceMemory`, in GB. `null` where the browser does not say —
   *  Firefox and Safari do not — and absence is **not** treated as a small
   *  number. Guessing "probably low" would drop every Safari visitor to Tier B
   *  on no evidence, and §E13's B trigger has a measured alternative. */
  readonly deviceMemoryGb: number | null;
  /** `prefers-reduced-motion: reduce`. */
  readonly reducedMotion: boolean;
  /** Sustained frames per second over `BUDGET.fpsSampleMs`, or `null` before
   *  enough frames have been seen to say anything. */
  readonly measuredFps: number | null;
}

/** The signals a server render can honestly claim: none of them. */
export const SERVER_SIGNALS: DeviceSignals = {
  scripting: false,
  webgpu: false,
  webgl2: false,
  deviceMemoryGb: null,
  reducedMotion: false,
  measuredFps: null,
};

/**
 * How far up the ladder this device may go.
 *
 * Ordered most-constraining first, and each branch is one clause of §E13's
 * trigger column.
 */
export function ladderRung(signals: DeviceSignals): Rung {
  if (!signals.scripting) return "D";
  if (signals.reducedMotion) return "C";
  if (!signals.webgpu && !signals.webgl2) return "C";
  if (signals.deviceMemoryGb !== null && signals.deviceMemoryGb < BUDGET.liteMemoryGb) return "B";
  if (signals.measuredFps !== null && signals.measuredFps < BUDGET.liteFps) return "B";
  return "gpu";
}

/**
 * The concrete tier, once the renderer has said which backend it took.
 *
 * `backend === null` means "asked for a GPU rung and did not get a renderer" —
 * an adapter that vanished, a context that would not create. That is not an
 * error state to surface as one: it is Tier C, which is a complete, designed,
 * reviewed edit of the same content (§E3.2).
 */
export function tierFor(rung: Rung, backend: Backend | null): Tier {
  if (rung === "D" || rung === "C") return rung;
  if (backend === null) return "C";
  if (rung === "B") return "B";
  return backend === "webgpu" ? "S" : "A";
}

/** Does this tier put a canvas on the screen at all? */
export function rendersClay(tier: Tier): boolean {
  return tier === "S" || tier === "A" || tier === "B";
}

/**
 * Will a device at this rung *attempt* a canvas?
 *
 * The same question as `rendersClay()`, asked one step earlier — and it has to
 * exist separately because `tierFor(rung, null)` correctly answers **C** before
 * a renderer exists, and a host that used that answer to decide whether to
 * build a renderer would never build one. That is not a hypothetical: it is the
 * defect `tests/clay.spec.ts` caught on its first honest run, where every tier
 * reported C on a machine with both a WebGPU adapter and WebGL 2.
 *
 * So: the *rung* decides whether to try, and the *backend* decides which of S
 * and A the attempt turned out to be (ADR-0037).
 */
export function rungRendersClay(rung: Rung): boolean {
  return rung === "gpu" || rung === "B";
}

/**
 * The rung a forced tier implies.
 *
 * `?tier=S` and `?tier=A` are the same *device* instruction — take the GPU path
 * — and differ only in which backend the renderer ends up on, which is not
 * ours to force from a URL. Forcing WebGL 2 specifically is a renderer option
 * (`forceWebGL`), not a tier.
 */
export function rungOf(tier: Tier): Rung {
  switch (tier) {
    case "S":
    case "A":
      return "gpu";
    case "B":
      return "B";
    case "C":
      return "C";
    case "D":
      return "D";
  }
}

/**
 * §E6.4's dial, per tier — the ladder's **first** move (§E23).
 *
 * > Adaptive quality degrades effects before it degrades frame rate, and the
 * > first thing it turns is the press's quality dial — which is the one
 * > degradation in this product that improves the picture.
 *
 * Tier B prints in two inks at a coarse screen, which §E13 requires to read
 * *"as a bolder print, not a worse one"*. Tiers C and D are printed too: the
 * storyboard is nine riso prints and the article is solid ink on a sheet.
 */
export function pressQualityFor(tier: Tier): "full" | "reduced" | "flat" {
  switch (tier) {
    case "S":
    case "A":
      return "full";
    case "B":
      return "reduced";
    case "C":
    case "D":
      return "flat";
  }
}

/**
 * Which effects this tier is allowed. §E13's *renders* column, as flags the
 * scene reads instead of re-deriving from a tier name in four places.
 */
export interface TierCapabilities {
  readonly clay: boolean;
  readonly depthOfField: boolean;
  readonly bloom: boolean;
  readonly gateWeave: boolean;
  /** A moving sun, driven by the tenant's real local time (§E7.4). Tier B
   *  keeps the sun but stops moving it: the light is still the city's real
   *  light, it just stops being recomputed. */
  readonly movingSun: boolean;
  readonly weather: boolean;
}

export function capabilitiesFor(tier: Tier): TierCapabilities {
  const clay = rendersClay(tier);
  const full = tier === "S" || tier === "A";
  return {
    clay,
    depthOfField: full,
    bloom: full,
    gateWeave: full,
    movingSun: full,
    weather: clay,
  };
}

/**
 * Read the browser, once.
 *
 * Everything defensive here is a real deployment: an embedded webview with no
 * `matchMedia`, a locked-down profile that throws on a WebGL probe, a browser
 * that has never heard of `deviceMemory`. None of them is a reason to fail to
 * draw a map.
 *
 * **The WebGL 2 probe creates and immediately discards a context.** That is the
 * only honest way to answer the question — `navigator.userAgent` sniffing and
 * feature tables both lie about exactly the machines that matter — and the
 * discarded context is one, not one per component, because this runs once.
 */
export function readSignals(): DeviceSignals {
  if (typeof window === "undefined") return SERVER_SIGNALS;

  return {
    scripting: true,
    webgpu: "gpu" in navigator,
    webgl2: probeWebgl2(),
    deviceMemoryGb: readDeviceMemory(),
    reducedMotion: prefersReducedMotion(),
    measuredFps: null,
  };
}

export function prefersReducedMotion(): boolean {
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

function readDeviceMemory(): number | null {
  const value: unknown = (navigator as { deviceMemory?: unknown }).deviceMemory;
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function probeWebgl2(): boolean {
  try {
    const canvas = document.createElement("canvas");
    const context = canvas.getContext("webgl2");
    if (context === null) return false;
    // Hand it back rather than waiting for the GC. Browsers cap live contexts
    // at a small number and evict the oldest, and the oldest would shortly be
    // the scene's own.
    context.getExtension("WEBGL_lose_context")?.loseContext();
    return true;
  } catch {
    return false;
  }
}

/**
 * The forced tier, for CI and for a reviewer who needs to see Tier B on a
 * machine that will never trigger it.
 *
 * A query parameter rather than an environment variable, because Phase 20's
 * gate is *"every fallback tier is exercised in CI by forcing its trigger"* and
 * a Playwright test forces a trigger by navigating. Invalid values return
 * `null` and are ignored — a typo in a URL must not silently pin the product to
 * a rung.
 */
export function forcedTier(search: string | null | undefined): Tier | null {
  if (search === null || search === undefined || search === "") return null;
  const raw = new URLSearchParams(search).get("tier");
  return raw !== null && (TIERS as readonly string[]).includes(raw) ? (raw as Tier) : null;
}
