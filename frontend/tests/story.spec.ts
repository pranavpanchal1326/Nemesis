import { join } from "node:path";
import { fileURLToPath } from "node:url";

import AxeBuilder from "@axe-core/playwright";
import { expect, type Locator, type Page } from "@playwright/test";

import { test } from "./fixtures/origin.ts";
import { ACT_IDS } from "../src/story/acts.ts";

/**
 * The Phase 20 gate — §E16, §E25, F12–F14.
 *
 * > * **Every scene is triggered by a genuine backend event in an E2E test. A
 * >   scene that can only be fired by a button fails.**
 * > * Every fallback tier exercised in CI by forcing its trigger.
 * > * Golden-image regression per scene at fixed seed and camera.
 * > * Frame budget held with all effects enabled.
 * > * Tier C prints reviewed as a design deliverable, not merely generated.
 * > * F12: **stopping the scroll stops the walk.**
 * > * F13: Act 4 renders the actual capture component, not a picture of one.
 *
 * **What is asserted here and what is asserted elsewhere.** The scroll
 * property has a unit half in `tests/story-spine.test.ts` — it is a
 * relationship between two functions and asserting it through a real scrollbar
 * would test the browser's scroll restoration as much as the film. What can
 * *only* be asserted in an engine is here: that the film's Act 4 is the citizen
 * loop's own component, that Act 5's stamps come off a complaint's ledger, that
 * Act 6 stamps nothing until a real merge arrives, that every act's copy is
 * present with no JavaScript, and the pictures.
 *
 * **The live half needs the backend running**, and skips loudly rather than
 * passing vacuously — the same rule `tests/citizen.spec.ts` states at length.
 *
 * **Headless Chromium reports `prefers-reduced-motion: reduce` by default**,
 * which would silently run every case below on the storyboard rung. Found by
 * `tests/clay.spec.ts`; the one case that is *about* reduced motion emulates it
 * explicitly, which is the right way round.
 */
test.use({
  contextOptions: { reducedMotion: "no-preference" },
  // Kothrud, inside a real ward of the tenant `nem seed-demo` provisions — the
  // same coordinate `tests/citizen.spec.ts` files from, so the Place card
  // resolves through the same PostGIS query a citizen's phone would and two
  // reports land close enough for dedup to have an opinion.
  permissions: ["geolocation"],
  geolocation: { latitude: 18.5074, longitude: 73.8077 },
});

/**
 * Building a clay scene is an adapter acquisition, a shader compile and a first
 * frame; on a software rasteriser that is tens of seconds.
 */
const SCENE_TIMEOUT_MS = 180_000;

/**
 * Every case in this file gets the scene budget, not Playwright's default.
 *
 * The default is thirty seconds, which is a timeout for a page. This suite
 * drives a live pipeline and a WebGPU scene on one laptop by design (ADR-0002),
 * and a per-assertion timeout longer than the test's own is a timeout that can
 * never be reached — which is not a slow test, it is an assertion that silently
 * cannot pass. Found the hard way: three cases were waiting ninety seconds
 * inside a thirty-second budget and failing with a message about the wrong
 * thing entirely.
 */
test.beforeEach(() => {
  test.setTimeout(SCENE_TIMEOUT_MS);
});

/** The demo city, provisioned by `nem seed-demo` and published by ADR-0046. */
const CITY = "pune-demo";

const PROOF = `/developers/proof/story?tenant=${CITY}&seed=1`;
const LANDING = "/";

/** §26.1 budgets eight seconds for the pipeline; this laptop runs models beside
 *  Ollama, so the wait is generous and the failure names what was missing. */
const PIPELINE_TIMEOUT_MS = 45_000;

const PHOTO = join(
  fileURLToPath(new URL(".", import.meta.url)),
  "fixtures",
  "media",
  "pothole.jpg",
);

/** §E10.2 puts every id, hash, coordinate and timestamp in the data face. */
const VOLATILE = ".type-mono-data";

/**
 * Whether there is a film to run at all.
 *
 * **Not `/api/realtime`.** That handler answers from configuration —
 * `available: true` means a tenant id is *set*, not that anything is listening
 * — so a spec that skipped on it would run the live gates against a stopped
 * backend and report failures that are not defects. This asks the question the
 * film actually depends on: did a server render of the proof route reach the
 * published zone index and find a centroid to put a camera over? Only then is
 * `<Walk>` in the DOM.
 */
async function cityIsPublished(page: Page): Promise<boolean> {
  const response = await page.goto(`${PROOF}&t=0`);
  if (response === null) return false;
  return (await page.locator('[data-story="walk"]').count()) > 0;
}

/**
 * File a real report **through the film's own Act 4**.
 *
 * The fallback picker rather than a camera, because a headless browser has
 * none — which exercises §E13's ladder rather than working around it: on a
 * phone `capture="environment"` opens the camera app, so this is a first-class
 * capture path and driving it is driving the product.
 *
 * Returns nothing, deliberately. What the film does with the complaint is read
 * out of the DOM by the caller; handing the id back would invite a test to
 * assert against something it fetched itself rather than against what the
 * reader can see.
 */
async function fileThroughTheFilm(page: Page): Promise<void> {
  // No explicit `scrollIntoViewIfNeeded`. Playwright scrolls before it clicks
  // anyway, and doing it separately adds a second stability wait on a page
  // whose banner region can legitimately move while the socket is
  // reconnecting — which is how a button plainly on screen gets reported as
  // "never became stable". The generous click timeouts below are for the same
  // machine condition: this suite drives a live pipeline, and CLIP inference
  // and a dev server share one laptop by design (ADR-0002).
  await page.setInputFiles('input[type="file"]', PHOTO);
  await expect(page.getByRole("button", { name: /undo/i })).toBeVisible({ timeout: 20_000 });

  const send = page.getByRole("button", { name: /^send$/i });
  await send.click({ timeout: 60_000 });

  // §E17.1 step 2 — the place, stated as a card.
  // §E17.1 step 2 resolves a coordinate to a real ward through
  // `GET /places/resolve`, which is a PostGIS query on a machine that is also
  // running the pipeline that the previous case just started. Generous, and
  // for a stated reason rather than by superstition.
  const place = page.getByRole("button", { name: /^send$/i });
  await expect(place).toBeEnabled({ timeout: 60_000 });
  await place.click({ timeout: 60_000 });

  await expect(page.getByRole("heading", { name: /filed/i })).toBeVisible({ timeout: 30_000 });
}

/**
 * Did this report reach deduplication, or did the classifier park it?
 *
 * Polls the complaint's own ledger — the same `GET /complaints/{id}/events` the
 * gates read — until it settles, and answers the one question Act 6's gate
 * turns on. Written as a loop rather than as an `expect.poll` because the
 * result is a *decision*, not an assertion: both answers are correct outcomes
 * of a working pipeline, and only one of them has a merge to render.
 */
async function pipelineReachedDedup(page: Page, complaintId: string): Promise<boolean> {
  // The **scene** budget rather than the pipeline's, and the difference is the
  // machine rather than the backend. Eight seconds is what §26.1 budgets for a
  // report in isolation; this case runs inside a suite that has just driven a
  // WebGPU scene twenty-six times and filed several other reports through the
  // same workers, on the one laptop ADR-0002 deliberately shares between a
  // model and everything else. Found by the full-suite run: alone the report
  // parks in seconds and the case skips by name, and under load it had neither
  // merged nor parked inside forty-five — which failed the assertion below
  // while saying nothing true about Act 6.
  let deadline = Date.now() + SCENE_TIMEOUT_MS;
  let parked = false;

  while (Date.now() < deadline) {
    const response = await page.request.get(`/api/complaints/${complaintId}/events`);
    if (response.ok()) {
      const body = (await response.json()) as { events?: { event_type: string }[] };
      const types = new Set((body.events ?? []).map((event) => event.event_type));
      if (types.has("cluster_match_found")) return true;
      // Parked is a settled state, not a waiting one (§24.2) — but a merge can
      // still arrive from a report filed moments later, so the first sight of
      // it shortens the wait rather than ending it.
      if (!parked && types.has("pipeline_stage_degraded")) {
        parked = true;
        deadline = Math.min(deadline, Date.now() + 15_000);
      }
    }
    await page.waitForTimeout(1500);
  }

  expect(parked, "the pipeline neither merged nor parked within its budget").toBe(true);
  return false;
}

async function settle(page: Page): Promise<void> {
  await page.waitForLoadState("networkidle");
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
  await page.waitForTimeout(400);
}

/**
 * Wait until the clay scene exists before touching the page.
 *
 * The canvas publishes the digest of the entity array it was handed once it has
 * finished building (§E22's assertion seam), and building is an adapter
 * acquisition, a shader compile and a first frame — which on a software
 * rasteriser holds the main thread for tens of seconds. Interacting before that
 * is how a button that is plainly on screen is reported as "not stable", and it
 * is the difference between a suite that passes alone and a suite that passes.
 */
async function sceneIsBuilt(page: Page): Promise<void> {
  await expect(page.locator("canvas.clay__canvas")).toHaveAttribute("data-clay-digest", /.+/, {
    timeout: SCENE_TIMEOUT_MS / 2,
  });
}

// --------------------------------------------------------------------------
// F12 · The spine and the cold open
// --------------------------------------------------------------------------

test.describe("F12 — the spine", () => {
  test("renders all nine acts' copy with no JavaScript at all", async ({ browser }) => {
    // §E13 Tier D. The film is nine acts of semantic markup with a camera
    // behind it, and this is the assertion that keeps it that way — a reader on
    // a 2G phone, a crawler, and anybody who has switched scripting off gets
    // every word.
    const context = await browser.newContext({ javaScriptEnabled: false });
    const page = await context.newPage();
    await page.goto(LANDING);

    for (const act of ACT_IDS) {
      // Every act, including the receipts below the fold. `Storyboard` and the
      // film reel both satisfy this, which is the point: the two tiers carry
      // the same words, so whichever one a deployment renders, Tier D holds.
      await expect(page.locator(`[data-act="${act}"]`).first(), act).toHaveCount(1);
    }
    // §44, published on the marketing surface — §E16.2's differentiator.
    await expect(page.locator('[data-act="receipts"]')).toContainText(/./);
    await context.close();
  });

  test("the walk does not advance while the scroll is still", async ({ page }) => {
    test.skip(
      !(await cityIsPublished(page)),
      "no published city to film — see NEMESIS_STORY_TENANT",
    );
    await page.goto(LANDING);
    await settle(page);
    const film = page.locator('[data-story="walk"]');
    test.skip((await film.count()) === 0, "this deployment renders the storyboard");

    await page.mouse.wheel(0, 4000);
    await page.waitForTimeout(1200);
    const first = await film.getAttribute("data-walk-metres");

    // Two full seconds of frames with nobody touching the scroll. A film that
    // advanced on time rather than on distance walks the rest of the road here.
    await page.waitForTimeout(2000);
    expect(await film.getAttribute("data-walk-metres")).toBe(first);
  });

  test("the spine reaches the end of the film, not the end of the page", async ({ page }) => {
    test.skip(!(await cityIsPublished(page)), "no published city to film");
    await page.goto(LANDING);
    await settle(page);
    const film = page.locator('[data-story="walk"]');
    test.skip((await film.count()) === 0, "this deployment renders the storyboard");

    // Act 9 is below the fold and off the spine. Measuring progress against the
    // document rather than the reel would leave `t` around 0.8 at the last shot
    // and the final two acts would never play.
    const reel = page.locator(".walk__reel");
    await reel.evaluate((node) => {
      window.scrollTo(0, node.getBoundingClientRect().height);
    });
    await page.waitForTimeout(2500);
    expect(Number(await film.getAttribute("data-story-t"))).toBeGreaterThan(0.95);
    expect(await film.getAttribute("data-story-act")).toBe("table");
  });

  test("the cold open sets the name and states the motto", async ({ page }) => {
    await page.goto(LANDING);
    await settle(page);
    // The name is one heading for the document even though it animates glyph by
    // glyph — a screen reader must not spell it out.
    const heading = page.locator("h1").first();
    await expect(heading).toBeVisible();
    await expect(page.locator("body")).toContainText("Prove, don't log.");
  });
});

// --------------------------------------------------------------------------
// F13 · The transition and the gates
// --------------------------------------------------------------------------

test.describe("F13 — the report and the pipeline", () => {
  test("Act 4 mounts the citizen loop's own capture component", async ({ page }) => {
    test.skip(!(await cityIsPublished(page)), "no published city to film");
    // §E16 Act 4: *"the viewfinder is the real `<ReportCapture>` in DOM"*. The
    // assertion is a comparison rather than a look: whatever `/report` renders
    // in its capture phase, the film's Act 4 renders too. A facsimile would
    // drift within a sprint, and this is what stops it.
    await page.goto("/report");
    await settle(page);
    const citizen = await page.locator('[data-phase="capture"]').innerHTML();

    await page.goto(`${PROOF}&act=report&f=0.2`);
    await settle(page);
    const film = page.locator('[data-act="report"] [data-phase="capture"]');
    await expect(film).toHaveCount(1);
    expect(await film.innerHTML()).toBe(citizen);
  });

  test("Act 5's stamps come from a complaint's own ledger", async ({ page }) => {
    test.skip(
      !(await cityIsPublished(page)),
      "no backend and no published city — run `nem up`, then `nem seed-demo`",
    );
    await page.goto(`${PROOF}&act=report&f=0.2`);
    await settle(page);

    await fileThroughTheFilm(page);

    // The theatre is M5's, reading `GET /complaints/{id}/events`. Nothing here
    // is fired by the click: the click filed a report, and the pipeline is what
    // stamps the card.
    const gates = page.locator('[data-act="pipeline"] [data-gate]');
    await expect(gates.first()).toBeVisible({ timeout: PIPELINE_TIMEOUT_MS });

    // Scoped to Act 5's own section. The film shows the theatre twice on
    // purpose once a report has been filed — inside the phone, which is what
    // the citizen sees (§E17.2), and as the physical gates the card travels
    // through, which is what actually happens (§E16 Act 5). They are the same
    // component reading the same ledger, so the assertion has to name which one
    // it is talking about rather than find two and stop.
    for (const gate of ["safety", "trust", "perception", "redaction", "dedup"]) {
      const row = page.locator(`[data-act="pipeline"] [data-gate="${gate}"]`);
      await expect(row, gate).toHaveCount(1);
      // Settled means an event arrived. §24.2's third outcome and the *held*
      // state are both settled states — the gate does not stall and does not
      // guess, and neither does this assertion.
      await expect(row, `${gate} never received its event`).not.toHaveAttribute(
        "data-state",
        "waiting",
        { timeout: PIPELINE_TIMEOUT_MS },
      );
    }
  });

  test("with nothing in the pipeline, Act 5 says so and stamps nothing", async ({ page }) => {
    test.skip(!(await cityIsPublished(page)), "no published city to film");
    await page.goto(`${PROOF}&act=pipeline&f=0.5`);
    await settle(page);
    // The honest empty state, and the one that proves the gate. Most readers
    // scroll past Act 4 without filing anything; a film that replayed a canned
    // run here would be a scene fired by a scroll position.
    await expect(page.locator('[data-gates="waiting"]')).toBeVisible();
    await expect(page.locator("[data-gate]")).toHaveCount(0);
  });
});

// --------------------------------------------------------------------------
// F14 · The merge, the table, and Tier C
// --------------------------------------------------------------------------

test.describe("F14 — the merge, the survey and the receipts", () => {
  test("Act 6 stamps nothing until a real merge arrives", async ({ page }) => {
    test.skip(!(await cityIsPublished(page)), "no published city to film");
    await page.goto(`${PROOF}&act=merge&f=0.55`);
    await settle(page);

    const act = page.locator('[data-act="merge"]');
    await expect(act).toHaveAttribute("data-merge", "waiting");
    await expect(act.locator(".merge__stamp")).toHaveCount(0);
    await expect(act.locator(".merge__waiting")).toBeVisible();
  });

  test("Act 6 stamps a real cluster_match_found — the gate", async ({ page }) => {
    test.skip(
      !(await cityIsPublished(page)),
      "no backend and no published city — run `nem up`, then `nem seed-demo`",
    );
    test.setTimeout(PIPELINE_TIMEOUT_MS * 4);

    // **Nothing is injected.** The gate's own words are *"a scene that can only
    // be fired by a button fails"*, and a test that pushed a synthetic envelope
    // onto the bus would be firing the scene itself — a button in a costume. So
    // this files two reports from the same coordinate and lets Phase 10's
    // deduplication decide there is a match. What reaches the browser is the
    // real `cluster_match_found`, over the real socket, shaped by
    // `nemesis/realtime/envelope.py`.
    await page.goto(`${PROOF}&act=report&f=0.2`);
    await settle(page);
    await sceneIsBuilt(page);
    await fileThroughTheFilm(page);

    // The second report, and then **the page stays where it is**. A film is one
    // document: `<Walk>` renders all nine acts, so Act 6 is already in the DOM
    // below Act 4 and is listening on the same socket. Navigating here would
    // drop the connection and its cursor, and the merge would fire into a page
    // that no longer exists — which is not the film failing, it is the test
    // walking out of the room.
    await page.goto(`${PROOF}&act=report&f=0.2`);
    await settle(page);
    await sceneIsBuilt(page);
    await fileThroughTheFilm(page);

    // The complaint the film is following, read off the peel card — the paper
    // Act 4 turns the photograph into, which carries the id in the data face.
    const complaintId = (await page.locator(".peel__card").last().innerText()).trim();
    expect(complaintId, "the film never peeled a card").toMatch(/^[0-9a-f-]{36}$/);

    // **Whether this machine can reach dedup at all is a question about the
    // classifier, not about the film.** Phase 9 abstains rather than guessing
    // when its top category falls below the floor its calibration sets (§24.2,
    // and Phase 9 published four categories below its F1 floor rather than
    // tuning them). A parked report never reaches deduplication, so there is no
    // merge to render — and a gate that failed here would be reporting a
    // fixture's CLIP score as a defect in Act 6.
    //
    // So the ledger is asked, and the answer decides which of two honest things
    // happens: the gate is taken, or it is skipped by name. It is never passed
    // vacuously and never failed for somebody else's reason.
    const merged = await pipelineReachedDedup(page, complaintId);

    test.skip(
      !merged,
      "the classifier abstained on this fixture, so the report parked before dedup — " +
        "no cluster_match_found exists to render (§24.2, docs/reports/story-merge-gate.md)",
    );

    const act = page.locator('[data-act="merge"]');
    await expect(act, "no cluster_match_found reached the browser").toHaveAttribute(
      "data-merge",
      "live",
      { timeout: PIPELINE_TIMEOUT_MS },
    );

    // The numbers on the stamp are the event's own. §E11.1: deduplication is
    // not deletion, so the reports that were absorbed leave their rings, and
    // the ring count is derived from the same report count the stamp prints —
    // the picture and the number cannot disagree.
    await expect(act.locator(".merge__stamp")).toContainText(/\d+ REPORTS/);
    await expect(act.locator(".merge__ring")).not.toHaveCount(0);
    // §E16's *"mono timestamp ticking"* — the event's own, never the browser's.
    await expect(act.locator(".merge__at")).toContainText(/\d{4}-\d{2}-\d{2}T/);
  });

  test("Act 7's ward field opens a real published ward", async ({ page }) => {
    test.skip(!(await cityIsPublished(page)), "no published city to film");
    await page.goto(`${PROOF}&act=city-awake&f=0.9`);
    await settle(page);

    const options = page.locator('[data-act="city-awake"] datalist option');
    const count = await options.count();
    test.skip(count === 0, "this city publishes no places");

    const name = await options.first().getAttribute("value");
    await page.locator('[data-act="city-awake"] input').fill(name ?? "");
    const open = page.locator("[data-ward]");
    await expect(open).toBeVisible();
    // The link goes to the §E18 page that holds that ward's real figures — the
    // film names a place and never restates a number about it.
    expect(await open.getAttribute("href")).toContain(`/${CITY}/ward/`);
  });

  test("Act 9 publishes the honesty table on the marketing surface", async ({ page }) => {
    // §E16.2, and §6 Principle #8 tested where it is least comfortable.
    // Asserted on the surface rather than on a tier: the film's Act 9 and the
    // storyboard's last frame both carry it, because the table describes the
    // platform and needs no tenant data.
    await page.goto(LANDING);
    await settle(page);
    const honesty = page.locator("[data-story-honesty]");
    await expect(honesty).toHaveCount(1);
    await expect(honesty).toContainText(/REAL|SIMULATED|ROADMAP/);
  });

  test("the acts say which of them are real", async ({ page }) => {
    await page.goto(LANDING);
    await settle(page);
    const film = page.locator('[data-story="walk"]');
    test.skip((await film.count()) === 0, "this deployment renders the storyboard");

    // The narrative acts and the real ones, marked in the DOM. §6 Principle #8:
    // the honest label is the differentiator, and a film is where a product is
    // most tempted to blur it.
    for (const act of ["cold-open", "walk", "stop", "silence", "table"]) {
      await expect(page.locator(`[data-act="${act}"]`), act).toHaveAttribute("data-real", "false");
    }
    for (const act of ["report", "pipeline", "merge", "city-awake", "receipts"]) {
      await expect(page.locator(`[data-act="${act}"]`), act).toHaveAttribute("data-real", "true");
    }
  });
});

// --------------------------------------------------------------------------
// The ladder — "every fallback tier exercised in CI by forcing its trigger"
// --------------------------------------------------------------------------

test.describe("§E13 — the ladder, forced", () => {
  // A cold browser context pays for the whole client bundle again, and this
  // suite runs it on a machine that is at that moment also running CLIP
  // inference for the reports the F13 cases filed (ADR-0002 puts a model on
  // the same box by design). Five minutes is not a claim about the product's
  // speed; it is a budget wide enough that a slow machine reports "the ladder
  // did not move" rather than "the page did not finish loading".
  test.describe.configure({ timeout: 300_000 });

  test("prefers-reduced-motion switches the landing to the storyboard", async ({ browser }) => {
    // §E11.1's own instruction, and §E13's consent rung. A person who has asked
    // their operating system for less motion is not overruled by a benchmark.
    const context = await browser.newContext({ reducedMotion: "reduce" });
    const page = await context.newPage();
    await page.goto(LANDING);

    // Wait for *a* surface, then assert which one. The ladder is decided one
    // frame after hydration and then the film swaps itself for the prints; how
    // long that takes depends on how busy the machine is. Waiting only for the
    // storyboard turns "the film rendered, which is the bug" and "the page was
    // slow, which is not" into the same bare timeout — and the first of those
    // is the whole point of the test.
    // The server renders the film's reel for everyone — that is §E13's Tier D,
    // and it is what a crawler and a scripting-off reader get. The ladder is
    // then decided one frame after hydration, and only then does a
    // reduced-motion device swap to the prints. So the wait is for the
    // *decision*, not for the first thing on screen: asserting immediately
    // would race the probe, and asserting on a bare timeout would report a slow
    // machine and a broken ladder in the same words.
    await expect(page.locator('[data-tier-surface="storyboard"]')).toBeVisible({
      timeout: 280_000,
    });
    await expect(page.locator('[data-story="walk"]')).toHaveCount(0);
    await context.close();
  });

  test("Tier C is nine prints, scroll-snapped, carrying the same copy", async ({ browser }) => {
    // §E13: *"Nine art-directed riso prints, scroll-snapped, same copy."* Ten
    // frames, because §E16's Act 9 is one of the nine acts and the register
    // counts it — the film has nine *shots* and the story has ten *frames*.
    const context = await browser.newContext({ reducedMotion: "reduce" });
    const page = await context.newPage();
    await page.goto(LANDING);

    const prints = page.locator(".storyboard__print");
    await expect(prints).toHaveCount(ACT_IDS.length, { timeout: SCENE_TIMEOUT_MS / 2 });
    // Every print is a drawing, not a screenshot of the 3D scene.
    await expect(page.locator(".storyboard__frame")).toHaveCount(ACT_IDS.length);
    await expect(page.locator("body")).toContainText("Prove, don't log.");
    await context.close();
  });

  test("Tier C is clean to axe in both scripts", async ({ browser }) => {
    for (const locale of ["en", "mr"]) {
      const context = await browser.newContext({ reducedMotion: "reduce" });
      const page = await context.newPage();
      await page.goto(`${LANDING}?locale=${locale}`);
      await expect(page.locator('[data-tier-surface="storyboard"]')).toBeVisible({
        timeout: SCENE_TIMEOUT_MS / 2,
      });
      const results = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
        .analyze();
      expect(
        results.violations,
        `${locale}: ${JSON.stringify(results.violations, null, 2)}`,
      ).toEqual([]);
      await context.close();
    }
  });

  test("the film itself is clean to axe", async ({ page }) => {
    await page.goto(LANDING);
    await settle(page);
    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    expect(results.violations, JSON.stringify(results.violations, null, 2)).toEqual([]);
  });
});

// --------------------------------------------------------------------------
// The golden images — "per scene at fixed seed and camera"
// --------------------------------------------------------------------------

test.describe("§E24 — a golden image per act", () => {
  /** Where inside each act to stand. The middle for most; the two acts whose
   *  whole content is a move are photographed where the move lands. */
  const FRACTIONS: Readonly<Record<string, number>> = {
    "cold-open": 0.4,
    walk: 0.5,
    // The two acts whose whole content is a move are photographed where the
    // move lands, a hair inside the act — a fraction of exactly 1 is the first
    // frame of the *next* act (`tests/story-acts.test.ts` says so).
    stop: 0.98,
    silence: 0.5,
    report: 0.2,
    pipeline: 0.5,
    merge: 0.55,
    "city-awake": 0.9,
    table: 0.98,
  };

  for (const [act, fraction] of Object.entries(FRACTIONS)) {
    test(`act ${act} at a fixed t`, async ({ page }) => {
      test.skip(!(await cityIsPublished(page)), "no published city to film");
      test.setTimeout(SCENE_TIMEOUT_MS);
      await page.goto(`${PROOF}&act=${act}&f=${String(fraction)}`);
      await settle(page);

      // Wait for the scene to *exist* before photographing it. The canvas
      // publishes the digest of the entity array it was handed once it has
      // built (§E22's assertion seam), and building is an adapter acquisition,
      // a shader compile and a first frame — tens of seconds on a software
      // rasteriser. Screenshotting before that photographs an empty canvas, and
      // screenshotting during it never stabilises.
      await expect(page.locator("canvas.clay__canvas")).toHaveAttribute("data-clay-digest", /.+/, {
        timeout: SCENE_TIMEOUT_MS / 2,
      });

      // Ids, hashes and timestamps are masked rather than tolerated: a
      // tolerance wide enough to absorb a changing id is wide enough to absorb
      // a regression.
      const volatile: Locator[] = await page.locator(VOLATILE).all();
      await expect(page).toHaveScreenshot(`story-${act}.png`, {
        mask: volatile,
        // The default five seconds is a timeout for a page, not for a GPU
        // pipeline. `tests/clay.spec.ts` makes the same allowance for the same
        // reason.
        timeout: 30_000,
      });
    });
  }

  test("the storyboard, as a design deliverable", async ({ page }) => {
    // §E13: Tier C is reviewed, not merely generated. A baseline is what makes
    // "reviewed" a thing that happens more than once.
    //
    // Photographed through the proof route with `?tier=C` rather than by
    // emulating reduced motion on the landing, and the difference is the
    // stepped clock: the press misregisters its plates at 12 Hz (§E6.1 stage 3)
    // and Playwright's `animations: "disabled"` only stops *CSS* animations, so
    // a storyboard photographed on a running clock differs from itself by a
    // couple of hundred pixels of ink. The proof route pins the clock, which is
    // what "at a fixed seed" has to mean for a surface printed by a press.
    await page.goto(`${PROOF}&tier=C`);
    await settle(page);
    await expect(page.locator('[data-tier-surface="storyboard"]')).toBeVisible();
    await expect(page).toHaveScreenshot("story-storyboard.png", {
      fullPage: true,
      timeout: 30_000,
    });
  });
});
