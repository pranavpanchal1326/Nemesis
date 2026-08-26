import { ImageResponse } from "next/og";

import { shippedStrings } from "@/lib/i18n/bundles";
import { plural, t } from "@/lib/i18n/strings";
import type { PublishedFigure } from "@/public/figures";
import {
  SHARE_CONTENT_TYPE,
  SHARE_SIZE,
  ShareCard,
  shareFonts,
  type ShareFigure,
} from "@/public/share-card";
import { cityNameFallback, fetchPlace } from "@/server/public-data";

/**
 * A place's share card — §E18.
 *
 * > `satori` + `resvg` share cards, server-rendered per complaint and per ward.
 *
 * **The suppression rule follows the figure onto the card**, which is the whole
 * reason this is not three lines of template. A withheld count rendered as `0`
 * in a message thread is the k-anonymity misreading published somewhere nobody
 * can click through to the explanation — so `shareFigure` reads the same
 * `PublishedFigure` the page reads and renders the same three states.
 *
 * `alt` is not decoration either: it is the card for anybody whose client does
 * not load images, and it carries the same figures rather than the file's name.
 */
export const alt = "Complaint and resolution figures for one place";
export const size = SHARE_SIZE;
export const contentType = SHARE_CONTENT_TYPE;

export default async function Image({
  params,
}: {
  params: Promise<{ tenant: string; zoneCode: string }>;
}) {
  const { tenant, zoneCode } = await params;
  const read = await fetchPlace(tenant, zoneCode, "en");
  const city = read.ok ? read.value.cityName : cityNameFallback(tenant);

  // The source language, deliberately. A share card has no request headers to
  // negotiate from — it is fetched by a crawler on behalf of whoever pasted the
  // link, and guessing that person's language from the crawler's `Accept-
  // Language` would be guessing about the wrong person. A per-locale card wants
  // `generateImageMetadata` and a locale in the URL, which is a decision for
  // when a second locale is actually published.
  const strings = shippedStrings(["common", "public"], "en");

  if (!read.ok) {
    return new ImageResponse(
      <ShareCard
        city={city}
        kicker={zoneCode}
        title={t(strings, "place.notFound")}
        figures={[]}
        notice={t(strings, "place.notFoundWhy")}
      />,
      { ...size, fonts: await shareFonts() },
    );
  }

  const zone = read.value;

  return new ImageResponse(
    <ShareCard
      city={city}
      kicker={zone.zoneCode}
      title={zone.zoneName}
      notice={zone.notice}
      figures={[
        shareFigure(t(strings, "figure.total"), zone.totalReports, strings),
        shareFigure(t(strings, "figure.open"), zone.openReports, strings),
        shareFigure(t(strings, "figure.resolved"), zone.resolvedReports, strings),
      ]}
    />,
    { ...size, fonts: await shareFonts() },
  );
}

/**
 * One figure, decided the same way `<Figure>` decides it.
 *
 * Duplicated rather than shared with `<Figure>` because `satori` renders a
 * subset of CSS and cannot take that component — but the *decision* is not
 * duplicated: both switch on the same `PublishedFigure` union, so a fourth
 * state added to it fails to compile in both places rather than silently
 * falling through to a number on the card.
 */
function shareFigure(
  label: string,
  figure: PublishedFigure,
  strings: ReturnType<typeof shippedStrings>,
): ShareFigure {
  switch (figure.kind) {
    case "withheld":
      return {
        label,
        value: plural(strings, "suppression.withheld", figure.threshold, {
          threshold: figure.threshold,
        }),
        withheld: true,
      };
    case "unknown":
      return { label, value: t(strings, "figure.unknown"), withheld: true };
    case "known":
      return figure.value === 0
        ? { label, value: t(strings, "figure.none"), withheld: true }
        : {
            label,
            value: new Intl.NumberFormat(strings.locale).format(figure.value),
            withheld: false,
          };
  }
}
