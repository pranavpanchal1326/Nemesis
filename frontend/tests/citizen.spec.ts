import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type Page } from "@playwright/test";

/**
 * M5's gate — §E17, §E16.1, §E27, and the Phase 20 standard applied early.
 *
 * > Submit → receipt → tracked, end to end **against a live stack**, with a real
 * > chain hash on the receipt.
 * >
 * > **Every one of the six gates is driven by its real event** (§E27), verified
 * > in an E2E test.
 * >
 * > The degraded classifier path renders the third outcome and does not stall.
 *
 * **These tests need the backend running.** That is the point rather than an
 * inconvenience: every claim here is about a chain of real components — the
 * ingest route, the pipeline workers, the append-only log, the disclosure
 * table, the BFF, and the browser — and a mock anywhere in that chain would
 * turn the gate into an assertion about the mock. The Phase 20 rule is exactly
 * this: *"a scene that can only be fired by a button fails the gate."*
 *
 * They skip, loudly, when the stack is down, rather than passing vacuously.
 */

const REPORT = "/report";

/** §26.1's own estimate is eight seconds; the pipeline runs models on this
 *  laptop with Ollama alongside it, so the wait is generous and the failure
 *  message says what was still missing. */
const PIPELINE_TIMEOUT_MS = 45_000;

/**
 * A decodable photograph, committed at `tests/fixtures/media/pothole.jpg`.
 *
 * **Not four magic bytes.** An earlier fixture was a JFIF header plus 512 zero
 * bytes, which satisfies §25.1's magic-byte sniffer — all the *upload* path
 * inspects — and cannot be decoded. Phase 8 decodes: ADR-0032 halts the
 * pipeline when the face detector cannot run and §22.1 fails closed, so an
 * undecodable file correctly parks the report at `pending_classification`
 * having never reached classification or dedup.
 *
 * The gate below asserts that every §E16.1 gate receives its real event. With
 * the old fixture it was asserting that against a report the pipeline had
 * legitimately stopped processing — testing the failure path while claiming to
 * test the success one. See the fixture's README.
 */
const PHOTO = join(
  fileURLToPath(new URL(".", import.meta.url)),
  "fixtures",
  "media",
  "pothole.jpg",
);

async function stackIsUp(page: Page): Promise<boolean> {
  try {
    const response = await page.request.get("/api/realtime");
    if (!response.ok()) return false;
    const body: unknown = await response.json();
    return typeof body === "object" && body !== null && "available" in body;
  } catch {
    return false;
  }
}

/**
 * Walk the flow to the point where a report has been filed, and return its id.
 *
 * The camera is not available to a headless browser, which exercises §E13's
 * ladder rather than working around it: the fallback picker is a **first-class
 * capture path** — on a phone `capture="environment"` opens the camera — so
 * driving it is driving the product, not a test hook.
 */
async function fileAReport(page: Page, description: string): Promise<string> {
  await page.goto(REPORT);

  // §E17.1 step 1. The fallback note is the designed lower tier announcing
  // itself; a headless run that did *not* see it would mean the camera branch
  // silently swallowed its own failure.
  await expect(page.getByText(/camera is not available/i)).toBeVisible();

  await page.setInputFiles('input[type="file"]', PHOTO);

  // §E17.1: "a four-second undo".
  await expect(page.getByRole("button", { name: /undo/i })).toBeVisible();

  await page.getByRole("textbox").fill(description);
  await page.getByRole("button", { name: /^send$/i }).click();

  // §E17.1 step 2 — Place. Geolocation is granted in `test.use` below, so the
  // card resolves against the seeded tenant's real ward boundaries.
  //
  // `exact` on the card's own heading, not `/where/i`. The loose pattern was
  // fine while this screen had exactly one heading and stopped being fine the
  // moment it got the `<h1>` it should always have had — the screen now says
  // *"Where is it?"* to a screen reader and *"Where"* over the card, and both
  // are correct. The step being driven here is the card, so the card is what
  // this locator names.
  await expect(page.getByRole("heading", { name: "Where", exact: true })).toBeVisible();
  const send = page.getByRole("button", { name: /^send$/i });
  await expect(send).toBeEnabled({ timeout: 15_000 });
  await send.click();

  // §E17.1 step 3 — the acknowledgement, then the id from the 202.
  await expect(page.getByRole("heading", { name: /filed/i })).toBeVisible();
  await expect(page.getByRole("link", { name: /follow what happens/i })).toBeVisible({
    timeout: 20_000,
  });

  const href = await page.getByRole("link", { name: /follow what happens/i }).getAttribute("href");
  const complaintId = href?.replace("/t/", "") ?? "";
  expect(complaintId, "no complaint id on the track link").toMatch(/^[0-9a-f-]{36}$/);
  return complaintId;
}

test.use({
  // Kothrud, in the tenant `nem seed-demo` provisions. A real coordinate inside
  // a real (approximate) ward boundary, so the Place card resolves through the
  // same PostGIS query a citizen's phone would.
  permissions: ["geolocation"],
  geolocation: { latitude: 18.5074, longitude: 73.8077 },
});

test.describe("M5 — the citizen loop, end to end", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(
      !(await stackIsUp(page)),
      "the backend is not reachable — run `nem up`, then `nem seed-demo`",
    );
  });

  test("submit → receipt → tracked, with a real chain hash", async ({ page }) => {
    const complaintId = await fileAReport(page, "open drain by the school gate");

    // §E17.3 — the receipt is a *document*, and its claim is the hash.
    const hash = page.locator(".receipt__hash");
    await expect(hash).toBeVisible();
    const rendered = (await hash.innerText()).trim();
    expect(rendered, "the receipt's chain hash is not a SHA-256").toMatch(/^[0-9a-f]{64}$/);

    // ADR-0044: the value on the receipt must be the hash of the event that was
    // actually written, not a plausible-looking string. Read the log back and
    // compare — this is the assertion that makes §E17.3's sentence true rather
    // than decorative.
    const history = await page.request.get(`/api/complaints/${complaintId}/events`);
    expect(history.ok()).toBe(true);
    const log = (await history.json()) as {
      chain_head: string;
      chain_head_sequence: number;
      events: { sequence: number; event_type: string; previous_hash: string; event_hash: string }[];
    };

    const submitted = log.events.find((event) => event.event_type === "complaint_submitted");
    expect(submitted?.event_hash).toBe(rendered);

    // The chain verifies link by link, which is what §E17.4's ledger publishes
    // the hashes *for*.
    for (let index = 1; index < log.events.length; index += 1) {
      expect(log.events[index]?.previous_hash).toBe(log.events[index - 1]?.event_hash);
    }
    expect(log.events.at(-1)?.event_hash).toBe(log.chain_head);

    // §E17.4 — the tracking screen is the ledger, and it opens on the same id.
    await page.goto(`/t/${complaintId}`);
    await expect(page.getByRole("heading", { name: /your report/i })).toBeVisible();
    await expect(page.locator(".evidence-trail__row").first()).toBeVisible({
      timeout: PIPELINE_TIMEOUT_MS,
    });
  });

  test("the flow completes with the optional field empty", async ({ page }) => {
    // §26.1 and §E17.1: *"one optional field … the app must work if it is left
    // empty."* Asserted as a path through the product rather than as a unit
    // test of the form, because the failure this guards against is a `required`
    // attribute somebody adds in a hurry.
    await page.goto(REPORT);
    await page.setInputFiles('input[type="file"]', PHOTO);
    await page.getByRole("button", { name: /^send$/i }).click();

    const send = page.getByRole("button", { name: /^send$/i });
    await expect(send).toBeEnabled({ timeout: 15_000 });
    await send.click();

    await expect(page.getByRole("heading", { name: /filed/i })).toBeVisible();
    await expect(page.locator(".receipt__hash")).toBeVisible({ timeout: 20_000 });
  });

  test("the place card names the ward the coordinate is actually in", async ({ page }) => {
    // §E17.1: *"presented as a card, not a picker"*, and the name comes from the
    // tenant's own zone tree through `GET /places/resolve` — not from a
    // third-party geocoder, which §6 Principle #6 forbids outright.
    await page.goto(REPORT);
    await page.setInputFiles('input[type="file"]', PHOTO);
    await page.getByRole("button", { name: /^send$/i }).click();

    await expect(page.locator(".place-card__name")).toContainText("Kothrud", {
      timeout: 15_000,
    });
    // Innermost first, joined — §E17.1's own reading order.
    await expect(page.locator(".place-card__name")).toContainText("Pune");
  });

  test("no request leaves the origin on the citizen route", async ({ page }) => {
    // §6 Principle #6, widened past fonts (A13). The citizen surface is the one
    // that runs on a phone with no network to spare, and a single CDN call here
    // is what breaks Phase 29's air-gapped bootstrap.
    const offOrigin: string[] = [];
    page.on("request", (request) => {
      const host = new URL(request.url()).host;
      if (host !== "127.0.0.1:3210" && host !== "localhost:3210") offOrigin.push(request.url());
    });

    await page.goto(REPORT);
    await page.waitForLoadState("networkidle");

    expect(offOrigin, "the citizen route fetched something off-origin").toEqual([]);
  });
});

test.describe("§E16.1 — every gate is driven by its real event", () => {
  /*
   * The test's own budget has to be larger than the wait inside it.
   *
   * Playwright's default is 30 s and `PIPELINE_TIMEOUT_MS` is 45 s, so a run
   * slower than thirty seconds died on the *outer* clock — reporting "a gate
   * never received its event" from whichever poll happened to be in flight
   * rather than from a wait that had actually expired. The generous inner
   * budget was never reachable, which made a slow pipeline look like a broken
   * one. The margin covers the upload, the render and the event-log
   * cross-check that follow it.
   */
  test.describe.configure({ timeout: PIPELINE_TIMEOUT_MS + 30_000 });

  test.beforeEach(async ({ page }) => {
    test.skip(
      !(await stackIsUp(page)),
      "the backend is not reachable — run `nem up`, then `nem seed-demo`",
    );
  });

  test("the theatre's stamps come from the log, not from a timer", async ({ page }) => {
    const complaintId = await fileAReport(page, "broken footpath outside the clinic");

    // Every gate settles. Which *outcome* each one lands on depends on what the
    // real pipeline found, and that is the property being asserted: the stamps
    // are readings, not a scripted sequence.
    const gates = page.locator(".theatre__gate");
    await expect(gates).toHaveCount(5);

    await expect(async () => {
      const waiting = await page.locator('.theatre__gate[data-state="waiting"]').count();
      expect(waiting, "a gate never received its event").toBe(0);
    }).toPass({ timeout: PIPELINE_TIMEOUT_MS });

    // Cross-check against the log the stamps claim to read. A theatre that
    // animated on a timer would pass everything above and fail this.
    const history = await page.request.get(`/api/complaints/${complaintId}/events`);
    const log = (await history.json()) as { events: { event_type: string }[] };
    const seen = new Set(log.events.map((event) => event.event_type));

    // The trust gate cannot stamp without this event, and the safety gate reads
    // it as evidence that the pass happened at all.
    expect(seen, "no EXIF check on the chain").toContain("exif_check_completed");
    expect(seen, "no redaction on the chain — §22.1 fails closed").toContain("media_redacted");
  });

  test("a gate the log has not reached yet says so instead of guessing", async ({ page }) => {
    // §E3.3, and the reason §E17.2 says *"do not show a spinner"*: an unresolved
    // gate is a named absence, not a placeholder and not an invented pass. This
    // catches the first render, before the pipeline has answered.
    await page.goto(REPORT);
    await page.setInputFiles('input[type="file"]', PHOTO);
    await page.getByRole("button", { name: /^send$/i }).click();
    const send = page.getByRole("button", { name: /^send$/i });
    await expect(send).toBeEnabled({ timeout: 15_000 });
    await send.click();

    // At least one gate is waiting the moment the theatre appears, and it says
    // what it is waiting for rather than spinning.
    await expect(page.locator(".theatre__gate").first()).toBeVisible({ timeout: 20_000 });
    await expect(
      page
        .locator(".theatre")
        .getByText(/not checked yet|not looked at yet|not compared yet/i)
        .first(),
    ).toBeVisible();
  });
});
