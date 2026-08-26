"use client";

/**
 * Where the character meets the event log — §E8.1, ADR-0041, F15's gate.
 *
 * > Because these are inputs and not timelines, the character **reacts to real
 * > backend events**. When `citizen_confirmed` arrives on the WebSocket,
 * > `relief` fires.
 *
 * That sentence is this file. It is nine lines of table and a subscription, and
 * the smallness is the point: every other part of the character layer is a pure
 * function, so this is the only place where the outside world gets in, and it is
 * the only place a reviewer has to read to know what can move a figure.
 *
 * **What comes from the log and what comes from the surface, stated plainly.**
 * §E8.1's inputs are of two kinds and they have two different sources, and
 * blurring that is how a "live" character quietly becomes a scripted one.
 *
 * · **Triggers come from the log.** `relief`, `disappointed` and `shutter` are
 *   in the table below, bound to shaped realtime events. Nothing in the
 *   application calls `fire()` for them outside this module — except
 *   `shoulders_drop`, which is the exception below and is labelled as one.
 *
 * · **Booleans come from the surface.** `walking`, `stopped`, `looking_down`
 *   and `raise_phone` describe what the *reader* is doing — scrolling the
 *   film's walk act, standing on the capture screen with the camera open. No
 *   backend event knows about any of that and pretending otherwise would be
 *   worse than saying so.
 *
 * · **`shoulders_drop` is fired by §E16 Act 2, which is narrative.** The film
 *   already declares that act `data-real="false"` in the DOM and
 *   `tests/story.spec.ts` asserts the split, so the one trigger this module
 *   does not own is the one trigger the surface has already told the reader is
 *   not a claim about this deployment.
 *
 * **Why `shutter` is fired by `exif_check_completed` rather than by the shutter
 * button.** The button is what takes the photograph; the *state* the machine
 * moves to is `wait`, and what a citizen is waiting for is the city to have the
 * evidence. `exif_check_completed` is the first thing the pipeline says about a
 * report that has actually arrived (§E16.1 gate 2 renders the same event as
 * *EXIF INTACT*). Binding the transition to the button would put a `fire()` on
 * a click path, which is exactly the shape the Phase 20 gate exists to refuse.
 *
 * **Why `disappointed` is `pipeline_stage_degraded`.** §24.2's third outcome:
 * the classifier was unavailable and the report was parked for a human. From
 * the citizen's side that is the disappointment — not an error dialog, not a
 * retry, just a report that has stopped moving — and §E3.3 says the interface
 * shows it rather than smoothing it over. It is also, on this deployment, the
 * commonest of the three (`docs/reports/story-merge-gate.md`), which means the
 * figure people actually see is the honest one.
 */

import type { RealtimeEnvelope } from "@/lib/realtime/envelope";
import { subscribeToEvents } from "@/lib/realtime/store";

import type { Machine, Trigger } from "./machine";

/**
 * The whole binding, as data.
 *
 * Keyed by shaped event type (`REALTIME_SHAPED_EVENT_TYPES`). Deliberately
 * partial: most events say nothing about a person, and a table that found a
 * pose for all ten would be a table inventing meanings — §E3.4's *"a vocabulary
 * that means two things means nothing"*, applied to posture.
 */
export const EVENT_TRIGGERS: Readonly<Record<string, Trigger>> = {
  citizen_confirmed: "relief",
  pipeline_stage_degraded: "disappointed",
  exif_check_completed: "shutter",
};

/** The trigger an event fires, or `null` for the events that move nobody. */
export function triggerFor(eventType: string): Trigger | null {
  return EVENT_TRIGGERS[eventType] ?? null;
}

export interface BindOptions {
  /**
   * Only react to events about this entity.
   *
   * The film and the citizen loop both follow *one* report, and a figure that
   * relaxed because somebody else's complaint was confirmed would be telling
   * the reader something untrue about their own. Omitted on a surface where the
   * figure stands for the system rather than for a report — a console empty
   * state, where any confirmation is good news.
   */
  readonly entityId?: string | null;
}

/**
 * Attach one machine to the bus.
 *
 * Returns the unsubscribe. Subscribes through `subscribeToEvents`, the same
 * seam `clay/live.ts` and `story/live-story.ts` use, so there is one socket per
 * tab no matter how many figures are on screen (ADR-0040).
 */
export function bindMachineToBus(machine: Machine, options: BindOptions = {}): () => void {
  const entityId = options.entityId ?? null;
  return subscribeToEvents((envelope: RealtimeEnvelope) => {
    if (entityId !== null && envelope.entity_id !== entityId) return;
    const trigger = triggerFor(envelope.event_type);
    if (trigger !== null) machine.fire(trigger);
  });
}
