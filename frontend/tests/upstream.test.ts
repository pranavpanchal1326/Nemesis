import { afterEach, describe, expect, it, vi } from "vitest";

/**
 * ADR-0060 — an unreachable API is an answer, not an exception.
 *
 * Every BFF handler renders a designed message when the generated client
 * reports an error, and none of them survived the upstream being *absent*:
 * `fetch` rejects rather than resolving when there is no server, the rejection
 * escaped the handler, and Next answered `500` with a framework error page. An
 * officer with the API stopped saw a browser failure inside a console screen
 * instead of the sentence this product wrote for that moment.
 *
 * **The stub has to be installed before the module loads, and finding out why
 * is the same lesson `tests/strings.test.ts` records.** `openapi-fetch` reads
 * `globalThis.fetch` once, at `createClient()`, which `server/upstream.ts`
 * calls at module scope. A stub assigned inside a test body is decoration: the
 * client has already closed over the real `fetch`. Resetting the module
 * registry and importing inside the stub's lifetime is what makes this
 * load-bearing — and it is why the assertion below survived being watched
 * failing with the middleware removed.
 */

/** What `fetch` rejects with when a request never completes. */
function unreachable(): typeof fetch {
  return vi.fn(() => Promise.reject(new TypeError("fetch failed")));
}

async function loadAgainst(fetchImpl: typeof fetch) {
  vi.resetModules();
  vi.stubGlobal("fetch", fetchImpl);
  return import("@/server/upstream");
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
  vi.restoreAllMocks();
});

describe("the typed client, with nothing listening", () => {
  it("answers 503 rather than rejecting", async () => {
    const { upstream } = await loadAgainst(unreachable());

    // No `await expect(...).rejects` anywhere: the whole claim is that this
    // resolves. A handler that has to try/catch is a handler that will forget.
    const { data, error, response } = await upstream.GET("/api/v1/complaints/{complaint_id}", {
      params: { path: { complaint_id: "00000000-0000-4000-8000-000000000000" } },
    });

    expect(data).toBeUndefined();
    expect(error).toBeDefined();
    expect(response.status).toBe(503);
  });

  it("shapes the refusal as the backend's own problem document", async () => {
    // Handlers narrow this exactly as they narrow a real refusal, so an outage
    // needs no second code path anywhere above the seam.
    const { upstream } = await loadAgainst(unreachable());
    const { error, response } = await upstream.GET("/api/v1/complaints/{complaint_id}", {
      params: { path: { complaint_id: "00000000-0000-4000-8000-000000000000" } },
    });

    expect(response.headers.get("content-type")).toContain("application/problem+json");
    // Read through `error` rather than the body: `openapi-fetch` has already
    // parsed and consumed it, which is the behaviour that makes this
    // conversion invisible to a handler in the first place.
    expect(error).toMatchObject({ status: 503 });
    expect(typeof (error as { title?: unknown }).title, "the refusal names itself").toBe("string");
  });

  it("re-throws anything that is not a transport failure", async () => {
    // The narrowing, asserted. A bug disguised as a well-formed 503 would be
    // rendered by every surface as "the service is not answering", and a bug
    // that reads as an outage is a bug nobody looks for.
    const bug = new RangeError("this is our own fault");
    const { upstream } = await loadAgainst(vi.fn(() => Promise.reject(bug)));

    await expect(
      upstream.GET("/api/v1/complaints/{complaint_id}", {
        params: { path: { complaint_id: "00000000-0000-4000-8000-000000000000" } },
      }),
    ).rejects.toThrow(bug);
  });
});

describe("the raw path §26.1's multipart submission needs", () => {
  it("answers 503 rather than rejecting", async () => {
    // `upstreamFetch` does not go through the client's middleware, so it
    // carries the conversion itself — and a citizen's photograph reaching a
    // framework stack trace is the worst place in the product for this to
    // happen, which is why it is asserted separately rather than assumed.
    const { upstreamFetch } = await loadAgainst(unreachable());

    const response = await upstreamFetch("/api/v1/complaints", {
      method: "POST",
      headers: new Headers(),
    });

    expect(response.status).toBe(503);
    expect(response.headers.get("content-type")).toContain("application/problem+json");
  });

  it("re-throws anything that is not a transport failure", async () => {
    const bug = new RangeError("this is our own fault");
    const { upstreamFetch } = await loadAgainst(vi.fn(() => Promise.reject(bug)));

    await expect(upstreamFetch("/api/v1/complaints", { headers: new Headers() })).rejects.toThrow(
      bug,
    );
  });
});
