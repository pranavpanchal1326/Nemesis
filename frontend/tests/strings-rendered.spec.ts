import { expect, test, type Page } from "@playwright/test";

/**
 * No surface renders a missing-key marker — the gate that was missing.
 *
 * `t()` renders an absent key as `⟦namespace.key⟧` rather than blank or as the
 * key itself, and `tests/contracts.test.ts` asserts that behaviour at the unit
 * level: *"a missing key is visible rather than blank"*. Visible to whom, is
 * the question this file answers. Nothing was reading the rendered text of a
 * route, so the marker was visible to a **visitor** and to nobody else.
 *
 * **It was already happening.** `/developers/proof/clay` loaded the `common`
 * namespace alone, while the peer list beside the canvas renders `<Figure>`,
 * which reads `figure.count` — a key that lives in `public` because it is the
 * same sentence the public pages print. Every one of five thousand rows read
 * `REPORTS ⟦FIGURE.COUNT⟧`. The landing and `/developers/proof/story` pass
 * both namespaces and were fine; this route was the odd one out, and the fault
 * was found by opening the page and reading it rather than by any assertion.
 *
 * So: the sweep, and the missing half of a two-part design. The fallback is
 * *designed* — §E13's ladder says a degradation is designed rather than
 * apologised for, and a visible marker beats a blank line every time. What a
 * designed fallback still needs is something that fails when it fires in
 * production, because a fallback nobody is alerted by is just a slower way of
 * shipping the bug.
 *
 * Deliberately a `.spec.ts` and not a `.test.ts`: the fault is a *namespace
 * passed at a route's `loadStrings` call*, which no jsdom render of a component
 * can see. It needs the page.
 */

/** The demo city, provisioned by `nem seed-demo` and published by ADR-0046. */
const CITY = "pune-demo";

/**
 * The marker `lib/i18n/strings.ts` emits, as its two halves.
 *
 * Matched as the bracket characters rather than a full pattern: `plural()`
 * emits `⟦key.category⟧` and `t()` emits `⟦key⟧`, and a gate that only knew one
 * shape would pass a page whose *counts* were broken while its labels were
 * fine — which is exactly the shape of the fault this file was written for.
 */
const OPEN = "⟦";
const CLOSE = "⟧";

/**
 * Every route that renders words without the backend.
 *
 * The same argument `tests/origin.spec.ts` makes for an explicit list rather
 * than a crawl: the surfaces most likely to be missing a namespace are the
 * unlinked proof routes, which no crawler reaches. Adding a route without
 * adding it here is visible in review.
 */
const ROUTES: readonly (readonly [name: string, path: string])[] = [
  ["the story surface", "/"],
  ["the resident's door", "/citizen"],
  ["the staff door", "/staff"],
  ["the citizen report flow", "/report"],
  ["the console shell", "/console"],
  ["the field app", "/field"],
  ["the clay proof", "/developers/proof/clay"],
  ["the story proof", "/developers/proof/story"],
  ["the type proof", "/developers/proof/type"],
  ["the press proof", "/developers/proof/press"],
  ["the contract matrix", "/developers/proof/contracts"],
  ["the §E18 proof surface", "/developers/proof/public"],
  ["the honesty table", `/${CITY}/honesty`],
  ["the honesty table in Marathi", `/${CITY}/honesty?locale=mr`],
];

/**
 * Read what a person reads.
 *
 * `innerText` and not `textContent`: the marker is only a defect where somebody
 * can see it, and `textContent` returns the contents of `<template>` and of
 * anything `display: none` is deliberately hiding. A false failure on a hidden
 * node would teach the next person to widen the exemption rather than fix the
 * key.
 */
async function visibleText(page: Page): Promise<string> {
  return page.evaluate(() => document.body.innerText);
}

function markersIn(text: string): string[] {
  return [...text.matchAll(new RegExp(`${OPEN}[^${CLOSE}]*${CLOSE}`, "g"))].map(
    (match) => match[0],
  );
}

test.describe("§E22 — no rendered surface shows a missing-key marker", () => {
  for (const [name, path] of ROUTES) {
    test(`${name} — ${path}`, async ({ page }) => {
      await page.goto(path);
      await page.waitForLoadState("networkidle");

      // A page that did not render is not a page that passed: an empty body
      // contains no markers and would sail through the assertion below.
      await expect(page.locator("body")).toBeVisible();
      const text = await visibleText(page);
      expect(text.trim().length, `${path} rendered no text at all`).toBeGreaterThan(0);

      const found = markersIn(text);
      expect(
        [...new Set(found)].join(", "),
        `${path} renders ${String(found.length)} missing-key marker(s). ` +
          "The fallback is working; the namespace passed to loadStrings on this route is not.",
      ).toBe("");
    });
  }
});

/**
 * The gate on the gate — F1: *"a baseline nobody has watched fail is a
 * screenshot, not a gate."* The same is true of an assertion, and this one was
 * watched failing against the real fault before it was fixed.
 */
test.describe("the assertion is not vacuous", () => {
  test("a marker injected into a clean page is caught", async ({ page }) => {
    await page.goto("/developers/proof/type");
    await page.waitForLoadState("networkidle");
    expect(markersIn(await visibleText(page)), "the clean route was already dirty").toEqual([]);

    await page.evaluate(
      ({ open, close }: { open: string; close: string }) => {
        const p = document.createElement("p");
        p.textContent = `${open}figure.count${close}`;
        document.body.appendChild(p);
      },
      { open: OPEN, close: CLOSE },
    );

    expect(markersIn(await visibleText(page)), "an injected marker was not caught").toEqual([
      `${OPEN}figure.count${CLOSE}`,
    ]);
  });
});
