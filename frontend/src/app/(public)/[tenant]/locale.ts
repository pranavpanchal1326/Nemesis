import "server-only";

import { headers } from "next/headers";

import type { Strings } from "@/lib/i18n/strings";
import { loadStrings, negotiateLocale, SEEDED_LOCALES } from "@/server/strings";

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
  readonly strings: Strings;
}

export async function publicLocale(
  searchParams: Record<string, string | string[] | undefined>,
): Promise<PublicLocale> {
  const requested = searchParams["locale"];
  const explicit = Array.isArray(requested) ? requested[0] : requested;
  const requestHeaders = await headers();

  const locale = negotiateLocale({
    ...(explicit === undefined ? {} : { explicit }),
    acceptLanguage: requestHeaders.get("accept-language"),
    available: SEEDED_LOCALES,
  });

  return { locale, strings: await loadStrings(["common", "public"], locale) };
}
