"use client";

/**
 * §E12's Web Audio graph — F16, M10.
 *
 * > Muted by default, with an unmute affordance that is designed rather than
 * > hidden; state persists per user. Corrects §E2 defect #9.
 * > … Web Audio graph with a master duck on modal open. All buses respect
 * > `prefers-reduced-motion` as a proxy for sensory sensitivity.
 *
 * The graph is four bus gains into a master gain into the destination, and
 * everything interesting about this module is in *when it exists*.
 *
 * **No `AudioContext` is created until somebody unmutes.** Not an optimisation:
 * a context created on page load is a context the browser has to suspend and
 * resume, it shows up in Chrome's autoplay diagnostics, and on a muted
 * deployment — which is every deployment, by default — it is a device the
 * product opened and never used. The first unmute builds the graph, renders the
 * cues it needs, and every later one just flips a gain.
 *
 * **`prefers-reduced-motion` mutes every bus, and an explicit unmute still
 * wins.** §E12 names the preference as a proxy for sensory sensitivity, so the
 * default under it is silence and the control says *why* it is silent rather
 * than disappearing (§E3.2: a degradation is designed, not hidden). But a
 * person who then presses unmute has said something about themselves that
 * outranks a system-wide default — the same reasoning §E13 uses for a forced
 * tier, and the opposite of overriding a stated preference, because this
 * preference is being overridden by the person who stated it.
 *
 * **The duck is one gain and one ramp.** A modal opens, the master drops to
 * `SOUND.gain.ducked` over `SOUND.duckMs` — two frames of the 12 fps clock, so
 * the duck lands on the same beat as the panel's own Lift (§E11.1). Nested
 * modals are counted rather than toggled: two overlapping ducks that each
 * restore on close would leave the second one restoring while the first is
 * still open.
 */

import { SOUND } from "@/design/generated/tokens";

import { BUSES, type Bus, type Recipe } from "./cues";
import { render } from "./synth";

const STORAGE_KEY = "nemesis.sound";

export interface SoundState {
  readonly muted: boolean;
  /** Whether the person's operating system asks for reduced motion. */
  readonly reducedMotion: boolean;
  /** Whether an `AudioContext` exists yet. The seam a test reads. */
  readonly running: boolean;
}

type Listener = (state: SoundState) => void;

/**
 * Read the persisted preference.
 *
 * **Muted is the default and an unreadable store is muted too.** A private
 * window, a locked-down webview, a browser with site data blocked — every one
 * of them is a person the product must not start making noise at.
 */
function persistedMute(): boolean {
  if (typeof window === "undefined") return true;
  try {
    return window.localStorage.getItem(STORAGE_KEY) !== "on";
  } catch {
    return true;
  }
}

function persist(muted: boolean): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, muted ? "off" : "on");
  } catch {
    // A person who cannot persist the preference still gets it for this
    // session. Refusing to unmute because we cannot remember it would be
    // punishing them for their privacy settings.
  }
}

function prefersReducedMotion(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

/**
 * The gain a bus runs at — and where `prefers-reduced-motion` actually bites.
 *
 * §E12 asks every bus to respect the preference *"as a proxy for sensory
 * sensitivity"*, and the honest reading of that is not "turn everything off",
 * because turning everything off is what the mute already does and would make
 * the clause a duplicate of the default. The distinction that matters to
 * somebody with sensory sensitivity is **continuous versus discrete**:
 *
 * · `ambient` and `positional` are *always playing*. They are the constant
 *   sensory load, and under the preference they are silent — including for
 *   somebody who has deliberately unmuted, because what they unmuted for is the
 *   information, not the room tone.
 * · `foley` and `alert` are *one sound when one thing happens*. They carry
 *   meaning — a stamp confirms, the struck note is the fail-safe — and
 *   silencing them would remove information rather than load.
 *
 * A free function rather than a method so `tests/sound.test.ts` can
 * assert it without a browser: a clause of the F16 gate that can only be
 * checked by listening is a clause nobody checks.
 */
export function busGainFor(bus: Bus, reducedMotion: boolean): number {
  if (reducedMotion && (bus === "ambient" || bus === "positional")) return 0;
  return SOUND.gain[bus];
}

class SoundGraph {
  #context: AudioContext | null = null;
  #master: GainNode | null = null;
  #buses = new Map<Bus, GainNode>();
  #buffers = new Map<string, AudioBuffer>();
  #listeners = new Set<Listener>();
  #muted = true;
  #reducedMotion = false;
  #modals = 0;
  #hydrated = false;
  /**
   * The published state, **by identity**.
   *
   * `<SoundControl>` reads this through `useSyncExternalStore`, which compares
   * snapshots by reference — so a `state()` that built a fresh object on every
   * call is an infinite render loop. It was exactly that, and the loop was not
   * subtle: the masthead re-rendered continuously and Playwright reported the
   * unmute button as *"element was detached from the DOM, retrying"* until the
   * case timed out. The same trap `ink/machine.ts` documents on its own
   * snapshot, found twice in one stage, which is why both now say so.
   */
  #snapshot: SoundState = { muted: true, reducedMotion: false, running: false };

  /**
   * Read the browser's two preferences, once, in an effect.
   *
   * Not in the constructor: this module is imported by components that render
   * on the server, and `localStorage` and `matchMedia` are neither of them
   * there. A graph that read them at import time would either throw during SSR
   * or, worse, answer "unmuted" on the server and "muted" on the client, which
   * is a hydration mismatch on a control whose whole job is to state a fact.
   */
  hydrate(): void {
    if (this.#hydrated || typeof window === "undefined") return;
    this.#hydrated = true;
    this.#reducedMotion = prefersReducedMotion();
    this.#muted = persistedMute();
    this.#publish();
  }

  state(): SoundState {
    return this.#snapshot;
  }

  subscribe(listener: Listener): () => void {
    this.#listeners.add(listener);
    return () => {
      this.#listeners.delete(listener);
    };
  }

  /**
   * Turn the sound on or off.
   *
   * The one place `AudioContext` is constructed, and it is constructed inside
   * the call that a click produced — browsers require a user gesture and a
   * context built anywhere else starts suspended.
   */
  setMuted(muted: boolean): void {
    this.#muted = muted;
    persist(muted);
    if (!muted) this.#ensureContext();
    this.#applyMasterGain();
    this.#publish();
  }

  /** This graph's gain for a bus, given the preference it read at hydration. */
  busGain(bus: Bus): number {
    return busGainFor(bus, this.#reducedMotion);
  }

  /** §E12's master duck. Counted, not toggled — see the module note. */
  pushModal(): void {
    this.#modals += 1;
    this.#applyMasterGain();
  }

  popModal(): void {
    this.#modals = Math.max(0, this.#modals - 1);
    this.#applyMasterGain();
  }

  /**
   * Play a one-shot cue.
   *
   * A no-op when muted, and deliberately not an error: every caller is a
   * surface reacting to something real, and a surface should not have to ask
   * whether the product is audible before reporting that a stamp landed.
   */
  play(name: string, bus: Bus, recipe: Recipe, gain = 1): void {
    if (this.#muted) return;
    const context = this.#ensureContext();
    if (context === null) return;
    const target = this.#buses.get(bus);
    if (target === undefined) return;

    const source = context.createBufferSource();
    source.buffer = this.#buffer(name, recipe, context);
    const level = context.createGain();
    level.gain.value = gain;
    source.connect(level).connect(target);
    source.start();
  }

  /**
   * Start a looping voice and return the handle that stops it.
   *
   * Used by the ambient bed and by positional foley. Returns a no-op stopper
   * when muted so a caller's cleanup is unconditional — a caller that had to
   * remember whether it had actually started something is a caller that leaks
   * one voice the first time somebody unmutes mid-scene.
   */
  loop(
    name: string,
    bus: Bus,
    recipe: Recipe,
    options: { readonly gain?: number; readonly pan?: number; readonly fadeMs?: number } = {},
  ): () => void {
    if (this.#muted) return () => undefined;
    const context = this.#ensureContext();
    if (context === null) return () => undefined;
    const target = this.#buses.get(bus);
    if (target === undefined) return () => undefined;

    const source = context.createBufferSource();
    source.buffer = this.#buffer(name, recipe, context);
    source.loop = true;

    const level = context.createGain();
    const gain = options.gain ?? 1;
    const fadeMs = options.fadeMs ?? SOUND.crossfadeMs;
    // **The `setValueAtTime` before the ramp is not redundant.** A
    // `linearRampToValueAtTime` interpolates from the *last scheduled event*,
    // and with none scheduled the ramp's start time is the context's own
    // creation — so the ramp is already over before the source starts and the
    // bed arrives at full gain instantly. Anchoring the start is what makes it
    // a fade rather than a click.
    const start = context.currentTime;
    level.gain.setValueAtTime(0, start);
    level.gain.linearRampToValueAtTime(gain, start + fadeMs / 1000);

    const panner = context.createStereoPanner();
    panner.pan.value = Math.max(-1, Math.min(1, options.pan ?? 0));

    source.connect(level).connect(panner).connect(target);
    source.start(start);

    return () => {
      const now = context.currentTime;
      level.gain.cancelScheduledValues(now);
      level.gain.setValueAtTime(level.gain.value, now);
      level.gain.linearRampToValueAtTime(0, now + fadeMs / 1000);
      // Stopped after the fade rather than at it: a source stopped while its
      // gain is still audible is a click, and a click is the one artefact a
      // cross-fade exists to avoid.
      source.stop(now + fadeMs / 1000 + 0.05);
    };
  }

  /** Release everything. Called when the last consumer unmounts. */
  close(): void {
    void this.#context?.close();
    this.#context = null;
    this.#master = null;
    this.#buses.clear();
    this.#buffers.clear();
    this.#publish();
  }

  #ensureContext(): AudioContext | null {
    if (this.#context !== null) return this.#context;
    if (typeof window === "undefined" || typeof AudioContext === "undefined") return null;

    const context = new AudioContext({ sampleRate: SOUND.sampleRate });
    const master = context.createGain();
    master.connect(context.destination);

    for (const bus of BUSES) {
      const node = context.createGain();
      node.gain.value = this.busGain(bus);
      node.connect(master);
      this.#buses.set(bus, node);
    }

    this.#context = context;
    this.#master = master;
    this.#applyMasterGain();
    this.#publish();
    return context;
  }

  #buffer(name: string, recipe: Recipe, context: AudioContext): AudioBuffer {
    const cached = this.#buffers.get(name);
    if (cached !== undefined) return cached;
    const samples = render(recipe);
    const buffer = context.createBuffer(1, samples.length, SOUND.sampleRate);
    // Copied through a fresh view rather than handed straight over: the DOM
    // types narrow `copyToChannel` to a `Float32Array<ArrayBuffer>`, and a
    // `Float32Array` returned from a plain function is `ArrayBufferLike` —
    // which could be a `SharedArrayBuffer`, and a shared buffer is genuinely
    // not something an audio buffer may alias.
    buffer.copyToChannel(new Float32Array(samples), 0);
    this.#buffers.set(name, buffer);
    return buffer;
  }

  #applyMasterGain(): void {
    const master = this.#master;
    const context = this.#context;
    if (master === null || context === null) return;
    const target = this.#muted
      ? 0
      : this.#modals > 0
        ? SOUND.gain.ducked * SOUND.gain.master
        : SOUND.gain.master;
    const now = context.currentTime;
    master.gain.cancelScheduledValues(now);
    master.gain.setValueAtTime(master.gain.value, now);
    master.gain.linearRampToValueAtTime(target, now + SOUND.duckMs / 1000);
  }

  #publish(): void {
    this.#snapshot = {
      muted: this.#muted,
      reducedMotion: this.#reducedMotion,
      running: this.#context !== null,
    };
    for (const listener of this.#listeners) listener(this.#snapshot);
  }
}

/**
 * The one graph.
 *
 * A module singleton for the same reason `RealtimeProvider` keeps one socket:
 * two `AudioContext`s in a tab is two devices, two master gains, and a duck
 * that only lowers half the product.
 */
export const sound = new SoundGraph();
