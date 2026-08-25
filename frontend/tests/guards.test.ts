import { describe, expect, it } from "vitest";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { runGuards } from "../scripts/check-guards.ts";

const FIXTURES = join(fileURLToPath(new URL(".", import.meta.url)), "fixtures", "lint");

/**
 * M0's gate: the lint rules *fail* on a seeded violation of each of the four
 * bans. A guard nobody has ever seen fail is a guard nobody knows works.
 */
describe("design-law guards", () => {
  const violations = runGuards(FIXTURES);
  const fired = new Set(violations.map((v) => v.guard.id));

  it.each([
    ["no-colour-literal", "colour-literal.ts"],
    ["no-glsl", "glsl.ts"],
    ["no-cdn", "cdn.ts"],
    ["no-hand-written-contract", "hand-written-contract.ts"],
  ])("%s fires on its seeded violation", (id, file) => {
    expect(fired).toContain(id);
    expect(violations.some((v) => v.guard.id === id && v.file === file)).toBe(true);
  });

  it("the escape marker exempts a line, and nothing else in that file leaks through", () => {
    expect(violations.some((v) => v.file === "exempt.ts")).toBe(false);
  });

  it("naming a generated type is not declaring a contract", () => {
    // Both shapes in this fixture tripped the guard before it was refined, and
    // both are legitimate: `type X = components["schemas"]["Y"]` is a *name for*
    // the published contract, and `import { type Complaint }` declares nothing
    // at all. Fixed in the guard rather than papered over with an exemption
    // comment, because an exemption on a correct line teaches the next reader
    // that the rule is approximate.
    expect(violations.some((v) => v.file === "generated-alias.ts")).toBe(false);
  });

  it("widening a generated type is still declaring a contract", () => {
    // The refinement's own risk, asserted. An intersection is a different shape
    // wearing the published one's name, which is exactly what Law 2 forbids —
    // and it is the case a naive "does the right-hand side mention `components`"
    // check would have waved through.
    expect(
      violations.some(
        (v) => v.guard.id === "no-hand-written-contract" && v.file === "widened-contract.ts",
      ),
    ).toBe(true);
  });

  it("every guard cites the section it enforces", () => {
    for (const v of violations) {
      expect(v.guard.source).toMatch(/§|ADR-/);
      expect(v.guard.why.length).toBeGreaterThan(40);
    }
  });
});
