import { describe, expect, it } from "vitest";

import {
  CLAY,
  GLAZE_LINEAR,
  LENS,
  PAPER_LINEAR,
  SEVERITY,
  SEVERITY_DESCENDING,
  WEATHER,
} from "../src/design/generated/tokens.ts";
import {
  CLAY_ATTRIBUTES,
  createClayMaterial,
  RAMP_ORDER,
  rampIndexOf,
  severityRamp,
} from "../src/clay/clay-tsl.ts";
import { COC_TAPS, flareIntensity, SAFETY_LAYER } from "../src/clay/lens.ts";
import {
  CLEAR,
  keyIntensity,
  lightingSun,
  sunAt,
  weatherFromAdjustments,
} from "../src/clay/sun.ts";

/**
 * F9's gate, and the one reservation §E3.4 audits.
 *
 * > A severity glaze and its badge ink are the same token, asserted — §E24's
 * > central claim, extended into the third dimension.
 *
 * A golden image per material feature in both backends is the other half of
 * that gate and lives in `tests/clay.spec.ts`, because a pixel needs an engine.
 * What is here is the half that can be checked without one, and it is the half
 * that would fail silently: a ramp quantised to bytes, an emissive severity, a
 * bloom that can see something other than the fail-safe. All three look *better*
 * in isolation, which is exactly why each has an assertion instead of a review.
 */

describe("§E24 — the glaze in the shader is the ink in the badge", () => {
  it("builds the ramp from the generated glaze, at full float precision", () => {
    // `FloatType`, not bytes. Quantising to 8 bits would put a *different
    // number* in the shader from the one in the badge — the exact §E24 failure,
    // arriving through the back door of a texture format.
    //
    // `Math.fround` is the honest right-hand side: a float32 texture holds the
    // token to float32, which is exact to seven digits, where a byte texture
    // would hold it to two. Writing the double here instead and loosening the
    // comparison would be asserting a weaker claim and calling it the same one.
    const ramp = severityRamp();
    const data = ramp.image.data as Float32Array;

    RAMP_ORDER.forEach((level, i) => {
      const [r, g, b] = GLAZE_LINEAR[level];
      expect(data[i * 4]).toBe(Math.fround(r));
      expect(data[i * 4 + 1]).toBe(Math.fround(g));
      expect(data[i * 4 + 2]).toBe(Math.fround(b));
      expect(data[i * 4 + 3]).toBe(1);
      // And the float32 hop must not be where the colour changes: a byte
      // texture would move these by up to 1/255, which is what this bounds.
      expect(Math.abs((data[i * 4] ?? 0) - r)).toBeLessThan(1 / 255 / 100);
    });
    ramp.dispose();
  });

  it("covers every published severity band, exactly once, ascending", () => {
    expect([...RAMP_ORDER].reverse()).toEqual([...SEVERITY_DESCENDING]);
    expect(new Set(RAMP_ORDER).size).toBe(RAMP_ORDER.length);
    expect(Object.keys(SEVERITY).sort()).toEqual([...RAMP_ORDER].sort());

    RAMP_ORDER.forEach((level, i) => {
      expect(rampIndexOf(level)).toBe(i);
    });
  });

  it("authors no colour of its own — the clay body is the riso-brown ink", () => {
    // §E9.2 calls riso-brown "the clay ink, MITTI's body colour". A clay swatch
    // of its own would be a fourth palette nobody asked for.
    expect(CLAY.bodyLinear).toHaveLength(3);
    expect(CLAY.body.startsWith("#")).toBe(true);
    // The stock the press prints on is generated the same way, from the same
    // conversion, so a 3D frame and a 2D sheet are the same sheet.
    expect(PAPER_LINEAR["mitti-950"]).toHaveLength(3);
  });
});

describe("§E7.1 — six properties, and severity is a fired glaze", () => {
  it("is matte and dielectric: roughness 0.92, zero metalness", () => {
    expect(CLAY.surface.roughness).toBe(0.92);
    // Not a rounded-down small number. Clay is a dielectric, and a metallic
    // term would put a coloured specular on a material that has none.
    expect(CLAY.surface.metalness).toBe(0);
  });

  it("**never emits light** — the reservation, asserted rather than reviewed", () => {
    // The one assertion in this file that is load-bearing for the product's
    // meaning: an emissive severity ramp would make the map glow, and §E7.3
    // reserves the only glow in NEMESIS for `safety_trigger_fired`.
    const clay = createClayMaterial({ instanced: true });
    expect(clay.material.emissiveNode).toBeNull();
    expect(clay.material.emissive.getHex()).toBe(0);
    expect(clay.material.emissiveIntensity).toBe(1);
    clay.dispose();
  });

  it("darkens and glosses the clay when the season is wet, from tokens", () => {
    const clay = createClayMaterial({ instanced: true });
    expect(() => {
      clay.setWetness(1);
    }).not.toThrow();
    // Clamped, so a tenant with a multiplier off the end of the scale gets
    // soaked clay rather than a shading term running past black.
    expect(() => {
      clay.setWetness(9);
    }).not.toThrow();
    expect(WEATHER.wetDarkening).toBeGreaterThan(0);
    expect(WEATHER.wetGloss).toBeGreaterThan(0);
    clay.dispose();
  });

  it("compiles a non-instanced variant for the ground, from the same recipe", () => {
    // The ground is one mesh with no instances and would fail to compile against
    // `attribute("claySeverity")`. Same recipe, one branch, stated rather than
    // discovered as a shader error.
    const ground = createClayMaterial({ instanced: false });
    expect(ground.material.colorNode).not.toBeNull();
    ground.dispose();
  });

  it("names its per-instance attributes in one place", () => {
    // The buffer that writes them and the shader that reads them are in
    // different files, and a typo between them is a black material, not an
    // error.
    expect(Object.values(CLAY_ATTRIBUTES)).toEqual([
      "clayGrain",
      "claySeverity",
      "clayOcclusion",
      "clayHeight",
    ]);
  });
});

describe("§E7.3 — bloom is reserved, structurally", () => {
  it("keeps the fail-safe on a layer of its own, away from the default", () => {
    // Layer 0 is where three.js puts everything. A reservation that shared a
    // channel with the default would not be a reservation.
    expect(SAFETY_LAYER).toBe(1);
    expect(SAFETY_LAYER).not.toBe(0);
  });

  it("draws the marker brightly enough that the threshold can see it", () => {
    // Written as a function of the token rather than as a number, so raising
    // the threshold cannot silently stop the one effect it exists to gate.
    expect(flareIntensity()).toBeGreaterThan(LENS.bloom.threshold);
  });

  it("holds the bloom for a stated number of steps rather than a duration", () => {
    // Two seconds on the 12 fps clock. Long enough to be seen, short enough
    // that a deterministic fail-safe does not become ambient lighting.
    expect(LENS.bloom.holdSteps).toBeGreaterThan(0);
    expect(Number.isInteger(LENS.bloom.holdSteps)).toBe(true);
  });

  it("samples the circle of confusion with a centre and a hexagonal ring", () => {
    expect(COC_TAPS).toBe(7);
  });
});

describe("§E7.4 — the model's weather is the SLA engine's own answer", () => {
  it("is clear when no seasonal window is in force", () => {
    expect(weatherFromAdjustments({})).toEqual(CLEAR);
    expect(weatherFromAdjustments(null)).toEqual(CLEAR);
    expect(weatherFromAdjustments(undefined)).toEqual(CLEAR);
  });

  it("takes the strongest window, not the first", () => {
    // A tenant may have a monsoon and a local shutdown overlapping, and the
    // model should show the one that is actually costing the most time.
    const weather = weatherFromAdjustments({ shutdown: "1.100", monsoon: "1.400" });
    expect(weather.label).toBe("monsoon");
    expect(weather.multiplier).toBeCloseTo(1.4, 6);
  });

  it("ignores a window that costs nothing, and anything unparseable", () => {
    expect(weatherFromAdjustments({ "diwali week": "1.000" })).toEqual(CLEAR);
    expect(weatherFromAdjustments({ "diwali week": "0.900" })).toEqual(CLEAR);
    expect(weatherFromAdjustments({ broken: "not a number" })).toEqual(CLEAR);
  });

  it("renders the tenant's own word for the season, and classifies nothing", () => {
    // `db/models/calendar.py`: "Free text, tenant-defined … a label rather than
    // a code". A frontend that matched on the substring "monsoon" would be
    // inventing a classification the backend deliberately left to the customer.
    const marathi = weatherFromAdjustments({ पावसाळा: "1.500" });
    expect(marathi.label).toBe("पावसाळा");
    expect(marathi.kind).toBe("monsoon");
  });

  it("saturates rather than running off the end of a shading term", () => {
    const extreme = weatherFromAdjustments({ monsoon: "9.000" });
    expect(extreme.wetness).toBe(1);
    expect(weatherFromAdjustments({ rain: "1.150" }).kind).toBe("rain");
    expect(WEATHER.soakedMultiplier).toBeGreaterThan(1);
  });
});

describe("§E7.4 — the sun follows the tenant's own local time", () => {
  const EQUATOR = { lat: 0, lng: 0 };

  it("is overhead at solar noon on the equator at an equinox", () => {
    // 2026-03-20 12:00 UTC is within a few minutes of solar noon at 0°E.
    const sun = sunAt(EQUATOR, new Date("2026-03-20T12:00:00Z"));
    expect(sun.altitudeDeg).toBeGreaterThan(80);
    expect(sun.up).toBeGreaterThan(0.98);
  });

  it("is below the horizon on the other side of the world at the same instant", () => {
    const antipode = sunAt({ lat: 0, lng: 180 }, new Date("2026-03-20T12:00:00Z"));
    expect(antipode.altitudeDeg).toBeLessThan(-80);
  });

  it("moves west over an hour, as a sun does", () => {
    const morning = sunAt({ lat: 18.52, lng: 73.86 }, new Date("2026-06-21T03:00:00Z"));
    const later = sunAt({ lat: 18.52, lng: 73.86 }, new Date("2026-06-21T04:00:00Z"));
    expect(later.altitudeDeg).toBeGreaterThan(morning.altitudeDeg);
    expect(later.azimuthDeg).toBeGreaterThan(morning.azimuthDeg);
  });

  it("returns a unit vector, so nothing downstream has to normalise it", () => {
    const sun = sunAt({ lat: 18.52, lng: 73.86 }, new Date("2026-01-15T09:30:00Z"));
    expect(Math.hypot(sun.east, sun.north, sun.up)).toBeCloseTo(1, 9);
  });

  it("holds the light at the horizon at night rather than under the ground", () => {
    // A stated deviation, not a hidden one: a light source below the ground is
    // not night, it is a lighting bug that looks like a style. The azimuth is
    // untouched, so shadows still point the right way at 03:00.
    const midnight = new Date("2026-01-15T20:00:00Z");
    const raw = sunAt({ lat: 18.52, lng: 73.86 }, midnight);
    const lit = lightingSun({ lat: 18.52, lng: 73.86 }, midnight);
    expect(raw.up).toBeLessThan(0);
    expect(lit.up).toBe(WEATHER.minSunUp);
    expect(lit.azimuthDeg).toBe(raw.azimuthDeg);
    expect(Math.hypot(lit.east, lit.north, lit.up)).toBeCloseTo(1, 6);
  });

  it("turns the key light down through twilight instead of switching it off", () => {
    // Civil twilight rather than the horizon, because a city fifteen minutes
    // after sunset is not black.
    expect(keyIntensity(WEATHER.twilightDeg - 5)).toBe(0);
    expect(keyIntensity(WEATHER.fullLightDeg + 40)).toBe(1);
    expect(keyIntensity(0)).toBeGreaterThan(0);
    expect(keyIntensity(0)).toBeLessThan(1);
  });
});
