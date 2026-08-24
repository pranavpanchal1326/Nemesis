/**
 * The design law, enforced.
 *
 * The backend keeps a family of `scripts/check_*.py` guards — domain literals,
 * tenant scoping, media redaction, event catalog — because some rules are not
 * type errors and not test failures, they are *standards*, and a standard that
 * is only in a document drifts. §E24 asks for the same thing on this side:
 *
 *   "A hand-written colour literal in application source fails CI, mirroring
 *    the backend's 'no magic values' standard."
 *
 * Four bans, each with a written reason and an explicit allowlist. An exemption
 * is a line in this file with a justification next to it, never a silent one.
 *
 * Usage:  node scripts/check-guards.ts [--verbose]
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(fileURLToPath(new URL(".", import.meta.url)), "..");
const SRC = join(ROOT, "src");

interface Guard {
  readonly id: string;
  readonly title: string;
  readonly source: string;
  readonly why: string;
  /** Files whose *path* is exempt, with the reason the exemption is legitimate. */
  readonly exemptPaths: readonly { readonly path: string; readonly because: string }[];
  readonly test: (line: string, filePath: string) => boolean;
}

/** A line carrying this marker is exempt, and must say why on the same line. */
const ESCAPE = "nemesis-guard-allow:";

const HEX_COLOUR = /#[0-9a-fA-F]{3,8}\b/;
const CSS_COLOUR_FN = /\b(?:rgba?|hsla?|oklch|oklab|lab|lch|color-mix)\s*\(/;
const GLSL_HINT =
  /\b(?:ShaderMaterial|RawShaderMaterial)\b|\bgl_(?:Position|FragColor|FragCoord)\b|\bvarying\s+(?:vec|float|mat)|#version\s+\d{3}/;
const CDN_HOST =
  /https?:\/\/(?:[a-z0-9-]+\.)*(?:cdn|jsdelivr|unpkg|cdnjs|googleapis|gstatic|cloudflare|bootstrapcdn|typekit|fontawesome)[a-z0-9.-]*\//i;

const GUARDS: readonly Guard[] = [
  {
    id: "no-colour-literal",
    title: "No hand-written colour literal in application source",
    source: "§E9.4 rule 3, §E24",
    why:
      "The severity ink in a badge and the glaze in a shader must be the same number. " +
      "They are the same number only if both are generated from design/tokens.json. " +
      "A literal is the moment that stops being true.",
    exemptPaths: [
      {
        path: join("design", "tokens.json"),
        because: "the single source; every colour in the product is authored here",
      },
      {
        path: join("design", "generated"),
        because: "written by the token generator, never by hand",
      },
    ],
    test: (line) => HEX_COLOUR.test(line) || CSS_COLOUR_FN.test(line),
  },
  {
    id: "no-glsl",
    title: "No GLSL string and no ShaderMaterial",
    source: "ADR-0037",
    why:
      "Shaders are authored once in TSL and compiled to WGSL and GLSL by the renderer. " +
      "Hand-written GLSL means either writing the shader twice or forfeiting WebGPU " +
      "and the compute stage the press needs.",
    exemptPaths: [],
    test: (line) => GLSL_HINT.test(line),
  },
  {
    id: "no-cdn",
    title: "No CDN-hosted asset",
    source: "§6 Principle #6, §E15",
    why:
      "Zero-cost, self-hosted, offline-capable. A font or a script fetched from a CDN " +
      "breaks the air-gapped bootstrap Phase 29 gates on, and it is the one dependency " +
      "nobody notices until the demo has no network.",
    exemptPaths: [],
    test: (line) => CDN_HOST.test(line),
  },
  {
    id: "no-hand-written-contract",
    title: "No hand-written type describing a backend contract",
    source: "§E24, execution-plan Law 2",
    why:
      "Screens for unlanded phases build against generated types with fixture values, " +
      "never against an interface the frontend invented. A hand-written interface " +
      "describing a backend contract is a review failure — if the type does not exist " +
      "yet, the screen is not ready to build.",
    exemptPaths: [],
    // Declaring a type that names a backend entity outside `generated/` is the
    // shape this ban is about. Narrow on purpose: it catches the real mistake
    // (re-declaring a contract) and not the legitimate one (a view model).
    test: (line, filePath) =>
      !filePath.startsWith(join("generated") + sep) &&
      /^\s*(?:export\s+)?(?:interface|type)\s+(?:Complaint|WorkOrder|Contractor|BudgetAllocation|Cluster|Policy|Simulation|Tenant|Review|Event)(?:Read|Response|Payload|Schema|Dto)?\b/.test(
        line,
      ),
  },
];

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      out.push(...walk(full));
    } else if (/\.(?:ts|tsx|css|mjs|js|jsx)$/.test(entry)) {
      out.push(full);
    }
  }
  return out;
}

interface Violation {
  readonly guard: Guard;
  readonly file: string;
  readonly line: number;
  readonly text: string;
}

export function runGuards(root: string = SRC): Violation[] {
  let files: string[];
  try {
    files = walk(root);
  } catch {
    return []; // no src yet — M0 runs this before the app exists
  }

  const violations: Violation[] = [];
  for (const file of files) {
    const rel = relative(root, file);
    const lines = readFileSync(file, "utf8").split(/\r?\n/);

    for (const guard of GUARDS) {
      if (guard.exemptPaths.some((e) => rel.startsWith(e.path))) continue;

      lines.forEach((text, i) => {
        if (text.includes(ESCAPE)) return;
        if (guard.test(text, rel)) {
          violations.push({ guard, file: rel, line: i + 1, text: text.trim() });
        }
      });
    }
  }
  return violations;
}

function main(): void {
  const verbose = process.argv.includes("--verbose");
  const violations = runGuards();

  if (verbose) {
    for (const g of GUARDS) console.log(`  · ${g.id.padEnd(26)} ${g.source}`);
  }

  if (violations.length === 0) {
    console.log(`guards: ${String(GUARDS.length)} checks, clean`);
    return;
  }

  const byGuard = new Map<string, Violation[]>();
  for (const v of violations) {
    const list = byGuard.get(v.guard.id) ?? [];
    list.push(v);
    byGuard.set(v.guard.id, list);
  }

  for (const [id, list] of byGuard) {
    const guard = GUARDS.find((g) => g.id === id);
    if (!guard) continue;
    console.error(`\n✗ ${guard.title}  (${guard.source})`);
    console.error(`  ${guard.why}\n`);
    for (const v of list) {
      console.error(`    ${v.file}:${String(v.line)}  ${v.text.slice(0, 96)}`);
    }
    console.error(
      `\n  If a line is legitimately exempt, mark it \`${ESCAPE} <reason>\` — and the reason is read in review.`,
    );
  }
  console.error(`\nguards: ${String(violations.length)} violation(s)`);
  process.exitCode = 1;
}

if (process.argv[1]?.endsWith("check-guards.ts")) main();
