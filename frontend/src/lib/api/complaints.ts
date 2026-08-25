import type { components } from "@/generated/api";

/**
 * The browser's view of a complaint — reads and the one mutation.
 *
 * Everything here talks to the **BFF route handlers**, never to FastAPI. The
 * tenant is server-held (ADR-0040) and this module could not name it if it
 * wanted to; `src/server/upstream.ts` imports `server-only`, so pulling the
 * upstream client into a client bundle is a build error rather than a review
 * catch.
 *
 * Every type is an alias of a generated one. Nothing in this file describes a
 * backend contract — execution-plan Law 2 — and if a field a surface needs is
 * missing, the fix is in the backend schema, not here.
 */

export type Complaint = components["schemas"]["ComplaintResponse"];
export type ComplaintHistory = components["schemas"]["ComplaintHistory"];
export type ComplaintHistoryEvent = components["schemas"]["ComplaintHistoryEvent"];
export type ComplaintReceipt = components["schemas"]["ComplaintSubmissionResponse"];

/** A read or a write that the server refused, in the shape a surface renders. */
export class ApiError extends Error {
  // Declared and assigned rather than a parameter property: `erasableSyntaxOnly`
  // is on (ADR-0042), and a parameter property is TypeScript that emits
  // JavaScript rather than TypeScript that erases to it.
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }

  /**
   * Whether trying again could plausibly work.
   *
   * Used by the submit path to decide between "we will keep trying" and "this
   * needs you to do something different". 415 will never succeed on a retry and
   * telling a citizen to wait would be a lie; 429 and 5xx will.
   */
  get retriable(): boolean {
    return this.status === 429 || this.status >= 500;
  }
}

async function readJson<T>(response: Response, fallbackTitle: string): Promise<T> {
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    const title =
      typeof body === "object" &&
      body !== null &&
      typeof (body as { title?: unknown }).title === "string"
        ? (body as { title: string }).title
        : fallbackTitle;
    throw new ApiError(response.status, title);
  }
  return (await response.json()) as T;
}

/**
 * One complaint's current state.
 *
 * This is the endpoint §E14.3's reconciliation rule calls **and** §27.3's
 * five-second polling fallback. One function, because they are one request —
 * the difference is how often it is made, not what it asks for.
 */
export async function fetchComplaint(
  complaintId: string,
  signal?: AbortSignal,
): Promise<Complaint> {
  const response = await fetch(`/api/complaints/${encodeURIComponent(complaintId)}`, {
    signal: signal ?? null,
  });
  return readJson<Complaint>(response, "That report could not be read.");
}

/**
 * The ledger — every event on the complaint's chain, with its hash links and
 * the live chain head (ADR-0043, ADR-0044).
 *
 * Never cached by the browser, because `chain_head` is what a receipt is
 * checked against.
 */
export async function fetchComplaintHistory(
  complaintId: string,
  signal?: AbortSignal,
): Promise<ComplaintHistory> {
  const response = await fetch(`/api/complaints/${encodeURIComponent(complaintId)}/events`, {
    cache: "no-store",
    signal: signal ?? null,
  });
  return readJson<ComplaintHistory>(response, "That report's history could not be read.");
}

/**
 * A submission, before it has been sent.
 *
 * `idempotencyKey` is on the draft rather than supplied at send time, and that
 * placement is the point: see `idempotency.ts`. It is also what makes a draft
 * serialisable — M11 writes exactly this object to IndexedDB.
 */
export interface ComplaintDraft {
  readonly idempotencyKey: string;
  readonly latitude: number;
  readonly longitude: number;
  readonly deviceFingerprint: string;
  readonly photo?: Blob | undefined;
  readonly audio?: Blob | undefined;
  readonly descriptionText?: string | undefined;
  readonly locale?: string | undefined;
}

export interface SubmissionOutcome {
  readonly receipt: ComplaintReceipt;
  /**
   * True when the server recognised the idempotency key and returned the
   * original report. The citizen is told *"your earlier report was already
   * filed"*, not *"your report was filed"* — §6 Principle #8 applied to the one
   * case where the difference is invisible without saying so.
   */
  readonly replayed: boolean;
}

/**
 * Send a draft. §26.1, through the BFF.
 *
 * Multipart rather than JSON with a base64 field, because the photograph is
 * the evidence and base64 costs a third of its size in bytes and all of it in
 * memory — on the device §E23 budgets a 2.0 s LCP for.
 */
export async function submitComplaint(
  draft: ComplaintDraft,
  signal?: AbortSignal,
): Promise<SubmissionOutcome> {
  const form = new FormData();
  form.set("latitude", String(draft.latitude));
  form.set("longitude", String(draft.longitude));
  form.set("device_fingerprint", draft.deviceFingerprint);
  if (draft.descriptionText !== undefined && draft.descriptionText !== "") {
    form.set("description_text", draft.descriptionText);
  }
  if (draft.locale !== undefined) form.set("locale", draft.locale);
  if (draft.photo !== undefined) form.set("photo", draft.photo, "capture.jpg");
  if (draft.audio !== undefined) form.set("audio", draft.audio, "capture.webm");

  const response = await fetch("/api/complaints", {
    method: "POST",
    body: form,
    headers: { "Idempotency-Key": draft.idempotencyKey },
    signal: signal ?? null,
  });

  const receipt = await readJson<ComplaintReceipt>(response, "That report could not be sent.");
  return { receipt, replayed: response.headers.get("Idempotent-Replay") === "true" };
}
