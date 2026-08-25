import "server-only";

import {
  readBudget,
  readContractor,
  readZone,
  type PublishedBudget,
  type PublishedContractor,
  type PublishedZone,
} from "@/public/figures";
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
): Promise<
  PublicRead<{
    readonly zones: readonly PublishedZone[];
    readonly notice: string;
    readonly generatedAt: string;
  }>
> {
  const { data, error } = await upstream.GET("/api/v1/public/{tenant_slug}/zones", {
    params: { path: { tenant_slug: slug }, query: { limit: 200 } },
    ...cache,
  });
  if (error !== undefined) return MISSING;

  return {
    ok: true,
    value: {
      zones: data.zones.map(readZone),
      notice: data.notice,
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
): Promise<PublicRead<PublishedZone>> {
  const { data, error } = await upstream.GET(
    "/api/v1/public/{tenant_slug}/ward/{zone_code}/summary",
    { params: { path: { tenant_slug: slug, zone_code: zoneCode } }, ...cache },
  );
  if (error !== undefined) return MISSING;
  return { ok: true, value: readZone(data) };
}

/** §16.1's track record. Never a rating — the API publishes no collapsible number. */
export async function fetchContractor(
  slug: string,
  contractorId: string,
): Promise<PublicRead<PublishedContractor>> {
  const { data, error } = await upstream.GET(
    "/api/v1/public/{tenant_slug}/contractor/{contractor_id}/profile",
    { params: { path: { tenant_slug: slug, contractor_id: contractorId } }, ...cache },
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
): Promise<PublicRead<PublishedBudget>> {
  const { data, error } = await upstream.GET("/api/v1/public/{tenant_slug}/budget/{zone_code}", {
    params: {
      path: { tenant_slug: slug, zone_code: zoneCode },
      query: { fiscal_year: fiscalYear },
    },
    ...cache,
  });
  if (error !== undefined) return MISSING;
  return { ok: true, value: readBudget(data) };
}

/**
 * The city's display name.
 *
 * **It is not published, and this is the honest workaround.** The public
 * contract returns `tenant`, which is the slug, and nothing carries
 * `tenants.name`. So the heading says the slug, title-cased, until the contract
 * publishes the name — recorded as a defect rather than hidden behind a lookup
 * table of cities we happen to know about, which would be a second source of
 * truth for a fact the platform already holds.
 */
export function cityName(slug: string): string {
  return slug
    .split("-")
    .map((part) => (part === "" ? part : `${part[0]?.toUpperCase() ?? ""}${part.slice(1)}`))
    .join(" ");
}
