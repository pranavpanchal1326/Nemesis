import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { PRESS, SEVERITY } from "../src/design/generated/tokens.ts";
import { jitterAt, planPress } from "../src/press/press-model.ts";

const SRC = join(fileURLToPath(new URL(".", import.meta.url)), "..", "src");

/**
 * ADR-0038 — "the press is one token source in two implementations".
 *
 * The ADR's whole risk is that the CSS press and the TSL press drift: two
 * sources for one effect, with no test that can catch a divergence in
 * appearance. These assertions attack that from both ends — the plan is
 * deterministic and correct, and neither implementation is allowed a parameter
 * the plan did not give it.
 */

describe("§E6.4 — the quality dial drops passes, it does not fade them", () => {
  it("full prints three inks, reduced two, flat one", () => {
    const at = (quality: "full" | "reduced" | "flat") =>
      planPress({ surface: "citizen", quality, severity: "high", seed: 7 });

    expect(at("full").plates).toHaveLength(3);
    expect(at("reduced").plates).toHaveLength(2);
    expect(at("flat").plates).toHaveLength(1);
  });

  it("flat is solid ink in poster register — no screen, no slip", () => {
    const plan = planPress({ surface: "citizen", quality: "flat", severity: "high", seed: 7 });
    expect(plan.cellPx).toBe(0);
    expect(plan.animated).toBe(false);
    for (const plate of plan.plates) expect(plate.offsetPx).toEqual([0, 0]);
  });

  it("reduced prints a coarser screen than full — a bolder print, not a worse one", () => {
    const full = planPress({ surface: "public", quality: "full", seed: 1 });
    const reduced = planPress({ surface: "public", quality: "reduced", seed: 1 });
    expect(reduced.cellPx).toBeGreaterThan(full.cellPx);
    expect(reduced.cellPx).toBe(PRESS.halftone.cellCoarse);
  });
});

describe("§E9.4 rule 3 — the badge and the shader are the same number", () => {
  it("the severity pass carries the glaze straight from the token", () => {
    const plan = planPress({ surface: "citizen", quality: "full", severity: "critical", seed: 1 });
    const severityPlate = plan.plates.find((p) => p.id === "severity:critical");
    expect(severityPlate?.hex).toBe(SEVERITY.critical.glaze);
  });

  it("an ink set that names `severity` prints one pass shorter without one", () => {
    // Not a fallback colour. A press with nothing on the third drum prints two
    // inks, and that is a legible state rather than an error state.
    const withSeverity = planPress({ surface: "public", quality: "full", severity: "low", seed: 1 });
    const without = planPress({ surface: "public", quality: "full", seed: 1 });
    expect(withSeverity.plates).toHaveLength(3);
    expect(without.plates).toHaveLength(2);
  });
});

describe("§E6.1 stage 3 — misregistration is a slip, and it is reproducible", () => {
  it("the same seed prints the same registration", () => {
    const a = planPress({ surface: "story", quality: "full", seed: 42 });
    const b = planPress({ surface: "story", quality: "full", seed: 42 });
    expect(a.plates.map((p) => p.offsetPx)).toEqual(b.plates.map((p) => p.offsetPx));
  });

  it("a different seed prints a different registration", () => {
    const a = planPress({ surface: "story", quality: "full", seed: 42 });
    const b = planPress({ surface: "story", quality: "full", seed: 43 });
    expect(a.plates.map((p) => p.offsetPx)).not.toEqual(b.plates.map((p) => p.offsetPx));
  });

  it("every slip is a direction and a distance inside the token's range", () => {
    // A sheet moves through a press; it does not jitter on two independent
    // axes. The distance is what tokens.json bounds, so that is what is checked.
    const plan = planPress({ surface: "story", quality: "full", seed: 9 });
    for (let step = 0; step < 60; step += 1) {
      for (const [dx, dy] of jitterAt(plan, step)) {
        const distance = Math.hypot(dx, dy);
        expect(distance).toBeGreaterThanOrEqual(PRESS.misregistration.minPx - 0.01);
        expect(distance).toBeLessThanOrEqual(PRESS.misregistration.maxPx + 0.01);
      }
    }
  });

  it("a still tier does not move between steps", () => {
    const plan = planPress({ surface: "story", quality: "reduced", seed: 9 });
    expect(jitterAt(plan, 0)).toEqual(jitterAt(plan, 137));
  });

  it("a golden image at step n is reproducible without running a clock", () => {
    const plan = planPress({ surface: "story", quality: "full", seed: 5 });
    expect(jitterAt(plan, 31)).toEqual(jitterAt(plan, 31));
    expect(jitterAt(plan, 31)).not.toEqual(jitterAt(plan, 32));
  });
});

describe("ADR-0038 — neither implementation may invent a parameter", () => {
  const read = (relative: string) => readFileSync(join(SRC, relative), "utf8");

  it.each(["press/press-filter.ts", "press/press-tsl.ts"])(
    "%s takes its numbers from the plan, not from the token file",
    (file) => {
      const source = read(file);
      // Importing tokens directly would let one implementation read a value the
      // other does not, which is precisely the drift the ADR names.
      expect(source).not.toMatch(/from ["']@\/design\/generated\/tokens["']/);
      expect(source).toMatch(/from ["']\.\/press-model["']/);
    },
  );

  it("the model is the only module that reads the press tokens", () => {
    expect(read("press/press-model.ts")).toMatch(/from ["']@\/design\/generated\/tokens["']/);
  });

  it("neither implementation contains a GLSL string (ADR-0037)", () => {
    for (const file of ["press/press-tsl.ts", "press/press-filter.ts"]) {
      expect(read(file)).not.toMatch(/ShaderMaterial|gl_FragColor|#version/);
    }
  });
});
