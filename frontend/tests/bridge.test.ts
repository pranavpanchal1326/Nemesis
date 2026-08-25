import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BASE } from "../src/lib/i18n/bundles.ts";
import {
  startRealtimeBridge,
  UNAVAILABLE_CAUSE_KEY,
  type RealtimeEndpoint,
} from "../src/lib/realtime/bridge.ts";
import {
  REFUSED_CAUSE_KEY,
  type SocketHandle,
  type SocketOptions,
} from "../src/lib/realtime/socket.ts";
import {
  clearEnvelopeListeners,
  publishEnvelope,
  realtimeStore,
} from "../src/lib/realtime/store.ts";
import type { RealtimeEnvelope } from "../src/lib/realtime/envelope.ts";

/**
 * The four gaps this file closes were all *named in a plan and then not built*
 * — which is the failure mode a progress table marked "Done" is worst at
 * catching. So each one is asserted here as a behaviour, not as a state:
 *
 * - **A5** — nothing mounted the socket. `connectRealtime` had fourteen
 *   assertions and no caller.
 * - **A4** — `transport: "polling"` was a state with nothing polling behind it.
 * - **A6** — `resyncRequired` was recorded and no surface refetched.
 * - **A3** — see `mutations.test.ts`.
 */

const ENDPOINT: RealtimeEndpoint = {
  available: true,
  url: "ws://127.0.0.1:8000/ws/pipeline-events",
  tenantId: "11111111-1111-1111-1111-111111111111",
};

function envelope(cursor: number, entityId = "complaint-a"): RealtimeEnvelope {
  return {
    event_type: "severity_scored",
    entity_type: "complaint",
    entity_id: entityId,
    sequence: cursor,
    timestamp: "2026-08-25T00:00:00.000000Z",
    cursor,
    payload: { new_severity: 71.5 },
  };
}

/** A socket that records how it was asked to connect and never opens one. */
function fakeConnect() {
  const calls: SocketOptions[] = [];
  let closed = 0;
  const connect = (options: SocketOptions): SocketHandle => {
    calls.push(options);
    return { close: () => void (closed += 1), handshakes: () => 1 };
  };
  return { connect, calls, closes: () => closed };
}

/** Runs the reconciler's scheduled drain immediately, so a test asserts the
 *  behaviour rather than the idle callback. */
const immediate = (task: () => void) => {
  task();
};

/** Named, so the linter can tell a deliberate no-op from a forgotten body. */
function noop(): void {
  // The bridge only needs to know it was called; the counting fakes do that.
}

beforeEach(() => {
  realtimeStore.getState().reset();
  clearEnvelopeListeners();
});

afterEach(() => {
  realtimeStore.getState().reset();
  clearEnvelopeListeners();
});

describe("A5 — the socket has a consumer", () => {
  it("connects with the tenant the BFF supplied, never one it invented", async () => {
    const socket = fakeConnect();
    const stop = startRealtimeBridge({
      discover: () => Promise.resolve(ENDPOINT),
      refetch: () => Promise.resolve(),
      refetchAll: noop,
      connect: socket.connect,
      schedule: immediate,
    });

    await vi.waitFor(() => {
      expect(socket.calls).toHaveLength(1);
    });
    // ADR-0040: the tenant is server-held. A bridge that read an env var here
    // would ship the trust boundary §E2 defect #11 is about.
    expect(socket.calls[0]?.tenantId).toBe(ENDPOINT.tenantId);
    expect(socket.calls[0]?.url).toBe(ENDPOINT.url);

    stop();
    expect(socket.closes()).toBe(1);
  });

  it("an envelope reaches the read path, because the socket is a hint", async () => {
    const refetched: string[] = [];
    const socket = fakeConnect();
    const stop = startRealtimeBridge({
      discover: () => Promise.resolve(ENDPOINT),
      refetch: (entityId) => {
        refetched.push(entityId);
        return Promise.resolve();
      },
      refetchAll: noop,
      connect: socket.connect,
      schedule: immediate,
    });

    publishEnvelope(envelope(1, "complaint-a"));

    await vi.waitFor(() => {
      expect(refetched).toContain("complaint-a");
    });
    // §E14.3: cleared only after the refetch resolves, so a failed read is
    // retried rather than leaving a screen showing an unconfirmed hint.
    await vi.waitFor(() => {
      expect(realtimeStore.getState().dirty).toHaveLength(0);
    });
    stop();
  });

  it("a discovery that answers 'nothing to connect to' degrades calmly", async () => {
    const socket = fakeConnect();
    const stop = startRealtimeBridge({
      discover: () => Promise.resolve({ available: false }),
      refetch: () => Promise.resolve(),
      refetchAll: noop,
      connect: socket.connect,
      schedule: immediate,
    });

    await vi.waitFor(() => {
      expect(realtimeStore.getState().transport).toBe("polling");
    });
    expect(socket.calls).toHaveLength(0);
    expect(realtimeStore.getState().degradation?.cause).toBe(UNAVAILABLE_CAUSE_KEY);
    // A key, and one the bundle actually holds — §E10.1. A banner whose
    // sentence is a literal in `src/` is a banner no locale can translate.
    expect(BASE.common[UNAVAILABLE_CAUSE_KEY]).toBeTypeOf("string");
    stop();
  });

  it("stopping before discovery resolves opens no socket", async () => {
    const socket = fakeConnect();
    let resolve: ((value: RealtimeEndpoint) => void) | undefined;
    const stop = startRealtimeBridge({
      discover: () =>
        new Promise<RealtimeEndpoint>((r) => {
          resolve = r;
        }),
      refetch: () => Promise.resolve(),
      refetchAll: noop,
      connect: socket.connect,
      schedule: immediate,
    });

    stop();
    resolve?.(ENDPOINT);
    await Promise.resolve();
    await Promise.resolve();

    // StrictMode mounts effects twice in development. A bridge that connected
    // after its own teardown would leave the first mount's socket open with
    // nothing holding the handle that closes it.
    expect(socket.calls).toHaveLength(0);
  });
});

describe("A4 — a refused upgrade degrades to polling, not to nothing", () => {
  it("promotes `refused` to `polling` and keeps the refusal's own cause", async () => {
    const socket = fakeConnect();
    const stop = startRealtimeBridge({
      discover: () => Promise.resolve(ENDPOINT),
      refetch: () => Promise.resolve(),
      refetchAll: noop,
      connect: socket.connect,
      schedule: immediate,
    });

    // What `socket.ts` does on close code 1008: the kill switch, or an unknown
    // tenant. Both mean *do not come back*.
    realtimeStore.getState().setTransport("refused", {
      cause: REFUSED_CAUSE_KEY,
      since: Date.now(),
    });

    await vi.waitFor(() => {
      expect(realtimeStore.getState().transport).toBe("polling");
    });
    // The banner still names the refusal. Overwriting the cause with "polling"
    // would replace the honest reason with the mechanism.
    expect(realtimeStore.getState().degradation?.cause).toBe(REFUSED_CAUSE_KEY);
    stop();
  });

  it("stops promoting once the bridge is stopped", async () => {
    const socket = fakeConnect();
    const stop = startRealtimeBridge({
      discover: () => Promise.resolve(ENDPOINT),
      refetch: () => Promise.resolve(),
      refetchAll: noop,
      connect: socket.connect,
      schedule: immediate,
    });
    await vi.waitFor(() => {
      expect(socket.calls).toHaveLength(1);
    });
    stop();

    realtimeStore.getState().setTransport("refused", { cause: REFUSED_CAUSE_KEY, since: 0 });
    await Promise.resolve();
    expect(realtimeStore.getState().transport).toBe("refused");
  });
});

describe("A6 — resyncRequired has a consumer", () => {
  it("drops every cached read once, and clears the flag", async () => {
    let refetchAllCalls = 0;
    const socket = fakeConnect();
    const stop = startRealtimeBridge({
      discover: () => Promise.resolve(ENDPOINT),
      refetch: () => Promise.resolve(),
      refetchAll: () => void (refetchAllCalls += 1),
      connect: socket.connect,
      schedule: immediate,
    });

    realtimeStore.getState().requireResync(true);

    await vi.waitFor(() => {
      expect(refetchAllCalls).toBe(1);
    });
    // Cleared by whoever acted on it. A flag that stays set is a flag whose
    // next transition to true is invisible.
    expect(realtimeStore.getState().resyncRequired).toBe(false);
    stop();
  });

  it("fires once per transition, not once per store write", async () => {
    let refetchAllCalls = 0;
    const socket = fakeConnect();
    const stop = startRealtimeBridge({
      discover: () => Promise.resolve(ENDPOINT),
      refetch: () => Promise.resolve(),
      refetchAll: () => void (refetchAllCalls += 1),
      connect: socket.connect,
      schedule: immediate,
    });

    realtimeStore.getState().requireResync(true);
    await vi.waitFor(() => {
      expect(refetchAllCalls).toBe(1);
    });

    // Ordinary traffic while the flag was being cleared must not re-fire it.
    publishEnvelope(envelope(2, "complaint-b"));
    publishEnvelope(envelope(3, "complaint-b"));
    await Promise.resolve();
    expect(refetchAllCalls).toBe(1);

    // A second genuine transition does fire again.
    realtimeStore.getState().requireResync(true);
    await vi.waitFor(() => {
      expect(refetchAllCalls).toBe(2);
    });
    stop();
  });
});
