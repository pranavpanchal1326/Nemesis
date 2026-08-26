import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type Locator, type Page } from "@playwright/test";

/**
 * The golden images — A8, §E24, and the Phase 20 gate brought forward.
 *
 * > **No golden images.** `playwright.config.ts` sets `toHaveScreenshot` to zero
 * > tolerance and there is nothing to compare against. *Visual regression is
 * > configured, not running.*
 *
 * **Why now, before the clay.** Stages 2 and 3 are the most visual work in this
 * project and there is no visual baseline at all. Building the renderer first
 * and capturing baselines afterwards means the baselines encode whatever
 * shipped, including its bugs — a photograph of a regression rather than a net
 * that catches one. M0–M6 are green today, so today is when a baseline is worth
 * capturing.
 *
 * **What is baselined, and what is deliberately not.** Everything below is a
 * surface that exists. ADR-0038's second implementation — the TSL press — has
 * no surface to render into until `<ClayScene>` lands at F8, so the press
 * baselines here are the CSS implementation across every ink set and every
 * quality tier. That is a stated limitation rather than a silent one: F8 adds
 * the renderer's baselines to a harness that already works, which is the whole
 * argument for capturing these first.
 *
 * **Determinism.** Fixed seed, fixed viewport, `deviceScaleFactor: 1`,
 * animations disabled at capture, one worker. Volatile content on the citizen
 * surfaces — ids, chain hashes, coordinates, timestamps, every one of which is
 * set in the data face by §E10.2 — is masked rather than tolerated, because a
 * tolerance large enough to absorb a changing hash is large enough to absorb a
 * regression.
 *
 * **Baselines are captured on Linux, by `nem web-golden`,** in the same
 * container image CI runs. A PNG rendered by a Windows font stack and one
 * rendered by CI's differ in every antialiased pixel, and a gate that can only
 * pass on one machine is a gate somebody turns off.
 */

const PRESS = "/developers/proof/press";
const MATRIX = "/developers/proof/contracts";
const PUBLIC = "/developers/proof/public";
const REPORT = "/report";

/** §E10.2 puts every id, hash, coordinate and timestamp in the data face. */
const VOLATILE = ".type-mono-data";

/** From `src/design/tokens.json` — every ink set, and §E6.4's whole dial. */
const INK_SETS = [
  "story",
  "public",
  "citizen",
  "console-day",
  "console-night",
  "document",
] as const;
const QUALITIES = ["full", "reduced", "flat"] as const;

const DENSITIES = ["comfortable", "compact", "dense"] as const;
const GROUNDS = ["paper", "light-table"] as const;
const SCRIPTS = ["en", "mr"] as const;

/** The §E18 states the proof surface renders, named by its own attribute. */
const PUBLIC_CASES = [
  "withheld",
  "genuine-zero",
  "unmeasured",
  "populated",
  "figures",
  "flagged",
] as const;

/**
 * Wait for the page to be still.
 *
 * The press animates at 12 Hz (§E6.1 stage 3) and the plan is seeded, so the
 * frame is deterministic — but only once fonts have loaded and the first paint
 * has settled. `document.fonts.ready` is the assertion that matters: a
 * screenshot taken mid-swap captures the fallback face, which is a different
 * bug's evidence.
 */
async function settle(page: Page): Promise<void> {
  await page.waitForLoadState("networkidle");
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
  await page.waitForTimeout(250);
}

async function shoot(page: Page, target: Locator, name: string, mask: Locator[] = []) {
  await target.waitFor({ state: "visible" });
  await settle(page);
  await expect(target).toHaveScreenshot(name, mask.length > 0 ? { mask } : {});
}

/**
 * Pin the height of a live region before capturing the screen around it.
 *
 * **Masking hides content and does nothing about size**, and on the two citizen
 * screens that sit downstream of the pipeline that is the whole problem. The
 * ledger's rows arrive as the pipeline runs; the receipt grows by a line when
 * the chain head lands; a report the engine parks at `pending_classification`
 * renders a shorter surface than one it classified. Every one of those is
 * correct behaviour, and every one of them changes the capture's *dimensions* —
 * which Playwright reports as a total mismatch, indistinguishable in CI from a
 * design regression. Three separate runs of this file failed that way for three
 * different data-dependent reasons before this existed.
 *
 * Waiting does not fix it, because the variation is not a race: it is what the
 * pipeline decided about one photograph on one machine. So the live regions are
 * given a fixed block size and their content is masked, and what is baselined
 * is what this file set out to baseline — the screen's design around them. The
 * *rows themselves* are asserted event by event in `citizen.spec.ts`, and their
 * contrast in `press-contrast.spec.ts`, which is where a claim about content
 * belongs.
 *
 * A stub would have been the other way to get determinism, and §E1's rule is
 * why it is not taken: a screen rendered from a fixture is a baseline of the
 * fixture. This one is a real render of a real submission with two boxes held
 * to a stated size.
 *
 * **What is left is worth stating plainly, because it is not much.** On the
 * tracking screen the mask covers most of the surface, so what these two
 * baselines catch is the frame — heading type, the severity row, the gutters,
 * the rhythm between blocks — and not the ledger's own composition. That is a
 * real limitation of a golden image over a live surface rather than a defect in
 * this harness, and it is the reason the two files are not the only assertion
 * about these screens.
 */
async function pinLiveRegions(page: Page, selector: string, height: number): Promise<void> {
  await page.addStyleTag({
    content: `${selector} { block-size: ${String(height)}px; overflow: hidden; }`,
  });
}

// ---------------------------------------------------------------------------
// The press — §E6, ADR-0038
// ---------------------------------------------------------------------------

test.describe("§E6 — the press, every ink set at every quality tier", () => {
  for (const surface of INK_SETS) {
    for (const quality of QUALITIES) {
      test(`${surface} · ${quality}`, async ({ page }) => {
        await page.goto(`${PRESS}?surface=${surface}&quality=${quality}&seed=1`);
        await shoot(page, page.locator(".press-proof"), `press-${surface}-${quality}.png`);
      });
    }
  }

  test("the bypassed sheet is baselined too", async ({ page }) => {
    // The press *off* is a rendering this product ships (§E13 Tier D, and the
    // `bypass` path ADR-0038 requires). An unbaselined bypass is where a
    // regression hides, because nobody looks at the boring one.
    await page.goto(`${PRESS}?surface=citizen&quality=full&seed=1&bypass=1`);
    await shoot(page, page.locator(".press-proof"), "press-citizen-bypassed.png");
  });
});

// ---------------------------------------------------------------------------
// The contract matrix — §E24's twelve combinations
// ---------------------------------------------------------------------------

test.describe("§E24 — three densities × two grounds × two scripts", () => {
  for (const density of DENSITIES) {
    for (const ground of GROUNDS) {
      for (const locale of SCRIPTS) {
        test(`${density} · ${ground} · ${locale}`, async ({ page }) => {
          await page.goto(`${MATRIX}?density=${density}&ground=${ground}&locale=${locale}`);
          // The whole page: this surface's job is to show every component in
          // every vocabulary at once, so cropping it would baseline a sample.
          await settle(page);
          await expect(page).toHaveScreenshot(`matrix-${density}-${ground}-${locale}.png`, {
            fullPage: true,
          });
        });
      }
    }
  }
});

// ---------------------------------------------------------------------------
// The §E18 suppression states — ADR-0021
// ---------------------------------------------------------------------------

test.describe("§E18 — the four states a figure can be in, and the flagged frame", () => {
  for (const proofCase of PUBLIC_CASES) {
    test(proofCase, async ({ page }) => {
      await page.goto(PUBLIC);
      await shoot(
        page,
        page.locator(`[data-proof-case="${proofCase}"]`),
        `public-${proofCase}.png`,
      );
    });
  }

  test("the suppression states in Marathi", async ({ page }) => {
    // C7's whole point is that a Marathi page must carry a Marathi disclaimer.
    // A baseline of the English one only would let the English one back in.
    await page.goto(`${PUBLIC}?locale=mr`);
    await shoot(page, page.locator('[data-proof-case="withheld"]'), "public-withheld-mr.png");
  });
});

// ---------------------------------------------------------------------------
// The citizen flow — §E17.1
// ---------------------------------------------------------------------------

/**
 * Three of these four screens exist only downstream of a real submission, so
 * they need the stack and they skip loudly without it — the same call
 * `citizen.spec.ts` makes, for the same reason: a screen rendered from a
 * fixture is a baseline of the fixture.
 */
async function stackIsUp(page: Page): Promise<boolean> {
  try {
    const response = await page.request.get("/api/realtime");
    return response.ok();
  } catch {
    return false;
  }
}

const PHOTO = join(
  fileURLToPath(new URL(".", import.meta.url)),
  "fixtures",
  "media",
  "pothole.jpg",
);

/**
 * Walk the flow, exactly as `citizen.spec.ts` does.
 *
 * Deliberately the same sequence of real interactions rather than a shortcut:
 * §E17.1's screens are defined by the transitions between them, and a baseline
 * of a screen reached another way is a baseline of something the product does
 * not do.
 */
async function fileAReport(page: Page, description: string): Promise<void> {
  await page.goto(REPORT);
  await page.setInputFiles('input[type="file"]', PHOTO);
  await page.getByRole("textbox").fill(description);
  await page.getByRole("button", { name: /^send$/i }).click();
}

/** The pipeline's own budget, matching `citizen.spec.ts`: §26.1 estimates eight
 *  seconds and this laptop runs the models with Ollama alongside them. */
const PIPELINE_TIMEOUT_MS = 45_000;

test.describe("§E17.1 — the citizen's four screens", () => {
  // Larger than the wait inside it, for the reason `citizen.spec.ts` gives at
  // the same place: an inner budget the outer clock cannot reach is not a
  // budget, and it fails as the wrong error.
  test.describe.configure({ timeout: PIPELINE_TIMEOUT_MS + 45_000 });

  test.use({
    // The same coordinate `citizen.spec.ts` uses — Kothrud, inside a real ward
    // boundary in the tenant `nem seed-demo` provisions — so the Place card
    // resolves through the PostGIS query rather than through its empty branch.
    permissions: ["geolocation"],
    geolocation: { latitude: 18.5074, longitude: 73.8077 },
  });

  test("1 · the viewfinder", async ({ page }) => {
    // The only one of the four with no backend behind it, and therefore the
    // only one that baselines in CI as well as on a developer's machine.
    await page.goto(REPORT);
    await shoot(page, page.locator('[data-phase="capture"]'), "citizen-1-capture.png");
  });

  test("2 · place, stated", async ({ page }) => {
    test.skip(!(await stackIsUp(page)), "the stack is not running");

    await fileAReport(page, "Golden baseline — the place card.");
    const place = page.locator('[data-phase="place"]');
    await expect(place).toBeVisible();
    // Wait for the resolve to land: the card's own "locating" branch is a real
    // state and a legitimate render, but it is not the one this baselines.
    await expect(page.getByRole("button", { name: /^send$/i })).toBeEnabled({ timeout: 15_000 });

    await shoot(page, place, "citizen-2-place.png", [
      // The captured photograph is the citizen's own and the resolved
      // coordinates are the machine's. Neither is a design decision, and both
      // change between runs.
      page.locator(".viewfinder__frame, .place-card__pad"),
      page.locator(VOLATILE),
    ]);
  });

  test("3 · sent — the optimistic receipt", async ({ page }) => {
    test.skip(!(await stackIsUp(page)), "the stack is not running");

    await fileAReport(page, "Golden baseline — the receipt.");
    const send = page.getByRole("button", { name: /^send$/i });
    await expect(send).toBeEnabled({ timeout: 15_000 });
    await send.click();

    const sent = page.locator('[data-phase="sent"]');
    await expect(sent).toBeVisible();
    await expect(page.getByRole("link", { name: /follow what happens/i })).toBeVisible({
      timeout: 20_000,
    });
    // The chain hash lands after the optimistic receipt paints, and it adds a
    // line. Capturing before it arrives baselines a screen one row shorter than
    // the one a citizen reads — and, because a screenshot of a different size
    // is a total mismatch rather than a small one, it fails as 36% of pixels
    // and reads like a design regression. Waiting for the settled receipt is
    // what makes this a baseline of a state rather than of a moment.
    await expect(page.locator(".receipt__hash")).toBeVisible({ timeout: 20_000 });
    // The theatre is pinned as well as the receipt, and for the same reason one
    // step further out: a gate's *stamp text* is what the pipeline decided —
    // "classifier unavailable · parked for human review" wraps to two lines
    // where "categorised" does not — so the theatre's height is a reading of one
    // run rather than a property of the design. §24.2's third outcome is
    // correct behaviour and it must not be a failing baseline.
    await pinLiveRegions(page, ".receipt", 420);
    await pinLiveRegions(page, ".theatre", 360);
    await shoot(page, sent, "citizen-3-sent.png", [
      page.locator(VOLATILE),
      page.locator(".viewfinder__frame, .place-card__pad"),
      // The whole receipt, and the reason is a design decision rather than
      // flakiness: `<Receipt>` seeds its press from the complaint id
      // (`hashSeed(complaintId)`), so its registration and grain are different
      // ink on every document by construction. That is §E6.1's fixed-seed
      // reproducibility working — one document, one print — and it means no
      // stable baseline of a real receipt exists without a stable id. What is
      // baselined here is the screen the receipt sits in.
      page.locator(".receipt"),
      // And the theatre, for the reason the pin above gives: its stamps say
      // what the pipeline decided about one photograph, and §24.2's third
      // outcome — "classifier unavailable · parked for human review" — is a
      // correct reading that would fail a baseline taken on a run that
      // classified. The stamps themselves are asserted event by event in
      // `citizen.spec.ts`, which is where a claim about what the log said
      // belongs.
      page.locator(".theatre"),
    ]);
  });

  test("4 · the tracking ledger", async ({ page }) => {
    test.skip(!(await stackIsUp(page)), "the stack is not running");

    await fileAReport(page, "Golden baseline — the tracking screen.");
    const send = page.getByRole("button", { name: /^send$/i });
    await expect(send).toBeEnabled({ timeout: 15_000 });
    await send.click();

    const link = page.getByRole("link", { name: /follow what happens/i });
    await expect(link).toBeVisible({ timeout: 20_000 });
    await page.goto((await link.getAttribute("href")) ?? REPORT);

    /*
     * Wait for the theatre to finish before capturing.
     *
     * The gates resolve as the pipeline runs, and each one that resolves adds
     * height to the surface being shot. Masking the live regions hides their
     * *content* and does nothing about their *size*, so a capture taken while
     * the pipeline was still working produced a 573 px baseline and the next
     * run produced a 733 px one — a mismatch reported as a third of all pixels,
     * which is indistinguishable from a real regression by anyone reading CI.
     *
     * The gates themselves are still asserted event by event in
     * `citizen.spec.ts`; this waits for the same condition only so that what is
     * baselined is one determinate state of the screen. Masking stays: the row
     * text, the relative times and the id-seeded receipt are volatile even once
     * everything has landed.
     */
    const ledger = page.locator(".track").first();
    await expect(ledger).toBeVisible();
    await expect(page.locator('.theatre__gate[data-state="waiting"]')).toHaveCount(0, {
      timeout: PIPELINE_TIMEOUT_MS,
    });
    await pinLiveRegions(page, ".track__status, .track__ledger, .track__receipt", 260);
    await shoot(page, ledger, "citizen-4-track.png", [
      page.locator(VOLATILE),
      page.locator(".viewfinder__frame, .place-card__pad, img"),
      // The ledger is a live surface: its rows arrive as the pipeline runs, its
      // relative times tick, and its receipt is seeded from the complaint id.
      // Masking those three baselines the screen's *design* rather than a
      // moment in one pipeline run — which is the split this file's header
      // describes, and the reason `citizen.spec.ts` remains the place where the
      // gates are asserted event by event.
      page.locator(".track__status, .track__ledger, .track__receipt"),
    ]);
  });
});

// ---------------------------------------------------------------------------
// The gate on the gate
// ---------------------------------------------------------------------------

/**
 * F1: *"each one is verified to fail against a deliberately perturbed render —
 * a baseline nobody has watched fail is a screenshot, not a gate."*
 *
 * The comparison here is clean-against-perturbed **within one run**, not
 * against the committed baseline. That is deliberate: comparing a fresh capture
 * on this machine against a Linux baseline would differ for platform reasons
 * and the test would pass without proving anything. Paired with the baselines
 * above — which do bind to the committed PNGs — the two together say both
 * things that matter: the render matches what was reviewed, and the comparison
 * is sensitive enough to notice when it does not.
 */
test.describe("the baselines are sensitive, not decorative", () => {
  async function capture(page: Page, url: string, selector: string): Promise<Buffer> {
    await page.goto(url);
    const target = page.locator(selector);
    await target.waitFor({ state: "visible" });
    await settle(page);
    return target.screenshot({ animations: "disabled" });
  }

  test("the same seed twice is the same image — or nothing below means anything", async ({
    page,
  }) => {
    const once = await capture(
      page,
      `${PRESS}?surface=citizen&quality=full&seed=1`,
      ".press-proof",
    );
    const again = await capture(
      page,
      `${PRESS}?surface=citizen&quality=full&seed=1`,
      ".press-proof",
    );
    expect(
      once.equals(again),
      "the press is not reproducible at a fixed seed, so every baseline in this " +
        "file is a coin toss with a PNG attached",
    ).toBe(true);
  });

  test("a one-step change of seed is caught", async ({ page }) => {
    // A registration slip is exactly the class of change §E6.2 cares about and
    // exactly the class a human reviewer would miss in a diff of source.
    const clean = await capture(
      page,
      `${PRESS}?surface=citizen&quality=full&seed=1`,
      ".press-proof",
    );
    const nudged = await capture(
      page,
      `${PRESS}?surface=citizen&quality=full&seed=2`,
      ".press-proof",
    );
    expect(clean.equals(nudged), "a changed seed produced an identical print").toBe(false);
  });

  test("a quality tier drop is caught", async ({ page }) => {
    const full = await capture(
      page,
      `${PRESS}?surface=citizen&quality=full&seed=1`,
      ".press-proof",
    );
    const flat = await capture(
      page,
      `${PRESS}?surface=citizen&quality=flat&seed=1`,
      ".press-proof",
    );
    expect(flat.equals(full), "three inks and one ink rendered identically").toBe(false);
  });

  test("a sub-pixel typographic change is caught on a text surface", async ({ page }) => {
    // 0.01em of tracking at 15 px is 0.15 px. If the harness cannot see that,
    // it cannot see a font-stack regression either — which is the failure A1
    // is about and the one this suite has to be able to notice.
    await page.goto(PUBLIC);
    const target = page.locator('[data-proof-case="populated"]');
    await target.waitFor({ state: "visible" });
    await settle(page);
    const clean = await target.screenshot({ animations: "disabled" });

    await page.addStyleTag({
      content: '[data-proof-case="populated"] * { letter-spacing: 0.01em !important; }',
    });
    await page.waitForTimeout(100);
    const perturbed = await target.screenshot({ animations: "disabled" });

    expect(
      clean.equals(perturbed),
      "0.01em of tracking changed nothing the harness can see — the zero-tolerance " +
        "comparison in playwright.config.ts is not reaching these pixels",
    ).toBe(false);
  });
});
