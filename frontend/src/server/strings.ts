import "server-only";

import { shippedBundle, SOURCE_LOCALE } from "@/lib/i18n/bundles";
import { makeStrings, mergeBundles, type Namespace, type Strings } from "@/lib/i18n/strings";

export { SEEDED_LOCALES, SOURCE_LOCALE } from "@/lib/i18n/bundles";

/**
 * Locale negotiation and bundle assembly — §E14.1, §E22.
 *
 * §E14.1 lists *"a place for locale negotiation"* as one of the reasons the BFF
 * seam exists at all. This is that place: the server decides which locale a
 * request gets, assembles the bundle for it, and hands the surfaces a resolved
 * `Strings`.
 *
 * **Assembly is local, and ADR-0058 is why.** Product copy is authored by
 * NEMESIS and versioned with the code; the Phase 5 locale registry carries
 * *tenant-authored* words — a ward's name, a category's display name — and
 * refuses an import into any namespace on this side. So there is no third tier
 * and no upstream call: see `loadOne`, and register row A17.
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
 * §E13's ladder says a degradation should be designed rather than apologised
 * for, and the designed degradation for text is *the words are still there*.
 * Since ADR-0058 that is structural rather than defended: this function reads
 * compiled-in bundles and cannot fail on a network, so an unreachable control
 * plane costs a page nothing at all.
 *
 * **Still a promise, and no longer `async`.** Every caller is a server
 * component that already awaits this, and the seam is worth keeping: a tier
 * that reads anything but a compiled-in bundle is asynchronous again, and
 * unwinding forty `await`s to put one back is a change nobody should have to
 * make. Written as a promise-returning function rather than an `async` one so
 * the absence of anything to await is visible in the source instead of implied.
 */
export function loadStrings(
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
    const parts = (namespace as readonly Namespace[]).map((one) => loadOne(one, locale));
    const merged = parts.reduce<Readonly<Record<string, string>>>(
      (all, part) => mergeBundles(all, part.bundle),
      {},
    );
    const primary = (namespace as readonly Namespace[]).at(-1) ?? "common";
    return Promise.resolve(makeStrings(primary, locale, merged));
  }
  return Promise.resolve(loadOne(namespace as Namespace, locale));
}

// Not `async`, and that is ADR-0058 showing in the type: with the third tier
// gone there is nothing here to await. `loadStrings` stays `async` because it is
// the awaited seam every caller already holds and because a future tier would be
// asynchronous again; this one reads a compiled-in bundle.
function loadOne(namespace: Namespace, locale: string): Strings {
  // **Two tiers, and A17 is why there are not three — ADR-0058.**
  //
  // Source language, then the shipped seed for this locale, the second
  // overriding the first key by key. So a registry that has translated forty of
  // sixty keys renders forty translated and twenty in the source language — a
  // partially-localised product rather than a broken one.
  //
  // There used to be a third tier here: the Phase 5 locale registry, fetched
  // for `common`, `citizen`, `console` and `public`. **It could never resolve.**
  // `db/models/i18n.py` registers four namespaces — `taxonomy`,
  // `organisation`, `zone`, `calendar` — and refuses an import into any of
  // these; the reader does not validate its path parameter, so the request was
  // answered `200 {}` and the merge was a no-op. Nothing rendered wrong, which
  // is exactly what made it worth removing: a tier that can only ever return
  // nothing is a capability this code claimed and did not have, and §E3.3 is a
  // rule about precisely that.
  //
  // Phase 18's gate — *a locale added in the control plane appears in the UI
  // with no code change* — is untouched, because it was never about this half.
  // It governs **tenant-authored** words: a ward's name, a category's display
  // name. Those arrive through the public read, and `nem gate-phase18-locale`
  // asserts them end to end over HTTP against `zone`. Product copy is authored
  // by NEMESIS and reviewed like code, which is the line `db/models/i18n.py`
  // drew and ADR-0058 stopped contradicting.
  //
  // The guard `no-product-copy-from-the-registry` in `scripts/check-guards.ts`
  // keeps the tier from returning as three plausible-looking lines.
  return makeStrings(namespace, locale, shippedBundle(namespace, locale));
}

/** The keys this application asks for. Exported so a coverage test can compare
 *  them against what the control plane actually holds. */
export function baseKeys(namespace: Namespace): readonly string[] {
  return Object.keys(shippedBundle(namespace, SOURCE_LOCALE));
}
