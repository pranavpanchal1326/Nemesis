/**
 * Which of §E9.4's five rows a score falls in.
 *
 * **The thresholds are the frontend's and that is a defect it is honest about.**
 * §13.1 makes the bands governed data — they live in the severity rubric
 * alongside the weights — and no endpoint publishes them. Until one does, this
 * is a display convenience and it must never be treated as the system's
 * judgement: the *score* is authoritative and is rendered beside the badge
 * wherever there is room for it, so a reader sees the number the server
 * produced rather than only the word this function chose for it.
 *
 * **Why it lives here rather than beside the panel that first needed it.**
 * It was private to `citizen/SeverityWhy.tsx` until the clay layer needed the
 * same banding to pick a glaze. Two copies of a rule this repository has
 * already called a defect is how a defect becomes two defects that disagree —
 * and §E9.4 rule 3 is explicit that the severity a badge shows and the glaze a
 * shader fires must be the same decision, not merely the same colour.
 *
 * The band edges are on the 0–100 scale `ComplaintRead.severity_score` uses.
 * `severity_scored`'s event payload is on 0–10 (`catalog.py`), so a caller
 * holding an event score converts before calling — `levelForEventScore()` does
 * exactly that, and exists so the factor of ten is written down once.
 */

import type { SeverityLevel } from "@/design/generated/tokens";

/** The band edges, ascending, each paired with the row it opens. */
export const SEVERITY_BANDS = [
  { atLeast: 80, level: "critical" },
  { atLeast: 60, level: "high" },
  { atLeast: 35, level: "medium" },
  { atLeast: 0, level: "low" },
] as const satisfies readonly { atLeast: number; level: SeverityLevel }[];

/** `severity_score` on a complaint read: 0–100. */
export function levelFor(score: number): SeverityLevel {
  for (const band of SEVERITY_BANDS) {
    if (score >= band.atLeast) return band.level;
  }
  return "low";
}

/**
 * The factor between the two scales the system uses for one number.
 *
 * `SeverityScoredV1.score` is `ge=0.0, le=10.0`; `ComplaintRead.severity_score`
 * is 0–100. That is a backend inconsistency rather than a frontend one, and
 * this constant is where the frontend agrees to know about it exactly once.
 */
export const EVENT_SCORE_SCALE = 10;

/** `severity_scored.score` off the event stream: 0–10 (`catalog.py`). */
export function levelForEventScore(score: number): SeverityLevel {
  return levelFor(score * EVENT_SCORE_SCALE);
}

/** The 0–1 position of a score inside the whole scale, for anything that ramps
 *  continuously — a pin's height, say. Clamped, because a rubric change that
 *  scores 104 must produce a tall pin rather than one through the ceiling. */
export function severityFraction(score: number): number {
  return Math.min(1, Math.max(0, score / 100));
}
