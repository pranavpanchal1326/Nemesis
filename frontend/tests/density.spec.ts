import { expect, test } from "./fixtures/origin";

/**
 * A10's gate — §E19, *"three density modes … persisted per user"*.
 *
 * > Density survives a reload and a new tab.  — F2
 *
 * Three claims, and the third is the one that is usually missed:
 *
 * 1. the control changes the document;
 * 2. the choice outlives the page, and outlives the tab it was made in;
 * 3. it is applied **before the first paint**, not after hydration. A
 *    preference restored in an effect is a preference the officer watches the
 *    console reflow into, on every navigation, forever. That is asserted by
 *    reading the attribute at `domcontentloaded` — before React has run — which
 *    is the only moment at which the two implementations differ.
 *
 * The console surface is the one that carries the control today; F3 moves it
 * into the §E19 chrome, and this file follows it there.
 */

const CONSOLE = "/developers"; // any route under the root layout carries the script
const SURFACE = "/console";

const STORAGE_KEY = "nemesis.density";

test.describe("density is persisted (A10)", () => {
  test("the control changes the document and the document remembers", async ({ page }) => {
    await page.goto(SURFACE);

    const control = page.locator(".density");
    await expect(control).toBeVisible();

    // The stylesheet's default is `compact`, and `:root` already is it — so the
    // attribute is legitimately absent until somebody chooses.
    await control.getByRole("radio", { name: /dense/i }).check();
    await expect(page.locator("html")).toHaveAttribute("data-density", "dense");
    expect(await page.evaluate((key) => localStorage.getItem(key), STORAGE_KEY)).toBe("dense");

    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("data-density", "dense");
    await expect(control.getByRole("radio", { name: /dense/i })).toBeChecked();
  });

  test("the choice follows the officer into a new tab", async ({ page, context }) => {
    await page.goto(SURFACE);
    await page
      .locator(".density")
      .getByRole("radio", { name: /comfortable/i })
      .check();

    // A second tab in the same context is the same origin and the same
    // `localStorage`, which is exactly what "per user, on this machine" means.
    const second = await context.newPage();
    await second.goto(SURFACE);
    await expect(second.locator("html")).toHaveAttribute("data-density", "comfortable");
    await second.close();
  });

  test("it is applied before the first paint, not after hydration", async ({ page }) => {
    await page.goto(CONSOLE);
    await page.evaluate((key) => {
      localStorage.setItem(key, "dense");
    }, STORAGE_KEY);

    // `domcontentloaded` is before React has hydrated. An effect-based
    // implementation reads `null` here and passes every other assertion in this
    // file — which is why this one exists.
    await page.goto(CONSOLE, { waitUntil: "domcontentloaded" });
    expect(
      await page.evaluate(() => document.documentElement.getAttribute("data-density")),
      "the density was not applied by the pre-paint script",
    ).toBe("dense");
  });
});
