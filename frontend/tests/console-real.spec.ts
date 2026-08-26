import { expect, type Page } from "@playwright/test";

import { test } from "./fixtures/origin.ts";

/**
 * F4, F5 and F6's gates — the three console screens that are **REAL** today.
 *
 * > **F4** — a decision taken in the browser appears in the event log and moves
 * >   the item, asserted against a live stack. The trail renders identical rows
 * >   to the citizen view minus the filtered ones, asserted by comparing
 * >   renders rather than by reading the code.
 * >
 * > **F5** — a policy cannot be activated without a backtest **and the refusal
 * >   states why** — asserted in the browser against the live guardrail, not
 * >   against a client-side check.
 * >
 * > **F6** — a tenant is provisioned, given an invented taxonomy, and published
 * >   — entirely through the UI, no SQL, no code change.
 *
 * Separate from `console.spec.ts` because the split is a real one: that file
 * asserts properties of the frontend and runs with the stack down, and every
 * claim here is about what the *backend* does when a browser asks it. Each test
 * skips loudly rather than passing vacuously, the same call `citizen.spec.ts`
 * makes and for the same reason — a gate that quietly passes when the thing it
 * gates is switched off is worse than no gate.
 *
 * **§E19.4's division is the rule these tests are written to.** The backend
 * enforces; the UI renders the rule legibly. So the assertions are on the
 * server's answer and on the screen's rendering of it, never on a disabled
 * attribute standing in for either.
 */

const CONSOLE = "/console";

async function stackIsUp(page: Page): Promise<boolean> {
  try {
    const response = await page.request.get("/api/realtime");
    return response.ok();
  } catch {
    return false;
  }
}

/** A value nothing else in the database can collide with. F6 provisions real
 *  rows in a real control plane, and a fixed slug would pass once. */
function unique(prefix: string): string {
  return `${prefix}-${Date.now().toString(36)}${Math.floor(Math.random() * 1e4).toString(36)}`;
}

interface OpenItem {
  readonly id: string;
  readonly complaint_id: string;
}

/** The queue as the server holds it, read through the same BFF route the
 *  screen reads. */
async function openItems(page: Page): Promise<readonly OpenItem[]> {
  return await page.evaluate(async () => {
    const response = await fetch("/api/review/queue", { cache: "no-store" });
    const body = (await response.json()) as {
      items: { id: string; complaint_id: string; decided_at: string | null }[];
    };
    return body.items
      .filter((item) => item.decided_at === null)
      .map((item) => ({ id: item.id, complaint_id: item.complaint_id }));
  });
}

/**
 * Narrow the queue to exactly one item — the one the caller names.
 *
 * The screen's selection is *row zero of the sorted, filtered list*, so a test
 * that read an item out of the API and assumed the panel was showing it would
 * be comparing two different complaints — which is precisely what the first
 * draft of the trail test did, and it failed for that reason rather than for
 * the reason it was written to catch. Typing the item's own id into the filter
 * makes the selection unambiguous, and it exercises the filter while it is
 * there.
 */
async function pinTo(page: Page, item: OpenItem): Promise<void> {
  const filter = page.locator("#review-filter");
  await filter.fill(item.id);
  await expect(page.locator(".review__row")).toHaveCount(1);
}

// ---------------------------------------------------------------------------
// F4 — the review queue
// ---------------------------------------------------------------------------

test.describe("F4 — a decision is a write, and the log is where it lands", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!(await stackIsUp(page)), "the backend is not reachable — run `nem up`");
  });

  test("a decision taken in the browser reaches the event log and moves the item", async ({
    page,
  }) => {
    await page.goto(`${CONSOLE}/review`);
    await expect(page.locator(".review").first()).toBeVisible();

    const [item] = await openItems(page);
    // Two lines rather than one assertion: `test.skip` ends the run but the
    // compiler cannot know that, and this repository bans both `!` and the cast
    // that would paper over it. The `return` is unreachable and is the honest
    // way to say so.
    test.skip(item === undefined, "the review queue is empty on this stack");
    if (item === undefined) return;

    // Pinned rather than assumed. The queue is paginated at fifty, so "the row
    // count went down" is not the assertion it looks like — deciding one item
    // on a backlog of hundreds pulls the next one into its place and the count
    // never moves. What has to be shown is that *this* item left the open set.
    await pinTo(page, item);

    // A rationale first: the control refuses without one, and so does the
    // server. Typed rather than filled, because §E22's path is the keyboard.
    await page.locator("#review-rationale").click();
    await page.keyboard.type("Gate: recording a decision from the browser.");

    const approve = page.getByRole("button", { name: /^approve$/i });
    await expect(approve).toBeEnabled();
    await approve.click();

    // The item left the open queue, read back from the server rather than from
    // the component's own state — the server decides what is open, and this is
    // the assertion that holds the screen to it.
    await expect(async () => {
      const remaining = await openItems(page);
      expect(remaining.map((row) => row.id)).not.toContain(item.id);
    }).toPass({ timeout: 20_000 });

    // And it is gone from the list under the same filter that found it, and the
    // pane behind it is empty. A decision that wrote and did not move the row
    // would pass everything above.
    //
    // *Not* an assertion on the "Recorded." line, and that is worth writing
    // down rather than working around: the confirmation is transient by
    // construction. The decided item leaves the open queue, the query
    // invalidates, and the panel that was carrying the sentence unmounts with
    // the item it belonged to — often before a reader has finished it. That is
    // a real observation about the screen and it belongs in a review of the
    // screen; a test that waited for the sentence would be asserting a race.
    await expect(page.locator(".review__row")).toHaveCount(0, { timeout: 20_000 });
    await expect(page.locator(".review__item-pane")).toContainText(/select an item/i);

    // The log. A decision that moved a row and wrote nothing would pass too.
    const history = await page.request.get(`/api/complaints/${item.complaint_id}/events`);
    expect(history.ok()).toBe(true);
    const log = (await history.json()) as { events: { event_type: string }[] };
    expect(
      log.events.map((event) => event.event_type),
      `no review_decided on the chain behind ${item.id}`,
    ).toContain("review_decided");
  });

  test("the officer's trail is the citizen's trail minus its filtered rows", async ({ page }) => {
    await page.goto(`${CONSOLE}/review`);
    await expect(page.locator(".review").first()).toBeVisible();

    const [item] = await openItems(page);
    // Two lines rather than one assertion: `test.skip` ends the run but the
    // compiler cannot know that, and this repository bans both `!` and the cast
    // that would paper over it. The `return` is unreachable and is the honest
    // way to say so.
    test.skip(item === undefined, "the review queue is empty on this stack");
    if (item === undefined) return;
    await pinTo(page, item);

    // §E26: *the same component as the citizen's, differing only by row
    // filtering, never by different code.* Asserted by comparing what the two
    // views render — reading the source would prove the call site is the same
    // and nothing about what a reader ends up seeing.
    //
    // The button rather than `e`, and that is not a shortcut being avoided: the
    // caret is in the filter above, where `e` is the letter e and must stay one
    // (§E22, and the reason `isTypingTarget` exists). The keyboard path to the
    // trail is asserted in `console.spec.ts`, where nothing is being typed.
    await page.locator(".review__toggle").click();
    const officer = page.locator(".evidence-trail__row");
    await expect(officer.first()).toBeVisible({ timeout: 20_000 });
    const officerRows = (await officer.allInnerTexts()).map(normalise);

    // The same complaint, which is the only way this comparison means anything.
    await page.goto(`/t/${item.complaint_id}`);
    const citizen = page.locator(".evidence-trail__row");
    await expect(citizen.first()).toBeVisible({ timeout: 20_000 });
    const citizenRows = (await citizen.allInnerTexts()).map(normalise);

    // Not equality, and not "the officer sees more": the officer view is the
    // superset by construction, so the citizen's rows have to be a *subset* of
    // it. An officer view that dropped a row a citizen can see would be two
    // components pretending to be one.
    const officerSet = new Set(officerRows);
    for (const row of citizenRows) {
      expect(officerSet, `the officer's trail is missing a row the citizen sees: ${row}`).toContain(
        row,
      );
    }

    // And the filtering is real rather than nominal: an officer view identical
    // to the citizen's would satisfy the subset check above while proving that
    // `view="officer"` does nothing.
    expect(
      officerRows.length,
      "the officer's trail filters nothing — the two views are the same rows",
    ).toBeGreaterThan(citizenRows.length);
  });

  test("every photograph on the queue comes from the redacted path", async ({ page }) => {
    await page.goto(`${CONSOLE}/review`);
    test.skip((await page.locator(".review__row").count()) === 0, "the review queue is empty");

    // §22.1 fails closed, and the console is where an unredacted original would
    // be most convenient to reach for. The rule is that `/api/review/media` is
    // the only image route on this screen — asserted on the DOM rather than on
    // the imports, because the failure mode is one `<img>` somebody added.
    const sources = await page
      .locator(".review__item img")
      .evaluateAll((nodes) =>
        nodes.map((node) => (node as HTMLImageElement).getAttribute("src") ?? ""),
      );
    for (const src of sources) {
      expect(src, "an image on the review screen is not from the redacted path").toMatch(
        /^\/api\/review\/media\//,
      );
    }
  });
});

// ---------------------------------------------------------------------------
// F5 — the policy studio
// ---------------------------------------------------------------------------

test.describe("F5 — the guardrail is the server's, and the screen says so", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!(await stackIsUp(page)), "the backend is not reachable — run `nem up`");
  });

  test("the studio renders a revision as a document, with its hash and its diff", async ({
    page,
  }) => {
    await page.goto(`${CONSOLE}/policy`);

    // §E19.8's *"rules as editable documents with revision history and diff"*.
    // The hash is asserted because the document on screen is the thing the hash
    // was computed over — a studio that reformatted it would be showing
    // something the hash does not attest to.
    await expect(page.locator(".policy__kind").first()).toBeVisible();
    const document = page.locator(".policy__document");
    test.skip((await document.count()) === 0, "this stack publishes no policy revisions");
    await expect(document).toBeVisible();
    await expect(page.locator(".policy__hash")).toContainText(/[0-9a-f]{16}/);
  });

  test("the rule is rendered before it is hit, and the control is never the check", async ({
    page,
  }) => {
    await page.goto(`${CONSOLE}/policy`);
    const activate = page.locator(".policy__action");
    test.skip((await activate.count()) === 0, "this stack publishes no policy revisions");

    // Disabled without a reason and **never because of the gate**: §E19.4's
    // rule is that a client-side check is not the control. What the gate
    // changes is the sentence above the button, which is the part that teaches
    // the rule — so the sentence is what is asserted, and it has to be one of
    // the two the product is willing to say.
    await expect(activate).toBeDisabled();
    const note = page.locator(".policy__activate p").first();
    await expect(note).toContainText(/evaluation set/i);
    const rule = await note.innerText();
    expect(
      /gated by the published evaluation set|no evaluation set is published/i.test(rule),
      `the activation panel said something else entirely: ${rule}`,
    ).toBe(true);

    // Supplying a reason enables it, and that is the *only* thing this screen
    // enforces. A control that stayed disabled while the gate was on would be
    // the client-side check the blueprint warns about, wearing the right
    // colours.
    await page.locator(".policy__reason").fill("Gate: proving the guardrail is the server's.");
    await expect(activate).toBeEnabled();
  });

  test("a gated kind is refused by the server, in the server's own words", async ({ page }) => {
    await page.goto(`${CONSOLE}/policy`);
    test.skip(
      (await page.locator(".policy__action").count()) === 0,
      "this stack publishes no policy revisions",
    );

    // Find a kind the deployment actually gates. Whether any kind is gated is a
    // property of the tenant's published evaluation sets, not of this code, so
    // the test looks for one and says plainly when there is none — a stack with
    // no set published cannot exercise a guardrail, and pretending otherwise by
    // asserting against an ungated kind would be a green test about nothing.
    const kinds = await page.locator(".policy__kind").allInnerTexts();
    let gated: string | null = null;
    for (const kind of kinds) {
      await page.goto(`${CONSOLE}/policy?kind=${encodeURIComponent(kind.trim())}`);
      const note = page.locator(".policy__activate p").first();
      if ((await note.count()) === 0) continue;
      if (/gated by the published evaluation set/i.test(await note.innerText())) {
        gated = kind.trim();
        break;
      }
    }
    test.skip(
      gated === null,
      "no policy kind on this stack is gated — publish an evaluation set to exercise the guardrail",
    );

    await page.locator(".policy__reason").fill("Gate: proving the refusal comes from the server.");
    await page.locator(".policy__action").click();

    // The refusal is the server's and it is rendered verbatim: it names the
    // evaluation set, the revision, and what to do next. A paraphrase would
    // lose all three, which is why `ActivateControl` prints the problem
    // document's own title.
    const status = page.locator(".policy__activate [role='status']");
    await expect(status).not.toBeEmpty({ timeout: 20_000 });
    const said = await status.innerText();
    expect(said, `an activation with no passing certificate was not refused: ${said}`).toMatch(
      /certificate|evaluation set/i,
    );
  });
});

// ---------------------------------------------------------------------------
// F6 — the control plane
// ---------------------------------------------------------------------------

test.describe("F6 — a city is provisioned through the UI, not through SQL", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!(await stackIsUp(page)), "the backend is not reachable — run `nem up`");
  });

  test("a tenant is provisioned, given an invented category, and published", async ({ page }) => {
    // Phase 5's own gate, re-run through the surface a solutions engineer would
    // actually use. Every step below is a control on the screen; nothing here
    // touches the API directly, because "through the UI" is the claim.
    await page.goto(`${CONSOLE}/control`);

    const slug = unique("gate-city");
    await page.getByLabel("Slug", { exact: true }).first().fill(slug);
    await page.getByLabel("Name", { exact: true }).fill("Gate City");
    await page.getByRole("button", { name: /^provision$/i }).click();

    const provisioned = page.locator("form", { has: page.getByLabel("Slug", { exact: true }) });
    await expect(provisioned.locator("[role='status']").first()).toContainText(
      new RegExp(`provisioned ${slug}`, "i"),
      { timeout: 30_000 },
    );

    // An *invented* taxonomy, not a template's: Phase 5's claim is that a
    // deployment can define a category this repository has never heard of.
    const category = unique("gate-defect");
    await page.getByLabel("Key", { exact: true }).fill(category);
    await page.getByLabel("Display name", { exact: true }).fill("A defect nobody has named");
    await page.getByRole("button", { name: /define a category/i }).click();
    await expect(page.getByText(/defined\./i).first()).toBeVisible({ timeout: 30_000 });

    // The tree redraws from the server, which is what makes the line above a
    // fact about the control plane rather than about a form's status message.
    await expect(page.locator(".control__key", { hasText: category })).toBeVisible({
      timeout: 30_000,
    });

    // ADR-0046: publication is an act somebody takes, with a required
    // justification, revocable through the same door. Both halves are asserted
    // — the refusal without a justification, and the act with one.
    const publish = page.getByRole("button", { name: /^publish$/i });
    await page.getByLabel("Slug", { exact: true }).last().fill(slug);
    await expect(publish, "publication was offered without a justification").toBeDisabled();

    await page.getByLabel("Justification").fill("Gate: proving ADR-0046 through the UI.");
    await expect(publish).toBeEnabled();
    await publish.click();
    await expect(page.getByText(new RegExp(`publication for ${slug} is now on`, "i"))).toBeVisible({
      timeout: 30_000,
    });
  });

  test("the developer portal states the deprecation clock rather than the version alone", async ({
    page,
  }) => {
    await page.goto(`${CONSOLE}/developers`);

    // §E14.4. A version registry that lists versions and not their sunset is a
    // registry that tells an integrator nothing they need — the clock is the
    // whole point of publishing the registry at all. Asserted as *either* a
    // countdown or the honest "no sunset date set", because which one is true
    // is the deployment's business and inventing a date would be worse than
    // saying there is none.
    await expect(page.getByRole("heading", { name: /version registry/i })).toBeVisible();
    await expect(
      page.getByText(/until sunset|no sunset date set/i).first(),
      "the version registry lists versions without their clock",
    ).toBeVisible();
  });
});

/** Whitespace is a rendering detail; the row's words are the claim. */
function normalise(text: string): string {
  return text.replace(/\s+/gu, " ").trim();
}
