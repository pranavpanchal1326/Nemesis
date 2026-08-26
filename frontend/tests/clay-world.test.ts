import { describe, expect, it } from "vitest";

import { BUDGET, CLAY, WORLD } from "../src/design/generated/tokens.ts";
import { DEFAULT_MAX_FOOTPRINTS, generateCity, seedForOrigin } from "../src/clay/city-kit.ts";
import {
  clampLatitude,
  createProjection,
  fromMercator,
  originOf,
  toMercator,
} from "../src/clay/projection.ts";
import {
  allocatePins,
  pinHeight,
  pinInstances,
  settleProgress,
  writePins,
} from "../src/clay/pins.ts";
import { entityDigest, order, type ClayEntity } from "../src/clay/entities.ts";
import { cityBuffers } from "../src/clay/geometry.ts";

/**
 * The world the clay is built on — F8's projection, F9's city, F10's pins.
 *
 * Everything asserted here is pure: `(inputs) → numbers`. That is the property
 * the whole layer is designed around, and it is why the Phase 19 gate can be
 * argued about at all — a scene whose geometry could only be inspected by
 * looking at it is a scene with no gate, only an opinion.
 */

const PUNE = { lat: 18.5204, lng: 73.8567 };

function entity(id: string, overrides: Partial<ClayEntity> = {}): ClayEntity {
  return {
    id,
    kind: "cluster",
    label: id,
    point: PUNE,
    severityScore: null,
    state: "resting",
    reports: { kind: "known", value: 1 },
    arrivedAtStep: null,
    href: null,
    ...overrides,
  };
}

describe("M8.2 — Web Mercator to local ENU metres, so precision holds at city scale", () => {
  it("round-trips a coordinate through Mercator without drift", () => {
    const back = fromMercator(toMercator(PUNE));
    expect(back.lat).toBeCloseTo(PUNE.lat, 9);
    expect(back.lng).toBeCloseTo(PUNE.lng, 9);
  });

  it("puts the origin at zero and keeps a round trip exact through local metres", () => {
    const projection = createProjection(PUNE);
    const here = projection.toLocal(PUNE);
    expect(here.east).toBeCloseTo(0, 6);
    expect(here.north).toBeCloseTo(0, 6);

    const there = { lat: PUNE.lat + 0.01, lng: PUNE.lng + 0.02 };
    const round = projection.toGeo(projection.toLocal(there));
    expect(round.lat).toBeCloseTo(there.lat, 7);
    expect(round.lng).toBeCloseTo(there.lng, 7);
  });

  it("measures a degree of latitude at roughly its real length", () => {
    // ~111 km per degree of latitude. The assertion is loose on purpose: it is
    // testing that the scale correction at the origin's latitude was applied at
    // all, not re-deriving the geoid.
    const projection = createProjection(PUNE);
    const north = projection.toLocal({ lat: PUNE.lat + 1, lng: PUNE.lng });
    expect(north.north).toBeGreaterThan(110_000);
    expect(north.north).toBeLessThan(112_000);
  });

  it("clamps latitude to Mercator's own limit rather than producing infinity", () => {
    expect(clampLatitude(90)).toBeLessThan(86);
    expect(Number.isFinite(toMercator({ lat: 90, lng: 0 }).y)).toBe(true);
  });

  it("has no origin for a city that has published no coordinate", () => {
    // The state the scene renders as "there is nothing to draw" — a real
    // answer, and the one an invented origin would hide.
    expect(originOf([])).toBeNull();
  });
});

describe("ADR-0047 — the city is generated from the tenant's own origin", () => {
  it("is deterministic: the same origin builds the same city", () => {
    const a = generateCity(PUNE);
    const b = generateCity(PUNE);
    expect(a.seed).toBe(b.seed);
    expect(a.footprints).toEqual(b.footprints);
  });

  it("does not re-roll when a centroid moves by a metre", () => {
    // ~11 m of rounding, so a re-published ward that shifts the centroid
    // slightly does not rebuild the whole city under the officer's cursor.
    expect(seedForOrigin(PUNE)).toBe(seedForOrigin({ lat: PUNE.lat + 0.00002, lng: PUNE.lng }));
  });

  it("gives two different cities two different clays", () => {
    expect(seedForOrigin(PUNE)).not.toBe(seedForOrigin({ lat: 21.1458, lng: 79.0882 }));
  });

  it("respects the footprint cap, which is where the VRAM budget is enforced", () => {
    const city = generateCity(PUNE, { radiusMetres: 12_000 });
    expect(city.footprints.length).toBeLessThanOrEqual(DEFAULT_MAX_FOOTPRINTS);
  });

  it("stays within its own radius, and thins toward the edge", () => {
    const city = generateCity(PUNE, { radiusMetres: 900 });
    for (const footprint of city.footprints) {
      // Half a block of slack: a plot's centre is inside the radius, and the
      // plot itself may straddle it.
      expect(Math.hypot(footprint.east, footprint.north)).toBeLessThan(900 + WORLD.kit.blockMetres);
    }
    const inner = city.footprints.filter((f) => Math.hypot(f.east, f.north) < 300);
    const outer = city.footprints.filter((f) => Math.hypot(f.east, f.north) > 700);
    const mean = (list: typeof city.footprints) =>
      list.reduce((sum, f) => sum + f.height, 0) / Math.max(1, list.length);
    expect(mean(inner)).toBeGreaterThan(mean(outer));
  });

  it("builds one instance buffer per footprint, all of it unglazed", () => {
    // §E5: data is never decorative, and it cuts both ways — a building is
    // scenery and must never carry a severity.
    const buffers = cityBuffers(generateCity(PUNE, { radiusMetres: 400 }));
    expect(buffers.count).toBeGreaterThan(0);
    expect(buffers.matrix).toHaveLength(buffers.count * 16);
    for (const value of buffers.severity) expect(value).toBe(-1);
    for (const value of buffers.grain) {
      expect(value).toBeGreaterThanOrEqual(0);
      expect(value).toBeLessThan(1);
    }
  });

  it("sits every building on the ground rather than through it", () => {
    const kit = generateCity(PUNE, { radiusMetres: 300 });
    const buffers = cityBuffers(kit);
    kit.footprints.forEach((footprint, i) => {
      // The box is centred on its own origin, so half its height is where its
      // base meets the ground plane.
      expect(buffers.matrix[i * 16 + 13]).toBeCloseTo(footprint.height / 2, 5);
    });
  });
});

describe("M8.4 — pins carry severity as height, and the Settle spends its overshoot downward", () => {
  it("maps a severity score onto the published height range", () => {
    expect(pinHeight(null)).toBe(WORLD.pin.minHeightMetres);
    expect(pinHeight(0)).toBeCloseTo(WORLD.pin.minHeightMetres, 6);
    expect(pinHeight(100)).toBeCloseTo(WORLD.pin.maxHeightMetres, 6);
    expect(pinHeight(50)).toBeGreaterThan(pinHeight(20));
  });

  it("does not replay the arrival of a pin that was already on the map", () => {
    // Otherwise every navigation is a fireworks display that tells the officer
    // five thousand reports just came in.
    expect(settleProgress(null, 0)).toBe(1);
    expect(settleProgress(null, 9_999)).toBe(1);
  });

  it("settles over exactly the published number of steps", () => {
    expect(settleProgress(10, 10)).toBe(0);
    expect(settleProgress(10, 10 + WORLD.pin.settleSteps)).toBe(1);
    expect(settleProgress(10, 10 + WORLD.pin.settleSteps + 40)).toBe(1);
  });

  it("never lets an overshoot make a pin taller than its severity", () => {
    // The Stamp curve passes 1. If that overshoot were spent on height, an
    // animation would be lying about a measurement — so it is spent on depth.
    const arriving = entity("a", { severityScore: 90, arrivedAtStep: 0 });
    const projection = createProjection(PUNE);
    const full = pinHeight(90);
    for (let step = 0; step <= WORLD.pin.settleSteps + 2; step += 1) {
      const [pin] = pinInstances([arriving], projection, step);
      expect(pin).toBeDefined();
      if (pin === undefined) continue;
      expect(pin.height).toBeLessThanOrEqual(full + 1e-9);
      expect(pin.depth).toBeGreaterThanOrEqual(0);
    }
  });

  it("is reproducible at a fixed step, which is what a golden image needs", () => {
    const projection = createProjection(PUNE);
    const world = [entity("a", { severityScore: 42, arrivedAtStep: 3 })];
    expect(pinInstances(world, projection, 5)).toEqual(pinInstances(world, projection, 5));
  });

  it("writes at most the budgeted number of instances and says how many", () => {
    const projection = createProjection(PUNE);
    const many = Array.from({ length: BUDGET.pins + 25 }, (_, i) => entity(`e${String(i)}`));
    const buffers = allocatePins(BUDGET.pins);
    const written = writePins(buffers, pinInstances(many, projection, 0));
    expect(written).toBe(BUDGET.pins);
    expect(buffers.count).toBe(BUDGET.pins);
  });

  it("puts north at −z, so the map faces the way everybody means", () => {
    const projection = createProjection(PUNE);
    const northward = entity("n", { point: { lat: PUNE.lat + 0.01, lng: PUNE.lng } });
    const buffers = allocatePins(4);
    writePins(buffers, pinInstances([northward], projection, 0));
    expect(buffers.matrix[14]).toBeLessThan(0);
  });
});

describe("§E22 — one order, and a digest that can catch a divergence", () => {
  it("sorts worst first, then by reports, then by id — a total order", () => {
    const ordered = order([
      entity("b", { severityScore: 10 }),
      entity("a", { severityScore: 90 }),
      entity("c", { severityScore: 90 }),
      entity("d"),
    ]);
    expect(ordered.map((e) => e.id)).toEqual(["a", "c", "b", "d"]);
  });

  it("changes the digest when a pin changes state, moves, or is scored", () => {
    const base = [entity("a", { severityScore: 10 })];
    expect(entityDigest(base)).toBe(entityDigest([entity("a", { severityScore: 10 })]));
    expect(entityDigest(base)).not.toBe(entityDigest([entity("a", { severityScore: 11 })]));
    expect(entityDigest(base)).not.toBe(
      entityDigest([entity("a", { severityScore: 10, state: "flagged" })]),
    );
    expect(entityDigest(base)).not.toBe(entityDigest([]));
  });
});

describe("§E7.1 — the thumbprint is a property of the entity, not of its index", () => {
  it("gives one id the same grain however the list re-sorted", async () => {
    const { thumbprintRotation } = await import("../src/clay/thumbprint.ts");
    expect(thumbprintRotation("zone:14")).toBe(thumbprintRotation("zone:14"));
    expect(thumbprintRotation("zone:14")).not.toBe(thumbprintRotation("zone:15"));
  });

  it("lands on one of the published rotation steps and never outside a turn", async () => {
    const { thumbprintRotation } = await import("../src/clay/thumbprint.ts");
    for (let i = 0; i < 200; i += 1) {
      const rotation = thumbprintRotation(`zone:${String(i)}`);
      expect(rotation).toBeGreaterThanOrEqual(0);
      expect(rotation).toBeLessThan(Math.PI * 2);
      const step = (rotation / (Math.PI * 2)) * CLAY.thumbprint.rotationSteps;
      expect(Math.abs(step - Math.round(step))).toBeLessThan(1e-9);
    }
  });

  it("wraps without a seam — the join is indistinguishable from any other column", async () => {
    // The honest form of "no seam". The two edge columns are *neighbours* on a
    // tiling surface, not copies, so equality would be the wrong assertion —
    // what must be true is that the step across the join is no larger than the
    // step between any other adjacent pair. A clamped edge, which is the bug
    // this is written against, shows up here as a multiple.
    const { thumbprintTile } = await import("../src/clay/thumbprint.ts");
    const size = 128;
    const tile = thumbprintTile(size);
    const at = (x: number, y: number) => tile[(y * size + x) * 4] ?? 0;

    const meanStep = (a: number, b: number) => {
      let total = 0;
      for (let y = 0; y < size; y += 1) total += Math.abs(at(a, y) - at(b, y));
      return total / size;
    };

    const across = meanStep(size - 1, 0);
    let interior = 0;
    for (let x = 0; x < size - 1; x += 1) interior += meanStep(x, x + 1);
    interior /= size - 1;

    expect(across).toBeLessThan(interior * 2);
  });
});
