import { NextResponse } from "next/server";

import type { components } from "@/generated/api";
import { controlPlaneHeaders, upstream } from "@/server/upstream";

/**
 * Define a defect category, through the seam — Phase 5, ADR-0040.
 *
 * The body is narrowed to `TaxonomyNodeSpec` rather than forwarded, so a
 * browser cannot post a field the console never offered — `severity_semantics`
 * in particular decides whether a category bypasses scoring, which is not
 * something a form field should be able to set by accident.
 *
 * The upstream refusal is forwarded for the reason the activation route gives:
 * every caller here is behind the control-plane token, and *"a taxonomy key
 * must be unique within the tenant"* is the whole value of the response.
 */
type NodeSpec = components["schemas"]["TaxonomyNodeSpec"];

export async function POST(request: Request): Promise<Response> {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return problem(400, "That request could not be read.");
  }

  const spec = readSpec(body);
  if (spec === null) return problem(422, "A category needs a key and a display name.");

  const { data, error, response } = await upstream.POST("/api/v1/control-plane/taxonomy", {
    headers: controlPlaneHeaders(),
    body: spec,
  });

  if (error !== undefined || data === undefined) {
    return problem(
      response.status === 200 ? 502 : response.status,
      detailOf(error) ?? "That category was not defined.",
    );
  }

  return NextResponse.json(data, { status: 201, headers: { "Cache-Control": "no-store" } });
}

function readSpec(value: unknown): NodeSpec | null {
  if (typeof value !== "object" || value === null) return null;
  const candidate = value as Record<string, unknown>;
  const key = candidate["key"];
  const displayName = candidate["display_name"];
  if (typeof key !== "string" || key.trim() === "") return null;
  if (typeof displayName !== "string" || displayName.trim() === "") return null;

  const parent = candidate["parent_key"];
  return {
    key,
    display_name: displayName,
    ...(typeof parent === "string" && parent !== "" ? { parent_key: parent } : {}),
  };
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
