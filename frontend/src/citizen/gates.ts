import type { ComplaintHistoryEvent } from "@/lib/api/complaints";

/**
 * §E16.1's six gates, as a pure function of a complaint's own history.
 *
 * The table in §E16.1 is a table of *events*, not of screens:
 *
 * | Gate | Stamp | Backing event |
 * |---|---|---|
 * | Safety | `NO HAZARD TRIGGER` / `SAFETY OVERRIDE · BYPASSING QUEUE` | `safety_trigger_fired` |
 * | Trust | `EXIF INTACT · DEVICE NOT ON WATCHLIST` | `exif_check_completed` |
 * | Perception | `POTHOLE 0.91` **and the runner-up** | `classification_scored` |
 * | Perception — degraded | `CLASSIFIER UNAVAILABLE · PARKED FOR HUMAN REVIEW` | `pipeline_stage_degraded` |
 * | Redaction | a face visibly blurs *on the photograph itself* | `media_redacted` |
 * | Dedup | `3 NEARBY REPORTS · MATCH 0.87` | `cluster_match_found` |
 *
 * **This reads the ledger, not the stream.** §E14.3: the socket is a hint and
 * *"nothing renders a fact from it"*. A stamp that says EXIF INTACT is a fact
 * the citizen is being asked to believe, so it comes from
 * `GET /complaints/{id}/events` — the same append-only log an auditor would
 * read, shaped by `nemesis/events/disclosure.py`. The stream's job is to make
 * the ledger refetch quickly, which is exactly what §E14.3 says its job is.
 *
 * Keeping it a pure function is what makes the Phase 20 gate testable: *"every
 * scene is triggered by a genuine backend event — a scene that can only be
 * fired by a button fails."* Feed it real rows, assert the stamps.
 */

/** The five gates a citizen watches, in pipeline order. Perception's degraded
 *  path is a **third outcome** of the perception gate, not a sixth gate — §24.2
 *  is explicit that it does not stall and does not guess. */
export const GATE_ORDER = ["safety", "trust", "perception", "redaction", "dedup"] as const;

export type GateName = (typeof GATE_ORDER)[number];

export type GateState =
  /** No event yet. The card is on the table and nothing has been stamped. */
  | "waiting"
  /** The normal outcome. */
  | "passed"
  /** The abnormal outcome that is still an outcome: a safety override, a
   *  parked classification, a duplicate match. Never an error. */
  | "flagged"
  /** §24.2's third outcome — the stage took its fallback and said so. */
  | "degraded";

export interface Gate {
  readonly name: GateName;
  readonly state: GateState;
  /** The locale key for the stamp. Never the words — §E10.1. */
  readonly stampKey: string;
  /** Interpolations for the stamp, already formatted. */
  readonly vars?: Readonly<Record<string, string | number>>;
}

type Payload = Readonly<Record<string, unknown>>;

function payloadOf(event: ComplaintHistoryEvent | undefined): Payload {
  return event?.payload ?? {};
}

function findLast(
  events: readonly ComplaintHistoryEvent[],
  eventType: string,
): ComplaintHistoryEvent | undefined {
  // Last rather than first: a stage that retried after a degradation appends
  // twice, and the citizen is owed the outcome that stands, not the first one
  // attempted.
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (event?.event_type === eventType) return event;
  }
  return undefined;
}

function number(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** Two decimals, matching §E16.1's `POTHOLE 0.91` and `MATCH 0.87`. */
function confidence(value: unknown): string | null {
  const parsed = number(value);
  return parsed === null ? null : parsed.toFixed(2);
}

/**
 * The safety gate.
 *
 * `safety_trigger_fired` is the *abnormal* branch, so its absence is the pass —
 * but absence alone cannot distinguish "checked and clear" from "not checked
 * yet". The trust stage runs the safety check and the EXIF check in the same
 * pass, so `exif_check_completed` is the evidence that the pass happened. That
 * coupling is stated here rather than assumed, because if the pipeline ever
 * splits them this gate would silently start claiming a clean result before
 * anything had looked.
 */
function safetyGate(events: readonly ComplaintHistoryEvent[]): Gate {
  const fired = findLast(events, "safety_trigger_fired");
  if (fired !== undefined) {
    return { name: "safety", state: "flagged", stampKey: "gate.safety.override" };
  }
  const checked = findLast(events, "exif_check_completed") !== undefined;
  return checked
    ? { name: "safety", state: "passed", stampKey: "gate.safety.clear" }
    : { name: "safety", state: "waiting", stampKey: "gate.safety.waiting" };
}

/**
 * The trust gate — §E16.1's `EXIF INTACT · DEVICE NOT ON WATCHLIST`.
 *
 * Two halves and only one is disclosed. `exif_present` comes off the event
 * (ADR-0045). The watchlist half is `abuse_pattern_flagged`, whose payload is
 * withheld from a citizen entirely (ADR-0043) — so the stamp says the EXIF
 * finding and says nothing about the device, rather than claiming a clearance
 * this surface cannot verify.
 *
 * Absent metadata is **not a failure**. §11.1 is explicit: share flows strip it
 * by default, so absence is weak evidence about the submitter and strong
 * evidence about the app they used. It stamps its own outcome and the card
 * continues.
 */
function trustGate(events: readonly ComplaintHistoryEvent[]): Gate {
  const event = findLast(events, "exif_check_completed");
  if (event === undefined) {
    return { name: "trust", state: "waiting", stampKey: "gate.trust.waiting" };
  }
  const present = payloadOf(event)["exif_present"] === true;
  return present
    ? { name: "trust", state: "passed", stampKey: "gate.trust.intact" }
    : { name: "trust", state: "flagged", stampKey: "gate.trust.stripped" };
}

/**
 * The perception gate, with §24.2's third outcome.
 *
 * The degraded branch is checked **first**, because a report that was parked
 * for a human may still carry an earlier, superseded classification, and
 * showing a confident category above *CLASSIFIER UNAVAILABLE* would be the
 * exact confusion §24.2 exists to prevent.
 *
 * §E16.1 asks for the runner-up alongside the winner — *"showing the runner-up
 * and a confidence figure is more persuasive than showing certainty"* — and
 * `alternatives` is **not** disclosed to a citizen (ADR-0043: it is Phase 11
 * active-learning material). So the stamp shows the winner and its confidence,
 * the runner-up is absent, and that gap is recorded against §E16.1 rather than
 * filled with a second-best guess.
 */
function perceptionGate(events: readonly ComplaintHistoryEvent[]): Gate {
  const degraded = findLast(events, "pipeline_stage_degraded");
  if (degraded !== undefined) {
    const stage = payloadOf(degraded)["stage"];
    return {
      name: "perception",
      state: "degraded",
      stampKey: "gate.perception.degraded",
      vars: { stage: typeof stage === "string" ? stage : "" },
    };
  }

  const scored = findLast(events, "classification_scored");
  if (scored === undefined) {
    return { name: "perception", state: "waiting", stampKey: "gate.perception.waiting" };
  }

  const payload = payloadOf(scored);
  const category = payload["category"];
  const score = confidence(payload["confidence"]);
  if (typeof category !== "string" || score === null) {
    // Scored, and the disclosure shaped away what the stamp needs. Say the
    // stage happened; do not invent the number it happened to produce.
    return { name: "perception", state: "passed", stampKey: "gate.perception.scoredOnly" };
  }
  return {
    name: "perception",
    state: "passed",
    stampKey: "gate.perception.scored",
    vars: { category, confidence: score },
  };
}

/**
 * The redaction gate — §22.1, and the trust moment §E16.1 calls out.
 *
 * > Showing a citizen's face disappear from their own evidence is a trust
 * > moment no competitor stages, and it is a real pipeline stage, not an
 * > illustration of one.
 *
 * Three outcomes, because §22.1's promise is about *every* face and the two
 * counts are kept apart precisely so failing it is visible (ADR-0045):
 * nothing to blur, all blurred, and — the one a single boolean could not
 * express — some blurred.
 */
function redactionGate(events: readonly ComplaintHistoryEvent[]): Gate {
  const event = findLast(events, "media_redacted");
  if (event === undefined) {
    return { name: "redaction", state: "waiting", stampKey: "gate.redaction.waiting" };
  }
  const payload = payloadOf(event);
  const detected = number(payload["faces_detected"]) ?? 0;
  const blurred = number(payload["faces_blurred"]) ?? 0;

  if (detected === 0) {
    return { name: "redaction", state: "passed", stampKey: "gate.redaction.noFaces" };
  }
  if (blurred < detected) {
    return {
      name: "redaction",
      state: "flagged",
      stampKey: "gate.redaction.partial",
      vars: { blurred, detected },
    };
  }
  return {
    name: "redaction",
    state: "passed",
    stampKey: "gate.redaction.blurred",
    vars: { count: blurred },
  };
}

/**
 * The dedup gate — §E16.1's `3 NEARBY REPORTS · MATCH 0.87`.
 *
 * Driven by `complaint_clustered` on the complaint's own chain rather than by
 * `cluster_match_found` on the cluster's, and the distinction matters: only the
 * first is in *this* report's history, which is the authority this surface
 * reads. The cluster event reaches the browser on the stream and is a hint.
 *
 * `outcome` is carried on the event precisely because a cluster of one has
 * three different causes — nothing nearby, something nearby that scored too
 * low, and something nearby too ambiguous to separate — and a reader who cannot
 * tell them apart reads the third as the first.
 */
function dedupGate(events: readonly ComplaintHistoryEvent[]): Gate {
  const event = findLast(events, "complaint_clustered");
  if (event === undefined) {
    return { name: "dedup", state: "waiting", stampKey: "gate.dedup.waiting" };
  }
  const payload = payloadOf(event);
  const outcome = payload["outcome"];
  const score = confidence(payload["combined_confidence"]);

  if (outcome === "merge") {
    return score === null
      ? { name: "dedup", state: "flagged", stampKey: "gate.dedup.mergedNoScore" }
      : {
          name: "dedup",
          state: "flagged",
          stampKey: "gate.dedup.merged",
          vars: { confidence: score },
        };
  }
  if (outcome === "investigate") {
    return { name: "dedup", state: "flagged", stampKey: "gate.dedup.investigate" };
  }
  return { name: "dedup", state: "passed", stampKey: "gate.dedup.distinct" };
}

/**
 * Fallback values that mean **the pipeline stopped here** — from
 * `DegradationFallback` in `nemesis/domain/lifecycle.py`.
 *
 * `skipped_stage` is deliberately absent: an EXIF checker that was unavailable
 * reduces information and the pipeline carries on, which is §11.1's own
 * position on absent metadata. The other two are terminal until a person acts.
 */
const HALTING_FALLBACKS: ReadonlySet<string> = new Set([
  "pending_classification",
  "halted_for_review",
]);

/**
 * Has the pipeline stopped for this report, short of finishing?
 *
 * **This is the defect this function exists to fix, and it was found by running
 * the real thing.** A report whose classification abstains is parked at
 * `pending_classification` and the stages after it never run — so the dedup gate
 * sat at *"not compared yet"* forever, the theatre polled every 1.2 s
 * indefinitely, and the citizen was left watching two rows that would never
 * change. §24.2's rule is that the card **continues**; §E17.2's is that the wait
 * is legible. An unreachable gate that pretends to still be waiting is neither.
 *
 * Two signals, both on the complaint's own chain:
 *
 * - `safety_trigger_fired` — §11.2's deterministic fail-safe *"bypasses the
 *   queue entirely"*, so nothing downstream of it runs at all.
 * - `pipeline_stage_degraded` carrying a halting fallback — §24.2's park.
 */
function pipelineHalted(events: readonly ComplaintHistoryEvent[]): boolean {
  if (findLast(events, "safety_trigger_fired") !== undefined) return true;
  const degraded = findLast(events, "pipeline_stage_degraded");
  if (degraded === undefined) return false;
  const fallback = payloadOf(degraded)["fallback_taken"];
  return typeof fallback === "string" && HALTING_FALLBACKS.has(fallback);
}

/**
 * Every gate, in pipeline order, from one complaint's disclosed history.
 *
 * When the pipeline has stopped, a gate that never received its event does not
 * keep waiting — it reports that it is **held**, which is what actually
 * happened. That is §E3.3 applied to a gap in time rather than a gap in data:
 * *"we have not looked yet"* and *"nobody will look until a person unblocks
 * this"* are different sentences, and only one of them is true here.
 */
export function readGates(events: readonly ComplaintHistoryEvent[]): readonly Gate[] {
  const gates = [
    safetyGate(events),
    trustGate(events),
    perceptionGate(events),
    redactionGate(events),
    dedupGate(events),
  ];

  if (!pipelineHalted(events)) return gates;

  return gates.map((gate) =>
    gate.state === "waiting"
      ? { name: gate.name, state: "degraded" as const, stampKey: "gate.held" }
      : gate,
  );
}

/** True once no gate is still waiting — which is when the theatre stops
 *  polling faster than §27.3's ordinary interval. */
export function allGatesSettled(gates: readonly Gate[]): boolean {
  return gates.every((gate) => gate.state !== "waiting");
}
