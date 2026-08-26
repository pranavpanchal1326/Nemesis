import { NextResponse } from "next/server";

import { POLICY_KINDS, type PolicyKind } from "@/generated/enums";
import { controlPlaneHeaders, upstream } from "@/server/upstream";

/**
 * Activation, through the seam — §E19.8, ADR-0028, ADR-0040.
 *
 * **The refusal is forwarded, not replaced.** Everywhere else in this
 * application an upstream problem document is dropped and a short sentence is
 * substituted, because §25 treats an error body as a disclosure surface. This
 * is the deliberate exception, and the reason is what the refusal says:
 *
 * > `dedup_thresholds` revision 8 has no passing certificate against evaluation
 * > set `'monsoon-2026'`, which this tenant published to gate exactly this
 * > activation. Run an evaluation against revision 8 first — if it fails, the
 * > certificate names which labelled complaints the candidate would have
 * > decided differently.
 *
 * There is nothing in that sentence an operator should not see: it names their
 * own evaluation set, their own revision, and what to do next. Replacing it
 * with *"that activation was refused"* would delete the entire teaching value
 * of the guardrail — which §E19.4 says is the point of rendering a rule at all.
 * The audience here is also different from the citizen surface's: this endpoint
 * is behind the control-plane token, so the reader is somebody the deployment
 * has already trusted with policy.
 *
 * Only the `title` crosses, and only for the statuses the guardrail uses. A 500
 * still says nothing, because an unexpected failure's detail is the one that
 * carries stack shape and internal names.
 */

/** Statuses whose upstream `title` is a designed message for an operator.
 *  403 is the certification refusal; 409 is a lifecycle conflict ("this
 *  revision is not approved"); 404 names a revision that does not exist. */
const FORWARDABLE = new Set([403, 404, 409, 422]);

export async function POST(
  request: Request,
  context: { params: Promise<{ kind: string; revision: string }> },
): Promise<Response> {
  const { kind, revision } = await context.params;

  if (!isPolicyKind(kind)) return problem(404, "No such policy kind.");
  const number = Number(revision);
  if (!Number.isInteger(number) || number < 1) return problem(404, "No such revision.");

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return problem(400, "That request could not be read.");
  }
  const reason = readReason(body);
  if (reason === null) return problem(422, "An activation needs a stated reason.");

  const { data, error, response } = await upstream.POST(
    "/api/v1/control-plane/policies/{kind}/{revision}/activate",
    {
      params: { path: { kind, revision: number } },
      headers: controlPlaneHeaders(),
      body: { reason },
    },
  );

  if (error !== undefined || data === undefined) {
    const status = response.status === 200 ? 502 : response.status;
    const upstreamTitle = FORWARDABLE.has(status) ? titleOf(error) : null;
    return problem(status, upstreamTitle ?? "That activation was refused.");
  }

  return NextResponse.json(data, { headers: { "Cache-Control": "no-store" } });
}

function isPolicyKind(value: string): value is PolicyKind {
  return (POLICY_KINDS as readonly string[]).includes(value);
}

function readReason(value: unknown): string | null {
  if (typeof value !== "object" || value === null) return null;
  const reason = (value as Record<string, unknown>)["reason"];
  return typeof reason === "string" && reason.trim() !== "" ? reason : null;
}

/** RFC 9457 carries the operator-facing sentence in `detail` and the short
 *  label in `title`; `errors.py` puts the guardrail's explanation in `detail`,
 *  so prefer it and fall back to the label. */
function titleOf(error: unknown): string | null {
  if (typeof error !== "object" || error === null) return null;
  const problemDocument = error as Record<string, unknown>;
  const detail = problemDocument["detail"];
  if (typeof detail === "string" && detail !== "") return detail;
  const title = problemDocument["title"];
  return typeof title === "string" && title !== "" ? title : null;
}

function problem(status: number, title: string): Response {
  return NextResponse.json({ status, title }, { status, headers: { "Cache-Control": "no-store" } });
}
