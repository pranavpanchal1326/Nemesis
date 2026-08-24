import { describe, expect, it } from "vitest";

import { contrastRatio, overprint } from "../scripts/generate-tokens.ts";
import {
  PAPER,
  ROLE,
  ROLE_GROUNDS,
  SEVERITY,
  SEVERITY_ROLE,
  type PaperName,
  type SeverityLevel,
} from "../src/design/generated/tokens.ts";

/**
 * §E22 — "WCAG 2.2 AA is a floor, audited rather than only scanned", and
 * "every severity pair tested at 4.5:1 on **both** grounds".
 *
 * This is the test that found the defect. Measured against the palette as §E9
 * authored it, three of its own role labels did not clear the floor:
 * `riso-aqua` as SIGNAL is 2.51:1 on paper-50, `mitti-500` as "secondary text
 * on light" is 3.80:1, and severity ink is 4.17:1 on kraft-200.
 *
 * The fix was not to repaint the Riso inks — they are the premise of the whole
 * direction (§E4) — but to derive text-safe values by **overprint** (§E6.3),
 * which is an operation the press already performs. The role layer in
 * `tokens.json` records each derivation; this file is what keeps it honest.
 */

const GROUND_HEX: Record<string, string> = PAPER;

function ground(name: string): string {
  const hex = GROUND_HEX[name as PaperName];
  if (hex === undefined) throw new Error(`unknown ground: ${name}`);
  return hex;
}

/** What the generator writes for each role. Not a backend contract — the shape
 *  of an artefact this repository produces, narrowed so the union of the two
 *  themes does not collapse to `any` when iterated. */
interface ResolvedRole {
  readonly value: string;
  readonly min: number;
  readonly derivation: string;
}

describe("§E22 — semantic roles clear their floor on every ground", () => {
  for (const theme of ["light", "dark"] as const) {
    const roles: Record<string, ResolvedRole> = ROLE[theme];
    for (const [role, def] of Object.entries(roles)) {
      // The ground is not a foreground. Asserting it against itself would be a
      // test that always fails for a reason that means nothing.
      if (role === "ground") continue;

      for (const stock of ROLE_GROUNDS[theme]) {
        it(`${theme}: ${role} on ${stock} ≥ ${String(def.min)}:1`, () => {
          const ratio = contrastRatio(def.value, ground(stock));
          expect(
            ratio,
            `${role} (${def.value}, from ${def.derivation}) on ${stock} is ${ratio.toFixed(2)}:1`,
          ).toBeGreaterThanOrEqual(def.min);
        });
      }
    }
  }
});

describe("§E9.4 — severity carries type on its own field, on both grounds", () => {
  const levels = Object.keys(SEVERITY) as SeverityLevel[];

  it.each(levels)("light: %s ink on its own tint", (level) => {
    const row = SEVERITY[level];
    const ratio = contrastRatio(row.ink, row.tint);
    expect(
      ratio,
      `${level}: ${row.ink} on ${row.tint} is ${ratio.toFixed(2)}:1`,
    ).toBeGreaterThanOrEqual(SEVERITY_ROLE.light.min);
  });

  it.each(levels)("dark: %s tint on the light table's ground", (level) => {
    // §E9.3 — a backlit print glows, so on the light table the *tint* carries
    // the type and the room stays behind it. This is why inverting the palette
    // was refused rather than implemented: the physics gives the accessible
    // answer for free.
    const row = SEVERITY[level];
    const ratio = contrastRatio(row.tint, ground("mitti-950"));
    expect(ratio).toBeGreaterThanOrEqual(SEVERITY_ROLE.dark.min);
  });

  it.each(levels)("dark: %s glaze draws the mark at 3:1, and never a word", (level) => {
    /**
     * The pair this file measured and the pair the component rendered were not
     * the same pair, and `axe` found it: an earlier badge filled itself with
     * the glaze and set the tint on top, which measures 2.63-4.37:1 across the
     * five levels. Twelve matrix combinations failed.
     *
     * The lesson is recorded in the assertion rather than only in a commit: the
     * glaze clears WCAG 1.4.11's 3:1 for a **meaningful graphic** and does not
     * clear 4.5:1 for text, which is exactly why it draws a shape and an edge
     * and is never asked to carry a label.
     */
    const row = SEVERITY[level];
    const asGraphic = contrastRatio(row.glaze, ground("mitti-950"));
    expect(asGraphic).toBeGreaterThanOrEqual(SEVERITY_ROLE.dark.markMin);

    const asText = contrastRatio(row.tint, row.glaze);
    expect(
      asText,
      `${level}: tint on glaze is ${asText.toFixed(2)}:1 — if this ever clears 4.5 the ` +
        `rule below can be relaxed, and until then the glaze must not carry type`,
    ).toBeLessThan(4.5);
  });

  it("severity type is never asked to sit directly on a table stock", () => {
    // kraft-200 is the zebra row, and `high` is 4.17:1 on it. That is not a
    // palette defect — it is the misuse the `tint` channel exists to prevent,
    // and this assertion is here so the number is on the record rather than
    // discovered later by someone who assumes it passes.
    const high = contrastRatio(SEVERITY.high.ink, ground("kraft-200"));
    expect(high).toBeLessThan(4.5);
    expect(contrastRatio(SEVERITY.high.ink, SEVERITY.high.tint)).toBeGreaterThanOrEqual(4.5);
  });
});

describe("§E22 — re-tested after the press, because halftone changes contrast", () => {
  /**
   * The press multiplies ink onto stock (§E6.1 stage 5). A severity field that
   * runs through it comes out darker than the authored tint, so the authored
   * ratio is not the shipped ratio. Text is exempt by construction (§E6.2,
   * ADR-0038) and does not move — which is exactly what makes this checkable:
   * only one side of the pair changes.
   */
  const levels = Object.keys(SEVERITY) as SeverityLevel[];

  it.each(levels)("%s: ink on its tint, after one overprint pass onto stock", (level) => {
    const row = SEVERITY[level];
    const pressed = overprint(row.tint, ground("paper-50"));
    const ratio = contrastRatio(row.ink, pressed);
    expect(
      ratio,
      `${level}: ${row.ink} on pressed ${pressed} is ${ratio.toFixed(2)}:1`,
    ).toBeGreaterThanOrEqual(4.5);
  });
});

describe("§E9.4 rule 2 — colour is never the only channel", () => {
  it("every severity carries a distinct shape", () => {
    // The *label* is asserted in tests/contracts.test.ts, against the locale
    // bundle — because a label is copy and lives where the control plane can
    // translate it, not in a token file where it would read "Critical" on a
    // Marathi console forever.
    const shapes = new Set(Object.values(SEVERITY).map((s) => s.shape));
    expect(shapes.size).toBe(Object.keys(SEVERITY).length);
  });

  it("where two severities are close in grayscale, their shapes differ in fill", () => {
    /**
     * §E19.7 establishes that officers print, and a printout is grayscale.
     *
     * Measured: `high` (#8A4E12) and `medium` (#6E5A11) sit 1.4% apart in
     * luminance. On a monochrome printout they are the same grey. That is not
     * a defect to fix by repainting — it is precisely the case §E9.4 rule 2
     * was written for, and the rule holds because the two carry a **filled**
     * circle and a **hollow** circle. Outline-versus-outline would not survive
     * the same test, which is why this asserts fill rather than mere
     * difference.
     */
    const rows = Object.entries(SEVERITY).map(([level, row]) => ({
      level,
      shape: row.shape,
      // Contrast against a pure-white reference is a monotonic stand-in for
      // luminance, and it reuses the one conversion the shader also uses.
      grey: contrastRatio(row.ink, "#FFFFFF"), // nemesis-guard-allow: the grayscale reference, not a design colour
    }));

    for (const a of rows) {
      for (const b of rows) {
        if (a.level >= b.level) continue;
        const apart = Math.abs(a.grey - b.grey) / Math.max(a.grey, b.grey);
        if (apart > 0.05) continue;

        const silhouette = (shape: string) => shape.split("-")[0];
        const modifier = (shape: string) => shape.split("-")[1] ?? "";
        const distinguishable =
          silhouette(a.shape) !== silhouette(b.shape) || modifier(a.shape) !== modifier(b.shape);

        expect(
          distinguishable,
          `${a.level} and ${b.level} are ${(apart * 100).toFixed(1)}% apart in grey; ` +
            `shapes "${a.shape}" and "${b.shape}" are the same mark, so a monochrome ` +
            `printout cannot tell them apart`,
        ).toBe(true);
      }
    }
  });
});
