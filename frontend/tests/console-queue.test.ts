import { describe, expect, it } from "vitest";

import { diffDocuments } from "../src/console/policy/diff";
import {
  isOpen,
  reasonKey,
  sortQueue,
  summarise,
  type QueueItem,
} from "../src/console/review/queue";
import { REVIEW_REASONS } from "../src/generated/enums";
import consoleBundle from "../src/i18n/base/console.json" with { type: "json" };

/**
 * F4 and F5's pure rules — §E19.1, §E19.8, §11.4.
 *
 * The browser tests prove the screens work. These prove the two things a
 * browser test would prove badly: that the queue's order is the order the
 * server computes, and that the policy diff is structural rather than textual.
 */

function item(overrides: Partial<QueueItem>): QueueItem {
  return {
    id: "00000000-0000-0000-0000-000000000001",
    complaint_id: "00000000-0000-0000-0000-0000000000aa",
    created_at: "2026-08-01T10:00:00Z",
    decided_at: null,
    evidence: {},
    occurrences: 1,
    priority: 5,
    reason: "low_trust",
    redacted_media: [],
    status: "open",
    trust_score: 0.4,
    ...overrides,
  };
}

describe("§E19.1 — the queue is in the order the server computes", () => {
  it("puts higher priority first", () => {
    const sorted = sortQueue([
      item({ id: "a", priority: 2 }),
      item({ id: "b", priority: 9 }),
      item({ id: "c", priority: 5 }),
    ]);
    expect(sorted.map((row) => row.id)).toEqual(["b", "c", "a"]);
  });

  it("puts the oldest first within a priority", () => {
    const sorted = sortQueue([
      item({ id: "new", priority: 5, created_at: "2026-08-03T10:00:00Z" }),
      item({ id: "old", priority: 5, created_at: "2026-08-01T10:00:00Z" }),
    ]);
    expect(sorted.map((row) => row.id)).toEqual(["old", "new"]);
  });

  it("is stable when priority and age are identical", () => {
    // Two rows that swap places between renders are two rows an officer
    // misclicks. Tie-broken on id so the order is a function of the data.
    const rows = [item({ id: "b" }), item({ id: "a" })];
    expect(sortQueue(rows).map((row) => row.id)).toEqual(["a", "b"]);
    expect(sortQueue(sortQueue(rows)).map((row) => row.id)).toEqual(["a", "b"]);
  });

  it("does not mutate its input", () => {
    // The caller holds the query cache's array. Sorting it in place would
    // reorder somebody else's data.
    const rows = [item({ id: "b", priority: 1 }), item({ id: "a", priority: 9 })];
    sortQueue(rows);
    expect(rows.map((row) => row.id)).toEqual(["b", "a"]);
  });
});

describe("§11.4 — decided means decided", () => {
  it("reads open-ness from decided_at and nothing else", () => {
    // `status` is a free string on the contract; `decided_at` is the server's
    // own answer to "has a human judged this", and one judgement is forever.
    expect(isOpen(item({ decided_at: null, status: "escalated" }))).toBe(true);
    expect(isOpen(item({ decided_at: "2026-08-02T09:00:00Z", status: "open" }))).toBe(false);
  });

  it("summarises only the open items, most common reason first", () => {
    const summary = summarise([
      item({ id: "1", reason: "safety_trigger" }),
      item({ id: "2", reason: "low_trust" }),
      item({ id: "3", reason: "low_trust" }),
      item({ id: "4", reason: "exif_mismatch", decided_at: "2026-08-02T09:00:00Z" }),
    ]);
    expect(summary.open).toBe(3);
    expect(summary.byReason).toEqual([
      { reason: "low_trust", count: 2 },
      { reason: "safety_trigger", count: 1 },
    ]);
  });

  it("has a sentence for every reason the contract can send", () => {
    // `ReviewReason` is a closed set. A member with no key would render as
    // `⟦reason.x⟧` on the strip an officer reads first.
    const bundle = consoleBundle as Record<string, string>;
    for (const reason of REVIEW_REASONS) {
      expect(bundle[reasonKey(reason)], `${reasonKey(reason)} is missing`).toBeTypeOf("string");
    }
  });
});

describe("§E19.8 — the diff is structural", () => {
  it("reports nothing for two documents that differ only in key order", () => {
    // A text diff of the pretty-printed JSON would report every line as
    // changed, on a screen an operator reads before activating a policy.
    expect(diffDocuments({ a: 1, b: 2 }, { b: 2, a: 1 })).toEqual([]);
  });

  it("addresses a nested leaf by its dotted path", () => {
    const changes = diffDocuments({ dedup: { threshold: 0.82 } }, { dedup: { threshold: 0.88 } });
    expect(changes).toEqual([
      { kind: "changed", path: "dedup.threshold", before: "0.82", after: "0.88" },
    ]);
  });

  it("distinguishes a number from the string that looks like it", () => {
    // `1` and `"1"` are a difference an operator should see, and `String()`
    // would collapse them.
    expect(diffDocuments({ floor: 1 }, { floor: "1" })).toEqual([
      { kind: "changed", path: "floor", before: "1", after: '"1"' },
    ]);
  });

  it("treats an empty collection as a leaf", () => {
    // "rules: [] was removed" is a change that matters, and recursing into an
    // empty array would produce no rows at all.
    expect(diffDocuments({ rules: [] }, {})).toEqual([
      { kind: "removed", path: "rules", before: "[]" },
    ]);
  });

  it("indexes array members, because position is meaningful in a rule list", () => {
    const changes = diffDocuments({ rules: [{ to: "roads" }] }, { rules: [{ to: "water" }] });
    expect(changes).toEqual([
      { kind: "changed", path: "rules.0.to", before: '"roads"', after: '"water"' },
    ]);
  });

  it("reports additions and removals in path order", () => {
    const changes = diffDocuments({ b: 1 }, { a: 1, c: 1 });
    expect(changes.map((change) => `${change.kind}:${change.path}`)).toEqual([
      "added:a",
      "removed:b",
      "added:c",
    ]);
  });
});
