import { expect, test, type Page } from "@playwright/test";

/**
 * ADR-0038 and §E6.2 — **the press applies to imagery and fields, never to
 * text or data.**
 *
 * §E25's Phase 18 gate states the assertion in the strongest available form:
 *
 * > the press renders identically in 2D and 3D at fixed seed, and **text
 * > layers are byte-identical with the press on and off**
 *
 * "Byte-identical" is a deliberate choice of words. Halftoned 13 px type in an
 * operator console would be a defect, not a style, and a tolerance would let it
 * in one pixel at a time. So this compares buffers, not similarity.
 *
 * The test only means something if the press is doing anything at all, which is
 * why the imagery assertion is the other half: the sheet must change, and the
 * type must not.
 */

const PROOF = "/developers/proof/press";

async function shot(page: Page, url: string, selector: string): Promise<Buffer> {
  await page.goto(url);
  const target = page.locator(selector);
  await target.waitFor({ state: "visible" });
  // The press animates at 12 Hz (§E6.1 stage 3). Pinning is not enough on its
  // own — Playwright's `animations: "disabled"` covers CSS, and the seeded plan
  // covers the plates — but a settled frame is what makes the comparison about
  // the press rather than about timing.
  await page.waitForTimeout(250);
  return target.screenshot({ animations: "disabled" });
}

test.describe("§E6.2 — text is exempt from the press", () => {
  test("the text layer is byte-identical with the press on and off", async ({ page }) => {
    const printed = await shot(page, `${PROOF}?seed=1`, '[data-proof="text"]');
    const bypassed = await shot(page, `${PROOF}?seed=1&bypass=1`, '[data-proof="text"]');

    expect(
      printed.equals(bypassed),
      "the text layer changed when the press was applied — §E6.2 and ADR-0038 " +
        "require it to composite in a separate, unprocessed layer",
    ).toBe(true);
  });

  test("the imagery does change, or the test above proves nothing", async ({ page }) => {
    const printed = await shot(page, `${PROOF}?seed=1`, '[data-proof="imagery"]');
    const bypassed = await shot(page, `${PROOF}?seed=1&bypass=1`, '[data-proof="imagery"]');

    expect(
      printed.equals(bypassed),
      "the press left the imagery unchanged, so the byte-identity assertion is vacuous",
    ).toBe(false);
  });

  test("the text layer is byte-identical across every quality tier", async ({ page }) => {
    // §E6.4's dial turns three inks into two into one. If any tier reached the
    // text layer, this is where it would show — and a tier is exactly the kind
    // of code path that gets added later without re-reading the ADR.
    const full = await shot(page, `${PROOF}?seed=1&quality=full`, '[data-proof="text"]');
    for (const quality of ["reduced", "flat"]) {
      const other = await shot(page, `${PROOF}?seed=1&quality=${quality}`, '[data-proof="text"]');
      expect(full.equals(other), `the ${quality} tier changed the text layer`).toBe(true);
    }
  });

  test("the text layer is byte-identical across every severity pass", async ({ page }) => {
    const none = await shot(page, `${PROOF}?seed=1`, '[data-proof="text"]');
    for (const severity of ["critical", "high", "medium", "low", "resolved"]) {
      const other = await shot(page, `${PROOF}?seed=1&severity=${severity}`, '[data-proof="text"]');
      expect(other.equals(none), `the ${severity} pass changed the text layer`).toBe(true);
    }
  });
});

test.describe("§E6.1 — the press is reproducible at a fixed seed", () => {
  test("the same seed prints the same sheet", async ({ page }) => {
    const a = await shot(page, `${PROOF}?seed=7`, '[data-proof="imagery"]');
    const b = await shot(page, `${PROOF}?seed=7`, '[data-proof="imagery"]');
    expect(a.equals(b), "a fixed seed did not reproduce — golden images are impossible").toBe(true);
  });

  test("a different seed prints a different registration", async ({ page }) => {
    const a = await shot(page, `${PROOF}?seed=7`, '[data-proof="imagery"]');
    const b = await shot(page, `${PROOF}?seed=8`, '[data-proof="imagery"]');
    expect(a.equals(b)).toBe(false);
  });
});
