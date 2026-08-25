import { NextResponse } from "next/server";

import { upstream } from "@/server/upstream";

/**
 * §E17.1's *Place* card, through the seam.
 *
 * **The coordinate is in the query string, and that is a deliberate exception
 * to a rule this repository otherwise holds.** A person's exact position is
 * personal data, and putting personal data in a URL normally means putting it
 * in an access log, a proxy log, and a `Referer` header.
 *
 * Here it does not:
 *
 * - The request is same-origin, so there is no `Referer` to leak it to.
 * - The upstream neither stores nor logs it — `nemesis/api/v1/places.py` says
 *   so, and the handler is four lines of query with no logging call in it.
 * - The alternative, a `POST` with a JSON body, would make the read
 *   unconditionally uncacheable and non-idempotent for a question that is
 *   purely a function of its input, and would still put the coordinate in the
 *   same access log's request line if anybody ever enabled body logging.
 *
 * What the browser gets back is a ward, which is coarser than the ~110 m the
 * public stream already permits.
 */
export async function GET(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const latitude = Number(url.searchParams.get("latitude"));
  const longitude = Number(url.searchParams.get("longitude"));

  if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) {
    return NextResponse.json(
      { status: 422, title: "That location could not be read." },
      { status: 422 },
    );
  }

  const { data, error, response } = await upstream.GET("/api/v1/places/resolve", {
    params: { query: { latitude, longitude } },
  });

  if (error !== undefined) {
    // Not fatal to the flow, and the caller treats it that way: a report with
    // coordinates and no ward name is still a report. §E17.1's card degrades to
    // stating the coordinates, which is honest rather than broken.
    return NextResponse.json(
      { status: response.status, title: "That location could not be placed." },
      { status: response.status === 422 ? 422 : 502 },
    );
  }

  return NextResponse.json(data, {
    // A ward boundary changes when a city redraws it, which is approximately
    // never — but the *answer* is per coordinate, and a shared cache holding
    // "this coordinate is in Kothrud" is a shared cache holding somebody's
    // position. Private, and short.
    headers: { "Cache-Control": "private, max-age=60" },
  });
}
