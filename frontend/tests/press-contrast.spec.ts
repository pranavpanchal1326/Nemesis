import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type Locator, type Page } from "@playwright/test";
import { PNG } from "pngjs";

/**
 * §E22, §E6.2 — **text stays legible after the press**, measured in a browser.
 *
 * `tests/contrast.test.ts` already measures every severity pair against every
 * ground, from the token file. That suite is correct and it has a hole this one
 * fills: it measures **design intent**, and the press is applied afterwards, by
 * a stack of blend modes, over a ground the tokens never name.
 *
 * §E2 defect #7 records the same class of mistake from the other side — a
 * palette that measured fine and failed once something composited on top of it.
 * §E2 defect #20 records it again — *"the token-level contrast suite measured
 * design intent and passed; only a real engine measured what shipped."*
 *
 * **The defect this exists because of.** §E17.3's receipt carries no imagery, so
 * the press had nothing to halftone — and screened the blank stock anyway, at
 * full strength. Its append-only sentence, the one line the whole document is
 * asking to be believed on, rendered at **2.86:1**. Every layer was individually
 * correct: the ink was near-black, the stock was pale, the type was exempt from
 * the press per ADR-0038. The *paper underneath the type* was not exempt, which
 * is half an exemption, and no assertion covered the difference.
 *
 * So this measures pixels: the darkest pixel in a text block is its ink, the
 * most common luminance is the ground it sits on, and the ratio between them is
 * what a person actually looks at.
 */

/** The same committed fixture `citizen.spec.ts` uses — a decodable photograph,
 *  because the receipt under test has to be one the pipeline actually issued.
 *  See `tests/fixtures/media/README.md`. */
const PHOTO = join(
  fileURLToPath(new URL(".", import.meta.url)),
  "fixtures",
  "media",
  "pothole.jpg",
);

/** §E22's floor for body text. */
const BODY_FLOOR = 4.5;

/** WCAG 1.4.3's large-text allowance — 18.66 px bold or 24 px regular. Applied
 *  only to the receipt's heading, which is both. */
const LARGE_FLOOR = 3.0;

function relativeLuminance(r: number, g: number, b: number): number {
  const channel = (value: number): number => {
    const c = value / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

/**
 * The ink-to-paper ratio of a rendered element.
 *
 * **The ground is the modal luminance, not the lightest pixel.** A text block is
 * mostly background, so the most common value *is* the paper — while the
 * lightest pixel might be a single anti-aliased highlight that nothing is
 * actually read against. The ink is the darkest, because that is the stroke.
 *
 * Rounded to two decimals before bucketing, so anti-aliasing does not scatter
 * one ground colour across two hundred near-identical buckets and leave the
 * mode meaningless.
 */
async function inkOnPaper(element: Locator): Promise<number> {
  await element.scrollIntoViewIfNeeded();
  const png = PNG.sync.read(await element.screenshot({ type: "png" }));

  const buckets = new Map<number, number>();
  for (let i = 0; i < png.data.length; i += 4) {
    const luminance =
      Math.round(
        relativeLuminance(png.data[i] ?? 0, png.data[i + 1] ?? 0, png.data[i + 2] ?? 0) * 100,
      ) / 100;
    buckets.set(luminance, (buckets.get(luminance) ?? 0) + 1);
  }

  const ink = Math.min(...buckets.keys());
  const [paper = 1] = [...buckets.entries()].sort((a, b) => b[1] - a[1])[0] ?? [];

  const lighter = Math.max(ink, paper);
  const darker = Math.min(ink, paper);
  return (lighter + 0.05) / (darker + 0.05);
}

async function stackIsUp(page: Page): Promise<boolean> {
  try {
    const response = await page.request.get("/api/realtime");
    return response.ok();
  } catch {
    return false;
  }
}

/**
 * A report to look at. Filed through the product rather than fixtured, for the
 * same reason `citizen.spec.ts` does it: the receipt under test has to be one
 * the system actually issued.
 */
async function fileAReport(page: Page): Promise<string> {
  await page.goto("/report");
  await page.setInputFiles('input[type="file"]', PHOTO);
  await page.getByRole("button", { name: /^send$/i }).click();
  const send = page.getByRole("button", { name: /^send$/i });
  await expect(send).toBeEnabled({ timeout: 15_000 });
  await send.click();
  await expect(page.getByRole("link", { name: /follow what happens/i })).toBeVisible({
    timeout: 20_000,
  });
  const href = await page.getByRole("link", { name: /follow what happens/i }).getAttribute("href");
  return href?.replace("/t/", "") ?? "";
}

test.use({
  permissions: ["geolocation"],
  geolocation: { latitude: 18.5074, longitude: 73.8077 },
});

test.describe("§E22 — the press never costs text its legibility", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!(await stackIsUp(page)), "the backend is not reachable — run `nem up`");
  });

  test("§E17.3's receipt clears the floor on every line", async ({ page }) => {
    const complaintId = await fileAReport(page);
    await page.goto(`/t/${complaintId}`);
    await expect(page.locator(".receipt__sheet")).toBeVisible({ timeout: 20_000 });

    // The sentence the document exists to be believed on. This is the one that
    // measured 2.86:1 before the press stopped screening blank stock.
    expect(
      await inkOnPaper(page.locator(".receipt__append-only")),
      "the receipt's append-only sentence is below §E22's floor",
    ).toBeGreaterThanOrEqual(BODY_FLOOR);

    // The reference and the hash: JetBrains Mono at 13 px, which is body text.
    expect(
      await inkOnPaper(page.locator(".receipt__hash")),
      "the chain hash is below §E22's floor",
    ).toBeGreaterThanOrEqual(BODY_FLOOR);

    expect(
      await inkOnPaper(page.locator(".receipt__title")),
      "the receipt's heading is below the large-text floor",
    ).toBeGreaterThanOrEqual(LARGE_FLOOR);
  });

  test("§E17.4's ledger clears the floor", async ({ page }) => {
    const complaintId = await fileAReport(page);
    await page.goto(`/t/${complaintId}`);
    await expect(page.locator(".evidence-trail__row").first()).toBeVisible({ timeout: 20_000 });

    for (const selector of [
      ".evidence-trail__event",
      ".evidence-trail__time",
      ".evidence-trail__hash",
    ]) {
      const element = page.locator(selector).first();
      if ((await element.count()) === 0) continue;
      expect(await inkOnPaper(element), `${selector} is below §E22's floor`).toBeGreaterThanOrEqual(
        BODY_FLOOR,
      );
    }
  });

  test("a sheet with nothing on it is not screened", async ({ page }) => {
    // The mechanism, asserted directly rather than only through its effect.
    // §E6.1's stages split into ink (1–3, 5) and paper (4, 6); a sheet with no
    // imagery has paper and no ink. A future change that reinstates the screen
    // here would put the receipt back at 2.86:1, and the two assertions above
    // would tell you the ratio moved without telling you why.
    const complaintId = await fileAReport(page);
    await page.goto(`/t/${complaintId}`);

    const sheet = page.locator(".receipt");
    await expect(sheet).toHaveAttribute("data-imagery", "none");
    await expect(sheet.locator(".press__screens")).toBeHidden();
    // The paper stages remain: a sheet is still a sheet.
    await expect(sheet.locator(".press__grain")).toBeAttached();
    await expect(sheet.locator(".press__deckle")).toBeAttached();
  });
});
