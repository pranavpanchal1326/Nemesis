import "server-only";

import type { QueuePage } from "@/console/review/queue";
import { upstream } from "@/server/upstream";

/**
 * The first page of the queue, read on the server — §E19.1, §E13.
 *
 * Server-rendered rather than fetched on mount, and the reason is the same one
 * §E18 gives for the public pages: a screen whose content only exists after
 * hydration is a screen that is blank in the Lighthouse run, blank to a screen
 * reader for the first second, and blank on the ward-office terminal whose
 * JavaScript is a version behind. The queue is the console's most-read screen
 * and it should be *there*.
 *
 * After the first paint the client owns it — the socket says an item changed
 * and `/api/review/queue` answers with the same generated shape (§E14.3).
 *
 * **A failure is a state, not an exception.** `ok: false` renders the sentence
 * *"the queue could not be read; nothing has been decided in the meantime"*,
 * which is a true and useful thing to tell a reviewer. Throwing would give them
 * an error boundary that says less.
 */
export type QueueRead = { readonly ok: true; readonly page: QueuePage } | { readonly ok: false };

export async function fetchQueue(limit = 50): Promise<QueueRead> {
  const { data, error } = await upstream.GET("/api/v1/review/queue", {
    params: { query: { limit } },
    // No caching layer between an officer and a work list. Two reviewers shown
    // the same cached page open the same item, and §11.4 allows one judgement
    // per item, ever.
    cache: "no-store",
  });

  if (error !== undefined) return { ok: false };
  return { ok: true, page: data };
}
