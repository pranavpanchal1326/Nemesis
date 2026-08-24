import commonBase from "@/i18n/base/common.json";
import commonMarathi from "@/i18n/base/common.mr.json";
import { makeStrings, mergeBundles, type Namespace, type Strings } from "./strings";

/**
 * The shipped bundles, client-safe.
 *
 * Split out of `src/server/strings.ts` so the same tiers are available where
 * there is no server: Storybook, a unit test, and §E13 Tier D. The server
 * module adds the one thing that needs a network — the Phase 5 locale registry
 * — and adds it *over* these, never instead of them.
 *
 * One definition, three consumers. A catalogue that rendered different words
 * from the product would be a catalogue nobody could review against.
 */

/** The source language. Not a default *preference* — the tier every other
 *  locale falls back to, key by key. */
export const SOURCE_LOCALE = "en";

function strip(bundle: Record<string, string>): Readonly<Record<string, string>> {
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(bundle)) {
    if (!key.startsWith("$")) out[key] = value;
  }
  return out;
}

export const BASE: Record<Namespace, Readonly<Record<string, string>>> = {
  common: strip(commonBase),
  citizen: {},
  console: {},
  public: {},
};

/**
 * Seed bundles shipped with the application.
 *
 * **Not a replacement for the locale registry.** The control plane overrides
 * every key here, and Phase 18's gate — *a locale added in the control plane
 * appears in the UI with no code change* — is unaffected, because adding a
 * locale still needs no code.
 *
 * They exist for two reasons. A deployment in Pune should not greet its
 * citizens in English while somebody gets round to filling the registry. And
 * §E10.1 makes Devanagari a design partner rather than a fallback: a per-script
 * type scale with no Devanagari text anywhere in the product is a scale nobody
 * has actually looked at.
 *
 * Coverage is deliberately partial. Untranslated keys fall through to the
 * source language — which is the normal state of a translation effort, and is
 * why `mergeBundles` overrides rather than replaces.
 */
const SEED: Record<string, Partial<Record<Namespace, Readonly<Record<string, string>>>>> = {
  mr: { common: strip(commonMarathi) },
};

/** Locales the application can render before the control plane says anything. */
export const SEEDED_LOCALES: readonly string[] = [SOURCE_LOCALE, ...Object.keys(SEED)];

/** Source language plus any shipped seed, with no network involved. */
export function shippedBundle(
  namespace: Namespace,
  locale: string,
): Readonly<Record<string, string>> {
  return mergeBundles(BASE[namespace], SEED[locale]?.[namespace] ?? {});
}

/** A resolved `Strings` with no server. Storybook, tests, and Tier D. */
export function shippedStrings(namespace: Namespace, locale: string): Strings {
  return makeStrings(namespace, locale, shippedBundle(namespace, locale));
}
