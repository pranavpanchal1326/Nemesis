import type { components } from "@/generated/api";

/**
 * The queue's ordering and its summary — §E19.1, §11.4.
 *
 * Pure functions over the generated types, in their own module, so the rules
 * below are asserted in vitest rather than inferred from a rendered list.
 *
 * Types are aliases of the generated ones. Nothing here describes a backend
 * contract (execution-plan Law 2); if a field this file wants is missing, the
 * fix is in `openapi.json`.
 */

export type QueueItem = components["schemas"]["ReviewItem"];
export type QueuePage = components["schemas"]["ReviewPage"];
export type DecisionKind = components["schemas"]["ReviewDecisionKind"];

/**
 * The default sort, and an honest note about what it is not.
 *
 * §E19.1 asks for **SLA remaining ascending, then severity**. Neither field
 * exists on `ReviewItem` today: SLA is Phase 12 and severity is scored after
 * the item leaves this queue. Inventing either — deriving a "remaining" from
 * `created_at` and a guessed deadline, or mapping `priority` onto a severity
 * label — would put a number on screen that looks like a measurement and is a
 * guess, which is the §E3.3 failure this product is written against.
 *
 * So the queue is sorted by what the server actually computes: `priority`
 * descending, then oldest first. That is `review.list_items`' own order, and it
 * is stated in the UI as what it is. When Phase 12 lands the deadline, this
 * function gains a first key and the screen gains a countdown; nothing else
 * moves.
 *
 * **Stable on ties**, by id. Two items filed in the same second at the same
 * priority must not swap places between renders — a queue whose rows reorder
 * under an officer's cursor is a queue that gets misclicked.
 */
export function sortQueue(items: readonly QueueItem[]): readonly QueueItem[] {
  return [...items].sort((a, b) => {
    if (a.priority !== b.priority) return b.priority - a.priority;
    const age = Date.parse(a.created_at) - Date.parse(b.created_at);
    if (age !== 0 && !Number.isNaN(age)) return age;
    return a.id.localeCompare(b.id);
  });
}

/** Items a reviewer can still act on. `decided_at` is the server's own answer
 *  to "has this been judged", and one judgement per item is forever (§11.4). */
export function isOpen(item: QueueItem): boolean {
  return item.decided_at === null;
}

export interface QueueSummary {
  readonly open: number;
  /** How many of each reason, most common first. Reasons are a closed set on
   *  the contract, so this is a complete account rather than a top-n. */
  readonly byReason: readonly { readonly reason: string; readonly count: number }[];
}

/**
 * What the strip above the queue says — §E19.1.
 *
 * > A dashboard should tell you what to do before it tells you how you are
 * > doing.
 *
 * The blueprint's example strip is about SLA breaches, and that sentence is
 * about *actionability* rather than about SLA specifically. What is actionable
 * and true today is the shape of the backlog: how many items are waiting and
 * why they are waiting, because the answer changes what a reviewer opens first
 * — seven `safety_trigger` items is a different morning from seven
 * `perceptual_duplicate` ones.
 *
 * The breach line the blueprint quotes is rendered beside this, marked as
 * Phase 12, rather than approximated. See `<BreachStrip>`.
 */
export function summarise(items: readonly QueueItem[]): QueueSummary {
  const open = items.filter(isOpen);
  const counts = new Map<string, number>();
  for (const item of open) counts.set(item.reason, (counts.get(item.reason) ?? 0) + 1);

  const byReason = [...counts.entries()]
    .map(([reason, count]) => ({ reason, count }))
    // Count descending, then the reason's own name, so the strip does not
    // reshuffle between two equally-common reasons on every poll.
    .sort((a, b) =>
      b.count - a.count !== 0 ? b.count - a.count : a.reason.localeCompare(b.reason),
    );

  return { open: open.length, byReason };
}

/**
 * The locale key for a reason.
 *
 * `ReviewReason` is a closed set on the contract and the bundle carries a key
 * per member; an unrecognised value renders through `t()`'s own missing-key
 * path — visibly, as `⟦reason.x⟧` — rather than as the raw token, because a
 * reason the frontend has never heard of means the backend added one and this
 * bundle needs a sentence, and that should be findable rather than plausible.
 */
export function reasonKey(reason: string): string {
  return `reason.${reason}`;
}
