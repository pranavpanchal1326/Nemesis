import type { Metadata } from "next";
import { headers } from "next/headers";

import { t } from "@/lib/i18n/strings";
import { staffDestinations } from "@/portal/destinations";
import { PortalAside, PortalHome } from "@/portal/PortalHome";
import { loadStrings, negotiateLocale, SEEDED_LOCALES } from "@/server/strings";

/**
 * The staff door — §E19, §E21.
 *
 * The console's five sections and the field app, generated from
 * `console/screens.ts` so the door cannot disagree with the rail or the palette
 * about what exists. Each destination carries the same §E24 chip its screen
 * carries, which means this page is also the shortest honest answer to *what is
 * actually wired?* — thirteen console screens, four of them real, and the rest
 * naming the phase that populates them.
 *
 * **`noindex`, unlike the citizen door.** Nothing here is secret — the chips
 * and the 404s do the protecting, and Phase 13 does the authenticating — but a
 * municipal back office in a search index invites the kind of traffic that
 * makes an unauthenticated deployment somebody's afternoon. §16.3 wants a
 * journalist to find a ward page; it does not want them to find the review
 * queue.
 */
export const metadata: Metadata = {
  title: "For staff",
  description: "The console, and the field app.",
  robots: { index: false, follow: false },
};

export default async function StaffPortal() {
  const requestHeaders = await headers();
  const locale = negotiateLocale({
    acceptLanguage: requestHeaders.get("accept-language"),
    available: SEEDED_LOCALES,
  });
  // `console` last: the door names the console's screens, and those names are
  // the console's own (`nav.*` lives there). `common` carries the chip's words
  // and everything shared.
  const strings = await loadStrings(["common", "console"], locale);

  return (
    <PortalHome
      strings={strings}
      audience="staff"
      groups={staffDestinations()}
      footer={
        <PortalAside
          href="/citizen"
          label={t(strings, "portal.toCitizen")}
          hint={t(strings, "portal.toCitizen.hint")}
        />
      }
    />
  );
}
