import type { Metadata } from "next";
import { headers } from "next/headers";

import { TrackScreen } from "@/citizen/TrackScreen";
import { loadStrings, negotiateLocale, SEEDED_LOCALES } from "@/server/strings";

/**
 * §E17.4 — **Track: a ledger, not a status badge.**
 *
 * The URL is the capability. A complaint id is a UUIDv4 the system hands to the
 * submitter on their receipt, and ADR-0043 calibrates what this page may show
 * to exactly that: more than the anonymous broadcast, less than Phase 13's
 * authenticated officer.
 *
 * `robots: noindex` because the id is a capability. A tracking page in a search
 * index is a capability in a search index — and unlike §E18's public ward pages,
 * which are indexable on purpose, nothing here is meant to be found by anybody
 * who was not given the link.
 */
export const metadata: Metadata = {
  title: "Your report",
  robots: { index: false, follow: false },
};

export default async function Track({ params }: { params: Promise<{ complaintId: string }> }) {
  const { complaintId } = await params;
  const requestHeaders = await headers();
  const locale = negotiateLocale({
    acceptLanguage: requestHeaders.get("accept-language"),
    available: SEEDED_LOCALES,
  });
  const strings = await loadStrings("common", locale);

  return <TrackScreen complaintId={complaintId} strings={strings} />;
}
