import type { Metadata } from "next";
import { headers } from "next/headers";

import { t } from "@/lib/i18n/strings";
import { citizenDestinations } from "@/portal/destinations";
import { PortalAside, PortalHome } from "@/portal/PortalHome";
import { TrackForm } from "@/portal/TrackForm";
import { loadStrings, negotiateLocale, SEEDED_LOCALES } from "@/server/strings";
import { publishedTenant } from "@/server/public-data";

/**
 * The resident's door — §E17, §E18.
 *
 * Everything a person who lives here can do, on one page, in the order they
 * would want it: report the thing, follow the report they already filed, read
 * what the city publishes about their ward, and read what this product will not
 * claim about itself.
 *
 * **Indexable, unlike the surfaces it points at.** `/report` is `noindex`
 * because it is a camera, and `/t/[id]` is `noindex` because an id is a
 * capability (ADR-0043). This page holds neither: it is the one address worth
 * printing on a poster, so it is the one that should be findable.
 */
export const metadata: Metadata = {
  title: "For residents",
  description: "Report a problem, follow a report you filed, and read what the city publishes.",
  robots: { index: true, follow: true },
  alternates: { canonical: "/citizen" },
};

export default async function CitizenPortal({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const requestHeaders = await headers();
  const locale = negotiateLocale({
    acceptLanguage: requestHeaders.get("accept-language"),
    available: SEEDED_LOCALES,
  });
  // Two namespaces: the door's own words are in `common` with the rest of the
  // citizen product, and `public` carries the sentences the transparency pages
  // print — which this page quotes when it points at them.
  const strings = await loadStrings(["common", "public"], locale);

  const rejectedParam = (await searchParams)["rejected"];
  const rejected = typeof rejectedParam === "string" ? rejectedParam : undefined;

  return (
    <PortalHome
      strings={strings}
      audience="citizen"
      groups={citizenDestinations(publishedTenant())}
      footer={
        <PortalAside
          href="/staff"
          label={t(strings, "portal.toStaff")}
          hint={t(strings, "portal.toStaff.hint")}
        />
      }
    >
      <TrackForm strings={strings} rejected={rejected} />
    </PortalHome>
  );
}
