import { NextResponse } from "next/server";

import { realtimeUrl, resolveTenant } from "@/server/upstream";

/**
 * Where to connect, and as whom — ADR-0040.
 *
 * The socket is the one thing the browser opens directly, because
 * `/ws/pipeline-events` is an unauthenticated one-directional stream by
 * construction and proxying it would add a hop with no security benefit.
 *
 * But the *tenant* is still server-held. Without this handler the client would
 * need `NEMESIS_TENANT_ID` in its bundle, which is precisely the "browser names
 * its own tenant" shape §E2 defect #11 is about. After Phase 13 this returns a
 * short-lived subprotocol token instead of an id, and the client does not
 * change.
 */
export function GET(): Response {
  const tenantId = resolveTenant();

  if (tenantId === undefined) {
    // Not an error. A deployment with no tenant configured has nothing to
    // stream, and the surfaces render their saved state — §E13, calmly.
    return NextResponse.json({ available: false }, { status: 200 });
  }

  return NextResponse.json(
    { available: true, url: realtimeUrl(), tenantId },
    { headers: { "Cache-Control": "no-store" } },
  );
}
