import { NextResponse } from "next/server";

import type { components } from "@/generated/api";
import { upstreamFetch } from "@/server/upstream";

/**
 * The mutation path, through the seam — §E14.1, §26.1, ADR-0040, ADR-0044.
 *
 * **Why a route handler and not a server action.** §E14.1 says every
 * browser-to-API read *and mutation* goes through a route handler; the
 * execution plan's M3 list said "server actions", and the blueprint governs on
 * direction. It is also the right answer on its own merits, and the reason is
 * M11.
 *
 * §E21's offline queue has to survive an app restart: a submission captured in
 * a basement is written to IndexedDB and replayed hours later, possibly after
 * the app has been redeployed. A server action's payload is an opaque RPC
 * encoding bound to a build id — a queued action serialised against one build
 * cannot be replayed against the next, and the failure is silent. A route
 * handler's request is a URL, a method, a set of headers and a body: durable,
 * inspectable, and replayable by anything that can `fetch`. The offline queue
 * is the feature that decides this, so it decides it now rather than after the
 * queue is written against the wrong shape.
 *
 * **The idempotency key is the client's, and it is passed through untouched.**
 * Minting it here would defeat it: the whole point is that a retry after a
 * timeout carries the *same* key as the attempt that timed out, and a server
 * that mints one per request cannot tell the two apart. `nemesis/ingest/
 * service.py` namespaces it (`submit:`) so a citizen cannot craft a key that
 * collides with the pipeline's own, and answers a match with the original
 * complaint and an `Idempotent-Replay` header.
 */

/** The generated multipart body. Field *names* are checked against it below;
 *  the file parts are `string` in OpenAPI and `File` on the wire, which is why
 *  `upstreamFetch` exists. */
type SubmitBody = components["schemas"]["Body_submit_complaint_api_v1_complaints_post"];
type SubmissionResponse = components["schemas"]["ComplaintSubmissionResponse"];

/**
 * §26.1's required fields, named against the generated body.
 *
 * `satisfies readonly (keyof SubmitBody)[]` is doing real work: rename
 * `device_fingerprint` upstream and this file stops compiling on the next
 * `nem web-types`, instead of quietly forwarding a part the server ignores.
 */
const REQUIRED_TEXT_FIELDS = [
  "latitude",
  "longitude",
  "device_fingerprint",
] as const satisfies readonly (keyof SubmitBody)[];

/**
 * The optional ones. `description_text` is §26.1's *one* optional field and
 * §E17's flow completes with it empty — which is why it is here and not above.
 */
const OPTIONAL_TEXT_FIELDS = [
  "description_text",
  "locale",
] as const satisfies readonly (keyof SubmitBody)[];

const FILE_FIELDS = ["photo", "audio"] as const satisfies readonly (keyof SubmitBody)[];

const IDEMPOTENCY_HEADER = "Idempotency-Key";

export async function POST(request: Request): Promise<Response> {
  let incoming: FormData;
  try {
    incoming = await request.formData();
  } catch {
    return problem(400, "That submission could not be read.");
  }

  const outgoing = new FormData();

  for (const field of REQUIRED_TEXT_FIELDS) {
    const value = incoming.get(field);
    if (typeof value !== "string" || value === "") {
      return problem(422, "That submission is missing something it needs.");
    }
    outgoing.set(field, value);
  }

  for (const field of OPTIONAL_TEXT_FIELDS) {
    const value = incoming.get(field);
    // An empty string is not the same as absent, and §26.1 treats them
    // differently: absent means the citizen said nothing, empty would be a
    // description they typed and deleted. Both complete the flow; only one is
    // worth sending.
    if (typeof value === "string" && value !== "") outgoing.set(field, value);
  }

  let hasMedia = false;
  for (const field of FILE_FIELDS) {
    const value = incoming.get(field);
    if (value === null || typeof value === "string") continue;
    outgoing.set(field, value, value.name);
    hasMedia = true;
  }

  if (!hasMedia) {
    // Refused here as well as upstream, and deliberately not *only* here. The
    // server owns the rule (§26.1: "at least one of photo or audio"); this
    // saves a citizen on a slow connection uploading nothing to be told no.
    // A client-side check that were the only control would be a control an
    // attacker skips.
    return problem(422, "A report needs a photo or a voice note.");
  }

  // Forwarded verbatim, including absence. A client that supplies no key gets
  // no idempotency, which is the correct behaviour for a fire-and-forget
  // submission and is never what the app itself does.
  const idempotencyKey = request.headers.get(IDEMPOTENCY_HEADER);
  const headers = new Headers();
  if (idempotencyKey !== null) headers.set(IDEMPOTENCY_HEADER, idempotencyKey);

  const response = await upstreamFetch("/api/v1/complaints", {
    method: "POST",
    body: outgoing,
    headers,
  });

  if (response.status !== 202) {
    // Problem+JSON from upstream is not forwarded verbatim — §25 treats an
    // error body as a disclosure surface. The status is kept because the
    // client's behaviour genuinely differs: 429 is worth retrying, 415 is not.
    return problem(response.status, refusalFor(response.status));
  }

  const body: unknown = await response.json();
  if (!isSubmissionResponse(body)) {
    return problem(502, "That report was accepted but the receipt was unreadable.");
  }

  return NextResponse.json(body, {
    status: 202,
    headers: {
      "Cache-Control": "no-store",
      // Passed through because the client renders a different sentence for it:
      // "your retry landed on the report you already filed" is not the same
      // message as "your report was filed".
      ...(response.headers.get("Idempotent-Replay") === null
        ? {}
        : { "Idempotent-Replay": "true" }),
    },
  });
}

/**
 * Narrow the upstream body to the generated type.
 *
 * A `as` cast here would make the whole seam decorative: the route would type-
 * check against a contract it never verified, which is the silent-lying failure
 * §E24 bans hand-written contracts to prevent. The fields checked are exactly
 * the ones `<Receipt>` renders, and `chain_hash` is among them — a receipt with
 * no hash is §E17.3's document with its claim removed (ADR-0044).
 */
function isSubmissionResponse(value: unknown): value is SubmissionResponse {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate["complaint_id"] === "string" &&
    typeof candidate["status"] === "string" &&
    typeof candidate["chain_hash"] === "string" &&
    typeof candidate["estimated_processing_time_seconds"] === "number"
  );
}

/** What a citizen is told, per status. Deliberately short of a reason where the
 *  reason would only be useful to an attacker. */
function refusalFor(status: number): string {
  if (status === 413) return "That photo is too large to send.";
  if (status === 415) return "That file is not a photo or a voice note.";
  if (status === 429) return "Too many reports from this device just now.";
  if (status === 422) return "That report is missing something it needs.";
  return "That report could not be sent.";
}

function problem(status: number, title: string): Response {
  return NextResponse.json({ status, title }, { status, headers: { "Cache-Control": "no-store" } });
}
