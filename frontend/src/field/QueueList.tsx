"use client";

import { useEffect, useState } from "react";

import { notTranslatable, plural, t, type Strings } from "@/lib/i18n/strings";
import { drain, remove, subscribeToQueue, type QueuedSubmission } from "@/lib/offline/queue";

/**
 * The outbox, visible — §E21, F17.
 *
 * > **Background upload with per-item state, so nothing fails silently.**
 *
 * That clause is the whole component. Five states, each rendered as a word
 * rather than as a colour or a spinner, and each one honest about what it
 * actually means:
 *
 * · **queued** — written down; nothing has been attempted.
 * · **sending** — a request is in flight right now.
 * · **sent** — the server has it, and the row says whether the server
 *   recognised it as a *repeat* (`Idempotent-Replay`), because "already filed"
 *   and "filed" are different sentences and §6 Principle #8 is about exactly
 *   the cases where the difference is invisible.
 * · **retrying** — it failed and will be tried again, with the attempt count.
 * · **parked** — it failed in a way retrying cannot fix, **with the server's own
 *   sentence on it**. §E3.3: a spinner over a permanent failure is a lie told
 *   to somebody standing in a basement.
 *
 * **The list is the queue, not a copy of it.** It subscribes to the store, so a
 * drain that succeeds while the screen is open updates it, and a restart shows
 * the same rows because the rows are in IndexedDB rather than in React
 * (ADR-0056).
 *
 * **A parked row can be discarded and nothing else can.** Retrying is automatic
 * and bounded; deleting a row that the server *may* have accepted would destroy
 * the one thing that makes a re-send safe — the key. So the only removable row
 * is one that has been refused for a reason retrying cannot fix.
 */
export function QueueList({ strings }: { readonly strings: Strings }) {
  const [rows, setRows] = useState<readonly QueuedSubmission[]>([]);

  useEffect(() => subscribeToQueue(setRows), []);

  const pending = rows.filter((row) => row.state !== "sent").length;

  return (
    <section className="outbox" aria-labelledby="field-outbox">
      <h2 id="field-outbox" className="type-title">
        {t(strings, "field.outbox.title")}
      </h2>
      <p className="type-caption">
        {plural(strings, "field.outbox.count", pending, { count: pending })}
      </p>

      {rows.length === 0 ? (
        <p className="type-body">{t(strings, "field.outbox.empty")}</p>
      ) : (
        <ul className="outbox__list">
          {rows.map((row) => (
            <li key={row.idempotencyKey} className="outbox__row" data-state={row.state}>
              <p className="outbox__state type-heading">
                {t(strings, `field.outbox.state.${row.state}`)}
              </p>

              {/* The key, in the data face. It is the identity of this
                  submission everywhere in the system (ADR-0056), and a field
                  hand reading a failure to somebody on the phone needs to be
                  able to read it out. */}
              <p className="outbox__key type-mono-data">
                {notTranslatable(row.idempotencyKey.slice(0, 8))}
              </p>

              {row.attempts > 0 ? (
                <p className="type-caption">
                  {plural(strings, "field.outbox.attempts", row.attempts, {
                    count: row.attempts,
                  })}
                </p>
              ) : null}

              {row.state === "sent" && row.complaintId !== null ? (
                <p className="type-caption">
                  {t(strings, row.replayed ? "field.outbox.replayed" : "field.outbox.filed")}{" "}
                  <span className="type-mono-data">{notTranslatable(row.complaintId)}</span>
                </p>
              ) : null}

              {row.failure === null ? null : (
                // The server's own sentence, forwarded. §25 already strips the
                // upstream problem document's internals; what survives is the
                // half written for a person.
                <p className="outbox__failure type-body">{notTranslatable(row.failure)}</p>
              )}

              {row.state === "parked" ? (
                <button
                  type="button"
                  className="outbox__discard type-micro"
                  onClick={() => {
                    void remove(row.idempotencyKey);
                  }}
                >
                  {t(strings, "field.outbox.discard")}
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      )}

      <button
        type="button"
        className="outbox__drain type-heading"
        onClick={() => {
          void drain();
        }}
      >
        {t(strings, "field.outbox.sendNow")}
      </button>
    </section>
  );
}
