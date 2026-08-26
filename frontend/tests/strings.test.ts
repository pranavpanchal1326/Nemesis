import { afterEach, describe, expect, it, vi } from "vitest";

import { t } from "@/lib/i18n/strings";

/**
 * A17 / ADR-0058 — the string tier that could never resolve, asserted gone.
 *
 * The tier was removed because it fetched
 * `/control-plane/translations/{namespace}/{locale}` for namespaces the
 * registry refuses to hold, and was therefore answered `200 {}` on every
 * request it would ever make. `check-guards.ts`' ninth ban stops the *literal*
 * coming back; this file asserts the *behaviour*, which is the half a grep
 * cannot see — a loader that reached the network through some other spelling
 * would pass the guard and fail here.
 *
 * It replaces a defence rather than removing one. F12 found that `loadStrings`
 * caught the control plane's error *response* and not the thrown connection, so
 * a deployment whose control plane was down rendered a 500 for every non-source
 * locale and rendered perfectly in English. That failure mode is now
 * unreachable by construction, and *"makes no call at all"* is the stronger
 * statement of it — so it is the one asserted.
 */

/**
 * **The import has to happen after the global is stubbed, and finding out why
 * is the reason this file is worth more than a grep.**
 *
 * The first version of this test assigned `globalThis.fetch` inside the case
 * and called an already-imported `loadStrings`. It passed — *and it passed with
 * the removed tier deliberately pasted back in*, which is the check every gate
 * in this repository is required to survive. `openapi-fetch` reads
 * `globalThis.fetch` **once, at `createClient()`**, and `src/server/upstream.ts`
 * calls that at module scope. So the client had already closed over the real
 * `fetch` before any test body ran, and the stub was decoration.
 *
 * Resetting the module registry and importing inside the stub's lifetime is
 * what makes the assertion load-bearing: the upstream client is constructed
 * against the exploding `fetch`, so a reinstated tier throws rather than
 * quietly succeeding.
 */
async function loadAgainst(fetchImpl: typeof fetch) {
  vi.resetModules();
  vi.stubGlobal("fetch", fetchImpl);
  return import("@/server/strings");
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
  vi.restoreAllMocks();
});

describe("assembling a bundle touches no network", () => {
  it("resolves every namespace and locale with fetch made unavailable", async () => {
    // Not a spy that records calls — a `fetch` that *throws*. A spy asserts
    // "we did not call it this time"; this asserts the page renders on a
    // machine where the call is impossible, which is the deployment fact.
    const exploded = vi.fn(() => {
      throw new Error("loadStrings reached the network");
    });
    const { loadStrings, SOURCE_LOCALE } = await loadAgainst(exploded);

    for (const locale of [SOURCE_LOCALE, "mr", "ar", "kok"]) {
      for (const namespace of ["common", "citizen", "console", "public"] as const) {
        const strings = await loadStrings(namespace, locale);
        expect(strings.locale).toBe(locale);
      }
      // The multi-namespace path too: it fans out over `loadOne`, and a tier
      // re-added there rather than in the single path would slip a test that
      // only checked one shape.
      const merged = await loadStrings(["common", "public"], locale);
      expect(merged.namespace).toBe("public");
    }

    expect(exploded).not.toHaveBeenCalled();
  });

  it("still renders a seeded locale's own words, so the removal is not vacuous", async () => {
    const { loadStrings, SOURCE_LOCALE } = await import("@/server/strings");
    // The tier is gone and the words are not: `mr` resolves from the shipped
    // seed. Without this, "no network call" would also be satisfied by a
    // loader that returned nothing.
    const english = await loadStrings("public", SOURCE_LOCALE);
    const marathi = await loadStrings("public", "mr");
    const key = Object.keys(marathi.bundle)[0] ?? "";
    expect(key.length).toBeGreaterThan(0);
    expect(t(marathi, key)).not.toBe(t(english, key));
  });

  it("falls through to the source language for a locale nothing ships", async () => {
    const { loadStrings, SOURCE_LOCALE } = await import("@/server/strings");
    // Konkani is the locale `nem gate-phase18-locale` adds over HTTP, and the
    // half of it this application owns is precisely the half that does not
    // arrive: product copy renders in English while the tenant's own ward names
    // render in `kok`. That is the true statement ADR-0058 leaves behind, so it
    // is asserted rather than described.
    const konkani = await loadStrings("common", "kok");
    const english = await loadStrings("common", SOURCE_LOCALE);
    expect(konkani.bundle).toEqual(english.bundle);
    expect(konkani.locale).toBe("kok");
  });
});

describe("negotiation is unchanged by the removal", () => {
  it("prefers an explicit choice over the tenant default and the header", async () => {
    const { negotiateLocale } = await import("@/server/strings");
    expect(
      negotiateLocale({
        explicit: "mr",
        tenantDefault: "en",
        acceptLanguage: "en-GB,en;q=0.9",
        available: ["en", "mr"],
      }),
    ).toBe("mr");
  });

  it("resolves a region subtag to its language", async () => {
    const { negotiateLocale } = await import("@/server/strings");
    expect(negotiateLocale({ acceptLanguage: "mr-IN,mr;q=0.9", available: ["en", "mr"] })).toBe(
      "mr",
    );
  });

  it("falls back to the source language when nothing matches", async () => {
    const { negotiateLocale, SOURCE_LOCALE } = await import("@/server/strings");
    expect(negotiateLocale({ acceptLanguage: "fr-FR", available: ["en", "mr"] })).toBe(
      SOURCE_LOCALE,
    );
  });
});
