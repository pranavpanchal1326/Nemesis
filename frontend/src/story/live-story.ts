"use client";

import { createStore } from "zustand/vanilla";

import type { RealtimeEnvelope } from "@/lib/realtime/envelope";
import { subscribeToEvents } from "@/lib/realtime/store";

/**
 * What the film has actually been told — §E16, execution-plan Law 3, and the
 * Phase 20 gate.
 *
 * > **Every scene is triggered by a genuine backend event in an E2E test. A
 * > scene that can only be fired by a button fails.**
 *
 * This module is where that gate is won or lost, so it is worth being exact
 * about what it does and does not do.
 *
 * **It holds events, not scenes.** Nothing here starts an animation, sets a
 * progress value or names an act. It records the last envelope of each kind the
 * film cares about, exactly as it arrived, and the acts read it. An act with no
 * event to read renders its *waiting* state and says so in words — which is the
 * honest state and, on a quiet deployment, the common one. A film that filled
 * that silence with a scripted merge would be a film with a button in it,
 * wearing a WebSocket for a costume.
 *
 * **The one thing the film supplies itself is the complaint it is following.**
 * Act 4 is the real `<ReportCapture>`; when a reader actually submits, the 202
 * comes back with a complaint id and Acts 5 and 6 follow *that* report through
 * its own ledger. That is not a trigger — the id names which real thing to
 * read, and every stamp still comes from `GET /complaints/{id}/events` through
 * `citizen/gates.ts`, the same reader M5's theatre uses.
 *
 * **Why a store and not the realtime store.** `realtime/store.ts` is the
 * transport: connection state, cursor, degradation. Adding film state to it
 * would put a marketing surface's memory inside the module the console and the
 * citizen loop depend on. This subscribes to the same bus through the same
 * `subscribeToEvents` seam and keeps its own, smaller memory.
 */

/** §E16 Act 6's numbers, as the merge event actually carries them. */
export interface LiveMerge {
  /** The cluster the merge happened on. */
  readonly clusterId: string;
  /** How many reports the survivor now stands for. `null` where the shaped
   *  payload did not carry it — stated rather than defaulted to 1, because
   *  "1 INCIDENT · 1 REPORT" is a sentence that would be a lie about a merge. */
  readonly reports: number | null;
  /** The match confidence, `[0,1]`, or `null`. §E16.1's `MATCH 0.87`. Read
   *  from `new_confidence`, which is what the shaper publishes — the raw
   *  event's `combined_confidence` never leaves the server. */
  readonly confidence: number | null;
  /** How far apart the merged reports were, in metres, or `null`. */
  readonly distanceMetres: number | null;
  /** When the event was published — §E16's *"a mono timestamp ticking"*, which
   *  is the event's own timestamp and never the browser's clock. */
  readonly at: string;
}

/** A severity the stream published, and the entity it was published *about*. */
export interface LiveSeverity {
  readonly entityId: string;
  /** The 0–10 score `severity_scored` carries. Act 6 renders §E16's 0–1 figure
   *  from it through `lib/severity.ts`, which is where that scale conversion
   *  already lives. */
  readonly score: number;
}

export interface StoryLive {
  /** The complaint Acts 5 and 6 are following, if the reader made one. */
  readonly complaintId: string | null;
  /** The last real merge the film was told about. */
  readonly merge: LiveMerge | null;
  /**
   * The last severity the stream published.
   *
   * Kept beside the merge rather than folded into it, because they are facts
   * about **different entities**: a merge happens to a cluster and a severity
   * is scored against a complaint, and `clay/live.ts` documents at length that
   * no shaped payload joins the two. Act 6's stamp therefore prints a severity
   * only when this one is about the complaint the film is following, and prints
   * the merge without it otherwise — the same distinction `gate.dedup.merged`
   * and `gate.dedup.mergedNoScore` already draw one surface down.
   */
  readonly severity: LiveSeverity | null;
  /** Every event type the film has seen, in arrival order, capped. The seam
   *  `tests/story.spec.ts` reads to assert a scene was fired by an event and
   *  not by a click. */
  readonly seen: readonly string[];
  readonly follow: (complaintId: string) => void;
  readonly apply: (envelope: RealtimeEnvelope) => void;
  readonly reset: () => void;
}

/**
 * How many event types to remember.
 *
 * Long enough that a reviewer can see the pipeline's whole run for one report;
 * short enough that a tab left open on the landing page overnight does not grow
 * an array all night. The film needs the *last* of each kind, so this list is
 * evidence rather than state.
 */
const SEEN_LIMIT = 64;

function number(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export const storyLive = createStore<StoryLive>()((set) => ({
  complaintId: null,
  merge: null,
  severity: null,
  seen: [],

  follow: (complaintId) => {
    set({ complaintId });
  },

  apply: (envelope) => {
    set((state) => {
      const seen = [...state.seen, envelope.event_type].slice(-SEEN_LIMIT);

      if (envelope.event_type === "severity_scored") {
        const score = number(envelope.payload["new_severity"]);
        if (score === null) return { ...state, seen };
        return { ...state, seen, severity: { entityId: envelope.entity_id, score } };
      }

      if (envelope.event_type !== "cluster_match_found") return { ...state, seen };
      return {
        ...state,
        seen,
        merge: {
          clusterId: envelope.entity_id,
          // The shaped payload's own field names (`realtime/envelope.py`).
          // Read defensively and reported as `null` when absent: ADR-0016 is
          // default-deny, so a field can legitimately stop arriving, and a
          // stamp that invented a count would be exactly the confident wrong
          // screen §E3.3 rules out.
          reports: number(envelope.payload["report_count"]),
          confidence: number(envelope.payload["new_confidence"]),
          distanceMetres: number(envelope.payload["geo_distance_meters"]),
          at: envelope.timestamp,
        },
      };
    });
  },

  reset: () => {
    set({ complaintId: null, merge: null, severity: null, seen: [] });
  },
}));

/**
 * Attach the film to the bus.
 *
 * Returns the unsubscribe, and is called once by `<Walk>`. Separate from the
 * store so a unit test can drive `apply()` with a fixture envelope without a
 * socket, and so the store itself stays a pure reducer over events.
 */
export function followTheBus(): () => void {
  return subscribeToEvents((envelope) => {
    storyLive.getState().apply(envelope);
  });
}
