import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { collectOffOrigin } from "./fixtures/origin.ts";

/**
 * M6's gate — §E18, §E13 Tier D, ADR-0021.
 *
 * > **A k-anonymity hole never renders as a zero.**
 * >
 * > A flagged row cannot render without its disclaimer and response link — the
 * > same compile-time contract as M4, exercised on a public route.
 * >
 * > **Public pages render correctly with JavaScript disabled** (§E13 Tier D).
 *
 * Three clauses, three different kinds of proof, and the split is deliberate.
 *
 * The **compile-time** clause is closed by `tests/fixtures/types/` — three
 * fixtures that must not compile, asserted by `tests/types.test.ts` against
 * their exact diagnostics. Nothing here can prove a type error; a passing render
 * proves only that somebody remembered the props this time.
 *
 * The **suppression** clause is driven through `/developers/proof/public`, which
 * renders the four states from the generated response type. It is not asserted
 * against the live city, and that is a decision rather than a shortcut:
 * suppression is a backend decision about data the pipeline produced, so a ward
 * is only below the floor if it happens to hold between one and four reports
 * that morning. An E2E waiting for that state would be green on a Tuesday and
 * vacuous on a Wednesday. The mapping is proved in `public-figures.test.ts`; the
 * *rendering* is proved here, in a browser, deterministically.
 *
 * The **Tier D** clause is asserted against the live stack with JavaScript
 * switched off at the context level, because there is no other honest way to
 * check it. Those tests skip loudly when the stack is down rather than passing
 * vacuously.
 */

const PROOF = "/developers/proof/public";

/** The demo city, provisioned by `nem seed-demo` and published by ADR-0046. */
const CITY = "pune-demo";

async function stackIsUp(page: Page): Promise<boolean> {
  try {
    const response = await page.request.get(`/${CITY}/honesty.json`);
    if (!response.ok()) return false;
    // The honesty page needs no backend, so it proves the *frontend* is up.
    // The city index is the one that proves the API answers.
    const city = await page.request.get(`/${CITY}`);
    return city.ok() && (await city.text()).includes("Published by");
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// The gate: a withheld figure is never a zero
// ---------------------------------------------------------------------------

test.describe("§E18, ADR-0021 — suppression is rendered, never blanked", () => {
  test("a suppressed place shows no digit where its figures would be", async ({ page }) => {
    await page.goto(PROOF);
    const withheld = page.locator('[data-proof-case="withheld"]');
    await expect(withheld).toBeVisible();

    // Every value cell in the suppressed case. The assertion is on the *values*
    // and not on the section, because the labels legitimately contain no digits
    // and the threshold sentence legitimately contains one.
    const values = withheld.locator(".published-figure__value .figure");
    await expect(values).toHaveCount(0);

    // And the positive half: the notice is there, with its threshold.
    await expect(withheld.locator(".suppression-notice").first()).toContainText(/fewer than 5/i);
  });

  test("the suppressed case never renders the zero the API actually sent", async ({ page }) => {
    await page.goto(PROOF);
    const withheld = page.locator('[data-proof-case="withheld"]');

    // `aggregates.zone_summary` sends `total_reports: 0` beside `suppressed:
    // true`. This is the assertion that the zero does not survive the journey.
    const cells = await withheld.locator(".published-figure__value").allInnerTexts();
    expect(cells.length).toBeGreaterThan(0);
    for (const cell of cells) {
      // "Fewer than 5 reports" contains a 5 — that is the threshold, and it is
      // supposed to be there. What must not appear is a standalone measure.
      expect(cell, `a bare figure survived suppression: ${cell}`).not.toMatch(/(^|\s)0(\s|$)/);
    }
  });

  test("a genuine zero says 'none filed' rather than showing 0", async ({ page }) => {
    await page.goto(PROOF);
    const quiet = page.locator('[data-proof-case="genuine-zero"]');

    await expect(quiet.locator(".figure--none").first()).toBeVisible();
    // And it is *not* the suppression notice: a quiet ward and a protected ward
    // are different facts and must not read the same.
    await expect(quiet.locator(".suppression-notice")).toHaveCount(0);
  });

  test("a figure that was never measured is not reported as withheld", async ({ page }) => {
    await page.goto(PROOF);
    const fresh = page.locator('[data-proof-case="unmeasured"]');

    await expect(fresh.locator(".figure--unknown").first()).toBeVisible();
    await expect(fresh.locator(".suppression-notice")).toHaveCount(0);
  });

  test("a breakdown that omits categories says how many it omitted", async ({ page }) => {
    await page.goto(PROOF);
    const busy = page.locator('[data-proof-case="populated"]');

    // The visible rows sum to 34 against a total of 41. A reader who can see
    // the shortfall and not its cause concludes the difference is zero.
    await expect(busy.locator(".zone-panel__hidden")).toContainText(/3 more categories/i);
  });

  test("the four figure states render distinguishably", async ({ page }) => {
    await page.goto(PROOF);
    const figures = page.locator('[data-proof-case="figures"]');

    await expect(figures.locator(".figure--none")).toHaveCount(1);
    await expect(figures.locator(".figure--unknown")).toHaveCount(1);
    await expect(figures.locator(".suppression-notice")).toHaveCount(1);
    // `known` renders in the data face; the other three do not.
    await expect(figures.locator(".type-mono-data.figure")).toHaveCount(2);
  });
});

// ---------------------------------------------------------------------------
// The flagged frame — §16.4, §6 Principle #8
// ---------------------------------------------------------------------------

test.describe("§16.4 — a flag and its response are in the same frame", () => {
  test("a rendered flag carries its disclaimer and a working response link", async ({ page }) => {
    await page.goto(PROOF);
    const flagged = page.locator('[data-proof-case="flagged"] .flagged-notice');
    await expect(flagged).toBeVisible();

    // All three, inside the same element — "in the same frame" asserted
    // structurally rather than by both happening to be on the page.
    await expect(flagged).toContainText(/not a finding/i);
    await expect(flagged).toContainText(/track record, not a score/i);

    const response = flagged.locator("a");
    await expect(response).toHaveAttribute("href", /#contractor-response$/);
  });

  test("the detector states its own method", async ({ page }) => {
    // §E19.6: "If you are going to flag a named commercial entity, you show
    // your method." Name, threshold and confidence, or none of the three.
    await page.goto(PROOF);
    const detector = page.locator('[data-proof-case="flagged"] .flagged-notice__detector');
    await expect(detector).toContainText("Rate-card deviation");
    await expect(detector).toContainText("18%");
    await expect(detector).toContainText("0.82");
  });

  test("the flag is not red — ADR-0039", async ({ page }) => {
    await page.goto(PROOF);
    const label = page.locator('[data-proof-case="flagged"] .flagged-notice__disclaimer');
    const colour = await label.evaluate((node) => getComputedStyle(node).color);

    // "An unproven anomaly rendered in urgent red is a §22.2 defamation
    // exposure with a design cause." The words are black; the hatch carries the
    // colour. Asserted as *measured* rather than as a class name, because a
    // stylesheet somewhere else could still repaint it.
    const [r = 0, g = 0, b = 0] = (colour.match(/\d+/g) ?? []).map(Number);
    expect(r, `flag text rendered ${colour}`).toBeLessThan(120);
    expect(Math.abs(r - g)).toBeLessThan(40);
    expect(Math.abs(g - b)).toBeLessThan(40);
  });
});

// ---------------------------------------------------------------------------
// §E13 Tier D — the page with no JavaScript
// ---------------------------------------------------------------------------

test.describe("§E13 Tier D — correct with JavaScript disabled", () => {
  test.use({ javaScriptEnabled: false });

  test("the city index lists its places with no script running", async ({ page }) => {
    test.skip(!(await stackIsUp(page)), "the stack is not running");

    await page.goto(`/${CITY}`);
    await expect(page.locator("h1")).toContainText(/places/i);

    // Real links to real places, in the HTML the server sent. On this surface
    // Tier D is not a degraded mode — it is what a crawler gets, and §16.2's
    // "bookmarkable by journalists" is a claim about exactly this response.
    const places = page.locator(".place-index__item a");
    expect(await places.count()).toBeGreaterThan(0);
    await expect(places.first()).toHaveAttribute("href", new RegExp(`^/${CITY}/ward/`));
  });

  test("a place's figures are in the server's HTML", async ({ page }) => {
    test.skip(!(await stackIsUp(page)), "the stack is not running");

    const href = await page
      .goto(`/${CITY}`)
      .then(() => page.locator(".place-index__item a").first().getAttribute("href"));
    expect(href).not.toBeNull();

    await page.goto(href ?? `/${CITY}`);
    await expect(page.locator(".zone-panel__figures")).toBeVisible();
    // The §22.2 notice is first-class UI, so it is present without hydration.
    await expect(page.locator('[data-notice="system-flagged"]')).toBeVisible();
  });

  test("the honesty table renders in full with no script", async ({ page }) => {
    await page.goto(`/${CITY}/honesty`);

    const rows = page.locator(".honesty__table tbody tr");
    expect(await rows.count()).toBeGreaterThan(40);
    await expect(page.locator(".honesty__legend")).toBeVisible();
  });

  test("the language switch works without JavaScript", async ({ page }) => {
    // §E3.3 — an affordance that is not one must not be shown. A `<select>`
    // with an `onChange` would be inert here; links are not.
    await page.goto(`/${CITY}/honesty?locale=mr`);
    await expect(page.locator("[data-surface='public']")).toHaveAttribute("lang", "mr");
    await expect(page.locator("h1")).toContainText("काय खरे आहे");
  });
});

// ---------------------------------------------------------------------------
// Indexability, sharing, and the data form
// ---------------------------------------------------------------------------

test.describe("§16.2 — indexable, citable, shareable", () => {
  test("a place page is indexable and canonical", async ({ page }) => {
    test.skip(!(await stackIsUp(page)), "the stack is not running");

    const href = await page
      .goto(`/${CITY}`)
      .then(() => page.locator(".place-index__item a").first().getAttribute("href"));
    await page.goto(href ?? `/${CITY}`);

    // The opposite call from `/t/[id]`, deliberately: a complaint id is a
    // capability and is `noindex`; a ward page is meant to be found.
    await expect(page.locator('meta[name="robots"]')).toHaveAttribute("content", /index/);
    await expect(page.locator('link[rel="canonical"]')).toHaveAttribute("href", /\/ward\//);
  });

  test("the share card is a real PNG carrying the notice", async ({ page }) => {
    test.skip(!(await stackIsUp(page)), "the stack is not running");

    await page.goto(`/${CITY}`);
    const src = await page.locator('meta[property="og:image"]').getAttribute("content");
    expect(src).not.toBeNull();

    const image = await page.request.get(src ?? "");
    expect(image.status()).toBe(200);
    expect(image.headers()["content-type"]).toContain("image/png");
    // satori + resvg produce a real raster, not an empty file.
    expect((await image.body()).byteLength).toBeGreaterThan(10_000);

    // The alt text is the card for anybody whose client does not load images,
    // so it must say something about the page rather than name the file.
    await expect(page.locator('meta[property="og:image:alt"]')).toHaveAttribute(
      "content",
      /transparency figures/i,
    );
  });

  test("the honesty table is published as data, not only as prose", async ({ page }) => {
    const response = await page.request.get(`/${CITY}/honesty.json`);
    expect(response.status()).toBe(200);
    expect(response.headers()["content-type"]).toContain("application/json");

    interface Published {
      readonly statuses: readonly string[];
      readonly counts: { readonly system: number; readonly surface: number };
      readonly system: readonly { readonly capability: string }[];
      readonly surfaces: readonly { readonly capability: string }[];
      readonly sources: readonly string[];
    }
    const body = (await response.json()) as Published;

    expect(body.sources).toHaveLength(2);
    expect(body.statuses).toContain("REFRAMED");
    expect(body.system).toHaveLength(body.counts.system);
    expect(body.surfaces).toHaveLength(body.counts.surface);
  });

  test("no request leaves the origin on a public page", async ({ page }) => {
    // §6 Principle #6. The collector is `fixtures/origin.ts` — the same one
    // `origin.spec.ts` applies to every route — because A13's complaint was
    // that this assertion existed in one place and generalised nowhere. Kept
    // here as well as there: this is M6's own gate, and a gate that moves out
    // of the milestone that owns it is a gate nobody notices going missing.
    const offOrigin = collectOffOrigin(page);

    await page.goto(`/${CITY}/honesty`);
    await page.waitForLoadState("networkidle");
    expect(offOrigin, `off-origin requests: ${offOrigin.join(", ")}`).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Accessibility
// ---------------------------------------------------------------------------

test.describe("§E22 — the public surface is clean", () => {
  for (const locale of ["en", "mr"]) {
    test(`axe is clean on every §E18 state — ${locale}`, async ({ page }) => {
      await page.goto(`${PROOF}?locale=${locale}`);
      const results = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
        .analyze();

      expect(
        results.violations.map((violation) => `${violation.id}: ${String(violation.nodes.length)}`),
      ).toEqual([]);
    });
  }

  test("axe is clean on the honesty table", async ({ page }) => {
    await page.goto(`/${CITY}/honesty`);
    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
      .analyze();

    expect(
      results.violations.map((violation) => `${violation.id}: ${String(violation.nodes.length)}`),
    ).toEqual([]);
  });

  test("the page never scrolls sideways, however wide the table", async ({ page }) => {
    // A table is the one element here that may exceed the viewport. It scrolls
    // inside its own box; the document does not.
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`/${CITY}/honesty`);

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
  });
});
