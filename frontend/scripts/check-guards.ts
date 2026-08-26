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
 * Ten bans, each with a written reason and an explicit allowlist. An exemption
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
/** `  type Complaint,` inside an import block. A named type import, not a
 *  declaration — and the two are indistinguishable to a line-oriented rule
 *  without this. */
const IMPORTED_TYPE = /^\s*type\s+\w+\s*(?:as\s+\w+\s*)?,?\s*$/;

/** `type X = components["schemas"][...]`, or the same over `paths` /
 *  `operations`. An alias of a generated type is a *name for* the contract,
 *  which is what execution-plan Law 2 asks for — the banned thing is a fresh
 *  shape, not a shorter way to spell the published one.
 *
 *  The right-hand side must be *only* the generated index expression, so
 *  `components["schemas"]["X"] & { extra: string }` still fails: widening a
 *  published contract locally is re-declaring it by another route. */
const ALIAS_OF_GENERATED =
  /^\s*(?:export\s+)?type\s+\w+\s*=\s*(?:components|paths|operations)(?:\s*\[[^\]]+\])+\s*;\s*$/;

/**
 * The shapes a timeline takes in a browser.
 *
 * Deliberately mechanical rather than semantic: a `play()` call, a raw frame
 * loop, a timer, a CSS animation or transition property, or a motion library.
 * Prose is unaffected — the docstrings in `src/ink/` say the words "animation"
 * and "duration" repeatedly, and every pattern here requires the punctuation
 * that turns a word into a call or a declaration.
 */
const TIMELINE =
  /\.play\s*\(|\brequestAnimationFrame\s*\(|\bsetInterval\s*\(|\bsetTimeout\s*\(|@keyframes|\banimation(?:-[a-z]+)?\s*:|\btransition(?:-[a-z]+)?\s*:|\bgsap\b|\bTween\b|@rive|\blottie\b/i;

/**
 * A line that is only a comment.
 *
 * The timeline ban is the one guard that has to know the difference, because
 * the modules it polices explain at length what they are *not* doing and would
 * otherwise fail on their own docstrings. A line made entirely of comment
 * cannot contain a call, so skipping it costs the guard nothing — and it is
 * this guard alone: a colour literal in a comment is still a colour somebody
 * will copy.
 */
const COMMENT_ONLY = /^\s*(?:\/\/|\/\*|\*)/;

/**
 * §E3.4's three named vocabularies, as usage patterns.
 *
 * > Colour, motion, and sound each carry exactly one meaning, or none. A
 * > vocabulary that means two things means nothing. Severity ink never
 * > decorates. **The stamp only confirms. Bloom only fires on the safety
 * > fail-safe.** Enforced in review, not by taste.
 *
 * F16's gate turns "enforced in review" into this: *"no second use of bloom,
 * the stamp, or a severity colour exists in `src/` — a usage grep with an
 * explicit allowlist"*. Each pattern below matches a **use**, and the guard's
 * `exemptPaths` is the allowlist — so adding a use means adding a file to a
 * list in this file, with a reason, in a diff somebody reviews.
 */
const BLOOM_USE = /\bbloom\b|\bBloom\b|setBloom|bloomUntilStep/;
const STAMP_CURVE = /--ease-stamp|\bease-stamp\b/;
const SEVERITY_INK = /--color-sev-[a-z]+-(?:ink|tint|glaze)\b/;

/**
 * Directories whose components are mounted by more than one surface.
 *
 * `sound/` is the unmute — the film and the console masthead. `components/` is
 * the §E26 contracts, which is the whole point of them. `clay/` and `press/`
 * render inside the story, the console and the proof routes. A surface's own
 * stylesheet is absent from this list on purpose: `public.css` is only ever on
 * paper and `console.css` only ever on the light table, so naming the paper
 * family there is a statement of fact rather than an assumption.
 */
const SHARED_ACROSS_GROUNDS = ["sound", "components", "clay", "press", "ink"] as const;

const CDN_HOST =
  /https?:\/\/(?:[a-z0-9-]+\.)*(?:cdn|jsdelivr|unpkg|cdnjs|googleapis|gstatic|cloudflare|bootstrapcdn|typekit|fontawesome)[a-z0-9.-]*\//i;

/**
 * The registry's **bundle** read, in either of the two spellings this
 * repository has used for it — the upstream path and the BFF proxy that used to
 * sit in front of it.
 *
 * Deliberately not every path under `translations/`.
 * `GET /control-plane/translations/coverage` is a different question — *how
 * much of the tenant's own taxonomy is translated* — it is the tenant-authored
 * half, it is read by the control-plane admin screen, and banning it would be
 * banning the mechanism this ADR keeps rather than the caller it removes.
 *
 * Matched as a *string*, so the ban catches the fetch and not a sentence about
 * it: `COMMENT_ONLY` filters the prose, and every occurrence in `src/` today is
 * inside a comment explaining why the fetch is gone.
 */
const TRANSLATIONS_BUNDLE_ENDPOINT =
  /control-plane\/translations\/\{namespace\}|["'`]\/api\/i18n\//;

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
    id: "no-character-timeline",
    title: "No timeline anywhere in the character layer",
    source: "§E8.1, ADR-0041, ADR-0048, F15",
    why:
      "§E8.1's claim is that the character is a set of named inputs over a state machine, " +
      "not playback — and ADR-0041 is explicit that `play()` on an event is indistinguishable, " +
      "in the code and to the viewer, from `play()` on a click. So the Phase 20 gate holds " +
      "only if there is no timeline in src/ink/ for a button to start. This is the grep F15's " +
      "gate names: a frame loop, a timer, a CSS animation or transition, or a motion library " +
      "inside the ink layer is the defect, and the figure's entire motion is a pure function " +
      "of the state machine and the 12 fps stepped clock.",
    exemptPaths: [],
    test: (line, filePath) =>
      filePath.startsWith("ink" + sep) && !COMMENT_ONLY.test(line) && TIMELINE.test(line),
  },
  {
    id: "single-meaning-bloom",
    title: "Bloom fires for the safety fail-safe and for nothing else",
    source: "§E3.4, §E7.3, §E25 Phase 20, F16",
    why:
      "§E7.3 reserves selective bloom exclusively for `safety_trigger_fired`, and §E3.4 is the " +
      "reason: a glow that also meant 'this is important' would stop meaning 'the fail-safe " +
      "fired', which is the one thing in this product that must be unmistakable. The allowlist " +
      "is the clay pipeline that implements the reservation — `lens.ts` makes it structural by " +
      "restricting the pass to SAFETY_LAYER — plus the tier flags that decide whether the node " +
      "is built at all.",
    exemptPaths: [
      {
        path: join("design", "generated"),
        because:
          "the token *definition*, written by the generator. Defining a vocabulary is not using " +
          "it, and a ban that fired on the definition would be a ban on the vocabulary existing",
      },
      { path: join("clay", "lens.ts"), because: "implements it, and restricts it to SAFETY_LAYER" },
      { path: join("clay", "scene.ts"), because: "builds and fires the pass" },
      { path: join("clay", "ClayScene.tsx"), because: "passes the fail-safe's step to the scene" },
      { path: join("clay", "live.ts"), because: "turns safety_trigger_fired into bloomUntilStep" },
      { path: join("clay", "tier.ts"), because: "§E13's capability flag per tier" },
      { path: join("clay", "quality.ts"), because: "the adaptive dial turns it off first" },
      {
        path: join("sound", "cues.ts"),
        because:
          "the struck note is the fail-safe's *sound* and names the same event; §E3.4 permits " +
          "one meaning in two channels — what it forbids is one channel with two meanings",
      },
    ],
    test: (line) => BLOOM_USE.test(line),
  },
  {
    id: "single-meaning-stamp",
    title: "The stamp curve confirms a decision and does nothing else",
    source: "§E3.4, §E11.1 motion 1, F16",
    why:
      "`--ease-stamp` is the only overshoot curve in the 2D product (§E11) and §E11.1 gives it " +
      "one job: 'confirmations land; they do not fade'. A second use makes the stamp mean " +
      "'something happened' instead of 'a decision was made', and a vocabulary that means two " +
      "things means nothing.",
    exemptPaths: [
      {
        path: join("design", "generated"),
        because:
          "the token *definition*, written by the generator. Defining a vocabulary is not using " +
          "it, and a ban that fired on the definition would be a ban on the vocabulary existing",
      },
      { path: join("components", "Stamp.tsx"), because: "the one confirmation primitive" },
      { path: join("components", "components.css"), because: "that primitive's own styles" },
      {
        path: join("story", "story.css"),
        because:
          "§E16 Act 6's merge ends with 'then the count stamps' — §E11.1's own sentence. Same " +
          "meaning (a decision was recorded), second call site, and the allowlist is where that " +
          "is said out loud",
      },
    ],
    test: (line) => STAMP_CURVE.test(line),
  },
  {
    id: "single-meaning-severity",
    title: "Severity ink is worn by severity and by nothing else",
    source: "§E3.4, §E9.4 rule 1, F16",
    why:
      "§E9.4 rule 1: severity ink never touches a non-severity element. It is the strongest " +
      "colour signal in the palette, and the moment a heading or a chart axis borrows it, a " +
      "reader can no longer tell a measurement from a decoration — which is §E3.3's failure " +
      "wearing §E3.4's clothes.",
    exemptPaths: [
      {
        path: join("design", "generated"),
        because:
          "the token *definition*, written by the generator. Defining a vocabulary is not using " +
          "it, and a ban that fired on the definition would be a ban on the vocabulary existing",
      },
      {
        path: join("components", "components.css"),
        because: "`<SeverityBadge>` and `<SeverityMark>`, which are what severity ink is for",
      },
    ],
    test: (line) => SEVERITY_INK.test(line),
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
    //
    // **Two false positives were found and fixed here rather than exempted**,
    // because an exemption comment on a correct line teaches the next reader
    // that the rule is approximate.
    //
    // 1. `import { type Complaint } from ...` matched, because a named type
    //    import inside a multi-line import block is a line beginning
    //    `  type Complaint,`. Importing a type is never declaring one.
    // 2. `export type Complaint = components["schemas"]["ComplaintResponse"]`
    //    matched. That is the opposite of the banned thing: it *names* a
    //    generated type so a surface can refer to it without spelling the index
    //    expression eleven times. `src/lib/realtime/envelope.ts` has done
    //    exactly this since M3 and escaped only because `RealtimeEnvelope` is
    //    not on the entity list — which is luck, not design.
    test: (line, filePath) =>
      !filePath.startsWith(join("generated") + sep) &&
      !IMPORTED_TYPE.test(line) &&
      !ALIAS_OF_GENERATED.test(line) &&
      /^\s*(?:export\s+)?(?:interface|type)\s+(?:Complaint|WorkOrder|Contractor|BudgetAllocation|Cluster|Policy|Simulation|Tenant|Review|Event)(?:Read|Response|Payload|Schema|Dto)?\b/.test(
        line,
      ),
  },
  {
    id: "no-product-copy-from-the-registry",
    title: "Product copy is never fetched from the locale registry",
    source: "§E10.1, §E3.3, ADR-0058, register row A17, F18",
    why:
      "`db/models/i18n.py` registers four namespaces - taxonomy, organisation, zone, calendar - " +
      "and refuses an import into any namespace this application renders copy from, because a " +
      "tenant able to edit the wording of a legal notice is not a localisation feature. The " +
      "reader does not validate its path parameter, so a request for `public` is answered " +
      "`200 {}` rather than refused: a tier that fetches it looks alive, resolves to nothing, " +
      "and cannot fail loudly enough for anyone to notice. It lived for three milestones on " +
      "exactly that. Phase 18's gate is met by the tenant-authored half and asserted over HTTP " +
      "by `nem gate-phase18-locale`; product copy is authored by NEMESIS and reviewed like code.",
    exemptPaths: [],
    // `generated/` is exempt by path rather than by allowlist entry: the client
    // is generated from `openapi.json` and *does* carry the operation, because the
    // backend still serves it. The ban is on this application calling it.
    test: (line, filePath) =>
      !filePath.startsWith(join("generated") + sep) &&
      !COMMENT_ONLY.test(line) &&
      TRANSLATIONS_BUNDLE_ENDPOINT.test(line),
  },
  {
    id: "ground-aware-tokens-in-shared-surfaces",
    title: "A component rendered on more than one ground uses the ground-aware token family",
    source: "§E9.3, §E22, §E24",
    why:
      "`--color-role-text-primary` is the paper ground's ink; `--role-text-primary` is whichever " +
      "ground the element is standing on, remapped by [data-surface='console'] and [data-ground]. " +
      "A component that renders on one surface may name either. A component that renders on " +
      "several must name the second, and `sound.css` named the first: the unmute button was " +
      "riso-black on mitti-950 in the console masthead - present, tabbable, correctly labelled, " +
      "and invisible on all twelve screens. `axe` reported it as *incomplete* rather than as a " +
      "violation, because the button's background is `none` and the scan cannot resolve what is " +
      "behind it, so nothing failed. This ban is that answer, written down.",
    exemptPaths: [
      {
        path: join("design", "generated"),
        because: "defines both families; the aliases are literally `--role-x: var(--color-role-x)`",
      },
    ],
    // Scoped to the directories whose components are mounted by more than one
    // surface. A surface's *own* stylesheet may name the paper family, because a
    // surface knows its ground: `public.css` is only ever on paper and
    // `console.css` only ever on the light table. This list is the set of
    // modules with no such guarantee.
    test: (line, filePath) =>
      SHARED_ACROSS_GROUNDS.some((dir) => filePath.startsWith(dir + sep)) &&
      !COMMENT_ONLY.test(line) &&
      /var\(\s*--color-role-/.test(line),
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
