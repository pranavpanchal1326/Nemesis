import "server-only";

import createClient, { type Middleware } from "openapi-fetch";

import type { paths } from "@/generated/api";

/**
 * The BFF seam — §E14.1, ADR-0040. Corrects §E2 defect #11.
 *
 * `backend/nemesis/api/deps.py` says it plainly: *"`X-Tenant-ID` names a
 * tenant; it does not prove anything about who is asking."* A browser client
 * that names its own tenant would ship a trust boundary that is not one, and
 * would have to be rewritten the moment Phase 13 lands.
 *
 * So **every browser-to-API request goes through Next route handlers**, and
 * this module is the only place in the application that knows the upstream
 * exists. `import "server-only"` is not a convention here — it is a build
 * error if this module is ever pulled into a client bundle, which is the one
 * mistake that would quietly undo the whole seam.
 *
 * **What Phase 13 changes:** `resolveTenant()`. Nothing else. The header
 * becomes a bearer token, the token carries the tenant claim, and every caller
 * downstream is unaffected because they all already ask this module rather than
 * building a request themselves.
 *
 * **The exception, and it is deliberate.** The WebSocket connects directly.
 * `/ws/pipeline-events` is an unauthenticated one-directional stream by
 * construction, so proxying it would add a hop with no security benefit
 * (§E14.1, ADR-0040).
 */

/** Where the FastAPI app is. Server-side only; never reaches the browser. */
function baseUrl(): string {
  return process.env["NEMESIS_API_URL"] ?? "http://127.0.0.1:8000";
}

/**
 * The tenant for this request.
 *
 * Today: one configured tenant, held on the server. After Phase 13: read from
 * verified session claims. The signature does not change, which is the point of
 * putting it behind a function on day one rather than inlining an env var at
 * eleven call sites.
 */
export function resolveTenant(): string | undefined {
  return process.env["NEMESIS_TENANT_ID"];
}

/**
 * Problem+JSON is the backend's error contract (`nemesis/api/errors.py`), and
 * it is deliberately not forwarded verbatim to the browser: an upstream problem
 * document can carry internal detail, and §25 treats error responses as a
 * disclosure surface. The BFF keeps the status and the type, and drops the rest
 * unless it is one of the fields a surface actually renders.
 */
export interface UpstreamProblem {
  readonly status: number;
  readonly type: string;
  readonly title: string;
  /** Present only when the upstream marked it safe to show a citizen. */
  readonly detail?: string;
}

const tenantHeader: Middleware = {
  onRequest({ request }) {
    const tenant = resolveTenant();
    if (tenant !== undefined) request.headers.set("X-Tenant-ID", tenant);
    return request;
  },
};

/**
 * The typed upstream client, generated from `openapi.json`.
 *
 * Every path, parameter and response body is inferred from the committed
 * schema, so a screen that asks for a field the server does not publish fails
 * to compile — which is execution-plan Law 2 enforced by the type system rather
 * than by review.
 */
export const upstream = createClient<paths>({ baseUrl: baseUrl() });
upstream.use(tenantHeader);

/** Where the browser opens its socket. Public by construction (ADR-0040). */
export function realtimeUrl(): string {
  const configured = process.env["NEMESIS_REALTIME_URL"];
  if (configured !== undefined) return configured;
  return baseUrl().replace(/^http/, "ws") + "/ws/pipeline-events";
}

/**
 * A raw request to the upstream, on a path the generated document knows.
 *
 * **Why this exists beside a fully typed client.** §26.1's submission is
 * `multipart/form-data` carrying two file parts, and OpenAPI describes a binary
 * part as `string`. So the generated body type for that operation says
 * `photo?: string | null`, and a `File` — which is what a camera produces and
 * what the browser sends — cannot be assigned to it. That is a limitation of
 * the document rather than of this code, and the honest response is to say so
 * here rather than to widen a type until it compiles.
 *
 * What is still enforced: the `path` parameter is `keyof paths`, so a route the
 * backend does not serve fails to compile, and a route the backend *renames*
 * fails to compile on the next `nem web-types`. Callers narrow the response
 * against the generated response type themselves. Execution-plan Law 2 holds
 * where the document can express the shape, and the one place it cannot is
 * marked.
 */
export async function upstreamFetch(
  path: Extract<keyof paths, string>,
  init: RequestInit & { readonly headers?: Headers },
): Promise<Response> {
  const headers = new Headers(init.headers);
  const tenant = resolveTenant();
  if (tenant !== undefined) headers.set("X-Tenant-ID", tenant);
  return fetch(new URL(path, baseUrl()), { ...init, headers });
}
