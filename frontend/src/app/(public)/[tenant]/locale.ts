import "server-only";

import { headers } from "next/headers";

import { directionOf, type Direction } from "@/lib/i18n/direction";
import type { Strings } from "@/lib/i18n/strings";
import { fetchPublishedLocales } from "@/server/public-data";
import { loadStrings, negotiateLocale } from "@/server/strings";

/**
 * One locale negotiation for the whole §E18 surface.
 *
 * Five pages needed the same six lines, and five copies of a negotiation is
 * five places for one of them to forget `?locale=`. The explicit choice comes
 * from the query string because §E13 Tier D has no JavaScript to hold a
 * preference and no cookie the page can set without one — a link is the only
 * language switch that works on the surface a crawler reads.
 *
 * `common` is loaded beside `public`: `<SuppressionNotice>` and
 * `<ContractorLedger>` say the same sentences here that they say on the citizen
 * and officer surfaces, and they say them from `common` so they cannot drift
 * into three wordings of one rule.
 */
export interface PublicLocale {
  readonly locale: string;
  /** Which way the frame runs (A11). Derived, never chosen: a direction that
   *  could be set independently of the locale is a direction that will
   *  eventually disagree with it. */
  readonly direction: Direction;
  /** Every locale the *tenant* declares, primary first — the list the language
   *  switch is built from, so adding one upstream needs no code here. */
  readonly locales: readonly string[];
  readonly strings: Strings;
}

export async function publicLocale(
  tenant: string,
  searchParams: Record<string, string | string[] | undefined>,
): Promise<PublicLocale> {
  const requested = searchParams["locale"];
  const explicit = Array.isArray(requested) ? requested[0] : requested;
  const requestHeaders = await headers();
  // The tenant's list, not this application's. `available` is what the reader
  // may be given, and the control plane is the only thing that knows it.
  const locales = await fetchPublishedLocales(tenant);

  const locale = negotiateLocale({
    ...(explicit === undefined ? {} : { explicit }),
    tenantDefault: locales[0],
    acceptLanguage: requestHeaders.get("accept-language"),
    available: locales,
  });

  return {
    locale,
    direction: directionOf(locale),
    locales,
    strings: await loadStrings(["common", "public"], locale),
  };
}
