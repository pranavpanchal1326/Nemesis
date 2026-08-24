import { NextResponse } from "next/server";

import { upstream } from "@/server/upstream";

/**
 * The read path, through the seam — §E14.1, §27.3.
 *
 * This is the endpoint §E14.3's reconciliation rule calls, and it is also
 * §27.3's 5-second polling fallback for when the socket is unavailable. Both
 * uses are the same request, which is why there is one handler rather than two.
 *
 * The browser never talks to FastAPI directly: `X-Tenant-ID` is held on the
 * server (ADR-0040), and a client that named its own tenant would ship a trust
 * boundary that is not one.
 *
 * **`If-None-Match` is forwarded on purpose.** The upstream answers 304 from
 * one indexed query, and the whole reason `version` is on the read schema is so
 * a polling client can tell whether anything moved without diffing a body. A
 * BFF that swallowed the header would turn a 304 into a full read every five
 * seconds, per open tab.
 */
export async function GET(
  request: Request,
  context: { params: Promise<{ complaintId: string }> },
): Promise<Response> {
  const { complaintId } = await context.params;
  const ifNoneMatch = request.headers.get("if-none-match");

  const { data, error, response } = await upstream.GET("/api/v1/complaints/{complaint_id}", {
    params: {
      path: { complaint_id: complaintId },
      header: ifNoneMatch === null ? {} : { "If-None-Match": ifNoneMatch },
    },
  });

  if (response.status === 304) {
    return new Response(null, {
      status: 304,
      headers: etagHeaders(response),
    });
  }

  if (error !== undefined || data === undefined) {
    // Problem+JSON from upstream is not forwarded verbatim: §25 treats error
    // bodies as a disclosure surface, and an internal detail on a citizen's
    // phone helps nobody.
    return NextResponse.json(
      { status: response.status, title: "That report could not be read." },
      { status: response.status === 404 ? 404 : 502 },
    );
  }

  return NextResponse.json(data, { headers: etagHeaders(response) });
}

function etagHeaders(response: Response): HeadersInit {
  const etag = response.headers.get("etag");
  return etag === null ? {} : { ETag: etag };
}
