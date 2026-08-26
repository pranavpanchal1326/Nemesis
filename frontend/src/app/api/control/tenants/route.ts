import { NextResponse } from "next/server";

import type { components } from "@/generated/api";
import { controlPlaneHeaders, upstream } from "@/server/upstream";

/**
 * Provision a tenant, through the seam — Phase 5, ADR-0040.
 *
 * The narrowing here is deliberately conservative: slug, name, and an optional
 * template. `ProvisioningRequest` can carry an entire city, and a route handler
 * that forwarded whatever arrived would let a browser post a taxonomy, a set of
 * zones and a translation bundle in one request — which is a capability the
 * platform has and which belongs behind a deployment tool, not behind a form on
 * a screen an operator can reach with a token.
 */
type ProvisioningRequest = components["schemas"]["ProvisioningRequest"];

export async function POST(request: Request): Promise<Response> {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return problem(400, "That request could not be read.");
  }

  const spec = readRequest(body);
  if (spec === null) return problem(422, "A tenant needs a slug and a name.");

  const result = await upstream.POST("/api/v1/control-plane/tenants", {
    headers: controlPlaneHeaders(),
    body: spec,
  });

  // `response.ok`, not `error`. The generated operation declares only its
  // success response and a validation error, so `error` is typed `undefined`
  // here — but a 403 from the token check is a real runtime outcome the
  // contract does not describe. Reading the status is the check that holds for
  // both.
  if (!result.response.ok) {
    return problem(
      result.response.status === 200 ? 502 : result.response.status,
      detailOf(problemBody(result)) ?? "That tenant was not provisioned.",
    );
  }

  return NextResponse.json(result.data, { status: 201, headers: { "Cache-Control": "no-store" } });
}

function readRequest(value: unknown): ProvisioningRequest | null {
  if (typeof value !== "object" || value === null) return null;
  const candidate = value as Record<string, unknown>;
  const tenant = candidate["tenant"];
  if (typeof tenant !== "object" || tenant === null) return null;

  const { slug, name } = tenant as Record<string, unknown>;
  if (typeof slug !== "string" || slug.trim() === "") return null;
  if (typeof name !== "string" || name.trim() === "") return null;

  const template = candidate["template"];
  return {
    tenant: { slug, name },
    ...(typeof template === "string" && template !== "" ? { template } : {}),
  };
}

/**
 * The refusal body, out of a result whose type does not admit one.
 *
 * `openapi-fetch` puts a non-2xx body on `error` at run time; the generated
 * operation for this path declares no such response, so the field is typed
 * `undefined` and the compiler is right about the contract while being wrong
 * about the deployment. Reading it through an explicit `unknown` says that out
 * loud rather than widening the generated type or casting the value.
 */
function problemBody(result: { readonly error?: unknown }): unknown {
  return result.error;
}

function detailOf(error: unknown): string | null {
  if (typeof error !== "object" || error === null) return null;
  const document = error as Record<string, unknown>;
  for (const field of ["detail", "title"] as const) {
    const value = document[field];
    if (typeof value === "string" && value !== "") return value;
  }
  return null;
}

function problem(status: number, title: string): Response {
  return NextResponse.json({ status, title }, { status, headers: { "Cache-Control": "no-store" } });
}
