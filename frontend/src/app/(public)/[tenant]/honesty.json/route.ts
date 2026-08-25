import {
  HONESTY_COUNTS,
  HONESTY_SOURCES,
  HONESTY_STATUSES,
  SURFACE_CLAIMS,
  SYSTEM_CLAIMS,
} from "@/public/generated/honesty";

/**
 * The honesty table, machine-readable — §E18, §E16.2.
 *
 * > Act 9's honesty table published here **as data, not as prose** (§E16.2).
 *
 * A rendered table is prose however carefully it is built: a reader comparing
 * two releases has to read two HTML pages side by side, and a reader building a
 * tool over it has to scrape. So the same generated rows are served as JSON,
 * from the same module the page renders, which is what makes "published as
 * data" a fact rather than a description of the page's tone.
 *
 * **The vocabulary is in the body.** `statuses` lists the five labels the table
 * can use, so a consumer can tell "this row is ROADMAP" from "this consumer
 * does not know what ROADMAP means" — the same reason the public API publishes
 * `suppression_threshold` beside `suppressed` rather than leaving a caller to
 * infer the floor.
 *
 * No tenant data, no upstream call, no per-caller content — so it is cached the
 * same way §26.4's own responses are, `public` rather than `private`, and for
 * the same stated reason.
 */
export const dynamic = "force-static";

export function GET(): Response {
  const body = {
    // Named so a consumer knows which documents to go and read when a row
    // surprises them. A status claim with no citation is an opinion.
    sources: HONESTY_SOURCES,
    statuses: HONESTY_STATUSES,
    counts: HONESTY_COUNTS,
    /** §44 — the whole system, one status column. */
    system: SYSTEM_CLAIMS,
    /** §E28 — the surfaces, two columns. A row is finished when both read REAL. */
    surfaces: SURFACE_CLAIMS,
  };

  return new Response(JSON.stringify(body, null, 2), {
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "public, max-age=300",
    },
  });
}
