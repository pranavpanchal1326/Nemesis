import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

import { COMPLAINT_STATUSES, WORK_ORDER_STATUSES } from "../src/generated/enums.ts";
import { SEVERITY_DESCENDING } from "../src/design/generated/tokens.ts";

/**
 * The matrix, swept — §E24, §E22.
 *
 * > Storybook for every component across **three densities × two themes × two
 * > scripts** … **`axe` gating and Lighthouse budgets in CI.**
 *
 * Twelve combinations, and `axe` over every one of them. §E22 is explicit that
 * *"WCAG 2.2 AA is a floor, audited rather than only scanned"* — a scan is not
 * an audit, and this is the scan half, held to zero violations so the audit half
 * starts from a clean sheet rather than from a backlog.
 */

const PROOF = "/developers/proof/contracts";

const DENSITIES = ["comfortable", "compact", "dense"] as const;
const GROUNDS = ["paper", "light-table"] as const;
const SCRIPTS = ["en", "mr"] as const;

const COMBINATIONS = DENSITIES.flatMap((density) =>
  GROUNDS.flatMap((ground) => SCRIPTS.map((locale) => ({ density, ground, locale }))),
);

function url({
  density,
  ground,
  locale,
}: {
  density: string;
  ground: string;
  locale: string;
}): string {
  return `${PROOF}?density=${density}&ground=${ground}&locale=${locale}`;
}

test.describe("§E22 — axe is clean across the whole matrix", () => {
  for (const combination of COMBINATIONS) {
    test(`${combination.density} · ${combination.ground} · ${combination.locale}`, async ({
      page,
    }) => {
      await page.goto(url(combination));
      await page.waitForLoadState("networkidle");

      const results = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
        .analyze();

      const summary = results.violations.map(
        (violation) => `${violation.id} (${String(violation.nodes.length)}): ${violation.help}`,
      );
      expect(summary, summary.join("\n")).toEqual([]);
    });
  }
});

test.describe("§E26.1 — every member of every vocabulary renders", () => {
  test("all thirteen complaint statuses and all six work order statuses are on the page", async ({
    page,
  }) => {
    // A view that omits one is a defect (§E26.1). Asserting presence rather
    // than eyeballing a screenshot is what makes that checkable — and the two
    // most likely to be missing are the two that only occur when something has
    // gone wrong.
    await page.goto(url({ density: "compact", ground: "paper", locale: "en" }));

    for (const status of COMPLAINT_STATUSES) {
      await expect(page.locator(`.status-chip[data-status="${status}"]`).first()).toBeVisible();
    }
    for (const status of WORK_ORDER_STATUSES) {
      await expect(page.locator(`.status-chip[data-status="${status}"]`).first()).toBeVisible();
    }
  });

  test("every severity carries a mark as well as a colour", async ({ page }) => {
    // §E9.4 rule 2 — colour is never the only channel. Measured, `high` and
    // `medium` are 1.4% apart in grayscale, so on the printouts §E19.7 says
    // officers make, the mark is the only channel that survives.
    await page.goto(url({ density: "compact", ground: "paper", locale: "en" }));

    for (const level of SEVERITY_DESCENDING) {
      const badge = page.locator(`.severity-badge[data-severity="${level}"]`).first();
      await expect(badge).toBeVisible();
      await expect(badge.locator("svg.severity-mark")).toHaveCount(1);
      await expect(badge.locator(".severity-badge__label")).not.toBeEmpty();
    }
  });

  test("the unscored state is a sentence, not an empty badge", async ({ page }) => {
    await page.goto(url({ density: "compact", ground: "paper", locale: "en" }));
    await expect(page.locator(".severity-badge--unscored")).toHaveText(/not yet scored/i);
  });
});

test.describe("ADR-0039 — a flag is hatched, and never a severity colour", () => {
  test("the flagged chip and notice use the flag ink, not a severity ink", async ({ page }) => {
    await page.goto(url({ density: "compact", ground: "paper", locale: "en" }));

    const severityInks = await page.evaluate(() => {
      const style = getComputedStyle(document.documentElement);
      return ["critical", "high", "medium", "low", "resolved"].map((level) =>
        style.getPropertyValue(`--color-sev-${level}-ink`).trim().toLowerCase(),
      );
    });

    for (const selector of [
      '.status-chip[data-register="flagged"] .status-chip__label',
      ".flagged-notice",
    ]) {
      const colour = await page
        .locator(selector)
        .first()
        .evaluate((el) => getComputedStyle(el).color);
      // Not asserting *which* colour — asserting it is none of the five that
      // mean severity. §22.2: an unproven anomaly rendered in a severity colour
      // is a defamation exposure with a design cause.
      for (const ink of severityInks) {
        expect(colour.toLowerCase()).not.toBe(ink);
      }
    }
  });

  test("the flag's response link is present and reachable by keyboard", async ({ page }) => {
    // §6 Principle #8 — the appeal path ships in the same phase as the
    // accountability feature. A response link nobody can reach is a mockup.
    await page.goto(url({ density: "compact", ground: "paper", locale: "en" }));
    const link = page.locator(".flagged-notice__response a");
    await expect(link).toBeVisible();
    await link.focus();
    await expect(link).toBeFocused();
  });
});

test.describe("§E10.1 — the matrix renders in both scripts", () => {
  test("the Devanagari column is actually Devanagari", async ({ page }) => {
    // A matrix whose second script silently fell back to English would pass
    // every other assertion here and prove nothing about the per-script scale.
    await page.goto(url({ density: "compact", ground: "paper", locale: "mr" }));
    const text = await page
      .locator('.severity-badge[data-severity="critical"]')
      .first()
      .innerText();
    expect(text).toMatch(/[ऀ-ॿ]/);
  });

  test("the page declares its language, so the type scale and the voice follow", async ({
    page,
  }) => {
    await page.goto(url({ density: "compact", ground: "paper", locale: "mr" }));
    await expect(page.locator('[data-proof="contracts"]')).toHaveAttribute("lang", "mr");
  });
});

test.describe("§E24 — a not-wired screen cannot reach a public URL", () => {
  test("the proof routes are dev-only by construction", async ({ request }) => {
    /**
     * In development these must answer, or the gates above are testing nothing.
     * In a production build `devOnly()` returns 404 — asserted by
     * `tests/route-guard.test.ts`, which reads the route source rather than
     * standing up a second server, because the guard is a property of the code
     * and not of a deployment.
     */
    for (const path of [PROOF, "/developers/proof/press", "/developers/proof/type"]) {
      const response = await request.get(path);
      expect(response.status(), `${path} should be reachable in development`).toBe(200);
    }
  });
});
