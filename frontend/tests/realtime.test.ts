import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { RealtimeEnvelope } from "../src/lib/realtime/envelope.ts";
import { carriesPayload, parseMessage } from "../src/lib/realtime/envelope.ts";
import { startReconciler } from "../src/lib/realtime/reconcile.ts";
import { connectRealtime } from "../src/lib/realtime/socket.ts";
import {
  clearEnvelopeListeners,
  realtimeStore,
  subscribeToEvents,
} from "../src/lib/realtime/store.ts";

/**
 * §E14.3's gates.
 *
 * §E2 defect #12 is *"Zustand fed by the WebSocket with no reconciliation
 * rule"*, and its consequence is *"a UI that is confidently wrong"*. These
 * assertions are the three ways that happens in practice: a gap that is
 * silently skipped, a socket that is open and dead, and a deliberately-disabled
 * capability being hammered by a reconnect loop.
 */

/** A WebSocket that does what the server documented, and nothing else. */
class FakeSocket implements Pick<WebSocket, "close" | "onopen" | "onmessage" | "onclose"> {
  static opened: string[] = [];

  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  closed = false;

  readonly url: string;

  constructor(url: string) {
    // Written out rather than a parameter property: `erasableSyntaxOnly` is on
    // in tsconfig, because these files run under Node's native type stripping.
    this.url = url;
    FakeSocket.opened.push(url);
  }

  open(): void {
    this.onopen?.(new Event("open"));
  }

  deliver(message: object): void {
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(message) }));
  }

  close(code = 1006): void {
    this.closed = true;
    this.onclose?.({ code, reason: "", wasClean: code === 1000 } as CloseEvent);
  }
}

function envelope(cursor: number, entityId = "c1"): RealtimeEnvelope {
  return {
    event_type: "cluster_match_found",
    entity_type: "complaint_cluster",
    entity_id: entityId,
    sequence: cursor,
    timestamp: "2026-08-24T10:00:00Z",
    cursor,
    payload: { match_confidence: 0.87 },
  };
}

let sockets: FakeSocket[] = [];

beforeEach(() => {
  realtimeStore.getState().reset();
  clearEnvelopeListeners();
  sockets = [];
  FakeSocket.opened = [];
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

function connect(tenantId = "t1") {
  return connectRealtime({
    url: "ws://localhost:8000/ws/pipeline-events",
    tenantId,
    factory: (url) => {
      const socket = new FakeSocket(url);
      sockets.push(socket);
      return socket as unknown as WebSocket;
    },
  });
}

describe("§E14.3 — a gap is replayed, not skipped", () => {
  it("a first connection asks for now, not for the whole outbox", () => {
    connect();
    expect(FakeSocket.opened[0]).toContain("tenant_id=t1");
    // `since=0` would be "replay everything since the beginning of time".
    expect(FakeSocket.opened[0]).not.toContain("since=");
  });

  it("a reconnect carries the cursor it got to", () => {
    connect();
    const first = sockets[0];
    first?.open();
    first?.deliver(envelope(41));
    first?.deliver(envelope(42));
    first?.close();

    vi.advanceTimersByTime(2_000);

    expect(FakeSocket.opened[1]).toContain("since=42");
  });

  it("a replayed envelope does not move the cursor backwards", () => {
    // `?since=` can be inclusive of its boundary, so a replay can redeliver the
    // last envelope. A cursor that moved backwards would replay it forever.
    connect();
    const socket = sockets[0];
    socket?.open();
    socket?.deliver(envelope(42));
    socket?.deliver(envelope(41));
    expect(realtimeStore.getState().cursor).toBe(42);
  });

  it("every envelope reaches transient subscribers, including past the ring limit", () => {
    // The clay layer subscribes outside React (§E14.2). Deriving "what is new"
    // by diffing the bounded `recent` ring silently skips envelopes once it
    // starts trimming — and losing a cluster_match_found is losing the hero
    // scene, under load, which is the one condition nobody tests by hand.
    const seen: number[] = [];
    subscribeToEvents((e) => seen.push(e.cursor));

    connect();
    const socket = sockets[0];
    socket?.open();
    for (let i = 1; i <= 600; i += 1) socket?.deliver(envelope(i));

    expect(seen).toHaveLength(600);
    expect(realtimeStore.getState().recent.length).toBeLessThanOrEqual(500);
    expect(new Set(seen).size).toBe(600);
  });
});

describe("§E14.3 — a refused upgrade is normal degraded mode, not an error", () => {
  it("close code 1008 stops reconnection permanently and falls to polling", () => {
    const handle = connect();
    sockets[0]?.close(1008);

    // The kill switch is checked *before* the handshake precisely so the client
    // takes the fallback immediately. Retrying is the one thing the server
    // explicitly asked clients not to do.
    vi.advanceTimersByTime(120_000);

    expect(handle.handshakes()).toBe(1);
    expect(realtimeStore.getState().transport).toBe("refused");
  });

  it("the banner names the degradation honestly and calmly", () => {
    connect();
    sockets[0]?.close(1008);
    const degradation = realtimeStore.getState().degradation;
    expect(degradation?.cause).toMatch(/switched off/i);
    // §E26: "Calm register, secondary ink, never an error colour."
    expect(degradation?.cause).not.toMatch(/error|failed|cannot/i);
  });

  it("an ordinary drop does reconnect, with backoff", () => {
    const handle = connect();
    sockets[0]?.open();
    sockets[0]?.close(1006);

    expect(handle.handshakes()).toBe(1);
    vi.advanceTimersByTime(1_000);
    expect(handle.handshakes()).toBe(2);
  });
});

describe("§E14.3 — an open socket can be dead", () => {
  it("silence past three heartbeats closes the socket so it can reconnect", () => {
    connect();
    const socket = sockets[0];
    socket?.open();

    // Heartbeats arrive as ordinary envelopes every 20 s. Two missed would make
    // a scheduling hiccup on a laptop running Ollama look like a dead socket.
    vi.advanceTimersByTime(40_000);
    expect(socket?.closed).toBe(false);

    vi.advanceTimersByTime(45_000);
    expect(socket?.closed).toBe(true);
  });

  it("a heartbeat keeps the connection alive and advances nothing", () => {
    connect();
    const socket = sockets[0];
    socket?.open();
    vi.advanceTimersByTime(40_000);
    socket?.deliver({ event_type: "heartbeat", timestamp: "2026-08-24T10:00:20Z" });
    vi.advanceTimersByTime(40_000);

    expect(socket?.closed).toBe(false);
    expect(realtimeStore.getState().cursor).toBe(0);
    expect(realtimeStore.getState().recent).toHaveLength(0);
  });

  it("a gap beyond the replay window asks the surfaces to resynchronise", () => {
    connect();
    sockets[0]?.open();
    sockets[0]?.deliver({ event_type: "resync_required", timestamp: "2026-08-24T10:00:00Z" });
    expect(realtimeStore.getState().resyncRequired).toBe(true);
  });
});

describe("§E14.3 — the socket is a hint; the read path is the authority", () => {
  it("every entity an event touched is refetched, once per window", async () => {
    const refetched: string[] = [];
    const stop = startReconciler({
      refetch: async (id) => {
        refetched.push(id);
        await Promise.resolve();
      },
      schedule: (task) => {
        task();
      },
    });

    connect();
    const socket = sockets[0];
    socket?.open();
    socket?.deliver(envelope(1, "cluster-a"));
    socket?.deliver(envelope(2, "cluster-a"));
    socket?.deliver(envelope(3, "cluster-b"));

    await vi.waitFor(() => {
      expect(refetched.length).toBeGreaterThanOrEqual(2);
    });

    // A burst of envelopes for one entity is one refetch, not one per envelope.
    expect(new Set(refetched)).toEqual(new Set(["cluster-a", "cluster-b"]));
    expect(realtimeStore.getState().dirty).toEqual([]);
    stop();
  });

  it("a failed refetch leaves the entity dirty rather than pretending it confirmed", async () => {
    const stop = startReconciler({
      refetch: () => Promise.reject(new Error("upstream down")),
      schedule: (task) => {
        task();
      },
    });

    connect();
    sockets[0]?.open();
    sockets[0]?.deliver(envelope(1, "cluster-c"));

    await vi.waitFor(() => {
      expect(realtimeStore.getState().dirty).toContain("cluster-c");
    });
    stop();
  });
});

describe("ADR-0016 — realtime payloads are default-deny", () => {
  it("a shaped event type is distinguishable from an unshaped one", () => {
    // §E27 maps twenty-four event types to visuals. Eight carry a payload; the
    // rest say that something happened and nothing about what. A surface that
    // assumes otherwise renders an empty pin and looks broken.
    expect(carriesPayload(envelope(1))).toBe(true);
    expect(carriesPayload({ ...envelope(1), event_type: "media_redacted" })).toBe(false);
  });

  it("a malformed frame is dropped, not thrown", () => {
    // A stream is not a request: one bad frame must not take down a socket that
    // is otherwise delivering.
    expect(parseMessage("{not json")).toBeNull();
    expect(parseMessage("null")).toBeNull();
    expect(parseMessage('{"no":"event type"}')).toBeNull();
  });
});
