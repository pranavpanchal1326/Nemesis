/**
 * The 12 fps stepped clock — §E7.2.
 *
 * > Characters, props, flags, weather, pins: 12 fps stepped. Poses hold two
 * > frames, then snap. Camera: uncapped, damped, filmic.
 *
 * A smooth camera moving through a world that animates on twos is how
 * stop-motion is actually shot, and the contrast is what makes the result read
 * as *handmade* rather than as *a low frame rate*.
 *
 * One clock drives everything that steps: the press's misregistration jitter
 * (§E6.1 stage 3), the Rive characters (§E8.1), the pins, the weather. It is
 * deliberately a single module rather than a hook, because the shader layer
 * subscribes to it from outside React — §E14.2's "transient subscriptions that
 * drive shader uniforms without a React re-render" applies to time too.
 *
 * This is also *cheaper* than smooth animation: four of every five animation
 * updates are skipped.
 */

import { MOTION } from "@/design/generated/tokens";

export type SteppedListener = (step: number, seededRandom: number) => void;

const STEP_MS = MOTION.stepMs;

/** Deterministic PRNG. Golden-image regression needs the same jitter at the
 *  same step every run — §E24, "at fixed seed and camera". */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Step index for a given elapsed time. Exported so a test can assert a frame
 *  without running a clock. */
export function stepAt(elapsedMs: number): number {
  return Math.floor(elapsedMs / STEP_MS);
}

class SteppedClock {
  #listeners = new Set<SteppedListener>();
  #frame: number | null = null;
  #startMs = 0;
  #lastStep = -1;
  #seed = 1;

  /** Pin the clock to a fixed step. Golden images and the reduced-motion path
   *  both need a world that is not moving. */
  #pinned: number | null = null;

  subscribe(listener: SteppedListener): () => void {
    this.#listeners.add(listener);
    this.#ensureRunning();
    return () => {
      this.#listeners.delete(listener);
      if (this.#listeners.size === 0) this.#stop();
    };
  }

  /** Freeze at `step`, or release with `null`. */
  pin(step: number | null): void {
    this.#pinned = step;
    if (step !== null) {
      this.#stop();
      this.#emit(step);
    } else {
      this.#ensureRunning();
    }
  }

  seed(value: number): void {
    this.#seed = value;
  }

  get step(): number {
    return this.#pinned ?? this.#lastStep;
  }

  #ensureRunning(): void {
    if (this.#frame !== null || this.#pinned !== null) return;
    if (typeof requestAnimationFrame !== "function") return; // SSR and jsdom
    this.#startMs = performance.now();
    const tick = () => {
      const step = stepAt(performance.now() - this.#startMs);
      if (step !== this.#lastStep) this.#emit(step);
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

  #emit(step: number): void {
    this.#lastStep = step;
    const random = mulberry32(this.#seed + step)();
    for (const listener of this.#listeners) listener(step, random);
  }
}

export const steppedClock = new SteppedClock();
