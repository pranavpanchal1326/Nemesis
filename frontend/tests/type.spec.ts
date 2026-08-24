import { expect, test } from "@playwright/test";

import { TYPE_STEPS } from "../src/design/generated/tokens.ts";

/**
 * §E10, §E10.1, §E22 — the type gates.
 *
 * §E2 defect #4 is that *"Devanagari was a localisation task, not a design
 * task"*, and names the consequence exactly: **"Line-height, shirorekha
 * clearance, and per-script scale are not retrofittable."** These assertions
 * are what stop that defect from being re-introduced by an ordinary-looking
 * stylesheet edit six months from now.
 */

const PROOF = "/developers/proof/type";

/** From src/design/tokens.json — the two numbers §E10.1's rule resolves to. */
const DELTA = 0.15;
const FLOOR = 1.35;

test.describe("§6 Principle #6 — self-hosted, offline-capable", () => {
  test("no request for a face leaves the origin", async ({ page }) => {
    const offOrigin: string[] = [];
    page.on("request", (request) => {
      const url = new URL(request.url());
      const isFont =
        request.resourceType() === "font" || /\.(?:woff2?|ttf|otf)$/i.test(url.pathname);
      if (isFont && url.host !== "127.0.0.1:3210" && url.host !== "localhost:3210") {
        offOrigin.push(request.url());
      }
    });

    await page.goto(PROOF);
    await page.waitForLoadState("networkidle");

    expect(
      offOrigin,
      "a font was fetched from a third party — §6 Principle #6, and the reason " +
        "Phase 29's air-gapped bootstrap would fail on the demo laptop",
    ).toEqual([]);
  });

  test("the faces that shipped are the faces that render", async ({ page }) => {
    await page.goto(PROOF);
    await page.waitForLoadState("networkidle");

    // `document.fonts` reports what the engine actually loaded, which is a
    // stronger claim than "the @font-face rule exists".
    const loaded = await page.evaluate(() =>
      [...document.fonts].filter((f) => f.status === "loaded").map((f) => f.family),
    );

    for (const family of ["Switzer", "Gambarino", "Panchang", "Noto Sans Devanagari", "Kalam"]) {
      expect(loaded, `${family} did not load`).toContain(family);
    }
  });
});

test.describe("§E10.1 — Devanagari is a design partner, not a fallback", () => {
  test("every step carries +0.15 line-height over its Latin value", async ({ page }) => {
    await page.goto(PROOF);
    await page.waitForLoadState("networkidle");

    for (const step of TYPE_STEPS) {
      const latin = page.locator(`[data-lang="en"][data-step="${step}"]`);
      const deva = page.locator(`[data-lang="mr"][data-step="${step}"]`);

      const [latinMetrics, devaMetrics] = await Promise.all([
        latin.evaluate((el) => {
          const s = getComputedStyle(el);
          return { size: parseFloat(s.fontSize), leading: parseFloat(s.lineHeight) };
        }),
        deva.evaluate((el) => {
          const s = getComputedStyle(el);
          return { size: parseFloat(s.fontSize), leading: parseFloat(s.lineHeight) };
        }),
      ]);

      // Same size, per script — §E10.1 forbids globally scaling one from the
      // other, so the *size* must match and only the leading may differ.
      expect(devaMetrics.size, `${step}: font size differs between scripts`).toBeCloseTo(
        latinMetrics.size,
        1,
      );

      // §E10.1's rule as implemented: a delta AND a floor. The delta alone
      // clips the matras wherever the Latin leading is set below 1.0, which is
      // every display step — measured and recorded in tokens.json.
      const ratio = (metrics: { size: number; leading: number }) => metrics.leading / metrics.size;
      const expected = Math.max(ratio(latinMetrics) + DELTA, FLOOR);
      expect(
        ratio(devaMetrics),
        `${step}: Devanagari leading is ${ratio(devaMetrics).toFixed(3)} against Latin ` +
          `${ratio(latinMetrics).toFixed(3)}; expected max(latin + ${String(DELTA)}, ${String(FLOOR)})`,
      ).toBeCloseTo(expected, 2);
    }
  });

  test("Devanagari ink clears its line box at every step", async ({ page }) => {
    await page.goto(PROOF);
    await page.waitForLoadState("networkidle");

    // Measured with real ink extents, not with `scrollHeight`. A `<p>` whose
    // leading is below 1.0 always reports overflow — that is the display steps
    // behaving as designed, not a clipped shirorekha. What actually matters is
    // whether the glyphs' own ascent and descent fit the line the type scale
    // gives them, which is what `actualBoundingBox*` reports.
    const measured = await page.evaluate((steps: readonly string[]) => {
      const canvas = document.createElement("canvas");
      const ctx = canvas.getContext("2d");
      if (!ctx) return [];
      return steps.map((step) => {
        const el = document.querySelector<HTMLElement>(`[data-lang="mr"][data-step="${step}"]`);
        if (!el) return { step, ink: 0, leading: 1 };
        const style = getComputedStyle(el);
        ctx.font = `${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
        const m = ctx.measureText(el.textContent);
        return {
          step,
          ink: m.actualBoundingBoxAscent + m.actualBoundingBoxDescent,
          leading: parseFloat(style.lineHeight),
        };
      });
    }, TYPE_STEPS);

    for (const row of measured) {
      expect(
        row.ink,
        `${row.step}: Devanagari ink is ${row.ink.toFixed(1)}px in a ${row.leading.toFixed(1)}px ` +
          `line — the shirorekha and the matras do not clear, and §E10.1's +0.15 is not enough here`,
      ).toBeLessThanOrEqual(row.leading);
    }
  });
});

test.describe("§E10.2 — two hard rules", () => {
  test("every numeric context is tabular", async ({ page }) => {
    await page.goto(PROOF);
    const variant = await page
      .locator('[data-lang="en"][data-step="mono-data"]')
      .evaluate((el) => getComputedStyle(el).fontVariantNumeric);
    // A column of costs that does not align is a column nobody trusts, and this
    // product's entire proposition is trust in columns of costs.
    expect(variant).toContain("tabular-nums");
  });

  test("prose measure caps at 68ch", async ({ page }) => {
    await page.goto(PROOF);
    const width = await page
      .locator('[data-lang="en"][data-step="body"]')
      .evaluate((el) => el.getBoundingClientRect().width);
    const chWidth = await page.locator('[data-lang="en"][data-step="body"]').evaluate((el) => {
      const probe = document.createElement("span");
      probe.style.font = getComputedStyle(el).font;
      probe.textContent = "0";
      el.appendChild(probe);
      const w = probe.getBoundingClientRect().width;
      probe.remove();
      return w;
    });
    expect(width / chWidth).toBeLessThanOrEqual(68.5);
  });
});
