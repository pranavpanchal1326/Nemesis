import type { Metadata } from "next";
import { headers } from "next/headers";

import { ReportFlow } from "@/citizen/ReportFlow";
import { loadStrings, negotiateLocale, SEEDED_LOCALES } from "@/server/strings";

/**
 * §E17.1 — **the app opens in the viewfinder, not on a form.**
 *
 * A server component whose whole body is locale negotiation. The flow beneath
 * it is a client component because a camera, a microphone and an optimistic
 * mutation are all things only a browser can do — but the words it renders were
 * resolved on the server, so the first paint is in the right language and the
 * right script rather than in English until hydration.
 *
 * The locale is negotiated twice, here and in the layout, and that is not
 * redundant: `negotiateLocale` is pure and cheap, and passing it down would
 * mean the page could not be rendered independently — which is exactly what
 * Next does when it re-renders a page without its layout.
 */
export const metadata: Metadata = {
  title: "Report",
  // §E23 budgets LCP < 2.0 s on this route. Nothing here is prefetchable and
  // the camera cannot start before consent, so the honest optimisation is to
  // ship less rather than to preload more.
  description: "Photograph it, say where, send. The city has to answer.",
};

export default async function Report() {
  const requestHeaders = await headers();
  const locale = negotiateLocale({
    acceptLanguage: requestHeaders.get("accept-language"),
    available: SEEDED_LOCALES,
  });
  const strings = await loadStrings("common", locale);

  return <ReportFlow strings={strings} locale={locale} />;
}
