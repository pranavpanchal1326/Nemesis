import { expect, test, type Page } from "@playwright/test";

import { expect as originExpect, test as originTest } from "./fixtures/origin";

/**
 * A11's gate — §E22, *"RTL-ready layout primitives"*.
 *
 * > The RTL locale renders mirrored with no physical-property regressions. — F2
 *
 * **What was, and was not, true before F2.** Every stylesheet in `src/` uses
 * logical properties and `scripts/check-guards.ts` fails the build on a
 * physical `left` / `right` / `margin-left`, so the ground was prepared. But
 * logical properties do nothing until something sets a direction, and nothing
 * in this application ever set one: the register's word for that state was
 * *"ready is a claim until a locale proves it."*
 *
 * **The locale is `ar`, and it carries no Arabic words.** `nem seed-demo`
 * declares it on the demo tenant as *data* — ward and category names — because
 * NEMESIS publishes no Arabic UI copy. So the surface renders English words in
 * a right-to-left frame. That is exactly the condition this assertion needs and
 * no more than the deployment can honestly claim; a page of machine-translated
 * civic English would prove less and claim more.
 *
 * **Mirroring is asserted by geometry, not by the attribute.** `dir="rtl"` on
 * an element proves that a prop was passed. What matters is whether the *box
 * model* turned, and the honest way to ask that is to measure the same element
 * in both directions and require it to have moved to the other side of its
 * container. A single physical `margin-left` surviving the guard — in a
 * third-party stylesheet, say, which the guard does not read — fails here and
 * passes everything else.
 */

/** The demo city, provisioned by `nem seed-demo`, which declares `en`, `mr` and
 *  `ar`. See that script for why `ar` is one of them. */
const CITY = "pune-demo";

/** The §E18 proof surface. Renders from the generated response type with no
 *  backend, which is what lets the mirroring half of this file be
 *  deterministic rather than dependent on what the pipeline produced today. */
const PROOF = "/developers/proof/public";

async function stackIsUp(page: Page): Promise<boolean> {
  try {
    const city = await page.request.get(`/${CITY}`);
    return city.ok();
  } catch {
    return false;
  }
}

/** Where an element sits inside its container, as a fraction: 0 is flush to the
 *  container's left edge, 1 to its right. Direction-agnostic on purpose — the
 *  assertion is that this number moved to the other end. */
async function horizontalAnchor(page: Page, selector: string): Promise<number> {
  return page.evaluate((css) => {
    const element = document.querySelector(css);
    const parent = element?.parentElement;
    if (element === null || parent === null || parent === undefined) return Number.NaN;
    const box = element.getBoundingClientRect();
    const frame = parent.getBoundingClientRect();
    const room = frame.width - box.width;
    return room <= 1 ? Number.NaN : (box.left - frame.left) / room;
  }, selector);
}

test.describe("§E22 — the frame turns (A11)", () => {
  test("the public surface declares its direction from its locale", async ({ page }) => {
    await page.goto(`${PROOF}?locale=ar`);
    const surface = page.locator("[data-surface='public']");
    await expect(surface).toHaveAttribute("lang", "ar");
    await expect(surface).toHaveAttribute("dir", "rtl");

    // And it is derived, not global: the same page in the product's own
    // locales runs the other way.
    for (const locale of ["en", "mr"]) {
      await page.goto(`${PROOF}?locale=${locale}`);
      await expect(page.locator("[data-surface='public']")).toHaveAttribute("dir", "ltr");
    }
  });

  test("the public surface actually mirrors, measured", async ({ page }) => {
    // The locale nav is the right element to measure: it is a row of links in a
    // flex footer, so its position within its container is decided entirely by
    // the inline axis — which is the axis `dir` flips.
    await page.goto(`${PROOF}?locale=en`);
    const ltr = await horizontalAnchor(page, ".public__locales");

    await page.goto(`${PROOF}?locale=ar`);
    const rtl = await horizontalAnchor(page, ".public__locales");

    expect(Number.isNaN(ltr) || Number.isNaN(rtl), "nothing measurable to mirror").toBe(false);

    // Two claims, and neither is "it is flush to the edge". The footer has
    // other children, so the nav's resting anchor is a number the layout
    // decides, not zero — asserting against a hand-picked threshold would be
    // asserting against today's padding.
    //
    // It moved, a long way. 0.4 rather than 0.5 because the nav does not rest
    // flush against either edge in either direction, so the full sweep is not
    // available to it; a layout that did not turn scores 0.
    expect(rtl - ltr, "the frame did not turn").toBeGreaterThan(0.4);
    // and it landed at the *mirror* of where it started, which is the actual
    // claim. A layout pinned by a physical property fails the first; one that
    // turned only partly — a stray physical padding inside a turned parent —
    // fails the second.
    expect(Math.abs(rtl - (1 - ltr)), "turned, but not to the mirror").toBeLessThan(0.05);
  });

  test("the citizen surface declares its direction too", async ({ browser }) => {
    // §E17's one-thumb layout is the surface where a frame that did not turn
    // would be most obviously wrong, and it negotiates from `Accept-Language`
    // rather than from a query string — there is no language switch on a
    // three-screen citizen flow.
    //
    // A context with a locale rather than `setExtraHTTPHeaders`: the header has
    // to be on the *document* request, and the browser owns that one. Setting
    // it per page is a header on the requests the page makes afterwards, which
    // is a different thing and quietly asserts nothing.
    const context = await browser.newContext({ locale: "ar" });
    try {
      const page = await context.newPage();
      await page.goto("/report");
      const surface = page.locator("[data-surface='report']");
      await expect(surface).toHaveAttribute("lang", "ar");
      await expect(surface).toHaveAttribute("dir", "rtl");
    } finally {
      await context.close();
    }
  });
});

/**
 * The live half: the demo tenant's own `ar`, negotiated through the control
 * plane rather than through a fixture.
 *
 * Uses the off-origin fixture, so a right-to-left run also asserts §6 Principle
 * #6 — an RTL locale is exactly where somebody would be tempted to reach for a
 * CDN's Arabic webfont.
 */
originTest.describe("§E22 — the tenant's own RTL locale, live", () => {
  originTest("a locale declared in the control plane is offered and rendered", async ({ page }) => {
    originTest.skip(!(await stackIsUp(page)), `the stack is down — start it and seed ${CITY}`);

    await page.goto(`/${CITY}?locale=ar`);
    const surface = page.locator("[data-surface='public']");
    await originExpect(surface).toHaveAttribute("dir", "rtl");
    await originExpect(surface).toHaveAttribute("lang", "ar");

    // A2's other half, on the same page: the switch is built from what the
    // tenant declares, so a locale added upstream is offered downstream with no
    // code change. `ar` is in that list because `nem seed-demo` put it there.
    await originExpect(page.locator(".public__locales a[hreflang='ar']")).toHaveCount(1);
  });
});
