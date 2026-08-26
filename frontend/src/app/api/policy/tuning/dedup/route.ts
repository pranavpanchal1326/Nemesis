import { NextResponse } from "next/server";

import { controlPlaneHeaders, upstream } from "@/server/upstream";

/**
 * The dedup tuning proposals — §E19.8, §13.3, ADR-0040.
 *
 * A `POST` that computes and writes nothing, per its upstream docstring, and
 * this handler keeps it that way: it takes no body and forwards none, so the
 * only thing a caller can ask for is the default window. Accepting a window
 * from the browser would be a reasonable feature and it is not this one —
 * `/tuning/dedup/draft`, the sibling that actually writes, is deliberately not
 * proxied at all, because putting a document in front of an approver is an act
 * that should go through the policy lifecycle screen rather than a button
 * beside a table of numbers.
 */
export async function POST(): Promise<Response> {
  const { data, error, response } = await upstream.POST(
    "/api/v1/control-plane/simulations/tuning/dedup",
    { headers: controlPlaneHeaders(), body: {} },
  );

  if (error !== undefined) {
    return NextResponse.json(
      { status: response.status, title: "Those proposals could not be computed." },
      { status: response.status === 200 ? 502 : response.status },
    );
  }

  return NextResponse.json(data, { headers: { "Cache-Control": "no-store" } });
}
