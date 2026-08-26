import { expect, type Page } from "@playwright/test";

import { test } from "./fixtures/origin.ts";
import {
  TIERS,
  capabilitiesFor,
  pressQualityFor,
  rendersClay,
  type Tier,
} from "../src/clay/tier.ts";

/**
 * §E13's ladder, proved rung by rung — F16's third gate clause, M10.6.
 *
 * > Every tier S/A/B/C/D produces **its documented rendering** when its trigger
 * > is forced.
 *
 * **What this file adds to what already existed.** `tests/clay.spec.ts` already
 * forces every tier and asserts the *accessible list* is a peer in all five —
 * which is §E22's claim. `tests/story.spec.ts` forces Tier C and Tier D on the
 * film. Neither asserts the thing §E13's table actually promises, which is the
 * **renders** column: three inks or two, a lens stack or none, a canvas or a
 * print. That column is what a reviewer reads the table for, and until F16 it
 * was the one part of it nothing checked.
 *
 * **The assertion is derived, not transcribed.** Every expectation below comes
 * from `capabilitiesFor()` and `pressQualityFor()` — the same functions the
 * scene builds itself from — and the DOM publishes what the scene actually did
 * (`data-clay-press`, `data-clay-effects`). So this is not a test that repeats
 * the table back to itself: it asserts that the *rendering* agrees with the
 * *policy*, on a real device, for a forced trigger. A scene that quietly
 * stopped building its bloom node would fail here even though the policy
 * function still said it should have one.
 *
 * **S and A are one forced trigger, not two** (ADR-0037). Forcing either is the
 * same instruction to the device — take the GPU path — and which of the two you
 * land on is the renderer's backend selection rather than ours. So the two rows
 * are asserted as *one of S or A*, and which one is reported rather than
 * demanded: a machine with no WebGPU adapter cannot be made to produce Tier S
 * by a query parameter, and a test that insisted would be a test that only
 * passes on the author's laptop.
 */
test.use({
  // Headless Chromium reports `prefers-reduced-motion: reduce` by default,
  // which silently pins every case to Tier C — the defect `tests/clay.spec.ts`
  // found, and the one that would make this whole file vacuous.
  contextOptions: { reducedMotion: "no-preference" },
});

const SCENE_TIMEOUT_MS = 180_000;

test.beforeEach(() => {
  test.setTimeout(SCENE_TIMEOUT_MS);
});

const PROOF = "/developers/proof/clay";
const SAMPLE_PINS = 24;

function proofFor(tier: Tier): string {
  return `${PROOF}?pins=${String(SAMPLE_PINS)}&tier=${tier}`;
}

/** Wait until the surface has stopped saying "pending" and named a tier. */
async function settled(page: Page): Promise<string> {
  const clay = page.locator(".clay");
  await expect(clay).toHaveAttribute("data-tier", /^[SABCD]$/, { timeout: SCENE_TIMEOUT_MS });
  return (await clay.getAttribute("data-tier")) ?? "";
}

test.describe("§E13 — every rung renders what the table says it renders", () => {
  for (const tier of TIERS) {
    // Tier D is "no scripting", and forcing it with a query parameter is a
    // contradiction — the parameter is read by the script that Tier D does not
    // run. Its own case is below, with scripting genuinely switched off.
    if (tier === "D") continue;

    test(`tier ${tier}: the press, the effects and the canvas match §E13`, async ({ page }) => {
      await page.goto(proofFor(tier));
      const reported = await settled(page);

      // See the module note: S and A are one instruction to the device.
      if (tier === "S" || tier === "A") {
        expect(["S", "A"], `forcing ${tier} landed on ${reported}`).toContain(reported);
      } else {
        expect(reported).toBe(tier);
      }

      const actual = reported as Tier;
      const clay = page.locator(".clay");

      // §E6.4's dial: three inks at the top, two at Tier B, one flat below.
      await expect(clay, "press quality").toHaveAttribute(
        "data-clay-press",
        pressQualityFor(actual),
      );

      // §E13's effects, as the scene actually built them.
      const capabilities = capabilitiesFor(actual);
      const effects = new Set(((await clay.getAttribute("data-clay-effects")) ?? "").split(" "));
      expect(effects.has("bloom"), "bloom").toBe(capabilities.bloom);
      expect(effects.has("dof"), "depth of field").toBe(capabilities.depthOfField);
      expect(effects.has("weave"), "gate weave").toBe(capabilities.gateWeave);
      expect(effects.has("sun"), "moving sun").toBe(capabilities.movingSun);

      // And the thing a reader sees first: is there a canvas at all?
      await expect(page.locator(".clay__canvas")).toHaveCount(rendersClay(actual) ? 1 : 0);
      // §E13's Tier C is *"nine art-directed riso prints"* on the film and the
      // 2D map elsewhere — either way, a picture rather than an empty box.
      if (!rendersClay(actual)) {
        await expect(page.locator('[data-clay-path="flat"]')).toHaveCount(1);
      }
    });
  }

  test("tier D: no scripting, and every place is still on the page", async ({ browser }) => {
    // The one trigger that cannot be forced by a parameter, forced properly.
    const context = await browser.newContext({ javaScriptEnabled: false });
    const page = await context.newPage();
    await page.goto(`${PROOF}?pins=${String(SAMPLE_PINS)}`);

    await expect(page.locator(".clay__canvas")).toHaveCount(0);
    await expect(page.locator(".clay-peers li")).toHaveCount(SAMPLE_PINS);
    await context.close();
  });

  test("tier C is reached by consent as well as by a parameter", async ({ page }) => {
    // §E13's C is a *consent* rung, and the difference between "forced in CI"
    // and "actually triggered" is the difference between testing the parameter
    // and testing the ladder. This is the real trigger.
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto(`${PROOF}?pins=${String(SAMPLE_PINS)}`);

    expect(await settled(page)).toBe("C");
    await expect(page.locator(".clay")).toHaveAttribute("data-clay-press", pressQualityFor("C"));
    await expect(page.locator(".clay__canvas")).toHaveCount(0);
  });

  test("the sound layer is silent on a reduced-motion visit, and says why", async ({ page }) => {
    // §E12's clause, on a real surface: the control is *rendered* rather than
    // hidden (§E3.2), and it states the preference it is respecting. What the
    // preference actually silences is asserted as arithmetic in
    // `tests/sound.test.ts`; this asserts the person is told.
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/console");

    const control = page.locator(".sound");
    if ((await control.count()) === 0) {
      test.skip(true, "this deployment renders no console shell here");
    }
    await expect(control.locator(".sound__toggle")).toHaveAttribute("aria-pressed", "false");
    await expect(control.locator(".sound__note")).toBeVisible();
  });

  test("the unmute is designed rather than hidden, and persists", async ({ page }) => {
    await page.goto("/console");
    const toggle = page.locator(".sound__toggle");
    if ((await toggle.count()) === 0) {
      test.skip(true, "this deployment renders no console shell here");
    }

    // Muted by default — §E12, correcting §E2 defect #9.
    await expect(toggle).toHaveAttribute("aria-pressed", "false");
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-pressed", "true");

    // "State persists per user."
    await page.reload();
    await expect(page.locator(".sound__toggle")).toHaveAttribute("aria-pressed", "true", {
      timeout: 30_000,
    });
  });
});
