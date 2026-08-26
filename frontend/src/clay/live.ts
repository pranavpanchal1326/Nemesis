/**
 * Pins driven by real events — F10, §E27, execution-plan Law 3.
 *
 * > **Law 3 — no scene before the event that drives it.** A merge that cannot
 * > be fired by a real `cluster_match_found` fails its gate.
 *
 * This module is that law, as a reducer. `applyEnvelope()` takes the state of
 * the clay and one envelope off the M3 bus and returns the next state. Nothing
 * here can be triggered by a button, because nothing here has a parameter a
 * button could supply.
 *
 * ---
 *
 * **What the stream can actually drive today, and what it cannot.**
 *
 * `realtime/envelope.py` is default-deny: an event type with no declared public
 * shape publishes an *empty* payload, and that is a deliberate privacy property
 * rather than a gap to work around (ADR-0016). Ten types carry a payload. Read
 * against §E27's traceability table, that gives the map exactly this:
 *
 * | Event | What arrives | What the clay does |
 * |---|---|---|
 * | `cluster_created` | a centroid coarsened to ~110 m | a pin, with the Settle motion |
 * | `cluster_match_found` | confidence, report count, distance | **the merge** |
 * | `severity_scored` | a 0–10 score, on the *complaint's* id | glazes that entity, if it is one we hold |
 * | `safety_trigger_fired` | the detection source | the bloom, and only ever this |
 * | `citizen_confirmed` | whether it was auto-confirmed | the closure |
 * | `abuse_pattern_flagged` | nothing | the fluorescent hatch (ADR-0039) |
 *
 * **And here is the honest gap, stated rather than papered over.** A pin on
 * this map is a *cluster*; a severity score is published against a *complaint*.
 * Those are different entity ids, and no shaped payload carries a cluster's
 * severity. So a cluster pin created by the stream stays **unglazed** until the
 * read path confirms it — which is `reconcile.ts` doing exactly what §E14.3
 * says it is for: *"the socket is a hint, so everything it touches is
 * provisional until refetched"*.
 *
 * The alternative — glazing a cluster with the severity of whichever complaint
 * scored most recently — would put a colour on the map that means nothing, and
 * a colour on this map means a severity band. §E3.3: a confidently wrong screen
 * is worse than an honest empty one. So the pin arrives as bare clay, which is
 * a true statement about what is known, and this paragraph is in the source
 * next to the branch that would otherwise be tempting to write.
 *
 * The backend-side fix is one shaper — a cluster severity on
 * `cluster_created` / `cluster_match_found` — and it is recorded as such rather
 * than worked around here.
 */

import { LENS } from "@/design/generated/tokens";
import type { RealtimeEnvelope } from "@/lib/realtime/envelope";
import { EVENT_SCORE_SCALE } from "@/lib/severity";
import { order, type ClayEntity, type PinState } from "./entities";
import type { GeoPoint } from "./projection";

export interface LiveClay {
  /** Keyed by entity id. Seeded from the read path, then amended by events. */
  readonly entities: ReadonlyMap<string, ClayEntity>;
  /**
   * The step the bloom stops at, or `null`.
   *
   * §E7.3: selective bloom is *"reserved exclusively for
   * `safety_trigger_fired`"*, and §E3.4 audits that as a usage grep. It is a
   * scene-level fact rather than a per-pin one because the fail-safe firing is
   * a fact about the system, and because the report it fired on is very often
   * one this map is not showing.
   */
  readonly bloomUntilStep: number | null;
  /** The last envelope the clay actually acted on, for the E2E gate to read. */
  readonly lastAppliedEvent: string | null;
}

export function seedLive(entities: readonly ClayEntity[]): LiveClay {
  return {
    entities: new Map(entities.map((entity) => [entity.id, entity])),
    bloomUntilStep: null,
    lastAppliedEvent: null,
  };
}

/** Ordered for both renderers. The peer list and the instance buffer read this
 *  one array, which is the whole of §E22's "synchronised". */
export function liveEntities(state: LiveClay): readonly ClayEntity[] {
  return order([...state.entities.values()]);
}

export function applyEnvelope(state: LiveClay, envelope: RealtimeEnvelope, step: number): LiveClay {
  switch (envelope.event_type) {
    case "cluster_created":
      return withEntity(state, envelope, createCluster(state, envelope, step));

    case "cluster_match_found":
      return withEntity(
        state,
        envelope,
        restate(state, envelope.entity_id, "merging", reportCount(envelope)),
      );

    case "cluster_merge_reverted":
      // §E27: "the merge running backward; rings re-separate". At M8 the clay
      // owns only the state change; M9's Act 6 owns the animation that plays
      // it in reverse. Recording it now means that act has something real to
      // hang on when it lands, rather than a button.
      return withEntity(state, envelope, restate(state, envelope.entity_id, "resting", null));

    case "severity_scored":
      return withEntity(state, envelope, glaze(state, envelope));

    case "citizen_confirmed":
      return withEntity(state, envelope, restate(state, envelope.entity_id, "resolved", null));

    case "abuse_pattern_flagged":
      return withEntity(state, envelope, restate(state, envelope.entity_id, "flagged", null));

    case "safety_trigger_fired":
      return {
        ...state,
        bloomUntilStep: step + BLOOM_HOLD_STEPS,
        lastAppliedEvent: envelope.event_type,
      };

    default:
      // Every other type is a signal to refetch, not a picture. `reconcile.ts`
      // already owns that, and duplicating it here would give the map a second,
      // slightly different idea of what is true.
      return state;
  }
}

/** Two seconds on the 12 fps clock, from `lens.bloom.holdSteps` — long
 *  enough to be seen, short enough that the deterministic fail-safe does not
 *  become ambient lighting. */
export const BLOOM_HOLD_STEPS = LENS.bloom.holdSteps;

export function bloomActive(state: LiveClay, step: number): boolean {
  return state.bloomUntilStep !== null && step < state.bloomUntilStep;
}

// --------------------------------------------------------------------------

function withEntity(
  state: LiveClay,
  envelope: RealtimeEnvelope,
  entity: ClayEntity | null,
): LiveClay {
  if (entity === null) return state;
  const entities = new Map(state.entities);
  entities.set(entity.id, entity);
  return { ...state, entities, lastAppliedEvent: envelope.event_type };
}

function createCluster(
  state: LiveClay,
  envelope: RealtimeEnvelope,
  step: number,
): ClayEntity | null {
  const point = centroid(envelope);
  if (point === null) return null;

  const existing = state.entities.get(envelope.entity_id);
  return {
    id: envelope.entity_id,
    kind: "cluster",
    // The stream publishes no name for a cluster, and inventing one — "Cluster
    // 4f2a" — would put a system identifier where a place name goes. The peer
    // list renders the coarse coordinate instead, which is what is actually
    // known about it.
    label: `${point.lat.toFixed(3)}, ${point.lng.toFixed(3)}`,
    point,
    // See the module docstring: no shaped payload carries a cluster's severity,
    // so a pin the stream created is bare clay until the read path says
    // otherwise.
    severityScore: existing?.severityScore ?? null,
    state: "settling",
    reports: { kind: "known", value: reportCount(envelope) ?? 1 },
    arrivedAtStep: step,
    href: null,
  };
}

function restate(
  state: LiveClay,
  id: string,
  next: PinState,
  reports: number | null,
): ClayEntity | null {
  const existing = state.entities.get(id);
  // An event about something this map is not showing is not an error and is
  // not a reason to invent a pin: the stream is tenant-wide and a ward view is
  // a filter on it.
  if (existing === undefined) return null;
  return {
    ...existing,
    state: next,
    reports: reports === null ? existing.reports : { kind: "known", value: reports },
  };
}

function glaze(state: LiveClay, envelope: RealtimeEnvelope): ClayEntity | null {
  const score = number(envelope.payload["new_severity"]);
  const existing = state.entities.get(envelope.entity_id);
  if (score === null || existing === undefined) return null;
  // `new_severity` is 0–10 on the wire (`events/catalog.py`) and everything
  // downstream of here is 0–100. The factor lives in `lib/severity.ts`, which
  // is the only place in the frontend allowed to know it.
  return { ...existing, severityScore: score * EVENT_SCORE_SCALE };
}

function centroid(envelope: RealtimeEnvelope): GeoPoint | null {
  const raw = envelope.payload["cluster_centroid"];
  if (typeof raw !== "object" || raw === null) return null;
  const record = raw as Record<string, unknown>;
  const lat = number(record["lat"]);
  const lng = number(record["lng"]);
  return lat === null || lng === null ? null : { lat, lng };
}

function reportCount(envelope: RealtimeEnvelope): number | null {
  return number(envelope.payload["report_count"]);
}

function number(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
