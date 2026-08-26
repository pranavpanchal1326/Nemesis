import type { Metadata } from "next";
import { headers } from "next/headers";

import { FieldScreen } from "@/field/FieldScreen";
import { loadStrings, negotiateLocale, SEEDED_LOCALES } from "@/server/strings";

/**
 * §E21 — three jobs, a camera, and a queue.
 *
 * A server component whose whole body is locale negotiation, exactly as
 * `/report` is. Everything below it needs a browser: a geolocation watch, a
 * camera, and IndexedDB.
 *
 * **Not `devOnly()`.** The job list is behind the not-wired chip because Phase
 * 14 owns work orders, but the *capture* half of this screen is real and
 * reaches the real ingest endpoint — so this is a route a pilot can actually
 * hand to a field team, which is what §E21's own placement in the plan
 * ("independent of Stages 2–3 — this phase can be pulled forward if a pilot
 * needs it") assumes.
 */
export const metadata: Metadata = {
  title: "Field",
  description: "Photograph the job, offline. It sends itself when there is signal.",
};

export default async function Field() {
  const requestHeaders = await headers();
  const locale = negotiateLocale({
    acceptLanguage: requestHeaders.get("accept-language"),
    available: SEEDED_LOCALES,
  });
  const strings = await loadStrings("common", locale);

  return <FieldScreen strings={strings} locale={locale} />;
}
