/**
 * Strings — §E10.1, §E22, and Phase 18's hardest gate.
 *
 * > **A locale added in the control plane appears in the UI with no code
 * > change.**  — §E25, Phase 18
 *
 * That is only meetable if no user-visible string in this application is a
 * literal in a component. Which is easy to say and easy to erode, so it is
 * enforced by the type system rather than by review:
 *
 *   **`Translated` is not `string`.** Every prop that renders words takes
 *   `Translated`, which only `t()` and `plural()` can produce. A component
 *   handed a bare literal does not compile. A component handed
 *   `t(s, "a") + t(s, "b")` does not compile either — concatenation widens back
 *   to `string` — which is §E10.1's *"never concatenate a sentence from
 *   fragments; a sentence is a translation unit"* made mechanical rather than
 *   aspirational.
 *
 * **Three tiers, one path.** The application ships a base bundle in the source
 * language; the control plane's Phase 5 locale registry overrides and extends
 * it; a missing key falls back to base. A deployment whose control plane is
 * unreachable renders correct English rather than a page of key names — §E13's
 * ladder applied to text — and a locale added upstream needs no code because
 * the keys already exist.
 */

declare const TRANSLATED: unique symbol;

/**
 * A string that came from the locale registry.
 *
 * The brand is erased at runtime; its whole job is to make "this text was
 * translated" a fact the compiler checks.
 */
export type Translated = string & { readonly [TRANSLATED]: true };

/** Namespaces mirror the surfaces, and the control plane keys on them. */
export const NAMESPACES = ["common", "citizen", "console", "public"] as const;
export type Namespace = (typeof NAMESPACES)[number];

export interface Strings {
  readonly locale: string;
  readonly namespace: Namespace;
  readonly bundle: Readonly<Record<string, string>>;
  /**
   * Keys asked for and not found. Empty in a healthy deployment; surfaced by
   * the dev overlay and asserted by a test, because a UI that silently renders
   * `citizen.receipt.title` looks like a bug in the *product* rather than a gap
   * in a bundle.
   */
  readonly missing: Set<string>;
}

export function makeStrings(
  namespace: Namespace,
  locale: string,
  bundle: Readonly<Record<string, string>>,
): Strings {
  return { namespace, locale, bundle, missing: new Set() };
}

/**
 * Resolve one key.
 *
 * `vars` are named, never positional, because word order changes between
 * scripts and a positional placeholder silently produces nonsense in the
 * language nobody on the team reads.
 */
export function t(
  strings: Strings,
  key: string,
  vars?: Readonly<Record<string, string | number>>,
): Translated {
  const template = strings.bundle[key];
  if (template === undefined) {
    strings.missing.add(key);
    // The key itself, marked. Visible in development, and never a blank space
    // — §E3.3: honesty is rendered, including about our own gaps.
    return `⟦${key}⟧` as Translated;
  }
  return interpolate(template, vars) as Translated;
}

/**
 * Resolve a key that varies with a count.
 *
 * `Intl.PluralRules` per locale rather than an `=== 1` check: Marathi and Hindi
 * do not partition the same way English does, and a hand-rolled rule is how a
 * civic product ends up telling somebody they are the "1 people" to report a
 * pothole.
 */
export function plural(
  strings: Strings,
  key: string,
  count: number,
  vars?: Readonly<Record<string, string | number>>,
): Translated {
  const category = new Intl.PluralRules(strings.locale).select(count);
  const specific = strings.bundle[`${key}.${category}`];
  const fallback = strings.bundle[`${key}.other`];
  const template = specific ?? fallback;

  if (template === undefined) {
    strings.missing.add(`${key}.${category}`);
    return `⟦${key}.${category}⟧` as Translated;
  }
  return interpolate(template, { ...vars, count }) as Translated;
}

/**
 * Mark a value that is legitimately not translatable.
 *
 * A chain hash, a complaint id, a coordinate, a contractor's registered name.
 * §E10 gives these their own faces precisely because they are the same glyphs
 * in every locale. The escape exists so those can flow through a `Translated`
 * prop — and it is deliberately ugly to type, so reaching for it is a decision
 * somebody makes rather than a habit.
 */
export function notTranslatable(value: string): Translated {
  return value as Translated;
}

const PLACEHOLDER = /\{(\w+)\}/g;

function interpolate(
  template: string,
  vars: Readonly<Record<string, string | number>> | undefined,
): string {
  if (vars === undefined) return template;
  return template.replace(PLACEHOLDER, (whole, name: string) => {
    const value = vars[name];
    return value === undefined ? whole : String(value);
  });
}

/**
 * Merge the control plane's bundle over the base.
 *
 * Override, not replace: a locale that has translated forty of sixty keys
 * renders forty translated and twenty in the source language, which is a
 * partially-localised product. Replacing would render twenty key names, which
 * is a broken one — and partial coverage is the *normal* state of a translation
 * effort, not an exceptional one.
 */
export function mergeBundles(
  base: Readonly<Record<string, string>>,
  upstream: Readonly<Record<string, string>>,
): Readonly<Record<string, string>> {
  return { ...base, ...upstream };
}
