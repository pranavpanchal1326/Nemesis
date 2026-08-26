import commonBase from "@/i18n/base/common.json";
import commonMarathi from "@/i18n/base/common.mr.json";
import consoleBase from "@/i18n/base/console.json";
import consoleMarathi from "@/i18n/base/console.mr.json";
import publicBase from "@/i18n/base/public.json";
import publicMarathi from "@/i18n/base/public.mr.json";
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
  console: strip(consoleBase),
  public: strip(publicBase),
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
  mr: {
    common: strip(commonMarathi),
    console: strip(consoleMarathi),
    public: strip(publicMarathi),
  },
  /**
   * **`ar` is declared and deliberately empty — A11, §E22.**
   *
   * Not an oversight and not a placeholder. This entry says one thing
   * precisely: *this application can render this locale's frame, and ships
   * none of its words.* Direction, digit shaping and date formatting all
   * follow the tag; every string falls through to the source language, which
   * is what `mergeBundles` does for a partially-translated locale anyway.
   *
   * It is here because §E22 claims *"RTL-ready layout primitives"* and nothing
   * had ever rendered a right-to-left locale to find out. `nem seed-demo`
   * declares the same locale on the demo tenant, as *data* — ward and category
   * names — for the same reason. Shipping invented Arabic civic copy to make
   * the screenshot look finished would claim more than this deployment can
   * support, and §E3.3 is a rule about exactly that.
   *
   * The locale an Indian deployment is most likely to actually need is `ur`,
   * and it will arrive the way every other locale does: through the control
   * plane, with no code change (Phase 18's gate).
   */
  ar: {},
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

/** A resolved `Strings` with no server. Storybook, tests, and Tier D.
 *
 *  Takes a list for the same reason `loadStrings` does — a surface usually
 *  needs its own namespace *and* `common` — and merges in the same order, later
 *  winning, so the catalogue and the product resolve a key identically. */
export function shippedStrings(
  namespace: Namespace | readonly Namespace[],
  locale: string,
): Strings {
  if (!Array.isArray(namespace)) {
    const one = namespace as Namespace;
    return makeStrings(one, locale, shippedBundle(one, locale));
  }
  const list = namespace as readonly Namespace[];
  const merged = list.reduce<Readonly<Record<string, string>>>(
    (all, one) => mergeBundles(all, shippedBundle(one, locale)),
    {},
  );
  return makeStrings(list.at(-1) ?? "common", locale, merged);
}
