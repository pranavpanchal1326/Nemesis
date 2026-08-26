/**
 * The Storybook diff — A9, §E24.
 *
 * > **Every visual PR posts its Storybook diff and a five-second scene
 * > capture.**
 *
 * CI already builds the catalogue and uploads it, which means a reviewer *can*
 * browse it. A9's complaint is the gap between that and the sentence above: a
 * pull request does not show what changed, so a visual change ships whenever
 * nobody happens to click through forty stories looking for it.
 *
 * This renders every story in two builds — the base ref's and this branch's —
 * at a fixed viewport with animations disabled, compares them pixel for pixel,
 * and writes a Markdown report CI posts as a comment. Stories that appeared,
 * disappeared, or changed are named. Stories that did not are counted and not
 * listed, because a report nobody reads is the state A9 already describes.
 *
 * **No new dependency.** `playwright`, `pixelmatch` and `pngjs` are already in
 * this workspace for the press's byte-identity gates, and the whole comparison
 * is the one those gates already perform. A hosted service would answer the
 * same question and would put this project's design review behind somebody
 * else's account, which §6 Principle #6 rules out for assets and which is a
 * worse trade for a review process.
 *
 * Usage:
 *
 *     node scripts/storybook-diff.ts <base-static-dir> <head-static-dir> [out-dir]
 *
 * Exit code is 0 whether or not anything changed: this reports, it does not
 * gate. The gate is `tests/golden.spec.ts`, which binds to committed baselines
 * — the difference matters, because a report that fails the build is a report
 * people learn to route around.
 */

import { createReadStream, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createServer, type Server } from "node:http";
import { extname, join, normalize, resolve } from "node:path";

import pixelmatch from "pixelmatch";
import { PNG } from "pngjs";
import { chromium, type Browser } from "@playwright/test";

/** Fixed, and the same one `playwright.config.ts` uses. A story rendered at a
 *  different width is a different story. */
const VIEWPORT = { width: 1280, height: 800 } as const;

/** Zero tolerance, as everywhere else in this project — but see the note in the
 *  report: a *reported* difference is not automatically a defect. */
const THRESHOLD = 0;

const MIME: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".svg": "image/svg+xml",
  ".woff2": "font/woff2",
  ".ttf": "font/ttf",
  ".ico": "image/x-icon",
  ".map": "application/json; charset=utf-8",
};

interface StoryIndex {
  readonly entries: Record<
    string,
    { readonly id: string; readonly title: string; readonly name: string }
  >;
}

/**
 * Serve a built catalogue over HTTP on an ephemeral port.
 *
 * `file://` would be simpler and does not work: Storybook's iframe loads ES
 * modules, and a module served from `file://` is blocked by the origin rules
 * every browser applies. Twelve lines of `node:http` is the honest cost of
 * rendering the thing the way a reviewer sees it.
 */
function serve(root: string): Promise<{ origin: string; close: () => Promise<void> }> {
  const absolute = resolve(root);
  const server: Server = createServer((request, response) => {
    const path = decodeURIComponent((request.url ?? "/").split("?")[0] ?? "/");
    // `normalize` then a prefix check: a catalogue is untrusted build output and
    // `..` in a URL must not reach outside it, even on a throwaway server.
    const target = normalize(join(absolute, path === "/" ? "index.html" : path));
    if (!target.startsWith(absolute) || !existsSync(target)) {
      response.writeHead(404).end("not found");
      return;
    }
    response.writeHead(200, {
      "content-type": MIME[extname(target)] ?? "application/octet-stream",
    });
    createReadStream(target).pipe(response);
  });

  return new Promise((resolveServer) => {
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = typeof address === "object" && address !== null ? address.port : 0;
      resolveServer({
        origin: `http://127.0.0.1:${String(port)}`,
        close: () =>
          new Promise((done) => {
            server.close(() => {
              done();
            });
          }),
      });
    });
  });
}

function readIndex(root: string): StoryIndex {
  const path = join(root, "index.json");
  if (!existsSync(path)) {
    throw new Error(
      `${root} is not a built catalogue — no index.json. Run \`npm run storybook:build\`.`,
    );
  }
  return JSON.parse(readFileSync(path, "utf8")) as StoryIndex;
}

async function shoot(browser: Browser, origin: string, storyId: string): Promise<Buffer> {
  const page = await browser.newPage({ viewport: { ...VIEWPORT }, deviceScaleFactor: 1 });
  try {
    await page.goto(`${origin}/iframe.html?id=${encodeURIComponent(storyId)}&viewMode=story`, {
      waitUntil: "networkidle",
    });
    await page.evaluate(async () => {
      await document.fonts.ready;
    });
    return await page.screenshot({ animations: "disabled", fullPage: true });
  } finally {
    await page.close();
  }
}

function compare(before: Buffer, after: Buffer): { changed: number; diff: Buffer | null } {
  const a = PNG.sync.read(before);
  const b = PNG.sync.read(after);
  if (a.width !== b.width || a.height !== b.height) {
    // A different canvas size is a change by definition, and pixelmatch cannot
    // compare it. Reporting the dimensions is more use to a reviewer than a
    // diff image of two things that do not line up.
    return { changed: -1, diff: null };
  }
  const diff = new PNG({ width: a.width, height: a.height });
  const changed = pixelmatch(a.data, b.data, diff.data, a.width, a.height, {
    threshold: 0.05,
    includeAA: false,
  });
  return { changed, diff: changed > THRESHOLD ? PNG.sync.write(diff) : null };
}

async function main(): Promise<number> {
  const [baseDir, headDir, outDir = "storybook-diff"] = process.argv.slice(2);
  if (baseDir === undefined || headDir === undefined) {
    console.error("usage: node scripts/storybook-diff.ts <base-static> <head-static> [out-dir]");
    return 2;
  }

  const baseIndex = readIndex(baseDir);
  const headIndex = readIndex(headDir);
  mkdirSync(outDir, { recursive: true });

  const baseServer = await serve(baseDir);
  const headServer = await serve(headDir);
  const browser = await chromium.launch();

  const added: string[] = [];
  const removed: string[] = [];
  const changedStories: { id: string; label: string; pixels: number }[] = [];
  let identical = 0;

  try {
    for (const [id, entry] of Object.entries(headIndex.entries)) {
      const label = `${entry.title} › ${entry.name}`;
      if (!(id in baseIndex.entries)) {
        added.push(label);
        continue;
      }
      const [before, after] = await Promise.all([
        shoot(browser, baseServer.origin, id),
        shoot(browser, headServer.origin, id),
      ]);
      const { changed, diff } = compare(before, after);
      if (changed === 0) {
        identical += 1;
        continue;
      }
      changedStories.push({ id, label, pixels: changed });
      writeFileSync(join(outDir, `${id}.before.png`), before);
      writeFileSync(join(outDir, `${id}.after.png`), after);
      if (diff !== null) writeFileSync(join(outDir, `${id}.diff.png`), diff);
    }
    for (const [id, entry] of Object.entries(baseIndex.entries)) {
      if (!(id in headIndex.entries)) removed.push(`${entry.title} › ${entry.name}`);
    }
  } finally {
    await browser.close();
    await baseServer.close();
    await headServer.close();
  }

  const lines: string[] = ["### Storybook diff — §E24", ""];
  if (added.length === 0 && removed.length === 0 && changedStories.length === 0) {
    const plural = identical === 1 ? "story" : "stories";
    lines.push(`No visual change. ${String(identical)} ${plural} rendered identically.`);
  } else {
    lines.push(
      `${String(changedStories.length)} changed · ${String(added.length)} added · ` +
        `${String(removed.length)} removed · ${String(identical)} unchanged`,
      "",
    );
    if (changedStories.length > 0) {
      lines.push("| Story | Pixels changed |", "|---|---|");
      for (const story of changedStories) {
        lines.push(
          `| ${story.label} | ${story.pixels === -1 ? "resized" : String(story.pixels)} |`,
        );
      }
      lines.push("");
    }
    for (const [heading, list] of [
      ["Added", added],
      ["Removed", removed],
    ] as const) {
      if (list.length > 0) lines.push(`**${heading}:** ${list.join(", ")}`, "");
    }
    lines.push(
      "Before/after/diff images are in the `storybook-diff` artefact on this run.",
      "",
      "A reported change is not automatically a defect — it is a change somebody",
      "should have intended. The gate that fails on an *unintended* one is",
      "`tests/golden.spec.ts`, which compares against committed baselines.",
    );
  }

  const report = lines.join("\n") + "\n";
  writeFileSync(join(outDir, "report.md"), report);
  console.log(report);
  return 0;
}

main().then(
  (code) => {
    process.exitCode = code;
  },
  (error: unknown) => {
    console.error(error);
    process.exitCode = 1;
  },
);
