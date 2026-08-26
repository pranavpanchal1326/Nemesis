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
 * The control-plane token, for the writes that redefine what a complaint means.
 *
 * `control_plane.py` is explicit that these are not open: *"Control-plane
 * writes redefine what a complaint means and are not open."* A review decision,
 * a policy activation and a tenant provisioning all carry
 * `X-Control-Plane-Token`, and until Phase 13 replaces it with a real session
 * that token is a shared secret.
 *
 * **It is held here and it is never sent to the browser.** Not in a prop, not
 * in a cookie, not in a `NEXT_PUBLIC_` variable — this module imports
 * `server-only`, so a client bundle that reaches for it is a build error rather
 * than a review catch. That is the whole reason the console's writes go through
 * route handlers instead of `fetch` from a component.
 *
 * **Returned per call rather than added by middleware.** A middleware would
 * attach the secret to every upstream request this application makes, including
 * the public transparency reads, which is a wider blast radius than the feature
 * needs. A route that performs a control-plane write says so by asking.
 */
export function controlPlaneHeaders(): Readonly<Record<string, string>> {
  const token = process.env["NEMESIS_CONTROL_PLANE_TOKEN"];
  // Absent rather than empty. An empty string would be *supplied and wrong*,
  // which the backend answers with a 403 that reads like a misconfigured token
  // instead of an unconfigured one.
  return token === undefined || token === "" ? {} : { "X-Control-Plane-Token": token };
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
 * The status a **transport** failure is reported as — the API is not answering
 * at all, as opposed to answering with a refusal.
 *
 * 503 rather than 502: the upstream did not produce a bad response, it produced
 * none, and the distinction is the one an operator reads first in a log.
 */
const UPSTREAM_UNREACHABLE = 503;

/**
 * A backend that is **down** is an answer, not an exception — and this is where
 * that is turned from one into the other.
 *
 * Every route handler in `app/api/` already reads `{ data, error, response }`
 * and renders a designed message when `error` is set. None of them survived the
 * upstream being *unreachable*, because `fetch` does not resolve with a status
 * in that case — it rejects, the rejection escapes the handler, and Next
 * answers `500` with a framework error page. So a stack with the API stopped
 * showed a console screen with a browser-level failure in it rather than the
 * sentence this product wrote for exactly that moment.
 *
 * **This is the same fault F12 found in `loadStrings`**, which caught the
 * control plane's error *response* and not the thrown connection: a deployment
 * whose control plane was down rendered a 500 for every non-source locale and
 * rendered perfectly in English. That one was fixed by removing the call
 * (ADR-0058). This one cannot be — the console's whole job is to read the
 * upstream — so it is fixed at the seam instead, once, rather than in twelve
 * handlers that would each have to remember.
 *
 * **Only transport failures are converted.** A `TypeError` from `fetch` means
 * the request never completed; anything else thrown here is this application's
 * own bug, and returning it unchanged keeps it loud instead of disguising it as
 * a well-formed 503 the surfaces would render as *"the service is not
 * answering"*. A bug that reads as an outage is a bug nobody looks for.
 */
const unreachableUpstream: Middleware = {
  onError({ error }) {
    if (!(error instanceof TypeError)) return;

    // Shaped as the backend's own problem+json (`nemesis/api/errors.py`), so
    // callers narrow it exactly as they narrow a real refusal and no handler
    // needs a second code path for the case where there was no server.
    return Response.json(
      {
        type: "about:blank",
        title: "The service is not answering.",
        status: UPSTREAM_UNREACHABLE,
      },
      { status: UPSTREAM_UNREACHABLE, headers: { "Content-Type": "application/problem+json" } },
    );
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
upstream.use(unreachableUpstream);

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

  // The same conversion `unreachableUpstream` performs for the typed client,
  // spelled out here because this path does not go through its middleware. A
  // caller of this function reads a `Response`; an unreachable API has to be
  // one, or the submission handler answers a citizen's photograph with a
  // framework stack trace.
  try {
    return await fetch(new URL(path, baseUrl()), { ...init, headers });
  } catch (error) {
    if (!(error instanceof TypeError)) throw error;
    return Response.json(
      {
        type: "about:blank",
        title: "The service is not answering.",
        status: UPSTREAM_UNREACHABLE,
      },
      { status: UPSTREAM_UNREACHABLE, headers: { "Content-Type": "application/problem+json" } },
    );
  }
}
