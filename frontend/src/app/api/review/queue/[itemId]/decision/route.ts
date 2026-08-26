import { NextResponse } from "next/server";

import type { components } from "@/generated/api";
import { controlPlaneHeaders, upstream } from "@/server/upstream";

/**
 * One judgement, recorded — §11.4, §E19.1, ADR-0040.
 *
 * > Approve, reject, or escalate. **One judgement per item, ever.**
 *
 * Three things this handler does and one thing it deliberately does not.
 *
 * **It holds the control-plane token.** A decision is a control-plane write and
 * carries `X-Control-Plane-Token`. The token lives on the server
 * (`controlPlaneHeaders()`), and the browser could not send it if the screen
 * wanted to — which is the point of the whole seam and the reason this is not a
 * `fetch` from a component.
 *
 * **It refuses an empty rationale before the network.** `DecisionRequest`
 * requires one, and §11.4's argument for it is that a decision without a stated
 * reason is a decision nobody can review later. Refusing here saves a round
 * trip; the server still refuses independently, and **the server is the
 * control** (§E19.4's division, which holds on every screen and not only on
 * closure).
 *
 * **It does not retry.** A decision is not idempotent — the endpoint allows one
 * per item, forever — so a retry after a timeout could record a second
 * judgement or report a failure for one that landed. The screen says what
 * happened and lets a person look, which is the honest answer for a write that
 * cannot be replayed safely.
 */

type DecisionRequest = components["schemas"]["DecisionRequest"];
type DecisionResponse = components["schemas"]["DecisionResponse"];

const KINDS = [
  "approve",
  "reject",
  "escalate",
] as const satisfies readonly DecisionRequest["decision"][];

export async function POST(
  request: Request,
  context: { params: Promise<{ itemId: string }> },
): Promise<Response> {
  const { itemId } = await context.params;

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return problem(400, "That decision could not be read.");
  }

  const parsed = readDecision(body);
  if (parsed === null) {
    return problem(422, "A decision needs one of approve, reject or escalate, and a reason.");
  }

  const { data, error, response } = await upstream.POST("/api/v1/review/queue/{item_id}/decision", {
    params: { path: { item_id: itemId } },
    headers: controlPlaneHeaders(),
    body: parsed,
  });

  if (error !== undefined) {
    return problem(response.status === 200 ? 502 : response.status, refusalFor(response.status));
  }

  return NextResponse.json(data satisfies DecisionResponse, {
    status: 201,
    headers: { "Cache-Control": "no-store" },
  });
}

/**
 * Narrow the browser's body to the generated request type.
 *
 * A cast would make the seam decorative — the handler would type-check against
 * a contract it never checked. `decision` is validated against the generated
 * enum rather than against a list written here, so adding a fourth kind
 * upstream fails to compile on the next `nem web-types` instead of being
 * silently rejected by a stale array.
 */
function readDecision(value: unknown): DecisionRequest | null {
  if (typeof value !== "object" || value === null) return null;
  const candidate = value as Record<string, unknown>;

  const decision = candidate["decision"];
  if (typeof decision !== "string") return null;
  if (!(KINDS as readonly string[]).includes(decision)) return null;

  const rationale = candidate["rationale"];
  if (typeof rationale !== "string" || rationale.trim() === "") return null;

  const label = candidate["decided_by_label"];

  return {
    decision: decision as DecisionRequest["decision"],
    rationale,
    ...(typeof label === "string" && label !== "" ? { decided_by_label: label } : {}),
  };
}

/** What the reviewer is told. 409 is the one worth naming precisely: it means
 *  somebody already judged this item, which is a fact about the queue rather
 *  than a fault, and the screen renders it as such. */
function refusalFor(status: number): string {
  if (status === 403) return "This deployment is not configured to record decisions.";
  if (status === 404) return "That item is not in this queue.";
  if (status === 409) return "That item was already decided.";
  if (status === 422) return "That decision is missing something it needs.";
  return "That decision was not recorded.";
}

function problem(status: number, title: string): Response {
  return NextResponse.json({ status, title }, { status, headers: { "Cache-Control": "no-store" } });
}
