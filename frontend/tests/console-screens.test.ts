import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import consoleBundle from "../src/i18n/base/console.json" with { type: "json" };
import { roadmapPhase, SCREENS, screenForPath, SECTIONS } from "../src/console/screens";

const ROOT = join(fileURLToPath(new URL(".", import.meta.url)), "..");
const APP = join(ROOT, "src", "app", "(console)");

/**
 * F7's first gate — §E24, and the rule the whole console rests on.
 *
 * > **No ROADMAP screen is reachable from a public URL** — a route test, not a
 * > convention.
 *
 * "A route test, not a convention" is the operative phrase, and it is why this
 * file reads the route sources rather than standing up a production server: the
 * guard is a property of the code, and a test that only checked one deployment
 * would pass on the day somebody forgot it in a different one.
 *
 * It also closes the gap the older `route-guard.test.ts` could not see. That
 * one asserts every `proof/` route calls `devOnly()`; it has no way to know
 * that `/console/money` should. The registry in `screens.ts` is what makes
 * "should" a fact a machine can check — and the same registry is what the rail
 * and the palette render, so a screen cannot be reachable in one and unguarded
 * in the other.
 */

/**
 * `devOnly()` **called**, not merely mentioned.
 *
 * The first version of this matched the string anywhere in the file and failed
 * on `/console/review`, whose docstring says *"No `devOnly()`. This screen is
 * backed end to end"*. A prose mention is the opposite of a call, and a guard
 * that cannot tell them apart would eventually accept a screen that only talks
 * about being guarded. Anchored to a statement position, which is where the
 * call must be — first, before anything else runs.
 */
const CALLS_DEV_ONLY = /^\s*devOnly\(\);/m;

/** Where a screen's `page.tsx` lives, from its href. */
function pageFor(href: string): string {
  const segments = href.split("/").filter((segment) => segment !== "");
  return join(APP, ...segments, "page.tsx");
}

describe("§E24 — a roadmap screen cannot reach a public URL", () => {
  it("every screen in the registry has a route", () => {
    // A rail item with no page is a 404 an officer finds by clicking.
    for (const screen of SCREENS) {
      expect(existsSync(pageFor(screen.href)), `${screen.href} has no page.tsx`).toBe(true);
    }
  });

  it("there are roadmap screens to guard", () => {
    // A test that silently checks nothing reports green for the absence of the
    // thing it was written to protect.
    expect(SCREENS.filter((screen) => roadmapPhase(screen) !== undefined).length).toBeGreaterThan(
      0,
    );
  });

  it.each(
    SCREENS.filter((screen) => roadmapPhase(screen) !== undefined).map(
      (screen) => [screen.id, screen.href] as const,
    ),
  )("%s is dev-only", (_id, href) => {
    const source = readFileSync(pageFor(href), "utf8");
    expect(CALLS_DEV_ONLY.test(source), `${href} does not call devOnly()`).toBe(true);
  });

  it.each(
    SCREENS.filter((screen) => roadmapPhase(screen) === undefined).map(
      (screen) => [screen.id, screen.href] as const,
    ),
  )("%s is wired and therefore not dev-only", (_id, href) => {
    // The inverse assertion matters as much as the first one. A REAL screen
    // marked dev-only would be as dishonest in the other direction as a fixture
    // shipped to a citizen — and it would 404 in production, silently, for the
    // people the console is for.
    const source = readFileSync(pageFor(href), "utf8");
    expect(CALLS_DEV_ONLY.test(source), `${href} calls devOnly() and should not`).toBe(false);
  });
});

describe("§E10.1 — every screen has words", () => {
  const bundle = consoleBundle as Record<string, string>;

  it.each(SCREENS.map((screen) => [screen.id] as const))("%s has a label and a hint", (id) => {
    // A missing key renders as `⟦nav.money⟧` — visible, which is the design —
    // but the rail is the one place that would ship to every screen at once.
    expect(bundle[`nav.${id}`], `nav.${id} is missing`).toBeTypeOf("string");
    expect(bundle[`nav.${id}.hint`], `nav.${id}.hint is missing`).toBeTypeOf("string");
  });

  it.each(SECTIONS.map((section) => [section] as const))(
    "the %s section has a title",
    (section) => {
      expect(bundle[`section.${section}`], `section.${section} is missing`).toBeTypeOf("string");
    },
  );

  it("every screen belongs to a declared section", () => {
    for (const screen of SCREENS) {
      expect(SECTIONS as readonly string[]).toContain(screen.section);
    }
  });
});

describe("screenForPath resolves the longest match", () => {
  it("does not mark command current on every console URL", () => {
    // `/console` is a prefix of every console href, so a naive `startsWith`
    // would put `aria-current="page"` on the first rail item everywhere.
    expect(screenForPath("/console")?.id).toBe("command");
    expect(screenForPath("/console/review")?.id).toBe("review");
    expect(screenForPath("/console/review/42")?.id).toBe("review");
    expect(screenForPath("/console/policy")?.id).toBe("policy");
  });

  it("answers nothing for a path outside the console", () => {
    expect(screenForPath("/report")).toBeUndefined();
    expect(screenForPath("/pune-demo/ward/W22")).toBeUndefined();
  });

  it("does not match a sibling whose name starts the same way", () => {
    // `/console/reviewers` is not `/console/review`. It falls back to the
    // console root rather than marking the review screen current — an unknown
    // console URL is still inside the console, and the rail should say so
    // truthfully rather than highlight a screen the reader is not on.
    expect(screenForPath("/console/reviewers")?.id).toBe("command");
  });
});
