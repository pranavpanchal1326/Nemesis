import { createStore } from "zustand/vanilla";

import type { RealtimeEnvelope } from "./envelope";

/**
 * The event bus — §E14.2.
 *
 * > **WebSocket** owns *change*, not data. Events → Zustand → transient
 * > subscriptions that drive shader uniforms and marker transforms **without a
 * > React re-render**.
 *
 * That last clause is why this is a *vanilla* store rather than a hook. At the
 * frame rates §E23 budgets for — 5 000 instanced pins at 60 fps with Ollama
 * running — a React render per event is not a performance concern, it is the
 * whole frame. The clay layer subscribes with `subscribe()` and writes
 * uniforms; React components that genuinely need to re-render use `useStore`,
 * and they are the minority.
 */

/** How the browser is currently learning about change. */
export type TransportState =
  | "idle"
  | "connecting"
  /** Socket open and delivering. */
  | "open"
  /**
   * The upgrade was refused. §E14.3: *"A refused upgrade is normal degraded
   * mode, not an error."* The `realtime_websocket_hub` kill switch is checked
   * **before** the handshake precisely so clients take the polling fallback
   * immediately — and never retry in a loop against a capability somebody
   * deliberately switched off.
   */
  | "refused"
  /** Falling back to §27.3's 5-second poll of the read path. */
  | "polling"
  /** Socket dropped; a reconnect is scheduled. */
  | "reconnecting";

export interface Degradation {
  /**
   * A **locale key**, not a sentence.
   *
   * An earlier version held the English words. That made the one banner a
   * citizen sees when the system is degraded the one piece of copy the Phase 5
   * locale registry could never reach — against Phase 18's own gate, *"a locale
   * added in the control plane appears in the UI with no code change"*, and on
   * the surface where being understood matters most.
   *
   * Resolved at the point of render, where a `Strings` is in hand. Named,
   * honest, and shown in secondary ink — never an error colour (§E26).
   */
  readonly cause: string;
  readonly since: number;
}

export interface RealtimeState {
  readonly transport: TransportState;
  /**
   * The outbox position the client has seen. Reconnecting with `?since=` is
   * what makes a ninety-second disconnect cost a round trip instead of a pin
   * that never appears with nothing to indicate it should have.
   */
  readonly cursor: number;
  readonly lastMessageAt: number | null;
  readonly degradation: Degradation | null;
  /**
   * Entities the socket says have changed and the read path has not yet
   * confirmed. §E14.3's reconciliation queue — the socket is a hint, so
   * everything it touches is provisional until refetched.
   */
  readonly dirty: readonly string[];
  /**
   * A bounded tail of envelopes. The pipeline theatre (§E17.2) and the
   * tracking ledger (§E17.4) read it; it is not a cache of the system's state,
   * and nothing renders a *fact* from it.
   */
  readonly recent: readonly RealtimeEnvelope[];
  /** Set when the gap exceeded the server's replay window (`MAX_REPLAY`). */
  readonly resyncRequired: boolean;
}

/** A replay that takes longer than a page reload is worse than a page reload —
 *  which is also why the server caps its own. */
const RECENT_LIMIT = 500;

export interface RealtimeActions {
  setTransport: (transport: TransportState, degradation?: Degradation | null) => void;
  ingest: (envelope: RealtimeEnvelope) => void;
  noteMessage: (at: number) => void;
  markClean: (entityId: string) => void;
  requireResync: (required: boolean) => void;
  reset: () => void;
}

const initial: RealtimeState = {
  transport: "idle",
  cursor: 0,
  lastMessageAt: null,
  degradation: null,
  dirty: [],
  recent: [],
  resyncRequired: false,
};

export const realtimeStore = createStore<RealtimeState & RealtimeActions>()((set) => ({
  ...initial,

  setTransport: (transport, degradation) => {
    set((state) => ({
      transport,
      degradation: degradation === undefined ? state.degradation : degradation,
    }));
  },

  ingest: (envelope) => {
    set((state) => {
      const recent = [...state.recent, envelope];
      const dirty = state.dirty.includes(envelope.entity_id)
        ? state.dirty
        : [...state.dirty, envelope.entity_id];

      return {
        // Monotonic. Replay can deliver an envelope the client already has —
        // §26.3's `?since=` is inclusive of the boundary in the worst case —
        // and moving the cursor backwards would replay it forever.
        cursor: Math.max(state.cursor, envelope.cursor),
        lastMessageAt: Date.now(),
        recent: recent.length > RECENT_LIMIT ? recent.slice(-RECENT_LIMIT) : recent,
        dirty,
      };
    });
  },

  noteMessage: (at) => {
    set({ lastMessageAt: at });
  },

  markClean: (entityId) => {
    set((state) => ({ dirty: state.dirty.filter((id) => id !== entityId) }));
  },

  requireResync: (required) => {
    set({ resyncRequired: required });
  },

  reset: () => {
    set(initial);
  },
}));

type EnvelopeListener = (envelope: RealtimeEnvelope) => void;

const listeners = new Set<EnvelopeListener>();

/**
 * Subscribe outside React — §E14.2's transient subscription.
 *
 * The clay layer, the pipeline theatre and the press's jitter all use this.
 * Returns an unsubscribe, and it never causes a render.
 *
 * **Why a listener set rather than a store subscription.** Deriving "what is
 * new" by diffing `recent` looks tidier and is wrong: `recent` is a bounded
 * ring, so once it starts trimming, a length comparison silently skips
 * envelopes. Losing a `cluster_match_found` is losing the hero scene, and it
 * would fail only under load — the one condition nobody tests by hand.
 */
export function subscribeToEvents(listener: EnvelopeListener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/**
 * The single ingress. Everything that arrives on the socket comes through here,
 * so the store and the transient subscribers can never disagree about what was
 * delivered.
 */
export function publishEnvelope(envelope: RealtimeEnvelope): void {
  realtimeStore.getState().ingest(envelope);
  for (const listener of listeners) listener(envelope);
}

/** Test seam: drop every transient subscriber. */
export function clearEnvelopeListeners(): void {
  listeners.clear();
}
