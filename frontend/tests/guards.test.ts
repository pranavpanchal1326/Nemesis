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

  it("every guard cites the section it enforces", () => {
    for (const v of violations) {
      expect(v.guard.source).toMatch(/§|ADR-/);
      expect(v.guard.why.length).toBeGreaterThan(40);
    }
  });
});
