import { startReconciler, type Refetch } from "./reconcile";
import { connectRealtime, type SocketHandle, type SocketOptions } from "./socket";
import { realtimeStore } from "./store";

/**
 * Mounting the transport — A4, A5, A6. §E14.2, §E14.3.
 *
 * `connectRealtime` and `startReconciler` were written, tested in isolation,
 * and **called by nothing**. A transport nobody mounts is a library, and a
 * library that ships inside a product is dead weight that passes its own tests.
 * This is the mount point.
 *
 * It is a plain function rather than a React effect for the reason
 * `reconcile.ts` and `socket.ts` are plain functions: every behaviour here is a
 * gate clause — *a refused upgrade degrades to polling*, *a client past the
 * replay window refetches* — and a gate clause asserted through a component
 * render is a clause whose test is about React. `RealtimeProvider` is thirty
 * lines of wiring on top; this is what is actually tested.
 *
 * Every collaborator is injected, so the whole thing runs against fakes in
 * milliseconds and against a real socket in a browser without a branch.
 */

export interface BridgeOptions {
  /** Where and as whom. Asks the BFF, because the tenant is server-held
   *  (ADR-0040) and a client that carried it in its bundle would be §E2 defect
   *  #11's shape. `null` means there is nothing to connect to. */
  readonly discover: () => Promise<RealtimeEndpoint | null>;
  /** Refetch everything known about one entity, from the read path. */
  readonly refetch: Refetch;
  /** Drop every cached read. Called once when the server says the gap exceeded
   *  its replay window. */
  readonly refetchAll: () => void;
  /** Injected in tests. Defaults to the real socket. */
  readonly connect?: (options: SocketOptions) => SocketHandle;
  /** Injected in tests, forwarded to the reconciler. */
  readonly schedule?: (task: () => void, timeoutMs: number) => void;
}

/** What `/api/realtime` answers. A shape this application owns rather than one
 *  the backend publishes, so describing it here is not a Law 2 violation. */
export interface RealtimeEndpoint {
  readonly available: boolean;
  readonly url?: string | undefined;
  readonly tenantId?: string | undefined;
}

/**
 * The cause shown when a deployment has no hub to connect to.
 *
 * A **key**, never the words. §E10.1, and Phase 18's gate: the banner a citizen
 * sees when the system is degraded must not be the one piece of copy the locale
 * registry can never reach.
 */
export const UNAVAILABLE_CAUSE_KEY = "degraded.realtimeUnavailable";

export function startRealtimeBridge(options: BridgeOptions): () => void {
  const connect = options.connect ?? connectRealtime;
  const store = realtimeStore.getState.bind(realtimeStore);

  let handle: SocketHandle | null = null;
  let stopped = false;

  const stopReconciler = startReconciler(
    options.schedule === undefined
      ? { refetch: options.refetch }
      : { refetch: options.refetch, schedule: options.schedule },
  );

  /**
   * A4 — the promotion that makes `polling` mean what the store says it means.
   *
   * `socket.ts` sets `refused` and stops reconnecting, which is correct and is
   * as far as it should go: it has no business knowing whether anything polls.
   * This does. Until this line existed, a refused upgrade degraded to *nothing
   * updating at all*, behind a banner saying the saved state was being
   * refreshed every few seconds — honest about the intent, wrong about the
   * fact, which is worse than either.
   *
   * The degradation is carried through untouched, so the banner still names the
   * refusal rather than the fallback.
   */
  const unsubscribeTransport = realtimeStore.subscribe((state) => {
    if (stopped || state.transport !== "refused") return;
    store().setTransport("polling");
  });

  /**
   * A6 — the gap exceeded the server's `MAX_REPLAY`.
   *
   * *"A replay that takes longer than a page reload is worse than a page
   * reload"*, so the server says so instead of sending ten thousand
   * animations. Everything cached was learned from a stream this client is no
   * longer current with, so all of it is dropped and re-read. Without a
   * consumer the flag was recorded and nothing happened — a client past the
   * window silently stopped being current, which is §E2 defect #12's
   * confidently-wrong screen arriving by a different route.
   *
   * The flag is cleared here rather than by whoever set it, because clearing it
   * is a claim that somebody acted on it.
   */
  const unsubscribeResync = realtimeStore.subscribe((state, previous) => {
    if (stopped || !state.resyncRequired || previous.resyncRequired) return;
    options.refetchAll();
    store().requireResync(false);
  });

  // A5 — the connection itself, once discovery answers.
  void options.discover().then((endpoint) => {
    if (stopped) return;

    if (
      endpoint === null ||
      !endpoint.available ||
      endpoint.url === undefined ||
      endpoint.tenantId === undefined
    ) {
      // Not an error. A deployment with no tenant configured has nothing to
      // stream, and the surfaces render their saved state and say so — §E13,
      // calmly.
      store().setTransport("polling", { cause: UNAVAILABLE_CAUSE_KEY, since: Date.now() });
      return;
    }

    handle = connect({ url: endpoint.url, tenantId: endpoint.tenantId });
  });

  return () => {
    stopped = true;
    unsubscribeTransport();
    unsubscribeResync();
    stopReconciler();
    handle?.close();
  };
}
