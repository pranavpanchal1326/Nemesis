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
  | {
      readonly overprint: readonly [string, string];
      readonly min?: number;
      readonly note?: string;
    };

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
    /** §E21's outdoor mode. A third ground, with a 7:1 floor. */
    readonly outdoor: RoleTheme;
    readonly severity: {
      readonly light: { readonly text: string; readonly field: string; readonly min: number };
      readonly dark: { readonly text: string; readonly field: string; readonly min: number };
    };
  };
  readonly inkSet: Record<
    string,
    { stock: string; sheet?: string; inks: readonly string[]; gradeGamma?: number } | string
  >;
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
  /**
   * §E12 — the sound layer's numbers (ADR-0050).
   *
   * Flat groups of numbers, like `clay` and `lens`, because none of them is a
   * colour and none of them reaches a stylesheet: sound is authored in the
   * synthesiser, not in CSS.
   */
  readonly sound: {
    readonly gain: Record<string, number | string>;
    readonly duck: Record<string, number | string>;
    readonly crossfade: Record<string, number | string>;
    readonly ambient: Record<string, number | string>;
    readonly foley: Record<string, number | string>;
    readonly merge: Record<string, number | string>;
    readonly note: Record<string, number | string>;
    readonly positional: Record<string, number | string>;
    readonly sampleRate: number;
    readonly seed: number;
  };
  readonly type: {
    readonly family: Record<string, FamilyDef | string>;
    readonly scale: {
      readonly latin: Record<string, TypeStep | string>;
      readonly devanagari: {
        readonly $leadingDelta: number;
        readonly $leadingFloor: number;
        readonly $familyMap: Record<string, string>;
      };
    };
    readonly measure: { readonly ch: number };
    readonly numeric: { readonly value: string };
  };
  readonly density: Record<string, Record<string, number | string>>;
  readonly layer: Record<string, number | string>;
  /**
   * §E7.1, §E7.3, §E23 — the clay engine's numbers.
   *
   * Three of these groups are *parameters*, not palettes: `clay` authors no
   * colour of its own and instead references the inks §E9.2 already assigned
   * to it, so the clay body and the brown ink in a badge cannot drift apart
   * any more than the glaze and the severity badge can.
   */
  readonly clay: {
    readonly body: { readonly ref: string };
    readonly warm: { readonly ref: string };
    readonly cool: { readonly ref: string };
    readonly surface: Record<string, number | string>;
    readonly thumbprint: Record<string, number | string>;
    readonly ao: Record<string, number | string>;
    readonly rim: Record<string, number | string>;
    readonly edge: Record<string, number | string>;
    readonly glaze: Record<string, number | string>;
  };
  readonly lens: {
    readonly tiltShift: Record<string, number | string>;
    readonly gateWeave: Record<string, number | string>;
    readonly vignette: Record<string, number | string>;
    readonly barrel: Record<string, number | string>;
    readonly bloom: Record<string, number | string>;
  };
  readonly render: Record<string, number | string>;
  readonly story: Record<string, number | string>;
  readonly world: {
    readonly kit: Record<string, number | string>;
    readonly pin: Record<string, number | string>;
    readonly camera: Record<string, number | string>;
    readonly extent: Record<string, number | string>;
  };
  readonly weather: Record<string, number | string>;
  readonly budget: Record<string, number | string>;
}

/** `$note` keys document the source; they are never emitted. */
function entries<T>(record: Record<string, T | string>): [string, Exclude<T, string>][] {
  return Object.entries(record).flatMap(([key, value]) =>
    key.startsWith("$") || typeof value === "string"
      ? []
      : [[key, value] as [string, Exclude<T, string>]],
  );
}

function numbers(record: Record<string, number | string>): [string, number][] {
  return Object.entries(record).flatMap(([key, value]) =>
    key.startsWith("$") || typeof value !== "number" ? [] : [[key, value] as [string, number]],
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

/** The three grounds this product renders on: a sheet, a light table, and
 *  a phone in the sun (§E9.1, §E9.3, §E21). */
export const GROUNDS = ["light", "dark", "outdoor"] as const;
export type GroundName = (typeof GROUNDS)[number];

export function resolveRoles(t: Tokens, theme: GroundName): Record<string, ResolvedRole> {
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
  // §E21's outdoor mode. A third set of the same role names, so a component
  // asks for `--role-text-primary` and is correct in sunlight without knowing
  // it is in sunlight — exactly as it is correct on the light table.
  for (const [role, resolved] of Object.entries(resolveRoles(t, "outdoor"))) {
    lines.push(`  --color-role-outdoor-${role}: ${resolved.value};`);
  }

  lines.push("", "  /* Faces (§E10) — self-hosted, no CDN (§6 Principle #6) */");
  for (const [name, def] of entries<FamilyDef>(t.type.family)) {
    lines.push(
      `  --font-${name}: ${def.stack.map((f) => (/\s/.test(f) ? `"${f}"` : f)).join(", ")};`,
    );
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
  // The Latin scale's *sizes*, as properties.
  //
  // The `.type-*` classes below are the whole step — face, weight, tracking and
  // leading together — and that is what copy should carry. But a component
  // sometimes needs one step's size on an element that already belongs to
  // another step: the unscored severity badge sits inside a peer row and has to
  // be a caption next to a body label, and there is no class it can wear
  // without also taking the caption's face and leading.
  //
  // Without these it re-declared `0.8125rem` by hand, which is the one thing
  // §E24 exists to stop — a second copy of a scale value that no longer moves
  // when the scale does. `globals.css` was already asking for `--text-body`
  // with a fallback, against a property nothing emitted.
  for (const [name, step] of entries<TypeStep>(t.type.scale.latin)) {
    lines.push(`  --text-${name}: ${step.size};`);
  }
  const angles = t.press.screenAngles.value;
  angles.forEach((a, i) => lines.push(`  --press-angle-${String(i + 1)}: ${String(a)}deg;`));
  for (const [k, v] of numbers(t.press.halftone)) lines.push(`  --press-${k}: ${String(v)};`);
  for (const [k, v] of numbers(t.press.misregistration))
    lines.push(`  --press-${k}: ${String(v)};`);
  for (const [k, v] of numbers(t.press.inkDensity)) lines.push(`  --press-${k}: ${String(v)};`);
  for (const [k, v] of numbers(t.press.paperGrain)) lines.push(`  --press-${k}: ${String(v)};`);
  for (const [k, v] of numbers(t.layer)) lines.push(`  --layer-${k}: ${String(v)};`);
  // §E16's film, as height. `story.css` multiplies an act's share of the spine
  // by this to size its section, so scroll position and `t` stay linear in each
  // other — see the note in `tokens.json` for why the number came down.
  lines.push(`  --story-viewports: ${String(t.story["viewports"] ?? 10)};`);
  lines.push(`  --story-panel-top: ${String(t.story["panelTopVh"] ?? 18)}dvh;`);
  // Where the film's reading ground has finished handing the frame back to the
  // model. The acts are set in paper inks and the stage is a clay render, so
  // without this the secondary ones print at about 1.5:1 on it; see the note in
  // `tokens.json` for why the answer is the press's own ground rather than a
  // veil, and why the number is this one.
  lines.push(`  --story-scrim-reach: ${String(t.story["scrimReachPct"] ?? 58)}%;`);
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

  // §E21 — outdoor mode. Last, so it wins over the console's own ground: a
  // field hand who opens the console on a phone in the sun is outdoors first
  // and on a light table second.
  lines.push('[data-ground="outdoor"] {');
  for (const role of Object.keys(resolveRoles(t, "outdoor"))) {
    lines.push(`  --role-${role}: var(--color-role-outdoor-${role});`);
  }
  lines.push("}", "");

  lines.push("/* Density — three modes, persisted per user (§E19) */");
  for (const [mode, def] of Object.entries(t.density).filter(([k]) => !k.startsWith("$"))) {
    const selector =
      mode === "compact" ? `:root, [data-density="compact"]` : `[data-density="${mode}"]`;
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
    // A delta AND a floor. §E10.1 wrote only the delta, and a flat delta clips
    // the matras wherever the Latin leading is below 1.0 — measured at
    // `display-1`, where Devanagari ink is 1.205em against a 1.09 line.
    const leading = Math.max(step.leading + deva.$leadingDelta, deva.$leadingFloor);
    lines.push(`    line-height: ${String(round(leading))};`);
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
    ' * moment §19.3\'s "defined once" either holds or quietly stops holding.',
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
    " * Every stock as linear-sRGB — §E6.1 stage 6, the sheet every plate",
    " * multiplies onto.",
    " *",
    " * The 2D press gets the stock as a CSS colour and the 3D press needs the",
    " * same stock as a linear triple. Converting it at the call site would be",
    " * the one hand-written colour conversion in the product, which is the",
    " * same failure as a hand-written colour with an extra step.",
    " */",
    `export const PAPER_LINEAR = ${j(
      Object.fromEntries(entries<Swatch>(t.paper).map(([k, v]) => [k, linear(v.value).map(round)])),
    )} as const satisfies Record<PaperName, readonly [number, number, number]>;`,
    "",
    "/**",
    " * Semantic roles, per ground (§E22).",
    " *",
    " * `derivation` is carried into the generated output on purpose: a reviewer",
    ' * asking "why is the signal colour not the aqua ink" gets the answer from',
    " * the token, not from a commit message. `min` is the contrast floor the",
    " * pair must clear, and tests/contrast.test.ts fails the build below it.",
    " */",
    `export const ROLE = ${j({
      light: resolveRoles(t, "light"),
      dark: resolveRoles(t, "dark"),
      outdoor: resolveRoles(t, "outdoor"),
    })} as const;`,
    "export type Ground = keyof typeof ROLE;",
    'export type RoleName = keyof (typeof ROLE)["light"];',
    "",
    "/** Which stocks text is allowed to sit on, per ground (§E22). */",
    `export const ROLE_GROUNDS = ${j({
      light: t.role.light.$grounds,
      dark: t.role.dark.$grounds,
      outdoor: t.role.outdoor.$grounds,
    })} as const;`,
    "",
    "/**",
    " * Which of §E9.4's four severity channels carries type and which fills the",
    " * field, per ground. On light, ink-on-tint. On the light table the sheet is",
    " * backlit, so the tint carries the type and the glaze fills the shape.",
    " */",
    `export const SEVERITY_ROLE = ${j(t.role.severity)} as const;`,
    "",
    "/**",
    " * Two or three inks per run — the real risograph constraint (§E9.2).",
    " *",
    " * `stock` is the page ground; `sheet` is what the press prints on. They",
    " * differ on exactly one surface — §E9.3's light table, where the ground is",
    " * the room and the print on it is backlit — and `sheet` is filled in here",
    " * so no consumer has to remember which case it is in.",
    " *",
    " * `gradeGamma` is the run's exposure (ADR-0061), applied to the photograph",
    " * about the sheet's white point before the plates are solved. Filled in at",
    " * 1.0 — the identity — wherever the source does not state one, so a reader",
    " * of this file can see that only one run is graded and every other is not.",
    " */",
    `export const INK_SET = ${j(
      Object.fromEntries(
        entries<{ stock: string; sheet?: string; inks: readonly string[]; gradeGamma?: number }>(
          t.inkSet,
        ).map(([name, set]) => [
          name,
          {
            stock: set.stock,
            sheet: set.sheet ?? set.stock,
            inks: set.inks,
            gradeGamma: set.gradeGamma ?? 1,
          },
        ]),
      ),
    )} as const;`,
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
    "/**",
    " * The clay material recipe (§E7.1).",
    " *",
    " * `bodyLinear`, `warmLinear` and `coolLinear` are the three inks §E9.2",
    " * already assigned to clay, converted once — the same conversion the",
    " * glaze above goes through, so the clay body in a shader and the brown ink",
    " * in a badge are one number by construction rather than by agreement.",
    " */",
    `export const CLAY = ${j({
      body: lookup(t, t.clay.body.ref),
      bodyLinear: linear(lookup(t, t.clay.body.ref)).map(round),
      warmLinear: linear(lookup(t, t.clay.warm.ref)).map(round),
      coolLinear: linear(lookup(t, t.clay.cool.ref)).map(round),
      surface: Object.fromEntries(numbers(t.clay.surface)),
      thumbprint: Object.fromEntries(numbers(t.clay.thumbprint)),
      ao: Object.fromEntries(numbers(t.clay.ao)),
      rim: Object.fromEntries(numbers(t.clay.rim)),
      edge: Object.fromEntries(numbers(t.clay.edge)),
      glaze: Object.fromEntries(numbers(t.clay.glaze)),
    })} as const;`,
    "",
    "/** The lens stack (§E7.3). Applied to the frame before the press prints it. */",
    `export const LENS = ${j({
      tiltShift: Object.fromEntries(numbers(t.lens.tiltShift)),
      gateWeave: Object.fromEntries(numbers(t.lens.gateWeave)),
      vignette: Object.fromEntries(numbers(t.lens.vignette)),
      barrel: Object.fromEntries(numbers(t.lens.barrel)),
      bloom: Object.fromEntries(numbers(t.lens.bloom)),
    })} as const;`,
    "",
    "/**",
    " * §E12 — the sound design, as numbers (ADR-0050).",
    " *",
    " * Nothing here is a file. `src/sound/synth.ts` renders every cue from these",
    " * at runtime, deterministically from `seed`, so what a reviewer hears is",
    " * what CI hears. The two durations that accompany a motion are multiples of",
    " * the 12 fps step, because §E11's coherence argument applies to a thud",
    " * exactly as it applies to the stamp the thud belongs to.",
    " */",
    `export const SOUND = ${j({
      gain: Object.fromEntries(numbers(t.sound.gain)),
      duckMs: Object.fromEntries(numbers(t.sound.duck))["ms"],
      crossfadeMs: Object.fromEntries(numbers(t.sound.crossfade))["ms"],
      ambient: Object.fromEntries(numbers(t.sound.ambient)),
      foley: Object.fromEntries(numbers(t.sound.foley)),
      merge: Object.fromEntries(numbers(t.sound.merge)),
      note: Object.fromEntries(numbers(t.sound.note)),
      positional: Object.fromEntries(numbers(t.sound.positional)),
      sampleRate: t.sound.sampleRate,
      seed: t.sound.seed,
    })} as const;`,
    "",
    "/**",
    " * How the frame leaves the renderer, before §E6's press reads it.",
    " *",
    " * Phase 19's ship line asks for `SRGBColorSpace` and ACES Filmic tone",
    " * mapping. Neither was set, and the symptom was not a colour cast: on the",
    " * story run — brown, sunflower and aqua, no black plate (§E9.2) — an",
    " * unmapped linear frame solved past full coverage on the one plate that",
    " * carries the clay, and the film printed as a flat wash with the model",
    " * invisible inside it.",
    " */",
    `export const RENDER = ${j(Object.fromEntries(numbers(t.render)))} as const;`,
    "",
    "/** §E16's film, as height. See the note in `tokens.json`: the reel was",
    " *  twenty screens because the shortest act needed one, which let the",
    " *  shortest act set the length of the whole film. */",
    `export const STORY = ${j(Object.fromEntries(numbers(t.story)))} as const;`,
    "",
    "/** The clay city, in real ground metres — never in scene units (M8.2). */",
    `export const WORLD = ${j({
      kit: Object.fromEntries(numbers(t.world.kit)),
      pin: Object.fromEntries(numbers(t.world.pin)),
      camera: Object.fromEntries(numbers(t.world.camera)),
      extent: Object.fromEntries(numbers(t.world.extent)),
    })} as const;`,
    "",
    "/**",
    " * §E7.4 — how a seasonal SLA multiplier becomes wet clay, and how a solar",
    " * altitude becomes a key light.",
    " *",
    " * Nothing here decides *whether* it is raining. That is the SLA engine's",
    " * own answer (`clay/sun.ts`), and these are the numbers that render it.",
    " */",
    `export const WEATHER = ${j(Object.fromEntries(numbers(t.weather)))} as const;`,
    "",
    "/**",
    " * §E23's budgets, and §E13's two Tier B thresholds.",
    " *",
    " * The adaptive quality manager reads these and so does the CI assertion.",
    " * A budget written in a table and re-typed in a test is two budgets, and",
    " * the one that gets relaxed is always the one nobody is looking at.",
    " */",
    `export const BUDGET = ${j(Object.fromEntries(numbers(t.budget)))} as const;`,
    "",
  ];
  return out.join("\n");
}

/**
 * The installed app's icon — §E21's PWA, F17.
 *
 * Generated rather than committed, for the third time in this repository and
 * for the same reason ADR-0047 and ADR-0050 give: the artefact is the source. A
 * committed PNG set is four binaries nobody can regenerate, that drift from the
 * palette the moment somebody re-mixes an ink.
 *
 * **One SVG, `"any maskable"`.** A maskable icon is cropped to a circle or a
 * squircle by the platform, and the safe zone is the middle 80% — so the mark
 * sits inside a 40% radius and the ground fills the whole square. What it draws
 * is the product's own stamp: a struck rectangle, off-register, on chalk.
 */
function buildIcon(t: Tokens): string {
  // A newline as a named constant, because the generator writes files and its
  // own source must not contain the character it is joining with.
  const NEWLINE = String.fromCharCode(10);
  const ground = lookup(t, "paper.chalk");
  const line = lookup(t, "ink.riso-black");
  const warm = lookup(t, "ink.riso-brown");
  const misregistration = numbers(t.press.misregistration).find(([k]) => k === "maxPx")?.[1] ?? 1;
  const offset = round(misregistration * 3);

  return [
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" role="img"',
    '     aria-label="NEMESIS">',
    "  <!-- GENERATED by scripts/generate-tokens.ts. Do not edit. -->",
    `  <rect width="512" height="512" fill="${ground}"/>`,
    `  <rect x="${String(136 + offset)}" y="${String(176 + offset)}" width="240" height="160"`,
    `        fill="${warm}" opacity="0.55"/>`,
    `  <rect x="136" y="176" width="240" height="160" fill="none" stroke="${line}"`,
    '        stroke-width="18"/>',
    `  <path d="M188 300 L188 212 L324 300 L324 212" fill="none" stroke="${line}"`,
    '        stroke-width="26" stroke-linecap="round" stroke-linejoin="round"/>',
    "</svg>",
    "",
  ].join(NEWLINE);
}

// --------------------------------------------------------------------------

async function main(): Promise<void> {
  const check = process.argv.includes("--check");
  const tokens = JSON.parse(readFileSync(SOURCE, "utf8")) as Tokens;

  const artefacts = [
    { path: join(OUT_DIR, "tokens.css"), body: buildCss(tokens), parser: "css" as const },
    { path: join(OUT_DIR, "tokens.ts"), body: buildTs(tokens), parser: "typescript" as const },
    // The PWA icon lands in `public/` because that is where a manifest can
    // reach it, and it is drift-checked exactly like the other two.
    { path: join(ROOT, "public", "icon.svg"), body: buildIcon(tokens), parser: "html" as const },
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
