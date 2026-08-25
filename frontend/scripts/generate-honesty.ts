/**
 * §E16.2 — publish the honesty table, from the document that owns it.
 *
 * > Act 9 renders §44 on the marketing surface. Every competitor overclaims;
 * > §6 Principle #8 says this is a competitive advantage rather than a
 * > limitation, and Act 9 is where that belief is actually tested in public.
 *
 * §E18 adds the constraint that decides how this is built: the table is
 * published **as data, not as prose**. So it is generated rather than
 * transcribed, from the two markdown tables that are its source of truth:
 *
 *   NEMESIS-Blueprint-v2.md      §44  — the whole system, one status column
 *   NEMESIS-Frontend-Blueprint.md §E28 — the surfaces, two status columns
 *
 * **Why generate rather than hand-author.** §E28 says it about itself: *"an
 * honesty table that is wrong is worse than no table, because it is the
 * artefact a reader trusts instead of checking."* A hand-kept copy of §44 in
 * `src/` is a second honesty table, and two honesty tables disagreeing is the
 * precise failure the whole discipline exists to prevent. `--check` fails CI on
 * drift, exactly as the token pipeline and the generated client do — so editing
 * the blueprint and forgetting the surface is a build error rather than a
 * published lie.
 *
 * Usage:
 *   node scripts/generate-honesty.ts            write
 *   node scripts/generate-honesty.ts --check    fail if the output is stale
 */

import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { format } from "prettier";

const ROOT = join(fileURLToPath(new URL(".", import.meta.url)), "..");
const REPO = join(ROOT, "..");
const SYSTEM_SOURCE = join(REPO, "NEMESIS-Blueprint-v2.md");
const SURFACE_SOURCE = join(REPO, "NEMESIS-Frontend-Blueprint.md");
const OUT = join(ROOT, "src", "public", "generated", "honesty.ts");

/**
 * The labels §44 and §E28 actually use.
 *
 * **Five, not three, and the parser is how that was discovered.** The execution
 * plan's §0 says the vocabulary is *"REAL / SIMULATED / ROADMAP, used exactly
 * as §44 and §E28 use them"*. §44 also uses `CUT PERMANENTLY (for MVP)` for the
 * fine-tuned YOLO row and — the one nobody had written down — **`REFRAMED`**,
 * for *"Intake/Classification/Ops as 'agents' → REFRAMED, deterministic
 * pipeline stages"*.
 *
 * `REFRAMED` is not a synonym for anything else on this list. The other four
 * say how far a claim got; `REFRAMED` says the claim was **withdrawn and
 * renamed** — v1.0 called three pipeline stages "agents" and v2.0 stopped. That
 * is the single most honest row in the table and it would have been the one
 * quietly dropped by a parser that mapped unknown labels onto `ROADMAP`.
 *
 * So it is a first-class label here, and an unrecognised status is still a hard
 * failure: a sixth label is a change to the vocabulary, taken deliberately,
 * not a row this parser should shrug at.
 */
const STATUSES = ["REAL", "SIMULATED", "ROADMAP", "CUT", "REFRAMED"] as const;
type Status = (typeof STATUSES)[number];

interface ParsedRow {
  readonly cells: readonly string[];
}

/** Pull the markdown table that follows a heading, as rows of raw cells. */
function tableUnder(markdown: string, heading: string): readonly ParsedRow[] {
  const start = markdown.indexOf(heading);
  if (start < 0) throw new Error(`generate-honesty: no heading ${heading}`);

  const lines = markdown.slice(start).split(/\r?\n/);
  const rows: ParsedRow[] = [];
  let inTable = false;

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed.startsWith("|")) {
      // A blank line inside a table does not happen in GFM, so the first
      // non-row line after the table started is the end of it. Stopping there
      // rather than scanning to the next heading is what keeps a second table
      // further down the section from being silently appended to this one.
      if (inTable) break;
      continue;
    }
    if (/^\|[\s:|-]+\|$/.test(trimmed)) {
      inTable = true;
      continue;
    }
    const cells = trimmed
      .slice(1, -1)
      .split("|")
      .map((cell) => cell.trim());
    if (inTable) rows.push({ cells });
  }

  if (rows.length === 0) throw new Error(`generate-honesty: ${heading} has no table rows`);
  return rows;
}

/**
 * Strip markdown emphasis and the `~~struck~~` form, keeping the words.
 *
 * The source tables carry `**REAL**`, `~~Closed~~` and inline links, because
 * they are documents. The published data carries the sentence.
 */
function plain(cell: string): string {
  return (
    cell
      .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
      // `*`, `~` and the backtick — and **not** `_`. Stripping underscores
      // turned `pending_classification` into `pendingclassification` and
      // `exif_check_completed` into one word, on the page whose whole job is to
      // be checkable against its source. GFM does not read an intra-word
      // underscore as emphasis either, so removing it was never right.
      .replace(/[*`~]/g, "")
      .replace(/\s+/g, " ")
      .trim()
  );
}

/**
 * The status a cell asserts, and the sentence that qualifies it.
 *
 * §44 writes things like *"REAL (demo-scale, 3 seeded accounts)"* and
 * *"CUT PERMANENTLY (for MVP)"*, and §E28 writes *"ROADMAP — no capture surface
 * exists"*. The label and the caveat are two different facts and the caveat is
 * the half that does the work, so they are split rather than concatenated into
 * a string a consumer has to parse again.
 */
function readStatus(cell: string): { status: Status | null; note: string } {
  const text = plain(cell);
  if (text === "" || text === "—" || text === "-") return { status: null, note: "" };

  const label = STATUSES.find((candidate) => text.toUpperCase().startsWith(candidate));
  if (label === undefined) {
    throw new Error(
      `generate-honesty: '${text}' is not one of ${STATUSES.join(" / ")}. §E1 fixes this ` +
        `vocabulary; a new label is a decision, not a parse failure to route around.`,
    );
  }

  const rest = text
    .slice(label.length)
    .replace(/^[\s—–\-(:]+/, "")
    .replace(/\)$/, "")
    .trim();
  return { status: label, note: rest };
}

interface SystemRow {
  readonly capability: string;
  readonly status: Status | null;
  readonly note: string;
  readonly section: string;
  readonly why: string;
}

interface SurfaceRow {
  readonly capability: string;
  readonly component: Status | null;
  readonly componentNote: string;
  readonly data: Status | null;
  readonly dataNote: string;
  readonly closesAt: string;
}

function systemRows(): readonly SystemRow[] {
  const markdown = readFileSync(SYSTEM_SOURCE, "utf8");
  return tableUnder(markdown, "## 44. Appendix C").map(({ cells }) => {
    const [capability = "", status = "", section = "", why = ""] = cells;
    const read = readStatus(status);
    return {
      capability: plain(capability),
      status: read.status,
      note: read.note,
      section: plain(section),
      why: plain(why),
    };
  });
}

function surfaceRows(): readonly SurfaceRow[] {
  const markdown = readFileSync(SURFACE_SOURCE, "utf8");
  return tableUnder(markdown, "## E28. Appendix C").map(({ cells }) => {
    const [capability = "", component = "", data = "", closesAt = ""] = cells;
    const builtRead = readStatus(component);
    const dataRead = readStatus(data);
    return {
      capability: plain(capability),
      component: builtRead.status,
      componentNote: builtRead.note,
      data: dataRead.status,
      dataNote: dataRead.note,
      closesAt: plain(closesAt),
    };
  });
}

function build(): string {
  const system = systemRows();
  const surface = surfaceRows();

  return `/* GENERATED by scripts/generate-honesty.ts from the two blueprints. Do not edit.
   §E16.2, §E18 — §44 and §E28 published as data rather than as prose. Editing
   this file makes the published table disagree with the document that owns it,
   which is the one failure an honesty table cannot survive. Edit the blueprint
   and re-run \`npm run honesty\`. */

/** The vocabulary §44 and §E28 actually use. A sixth label is a decision. */
export const HONESTY_STATUSES = ${JSON.stringify(STATUSES)} as const;
export type HonestyStatus = (typeof HONESTY_STATUSES)[number];

/** One row of §44 — the whole system, judged once. */
export interface SystemClaim {
  readonly capability: string;
  /** \`null\` where the source table states no label. */
  readonly status: HonestyStatus | null;
  /** The caveat beside the label. Usually the half that matters. */
  readonly note: string;
  /** The blueprint section that argues it. */
  readonly section: string;
  readonly why: string;
}

/**
 * One row of §E28 — the surfaces, judged twice.
 *
 * Two columns because the single column was answering two questions at once:
 * *does real backend stand behind this?* and *has Track E built it?* A row is
 * only finished when both read REAL.
 */
export interface SurfaceClaim {
  readonly capability: string;
  readonly component: HonestyStatus | null;
  readonly componentNote: string;
  readonly data: HonestyStatus | null;
  readonly dataNote: string;
  /** The milestone that closes the component, or the phase that closes the data. */
  readonly closesAt: string;
}

export const SYSTEM_CLAIMS: readonly SystemClaim[] = ${JSON.stringify(system, null, 2)};

export const SURFACE_CLAIMS: readonly SurfaceClaim[] = ${JSON.stringify(surface, null, 2)};

/** Counted here so a surface can state the shape of the table without walking
 *  it twice, and so a test can assert the table is not silently empty. */
export const HONESTY_COUNTS = {
  system: ${String(system.length)},
  surface: ${String(surface.length)},
  systemReal: ${String(system.filter((row) => row.status === "REAL").length)},
  surfaceFinished: ${String(
    surface.filter((row) => row.component === "REAL" && row.data === "REAL").length,
  )},
} as const;

/** When the source documents were last read. Published beside the table because
 *  a status claim with no date is a status claim about an unknown moment. */
export const HONESTY_SOURCES = [
  "NEMESIS-Blueprint-v2.md §44",
  "NEMESIS-Frontend-Blueprint.md §E28",
] as const;
`;
}

async function main(): Promise<void> {
  const check = process.argv.includes("--check");
  const body = await format(build(), { parser: "typescript", printWidth: 100 });

  if (!check) {
    writeFileSync(OUT, body, "utf8");
    console.log(`  wrote ${OUT.replace(ROOT, ".")}`);
    return;
  }

  let current: string | null = null;
  try {
    current = readFileSync(OUT, "utf8");
  } catch {
    current = null;
  }
  if (current === body) {
    console.log("honesty: the published table matches §44 and §E28");
    return;
  }
  console.error(
    `✗ stale: ${OUT.replace(ROOT, ".")}\n\n` +
      `honesty: the published table no longer matches the blueprints. Run \`npm run honesty\`.\n` +
      `§E28 — an honesty table that is wrong is worse than no table, because it is the\n` +
      `artefact a reader trusts instead of checking.`,
  );
  process.exitCode = 1;
}

if (process.argv[1]?.endsWith("generate-honesty.ts")) await main();
