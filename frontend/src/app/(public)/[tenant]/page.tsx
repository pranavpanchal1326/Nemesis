import type { Metadata } from "next";
import Link from "next/link";

import { notTranslatable, t, type Strings } from "@/lib/i18n/strings";
import { orderZones, type PublishedZone } from "@/public/figures";
import { PublicShell } from "@/public/PublicShell";
import { cityNameFallback, fetchCity } from "@/server/public-data";
import { publicLocale } from "./locale";

/**
 * The city — §E18, §16.2.
 *
 * A directory of every place this city publishes, and the first page a search
 * engine or a journalist lands on. Nothing here is a dashboard: no leaderboard,
 * no "worst ward", no ranking. §16.1's argument about collapsing a contractor to
 * one number applies to a ward with more force — a ward that ranks badly is
 * usually a ward that *reports* well, and a table sorted by complaint count
 * publishes that inversion as if it were a finding.
 *
 * So the places are ordered by kind and then alphabetically, and the cards carry
 * no figures at all — see `<PlaceGroup>` for why that omission is the design
 * rather than an oversight. Any comparison a reader wants to make, they make by
 * opening two pages, having read what the figures are and what is withheld.
 */
type Params = Promise<{ tenant: string }>;
type Search = Promise<Record<string, string | string[] | undefined>>;

export async function generateMetadata({
  params,
  searchParams,
}: {
  params: Params;
  searchParams: Search;
}): Promise<Metadata> {
  const { tenant } = await params;
  // C8: the title says the city's own name for itself, not a title-cased slug.
  // The fetch is the same one the body makes and Next de-duplicates it within
  // the request, so this is a second read of one response rather than a second
  // round trip.
  const { locale } = await publicLocale(tenant, await searchParams);
  const read = await fetchCity(tenant, locale);
  const city = read.ok ? read.value.cityName : cityNameFallback(tenant);
  return {
    title: city,
    description: `Complaint and resolution figures published by ${city}.`,
    alternates: { canonical: `/${tenant}` },
  };
}

export default async function CityIndex({
  params,
  searchParams,
}: {
  params: Params;
  searchParams: Search;
}) {
  const { tenant } = await params;
  const { locale, locales, strings } = await publicLocale(tenant, await searchParams);
  const read = await fetchCity(tenant, locale);

  if (!read.ok) {
    // Nothing published means no name to publish either, so the heading falls
    // back to the slug — the one place a guess is still the honest answer.
    return (
      <NotPublishing
        city={cityNameFallback(tenant)}
        citySlug={tenant}
        locale={locale}
        strings={strings}
      />
    );
  }

  const city = read.value.cityName;

  const ordered = orderZones(read.value.zones);
  const wards = ordered.filter((zone) => zone.zoneKind === "ward");
  const others = ordered.filter((zone) => zone.zoneKind !== "ward");

  return (
    <PublicShell
      city={city}
      citySlug={tenant}
      strings={strings}
      locale={locale}
      locales={locales}
      generatedAt={read.value.generatedAt}
      notice={read.value.notice}
    >
      <div className="place-index">
        <h1 className="type-display-1">{t(strings, "city.places")}</h1>
        <PlaceGroup heading={t(strings, "city.wards")} zones={wards} tenant={tenant} />
        <PlaceGroup heading={t(strings, "city.otherPlaces")} zones={others} tenant={tenant} />
      </div>
    </PublicShell>
  );
}

function PlaceGroup({
  heading,
  zones,
  tenant,
}: {
  readonly heading: string;
  readonly zones: readonly PublishedZone[];
  readonly tenant: string;
}) {
  if (zones.length === 0) return null;

  return (
    <section>
      <h2 className="type-heading">{heading}</h2>
      <ul className="place-index__list">
        {zones.map((zone) => (
          <li key={zone.zoneCode} className="place-index__item">
            <Link href={`/${tenant}/ward/${zone.zoneCode}`}>
              <span className="place-index__kind type-micro">{notTranslatable(zone.zoneCode)}</span>
              <span className="type-heading">{notTranslatable(zone.zoneName)}</span>
              {/*
               * Deliberately no figure on the card.
               *
               * The obvious design puts the report count here, and it is the
               * wrong one twice over. It turns a directory into a league table
               * (see the page docstring), and it would have to answer the
               * suppression question in a space with no room for the sentence —
               * which is how a k-anonymity hole ends up rendered as a blank,
               * which is the thing ADR-0021 exists to prevent. The figures are
               * one click away, with their notice above them.
               */}
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * A city that does not publish.
 *
 * `public_deps.py` answers 404 for both "no such tenant" and "exists but has
 * not opted in", and it does so on purpose: *"a distinguishable 'exists but is
 * not publishing' would still confirm the customer list to anyone who wanted to
 * compile one."* This page cannot tell those apart and does not pretend to. It
 * says what is true of both — there is nothing here — and says why the absence
 * is a decision rather than a fault (ADR-0046).
 */
function NotPublishing({
  city,
  citySlug,
  locale,
  strings,
}: {
  readonly city: string;
  readonly citySlug: string;
  readonly locale: string;
  readonly strings: Strings;
}) {
  return (
    <PublicShell city={city} citySlug={citySlug} strings={strings} locale={locale}>
      <div className="place-index">
        <h1 className="type-display-1">{t(strings, "city.notPublishing")}</h1>
        <p className="type-body">{t(strings, "city.notPublishingWhy")}</p>
      </div>
    </PublicShell>
  );
}
