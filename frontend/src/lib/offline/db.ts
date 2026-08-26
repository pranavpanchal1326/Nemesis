"use client";

/**
 * IndexedDB, wrapped once so nothing else has to look at it — F17, ADR-0056.
 *
 * IndexedDB is an event-based API from 2011 and every operation is a request
 * object with `onsuccess` and `onerror` handlers. That is not a reason to add a
 * dependency; it is a reason to write the promise adapter once, here, and never
 * mention `IDBRequest` above this file.
 *
 * **The store is keyed by the idempotency key** (ADR-0056), which makes `put` an
 * upsert on the *submission* rather than on a row: enqueueing twice writes one
 * row, for the same reason sending twice files one report.
 *
 * **Everything degrades to "no queue" rather than to an exception.** A private
 * window with storage disabled, a webview with a locked-down profile, a browser
 * where the user has blocked site data — every one of them is a real field
 * device, and none of them is a reason for a capture screen to fail to open.
 * `openQueue()` returns `null` there, the surface says the queue is unavailable
 * in words (§E3.3), and submissions go straight out over the network.
 */

const DB_NAME = "nemesis.offline";
const DB_VERSION = 1;

/** The one object store. Named for what it is, in the backend's own vocabulary:
 *  an outbox is a durable list of things that must eventually be sent. */
export const OUTBOX = "outbox";

function promised<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => {
      resolve(request.result);
    };
    request.onerror = () => {
      reject(request.error ?? new Error("indexeddb request failed"));
    };
  });
}

let opening: Promise<IDBDatabase | null> | null = null;

/**
 * The database, opened once per session.
 *
 * Cached as the *promise* rather than as the result, so two callers racing at
 * startup — the queue's drain and the field screen's first render — open one
 * connection rather than two.
 */
export function openQueue(): Promise<IDBDatabase | null> {
  opening ??= open();
  return opening;
}

function open(): Promise<IDBDatabase | null> {
  if (typeof indexedDB === "undefined") return Promise.resolve(null);

  return new Promise((resolve) => {
    let request: IDBOpenDBRequest;
    try {
      request = indexedDB.open(DB_NAME, DB_VERSION);
    } catch {
      // Firefox in private browsing throws here rather than erroring the
      // request. Same answer either way: there is no queue on this device.
      resolve(null);
      return;
    }

    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(OUTBOX)) {
        // `keyPath` rather than an auto-increment: the key **is** the
        // idempotency key (ADR-0056), which is what makes a duplicate enqueue
        // unrepresentable rather than merely unlikely.
        const store = db.createObjectStore(OUTBOX, { keyPath: "idempotencyKey" });
        // Ordered by when the person committed, so a drain sends the oldest
        // evidence first. A field hand who photographed four jobs this morning
        // should not have the fourth arrive before the first.
        store.createIndex("createdAt", "createdAt", { unique: false });
      }
    };

    request.onsuccess = () => {
      const db = request.result;
      // A second tab upgrading the schema blocks this connection forever
      // otherwise, and a field app open in two tabs is a person who opened a
      // link. Closing on version change is what lets the other tab proceed.
      db.onversionchange = () => {
        db.close();
        opening = null;
      };
      resolve(db);
    };

    request.onerror = () => {
      resolve(null);
    };
  });
}

/** Run a transaction and resolve when it has actually committed. */
export async function withStore<T>(
  mode: IDBTransactionMode,
  work: (store: IDBObjectStore) => Promise<T> | T,
): Promise<T | null> {
  const db = await openQueue();
  if (db === null) return null;

  const transaction = db.transaction(OUTBOX, mode);
  const store = transaction.objectStore(OUTBOX);
  const result = await work(store);

  // **Waiting for `complete`, not for the last request.** A write that resolved
  // on its request would report success while the transaction was still open,
  // and a tab killed in that window loses the row — which is the exact failure
  // the Phase 22 gate's second clause is about.
  await new Promise<void>((resolve, reject) => {
    transaction.oncomplete = () => {
      resolve();
    };
    transaction.onabort = () => {
      reject(transaction.error ?? new Error("transaction aborted"));
    };
    transaction.onerror = () => {
      reject(transaction.error ?? new Error("transaction failed"));
    };
  });

  return result;
}

export const request = promised;

/** Whether this device can queue at all. Surfaced in words, never assumed. */
export async function queueAvailable(): Promise<boolean> {
  return (await openQueue()) !== null;
}

/** Drop the cached connection. Tests and a signed-out session use it. */
export function resetQueueConnection(): void {
  opening = null;
}
