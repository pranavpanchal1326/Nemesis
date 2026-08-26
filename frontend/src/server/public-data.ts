import "server-only";

import {
  readBudget,
  readContractor,
  readZone,
  type PublishedBudget,
  type PublishedContractor,
  type PublishedZone,
} from "@/public/figures";
import { SEEDED_LOCALES, SOURCE_LOCALE } from "@/lib/i18n/bundles";
import { upstream } from "@/server/upstream";

/**
 * The §26.4 reads, server-side — §E18, ADR-0040.
 *
 * **Why these do not go through a BFF route handler, when M3's rule is that
 * every browser-to-API read does.** They are not browser-to-API reads. §E18's
 * pages are server-rendered and their data is fetched during the render, on the
 * server, by a server component — the browser receives HTML and never issues
 * the request. A route handler in front of that would be the server calling
 * itself over HTTP to reach the module it already imports.
 *
 * ADR-0040's seam is intact and is being *used* rather than bypassed: the
 * tenant header is still applied by `upstream`, the browser still never names a
 * tenant, and `import "server-only"` still makes a client-bundle import a build
 * error. The seam was always about who holds the credential, and on this
 * surface the answer is the same server that renders the page.
 *
 * It is also the only shape that satisfies §E13 Tier D. A page whose data
 * arrives through a client fetch is a page with no data when JavaScript is off,
 * and Tier D is not a fallback here — it is what a crawler and a 2G phone
 * actually get.
 *
 * **The tenant slug is a path segment, not a header, and that is deliberate at
 * both ends.** `api/public_deps.py`: *"asking a journalist to obtain a UUID
 * before they can call a public API defeats the purpose."* So `/[tenant]` in
 * the URL maps straight onto `{tenant_slug}` upstream, and the page is
 * bookmarkable and citable — §16.2's actual requirement.
 */

/**
 * Cache the way the upstream asks to be cached.
 *
 * `public/aggregates.py` sets `Cache-Control: public, max-age=300` and states
 * why it is `public` rather than `private`: the responses carry nothing
 * caller-specific, which is a property of the scrub. Mirroring that number here
 * rather than inventing one means a change on either side is a change to one
 * documented figure instead of a drift between two.
 */
const REVALIDATE_SECONDS = 300;

const cache = { next: { revalidate: REVALIDATE_SECONDS } } as const;

/** A read that came back 404. Distinguished from an error because a 404 on this
 *  surface is a *rendering* — "this city does not publish", "no such place" —
 *  and not a failure. The route turns each into its own honest page. */
export type PublicRead<T> = { readonly ok: true; readonly value: T } | { readonly ok: false };

const MISSING = { ok: false } as const;

/**
 * Every place a city publishes, plus the city's own name for itself.
 *
 * The discovery endpoint exists for the reason its docstring gives — *"three
 * endpoints keyed by identifiers a caller cannot learn is a public API that
 * only works for people holding an internal document"* — and it is what makes
 * `/[tenant]` a page rather than a 404 waiting for a zone code.
 */
export async function fetchCity(
  slug: string,
  locale: string,
): Promise<
  PublicRead<{
    readonly zones: readonly PublishedZone[];
    readonly notice: string;
    readonly noticeLocale: string;
    readonly cityName: string;
    readonly generatedAt: string;
  }>
> {
  const { data, error } = await upstream.GET("/api/v1/public/{tenant_slug}/zones", {
    params: { path: { tenant_slug: slug }, query: { limit: 200, locale } },
    ...cache,
  });
  if (error !== undefined) return MISSING;

  return {
    ok: true,
    value: {
      zones: data.zones.map(readZone),
      notice: data.notice,
      noticeLocale: data.notice_locale,
      cityName: data.tenant_name,
      generatedAt: data.generated_at,
    },
  };
}

/**
 * One place.
 *
 * **v1's `/ward/` path, not v2's `/zone/`.** v2 is the accurate noun (ADR-0018)
 * and it is marked *preview* in the version registry, which means it carries no
 * compatibility promise. A public, indexable, citable URL cannot rest on a
 * contract that may be reshaped — §16.2 wants these bookmarked by journalists,
 * and a bookmark that breaks on a preview version bump is a bookmark that
 * discredits the page it points at. The URL segment is `/ward/` for the same
 * reason and moves when v2 goes stable.
 */
export async function fetchPlace(
  slug: string,
  zoneCode: string,
  locale: string,
): Promise<PublicRead<PublishedZone>> {
  const { data, error } = await upstream.GET(
    "/api/v1/public/{tenant_slug}/ward/{zone_code}/summary",
    { params: { path: { tenant_slug: slug, zone_code: zoneCode }, query: { locale } }, ...cache },
  );
  if (error !== undefined) return MISSING;
  return { ok: true, value: readZone(data) };
}

/** §16.1's track record. Never a rating — the API publishes no collapsible number. */
export async function fetchContractor(
  slug: string,
  contractorId: string,
  locale: string,
): Promise<PublicRead<PublishedContractor>> {
  const { data, error } = await upstream.GET(
    "/api/v1/public/{tenant_slug}/contractor/{contractor_id}/profile",
    {
      params: { path: { tenant_slug: slug, contractor_id: contractorId }, query: { locale } },
      ...cache,
    },
  );
  if (error !== undefined) return MISSING;
  return { ok: true, value: readContractor(data) };
}

/**
 * §17.6's ward budget for one financial year.
 *
 * `fiscal_year` is a **required** query parameter upstream, so it cannot be
 * defaulted here without this module inventing a tenant's financial calendar.
 * The route reads it from the URL and states which year it is showing, which is
 * also the only version a citation can be made against.
 */
export async function fetchBudget(
  slug: string,
  zoneCode: string,
  fiscalYear: string,
  locale: string,
): Promise<PublicRead<PublishedBudget>> {
  const { data, error } = await upstream.GET("/api/v1/public/{tenant_slug}/budget/{zone_code}", {
    params: {
      path: { tenant_slug: slug, zone_code: zoneCode },
      query: { fiscal_year: fiscalYear, locale },
    },
    ...cache,
  });
  if (error !== undefined) return MISSING;
  return { ok: true, value: readBudget(data) };
}

/**
 * Every locale this tenant declares — A2, A11.
 *
 * Phase 18's gate is *"a locale added in the control plane appears in the UI
 * with no code change"*, and until F2 the language switch was a two-element
 * array in `<PublicShell>` with a comment admitting it should not be. A locale
 * added upstream therefore appeared in **no** switch, which made the gate
 * unmeetable by construction rather than by oversight.
 *
 * **The cost, stated.** This is a second upstream read on a page that already
 * makes one. It asks for a single zone because the envelope is what it wants
 * and the zones are not, it is cached for the same five minutes as everything
 * else on this surface, and Next de-duplicates it within a render. A page that
 * offers a language nobody configured, or hides one somebody did, is worse than
 * a cached request.
 *
 * A failed read returns the locales this application ships, so the switch
 * degrades to what can certainly be rendered rather than disappearing — §E13's
 * ladder, applied to a control.
 */
export async function fetchPublishedLocales(slug: string): Promise<readonly string[]> {
  const { data, error } = await upstream.GET("/api/v1/public/{tenant_slug}/zones", {
    params: { path: { tenant_slug: slug }, query: { limit: 1, locale: SOURCE_LOCALE } },
    ...cache,
  });
  if (error !== undefined) return SEEDED_LOCALES;

  // Read defensively even though the contract says this field is required.
  // The generated types describe the API this build was generated against, and
  // a frontend deployed ahead of its backend is an ordinary Tuesday — the
  // additive field simply is not there yet. Trusting the type here turns that
  // into a 500 on a public page, which is a worse answer than a switch with
  // two entries in it.
  // The cast is the whole point: it widens the *generated* type back to what a
  // running server may actually have sent. Without it the compiler — reading
  // the contract rather than the deployment — proves the check below is
  // redundant, and the lint rule deletes the only thing standing between an
  // older backend and a 500 on a public page.
  const declared = (data as { locales?: readonly string[] }).locales;
  return declared === undefined || declared.length === 0 ? SEEDED_LOCALES : declared;
}

/**
 * The city's display name **when there is no published body to read it from**.
 *
 * C8 landed with ADR-0052: every public response now carries `tenant_name`, and
 * every surface above that has one uses it. This remains for the two cases that
 * have no response to read:
 *
 * * a fetch that failed or a tenant that does not publish — the page still
 *   needs a heading to say *"this city does not publish"* under;
 * * `/[tenant]/honesty`, which is generated from the blueprints at build time
 *   and calls no API at all.
 *
 * Title-casing a slug is a guess, and it is confined here so it cannot be
 * mistaken for the published fact. A lookup table of cities we happen to know
 * about would still be the wrong answer, for the reason it always was: a second
 * source of truth for something the platform holds.
 */
export function cityNameFallback(slug: string): string {
  // An id is not a name, and title-casing one produces a string that *looks*
  // like a name and is not: "672c8898 6103 49d2 B45d C04932c03873" shipped in
  // the console masthead and in the clay layer's caption. Returned unchanged
  // instead, so it reads as the identifier it is — §E3.3 applied to a fallback,
  // which is exactly where a small dishonesty is easiest to miss.
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(slug)) return slug;

  return slug
    .split("-")
    .map((part) => (part === "" ? part : `${part[0]?.toUpperCase() ?? ""}${part.slice(1)}`))
    .join(" ");
}

/**
 * The slug this deployment has published under, if it has published at all.
 *
 * **Not `resolveTenant()`, and the difference is the whole point.**
 * `NEMESIS_TENANT_ID` is the trust boundary the BFF holds — a tenant *id*, an
 * opaque UUID, never in a URL a person reads (ADR-0040). §E18's addresses are
 * slugs, and a slug exists only because a city chose to publish under it
 * (ADR-0046). Linking a resident at `/{id}` produced a page that answered, in
 * the sense that it rendered *"this city does not publish"* about a tenant that
 * does — which is the worst kind of working link.
 *
 * **The variable is named for the landing, and the concept is not.**
 * `NEMESIS_STORY_TENANT` was introduced for §E16's film, whose own docstring
 * gives the reasoning this function inherits: *"a deployment that has not
 * published anything sets nothing"*, and nothing falls back to a demo city,
 * because shipping somebody else's wards under your own address is the
 * confidently wrong screen §E3.3 forbids. Three surfaces now ask the same
 * question, so they ask it here rather than reading the variable three times —
 * and a rename becomes one edit instead of a grep.
 */
export function publishedTenant(): string | undefined {
  const slug = process.env["NEMESIS_STORY_TENANT"];
  return slug === undefined || slug === "" ? undefined : slug;
}
