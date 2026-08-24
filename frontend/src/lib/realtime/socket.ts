"use client";

import { isEnvelope, isHeartbeat, isResyncRequired, parseMessage } from "./envelope";
import { publishEnvelope, realtimeStore } from "./store";

/**
 * The connection — §E14.1, §E14.3, ADR-0040.
 *
 * The socket connects **directly**, not through the BFF. That is the single
 * deliberate exception to §E14.1's rule that every browser-to-API request goes
 * through a route handler: `/ws/pipeline-events` is an unauthenticated,
 * one-directional stream by construction, so proxying it would add a hop with
 * no security benefit.
 *
 * Three behaviours here are not conveniences; each one corrects a specific
 * failure the backend went to trouble to make detectable.
 *
 * **1 — A refused upgrade is not an error.** `realtime_websocket_hub` is
 * checked *before* the handshake, and `nemesis/api/v1/realtime.py` says why:
 * *"accepting the socket and then closing it teaches every client to reconnect
 * in a loop against a capability somebody deliberately switched off."* Close
 * code 1008 therefore ends reconnection permanently for this session and moves
 * to polling with a calm banner (§E26 `<DegradedBanner>`).
 *
 * **2 — An open socket can be dead.** Heartbeats arrive as ordinary envelopes
 * on the same queue as everything else, so a tab that has stopped reading fills
 * its queue on heartbeats alone and is shed on schedule. The client half of
 * that bargain is to notice when they stop: silence past the watchdog means the
 * socket is open and useless, and the fix is to reconnect rather than to wait.
 *
 * **3 — A gap is replayed, not skipped.** Reconnecting carries `?since=` so the
 * server replays from the cursor. Without it a client either replays everything
 * or silently misses whatever happened, and on a map "silently missed" is a pin
 * that never appears with nothing to indicate it should have.
 */

/** `HEARTBEAT_SECONDS` in `nemesis/realtime/service.py`. */
const HEARTBEAT_MS = 20_000;

/**
 * Three missed heartbeats. Two would make a single scheduling hiccup on a
 * loaded laptop look like a dead socket — and this laptop is running Ollama.
 */
const WATCHDOG_MS = HEARTBEAT_MS * 3;

/** `CLOSE_POLICY` in `nemesis/api/v1/realtime.py`: unknown tenant, or the kill
 *  switch. Both mean *do not come back*. */
const CLOSE_POLICY_VIOLATION = 1008;

const BACKOFF_MS = [500, 1_000, 2_000, 4_000, 8_000, 15_000] as const;

export interface SocketOptions {
  readonly url: string;
  readonly tenantId: string;
  /** Injected in tests. Defaults to the platform `WebSocket`. */
  readonly factory?: (url: string) => WebSocket;
  readonly now?: () => number;
}

export interface SocketHandle {
  readonly close: () => void;
  /** Exposed for the gate that asserts a refused upgrade produces no storm. */
  readonly handshakes: () => number;
}

export function connectRealtime(options: SocketOptions): SocketHandle {
  const factory = options.factory ?? ((url: string) => new WebSocket(url));
  const now = options.now ?? (() => Date.now());

  const store = realtimeStore.getState.bind(realtimeStore);
  let socket: WebSocket | null = null;
  let attempt = 0;
  let handshakes = 0;
  let stopped = false;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let watchdog: ReturnType<typeof setInterval> | null = null;

  const clearTimers = () => {
    if (reconnectTimer !== null) clearTimeout(reconnectTimer);
    if (watchdog !== null) clearInterval(watchdog);
    reconnectTimer = null;
    watchdog = null;
  };

  const endpoint = (): string => {
    const url = new URL(options.url);
    url.searchParams.set("tenant_id", options.tenantId);
    const cursor = store().cursor;
    // `since=0` would ask for the whole outbox from the beginning of time. A
    // first connection wants "from now"; only a reconnection has a gap.
    if (cursor > 0) url.searchParams.set("since", String(cursor));
    return url.toString();
  };

  const scheduleReconnect = () => {
    if (stopped) return;
    const delay = BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)] ?? 15_000;
    attempt += 1;
    store().setTransport("reconnecting");
    // Jitter, so a hub restart does not bring every open tab back in the same
    // 50 ms window — the thundering herd that turns a blip into an outage.
    reconnectTimer = setTimeout(open, delay + Math.random() * 250);
  };

  const startWatchdog = () => {
    if (watchdog !== null) clearInterval(watchdog);
    watchdog = setInterval(() => {
      const last = store().lastMessageAt;
      if (last === null) return;
      if (now() - last < WATCHDOG_MS) return;
      // Open and dead. Closing ourselves is what turns an invisible failure
      // into a reconnect with a cursor.
      socket?.close();
    }, HEARTBEAT_MS);
  };

  function open(): void {
    if (stopped) return;
    handshakes += 1;
    store().setTransport("connecting");

    const ws = factory(endpoint());
    socket = ws;

    ws.onopen = () => {
      attempt = 0;
      store().noteMessage(now());
      store().setTransport("open", null);
      startWatchdog();
    };

    ws.onmessage = (event: MessageEvent<string>) => {
      store().noteMessage(now());
      const message = parseMessage(event.data);
      if (message === null) return;

      if (isHeartbeat(message)) return;

      if (isResyncRequired(message)) {
        // The gap exceeded the server's replay window. Handing the client ten
        // thousand animations to play would be slower than a reload, so the
        // server says so and the surfaces refetch from the read path.
        store().requireResync(true);
        return;
      }

      if (isEnvelope(message)) publishEnvelope(message);
    };

    ws.onclose = (event: CloseEvent) => {
      clearTimers();
      if (stopped) return;

      if (event.code === CLOSE_POLICY_VIOLATION) {
        // Deliberate refusal — the kill switch, or an unknown tenant. Retrying
        // is the one thing the server explicitly asked clients not to do.
        store().setTransport("refused", {
          cause:
            "Live updates are switched off on this deployment. " +
            "Showing the latest saved state instead, refreshed every few seconds.",
          since: now(),
        });
        return;
      }

      scheduleReconnect();
    };

    ws.onerror = () => {
      // Deliberately empty. `onclose` always follows, and handling both means
      // two reconnects for one failure.
    };
  }

  open();

  return {
    close: () => {
      stopped = true;
      clearTimers();
      socket?.close();
      store().setTransport("idle");
    },
    handshakes: () => handshakes,
  };
}
