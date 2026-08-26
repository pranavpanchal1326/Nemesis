"use client";

/**
 * The outbox — §E21, §E25 Phase 22, ADR-0056, F17.
 *
 * > A complaint and a closure photo captured fully offline sync correctly on
 * > reconnect. **A killed app mid-upload resumes without duplicating or losing
 * > the submission.** … Background upload with per-item state, so nothing fails
 * > silently.
 *
 * **The queue is the only writer of a submission**, online or off. That is
 * ADR-0056's fourth consequence and it is worth restating where somebody will
 * read it: the tempting design posts directly and falls back to the queue when
 * the post fails, which produces a durability path that only executes when the
 * network is down — a path nobody exercises and therefore nobody trusts. Here,
 * `enqueue()` then `drain()` is what an ordinary submission does on a perfect
 * connection, so the Phase 22 gate and a Tuesday afternoon take the same code.
 *
 * **A row's state is on the row.** §E21 asks for *"visible per-item state, so
 * nothing fails silently"*; a queue whose UI state lived beside it in React
 * would lose that state on the restart the gate is about.
 *
 * **Failure is classified, not retried blindly.** `ApiError.retriable` already
 * draws the line the citizen surface draws — a 429 or a 5xx will succeed later;
 * a 415 never will. A row that fails non-retriably is **parked** with the
 * server's own sentence on it, and a person is told. §E3.3: a spinner over a
 * permanent failure is a lie told to somebody standing in a basement.
 */

import { ApiError, submitComplaint, type ComplaintDraft } from "@/lib/api/complaints";

import { openQueue, OUTBOX, request, withStore } from "./db";

/** Where a queued submission has got to. */
export type QueueState =
  /** Written down, waiting for a drain. */
  | "queued"
  /** A request is in flight right now. */
  | "sending"
  /** The server has it. Kept briefly so the person sees it land. */
  | "sent"
  /** The attempt failed and will be tried again. */
  | "retrying"
  /** The attempt failed in a way that retrying cannot fix. */
  | "parked";

/**
 * One row of the outbox.
 *
 * A `ComplaintDraft` plus what has happened to it. The draft's `photo` and
 * `audio` are `Blob`s and are stored as `Blob`s (ADR-0056): IndexedDB stores
 * structured clones, so the bytes stay in the browser's blob store instead of
 * being base64-inflated by a third into the object store.
 */
export interface QueuedSubmission {
  /** The primary key. See ADR-0056 — this is the identity of the submission. */
  readonly idempotencyKey: string;
  readonly draft: ComplaintDraft;
  readonly state: QueueState;
  readonly attempts: number;
  /** Epoch millis. The index the drain reads in order. */
  readonly createdAt: number;
  /** The complaint id the server returned, once it has. */
  readonly complaintId: string | null;
  /** Whether the server recognised the key and returned the original report. */
  readonly replayed: boolean;
  /** The server's own sentence, when something went wrong. */
  readonly failure: string | null;
}

type Listener = (rows: readonly QueuedSubmission[]) => void;

const listeners = new Set<Listener>();

export function subscribeToQueue(listener: Listener): () => void {
  listeners.add(listener);
  void list().then((rows) => {
    listener(rows);
  });
  return () => {
    listeners.delete(listener);
  };
}

async function publish(): Promise<void> {
  const rows = await list();
  for (const listener of listeners) listener(rows);
}

/** Everything in the outbox, oldest first. */
export async function list(): Promise<readonly QueuedSubmission[]> {
  const rows = await withStore("readonly", (store) =>
    request(store.index("createdAt").getAll() as IDBRequest<QueuedSubmission[]>),
  );
  return rows ?? [];
}

export async function get(idempotencyKey: string): Promise<QueuedSubmission | null> {
  const row = await withStore("readonly", (store) =>
    request(store.get(idempotencyKey) as IDBRequest<QueuedSubmission | undefined>),
  );
  return row ?? null;
}

/**
 * Write a draft down.
 *
 * Returns `false` when this device has no queue at all — a private window, a
 * locked-down webview — which the caller states in words rather than treating
 * as an error (see `db.ts`).
 *
 * **Re-enqueueing an existing key does not reset it.** A row that is already
 * `sending` must not be dragged back to `queued` by a second tap; the upsert
 * keeps the newer draft's bytes and the older row's progress, because the two
 * are the same submission by definition.
 */
export async function enqueue(draft: ComplaintDraft): Promise<boolean> {
  const existing = await get(draft.idempotencyKey);
  const row: QueuedSubmission = {
    idempotencyKey: draft.idempotencyKey,
    draft,
    state: existing?.state ?? "queued",
    attempts: existing?.attempts ?? 0,
    createdAt: existing?.createdAt ?? Date.now(),
    complaintId: existing?.complaintId ?? null,
    replayed: existing?.replayed ?? false,
    failure: existing?.failure ?? null,
  };

  const written = await withStore("readwrite", (store) => request(store.put(row)));
  if (written === null) return false;
  await publish();
  return true;
}

async function patch(idempotencyKey: string, changes: Partial<QueuedSubmission>): Promise<void> {
  const current = await get(idempotencyKey);
  if (current === null) return;
  await withStore("readwrite", (store) => request(store.put({ ...current, ...changes })));
  await publish();
}

export async function remove(idempotencyKey: string): Promise<void> {
  await withStore("readwrite", (store) => request(store.delete(idempotencyKey)));
  await publish();
}

/**
 * How many times a row is retried before it parks.
 *
 * Not "forever". A device that has been in a dead zone for a day comes back and
 * drains; a row that has failed six times against a *reachable* server has met
 * something a seventh attempt will not fix, and saying so is more use to a
 * field hand than a spinner that never stops.
 */
export const MAX_ATTEMPTS = 6;

/**
 * Which rows a drain should try.
 *
 * A pure function over the rows, so `tests/offline.test.ts` can assert the
 * policy — that `sent` is left alone, that `parked` is not retried, that a row
 * mid-flight in another drain is not sent twice — without a database and
 * without a network.
 */
export function drainable(rows: readonly QueuedSubmission[]): readonly QueuedSubmission[] {
  return rows.filter(
    (row) => (row.state === "queued" || row.state === "retrying") && row.attempts < MAX_ATTEMPTS,
  );
}

/**
 * Whether a row is finished with, and how a restart should read it.
 *
 * **The subtle one.** A row left in `sending` when the process died is *not*
 * evidence that the request failed — the 202 may have been written on the
 * server and lost on the way back. It is retried, and it is safe to retry
 * precisely because the key goes with it: the server answers the repeat with
 * `Idempotent-Replay: true` and the original complaint. That is the whole of
 * the gate's second clause, and it is one line of policy.
 */
export function resumeState(row: QueuedSubmission): QueueState {
  if (row.state === "sent") return "sent";
  if (row.state === "parked") return "parked";
  return "retrying";
}

let draining = false;

/**
 * Send everything that can be sent.
 *
 * Serial rather than parallel, deliberately: these are multipart uploads of
 * photographs over the worst connection in the system, and six at once on a 2G
 * link is six that all time out. Guarded against re-entry, because `online`,
 * `visibilitychange` and a mount can all fire within a frame of each other.
 */
export async function drain(): Promise<void> {
  if (draining) return;
  draining = true;
  try {
    for (const row of drainable(await list())) {
      await send(row);
    }
  } finally {
    draining = false;
  }
}

async function send(row: QueuedSubmission): Promise<void> {
  await patch(row.idempotencyKey, { state: "sending", attempts: row.attempts + 1 });
  try {
    const outcome = await submitComplaint(row.draft);
    await patch(row.idempotencyKey, {
      state: "sent",
      complaintId: outcome.receipt.complaint_id,
      replayed: outcome.replayed,
      failure: null,
    });
  } catch (error) {
    const retriable = error instanceof ApiError ? error.retriable : true;
    await patch(row.idempotencyKey, {
      // A network failure — the field's normal state — is not an `ApiError` at
      // all: `fetch` rejects. That is the most retriable thing there is, which
      // is why the fallback above is `true` rather than `false`.
      state: retriable && row.attempts + 1 < MAX_ATTEMPTS ? "retrying" : "parked",
      failure: error instanceof Error ? error.message : String(error),
    });
  }
}

/**
 * Resume the outbox after a restart, then drain it.
 *
 * Called once when the field surface mounts. The two halves are separate
 * because the first is the gate's second clause and the second is its first:
 * *reopen and re-read* is what makes a killed app safe, and *drain on reconnect*
 * is what makes a dead zone temporary.
 */
export async function resume(): Promise<void> {
  const db = await openQueue();
  if (db === null) return;

  for (const row of await list()) {
    const state = resumeState(row);
    if (state !== row.state) await patch(row.idempotencyKey, { state });
  }
  await drain();
}

/**
 * Drain whenever the browser thinks it can reach the network again.
 *
 * `online` is a hint and not a promise — a captive portal fires it — so a
 * failed drain simply leaves the rows retriable. `visibilitychange` is here
 * because a phone in a pocket does not fire `online` until it is looked at, and
 * a field hand walking out of a basement looks at the phone.
 */
export function drainOnReconnect(): () => void {
  const go = (): void => {
    void drain();
  };
  window.addEventListener("online", go);
  document.addEventListener("visibilitychange", go);
  return () => {
    window.removeEventListener("online", go);
    document.removeEventListener("visibilitychange", go);
  };
}

/** The outbox store name, re-exported so a surface never imports `db.ts`. */
export { OUTBOX };
