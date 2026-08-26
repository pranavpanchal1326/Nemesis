import AxeBuilder from "@axe-core/playwright";
import { expect, type Page } from "@playwright/test";

import { test } from "./fixtures/origin.ts";

/**
 * F3's gate — §E19, §E22, §E9.3, §E9.4.
 *
 * > * Full keyboard path with no mouse, including a screen-reader pass over the
 * >   palette.
 * > * The print stylesheet produces a usable A4 document **in grayscale**, with
 * >   severity legible by shape (§E9.4 rule 2).
 * > * Lighthouse ≥ 90 on the department route (completes A14).
 * > * `axe` clean across three densities × two scripts.
 *
 * The Lighthouse clause is in `lighthouserc.json`, where the other two routes
 * are, because a budget asserted from a Playwright test would be a second
 * budget somebody has to keep in step with the first. Everything else is here.
 *
 * **Every test drives the keyboard and never the mouse**, except the ones about
 * the pointer. §E22's claim is not "the console can be used with a keyboard" —
 * it is that the whole path works with no mouse at all, and the only way to
 * assert that honestly is to write the tests that way.
 *
 * The console reads a live control plane, and every screen renders its own
 * unavailable state when it cannot. So these tests do **not** skip when the
 * stack is down: the chrome, the palette, the keyboard model and the print
 * stylesheet are all properties of the frontend, and asserting them against a
 * console with an empty queue is asserting exactly the right thing.
 */

const CONSOLE = "/console";

/** The `⌘K` / `Ctrl-K` chord, on whatever this runner is. */
const PALETTE_CHORD = process.platform === "darwin" ? "Meta+k" : "Control+k";

async function openConsole(page: Page): Promise<void> {
  await page.goto(CONSOLE);
  await expect(page.locator(".console")).toBeVisible();
}

/**
 * The open palette, as a scope.
 *
 * Every role query about the palette goes through this. The console's screens
 * carry listboxes and options of their own — the review queue on the very
 * screen `⌘K` opens over — and a page-wide `getByRole("option")` asks a
 * question about the whole document while claiming to ask one about the
 * palette.
 */
function palette(page: Page) {
  return page.locator("dialog.palette");
}

// ---------------------------------------------------------------------------
// The shell
// ---------------------------------------------------------------------------

test.describe("§E19 — the light table", () => {
  test("the ground is the room, not the paper", async ({ page }) => {
    await openConsole(page);

    // §E9.3, and the blueprint's own correction: the page ground is mitti-950
    // "because that is the room, not the paper", and the ink glows on it. The
    // assertion is on the *resolved* colours rather than on a class name,
    // because the thing that broke in the earlier draft was the resolution.
    const surface = page.locator('[data-surface="console"]');
    await expect(surface).toHaveCount(1);

    const contrast = await page.evaluate(() => {
      const node = document.querySelector(".console");
      if (node === null) return null;
      const style = getComputedStyle(node);
      return { background: style.backgroundColor, colour: style.color };
    });
    expect(contrast).not.toBeNull();
    // Distinct, and not defaulted: a transparent console would mean the role
    // tokens never resolved, which is how the light table became unreadable
    // the first time somebody built it.
    expect(contrast?.background).not.toBe("rgba(0, 0, 0, 0)");
    expect(contrast?.background).not.toBe(contrast?.colour);
  });

  test("the rail names every screen, and says which are not wired", async ({ page }) => {
    await openConsole(page);
    const rail = page.getByRole("navigation", { name: /console sections/i });

    // The command view and the four REAL screens.
    for (const label of [/^Command$/, /Review queue/, /Policy studio/, /Control plane/]) {
      await expect(rail.getByRole("link", { name: label })).toBeVisible();
    }

    // §E24: the roadmap screens are *in* the rail with their chip, not hidden.
    // Hiding them would make the console look finished, which is the §E3.3
    // failure this product is written against.
    await expect(rail.getByRole("link", { name: /Area view/ })).toBeVisible();
    await expect(rail.getByText("not wired").first()).toBeVisible();
  });

  test("the skip link is real and lands in the screen", async ({ page }) => {
    await openConsole(page);
    await page.keyboard.press("Tab");

    const skip = page.locator(".console__skip");
    await expect(skip).toBeFocused();
    await page.keyboard.press("Enter");

    // The document moved, not only the scroll position: the next Tab has to
    // land inside the screen rather than back at the top of a twelve-item rail.
    await expect(page.locator("#console-screen")).toBeVisible();
    expect(await page.evaluate(() => window.location.hash)).toBe("#console-screen");
  });
});

// ---------------------------------------------------------------------------
// The keyboard — §E22
// ---------------------------------------------------------------------------

test.describe("§E22 — the full keyboard path, with no mouse", () => {
  test("⌘K opens the palette, Escape closes it", async ({ page }) => {
    await openConsole(page);

    await page.keyboard.press(PALETTE_CHORD);
    const palette = page.locator("dialog.palette");
    await expect(palette).toBeVisible();

    // A native `<dialog>` opened with `showModal()`: focus is trapped and the
    // rest of the page is inert, which is what makes the screen-reader pass
    // below meaningful rather than aspirational.
    await expect(page.locator(".palette__field")).toBeFocused();
    await expect(palette).toHaveJSProperty("open", true);

    await page.keyboard.press("Escape");
    await expect(palette).not.toBeVisible();
  });

  test("the palette filters and navigates on Enter", async ({ page }) => {
    await openConsole(page);
    await page.keyboard.press(PALETTE_CHORD);
    await page.keyboard.type("policy");

    // Substring, not fuzzy: an officer who has learned `⌘K poli ⏎` must get the
    // same screen every time.
    //
    // Scoped to the dialog. `showModal()` makes the rest of the document inert,
    // so a screen reader meets exactly one listbox here — but Playwright's role
    // engine does not model the top layer, and the command view behind the
    // palette is the review queue, whose rows are also options. Counting them
    // would be counting the wrong thing, not a stricter version of this claim.
    const options = palette(page).getByRole("option");
    await expect(options).toHaveCount(1);

    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/console\/policy/);
  });

  test("arrow keys move the palette selection without moving focus", async ({ page }) => {
    await openConsole(page);
    await page.keyboard.press(PALETTE_CHORD);

    const field = page.locator(".palette__field");
    const first = await field.getAttribute("aria-activedescendant");
    await page.keyboard.press("ArrowDown");
    const second = await field.getAttribute("aria-activedescendant");

    // `aria-activedescendant` rather than focus, so the caret stays in the
    // field while the highlight moves — which is what lets somebody keep typing.
    expect(second).not.toBe(first);
    await expect(field).toBeFocused();
  });

  test("the palette announces itself as a dialog with a combobox and a listbox", async ({
    page,
  }) => {
    await openConsole(page);
    await page.keyboard.press(PALETTE_CHORD);

    // The screen-reader pass F3 asks for, expressed as the roles a screen
    // reader would actually meet. A `<ul role="listbox">` fails `axe` here,
    // which is why the results are a `<div>`.
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole("combobox")).toBeFocused();
    await expect(dialog.getByRole("listbox")).toBeVisible();
    await expect(dialog.getByRole("option").first()).toBeVisible();

    // The inertness itself, asserted rather than assumed — this is the half of
    // the claim the scoping above gives up, and it is the half that matters.
    // The queue behind the palette must be unreachable, or the "no mouse" path
    // has a hole in it exactly where the focus trap was supposed to be.
    expect(
      await page.evaluate(() => {
        const row = document.querySelector<HTMLElement>(".review__row");
        if (row === null) return "absent";
        row.focus();
        return document.activeElement === row ? "reachable" : "trapped";
      }),
    ).not.toBe("reachable");

    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
      .analyze();
    const summary = results.violations.map(
      (violation) => `${violation.id} (${String(violation.nodes.length)}): ${violation.help}`,
    );
    expect(summary, summary.join("\n")).toEqual([]);
  });

  test("? opens the palette on the shortcut list", async ({ page }) => {
    await openConsole(page);
    await page.keyboard.press("?");

    // The help list is `SHORTCUTS`, which is also what the key model resolves
    // against — so a shortcut that exists and is undocumented is not possible.
    await expect(page.locator(".palette__keys")).toBeVisible();
    await expect(page.locator(".palette__key")).toHaveCount(6);
  });

  test("a single letter is a letter inside a text field", async ({ page }) => {
    await openConsole(page);
    await page.keyboard.press(PALETTE_CHORD);
    await page.keyboard.type("jke");

    // `j`, `k` and `e` are shortcuts everywhere except in a field somebody is
    // typing into. One listener that forgets this is a search box that cannot
    // spell "Kajgaon".
    await expect(page.locator(".palette__field")).toHaveValue("jke");
  });
});

// ---------------------------------------------------------------------------
// Print — §E19, §E9.4 rule 2
// ---------------------------------------------------------------------------

test.describe("§E19 — print is a first-class target", () => {
  test("the chrome goes and the provenance arrives", async ({ page }) => {
    await openConsole(page);
    await page.emulateMedia({ media: "print" });

    // An affordance on paper is furniture that cost a tree. What replaces the
    // rail is provenance: which city, which screen, how old the figures are.
    await expect(page.locator(".console__rail")).toBeHidden();
    await expect(page.locator(".console__density")).toBeHidden();
    await expect(page.locator(".console__palette-open")).toBeHidden();
    await expect(page.locator(".console__provenance")).toBeVisible();
    await expect(page.locator(".console__provenance")).toContainText(/page as at/i);
  });

  test("the printed page is ink on paper, not a dark screen", async ({ page }) => {
    await openConsole(page);
    await page.emulateMedia({ media: "print" });

    // A mitti-950 ground would empty a toner cartridge and arrive as a grey
    // rectangle. Asserted by measuring the printed luminance rather than by
    // checking a class: the failure mode is a rule that did not apply.
    const luminance = await page.evaluate(() => {
      const node = document.querySelector(".console");
      if (node === null) return null;
      const [r = 0, g = 0, b = 0] = (
        getComputedStyle(node).backgroundColor.match(/\d+/g) ?? []
      ).map(Number);
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    });
    expect(luminance).not.toBeNull();
    expect(luminance ?? 0).toBeGreaterThan(200);
  });

  test("severity survives grayscale, because it is a shape", async ({ page }) => {
    // §E9.4 rule 2, and the measurement that makes it load-bearing rather than
    // belt-and-braces: `high` and `medium` sit 1.4% apart in grayscale. The
    // contract matrix is where every severity renders, so it is where the claim
    // is checked — under a print emulation and a grayscale filter at once.
    await page.goto("/developers/proof/contracts");
    await page.emulateMedia({ media: "print" });
    await page.addStyleTag({ content: "html { filter: grayscale(1) !important; }" });

    const marks = page.locator("svg.severity-mark");
    expect(await marks.count()).toBeGreaterThan(0);

    // Distinct geometry, not distinct colour: the `d`/`r`/`fill` of each mark
    // differs, which is the channel a photocopier keeps.
    const shapes = await marks.evaluateAll((nodes) =>
      nodes.map((node) => node.innerHTML.replace(/\s+/g, "")),
    );
    expect(new Set(shapes).size).toBeGreaterThan(1);
  });
});

// ---------------------------------------------------------------------------
// axe — three densities × two scripts
// ---------------------------------------------------------------------------

const DENSITIES = ["comfortable", "compact", "dense"] as const;
const SCRIPTS = [
  { locale: "en", label: "Latin" },
  { locale: "mr", label: "Devanagari" },
] as const;

test.describe("§E22 — axe across three densities and two scripts", () => {
  for (const density of DENSITIES) {
    for (const script of SCRIPTS) {
      test(`${density} · ${script.label}`, async ({ browser }) => {
        /*
         * A fresh context per combination, with the locale set on the context
         * rather than as a header.
         *
         * `setExtraHTTPHeaders({ "Accept-Language": "mr" })` on a reused page
         * does reach the server — the same request over `curl` renders
         * `lang="mr"` — and the browser still showed English, because the
         * document for `/console` was already in the context cache from an
         * earlier navigation in the same worker. A locale test that silently
         * reads a cached document is a locale test that passes in one language.
         *
         * `newContext({ locale })` sets `Accept-Language` *and*
         * `navigator.language`, and starts with an empty cache — which is the
         * difference between asserting negotiation and asserting a cache hit.
         */
        const context = await browser.newContext({ locale: script.locale });
        await context.addInitScript((mode) => {
          localStorage.setItem("nemesis.density", mode);
        }, density);
        const page = await context.newPage();

        try {
          await openConsole(page);

          await expect(page.locator("html")).toHaveAttribute("data-density", density);
          await expect(page.locator('[data-surface="console"]')).toHaveAttribute(
            "lang",
            script.locale,
          );

          const results = await new AxeBuilder({ page })
            .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
            .analyze();
          const summary = results.violations.flatMap((violation) =>
            violation.nodes.map(
              (node) => `${violation.id}: ${node.target.join(" ")} — ${node.failureSummary ?? ""}`,
            ),
          );
          expect(summary, summary.join("\n")).toEqual([]);
        } finally {
          await context.close();
        }
      });
    }
  }
});
