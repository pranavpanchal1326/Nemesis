import type { Metadata } from "next";
import { headers } from "next/headers";

import { Press } from "@/press/Press";
import { fetchClayWorld } from "@/server/clay-data";
import {
  cityNameFallback,
  fetchCity,
  fetchPublishedLocales,
  publishedTenant,
} from "@/server/public-data";
import { loadStrings, negotiateLocale, SEEDED_LOCALES } from "@/server/strings";
import { directionOf } from "@/lib/i18n/direction";
import { Storyboard } from "@/story/Storyboard";
import { StoryShell } from "@/story/StoryShell";
import { Walk } from "@/story/Walk";
import type { StoryZone } from "@/story/acts/close";

/**
 * The landing — §E16, M9.
 *
 * > **The film never cuts to the product. The film becomes the product.**
 *
 * A server component, which is what makes that sentence survivable. The film's
 * copy, the city's published places and §44's honesty table are all rendered
 * into the HTML here, so §E13's Tier D — *"semantic article, all copy present,
 * public data server-rendered"* — is the page's actual default rather than a
 * branch somebody has to remember to maintain. `<Walk>` adds the camera and the
 * scroll on top of it.
 *
 * **Which city the film is a film of.** `NEMESIS_STORY_TENANT` names the
 * published slug this deployment's landing draws. It is its own variable rather
 * than `NEMESIS_TENANT_ID` because the two are different questions: the tenant
 * id is the trust boundary the BFF holds (ADR-0040), and this is a *slug the
 * city has already chosen to publish* (ADR-0046). A deployment that has not
 * published anything sets nothing, and the landing prints the storyboard —
 * nine acts, the same words, and no claim about a place. That is the honest
 * answer, and it is the reason there is no fallback to a demo city here:
 * shipping somebody else's wards on your own landing page is exactly the
 * "confidently wrong screen" §E3.3 exists to forbid.
 */

export const metadata: Metadata = {
  title: "NEMESIS — AI Civic Operations Agent",
  description:
    "A scroll-driven film in nine acts that becomes the product. Every scene is fired by a real event from the pipeline, and the last act publishes what is real, what is simulated, and what is still a roadmap item.",
  alternates: { canonical: "/" },
};

/** The ink set §E9.2 assigns to this surface. */
const SURFACE = "story";

/**
 * A fixed seed for the generated city.
 *
 * The landing is the most-photographed surface in the product and §E24 wants
 * golden images at a fixed seed; a landing whose skyline was different on every
 * request would make every one of them a new baseline. ADR-0047 already
 * establishes that the city is generated and means nothing — so pinning it
 * costs no truth and buys a reviewable picture.
 */
const CITY_SEED = 1;

export default async function Landing({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const requested = params["locale"];
  const explicit = Array.isArray(requested) ? requested[0] : requested;

  const slug = publishedTenant() ?? null;
  const available = slug === null ? SEEDED_LOCALES : await fetchPublishedLocales(slug);
  const locale = negotiateLocale({
    ...(explicit === undefined ? {} : { explicit }),
    acceptLanguage: (await headers()).get("accept-language"),
    available: available.length === 0 ? SEEDED_LOCALES : available,
  });
  const strings = await loadStrings(["common", "public"], locale);

  if (slug === null) {
    // Nothing published, so nothing to be a film of. §E13's Tier C is a design
    // deliverable rather than an apology, and this is the one situation where
    // it is also the *correct* rendering rather than a lower rung.
    return (
      <Press quality="full" surface={SURFACE}>
        <Storyboard strings={strings} city={cityNameFallback("")} />
      </Press>
    );
  }

  const [world, city] = await Promise.all([fetchClayWorld(slug, locale), fetchCity(slug, locale)]);

  // Places, not figures. The film links to a ward and names it; every number
  // about that ward stays on the ward's own page, where `PublishedFigure` makes
  // a suppressed count impossible to interpolate into JSX (M6). A landing page
  // is the last surface that should be re-deriving a k-anonymity rule.
  const zones: readonly StoryZone[] = city.ok
    ? city.value.zones.map((zone) => ({ code: zone.zoneCode, name: zone.zoneName }))
    : [];

  return (
    <div dir={directionOf(locale)} lang={locale}>
      <Press quality="full" surface={SURFACE}>
        <StoryShell strings={strings}>
          <Walk
            strings={strings}
            locale={locale}
            entities={world.entities}
            origin={world.origin}
            weather={world.weather}
            surface={SURFACE}
            city={world.cityName ?? cityNameFallback(slug)}
            citySlug={slug}
            zones={zones}
            publicApiBase={process.env["NEMESIS_PUBLIC_API_URL"] ?? null}
            seed={CITY_SEED}
          />
        </StoryShell>
      </Press>
    </div>
  );
}
