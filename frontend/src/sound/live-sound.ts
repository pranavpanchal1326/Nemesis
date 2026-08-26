"use client";

/**
 * The two sounds the *system* makes — §E12, F16.
 *
 * Everything else on the foley bus is something a person did: a stamp landed
 * because somebody decided, a shutter fired because somebody photographed. Two
 * cues are different — they are the pipeline speaking, and nobody is holding
 * the thing that made the noise:
 *
 * · **The merge**, on `cluster_match_found`. §E12 gives it its own cue for the
 *   same reason §E11.1 gives it its own motion: it is the moment the product's
 *   central claim becomes visible, and *"three soft taps converging into one
 *   low thump"* is that claim as sound.
 * · **The struck note**, on `safety_trigger_fired`. §E12: *"the only alarming
 *   sound in the product … §11.2's fail-safe should feel grave, not panicked."*
 *
 * **Both are bound to real events and to nothing else.** There is no
 * `playMerge()` for a button to call — the binding is a subscription to the
 * bus, in the same shape `clay/live.ts`, `story/live-story.ts` and
 * `ink/live-ink.ts` use, and the Phase 20 gate applies to a sound exactly as it
 * applies to a scene. A demo that could trigger the merge cue from a keypress
 * would be a demo with a soundboard in it.
 */

import type { RealtimeEnvelope } from "@/lib/realtime/envelope";
import { subscribeToEvents } from "@/lib/realtime/store";

import { CUES } from "./cues";
import { sound } from "./graph";

/**
 * Which event plays which cue.
 *
 * Two rows, and the §E3.4 audit checks that nothing else in `src/` plays either
 * of them. Keyed by shaped event type, like `ink/live-ink.ts`, and asserted
 * against the published set for the same reason: a binding to an event this
 * system does not publish is a sound that can never play.
 */
export const EVENT_CUES = {
  cluster_match_found: "merge",
  safety_trigger_fired: "alarm",
} as const satisfies Record<string, keyof typeof CUES>;

export function cueFor(eventType: string): keyof typeof CUES | null {
  return eventType in EVENT_CUES ? EVENT_CUES[eventType as keyof typeof EVENT_CUES] : null;
}

/** Attach the sound layer to the bus. Called once, by `<SoundProvider>`. */
export function followTheBus(): () => void {
  return subscribeToEvents((envelope: RealtimeEnvelope) => {
    const name = cueFor(envelope.event_type);
    if (name === null) return;
    const cue = CUES[name];
    sound.play(name, cue.bus, cue.recipe);
  });
}
