import AxeBuilder from "@axe-core/playwright";
import { expect, type Page } from "@playwright/test";

import { test } from "./fixtures/origin.ts";

/**
 * F7's gate — the nine console screens with no backend behind them yet.
 *
 * > * **No ROADMAP screen is reachable from a public URL** — a route test, not
 * >   a convention.
 * > * Every screen compiles against generated types; a hand-written interface
 * >   describing a backend contract fails `check-guards`.
 * > * The closure screen renders `Resolved` **disabled with its unmet
 * >   conditions attached**, and no client-side check is ever the control
 * >   (§E19.4).
 * > * The blacklist action renders *"3 of 5 requirements met"* rather than
 * >   hiding itself (§E19.6).
 *
 * **Two of those four are asserted elsewhere, and deliberately so.** The route
 * guard is a property of the source — `tests/console-screens.test.ts` reads
 * every `page.tsx` in the registry and asserts that exactly the roadmap ones
 * call `devOnly()`, which holds for every deployment rather than for the one a
 * browser happened to be pointed at. The generated-types rule is
 * `scripts/check-guards.ts`' fourth ban, enforced at build time. What is left
 * is what only a rendered page can answer, and that is this file.
 *
 * **Nothing here needs the stack**, and that is the point of the phase: a
 * roadmap screen renders generated *types* with fixture *values*, so what it
 * shows is a property of the frontend alone. A skip here would mean the screen
 * had quietly acquired a backend dependency it is not supposed to have.
 */

const SCREENS = {
  roles: "/console/roles",
  area: "/console/area",
  work: "/console/work",
  closure: "/console/closure",
  money: "/console/money",
  integrity: "/console/integrity",
  reports: "/console/reports",
} as const;

async function open(page: Page, href: string): Promise<void> {
  await page.goto(href);
  await expect(page.locator(".console")).toBeVisible();
}

// ---------------------------------------------------------------------------
// §E24 — a fixture screen says it is one, everywhere it can be seen
// ---------------------------------------------------------------------------

test.describe("§E24 — the chip is on the screen, not only in the rail", () => {
  for (const [id, href] of Object.entries(SCREENS)) {
    test(`${id} names the phase that will populate it`, async ({ page }) => {
      await open(page, href);

      // Three places, and all three have to agree: the heading's chip, the
      // fixture notice at the top of the screen, and the phase number itself.
      // §E24's failure mode is not a missing chip — it is a chip that says
      // "later" while the rail says "Phase 14", and a reader who believes
      // whichever they read first.
      const notice = page.locator(".fixture").first();
      await expect(notice).toBeVisible();

      const chip = page.locator(".console__heading .not-wired");
      await expect(chip).toBeVisible();

      // The phase is a number — sometimes two, for a screen that needs two
      // phases before it means anything — and it is read rather than pattern
      // matched, so a screen naming 12 in the heading and 14 in the notice
      // fails here instead of satisfying a looser regex.
      const phase = (await chip.locator(".not-wired__phase").innerText()).trim();
      expect(phase, `the heading chip on ${href} names no phase`).toMatch(/^\d+(,\s*\d+)*$/u);
      await expect(notice).toContainText(`Phase ${phase}`);
    });
  }

  test("a roadmap screen names what it cannot draw at all", async ({ page }) => {
    await open(page, SCREENS.work);

    // §E19's third kind of thing: a part of the section with no contract behind
    // it is a *named absence*, not an empty panel. An empty panel is
    // indistinguishable from a bug; a named absence is a work item.
    await expect(page.locator(".fixture__gap").first()).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// §E19.4 — closure
// ---------------------------------------------------------------------------

test.describe("§E19.4 — the rule is legible before it is hit", () => {
  test("Resolved is disabled with its unmet conditions attached", async ({ page }) => {
    await open(page, SCREENS.closure);

    const resolved = page.getByRole("button", { name: /mark resolved/i });
    await expect(resolved).toBeDisabled();

    // Attached, not in a tooltip. `aria-describedby` is what makes a
    // screen-reader user hear *why* at the moment they reach the control, and
    // it is asserted by following the reference rather than by checking that
    // the attribute exists — an id pointing at nothing is the failure this
    // catches.
    const described = await resolved.getAttribute("aria-describedby");
    expect(described, "the disabled control explains nothing").not.toBeNull();
    for (const id of (described ?? "").split(/\s+/u).filter((value) => value !== "")) {
      await expect(page.locator(`#${id}`)).toBeVisible();
    }

    // The count, and the unmet condition itself. A bare disabled control tells
    // an officer nothing about how far away they are.
    await expect(page.locator("#closure-count")).toContainText(/\d+ of \d+ conditions met/i);
    await expect(page.locator('.roadmap__condition[data-met="false"]')).not.toHaveCount(0);

    // And the screen says whose rule it is. §E19.4's warning is that a
    // client-side check mistaken for the control is how somebody eventually
    // ships a path around it.
    await expect(page.locator(".roadmap__why").first()).toContainText(/backend enforces/i);
  });

  test("an ambiguous verification is printed, not rounded into a verdict", async ({ page }) => {
    await open(page, SCREENS.closure);

    // §E19.4 asks for the SSIM result *"honestly, including when ambiguous"*.
    // A score that came back undecided is not a pass and not a failure, and
    // rounding it into either would be the product deciding something the
    // algorithm declined to.
    await expect(page.locator(".roadmap__condition-detail")).toContainText(/ambiguous/i);
  });

  test("met-ness survives grayscale, because it is a mark and not a colour", async ({ page }) => {
    await open(page, SCREENS.closure);

    // §E9.4 rule 2, on a screen that is printed: the difference between met and
    // unmet cannot be carried by colour alone, because on paper it is not
    // carried at all.
    const marks = await page.locator(".roadmap__condition-mark").allInnerTexts();
    expect(new Set(marks.map((mark) => mark.trim())).size).toBeGreaterThan(1);
  });
});

// ---------------------------------------------------------------------------
// §E19.6 — the blacklist counter
// ---------------------------------------------------------------------------

test.describe("§E19.6 — the action is shown, and its requirements with it", () => {
  test("the blacklist control counts its requirements rather than hiding", async ({ page }) => {
    await open(page, SCREENS.integrity);

    const blacklist = page.getByRole("button", { name: /^blacklist$/i });
    // Present and disabled. Hiding it would teach nobody the rule, which is the
    // §E19.6 argument in one sentence: *"blacklisting is never a button. It is
    // the outcome of a completed case."*
    await expect(blacklist).toBeVisible();
    await expect(blacklist).toBeDisabled();

    await expect(page.locator("#blacklist-count")).toContainText(
      /\d+ of \d+ requirements met/i,
      // The literal §E19.6 shape — "3 of 5" — rather than "not yet eligible".
    );
    await expect(page.locator("#blacklist-requirements .roadmap__condition")).toHaveCount(5);
    await expect(
      page.locator('#blacklist-requirements .roadmap__condition[data-met="false"]'),
    ).not.toHaveCount(0);
  });

  test("a signal names its detector, its threshold and its confidence", async ({ page }) => {
    await open(page, SCREENS.integrity);

    // *"A signal is not a finding."* The integrity room is the screen where an
    // unlabelled number does the most damage, so every card carries the three
    // facts that make it readable as a signal.
    const method = page.locator(".roadmap__method").first();
    await expect(method).toContainText(/detector/i);
    await expect(method).toContainText(/threshold/i);
    await expect(method).toContainText(/confidence/i);
  });
});

// ---------------------------------------------------------------------------
// The rest of F7's named ships
// ---------------------------------------------------------------------------

test.describe("F7 — the screens that were built to be read early", () => {
  test("the work order warns about concentration without blocking on it", async ({ page }) => {
    await open(page, SCREENS.work);

    // §15.3. The warning's whole purpose is that it *blocks nothing* and makes
    // the pattern impossible to not-know at the moment of the decision — so the
    // assertion is that both halves are on the screen.
    const warning = page.locator('[data-flag="concentration"]');
    await expect(warning).toBeVisible();
    await expect(warning).toContainText(/%/);
    await expect(
      page.locator(".roadmap__why").filter({ hasText: /blocks nothing/i }),
    ).toBeVisible();

    // And the rate-card variance, stated against the Schedule of Rates rather
    // than as a bare number.
    await expect(page.getByText(/over the schedule of rates/i)).toBeVisible();
  });

  test("the area view states under-reporting as a signal, with what to do about it", async ({
    page,
  }) => {
    await open(page, SCREENS.area);

    // §23.1 records reporting bias as a risk. Naming it on the ward's own
    // screen is what turns a documented concern into something an officer can
    // act on — so the suggestion is part of the assertion, not decoration.
    await expect(page.getByText(/under-?reporting/i).first()).toBeVisible();
    await expect(page.getByText(/suggested:/i)).toBeVisible();
  });

  test("the money view offers the figures as a citizen would see them", async ({ page }) => {
    await open(page, SCREENS.money);

    // §E19's argument for the toggle: knowing that the internal number and the
    // public number are the same number is the whole point.
    await expect(page.getByText(/what citizens see/i).first()).toBeVisible();
  });

  test("the report builder's footer is the feature", async ({ page }) => {
    await open(page, SCREENS.reports);

    // §E19.7: *"a report that carries its own proof is a category difference
    // from a report that carries a logo."*
    await expect(page.getByText(/verification footer/i)).toBeVisible();
    await expect(page.getByText(/chain root/i)).toBeVisible();
  });

  test("every roadmap screen is axe-clean", async ({ page }) => {
    // The nine screens joined the console at once, and a keyboard trap or an
    // unlabelled control on a fixture screen is exactly as unusable as one on a
    // wired screen. `console.spec.ts` sweeps the shell across densities and
    // scripts; this sweeps the screens.
    for (const href of Object.values(SCREENS)) {
      await open(page, href);
      const results = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
        .analyze();
      const summary = results.violations.map(
        (violation) => `${href} — ${violation.id} (${String(violation.nodes.length)})`,
      );
      expect(summary, summary.join("\n")).toEqual([]);
    }
  });
});
