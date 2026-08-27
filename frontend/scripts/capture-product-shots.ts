/**
 * Product screenshots — the whole journey, photographed from the running app.
 *
 * Not a gate. `tests/golden.spec.ts` and `tests/story.spec.ts` own the
 * pixel-exact regressions; this script exists to produce the frames the README
 * and the prototype deck show a reader, which is a different job with different
 * rules: real data, real journeys, and the surfaces a resident and a member of
 * staff actually walk through, in the order they walk through them.
 *
 *   node scripts/capture-product-shots.ts [--base URL] [--dev-base URL] [--out DIR] [--only substr]
 *
 * Both servers must already be running against a seeded backend
 * (`nem up`, then `nem seed-demo`), because a screenshot of an empty
 * deployment is a screenshot of nothing:
 *
 *   npm run build && npx next start --port 3210    # --base, the product
 *   npm run dev -- --port 3211                     # --dev-base, the roadmap screens
 *
 * The second server is not a workaround. §E24 routes a console screen whose
 * contract still returns nulls to a 404 in a production build, so the seven
 * roadmap screens genuinely do not exist at `--base` — and a deck that showed
 * them without saying so would be making the claim that route refuses to make.
 * They are captured from the dev build and labelled `roadmap` in the manifest.
 */
import { mkdir, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium, type Browser, type BrowserContext, type Page } from "@playwright/test";

const args = process.argv.slice(2);
const flag = (name: string, fallback: string): string => {
  const i = args.indexOf(`--${name}`);
  return i >= 0 ? (args[i + 1] ?? fallback) : fallback;
};

const BASE = flag("base", "http://127.0.0.1:3210");
const DEV_BASE = flag("dev-base", "http://127.0.0.1:3211");
const OUT = path.resolve(process.cwd(), flag("out", "../assets/screens"));

/** The tenant and the ids `nem seed-demo` publishes. */
const TENANT = "pune-demo";
const WARD = "W-KOTHRUD";
const ZONE = "Z-WEST";
const CONTRACTOR = "f5e3e2fb-82de-4b1d-93e5-344f2d82ffe9";

/** The fixture `tests/citizen.spec.ts` files its reports with — a decodable
 *  photograph, so the pipeline actually classifies rather than parking. */
const PHOTO = path.join(
  fileURLToPath(new URL(".", import.meta.url)),
  "..",
  "tests",
  "fixtures",
  "media",
  "pothole.jpg",
);

/** Kothrud, inside a real ward boundary in the seeded tenant. The Place card
 *  resolves through the same PostGIS query a resident's phone would. */
const WHERE = { latitude: 18.5074, longitude: 73.8077 };

const DESKTOP = { width: 1728, height: 1080 };
const PHONE = { width: 430, height: 932 };

type Surface = "desktop" | "phone";
type Availability = "live" | "roadmap";

interface Shot {
  readonly file: string;
  readonly url: string;
  readonly title: string;
  readonly caption: string;
  readonly viewport?: { width: number; height: number };
  readonly fullPage?: boolean;
  /** Extra settling for a surface that streams, animates, or draws to canvas. */
  readonly settle?: number;
  /** Served from the dev build: §E24 404s it in production, deliberately. */
  readonly roadmap?: boolean;
  readonly before?: (page: Page) => Promise<void>;
}

interface Frame {
  readonly file: string;
  readonly title: string;
  readonly caption: string;
  readonly url: string;
  readonly surface: Surface;
  readonly availability: Availability;
}

/** The first line of an error, for a log that reports a miss without a stack. */
function firstLine(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  return message.split(/\r?\n/)[0] ?? message;
}

/** Wait for the app to be visually done: network quiet, fonts in, canvases drawn. */
async function settle(page: Page, extra = 0): Promise<void> {
  await page.waitForLoadState("domcontentloaded");
  await page.waitForLoadState("networkidle", { timeout: 45_000 }).catch(() => undefined);
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(1200 + extra);
}

/**
 * Seek the landing film to a point on its spine.
 *
 * The film is scroll-driven through Lenis, and Lenis animates the real scroll
 * position rather than a transform — so setting the scroll position and letting
 * the damping converge lands the spine where a reader's wheel would have landed
 * it, with none of the wheel events a headless run cannot deliver.
 */
function seekAct(t: number) {
  return async (page: Page): Promise<void> => {
    await page.evaluate((progress) => {
      const doc = document.documentElement;
      const travel = doc.scrollHeight - window.innerHeight;
      window.scrollTo({ top: travel * progress, behavior: "instant" as ScrollBehavior });
    }, t);
    await page.waitForTimeout(2600);
  };
}

// ── The still surfaces ─────────────────────────────────────────────────────

const SHOTS: readonly Shot[] = [
  // The landing film — nine acts, §E16.
  {
    file: "01-landing-cold-open",
    url: "/",
    title: "Landing — cold open",
    caption:
      "The wordmark over a clay model of the city, lit by that city's real local time and its real weather.",
    settle: 3500,
  },
  {
    file: "02-landing-walk",
    url: "/",
    title: "Act 1 — the walk",
    caption: "A pothole gets reported. The app says “In Progress”. Weeks pass.",
    before: seekAct(0.12),
  },
  {
    file: "03-landing-stop",
    url: "/",
    title: "Act 2 — the stop",
    caption: "The camera drops to ankle height and the problem fills the lower third.",
    before: seekAct(0.25),
  },
  {
    file: "04-landing-silence",
    url: "/",
    title: "Act 3 — the silence",
    caption: "One ghost flag for every report that was never closed. They dim together.",
    before: seekAct(0.36),
  },
  {
    file: "05-landing-report",
    url: "/",
    title: "Act 4 — the report",
    caption: "The camera pushes through the phone into the real citizen app, running in the page.",
    before: seekAct(0.48),
  },
  {
    file: "06-landing-pipeline",
    url: "/",
    title: "Act 5 — the pipeline",
    caption:
      "Every gate stamps the card with what it found. A stamp is an event from the log, never a status flag.",
    before: seekAct(0.61),
  },
  {
    file: "07-landing-merge",
    url: "/",
    title: "Act 6 — the merge",
    caption:
      "Duplicate reports fuse into one cluster — and the scene waits for a real match rather than playing one.",
    before: seekAct(0.75),
  },
  {
    file: "08-landing-city-awake",
    url: "/",
    title: "Act 7 — the city, awake",
    caption: "The survey: every ward this deployment publishes, drawn from the live open-data API.",
    before: seekAct(0.86),
  },
  {
    file: "09-landing-table",
    url: "/",
    title: "Act 8 — the table",
    caption: "The model, photographed on the bench it was made on. The next frame is the console.",
    before: seekAct(0.94),
  },
  {
    file: "10-landing-receipts",
    url: "/",
    title: "Act 9 — the receipts",
    caption:
      "Deliberately boring: a live curl against the public API, and every claim carrying a status label.",
    // The receipts sit *below* the film, off the spine — §E16 calls them "below
    // fold" and gives them no `t`. Seeking the spine to 1 asks the renderer for
    // the last frame of the walk and the bottom of the document at once, which
    // is where a headless GPU process goes to die. The anchor is the honest
    // route to this act anyway: it is what the skip link at the top uses.
    before: async (page: Page): Promise<void> => {
      await page.evaluate(() => {
        document
          .querySelector("[data-act='receipts']")
          ?.scrollIntoView({ behavior: "instant" as ScrollBehavior, block: "start" });
      });
      await page.waitForTimeout(2600);
    },
  },

  // The resident's doors. The journey itself is scripted below.
  {
    file: "11-resident-home",
    url: "/citizen",
    title: "Resident — the three doors",
    caption:
      "Report a problem, follow a report by its receipt id, or read what the city publishes about itself.",
    settle: 1000,
  },

  // What the city publishes — the same pages a journalist reads.
  {
    file: "17-public-city",
    url: `/${TENANT}`,
    title: "Public — the city",
    caption:
      "The open-data portal: every zone and ward, with what is counted and what is withheld.",
    fullPage: true,
  },
  {
    file: "18-public-ward",
    url: `/${TENANT}/ward/${WARD}`,
    title: "Public — one ward",
    caption: "Kothrud, by category, with the suppression threshold stated rather than implied.",
    fullPage: true,
  },
  {
    file: "19-public-budget",
    url: `/${TENANT}/budget/${ZONE}?fiscal_year=2026`,
    title: "Public — the money",
    caption: "Allocated against spent for a zone, in a fiscal year a resident can name.",
    fullPage: true,
  },
  {
    file: "20-public-contractor",
    url: `/${TENANT}/contractor/${CONTRACTOR}`,
    title: "Public — the contractor ledger",
    caption:
      "Four independent metrics, published side by side. There is no overall score, because an average would hide the one that matters.",
    fullPage: true,
  },
  {
    file: "21-public-honesty",
    url: `/${TENANT}/honesty`,
    title: "Public — what is real",
    caption:
      "Every claim the product makes about itself, labelled REAL, SIMULATED, ROADMAP, CUT or REFRAMED.",
    fullPage: true,
  },

  // The staff journey.
  {
    file: "22-staff-home",
    url: "/staff",
    title: "Staff — the thirteen surfaces",
    caption:
      "The whole operating surface, with the ones that are not wired yet saying so on the tile.",
    fullPage: true,
  },
  {
    file: "23-console-command",
    url: "/console",
    title: "Console — command",
    caption: "What breaches first, the city as a clay model, and the queue underneath it.",
    settle: 3000,
  },
  {
    file: "24-console-review",
    url: "/console/review",
    title: "Console — the review queue",
    caption:
      "The reports the pipeline would not decide alone, ordered by priority then oldest first.",
    settle: 1500,
  },
  {
    file: "25-console-review-item",
    url: "/console/review",
    title: "Console — one decision",
    caption:
      "Why the gate stopped, the redacted photograph, the evidence, and the decisions a reviewer may take.",
    settle: 1500,
    before: async (page: Page): Promise<void> => {
      const row = page.locator(".review__row").first();
      if (await row.isVisible().catch(() => false)) {
        await row.click().catch(() => undefined);
        await page.waitForTimeout(2500);
      }
    },
  },
  {
    file: "26-console-palette",
    url: "/console",
    title: "Console — the command palette",
    caption: "Every screen one keystroke away. The console is a keyboard surface first.",
    settle: 2500,
    before: async (page: Page): Promise<void> => {
      await page.keyboard.press("Control+k").catch(() => undefined);
      await page.waitForTimeout(1200);
    },
  },
  {
    file: "27-console-policy",
    url: "/console/policy",
    title: "Console — the policy studio",
    caption: "Rules as documents, with a backtest before they bite.",
    fullPage: true,
  },
  {
    file: "28-console-control",
    url: "/console/control",
    title: "Console — the control plane",
    caption: "Taxonomy, zones, departments, calendars, locales and tenants.",
    fullPage: true,
  },
  {
    file: "29-console-developers",
    url: "/console/developers",
    title: "Console — the developer portal",
    caption: "Keys, webhooks, usage and the version clock.",
    fullPage: true,
  },

  // §E24's roadmap screens. Dev build only — a 404 in production, on purpose.
  {
    file: "30-console-area",
    url: "/console/area",
    title: "Console — area view",
    caption: "One ward over time, including what it is not telling us.",
    fullPage: true,
    roadmap: true,
  },
  {
    file: "31-console-work",
    url: "/console/work",
    title: "Console — work orders",
    caption: "Assignment, the contractor picker and the rate card.",
    fullPage: true,
    roadmap: true,
  },
  {
    file: "32-console-closure",
    url: "/console/closure",
    title: "Console — closure",
    caption: "Evidence or nothing. The conditions for closing a job are shown before they are hit.",
    fullPage: true,
    roadmap: true,
  },
  {
    file: "33-console-money",
    url: "/console/money",
    title: "Console — money",
    caption: "Allocated against spent, and what a citizen sees of it.",
    fullPage: true,
    roadmap: true,
  },
  {
    file: "34-console-integrity",
    url: "/console/integrity",
    title: "Console — integrity",
    caption: "Signals, case files, and the requirements a blacklist has to meet before it is one.",
    fullPage: true,
    roadmap: true,
  },
  {
    file: "35-console-reports",
    url: "/console/reports",
    title: "Console — the report builder",
    caption: "A document that carries its own proof.",
    fullPage: true,
    roadmap: true,
  },
  {
    file: "36-console-roles",
    url: "/console/roles",
    title: "Console — roles",
    caption: "What each role sees, and what it may do.",
    fullPage: true,
    roadmap: true,
  },

  // Outdoors, and the proof surfaces.
  {
    file: "37-field-app",
    url: "/field",
    title: "Field — the crew's phone",
    caption:
      "Capture and close jobs outdoors and offline. Installs to a phone; built for sunlight and gloves.",
    viewport: PHONE,
    fullPage: true,
  },
  {
    file: "38-developers-portal",
    url: "/developers",
    title: "Developers — the proof surfaces",
    caption:
      "The routes each rendering pipeline is photographed through, and the contracts it holds.",
    fullPage: true,
  },
];

// ── The resident's journey, driven end to end ──────────────────────────────

/**
 * File a real report and photograph every step of it.
 *
 * One page, one session, one complaint id that exists in the log afterwards —
 * because §E17's claim is a *chain*: submit, receipt, tracked, with a chain hash
 * on the receipt that matches the event the backend actually wrote. Five
 * screenshots stitched together from five separate page loads would look the
 * same and prove nothing, and the last frame would have no id to open.
 *
 * The camera is unavailable to this browser, which drives §E13's ladder rather
 * than working around it: the picker is a first-class capture path — on a phone
 * `capture="environment"` opens the camera — so this is the product, not a hook.
 */
async function residentJourney(browser: Browser): Promise<readonly Frame[]> {
  const context = await browser.newContext({
    viewport: PHONE,
    deviceScaleFactor: 3,
    isMobile: true,
    hasTouch: true,
    colorScheme: "light",
    locale: "en-IN",
    timezoneId: "Asia/Kolkata",
    permissions: ["geolocation"],
    geolocation: WHERE,
  });
  const page = await context.newPage();
  const frames: Frame[] = [];

  const shoot = async (file: string, title: string, caption: string): Promise<void> => {
    await page.screenshot({
      path: path.join(OUT, `${file}.png`),
      caret: "hide",
      scale: "device",
      timeout: 90_000,
    });
    frames.push({
      file: `${file}.png`,
      title,
      caption,
      url: "/report",
      surface: "phone",
      availability: "live",
    });
    console.log(`  ok  ${file}.png  (journey)`);
  };

  try {
    await page.goto(`${BASE}/report`, { waitUntil: "domcontentloaded", timeout: 90_000 });
    await settle(page);
    await shoot(
      "12-resident-capture",
      "Resident — step 1, capture",
      "Photograph it, say where, send. About thirty seconds, and the viewfinder is the first screen.",
    );

    await page.setInputFiles('input[type="file"]', PHOTO);
    await page.waitForTimeout(1500);
    await shoot(
      "13-resident-describe",
      "Resident — step 1, in your own words",
      "A four-second undo on the photograph, and one optional line. The flow completes if it is left empty.",
    );

    await page.getByRole("textbox").fill("Open drain by the school gate, water standing in it");
    await page.waitForTimeout(600);
    await page.getByRole("button", { name: /^send$/i }).click();

    await page
      .getByRole("heading", { name: "Where", exact: true })
      .waitFor({ timeout: 30_000 })
      .catch(() => undefined);
    await page.waitForTimeout(2500);
    await shoot(
      "14-resident-place",
      "Resident — step 2, where",
      "A card, not a picker. The ward resolves against the city's own zone tree — never a third-party geocoder.",
    );

    const send = page.getByRole("button", { name: /^send$/i });
    await send.waitFor({ timeout: 20_000 }).catch(() => undefined);
    await send.click().catch(() => undefined);

    const track = page.getByRole("link", { name: /follow what happens/i });
    await track.waitFor({ timeout: 40_000 }).catch(() => undefined);
    await page.waitForTimeout(2000);
    await shoot(
      "15-resident-receipt",
      "Resident — step 3, the receipt",
      "Filed. The receipt is a document, and its claim is a SHA-256 of the event the log actually wrote.",
    );

    const href = (await track.getAttribute("href").catch(() => null)) ?? "";
    const complaintId = href.replace("/t/", "");

    if (/^[0-9a-f-]{36}$/.test(complaintId)) {
      // The same report, on the desktop the resident opens the link on later.
      await page.setViewportSize(DESKTOP);
      await page.goto(`${BASE}/t/${complaintId}`, { waitUntil: "domcontentloaded" });
      await settle(page, 6000);
      await page.screenshot({
        path: path.join(OUT, "16-resident-track.png"),
        fullPage: true,
        caret: "hide",
        scale: "device",
        timeout: 90_000,
      });
      frames.push({
        file: "16-resident-track.png",
        title: "Resident — the evidence trail",
        caption:
          "The receipt id is the only way in — nobody can look the report up by name. Each gate it passed is an event, not a status.",
        url: `/t/${complaintId}`,
        surface: "desktop",
        availability: "live",
      });
      console.log(`  ok  16-resident-track.png  (journey, ${complaintId})`);
    } else {
      console.log("  XX  16-resident-track  no complaint id on the track link");
    }
  } catch (error) {
    console.log(`  XX  resident journey  ${firstLine(error)}`);
  } finally {
    await context.close();
  }
  return frames;
}

// ── The runner ─────────────────────────────────────────────────────────────

async function capture(browser: Browser, shot: Shot): Promise<Frame | null> {
  const base = shot.roadmap === true ? DEV_BASE : BASE;
  const phone = shot.viewport === PHONE;
  const context: BrowserContext = await browser.newContext({
    viewport: shot.viewport ?? DESKTOP,
    // The film measures its own frame rate and drops the press to two inks when
    // it cannot hold its budget — an honest banner, and the wrong picture of
    // the product on a capture run. 1.5x is the most this machine renders the
    // clay at without tripping it.
    deviceScaleFactor: phone ? 3 : 1.5,
    isMobile: phone,
    hasTouch: phone,
    colorScheme: "light",
    locale: "en-IN",
    timezoneId: "Asia/Kolkata",
  });
  const page = await context.newPage();
  try {
    const response = await page.goto(`${base}${shot.url}`, {
      waitUntil: "domcontentloaded",
      timeout: 120_000,
    });
    const status = response?.status() ?? 0;
    if (status >= 400) {
      console.log(`  XX  ${shot.file}  HTTP ${String(status)} at ${base}${shot.url}`);
      return null;
    }
    await settle(page, shot.settle ?? 0);
    if (shot.before !== undefined) await shot.before(page);
    await page.screenshot({
      path: path.join(OUT, `${shot.file}.png`),
      fullPage: shot.fullPage === true,
      // Not `animations: "disabled"`. Half these surfaces draw to a WebGL
      // canvas on a rAF loop that never idles, and Playwright's
      // wait-for-animations path never reaches a stable frame on one — the
      // golden-image specs freeze the world through the proof routes' own
      // `?step=` instead. Here a settled page is close enough, and a generous
      // timeout beats a missing frame.
      caret: "hide",
      scale: "device",
      timeout: 90_000,
    });
    console.log(`  ok  ${shot.file}.png  (${String(status)})  ${shot.url}`);
    return {
      file: `${shot.file}.png`,
      title: shot.title,
      caption: shot.caption,
      url: shot.url,
      surface: phone ? "phone" : "desktop",
      availability: shot.roadmap === true ? "roadmap" : "live",
    };
  } catch (error) {
    console.log(`  XX  ${shot.file}  ${firstLine(error)}`);
    return null;
  } finally {
    await context.close();
  }
}

async function main(): Promise<void> {
  const only = flag("only", "");
  const wanted = only === "" ? SHOTS : SHOTS.filter((s) => s.file.includes(only));
  const runJourney = only === "" || only.includes("resident") || only === "journey";
  if (only === "") await rm(OUT, { recursive: true, force: true });
  await mkdir(OUT, { recursive: true });

  console.log(
    `Photographing ${String(wanted.length)} surfaces from ${BASE} (roadmap from ${DEV_BASE})`,
  );
  // Headed, with the GPU left on.
  //
  // §E13's ladder is honest about a renderer it cannot get: a default headless
  // Chromium has no WebGL 2, so the landing film correctly falls back to Tier C
  // and prints "running in a reduced mode" across the shot. That frame is a
  // true picture of a machine without a GPU and a false picture of the product,
  // so the capture run gets a real one.
  const browser = await chromium.launch({
    headless: false,
    args: [
      "--use-angle=default",
      "--enable-unsafe-swiftshader",
      "--enable-gpu",
      "--ignore-gpu-blocklist",
      "--hide-scrollbars",
      "--mute-audio",
    ],
  });

  const frames: Frame[] = [];
  let failed = 0;
  try {
    for (const shot of wanted) {
      const frame = await capture(browser, shot);
      if (frame === null) failed += 1;
      else frames.push(frame);
    }
    if (runJourney) frames.push(...(await residentJourney(browser)));
  } finally {
    await browser.close();
  }

  // The manifest, so the README and the deck take their captions from the same
  // place the frames came from rather than from a second, drifting copy.
  frames.sort((a, b) => a.file.localeCompare(b.file));
  if (only === "") {
    await writeFile(path.join(OUT, "shots.json"), `${JSON.stringify(frames, null, 2)}\n`, "utf8");
  }

  console.log(`\nWrote ${String(frames.length)} frames to ${OUT}`);
  if (failed > 0) console.log(`${String(failed)} surface(s) did not photograph.`);
}

await main();
