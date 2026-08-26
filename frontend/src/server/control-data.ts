import "server-only";

import type { components } from "@/generated/api";
import { controlPlaneHeaders, upstream } from "@/server/upstream";

/**
 * The control plane's and the developer portal's reads — §E19, §E14.4,
 * ADR-0046.
 *
 * F6's gate is Phase 5's own gate, re-run through the surface a solutions
 * engineer would actually use:
 *
 * > a tenant is provisioned, given an invented taxonomy, and published —
 * > entirely through the UI, no SQL, no code change.
 *
 * These are the reads that gate needs to be *checkable* — after the three
 * writes, the same screens have to show the new tenant's taxonomy and its
 * publication state, or the gate is somebody watching a form submit.
 *
 * **Everything here is `no-store`.** An administrative surface that showed a
 * cached taxonomy after somebody added a category would make the gate above
 * unfalsifiable: the operator would not be able to tell a working write from a
 * stale read.
 *
 * **A failed read is `null`, not an exception.** These screens show eight
 * unrelated facts, and one unreachable endpoint should cost its own panel and
 * not the page — an admin surface whose taxonomy panel takes the webhook list
 * down with it is a surface people stop trusting during exactly the incident
 * they opened it for.
 */

export type Taxonomy = components["schemas"]["TaxonomyListResponse"];
/** The control plane's zone row — `OrgUnitResponse`, the same shape
 *  departments use, because a zone and a department are both org units in the
 *  hierarchy. Not `ZoneIndexResponse`, which is the *public* surface's zone. */
export type Zone = components["schemas"]["OrgUnitResponse"];
export type Department = components["schemas"]["DepartmentResponse"];
export type Calendar = components["schemas"]["CalendarResponse"];
export type Coverage = components["schemas"]["CoverageResponse"];
export type Versions = components["schemas"]["VersionsResponse"];
export type ApiKey = components["schemas"]["KeyResponse"];
export type Webhook = components["schemas"]["WebhookResponse"];
export type Usage = components["schemas"]["UsageResponse"];

const live = { headers: controlPlaneHeaders(), cache: "no-store" } as const;

export interface ControlPlaneData {
  readonly taxonomy: Taxonomy | null;
  readonly zones: readonly Zone[] | null;
  readonly departments: readonly Department[] | null;
  readonly calendars: readonly Calendar[] | null;
  readonly coverage: readonly Coverage[] | null;
}

export async function fetchControlPlane(): Promise<ControlPlaneData> {
  const [taxonomy, zones, departments, calendars, coverage] = await Promise.all([
    read(() => upstream.GET("/api/v1/control-plane/taxonomy", live)),
    read(() => upstream.GET("/api/v1/control-plane/zones", live)),
    read(() => upstream.GET("/api/v1/control-plane/departments", live)),
    read(() => upstream.GET("/api/v1/control-plane/calendars", live)),
    read(() => upstream.GET("/api/v1/control-plane/translations/coverage", live)),
  ]);

  return { taxonomy, zones, departments, calendars, coverage };
}

export interface DeveloperPortalData {
  readonly keys: readonly ApiKey[] | null;
  readonly webhooks: readonly Webhook[] | null;
  readonly usage: Usage | null;
  readonly versions: Versions | null;
}

export async function fetchDeveloperPortal(): Promise<DeveloperPortalData> {
  const [keys, webhooks, usage, versions] = await Promise.all([
    read(() => upstream.GET("/api/v1/integrations/keys", live)),
    read(() => upstream.GET("/api/v1/integrations/webhooks", live)),
    read(() =>
      upstream.GET("/api/v1/integrations/usage", { ...live, params: { query: { days: 7 } } }),
    ),
    // The version registry is public — it is the contract's own deprecation
    // clock and any integrator may read it — so it carries no token.
    read(() => upstream.GET("/api/v1/versions", { cache: "no-store" })),
  ]);

  return { keys, webhooks, usage, versions };
}

/**
 * One read, reduced to `T | null`.
 *
 * The `catch` matters as much as the `error` check: `upstream.GET` rejects on a
 * connection failure rather than resolving with an error body, and a control
 * plane that is *down* is exactly the case an admin screen has to survive.
 */
async function read<T>(call: () => Promise<{ data?: T; error?: unknown }>): Promise<T | null> {
  try {
    const { data, error } = await call();
    return error === undefined && data !== undefined ? data : null;
  } catch {
    return null;
  }
}
