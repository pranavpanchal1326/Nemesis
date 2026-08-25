import "server-only";

import { shippedBundle, SOURCE_LOCALE } from "@/lib/i18n/bundles";
import { makeStrings, mergeBundles, type Namespace, type Strings } from "@/lib/i18n/strings";
import { upstream } from "@/server/upstream";

export { SEEDED_LOCALES, SOURCE_LOCALE } from "@/lib/i18n/bundles";

/**
 * Locale negotiation and bundle assembly — §E14.1, §E22.
 *
 * §E14.1 lists *"a place for locale negotiation"* as one of the reasons the BFF
 * seam exists at all. This is that place: the server decides which locale a
 * request gets, fetches the control plane's bundle for it, merges it over the
 * base, and hands the surfaces a resolved `Strings`.
 *
 * Doing it on the server rather than in the client is not an optimisation. The
 * public surfaces are server-rendered so they are indexable and shareable
 * (§E18), and a page whose language is chosen after hydration is a page that
 * ships the wrong `lang` attribute to a crawler and to a screen reader — which
 * on a Devanagari surface means the wrong per-script type scale *and* the wrong
 * voice.
 */

/**
 * Pick a locale from the request.
 *
 * An explicit choice wins over a header, because a person who has chosen
 * Marathi on a shared municipal machine should not be overruled by whatever the
 * browser was installed with. The control plane's tenant `primary_locale` is
 * the next tier — a Pune deployment should not greet its citizens in English
 * because that is what a laptop shipped with — and `Accept-Language` is last.
 */
export function negotiateLocale(options: {
  readonly explicit?: string | undefined;
  readonly tenantDefault?: string | undefined;
  readonly acceptLanguage?: string | null;
  readonly available: readonly string[];
}): string {
  const known = new Set(options.available);
  const preferences = [
    options.explicit,
    options.tenantDefault,
    ...parseAcceptLanguage(options.acceptLanguage),
  ];

  for (const preference of preferences) {
    if (preference === undefined) continue;
    if (known.has(preference)) return preference;
    // `mr-IN` should find `mr`. A locale registry keyed on language alone is
    // the common case, and failing to match it would send a Marathi speaker to
    // English over a region subtag nobody configured.
    const language = preference.split("-")[0];
    if (language !== undefined && known.has(language)) return language;
  }
  return SOURCE_LOCALE;
}

function parseAcceptLanguage(header: string | null | undefined): string[] {
  if (header === null || header === undefined) return [];
  return header
    .split(",")
    .map((part) => {
      const [tag = "", q = "q=1"] = part.trim().split(";");
      return { tag: tag.trim(), q: Number(q.replace("q=", "")) || 0 };
    })
    .filter((entry) => entry.tag !== "" && entry.tag !== "*")
    .sort((a, b) => b.q - a.q)
    .map((entry) => entry.tag);
}

/**
 * Assemble the bundle for one namespace and locale.
 *
 * The control plane's failure is not this page's failure: an unreachable
 * registry yields the base bundle and a correct page in the source language.
 * §E13's ladder says a degradation should be designed rather than apologised
 * for, and the designed degradation for text is *the words are still there*.
 */
export async function loadStrings(
  namespace: Namespace | readonly Namespace[],
  locale: string,
): Promise<Strings> {
  // A surface usually needs more than one namespace: §E18's public pages render
  // `<SuppressionNotice>` and `<ContractorLedger>`, whose sentences live in
  // `common` because they are the same sentences the citizen and officer
  // surfaces show. Merging here rather than making every page hold two
  // `Strings` keeps `t(strings, key)` a single lookup, which is what makes a
  // missing key a single reportable fact.
  //
  // **Later wins on a collision**, and the surface's own namespace is passed
  // last, so a public page that wants a different word for `figure.total` than
  // `common` uses gets it by adding the key to `public` — not by reordering
  // arguments at eleven call sites.
  if (Array.isArray(namespace)) {
    const parts = await Promise.all(
      (namespace as readonly Namespace[]).map((one) => loadOne(one, locale)),
    );
    const merged = parts.reduce<Readonly<Record<string, string>>>(
      (all, part) => mergeBundles(all, part.bundle),
      {},
    );
    const primary = (namespace as readonly Namespace[]).at(-1) ?? "common";
    return makeStrings(primary, locale, merged);
  }
  return loadOne(namespace as Namespace, locale);
}

async function loadOne(namespace: Namespace, locale: string): Promise<Strings> {
  // Three tiers, merged in order of authority: source language, shipped seed,
  // control plane. Each one overrides the last key by key, so a registry that
  // has translated forty of sixty keys renders forty translated and twenty in
  // the source language — a partially-localised product rather than a broken
  // one.
  const seeded = shippedBundle(namespace, locale);

  if (locale === SOURCE_LOCALE) return makeStrings(namespace, locale, seeded);

  const { data, error } = await upstream.GET(
    "/api/v1/control-plane/translations/{namespace}/{locale}",
    { params: { path: { namespace, locale } } },
  );

  if (error !== undefined) return makeStrings(namespace, locale, seeded);
  return makeStrings(namespace, locale, mergeBundles(seeded, data));
}

/** The keys this application asks for. Exported so a coverage test can compare
 *  them against what the control plane actually holds. */
export function baseKeys(namespace: Namespace): readonly string[] {
  return Object.keys(shippedBundle(namespace, SOURCE_LOCALE));
}
