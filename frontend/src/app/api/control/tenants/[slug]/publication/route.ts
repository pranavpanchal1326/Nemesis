import { NextResponse } from "next/server";

import { controlPlaneHeaders, upstream } from "@/server/upstream";

/**
 * Publish, or stop publishing — ADR-0046, §16.2, ADR-0040.
 *
 * > publication is an act somebody takes, logged as an `admin_action` with a
 * > **required justification**, revocable through the same door.
 *
 * The justification is refused here as well as upstream, and the reason is not
 * a round trip saved: a request arriving without one is a client that has found
 * a way around the form, and answering it 422 with the rule in the sentence is
 * how that gets noticed rather than logged as an empty string.
 *
 * The server still enforces it. This is a convenience and it is never the
 * control — §E19.4's division, applied to the one write in this product that
 * changes what the public internet can see.
 */
export async function PUT(
  request: Request,
  context: { params: Promise<{ slug: string }> },
): Promise<Response> {
  const { slug } = await context.params;

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return problem(400, "That request could not be read.");
  }

  if (typeof body !== "object" || body === null) {
    return problem(422, "That request is not a publication decision.");
  }
  const candidate = body as Record<string, unknown>;
  const enabled = candidate["enabled"];
  const justification = candidate["justification"];

  if (typeof enabled !== "boolean") {
    return problem(422, "A publication decision must say on or off.");
  }
  if (typeof justification !== "string" || justification.trim() === "") {
    return problem(
      422,
      "Publishing is an act somebody takes, and it needs a stated justification.",
    );
  }

  const result = await upstream.PUT("/api/v1/control-plane/tenants/{slug}/publication", {
    params: { path: { slug } },
    headers: controlPlaneHeaders(),
    body: { enabled, justification },
  });

  // `response.ok`, not `error`. The generated operation declares only its
  // success response and a validation error, so `error` is typed `undefined`
  // here — but a 403 from the token check is a real runtime outcome the
  // contract does not describe. Reading the status is the check that holds for
  // both.
  if (!result.response.ok) {
    return problem(
      result.response.status === 200 ? 502 : result.response.status,
      detailOf(problemBody(result)) ?? "That publication change was refused.",
    );
  }

  return NextResponse.json(result.data, { headers: { "Cache-Control": "no-store" } });
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
