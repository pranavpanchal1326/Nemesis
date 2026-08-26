import { describe, expect, it } from "vitest";

import type { RealtimeEnvelope } from "../src/lib/realtime/envelope.ts";
import { EVENT_SCORE_SCALE, levelFor } from "../src/lib/severity.ts";
import {
  applyEnvelope,
  BLOOM_HOLD_STEPS,
  bloomActive,
  liveEntities,
  seedLive,
} from "../src/clay/live.ts";
import { levelOf, type ClayEntity } from "../src/clay/entities.ts";

/**
 * Law 3, in the one place it can be asserted without a browser.
 *
 * > **A scene that can only be fired by a button fails the gate.**
 *
 * The browser half of that — a pin appearing on a canvas because the backend
 * published an event — is in `tests/clay.spec.ts`. This is the half that says
 * *what an event means*, and it is where the honest gaps in that mapping are
 * pinned down so they cannot quietly close themselves later.
 */

const PUNE = { lat: 18.5204, lng: 73.8567 };

function envelope(
  eventType: RealtimeEnvelope["event_type"],
  entityId: string,
  payload: Record<string, unknown> = {},
): RealtimeEnvelope {
  return {
    event_type: eventType,
    entity_type: "complaint_cluster",
    entity_id: entityId,
    sequence: 1,
    timestamp: "2026-08-25T10:00:00Z",
    cursor: 1,
    payload,
  };
}

function existing(id: string, overrides: Partial<ClayEntity> = {}): ClayEntity {
  return {
    id,
    kind: "cluster",
    label: id,
    point: PUNE,
    severityScore: null,
    state: "resting",
    reports: { kind: "known", value: 2 },
    arrivedAtStep: null,
    href: null,
    ...overrides,
  };
}

describe("§E27 — a pin exists because an event said so", () => {
  it("creates a settling pin from `cluster_created`, at the centroid it carried", () => {
    const state = applyEnvelope(
      seedLive([]),
      envelope("cluster_created", "c1", {
        cluster_centroid: { lat: PUNE.lat, lng: PUNE.lng },
        report_count: 3,
      }),
      12,
    );

    const [pin] = liveEntities(state);
    expect(pin?.id).toBe("c1");
    expect(pin?.state).toBe("settling");
    expect(pin?.arrivedAtStep).toBe(12);
    expect(pin?.reports).toEqual({ kind: "known", value: 3 });
    expect(state.lastAppliedEvent).toBe("cluster_created");
  });

  it("**leaves a new cluster unglazed** — the honest gap, held open", () => {
    // A pin is a cluster; a severity is published against a *complaint*. No
    // shaped payload carries a cluster's severity, so glazing it with whichever
    // complaint scored most recently would put a colour on the map that means
    // nothing — and a colour on this map means a severity band.
    //
    // If a backend shaper ever adds cluster severity, this test is the thing
    // that has to be deliberately changed, which is the point of writing it.
    const state = applyEnvelope(
      seedLive([]),
      envelope("cluster_created", "c1", {
        cluster_centroid: PUNE,
        new_severity: 9,
      }),
      0,
    );
    const [pin] = liveEntities(state);
    expect(pin?.severityScore).toBeNull();
    expect(levelOf(pin ?? existing("x"))).toBeNull();
  });

  it("does not invent a pin for a cluster with no centroid", () => {
    const state = applyEnvelope(seedLive([]), envelope("cluster_created", "c1", {}), 0);
    expect(liveEntities(state)).toHaveLength(0);
  });

  it("converts `severity_scored` from the wire's 0–10 to the product's 0–100", () => {
    // The factor lives in `lib/severity.ts` and nowhere else. A factor of ten
    // applied in two places is a factor of ten applied wrongly in one of them.
    const state = applyEnvelope(
      seedLive([existing("c1")]),
      envelope("severity_scored", "c1", { new_severity: 8.2 }),
      0,
    );
    const [pin] = liveEntities(state);
    expect(pin?.severityScore).toBeCloseTo(8.2 * EVENT_SCORE_SCALE, 6);
    expect(levelOf(pin ?? existing("x"))).toBe(levelFor(82));
  });

  it("moves a matched cluster into `merging`, because dedup is not deletion", () => {
    const state = applyEnvelope(
      seedLive([existing("c1")]),
      envelope("cluster_match_found", "c1", { report_count: 5 }),
      0,
    );
    const [pin] = liveEntities(state);
    expect(pin?.state).toBe("merging");
    expect(pin?.reports).toEqual({ kind: "known", value: 5 });
  });

  it("records a reverted merge as a state change, ready for the act that plays it", () => {
    const merged = applyEnvelope(
      seedLive([existing("c1")]),
      envelope("cluster_match_found", "c1"),
      0,
    );
    const reverted = applyEnvelope(merged, envelope("cluster_merge_reverted", "c1"), 1);
    expect(liveEntities(reverted)[0]?.state).toBe("resting");
  });

  it("renders a flag as its own state, never as a severity", () => {
    // ADR-0039: an unproven anomaly in a severity colour is a §22.2 defamation
    // exposure with a design cause.
    const state = applyEnvelope(
      seedLive([existing("c1", { severityScore: 90 })]),
      envelope("abuse_pattern_flagged", "c1"),
      0,
    );
    const [pin] = liveEntities(state);
    expect(pin?.state).toBe("flagged");
  });

  it("turns a confirmed resolution into the resolved glaze, whatever it was scored", () => {
    const state = applyEnvelope(
      seedLive([existing("c1", { severityScore: 95 })]),
      envelope("citizen_confirmed", "c1"),
      0,
    );
    const [pin] = liveEntities(state);
    expect(pin?.state).toBe("resolved");
    expect(levelOf(pin ?? existing("x"))).toBe("resolved");
  });

  it("ignores an event about something this map is not showing", () => {
    // The stream is tenant-wide and a ward view is a filter on it. Not an
    // error, and not a reason to invent a pin.
    const state = applyEnvelope(seedLive([]), envelope("severity_scored", "ghost", {}), 0);
    expect(liveEntities(state)).toHaveLength(0);
    expect(state.lastAppliedEvent).toBeNull();
  });

  it("treats an unshaped event as a signal to refetch, not as a picture", () => {
    const before = seedLive([existing("c1")]);
    expect(applyEnvelope(before, envelope("complaint_submitted", "c1"), 0)).toBe(before);
  });
});

describe("§E7.3 — the bloom fires for one event and holds for a stated window", () => {
  it("is off until the fail-safe fires", () => {
    const state = seedLive([existing("c1")]);
    expect(state.bloomUntilStep).toBeNull();
    expect(bloomActive(state, 0)).toBe(false);
  });

  it("fires on `safety_trigger_fired` and stops on its own", () => {
    const fired = applyEnvelope(seedLive([]), envelope("safety_trigger_fired", "s1"), 100);
    expect(fired.bloomUntilStep).toBe(100 + BLOOM_HOLD_STEPS);
    expect(bloomActive(fired, 100)).toBe(true);
    expect(bloomActive(fired, 100 + BLOOM_HOLD_STEPS - 1)).toBe(true);
    expect(bloomActive(fired, 100 + BLOOM_HOLD_STEPS)).toBe(false);
  });

  it("is a fact about the system, not about a pin", () => {
    // The report a fail-safe fired on is very often one this map is not
    // showing, so the bloom is scene-level and touches no entity.
    const fired = applyEnvelope(
      seedLive([existing("c1")]),
      envelope("safety_trigger_fired", "somewhere-else"),
      0,
    );
    expect(liveEntities(fired)[0]).toEqual(existing("c1"));
  });

  it("is the only event that can start it", () => {
    // §E3.4 audits this as a usage grep; this is the same claim, from the data
    // side. Every other shaped event must leave the bloom exactly as it was.
    const others = [
      "cluster_created",
      "cluster_match_found",
      "cluster_merge_reverted",
      "severity_scored",
      "citizen_confirmed",
      "abuse_pattern_flagged",
      "complaint_submitted",
    ];
    for (const eventType of others) {
      const state = applyEnvelope(
        seedLive([existing("c1")]),
        envelope(eventType, "c1", { cluster_centroid: PUNE, new_severity: 5 }),
        0,
      );
      expect(state.bloomUntilStep, `${eventType} must not bloom`).toBeNull();
    }
  });
});
