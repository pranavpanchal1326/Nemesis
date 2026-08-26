import { expect, test, type Page } from "@playwright/test";

/**
 * The two front doors, driven — ADR-0059.
 *
 * `tests/portal.test.ts` asserts the *lists*: that the staff door carries every
 * console screen with the right chip, and that every card has words. This file
 * asserts the thing a list cannot: that the links answer, that the two doors
 * reach each other, that every surface has a way back to one of them, and that
 * the receipt field takes a receipt.
 *
 * **Why every href is fetched rather than clicked.** Clicking thirteen cards is
 * thirteen navigations and thirteen chances to be flaky about which one settled;
 * `page.request.get` asks the router the same question — *does this address
 * answer* — in one pass, and a card pointing at a 404 fails on the card rather
 * than on whatever the browser did next. The two that are *clicked* are the two
 * that matter as journeys: the door-to-door link and the receipt field.
 */

const CITY = "pune-demo";

/** Every card's href on a door, in document order. */
async function cardHrefs(page: Page, path: string): Promise<string[]> {
  await page.goto(path);
  await expect(page.locator(".portal__card").first()).toBeVisible();
  return page
    .locator(".portal__card")
    .evaluateAll((nodes) => nodes.map((node) => node.getAttribute("href") ?? ""));
}

test.describe("§E14.4 — the resident's door", () => {
  test("names itself, and offers what a resident can do", async ({ page }) => {
    await page.goto("/citizen");
    await expect(page.locator("h1")).toHaveText("For residents");

    const hrefs = await cardHrefs(page, "/citizen");
    expect(hrefs, "the report card").toContain("/report");
    expect(hrefs, "the city index").toContain(`/${CITY}`);
    expect(hrefs, "the honesty table").toContain(`/${CITY}/honesty`);
  });

  test("every card answers", async ({ page }) => {
    for (const href of await cardHrefs(page, "/citizen")) {
      const response = await page.request.get(href);
      expect(response.status(), `${href} does not answer`).toBeLessThan(400);
    }
  });

  test("the receipt field refuses something that is not a receipt", async ({ page }) => {
    await page.goto("/citizen");
    await page.fill("#receipt", "not-a-receipt");
    await page.click(".portal__track-submit");

    // Back on the door, with what was typed still in the box and a sentence
    // saying what a receipt id looks like — not a 404 from the ledger.
    await expect(page).toHaveURL(/\/citizen\?rejected=/);
    await expect(page.locator("#receipt-error")).toBeVisible();
    await expect(page.locator("#receipt")).toHaveValue("not-a-receipt");
  });

  test("the receipt field opens a ledger for a well-formed id", async ({ page }) => {
    await page.goto("/citizen");
    // A syntactically valid id that no tenant has issued: the assertion is that
    // the *door* hands off, and what the ledger then says about an unknown id
    // is `tests/citizen.spec.ts`' question, not this file's.
    await page.fill("#receipt", "00000000-0000-4000-8000-000000000000");
    await page.click(".portal__track-submit");
    await expect(page).toHaveURL("/t/00000000-0000-4000-8000-000000000000");
  });
});

test.describe("§E14.4 — the staff door", () => {
  test("names itself, and offers the console and the field app", async ({ page }) => {
    await page.goto("/staff");
    await expect(page.locator("h1")).toHaveText("For staff");

    const hrefs = await cardHrefs(page, "/staff");
    expect(hrefs, "the command view").toContain("/console");
    expect(hrefs, "the review queue").toContain("/console/review");
    expect(hrefs, "the field app").toContain("/field");
  });

  test("every card answers", async ({ page }) => {
    for (const href of await cardHrefs(page, "/staff")) {
      const response = await page.request.get(href);
      expect(response.status(), `${href} does not answer`).toBeLessThan(400);
    }
  });

  test("a screen whose data is a fixture says so on its card", async ({ page }) => {
    // §E24, and the reason this door is generated from `screens.ts` rather than
    // written out: a hand-kept door is a door that eventually shows nine
    // roadmap screens as if they were finished.
    await page.goto("/staff");
    const money = page.locator(".portal__card", { hasText: "Money" }).first();
    await expect(money.locator(".not-wired")).toBeVisible();
  });
});

test.describe("nothing is a dead end", () => {
  test("the doors reach each other", async ({ page }) => {
    await page.goto("/citizen");
    await page.click(".portal__footer a");
    await expect(page).toHaveURL("/staff");

    await page.click(".portal__footer a");
    await expect(page).toHaveURL("/citizen");
  });

  // A loop rather than a parameterised helper: Playwright's `test` has no
  // `.each`, and `origin.spec.ts` names its routes the same way for the same
  // reason — a list in the file is a list somebody has to edit when they add a
  // surface, which is exactly the review moment this gate wants.
  const WAYS: readonly (readonly [name: string, path: string, doors: readonly string[]])[] = [
    ["the landing", "/", ["/citizen", "/staff"]],
    ["the citizen surface", "/report", ["/citizen"]],
    ["the console", "/console", ["/staff"]],
    ["the field app", "/field", ["/staff"]],
    ["a public page", `/${CITY}`, ["/citizen"]],
  ];

  for (const [name, path, doors] of WAYS) {
    test(`${name} links to a door`, async ({ page }) => {
      await page.goto(path);
      const hrefs = await page
        .locator("a[href]")
        .evaluateAll((nodes) => nodes.map((node) => node.getAttribute("href") ?? ""));
      for (const door of doors) {
        expect(hrefs, `${path} has no way to ${door}`).toContain(door);
      }
    });
  }
});
