/**
 * The spine — §E16, M9.1.
 *
 * > **Spine.** One normalised `t ∈ [0,1]` driven by a damped scroll proxy
 * > (Lenis, `lerp ≈ 0.075`) so the camera has weight and never jitters.
 *
 * > **Scroll controls distance travelled, not playback.** Stop scrolling and
 * > the character stops walking.
 *
 * Those two sentences are the whole module, and the second is the harder of the
 * two because it is a *negative* requirement: nothing downstream of here may be
 * a function of wall-clock time. A walk cycle advanced by `performance.now()`
 * would keep walking while the reader stands still, and the film would quietly
 * become a video with a scrollbar attached — which §E16 says explicitly it is
 * not. So `SpineState` carries no timestamp, {@link walkMetres} is a pure
 * function of `t`, and F12's gate asserts the property directly by holding the
 * scroll still and reading the distance twice.
 *
 * **The damping is here and not in the camera.** `clay/camera.ts` damps the rig
 * toward a ground target at `WORLD.camera.dampingPerSecond`, which is right for
 * a camera being *led* by a selection. The film leads it with a scroll position
 * instead, and damping the same signal twice produces a camera that lags the
 * reader by a quarter of a second and reads as a dropped frame rather than as
 * weight. So the film pins the rig and the only smoothing in the chain is this
 * one.
 *
 * **Frame-rate independence, and why Lenis' own number needs converting.**
 * Lenis' `lerp` is a fraction closed *per frame* at 60 Hz. Applied unchanged on
 * a 144 Hz laptop it closes 2.4x faster, so the same flick of a trackpad
 * travels a different distance on two machines — and a golden image taken at a
 * fixed scroll offset stops being reproducible. `SPINE.ratePerSecond` converts
 * it once, and `damp()` (`lib/easing.ts`) is the same correctly-sampled curve
 * the clay camera already uses.
 *
 * **No React.** §E14.2: the store drives shader uniforms and transforms through
 * transient subscriptions without a render. A spine that set state would
 * re-render the whole film sixty times a second, which is the one thing a
 * scroll-driven page cannot afford.
 */

import { WORLD } from "@/design/generated/tokens";
import { damp } from "@/lib/easing";

import { actAt, clampT, localT, type ActId, type FilmAct } from "./acts";

/** §E16's own numbers, in one place. */
export const SPINE = {
  /** §E16: *"a damped scroll proxy (Lenis, `lerp ≈ 0.075`)"*. */
  lerp: 0.075,
  /** The refresh rate `lerp` is quoted against. Lenis' own default. */
  referenceHz: 60,
  /**
   * The same damping expressed as a fraction closed per second, which is what a
   * frame-rate-independent `damp()` needs. From `1 - (1 - lerp)^hz = 1 - e^-r`.
   * Computed rather than written down: two numbers that must agree are one
   * number.
   */
  get ratePerSecond(): number {
    return -Math.log(1 - SPINE.lerp) * SPINE.referenceHz;
  },
  /**
   * How far the figure walks across Act 1, in ground metres.
   *
   * Derived from the frame's own extent rather than picked, for the reason
   * `configureCamera()` derives its far plane the same way: a tenant with a
   * wider `WORLD.extent` would otherwise have a figure that walks a fixed
   * distance across a larger city and appears not to move.
   */
  get walkMetres(): number {
    return WORLD.extent.halfMetres / 6;
  },
  /**
   * Below this, the spine is not moving.
   *
   * A damped proxy never mathematically arrives; it approaches. Without a floor
   * the film would report a walk advancing by a millionth of a metre per frame
   * forever, and F12's gate — *"stopping the scroll stops the walk"* — would be
   * true in physics and false in the assertion. One thousandth of the spine is
   * roughly a pixel of scroll on a nine-viewport page.
   */
  deadband: 0.001,
} as const;

export interface SpineState {
  /** The damped, normalised position along the film. */
  readonly t: number;
  /** The raw scroll position, undamped. Kept because the difference between the
   *  two is exactly what "the camera has weight" means, and a debug overlay
   *  that could not show both would be showing an opinion. */
  readonly rawT: number;
  readonly act: FilmAct;
  /** How far through the current act, `[0,1]`. */
  readonly local: number;
  /** Ground metres walked (§E16 Act 1). A pure function of `t`. */
  readonly walked: number;
  /** Whether the spine moved on this frame. `false` is the walk stopping. */
  readonly moving: boolean;
}

export type SpineListener = (state: SpineState) => void;

/**
 * Ground metres walked at `t` — the property that makes this a walk.
 *
 * Pure, monotonic, and with no time term anywhere in it. The figure covers the
 * whole distance across Act 1 and then stands still: Acts 2 and 3 are *"the
 * stop"* and *"the silence"*, and a figure that kept strolling through the
 * disappointment beat would undo the one movement §E16 asks to be held for a
 * full second.
 */
export function walkMetres(t: number): number {
  const clamped = clampT(t);
  const current = actAt(clamped);
  if (current.index < 1) return 0;
  if (current.index > 1) return SPINE.walkMetres;
  return SPINE.walkMetres * localT(clamped);
}

/** Assemble the state for a position. Exported so a test — and the proof route
 *  — can ask what the film looks like at a `t` without a scroll container. */
export function spineStateAt(t: number, rawT = t, moving = false): SpineState {
  const clamped = clampT(t);
  return {
    t: clamped,
    rawT: clampT(rawT),
    act: actAt(clamped),
    local: localT(clamped),
    walked: walkMetres(clamped),
    moving,
  };
}

/**
 * One damping step.
 *
 * Split out and exported because it is the piece with a number in it, and a
 * number in a scroll handler is a number nobody ever asserts. `dtSeconds` is
 * clamped for the same reason `scene.ts` clamps its own: the first frame after
 * a tab has been in the background is an hour long, and an unclamped step would
 * teleport the camera to the end of the film on the frame the reader returns.
 */
export function advanceT(current: number, target: number, dtSeconds: number): number {
  const dt = Math.min(Math.max(dtSeconds, 0), 0.25);
  const next = damp(current, target, SPINE.ratePerSecond, dt);
  return Math.abs(target - next) < SPINE.deadband ? target : next;
}

/**
 * The scroll proxy this spine is driven by.
 *
 * An interface rather than `Lenis` directly, for two reasons that are both
 * about being able to *run* the film: Lenis touches `window` in its
 * constructor, so a unit test cannot build one, and the proof route drives the
 * spine from a query parameter with no scrolling at all. `bindLenis()` is the
 * production implementation and the only place the library is named.
 */
export interface ScrollProxy {
  /** Normalised scroll position, `[0,1]`. */
  readonly progress: () => number;
  readonly destroy: () => void;
  /** Called once per frame by the spine, so a proxy that needs its own raf —
   *  Lenis does — is pumped by the same loop rather than a second one. */
  readonly advance?: (nowMs: number) => void;
}

/**
 * The film's clock — which is not a clock.
 *
 * Runs a `requestAnimationFrame` loop because the *damping* needs frames; what
 * it does not do is let a frame advance anything but the damping. Everything a
 * listener reads is a function of `t`.
 */
export class StorySpine {
  #listeners = new Set<SpineListener>();
  #proxy: ScrollProxy | null = null;
  #frame: number | null = null;
  #lastMs = 0;
  #t = 0;
  #rawT = 0;
  /** Set by `seek()`. While pinned the proxy is ignored entirely, which is what
   *  makes a golden image at a fixed `t` a fixed `t`. */
  #pinned: number | null = null;

  get state(): SpineState {
    return spineStateAt(this.#t, this.#rawT, false);
  }

  subscribe(listener: SpineListener): () => void {
    this.#listeners.add(listener);
    // Immediately, so a scene that mounts mid-film is dressed for where the
    // reader already is rather than for the top of the page.
    listener(this.state);
    this.#ensureRunning();
    return () => {
      this.#listeners.delete(listener);
      if (this.#listeners.size === 0) this.#stop();
    };
  }

  attach(proxy: ScrollProxy): void {
    this.#proxy?.destroy();
    this.#proxy = proxy;
    this.#ensureRunning();
  }

  detach(): void {
    this.#proxy?.destroy();
    this.#proxy = null;
    this.#stop();
  }

  /**
   * Jump to a position and hold there.
   *
   * `null` releases. Used by the proof route, and by any test that wants act 6
   * without simulating four thousand pixels of wheel.
   */
  seek(t: number | null): void {
    this.#pinned = t === null ? null : clampT(t);
    if (this.#pinned !== null) {
      this.#t = this.#pinned;
      this.#rawT = this.#pinned;
      this.#stop();
      this.#emit(false);
    } else {
      this.#ensureRunning();
    }
  }

  /** Advance one frame by hand. The seam a unit test drives; production calls
   *  it from `requestAnimationFrame`. */
  step(dtSeconds: number): void {
    if (this.#pinned !== null) return;
    const before = this.#t;
    this.#rawT = clampT(this.#proxy?.progress() ?? this.#rawT);
    this.#t = advanceT(this.#t, this.#rawT, dtSeconds);
    this.#emit(this.#t !== before);
  }

  #ensureRunning(): void {
    if (this.#frame !== null || this.#pinned !== null) return;
    if (typeof requestAnimationFrame !== "function") return; // SSR and jsdom
    this.#lastMs = performance.now();
    const tick = (nowMs: number): void => {
      this.#proxy?.advance?.(nowMs);
      const dt = (nowMs - this.#lastMs) / 1000;
      this.#lastMs = nowMs;
      this.step(dt);
      this.#frame = requestAnimationFrame(tick);
    };
    this.#frame = requestAnimationFrame(tick);
  }

  #stop(): void {
    if (this.#frame !== null) {
      cancelAnimationFrame(this.#frame);
      this.#frame = null;
    }
  }

  #emit(moving: boolean): void {
    const state = spineStateAt(this.#t, this.#rawT, moving);
    for (const listener of this.#listeners) listener(state);
  }
}

/** The act a `t` belongs to, by id — the shape the DOM layer wants. */
export function actIdAt(t: number): ActId {
  return actAt(t).id;
}
