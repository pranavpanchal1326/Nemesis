import { beforeEach, describe, expect, it, vi } from "vitest";

import { DENSITY } from "@/design/generated/tokens";
import {
  DEFAULT_DENSITY,
  DENSITY_ATTRIBUTE,
  DENSITY_BOOT_SCRIPT,
  DENSITY_MODES,
  DENSITY_STORAGE_KEY,
  isDensityMode,
  storedDensity,
} from "@/lib/density";

/**
 * A10's unit half — §E19, *"three density modes … persisted per user"*.
 *
 * The browser half — that a choice survives a reload and a new tab, and that it
 * is applied before the first paint rather than after hydration — is in
 * `tests/density.spec.ts`, because those are claims about a document loading.
 * What is asserted here is the part a browser test would prove only by
 * accident: that the modes come from the tokens rather than from a list
 * somebody typed, and that the pre-paint script is the same contract as the
 * module it is inlined beside.
 */

/** A `localStorage` that can be made to fail, because a municipal terminal with
 *  storage denied is a deployment rather than a hypothetical. */
function stubStorage(value: string | null, { throws = false } = {}): void {
  vi.stubGlobal("localStorage", {
    getItem: () => {
      if (throws) throw new Error("storage denied");
      return value;
    },
    setItem: () => {
      if (throws) throw new Error("storage denied");
    },
  });
}

describe("density", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("has exactly the modes the tokens define", () => {
    // §E19 says three, and `tokens.json` is where three is decided. A control
    // offering a fourth, or missing one, would be a control disagreeing with
    // the stylesheet it drives.
    expect([...DENSITY_MODES]).toEqual(Object.keys(DENSITY));
    expect(DENSITY_MODES).toHaveLength(3);
  });

  it("defaults to the mode the stylesheet already applies to :root", () => {
    // `tokens.css` emits `:root, [data-density="compact"]`. A default that
    // disagreed would make the first paint one mode and the first read another.
    expect(DEFAULT_DENSITY).toBe("compact");
    expect(isDensityMode(DEFAULT_DENSITY)).toBe(true);
  });

  it("reads a stored mode back", () => {
    stubStorage("dense");
    expect(storedDensity()).toBe("dense");
  });

  it("falls back rather than trusting what it read", () => {
    // Storage is writable by anything sharing the origin, and by the user.
    for (const junk of [null, "", "spacious", "__proto__"]) {
      stubStorage(junk);
      expect(storedDensity(), String(junk)).toBe(DEFAULT_DENSITY);
    }
  });

  it("survives a browser profile that denies storage", () => {
    stubStorage(null, { throws: true });
    expect(storedDensity()).toBe(DEFAULT_DENSITY);
  });

  it("inlines a boot script that agrees with the module", () => {
    // The script is a string in the document and cannot import anything, so
    // the one risk it carries is drifting from the constants it duplicates.
    // Asserted here rather than reviewed.
    expect(DENSITY_BOOT_SCRIPT).toContain(JSON.stringify(DENSITY_STORAGE_KEY));
    expect(DENSITY_BOOT_SCRIPT).toContain(JSON.stringify(DENSITY_ATTRIBUTE));
    for (const mode of DENSITY_MODES) {
      expect(DENSITY_BOOT_SCRIPT, mode).toContain(`"${mode}"`);
    }
    // And it must not be able to take the page down before hydration.
    expect(DENSITY_BOOT_SCRIPT).toContain("catch");
  });

  it("applies only a mode it recognises, in the script as well as the module", () => {
    // The script's guard is an `indexOf` over the same list. If that check ever
    // disappears, an attacker-writable storage key becomes an attribute
    // injected into `<html>`.
    expect(DENSITY_BOOT_SCRIPT).toContain("indexOf");
  });
});
