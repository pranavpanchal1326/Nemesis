import { NextResponse } from "next/server";

import { upstream } from "@/server/upstream";

/**
 * §E17.4's ledger, through the seam — ADR-0043, ADR-0044.
 *
 * The upstream decides what a reader may see (`nemesis/events/disclosure.py`);
 * this handler adds the tenant and forwards. It deliberately does **not**
 * filter further. Two filters for one rule is two places to disagree, and the
 * one that would win is the one running on a machine a citizen does not
 * control — so the server-side table is the only table.
 *
 * **`no-store`, matching upstream.** The response carries `chain_head`, and
 * §E17.3's receipt is checked against it. A head served from a cache is a head
 * that may already have moved, and a stale hash on a document claiming *"this
 * record cannot be edited"* fails in the exact direction the hash exists to
 * prevent (ADR-0044).
 */
export async function GET(
  _request: Request,
  context: { params: Promise<{ complaintId: string }> },
): Promise<Response> {
  const { complaintId } = await context.params;

  const { data, error, response } = await upstream.GET("/api/v1/complaints/{complaint_id}/events", {
    params: { path: { complaint_id: complaintId } },
  });

  if (error !== undefined || data === undefined) {
    return NextResponse.json(
      { status: response.status, title: "That report's history could not be read." },
      { status: response.status === 404 ? 404 : 502 },
    );
  }

  return NextResponse.json(data, { headers: { "Cache-Control": "no-store" } });
}
