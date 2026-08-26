import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import {
  HONESTY_COUNTS,
  HONESTY_SOURCES,
  HONESTY_STATUSES,
  SURFACE_CLAIMS,
  SYSTEM_CLAIMS,
} from "@/public/generated/honesty";

/**
 * §E16.2 — the honesty table, checked rather than trusted.
 *
 * `npm run honesty:check` already fails CI when the generated table drifts from
 * the blueprints. That covers *transcription*. What it cannot cover is the
 * table being well-formed as data: a row with no capability, a summary count
 * that no longer counts anything, a status outside the vocabulary, or a
 * `closesAt` that says nothing.
 *
 * Those matter more here than they would anywhere else, because §E28 says it
 * about itself: *"an honesty table that is wrong is worse than no table, because
 * it is the artefact a reader trusts instead of checking."* This file is the
 * checking.
 */

const REPO = join(fileURLToPath(new URL(".", import.meta.url)), "..", "..");

describe("the table is a table", () => {
  it("is not empty, in either half", () => {
    // A generator whose parser silently matched nothing would publish an empty
    // honesty page, which reads as "we claim nothing" rather than as a bug.
    expect(SYSTEM_CLAIMS.length).toBeGreaterThan(30);
    expect(SURFACE_CLAIMS.length).toBeGreaterThan(20);
  });

  it("names its sources, and they exist", () => {
    expect(HONESTY_SOURCES.length).toBe(2);
    for (const source of HONESTY_SOURCES) {
      const [file] = source.split(" ");
      expect(file, "a source citation with no file").toBeDefined();
      expect(() => readFileSync(join(REPO, file ?? ""), "utf8")).not.toThrow();
    }
  });

  it("gives every row a capability to be a claim about", () => {
    for (const claim of [...SYSTEM_CLAIMS, ...SURFACE_CLAIMS]) {
      expect(claim.capability.length).toBeGreaterThan(0);
    }
  });
});

describe("the vocabulary is closed", () => {
  it("uses only the five labels, or none", () => {
    const allowed = new Set<string | null>([...HONESTY_STATUSES, null]);
    for (const claim of SYSTEM_CLAIMS) {
      expect(allowed.has(claim.status), `${claim.capability}: ${String(claim.status)}`).toBe(true);
    }
    for (const claim of SURFACE_CLAIMS) {
      expect(allowed.has(claim.component)).toBe(true);
      expect(allowed.has(claim.data)).toBe(true);
    }
  });

  it("keeps REFRAMED, which is the row a lazier parser would have dropped", () => {
    /**
     * `REFRAMED` appears once, on *"Intake/Classification/Ops as 'agents'"*, and
     * it is the most honest row in §44: v1.0 called three deterministic pipeline
     * stages "agents" and v2.0 withdrew the claim. A parser that mapped unknown
     * labels onto ROADMAP would have deleted precisely that correction — so its
     * survival is asserted rather than assumed.
     */
    const reframed = SYSTEM_CLAIMS.filter((claim) => claim.status === "REFRAMED");
    expect(reframed.length).toBe(1);
    expect(reframed[0]?.note).toContain("deterministic pipeline stages");
  });
});

describe("the counts summarise the rows they claim to summarise", () => {
  it("matches, so the headline figure cannot drift from the table", () => {
    expect(HONESTY_COUNTS.system).toBe(SYSTEM_CLAIMS.length);
    expect(HONESTY_COUNTS.surface).toBe(SURFACE_CLAIMS.length);
    expect(HONESTY_COUNTS.systemReal).toBe(
      SYSTEM_CLAIMS.filter((claim) => claim.status === "REAL").length,
    );
    expect(HONESTY_COUNTS.surfaceFinished).toBe(
      SURFACE_CLAIMS.filter((claim) => claim.component === "REAL" && claim.data === "REAL").length,
    );
  });
});

describe("the parse keeps the words the rows are about", () => {
  it("does not eat underscores out of an identifier", () => {
    /**
     * Found by looking at the rendered page: stripping `_` as markdown emphasis
     * turned `pending_classification` into `pendingclassification` and
     * `exif_check_completed` into one word — on the page whose entire purpose is
     * to be checkable against its source.
     */
    const notes = SURFACE_CLAIMS.map((claim) => `${claim.capability} ${claim.dataNote}`).join(" ");
    expect(notes).toContain("pending_classification");
    expect(notes).toContain("exif_check_completed");
  });

  it("does not leave a bracket open", () => {
    // The same pass stripped a trailing `)` unconditionally, publishing
    // "(ADR-0044" with nothing closing it.
    for (const claim of [...SYSTEM_CLAIMS, ...SURFACE_CLAIMS]) {
      const text = "note" in claim ? claim.note : `${claim.componentNote} ${claim.dataNote}`;
      const opens = (text.match(/\(/g) ?? []).length;
      const closes = (text.match(/\)/g) ?? []).length;
      expect(opens, `unbalanced parentheses: ${text}`).toBe(closes);
    }
  });
});

describe("a surface row says when it closes", () => {
  it("names a milestone, a phase, or 'done' — never nothing", () => {
    // "Closes at: —" is a roadmap entry with no roadmap. Every row on §E28
    // carries one, and a blank means the parser lost a column rather than that
    // the row is genuinely open-ended.
    for (const claim of SURFACE_CLAIMS) {
      expect(claim.closesAt.length, `${claim.capability} has no closing point`).toBeGreaterThan(0);
      expect(claim.closesAt, `${claim.capability}: ${claim.closesAt}`).toMatch(
        /M\d|Phase \d|done/i,
      );
    }
  });
});
