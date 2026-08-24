/**
 * The token pipeline — §E24, and the delivery of §19.3's promise that severity
 * colour is "defined once".
 *
 * One source (`src/design/tokens.json`) generates two artefacts:
 *
 *   src/design/generated/tokens.css   the 2D layer: Tailwind v4 `@theme` plus
 *                                     the type scale, per script
 *   src/design/generated/tokens.ts    the shader layer and application code:
 *                                     typed constants, and the glaze as the
 *                                     linear-sRGB triple a TSL uniform wants
 *
 * The badge and the shader are literally the same number because both descend
 * from the same line of JSON. `scripts/check-guards.ts` is what keeps that true
 * — a colour literal anywhere else in `src/` fails the build.
 *
 * Usage:
 *   node scripts/generate-tokens.ts            write
 *   node scripts/generate-tokens.ts --check    fail if the outputs are stale
 */

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { format } from "prettier";

const ROOT = join(fileURLToPath(new URL(".", import.meta.url)), "..");
const SOURCE = join(ROOT, "src", "design", "tokens.json");
const OUT_DIR = join(ROOT, "src", "design", "generated");

// --------------------------------------------------------------------------
// The shape of the source, declared so a typo in tokens.json is a type error
// here rather than an undefined in a stylesheet.
// --------------------------------------------------------------------------

interface Swatch {
  readonly value: string;
  readonly note?: string;
}
interface SeverityRow {
  readonly ink: string;
  readonly tint: string;
  readonly glaze: string;
  readonly inkPass: string;
  readonly shape: string;
  readonly label: string;
  readonly order: number;
}
interface TypeStep {
  readonly size: string;
  readonly family: string;
  readonly weight: number;
  readonly tracking: string;
  readonly leading: number;
  readonly transform: string;
  readonly rotate?: string;
  readonly note?: string;
}
interface FamilyDef {
  readonly stack: readonly string[];
  readonly role: string;
}
/**
 * A semantic role is either a reference to an authored token or an
 * **overprint** of two — §E6.3's "two inks overlapping create a third colour by
 * multiply", used here as the derivation that makes an ink text-safe without
 * repainting the Riso palette the whole direction rests on.
 */
type RoleValue =
  | { readonly ref: string; readonly min?: number; readonly note?: string }
  | { readonly overprint: readonly [string, string]; readonly min?: number; readonly note?: string };

interface RoleTheme {
  readonly $grounds: readonly string[];
  readonly [role: string]: RoleValue | readonly string[] | undefined;
}

interface Tokens {
  readonly paper: Record<string, Swatch | string>;
  readonly ink: Record<string, Swatch | string>;
  readonly severity: Record<string, SeverityRow | string>;
  readonly role: {
    readonly light: RoleTheme;
    readonly dark: RoleTheme;
    readonly severity: {
      readonly light: { readonly text: string; readonly field: string; readonly min: number };
      readonly dark: { readonly text: string; readonly field: string; readonly min: number };
    };
  };
  readonly inkSet: Record<string, { stock: string; inks: readonly string[] } | string>;
  readonly press: {
    readonly screenAngles: { readonly value: readonly number[] };
    readonly halftone: Record<string, number | string>;
    readonly misregistration: Record<string, number | string>;
    readonly inkDensity: Record<string, number | string>;
    readonly paperGrain: Record<string, number | string>;
    readonly quality: Record<
      string,
      { readonly inks: number; readonly halftone: string; readonly misregistration: string }
    >;
  };
  readonly motion: {
    readonly step: { readonly ms: number };
    readonly easing: Record<string, Swatch | string>;
    readonly duration: Record<string, { readonly ms: number; readonly steps: number } | string>;
    readonly reducedMotion: { readonly ms: number };
  };
  readonly type: {
    readonly family: Record<string, FamilyDef | string>;
    readonly scale: {
      readonly latin: Record<string, TypeStep | string>;
      readonly devanagari: {
        readonly $leadingDelta: number;
        readonly $familyMap: Record<string, string>;
      };
    };
    readonly measure: { readonly ch: number };
    readonly numeric: { readonly value: string };
  };
  readonly density: Record<string, Record<string, number | string>>;
  readonly layer: Record<string, number | string>;
}

/** `$note` keys document the source; they are never emitted. */
function entries<T>(record: Record<string, T | string>): [string, T][] {
  return Object.entries(record).filter(
    (entry): entry is [string, T] => !entry[0].startsWith("$") && typeof entry[1] !== "string",
  );
}

function numbers(record: Record<string, number | string>): [string, number][] {
  return Object.entries(record).filter(
    (entry): entry is [string, number] => !entry[0].startsWith("$") && typeof entry[1] === "number",
  );
}

// --------------------------------------------------------------------------
// Colour. The shader needs linear-sRGB; CSS needs the hex it was authored in.
// Both come from the same string, converted here once, rather than a designer
// value and a "close enough" shader constant drifting apart.
// --------------------------------------------------------------------------

function srgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  const to = (i: number) => parseInt(h.slice(i, i + 2), 16) / 255;
  return [to(0), to(2), to(4)];
}

function linear(hex: string): [number, number, number] {
  const f = (c: number) => (c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
  const [r, g, b] = srgb(hex);
  return [f(r), f(g), f(b)];
}

function hexNumber(hex: string): number {
  return parseInt(hex.replace("#", ""), 16);
}

/**
 * §E6.3 — overprint. Two inks on one sheet multiply; the result is a genuine
 * third colour, not a blend chosen by a designer. This is the same operation
 * the press performs in `press-tsl.ts` stage 5, applied at token-generation
 * time so a text-safe ink is *derived from the press* rather than invented.
 */
export function overprint(a: string, b: string): string {
  const channel = (i: number) => {
    const x = parseInt(a.replace("#", "").slice(i, i + 2), 16);
    const y = parseInt(b.replace("#", "").slice(i, i + 2), 16);
    return Math.round((x * y) / 255)
      .toString(16)
      .padStart(2, "0")
      .toUpperCase();
  };
  return `#${channel(0)}${channel(2)}${channel(4)}`;
}

/** Resolve a `paper.x` / `ink.y` / `severity.z.channel` path to its hex. */
function lookup(t: Tokens, path: string): string {
  const [group, name, channel] = path.split(".");
  if (group === "paper" && name !== undefined) {
    const entry = t.paper[name];
    if (entry && typeof entry !== "string") return entry.value;
  }
  if (group === "ink" && name !== undefined) {
    const entry = t.ink[name];
    if (entry && typeof entry !== "string") return entry.value;
  }
  if (group === "severity" && name !== undefined && channel !== undefined) {
    const entry = t.severity[name];
    if (entry && typeof entry !== "string") {
      const value = (entry as unknown as Record<string, string>)[channel];
      if (typeof value === "string") return value;
    }
  }
  throw new Error(`tokens: unresolvable reference "${path}"`);
}

interface ResolvedRole {
  readonly value: string;
  readonly min: number;
  readonly derivation: string;
}

export function resolveRoles(t: Tokens, theme: "light" | "dark"): Record<string, ResolvedRole> {
  const out: Record<string, ResolvedRole> = {};
  for (const [role, def] of Object.entries(t.role[theme])) {
    if (role.startsWith("$") || def === undefined || Array.isArray(def)) continue;
    const value = def as RoleValue;
    if ("overprint" in value) {
      const [a, b] = value.overprint;
      out[role] = {
        value: overprint(lookup(t, a), lookup(t, b)),
        min: value.min ?? 4.5,
        derivation: `${a} × ${b} (overprint, §E6.3)`,
      };
    } else {
      out[role] = { value: lookup(t, value.ref), min: value.min ?? 4.5, derivation: value.ref };
    }
  }
  return out;
}

/** WCAG 2.2 relative luminance — used by the contrast test, exported from here
 *  so the number the test checks is derived from the same conversion the
 *  shader uses, not from a second implementation. */
export function relativeLuminance(hex: string): number {
  const [r, g, b] = linear(hex);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrastRatio(a: string, b: string): number {
  const la = relativeLuminance(a);
  const lb = relativeLuminance(b);
  const [hi, lo] = la > lb ? [la, lb] : [lb, la];
  return (hi + 0.05) / (lo + 0.05);
}

const round = (n: number) => Number(n.toFixed(5));

// --------------------------------------------------------------------------
// Generation
// --------------------------------------------------------------------------

const BANNER = (source: string) =>
  `/* GENERATED by scripts/generate-tokens.ts from ${source}. Do not edit.\n` +
  `   §E24: tokens are the source of truth, and the severity ink in a badge and\n` +
  `   the glaze in a shader are the same number because both are written here. */\n`;

function buildCss(t: Tokens): string {
  const lines: string[] = [BANNER("src/design/tokens.json"), "@theme {"];

  lines.push("  /* Paper stocks — ground, carries no meaning (§E9.1) */");
  for (const [name, s] of entries<Swatch>(t.paper)) {
    lines.push(`  --color-${name}: ${s.value};`);
  }

  lines.push("", "  /* Risograph inks (§E9.2) */");
  for (const [name, s] of entries<Swatch>(t.ink)) {
    lines.push(`  --color-${name}: ${s.value};`);
  }

  lines.push("", "  /* Severity — four channels per level, never interchangeable (§E9.4) */");
  for (const [level, row] of entries<SeverityRow>(t.severity)) {
    lines.push(`  --color-sev-${level}-ink: ${row.ink};`);
    lines.push(`  --color-sev-${level}-tint: ${row.tint};`);
    lines.push(`  --color-sev-${level}-glaze: ${row.glaze};`);
  }

  lines.push(
    "",
    "  /* Semantic roles (§E22). The text-safe values are overprints, computed",
    "     by the generator — §E6.3's press mechanic, not a repainted palette. */",
  );
  for (const [role, resolved] of Object.entries(resolveRoles(t, "light"))) {
    lines.push(`  --color-role-${role}: ${resolved.value};`);
  }
  for (const [role, resolved] of Object.entries(resolveRoles(t, "dark"))) {
    lines.push(`  --color-role-dark-${role}: ${resolved.value};`);
  }

  lines.push("", "  /* Faces (§E10) — self-hosted, no CDN (§6 Principle #6) */");
  for (const [name, def] of entries<FamilyDef>(t.type.family)) {
    lines.push(`  --font-${name}: ${def.stack.map((f) => (/\s/.test(f) ? `"${f}"` : f)).join(", ")};`);
  }

  lines.push("", "  /* Motion (§E11) — every duration a multiple of the 12 fps step */");
  for (const [name, e] of entries<Swatch>(t.motion.easing)) {
    lines.push(`  --ease-${name}: ${e.value};`);
  }
  for (const [name, d] of entries<{ ms: number; steps: number }>(t.motion.duration)) {
    lines.push(`  --duration-${name}: ${String(d.ms)}ms;`);
  }

  lines.push("}", "");

  lines.push(":root {");
  lines.push(`  --step-ms: ${String(t.motion.step.ms)}ms;`);
  lines.push(`  --measure: ${String(t.type.measure.ch)}ch;`);
  lines.push(`  --numeric: ${t.type.numeric.value};`);
  const angles = t.press.screenAngles.value;
  angles.forEach((a, i) => lines.push(`  --press-angle-${String(i + 1)}: ${String(a)}deg;`));
  for (const [k, v] of numbers(t.press.halftone)) lines.push(`  --press-${k}: ${String(v)};`);
  for (const [k, v] of numbers(t.press.misregistration)) lines.push(`  --press-${k}: ${String(v)};`);
  for (const [k, v] of numbers(t.press.inkDensity)) lines.push(`  --press-${k}: ${String(v)};`);
  for (const [k, v] of numbers(t.press.paperGrain)) lines.push(`  --press-${k}: ${String(v)};`);
  for (const [k, v] of numbers(t.layer)) lines.push(`  --layer-${k}: ${String(v)};`);
  lines.push("}", "");

  // §E9.3 — the console at night is a light table, not an inverted palette, so
  // the dark ground re-points the same role names at the values that pass on
  // it. A component asks for `--role-text-signal` and is correct in both rooms
  // without knowing which room it is in.
  lines.push("/* Semantic roles, resolved per ground (§E22, §E9.3) */");
  lines.push(":root {");
  for (const role of Object.keys(resolveRoles(t, "light"))) {
    lines.push(`  --role-${role}: var(--color-role-${role});`);
  }
  lines.push("}", "");
  lines.push('[data-surface="console"], [data-ground="light-table"] {');
  for (const role of Object.keys(resolveRoles(t, "dark"))) {
    lines.push(`  --role-${role}: var(--color-role-dark-${role});`);
  }
  lines.push("}", "");

  lines.push("/* Density — three modes, persisted per user (§E19) */");
  for (const [mode, def] of Object.entries(t.density).filter(([k]) => !k.startsWith("$"))) {
    const selector = mode === "compact" ? `:root, [data-density="compact"]` : `[data-density="${mode}"]`;
    lines.push(`${selector} {`);
    for (const [k, v] of numbers(def)) {
      lines.push(`  --density-${k}: ${k === "fontScale" ? String(v) : `${String(v)}px`};`);
    }
    lines.push("}");
  }
  lines.push("");

  // The type scale, per script. §E10.1: never globally scaled from the Latin
  // values, and Devanagari carries +0.15 leading — applied here, by the
  // generator, so no stylesheet can quietly disagree with it.
  const deva = t.type.scale.devanagari;
  lines.push("@layer components {");
  for (const [name, step] of entries<TypeStep>(t.type.scale.latin)) {
    lines.push(`  .type-${name} {`);
    lines.push(`    font-family: var(--font-${step.family});`);
    lines.push(`    font-size: ${step.size};`);
    lines.push(`    font-weight: ${String(step.weight)};`);
    lines.push(`    letter-spacing: ${step.tracking};`);
    lines.push(`    line-height: ${String(step.leading)};`);
    if (step.transform !== "none") lines.push(`    text-transform: ${step.transform};`);
    if (step.rotate) lines.push(`    transform: rotate(${step.rotate});`);
    lines.push(`    font-variant-numeric: ${t.type.numeric.value};`);
    lines.push("  }");
  }
  lines.push("", "  /* Devanagari is a design partner, not a fallback (§E10.1) */");
  for (const [name, step] of entries<TypeStep>(t.type.scale.latin)) {
    const family = deva.$familyMap[step.family] ?? step.family;
    lines.push(`  :where(:lang(mr), :lang(hi), :lang(sa)) .type-${name} {`);
    lines.push(`    font-family: var(--font-${family});`);
    lines.push(`    line-height: ${String(round(step.leading + deva.$leadingDelta))};`);
    lines.push("  }");
  }
  lines.push("}", "");

  return lines.join("\n");
}

function buildTs(t: Tokens): string {
  const severity = entries<SeverityRow>(t.severity).sort((a, b) => b[1].order - a[1].order);

  const j = (v: unknown) => JSON.stringify(v);

  const out: string[] = [
    BANNER("src/design/tokens.json"),
    "",
    "/** Paper stocks. Ground; carries no meaning (§E9.1). */",
    `export const PAPER = ${j(Object.fromEntries(entries<Swatch>(t.paper).map(([k, v]) => [k, v.value])))} as const;`,
    "export type PaperName = keyof typeof PAPER;",
    "",
    "/** Risograph inks (§E9.2). */",
    `export const INK = ${j(Object.fromEntries(entries<Swatch>(t.ink).map(([k, v]) => [k, v.value])))} as const;`,
    "export type InkName = keyof typeof INK;",
    "",
    "/**",
    " * Severity, all four channels (§E9.4).",
    " *",
    " * `shape` and `label` are not decoration: colour is never the only channel,",
    " * because colour-blind readers work here and officers print (§E19.7).",
    " */",
    `export const SEVERITY = ${j(Object.fromEntries(severity))} as const;`,
    "export type SeverityLevel = keyof typeof SEVERITY;",
    `export const SEVERITY_DESCENDING = ${j(severity.map(([k]) => k))} as const satisfies readonly SeverityLevel[];`,
    "",
    "/**",
    " * The glaze as linear-sRGB, which is what a TSL uniform wants.",
    " *",
    " * This is the single most load-bearing export in the file: it is the exact",
    " * moment §19.3's \"defined once\" either holds or quietly stops holding.",
    " */",
    `export const GLAZE_LINEAR = ${j(
      Object.fromEntries(severity.map(([k, v]) => [k, linear(v.glaze).map(round)])),
    )} as const satisfies Record<SeverityLevel, readonly [number, number, number]>;`,
    "",
    "/** The glaze as a three.js colour literal. */",
    `export const GLAZE_HEX = ${j(Object.fromEntries(severity.map(([k, v]) => [k, hexNumber(v.glaze)])))} as const satisfies Record<SeverityLevel, number>;`,
    "",
    "/** Every ink as linear-sRGB, for the press's ink basis (§E6.1 stage 1). */",
    `export const INK_LINEAR = ${j(
      Object.fromEntries(entries<Swatch>(t.ink).map(([k, v]) => [k, linear(v.value).map(round)])),
    )} as const;`,
    "",
    "/**",
    " * Semantic roles, per ground (§E22).",
    " *",
    " * `derivation` is carried into the generated output on purpose: a reviewer",
    " * asking \"why is the signal colour not the aqua ink\" gets the answer from",
    " * the token, not from a commit message. `min` is the contrast floor the",
    " * pair must clear, and tests/contrast.test.ts fails the build below it.",
    " */",
    `export const ROLE = ${j({
      light: resolveRoles(t, "light"),
      dark: resolveRoles(t, "dark"),
    })} as const;`,
    "export type Ground = keyof typeof ROLE;",
    "export type RoleName = keyof (typeof ROLE)[\"light\"];",
    "",
    "/** Which stocks text is allowed to sit on, per ground (§E22). */",
    `export const ROLE_GROUNDS = ${j({
      light: t.role.light.$grounds,
      dark: t.role.dark.$grounds,
    })} as const;`,
    "",
    "/**",
    " * Which of §E9.4's four severity channels carries type and which fills the",
    " * field, per ground. On light, ink-on-tint. On the light table the sheet is",
    " * backlit, so the tint carries the type and the glaze fills the shape.",
    " */",
    `export const SEVERITY_ROLE = ${j(t.role.severity)} as const;`,
    "",
    "/** Two or three inks per run — the real risograph constraint (§E9.2). */",
    `export const INK_SET = ${j(Object.fromEntries(entries<{ stock: string; inks: readonly string[] }>(t.inkSet)))} as const;`,
    "export type InkSetName = keyof typeof INK_SET;",
    "",
    "/** The press (§E6). `quality` is the fallback ladder's first move (§E6.4). */",
    `export const PRESS = ${j({
      screenAngles: t.press.screenAngles.value,
      halftone: Object.fromEntries(numbers(t.press.halftone)),
      misregistration: Object.fromEntries(numbers(t.press.misregistration)),
      inkDensity: Object.fromEntries(numbers(t.press.inkDensity)),
      paperGrain: Object.fromEntries(numbers(t.press.paperGrain)),
      quality: t.press.quality,
    })} as const;`,
    "export type PressQuality = keyof typeof PRESS.quality;",
    "",
    "/** Motion (§E11). Durations are multiples of the 12 fps step. */",
    `export const MOTION = ${j({
      stepMs: t.motion.step.ms,
      easing: Object.fromEntries(entries<Swatch>(t.motion.easing).map(([k, v]) => [k, v.value])),
      durationMs: Object.fromEntries(
        entries<{ ms: number; steps: number }>(t.motion.duration).map(([k, v]) => [k, v.ms]),
      ),
      reducedMotionMs: t.motion.reducedMotion.ms,
    })} as const;`,
    "",
    "/** Compositing order. `text` is above `press` and is never processed by it (ADR-0038). */",
    `export const LAYER = ${j(Object.fromEntries(numbers(t.layer)))} as const;`,
    "",
    "/** Three density modes, persisted per user (§E19). */",
    `export const DENSITY = ${j(
      Object.fromEntries(
        Object.entries(t.density)
          .filter(([k]) => !k.startsWith("$"))
          .map(([k, v]) => [k, Object.fromEntries(numbers(v))]),
      ),
    )} as const;`,
    "export type DensityMode = keyof typeof DENSITY;",
    "",
    "/** Prose measure cap, tooltips included (§E10.2). */",
    `export const MEASURE_CH = ${String(t.type.measure.ch)};`,
    "",
    "/** The type scale's step names, so a component cannot invent one. */",
    `export const TYPE_STEPS = ${j(entries<TypeStep>(t.type.scale.latin).map(([k]) => k))} as const;`,
    "export type TypeStep = (typeof TYPE_STEPS)[number];",
    "",
  ];
  return out.join("\n");
}

// --------------------------------------------------------------------------

async function main(): Promise<void> {
  const check = process.argv.includes("--check");
  const tokens = JSON.parse(readFileSync(SOURCE, "utf8")) as Tokens;

  const artefacts = [
    { path: join(OUT_DIR, "tokens.css"), body: buildCss(tokens), parser: "css" as const },
    { path: join(OUT_DIR, "tokens.ts"), body: buildTs(tokens), parser: "typescript" as const },
  ];

  mkdirSync(OUT_DIR, { recursive: true });

  let stale = 0;
  for (const a of artefacts) {
    const formatted = await format(a.body, { parser: a.parser, printWidth: 100 });
    if (check) {
      let current: string | null = null;
      try {
        current = readFileSync(a.path, "utf8");
      } catch {
        current = null;
      }
      if (current !== formatted) {
        stale += 1;
        console.error(`✗ stale: ${a.path.replace(ROOT, ".")}`);
      }
    } else {
      writeFileSync(a.path, formatted, "utf8");
      console.log(`  wrote ${a.path.replace(ROOT, ".")}`);
    }
  }

  if (check) {
    if (stale > 0) {
      console.error(
        `\ntokens: ${String(stale)} generated artefact(s) do not match src/design/tokens.json.\n` +
          `Run \`npm run tokens\`. §E24 — the badge and the shader are the same number only\n` +
          `while these files are generated rather than edited.`,
      );
      process.exitCode = 1;
    } else {
      console.log("tokens: generated artefacts match the source");
    }
  }
}

if (process.argv[1]?.endsWith("generate-tokens.ts")) await main();
