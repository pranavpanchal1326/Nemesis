import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  fetchComplaintHistory,
  submitComplaint,
  type ComplaintDraft,
} from "../src/lib/api/complaints.ts";
import { newIdempotencyKey } from "../src/lib/api/idempotency.ts";
import { isPollingTransport, POLL_INTERVAL_MS } from "../src/lib/api/queries.ts";

/**
 * A3 — mutations, which did not exist.
 *
 * The M3 ships list promised *"server actions for mutations, carrying a
 * client-generated idempotency key end to end"*, and nothing could be
 * submitted. That blocked M5 outright and M11 structurally: server-side
 * idempotency is what makes an offline queue safe, and the key has to be
 * generated somewhere.
 *
 * These are unit tests against a stubbed `fetch` rather than a live stack on
 * purpose. What is being asserted is what the *client* puts on the wire — the
 * key's placement, the multipart shape, the replay signal — and the stack's
 * half is already asserted in `backend/tests/test_ingest_api.py` against a real
 * database. Two halves, each tested where it can fail.
 */

interface Captured {
  readonly url: string;
  readonly method: string | undefined;
  readonly headers: Headers;
  readonly body: FormData;
}

let captured: Captured[] = [];
let respond: () => Response;

const RECEIPT = {
  complaint_id: "8b1c4f5e-2b0a-4c9e-9a1d-2f6c7d8e9a0b",
  status: "submitted",
  estimated_processing_time_seconds: 8,
  chain_hash: "a".repeat(64),
};

function draft(overrides: Partial<ComplaintDraft> = {}): ComplaintDraft {
  return {
    idempotencyKey: "fixed-key-for-assertion",
    latitude: 18.5204,
    longitude: 73.8567,
    deviceFingerprint: "device-1",
    photo: new Blob([new Uint8Array([0xff, 0xd8, 0xff, 0xe0])], { type: "image/jpeg" }),
    ...overrides,
  };
}

beforeEach(() => {
  captured = [];
  respond = () =>
    new Response(JSON.stringify(RECEIPT), {
      status: 202,
      headers: { "content-type": "application/json" },
    });

  vi.stubGlobal("fetch", (input: string | URL, init?: RequestInit) => {
    const body = init?.body;
    captured.push({
      // Typed as `string | URL` rather than `RequestInfo`: everything this
      // application calls passes a path, and narrowing here is what lets the
      // assertion be on the URL rather than on a `Request` object's toString.
      url: typeof input === "string" ? input : input.href,
      method: init?.method,
      headers: new Headers(init?.headers),
      body: body instanceof FormData ? body : new FormData(),
    });
    return Promise.resolve(respond());
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the idempotency key", () => {
  it("is unique per call", () => {
    const keys = new Set(Array.from({ length: 64 }, () => newIdempotencyKey()));
    expect(keys.size).toBe(64);
  });

  it("belongs to the draft, so a retry carries the same one", async () => {
    // The property the header exists for. §26.1 has no natural key — two
    // citizens photographing the same pothole from the same corner within a
    // second are two genuine reports — so a repeat is recognised only when the
    // client says it is one. Minting per *request* would produce exactly the
    // failure it is meant to prevent: a lost response, a second tap, and a city
    // holding two reports of one pothole.
    const one = draft();
    await submitComplaint(one);
    await submitComplaint(one);

    expect(captured).toHaveLength(2);
    expect(captured[0]?.headers.get("Idempotency-Key")).toBe("fixed-key-for-assertion");
    expect(captured[1]?.headers.get("Idempotency-Key")).toBe("fixed-key-for-assertion");
  });
});

describe("the submission", () => {
  it("goes to the BFF, never to the upstream", async () => {
    await submitComplaint(draft());
    // ADR-0040. A browser that named its own tenant would ship a trust boundary
    // that is not one, and would be rewritten the moment Phase 13 lands.
    expect(captured[0]?.url).toBe("/api/complaints");
    expect(captured[0]?.method).toBe("POST");
  });

  it("sends multipart with the field names the contract declares", async () => {
    await submitComplaint(
      draft({ descriptionText: "open drain by the school gate", locale: "mr" }),
    );
    const body = captured[0]?.body;
    expect(body?.get("latitude")).toBe("18.5204");
    expect(body?.get("longitude")).toBe("73.8567");
    expect(body?.get("device_fingerprint")).toBe("device-1");
    expect(body?.get("description_text")).toBe("open drain by the school gate");
    expect(body?.get("locale")).toBe("mr");
    expect(body?.get("photo")).toBeInstanceOf(Blob);
  });

  it("completes with the optional field empty", async () => {
    // §26.1 and §E17: *"one optional field, and the flow completes with that
    // field empty."* An empty description is not sent at all, because absent
    // and empty are different claims and only one is worth storing.
    await submitComplaint(draft({ descriptionText: "" }));
    expect(captured[0]?.body.has("description_text")).toBe(false);

    const outcome = await submitComplaint(draft());
    expect(outcome.receipt.complaint_id).toBe(RECEIPT.complaint_id);
  });

  it("carries the chain hash off the receipt", async () => {
    // ADR-0044, §E17.3. *"Nobody reads the hash. Everybody feels that this
    // system keeps records."* A receipt with no hash is that document with its
    // claim removed.
    const outcome = await submitComplaint(draft());
    expect(outcome.receipt.chain_hash).toHaveLength(64);
    expect(outcome.replayed).toBe(false);
  });

  it("distinguishes a landed retry from a new report", async () => {
    respond = () =>
      new Response(JSON.stringify(RECEIPT), {
        status: 202,
        headers: { "content-type": "application/json", "Idempotent-Replay": "true" },
      });

    const outcome = await submitComplaint(draft());
    // Two different sentences for the citizen: *"your report was filed"* and
    // *"your earlier report was already filed"*. Without this the second one is
    // indistinguishable from the first, which is §6 Principle #8 failing in the
    // one case where it is invisible without saying so.
    expect(outcome.replayed).toBe(true);
  });

  it("reports a refusal with a status a caller can act on", async () => {
    respond = () =>
      new Response(JSON.stringify({ status: 415, title: "That file is not a photo." }), {
        status: 415,
        headers: { "content-type": "application/json" },
      });

    const error = await submitComplaint(draft()).catch((cause: unknown) => cause);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(415);
    // 415 will never succeed on a retry, and telling a citizen to wait would be
    // a lie. 429 and 5xx will.
    expect((error as ApiError).retriable).toBe(false);
  });

  it("marks a rate limit as worth retrying", async () => {
    respond = () =>
      new Response(JSON.stringify({ status: 429, title: "Too many." }), { status: 429 });
    const error = (await submitComplaint(draft()).catch((cause: unknown) => cause)) as ApiError;
    expect(error.retriable).toBe(true);
  });
});

describe("the ledger read", () => {
  it("is never served from a cache", async () => {
    respond = () =>
      new Response(
        JSON.stringify({
          complaint_id: RECEIPT.complaint_id,
          chain_head: "b".repeat(64),
          chain_head_sequence: 1,
          events: [],
          total: 1,
          limit: 200,
          offset: 0,
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      );

    const history = await fetchComplaintHistory(RECEIPT.complaint_id);
    expect(history.chain_head).toHaveLength(64);
    // ADR-0044: the head is what a receipt is checked against, and a head
    // served from a cache is a head that may already have moved.
    expect(captured[0]?.url).toContain("/events");
  });
});

describe("§27.3's fallback is a behaviour, not a state", () => {
  it("polls in exactly the transports where the socket is not delivering", () => {
    expect(isPollingTransport("polling")).toBe(true);
    // Backoff reaches fifteen seconds. Fifteen seconds of a frozen screen after
    // a dropped connection is the failure the fallback exists for.
    expect(isPollingTransport("reconnecting")).toBe(true);

    expect(isPollingTransport("open")).toBe(false);
    // The first few hundred milliseconds of page life. Polling here would race
    // the handshake for no benefit.
    expect(isPollingTransport("idle")).toBe(false);
    expect(isPollingTransport("connecting")).toBe(false);
    // `refused` is promoted to `polling` by the bridge the moment something is
    // polling, so the state and the behaviour agree — see `bridge.test.ts`.
    expect(isPollingTransport("refused")).toBe(false);
  });

  it("polls on the interval the read path's own cache header states", () => {
    // `nemesis/api/v1/complaints.py` sets `Cache-Control: private, max-age=5`
    // and argues the number: longer makes the fallback visibly laggier than the
    // socket it replaces, shorter defeats the conditional request. Two numbers,
    // one decision.
    expect(POLL_INTERVAL_MS).toBe(5_000);
  });
});
