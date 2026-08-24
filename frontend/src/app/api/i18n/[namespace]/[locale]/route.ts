import { NextResponse } from "next/server";

import { upstream } from "@/server/upstream";

/**
 * Strings, from the control plane — §E10.1, §E22, Phase 18's gate.
 *
 * > **A locale added in the control plane appears in the UI with no code
 * > change.**  — §E25, Phase 18
 *
 * That gate is only meetable if no string in this application is a literal in a
 * component. Every one resolves through here, from the Phase 5 locale registry,
 * which is a real endpoint today
 * (`/api/v1/control-plane/translations/{namespace}/{locale}`).
 *
 * Cached for a minute rather than per-request: a locale bundle changes when
 * somebody edits the control plane, which is a human-scale event, and fetching
 * it on every navigation would make the seam the slowest thing on the page.
 */
export async function GET(
  _request: Request,
  context: { params: Promise<{ namespace: string; locale: string }> },
): Promise<Response> {
  const { namespace, locale } = await context.params;

  const { data, error } = await upstream.GET(
    "/api/v1/control-plane/translations/{namespace}/{locale}",
    { params: { path: { namespace, locale } } },
  );

  if (error !== undefined) {
    // An empty bundle, not a 500. A missing locale must degrade to the base
    // strings rather than taking the page down — §E13's ladder applied to text.
    return NextResponse.json({}, { status: 200, headers: { "Cache-Control": "no-store" } });
  }

  return NextResponse.json(data, {
    headers: { "Cache-Control": "public, max-age=60, stale-while-revalidate=600" },
  });
}
