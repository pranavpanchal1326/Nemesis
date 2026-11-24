import { expect } from "@playwright/test";

import { test } from "./fixtures/origin.ts";
import { signInAsOperator } from "./fixtures/operator.ts";

/**
 * The gate refuses — ADR-0062, closing audit finding F5.
 *
 * **Why this file exists separately from `console.spec.ts`.** Every test in
 * that file calls `signInAsOperator()` first, because it is testing the
 * console. So the entire console suite could pass against a build with the gate
 * deleted — signing in would simply be a redundant request. A gate nobody has
 * watched refuse is not a gate, which is the argument `golden.spec.ts` makes
 * about baselines in its own words: *"a baseline nobody has watched fail is a
 * screenshot, not a gate."*
 *
 * So these tests deliberately do **not** sign in. Playwright gives each test a
 * fresh browser context, so there is no cookie to clear — the absence is the
 * fixture.
 *
 * The write assertions matter more than the redirect. A screen an anonymous
 * caller cannot see is a disclosure control; a *write* an anonymous caller
 * cannot make is the thing that stops someone recording the one permitted
 * judgement on a citizen's report forever.
 */

/** Every route handler that attaches `X-Control-Plane-Token`. */
const PRIVILEGED_WRITES = [
  { method: "POST", path: "/api/control/taxonomy" },
  { method: "POST", path: "/api/control/tenants" },
  { method: "PUT", path: "/api/control/tenants/pune-demo/publication" },
  { method: "POST", path: "/api/policy/tuning/dedup" },
  { method: "POST", path: "/api/policy/severity/3/activate" },
  { method: "POST", path: "/api/review/queue/00000000-0000-0000-0000-000000000000/decision" },
] as const;

test.describe("without an operator session", () => {
  test("every console screen redirects to the door rather than rendering", async ({ page }) => {
    // The command screen and one roadmap screen: the gate is in the shared
    // layout, so if it holds for these two it holds for all thirteen — and
    // asserting all thirteen would be asserting the layout thirteen times.
    for (const route of ["/console", "/console/review", "/console/policy"]) {
      await page.goto(route);
      await expect(page).toHaveURL(/\/sign-in$/);
      // Not merely redirected — the console's own root must not be in the
      // document. A redirect that still streamed the shell would have leaked
      // the queue before the browser followed it.
      await expect(page.locator(".console")).toHaveCount(0);
    }
  });

  test("the door itself renders, and names what the passphrase is not", async ({ page }) => {
    await page.goto("/sign-in");

    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expect(page.locator("#passphrase")).toBeVisible();
    // §E3.3: an officer must not be left assuming their decisions are
    // attributed to them, because until Phase 13 they are not.
    await expect(page.getByText(/one passphrase, shared by the team/i)).toBeVisible();
  });

  for (const { method, path } of PRIVILEGED_WRITES) {
    test(`${method} ${path} is refused`, async ({ request }) => {
      const response = await request.fetch(path, {
        method,
        // A well-formed body, so a 403 cannot be a validation error wearing
        // the wrong status. The point is that it never reaches validation.
        data: { decision: "approve", rationale: "test", publish: true, key: "x" },
        failOnStatusCode: false,
      });

      expect(response.status()).toBe(403);
      const body: unknown = await response.json();
      expect(body).toMatchObject({ status: 403 });
    });
  }

  test("a wrong passphrase is refused, and a right one is accepted", async ({ page }) => {
    const refused = await page.request.post("/api/session", {
      data: { passphrase: "not the passphrase" },
      failOnStatusCode: false,
    });
    expect(refused.status()).toBe(401);

    // Still refused afterwards — a failed attempt must not leave a session.
    await page.goto("/console");
    await expect(page).toHaveURL(/\/sign-in$/);

    await signInAsOperator(page);
    await page.goto("/console");
    await expect(page.locator(".console")).toBeVisible();
  });

  test("signing out closes the console again", async ({ page }) => {
    await signInAsOperator(page);
    await page.goto("/console");
    await expect(page.locator(".console")).toBeVisible();

    const out = await page.request.delete("/api/session");
    expect(out.ok()).toBe(true);

    await page.goto("/console");
    await expect(page).toHaveURL(/\/sign-in$/);
  });
});
