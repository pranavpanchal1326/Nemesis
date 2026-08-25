import { ImageResponse } from "next/og";

import { shippedStrings } from "@/lib/i18n/bundles";
import { t } from "@/lib/i18n/strings";
import { SHARE_CONTENT_TYPE, SHARE_SIZE, ShareCard, shareFonts } from "@/public/share-card";
import { cityName, fetchCity } from "@/server/public-data";

/**
 * The city's share card — §E18.
 *
 * **It carries no figures, and that is the design.** A city-level total is the
 * one number on this surface that would immediately be read as a score — *"Pune:
 * 4,100 complaints"* is a headline about a city rather than a fact about a
 * service, and it is exactly the ranking §16.1's reasoning refuses. What the
 * card carries instead is how many places publish, which is a claim about the
 * *transparency*, which is what this surface is.
 */
export const alt = "Transparency figures published by a city";
export const size = SHARE_SIZE;
export const contentType = SHARE_CONTENT_TYPE;

export default async function Image({ params }: { params: Promise<{ tenant: string }> }) {
  const { tenant } = await params;
  const city = cityName(tenant);
  const strings = shippedStrings(["common", "public"], "en");
  const read = await fetchCity(tenant);

  if (!read.ok) {
    return new ImageResponse(
      (
        <ShareCard
          city={city}
          kicker={tenant}
          title={t(strings, "city.notPublishing")}
          figures={[]}
          notice={t(strings, "city.notPublishingWhy")}
        />
      ),
      { ...size, fonts: await shareFonts() },
    );
  }

  const places = read.value.zones.length;

  return new ImageResponse(
    (
      <ShareCard
        city={t(strings, "share.city")}
        kicker={tenant}
        title={city}
        notice={read.value.notice}
        figures={[
          {
            label: t(strings, "city.places"),
            value: new Intl.NumberFormat(strings.locale).format(places),
            withheld: false,
          },
        ]}
      />
    ),
    { ...size, fonts: await shareFonts() },
  );
}
