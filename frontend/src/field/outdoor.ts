"use client";

/**
 * Outdoor mode — §E21, §E25 Phase 22, F17.
 *
 * > **Outdoor mode** goes near-monochrome with heavier weights and larger type.
 * > Sunlight on a phone at noon defeats every subtle palette; **design for
 * > gloves and glare, not for a design review.**
 *
 * **It is a ground, not a theme.** Setting `data-ground="outdoor"` re-points
 * every `--role-*` custom property at the outdoor values the token generator
 * computed, exactly as `[data-surface="console"]` re-points them at the light
 * table's (§E9.3). No component knows it is outdoors; every component is
 * correct there. That is the whole reason the role layer exists.
 *
 * **The floor is 7:1, and it is checked rather than claimed.** Every `min` in
 * the outdoor theme is 7 rather than 4.5 and `tests/contrast.test.ts` runs the
 * same loop over the third ground it already ran over two — so Phase 22's
 * *"outdoor mode passes contrast at 7:1 for primary text"* is a build failure
 * when it stops being true, not a line in a report.
 *
 * **Persisted, because the sun does not move between page loads.** Somebody who
 * turned it on this morning is still outside this afternoon.
 */

const STORAGE_KEY = "nemesis.outdoor";

/** The attribute the generated stylesheet keys off. */
export const OUTDOOR_ATTRIBUTE = "data-ground";

type Listener = () => void;

const listeners = new Set<Listener>();
let enabled = false;
let hydrated = false;

function read(): boolean {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "on";
  } catch {
    return false;
  }
}

export const outdoor = {
  /**
   * Read the stored preference, once, from an effect.
   *
   * Not at import time: this module is imported by a component that renders on
   * the server, where there is no `localStorage`, and a value that differed
   * between the server render and the first client render is a hydration
   * mismatch on the surface least able to afford a flash of the wrong palette.
   */
  hydrate(): void {
    if (hydrated || typeof window === "undefined") return;
    hydrated = true;
    enabled = read();
    for (const listener of listeners) listener();
  },

  enabled(): boolean {
    return enabled;
  },

  set(value: boolean): void {
    enabled = value;
    try {
      window.localStorage.setItem(STORAGE_KEY, value ? "on" : "off");
    } catch {
      // A person who cannot persist it still gets it for this session, which
      // is the same trade `lib/device.ts` and the sound graph both make.
    }
    for (const listener of listeners) listener();
  },

  subscribe(listener: Listener): () => void {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
};
