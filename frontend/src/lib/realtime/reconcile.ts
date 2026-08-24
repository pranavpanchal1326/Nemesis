"use client";

import { realtimeStore } from "./store";

/**
 * The reconciliation rule — §E14.3. Corrects §E2 defect #12.
 *
 * > **The WebSocket is a hint, not a source of truth.**
 *
 * §E2 defect #12 names the failure exactly: *"An event stream and a read API
 * disagreeing, with no stated authority, produces a UI that is confidently
 * wrong."* Confidently wrong is the worst state this product can be in — the
 * whole proposition is that the record can be trusted — so the authority is
 * stated once, here, and no surface is allowed to invent its own.
 *
 * The loop is: **event arrives → optimistic patch → the affected entity is
 * refetched from the read path on idle.** Two properties make that safe. The
 * read path is a projection, so a refetch is one query and not a replay. And
 * `version` is on the read schema, so a refetch that returns an older version
 * than the one already applied is discarded rather than flickering backwards.
 *
 * **Idle, not immediate.** A burst of thirty envelopes for one cluster is one
 * refetch, not thirty. `requestIdleCallback` also means reconciliation never
 * competes with the frame budget §E23 sets for the clay layer — the map keeps
 * its 60 fps and the numbers catch up a moment later, which is the correct
 * priority for a surface someone is looking at.
 */

export type Refetch = (entityId: string) => Promise<void>;

export interface ReconcilerOptions {
  readonly refetch: Refetch;
  /** Coalescing window. Defaults to two 12 fps steps (§E11). */
  readonly windowMs?: number;
  readonly schedule?: (task: () => void, timeoutMs: number) => void;
}

type IdleScheduler = (task: () => void, timeoutMs: number) => void;

function defaultScheduler(): IdleScheduler {
  if (typeof requestIdleCallback === "function") {
    return (task, timeoutMs) => {
      requestIdleCallback(
        () => {
          task();
        },
        { timeout: timeoutMs },
      );
    };
  }
  return (task, timeoutMs) => {
    setTimeout(task, timeoutMs);
  };
}

/**
 * Start reconciling. Returns a stop function.
 *
 * Every entity the socket touched is refetched exactly once per window, and
 * only cleared from the dirty set **after** the refetch resolves — so a failed
 * refetch is retried on the next window rather than silently leaving a screen
 * showing a hint it never confirmed.
 */
export function startReconciler(options: ReconcilerOptions): () => void {
  const windowMs = options.windowMs ?? 168;
  const schedule = options.schedule ?? defaultScheduler();

  let running = false;
  let stopped = false;

  const drain = () => {
    if (stopped || running) return;
    const dirty = realtimeStore.getState().dirty;
    if (dirty.length === 0) return;

    running = true;
    void Promise.allSettled(
      dirty.map(async (entityId) => {
        await options.refetch(entityId);
        realtimeStore.getState().markClean(entityId);
      }),
    ).finally(() => {
      running = false;
      // Anything that arrived *while* this drain was in flight is still dirty,
      // and the store subscription that would have scheduled it was suppressed
      // by the `running` guard. Without this line those entities wait for the
      // next unrelated event to arrive before they are ever reconciled — which
      // on a quiet ward is indefinitely, and produces exactly the confidently
      // wrong screen §E2 defect #12 describes.
      if (!stopped && realtimeStore.getState().dirty.length > 0) schedule(drain, windowMs);
    });
  };

  const unsubscribe = realtimeStore.subscribe((state, previous) => {
    if (state.dirty === previous.dirty) return;
    if (state.dirty.length === 0) return;
    schedule(drain, windowMs);
  });

  return () => {
    stopped = true;
    unsubscribe();
  };
}

/**
 * Apply a freshly-read version, rejecting anything staler than what is already
 * applied.
 *
 * `version` is *"the log position this representation reflects"* — the backend
 * exposes it on the read schema rather than hiding it inside the ETag precisely
 * so a client polling §27.3's 5-second fallback can tell whether anything
 * moved. Out-of-order responses are normal on a slow connection, and without
 * this a screen flickers backwards through its own history.
 */
export function isNewer(incomingVersion: number, appliedVersion: number | undefined): boolean {
  return appliedVersion === undefined || incomingVersion > appliedVersion;
}
