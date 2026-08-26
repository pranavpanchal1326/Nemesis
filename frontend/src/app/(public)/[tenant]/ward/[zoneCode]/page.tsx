import type { Metadata } from "next";
import Link from "next/link";

import { notTranslatable, t } from "@/lib/i18n/strings";
import { PlaceHeading, PublicShell } from "@/public/PublicShell";
import { ZonePanel } from "@/public/ZonePanel";
import { cityNameFallback, fetchPlace } from "@/server/public-data";
import { publicLocale } from "../../locale";

/**
 * One place — §E18, §16.2, ADR-0021.
 *
 * The page a ward councillor's opponent links to, and the page this whole
 * surface is judged on. Every figure arrives through `readZone`, so a
 * suppressed place renders *"fewer than five reports — withheld to protect
 * reporters"* in six cells rather than six zeros. The gate for this milestone
 * is that a k-anonymity hole never renders as a zero, and it is closed in
 * `src/public/figures.ts` by making the raw number impossible to interpolate.
 *
 * **The URL segment is `/ward/`, matching v1's path.** v2 renames it to
 * `/zone/`, which ADR-0018 argues is the accurate word, and this URL follows
 * when v2 leaves preview. A citable URL cannot be built on a contract that
 * carries no compatibility promise — §16.2 wants these bookmarked, and a
 * bookmark that breaks discredits the page it pointed at.
 */
type Params = Promise<{ tenant: string; zoneCode: string }>;
type Search = Promise<Record<string, string | string[] | undefined>>;

export async function generateMetadata({
  params,
  searchParams,
}: {
  params: Params;
  searchParams: Search;
}): Promise<Metadata> {
  const { tenant, zoneCode } = await params;
  const { locale } = await publicLocale(tenant, await searchParams);
  const read = await fetchPlace(tenant, zoneCode, locale);
  const city = read.ok ? read.value.cityName : cityNameFallback(tenant);

  if (!read.ok) {
    // A 404 must not be indexed, and it must not carry a title that reads like
    // a real place. `robots: noindex` here rather than only a status code,
    // because a crawler that already has the URL asks again.
    return { title: city, robots: { index: false, follow: false } };
  }

  return {
    title: `${read.value.zoneName} · ${city}`,
    description: read.value.suppressed
      ? `${read.value.zoneName}: too few reports to publish figures without identifying the people who filed them.`
      : `Complaint and resolution figures for ${read.value.zoneName}, published by ${city}.`,
    alternates: { canonical: `/${tenant}/ward/${zoneCode}` },
  };
}

export default async function Place({
  params,
  searchParams,
}: {
  params: Params;
  searchParams: Search;
}) {
  const { tenant, zoneCode } = await params;
  const { locale, locales, strings } = await publicLocale(tenant, await searchParams);
  const read = await fetchPlace(tenant, zoneCode, locale);
  const city = read.ok ? read.value.cityName : cityNameFallback(tenant);

  if (!read.ok) {
    return (
      <PublicShell
        city={city}
        citySlug={tenant}
        strings={strings}
        locale={locale}
        locales={locales}
      >
        <h1 className="type-display-1">{t(strings, "place.notFound")}</h1>
        <p className="type-body">{t(strings, "place.notFoundWhy")}</p>
        <p className="type-caption">
          <Link href={`/${tenant}`}>{t(strings, "place.back")}</Link>
        </p>
      </PublicShell>
    );
  }

  const zone = read.value;

  return (
    <PublicShell
      city={city}
      citySlug={tenant}
      strings={strings}
      locale={locale}
      locales={locales}
      generatedAt={zone.generatedAt}
      notice={zone.notice}
    >
      <PlaceHeading name={zone.zoneName} kind={zone.zoneKind} strings={strings} />

      <p className="type-caption">
        {zone.centroid === null
          ? t(strings, "place.noCentroid")
          : t(strings, "place.centroid", {
              // Coarsened upstream to `GPS_DECIMALS` before it ever reaches
              // here; printed as it arrives rather than re-rounded, so the
              // page and the API agree digit for digit.
              lat: zone.centroid.lat,
              lng: zone.centroid.lng,
            })}
      </p>

      <ZonePanel zone={zone} strings={strings} />

      {/* A navigation row, not a sentence — so WCAG 2.2's 2.5.8 applies to it
          in full and the interpuncts are separators rather than prose. The
          class gives each link a 24 px box without touching §E10's type. */}
      <nav className="public__place-nav type-caption">
        <Link href={`/${tenant}/budget/${zoneCode}`}>{t(strings, "place.budget")}</Link>
        {" · "}
        <Link href={`/${tenant}`}>{t(strings, "place.back")}</Link>
        {" · "}
        <span>{notTranslatable(zone.zoneCode)}</span>
      </nav>
    </PublicShell>
  );
}
