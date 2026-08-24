import { execFileSync } from "node:child_process";
import { readdirSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const ROOT = join(fileURLToPath(new URL(".", import.meta.url)), "..");
const FIXTURES = join(ROOT, "tests", "fixtures", "types");

/**
 * The contracts §E26 says are *"enforced by the type system, not by review"*.
 *
 * A rule enforced by the type system is only enforced while somebody has
 * watched it fail. Otherwise it is a claim *about* the type system, which is a
 * different and much weaker thing — and it is the exact shape of §E2 defect
 * #14, where fairness features were *"listed as ROADMAP UI mockups"* and a
 * mockup was mistaken for a path.
 *
 * So each fixture in `tests/fixtures/types/` is a compile that must fail, and
 * this asserts that it does. The mirror of M0's seeded lint violations, one
 * layer up.
 */

interface Expectation {
  readonly file: string;
  readonly why: string;
  /** The TypeScript diagnostic that must appear for this file. */
  readonly code: string;
}

const EXPECTED: readonly Expectation[] = [
  {
    file: "flagged-without-disclaimer.tsx",
    why: "§16.4, §22.2 — a flag cannot render without its disclaimer",
    code: "TS2741",
  },
  {
    file: "flagged-without-response.tsx",
    why: "§6 Principle #8 — the appeal path ships with the accountability feature",
    code: "TS2741",
  },
  {
    file: "untranslated-literal.tsx",
    why: "Phase 18 — a literal in a component defeats the locale gate",
    code: "TS2322",
  },
  {
    file: "concatenated-sentence.tsx",
    why: "§E10.1 — a sentence is a translation unit, so fragments do not add up",
    code: "TS2322",
  },
  {
    file: "non-exhaustive-status.ts",
    why: "§E26.1 — a view that omits a status is a defect",
    code: "TS2366",
  },
  {
    file: "severity-without-level.tsx",
    why: "unscored is a value, not an omission",
    code: "TS2741",
  },
];

function compile(): string {
  try {
    execFileSync(
      process.execPath,
      [join(ROOT, "node_modules", "typescript", "bin", "tsc"), "-p", FIXTURES],
      { cwd: ROOT, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] },
    );
    return "";
  } catch (error) {
    const failure = error as { stdout?: string; stderr?: string };
    return `${failure.stdout ?? ""}${failure.stderr ?? ""}`;
  }
}

describe("§E26 — the required props are required, provably", () => {
  const output = compile();

  it("the fixture directory does not compile at all", () => {
    expect(
      output,
      "every fixture compiled — the type-level contracts are not being enforced",
    ).not.toBe("");
  });

  it("tsc actually had files to compile", () => {
    /**
     * This assertion exists because the failure it catches already happened.
     * The root tsconfig excludes `tests/fixtures/types` so the main typecheck
     * ignores it; that `exclude` is *inherited*, so the fixture config compiled
     * an empty input set and `tsc` failed with TS18003 — "no inputs were
     * found". Every per-file assertion below went red at once, which was the
     * only reason anybody noticed.
     *
     * A gate that passes because it ran nothing is the worst kind of green, so
     * it is now named rather than inferred.
     */
    expect(output, "tsc found no inputs — the fixtures are being excluded").not.toContain(
      "TS18003",
    );
  });

  it.each(EXPECTED)("$file fails: $why", ({ file, code }) => {
    const lines = output.split(/\r?\n/).filter((line) => line.includes(file));
    expect(lines.length, `${file} produced no diagnostic`).toBeGreaterThan(0);
    expect(
      lines.some((line) => line.includes(code)),
      `${file} failed, but not with ${code}:\n${lines.join("\n")}`,
    ).toBe(true);
  });

  it("every fixture on disk is accounted for", () => {
    // A fixture nobody asserts against is a fixture that can start compiling
    // without anybody noticing.
    const onDisk = readdirSync(FIXTURES).filter((name) => /\.tsx?$/.test(name));
    const asserted = new Set(EXPECTED.map((expectation) => expectation.file));
    for (const name of onDisk) {
      expect(asserted.has(name), `${name} is not asserted in tests/types.test.ts`).toBe(true);
    }
  });
});
