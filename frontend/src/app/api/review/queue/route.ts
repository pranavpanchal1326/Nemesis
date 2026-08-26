import { NextResponse } from "next/server";

import type { components } from "@/generated/api";
import { upstream } from "@/server/upstream";

/**
 * The review queue, through the seam — §E14.1, §11.4, ADR-0040.
 *
 * A route handler rather than a direct call, for the reason every other one
 * exists: the browser never names its own tenant. `src/server/upstream.ts`
 * holds the header and `import "server-only"` makes pulling it into a client
 * bundle a build error.
 *
 * **Why the client reads this at all, when the page is server-rendered.** The
 * first page comes from the server so the queue is present at Tier D and in the
 * Lighthouse run. After that the socket says an item changed and the client
 * refetches — §E14.3's rule that an event is a hint and the read path is the
 * fact. Both paths return the same generated shape, so the screen has one
 * renderer.
 */

type ReviewPage = components["schemas"]["ReviewPage"];

export async function GET(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const status = url.searchParams.get("status");
  const limit = Number(url.searchParams.get("limit") ?? "50");

  const { data, error, response } = await upstream.GET("/api/v1/review/queue", {
    params: {
      query: {
        // Forwarded rather than defaulted here: `status=decided` is how the
        // screen shows what a reviewer did this morning, and re-deciding the
        // default in two places is how the two stop agreeing.
        ...(status === null ? {} : { status }),
        limit: Number.isFinite(limit) ? Math.min(Math.max(limit, 1), 200) : 50,
      },
    },
  });

  if (error !== undefined) {
    // The status is kept and the upstream problem document is not forwarded —
    // §25 treats an error body as a disclosure surface, and this one is read by
    // a browser on a municipal network.
    return problem(response.status === 200 ? 502 : response.status, "The queue could not be read.");
  }

  return NextResponse.json(data satisfies ReviewPage, {
    // Never cached. A queue is a work list; a stale one sends two reviewers to
    // the same item, and §11.4 allows exactly one judgement per item.
    headers: { "Cache-Control": "no-store" },
  });
}

function problem(status: number, title: string): Response {
  return NextResponse.json({ status, title }, { status, headers: { "Cache-Control": "no-store" } });
}
