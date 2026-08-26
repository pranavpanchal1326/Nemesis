import { join } from "node:path";
import { fileURLToPath } from "node:url";

import AxeBuilder from "@axe-core/playwright";
import { expect, type Page } from "@playwright/test";

import { test } from "./fixtures/origin.ts";

/**
 * F15's gate — §E8, §E8.1, ADR-0041, ADR-0048.
 *
 * > **Gate:** a real `citizen_confirmed` event moves a character, asserted E2E.
 * > Not one timeline exists in the source — the audit is a grep, because
 * > §E8.1's whole claim is that these are inputs rather than playback.
 *
 * **The grep half is `scripts/check-guards.ts`** (`no-character-timeline`), and
 * it runs in `npm run check` before this file does. The unit half — the input
 * names, the transition table, the binding table — is
 * `tests/ink-machine.test.ts`. What is left, and what can only be asserted in a
 * browser, is here: that a figure on a real surface moves because the pipeline
 * said something, and not because a test clicked.
 *
 * **One clause of this gate is unexercised on this checkout, and it skips by
 * name rather than passing quietly.** Nothing in this system appends
 * `citizen_confirmed`: the event is registered, projected and shaped for the
 * wire, and the door that would write it — §E17.5's *close the loop* — is
 * Phase 15 on both sides and is unbuilt. Publishing a synthetic envelope from
 * this file would pass the gate by violating it, in the exact words the Phase
 * 20 gate uses: *a scene that can only be fired by a button fails.* The reasoning,
 * what it is not, and the three routes that would take it are in
 * `docs/reports/character-relief-gate.md`.
 *
 * **So the substance is asserted with the events this deployment does emit.**
 * `exif_check_completed` fires `shutter` and `pipeline_stage_degraded` fires
 * `disappointed` — both are in the same table, arrive on the same socket
 * through the same binding, and both move the same figure. What the skipped
 * clause leaves untested is one row of that table, not the mechanism.
 */
test.use({
  // Headless Chromium reports `prefers-reduced-motion: reduce` by default,
  // which would run every case below on the storyboard rung — where there is no
  // film and therefore no figure. Found by `tests/clay.spec.ts`.
  contextOptions: { reducedMotion: "no-preference" },
  permissions: ["geolocation"],
  geolocation: { latitude: 18.5074, longitude: 73.8077 },
});

const SCENE_TIMEOUT_MS = 180_000;

test.beforeEach(() => {
  test.setTimeout(SCENE_TIMEOUT_MS);
});

const CITY = "pune-demo";
const PROOF = `/developers/proof/story?tenant=${CITY}&seed=1`;
const LANDING = "/";

const PHOTO = join(
  fileURLToPath(new URL(".", import.meta.url)),
  "fixtures",
  "media",
  "pothole.jpg",
);

const FIGURE = '[data-ink-figure="reporter"]';

/**
 * Can this deployment produce a `citizen_confirmed` event at all?
 *
 * A constant rather than a probe, because there is nothing to probe. The event
 * is registered in `nemesis/events/catalog.py`, projected in
 * `projections/handlers.py` and shaped for the wire in `realtime/envelope.py` —
 * and **no route, worker, CLI task or seeder in this repository appends it.**
 * §E17.5's before/after slider is the door, and it is ROADMAP (Phase 15) on
 * both sides.
 *
 * Written as a named boolean so the day that door lands, this file changes by
 * one line and the case below runs.
 */
const CAN_CONFIRM_A_CLOSURE = false;

/** The same question `tests/story.spec.ts` asks, and for the same reason: only
 *  a published city puts `<Walk>` — and therefore a figure — in the DOM. */
async function cityIsPublished(page: Page): Promise<boolean> {
  const response = await page.goto(`${PROOF}&t=0`);
  if (response === null) return false;
  return (await page.locator('[data-story="walk"]').count()) > 0;
}

async function settle(page: Page): Promise<void> {
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(400);
}

// --------------------------------------------------------------------------
// The figure exists, and the acts declare it rather than playing it
// --------------------------------------------------------------------------

test.describe("F15 — the Reporter on the film", () => {
  test("stands in the state each act declares, at a pinned point on the spine", async ({
    page,
  }) => {
    test.skip(!(await cityIsPublished(page)), "no published city to film");

    // Every one of these is a **fresh page load at a position**, never a scroll
    // through the film. That is the hard version of the assertion and it is the
    // version that matters: a reader deep-links to Act 4, or reloads mid-scroll,
    // and must see the figure that act describes rather than the one the top of
    // the page starts with.
    //
    // **This case found a defect.** The first implementation declared each act's
    // inputs and nothing else, so a machine loaded straight into Act 2 sat in
    // `idle` — correctly, per §E8.1's chain, and wrongly for the reader.
    // `StoryFigure.catchUp` is the fix, and it replays declarations rather than
    // seeking a state, which is the distinction ADR-0041 exists to protect.
    const positions: readonly (readonly [number, string])[] = [
      [0.12, "walk"],
      [0.27, "observe"],
      [0.36, "dejected"],
      [0.48, "report"],
    ];

    for (const [t, expected] of positions) {
      await page.goto(`${PROOF}&t=${String(t)}`);
      await settle(page);
      await expect(page.locator(FIGURE), `t=${String(t)}`).toHaveAttribute(
        "data-ink-state",
        expected,
        { timeout: 30_000 },
      );
    }
  });

  test("leaves the stage once the film pulls back to the city", async ({ page }) => {
    test.skip(!(await cityIsPublished(page)), "no published city to film");
    await page.goto(`${PROOF}&t=0.88`);
    await settle(page);
    // §E16 Acts 7–9 are the city, the survey frame and the workbench. A
    // road-level ink figure in those shots would be a person the size of a ward.
    await expect(page.locator(FIGURE)).toHaveCount(0);
  });

  test("describes itself in words that change with its state", async ({ page }) => {
    test.skip(!(await cityIsPublished(page)), "no published city to film");

    await page.goto(`${PROOF}&t=0.12`);
    await settle(page);
    const walking = await page.locator(FIGURE).getAttribute("aria-label");

    await page.goto(`${PROOF}&t=0.36`);
    await settle(page);
    const dropped = await page.locator(FIGURE).getAttribute("aria-label");

    // §E22 makes the accessible peer a peer. A single unchanging label would be
    // a decoration wearing a description.
    expect(walking).toBeTruthy();
    expect(dropped).toBeTruthy();
    expect(walking).not.toBe(dropped);
  });

  test("is clean to axe on the surface it appears on", async ({ page }) => {
    test.skip(!(await cityIsPublished(page)), "no published city to film");
    await page.goto(`${PROOF}&t=0.27`);
    await settle(page);
    const results = await new AxeBuilder({ page }).include('[data-story="walk"]').analyze();
    expect(results.violations).toEqual([]);
  });
});

// --------------------------------------------------------------------------
// The gate: a real backend event moves the figure
// --------------------------------------------------------------------------

test.describe("F15 — the gate", () => {
  test("a real pipeline event moves the figure, with nobody touching it", async ({ page }) => {
    test.skip(!(await cityIsPublished(page)), "no published city to film");

    await page.goto(LANDING);
    await settle(page);
    const film = page.locator('[data-story="walk"]');
    test.skip((await film.count()) === 0, "this deployment renders the storyboard");

    // Scroll to Act 4 and file a report **through the film's own capture
    // component** — the same path `tests/story.spec.ts` uses, because it is the
    // citizen loop's own `<ReportFlow>` and not a picture of one.
    await page.mouse.wheel(0, 5200);
    await page.waitForTimeout(1200);

    const picker = page.locator('input[type="file"]');
    await expect(picker).toBeAttached({ timeout: 30_000 });
    await picker.setInputFiles(PHOTO);
    await expect(page.getByRole("button", { name: /undo/i })).toBeVisible({ timeout: 20_000 });

    const send = page.getByRole("button", { name: /^send$/i });
    await send.click({ timeout: 60_000 });
    const place = page.getByRole("button", { name: /^send$/i });
    await expect(place).toBeEnabled({ timeout: 60_000 });
    await place.click({ timeout: 60_000 });
    await expect(page.getByRole("heading", { name: /filed/i })).toBeVisible({ timeout: 30_000 });

    const figure = page.locator(FIGURE);
    const before = Number((await figure.getAttribute("data-ink-transitions")) ?? "0");

    // From here nothing in this test touches the page. The only thing that can
    // move the figure is an envelope arriving on the socket — `exif_check_completed`
    // for the report just filed, which fires `shutter`, or `pipeline_stage_degraded`
    // if §24.2's third outcome parks it, which fires `disappointed`.
    await expect
      .poll(async () => (await figure.getAttribute("data-ink-state")) ?? "", {
        timeout: 90_000,
        message:
          "no shaped event bound to a character input arrived for this report — " +
          "the pipeline is not running, or nothing it published moves a figure",
      })
      .toMatch(/^(?:wait|dejected)$/);

    const after = Number((await figure.getAttribute("data-ink-transitions")) ?? "0");
    // The count, not the state name: *moved to* rather than *rendered as*.
    expect(after).toBeGreaterThan(before);
  });

  test("a real citizen_confirmed event fires relief", async ({ page }) => {
    test.skip(
      !CAN_CONFIRM_A_CLOSURE,
      "nothing in this system appends citizen_confirmed — §E17.5's close-the-loop " +
        "door is Phase 15 and unbuilt on both sides, so no real event exists to " +
        "fire relief (docs/reports/character-relief-gate.md)",
    );

    // Unreachable today, and written out rather than left as a comment: this is
    // the assertion the day the door exists, and it is three lines.
    await page.goto(LANDING);
    const figure = page.locator(FIGURE);
    await expect(figure).toHaveAttribute("data-ink-state", "confirmed", { timeout: 90_000 });
  });
});
