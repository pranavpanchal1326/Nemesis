import { test as base, expect, type Page } from "@playwright/test";

import { collectOffOrigin, isOffOrigin, test } from "./fixtures/origin.ts";

/**
 * A13 — every route, every resource type.
 *
 * > **The no-off-origin assertion covers fonts only.** `tests/type.spec.ts`
 * > fails on a font fetched from a third party; images, scripts and audio are
 * > covered only by the source-level CDN grep. *A runtime fetch the grep cannot
 * > see would pass.*
 *
 * The fixture in `fixtures/origin.ts` does the watching. This file names the
 * surfaces it watches, and proves the watcher is not vacuous.
 *
 * **Why a route list and not a crawl.** A crawler finds the pages that are
 * linked; the failure mode here is a page that a dependency reaches out from,
 * and those are as likely to be the unlinked proof surfaces as the public ones.
 * The list is explicit so that adding a route without adding it here is visible
 * in review — which is the same argument `check_event_catalog.py` makes about
 * an event type nobody registered.
 */

/** The demo city, provisioned by `nem seed-demo` and published by ADR-0046. */
const CITY = "pune-demo";

/**
 * Routes that render without the backend.
 *
 * Every one of these is a surface a browser can reach with the API down, which
 * is exactly the condition §E13's ladder and Phase 29's air-gapped bootstrap
 * describe — so these are the routes where an off-origin fetch does the most
 * damage and the ones that must never skip.
 */
const OFFLINE_ROUTES: readonly (readonly [name: string, path: string])[] = [
  ["the story surface", "/"],
  ["the citizen report flow", "/report"],
  ["the console shell", "/console"],
  ["the developer portal", "/developers"],
  ["the type proof", "/developers/proof/type"],
  ["the press proof", "/developers/proof/press"],
  ["the contract matrix", "/developers/proof/contracts"],
  ["the §E18 proof surface", "/developers/proof/public"],
  ["the honesty table", `/${CITY}/honesty`],
  ["the honesty table in Marathi", `/${CITY}/honesty?locale=mr`],
];

/** Routes that need the stack, and skip loudly rather than passing vacuously. */
const LIVE_ROUTES: readonly (readonly [name: string, path: string])[] = [
  ["the city index", `/${CITY}`],
];

async function stackIsUp(page: Page): Promise<boolean> {
  try {
    const response = await page.request.get(`/${CITY}`);
    return response.ok() && (await response.text()).includes("Published by");
  } catch {
    return false;
  }
}

/**
 * Load a page the way a visitor does, and wait for the requests that arrive
 * after first paint.
 *
 * `networkidle` and not `load`: the fetch this gate exists to catch is the one a
 * hydrated component makes, and `load` fires before hydration has asked for
 * anything.
 */
async function settle(page: Page, path: string): Promise<void> {
  await page.goto(path);
  await page.waitForLoadState("networkidle");
}

test.describe("§6 Principle #6 — no request leaves the origin", () => {
  for (const [name, path] of OFFLINE_ROUTES) {
    test(`${name} — ${path}`, async ({ page }) => {
      await settle(page, path);
      // The assertion is the fixture's, applied at teardown. What this body
      // owes is a page that actually rendered: an assertion over a 404's three
      // requests is a pass nobody earned.
      await expect(page.locator("body")).toBeVisible();
    });
  }

  for (const [name, path] of LIVE_ROUTES) {
    test(`${name} — ${path}`, async ({ page }) => {
      test.skip(!(await stackIsUp(page)), "the stack is not running");
      await settle(page, path);
      await expect(page.locator("body")).toBeVisible();
    });
  }

  test("every scripted surface is covered by the sweep above", () => {
    // The list is the gate's scope, so a gap in it is a gap in the gate. This
    // is the same self-check `check-guards` runs against its own rules.
    const covered = new Set(OFFLINE_ROUTES.concat(LIVE_ROUTES).map(([, path]) => path));
    for (const required of ["/report", "/console", `/${CITY}/honesty`, "/developers/proof/press"]) {
      expect(covered, `${required} is not swept`).toContain(required);
    }
  });
});

/**
 * The gate on the gate — F1: *"a baseline nobody has watched fail is a
 * screenshot, not a gate."* The same is true of an assertion.
 */
base.describe("the assertion is not vacuous", () => {
  base(
    "a seeded off-origin <img> is caught on a route that is not the public one",
    async ({ page }) => {
      const collected = collectOffOrigin(page);

      await page.goto("/developers/proof/type");
      await page.waitForLoadState("networkidle");
      expect(collected, "the clean route was already dirty").toEqual([]);

      // `.invalid` is reserved by RFC 2606 and never resolves, so seeding this
      // violation does not itself make a network request — the assertion is about
      // what the engine *attempted*, which is the right question.
      await page.evaluate(async () => {
        await new Promise<void>((resolve) => {
          const img = document.createElement("img");
          img.addEventListener("error", () => {
            resolve();
          });
          img.addEventListener("load", () => {
            resolve();
          });
          img.src = "https://cdn.example.invalid/pothole.png";
          document.body.appendChild(img);
        });
      });

      expect(collected.join("\n"), "an off-origin image was not caught").toContain(
        "cdn.example.invalid",
      );
      expect(collected.join("\n"), "the resource type was not recorded").toContain("image");
    },
  );

  base("the collector ignores the schemes that never leave the machine", () => {
    // The press draws to `data:` and the viewfinder holds a `blob:`. A gate that
    // failed on those would be turned off within a week, which is the failure
    // mode worth designing against.
    expect(isOffOrigin("data:image/png;base64,iVBORw0KGgo=")).toBe(false);
    expect(isOffOrigin("blob:http://127.0.0.1:3210/2f0a")).toBe(false);
    expect(isOffOrigin("http://127.0.0.1:3210/fonts/switzer-variable.woff2")).toBe(false);
    expect(isOffOrigin("http://localhost:3210/report")).toBe(false);
    expect(isOffOrigin("https://fonts.gstatic.com/s/switzer.woff2")).toBe(true);
    // The API is off-origin from the browser's point of view, and that is the
    // point: every read goes through the BFF, so a direct hit on :8000 is a
    // route that forgot.
    expect(isOffOrigin("http://127.0.0.1:8000/api/v1/public/pune-demo")).toBe(true);
  });
});
