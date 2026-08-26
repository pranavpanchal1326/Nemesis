import { beforeEach, describe, expect, it } from "vitest";

import type { RealtimeEnvelope } from "../src/lib/realtime/envelope.ts";
import { storyLive } from "../src/story/live-story.ts";

/**
 * What the film may believe — execution-plan Law 3, and the Phase 20 gate's
 * unit half.
 *
 * > A scene that can only be fired by a button fails.
 *
 * The browser half of that gate is `tests/story.spec.ts`, which drives a real
 * pipeline. This half asserts the reducer the acts read, against envelopes
 * shaped **exactly** as `nemesis/realtime/envelope.py` shapes them — because
 * the failure this catches is not "the film ignores events", it is "the film
 * reads a field the server does not publish and therefore renders nothing, or
 * worse, renders a default".
 *
 * ADR-0016 makes the wire default-deny: a field that is not in the shaper never
 * arrives. So every number the film prints has to survive being absent, and
 * absent has to mean *say less*, never *make something up*.
 */

/** As `_cluster_match_found` publishes it. Field names copied from the shaper,
 *  deliberately, rather than from the module under test. */
function merged(overrides: Record<string, unknown> = {}): RealtimeEnvelope {
  return {
    cursor: 41,
    entity_id: "cluster-7",
    entity_type: "cluster",
    event_type: "cluster_match_found",
    payload: {
      new_confidence: 0.87,
      report_count: 3,
      geo_distance_meters: 11.4,
      ...overrides,
    },
    sequence: 41,
    timestamp: "2026-08-26T06:12:44.180000Z",
  };
}

function scored(entityId: string, score: number): RealtimeEnvelope {
  return {
    cursor: 42,
    entity_id: entityId,
    entity_type: "complaint",
    event_type: "severity_scored",
    payload: { new_severity: score },
    sequence: 42,
    timestamp: "2026-08-26T06:12:45.000000Z",
  };
}

describe("§E16 — what the film has been told", () => {
  beforeEach(() => {
    storyLive.getState().reset();
  });

  it("starts believing nothing", () => {
    const state = storyLive.getState();
    expect(state.merge).toBeNull();
    expect(state.severity).toBeNull();
    expect(state.complaintId).toBeNull();
    expect(state.seen).toEqual([]);
  });

  it("reads the merge off the shaper's own field names", () => {
    storyLive.getState().apply(merged());
    const merge = storyLive.getState().merge;
    expect(merge?.clusterId).toBe("cluster-7");
    expect(merge?.reports).toBe(3);
    // `new_confidence`, not `confidence`: the raw event's `combined_confidence`
    // never leaves the server, and reading the wrong name here would print no
    // match figure on the most-photographed frame in the product.
    expect(merge?.confidence).toBeCloseTo(0.87, 6);
    expect(merge?.distanceMetres).toBeCloseTo(11.4, 6);
    // The event's own timestamp — §E16's *"mono timestamp ticking"* — and never
    // the browser's clock.
    expect(merge?.at).toBe("2026-08-26T06:12:44.180000Z");
  });

  it("says less rather than guessing when a field does not arrive", () => {
    // ADR-0016 is default-deny and a shaper can legitimately stop publishing a
    // field. `null` makes Act 6 print the stamp without that figure; a default
    // would make it print a number about nothing.
    storyLive.getState().apply(merged({ report_count: null, new_confidence: undefined }));
    const merge = storyLive.getState().merge;
    expect(merge).not.toBeNull();
    expect(merge?.reports).toBeNull();
    expect(merge?.confidence).toBeNull();
  });

  it("keeps a severity with the entity it was scored against", () => {
    // A merge happens to a cluster; a severity is scored against a complaint.
    // `clay/live.ts` documents that no shaped payload joins the two, so the
    // film keeps them apart and Act 6 prints the severity only when it is about
    // the report being followed.
    storyLive.getState().apply(scored("complaint-9", 7.8));
    expect(storyLive.getState().severity).toEqual({ entityId: "complaint-9", score: 7.8 });
    expect(storyLive.getState().merge).toBeNull();
  });

  it("records every event type it has seen, for the gate to read", () => {
    storyLive.getState().apply(scored("complaint-9", 7.8));
    storyLive.getState().apply(merged());
    expect(storyLive.getState().seen).toEqual(["severity_scored", "cluster_match_found"]);
  });

  it("ignores an event it has no scene for, without forgetting it arrived", () => {
    const heartbeatish: RealtimeEnvelope = {
      cursor: 1,
      entity_id: "complaint-1",
      entity_type: "complaint",
      event_type: "complaint_submitted",
      payload: {},
      sequence: 1,
      timestamp: "2026-08-26T06:00:00.000000Z",
    };
    storyLive.getState().apply(heartbeatish);
    expect(storyLive.getState().merge).toBeNull();
    expect(storyLive.getState().seen).toEqual(["complaint_submitted"]);
  });

  it("follows the complaint the reader's own report produced", () => {
    // The one fact the film supplies itself, and it is not a trigger: it names
    // which real thing to read. Every stamp still comes off that complaint's
    // own ledger through `citizen/gates.ts`.
    storyLive.getState().follow("complaint-9");
    expect(storyLive.getState().complaintId).toBe("complaint-9");
  });
});
