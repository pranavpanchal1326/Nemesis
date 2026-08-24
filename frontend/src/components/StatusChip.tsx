import type { ComplaintStatus, WorkOrderStatus } from "@/generated/enums";
import { t, type Strings } from "@/lib/i18n/strings";
import "./components.css";

/**
 * The status vocabulary, rendered — §E26.1. Corrects §E2 defects #15 and #16.
 *
 * > These enums are defined in `backend/nemesis/domain/lifecycle.py` and are the
 * > **complete** set. A status chip rendering a value not on these lists, or a
 * > view omitting one, is a defect — including the states that only occur when
 * > something has gone wrong, which are the ones a UI is most likely to forget
 * > and most needs to show.
 *
 * The union comes from the published schema, so the compiler enforces the
 * completeness the blueprint asks for: delete a case below and `tsc` fails,
 * because `@typescript-eslint/switch-exhaustiveness-check` and the return type
 * together leave nowhere for a missing member to hide.
 *
 * **Two statuses get their own treatment**, and both for the same reason —
 * they mean *the system did not do the normal thing*:
 *
 * `pending_classification` — the classifier was unavailable, so the pipeline
 * parked the report for a human rather than guessing a category that would be
 * indistinguishable downstream from a confident one (§24.2). It is rendered as
 * a **third outcome**, not as a stalled second one, and it carries its reason
 * inline. *"We didn't guess"* reads as competence; a fabricated 0.6 confidence
 * reads as noise.
 *
 * `flagged` — routed out of the normal path by trust and safety. It renders in
 * the **fluorescent hatch**, never in a severity colour (ADR-0039), because an
 * unproven anomaly in urgent red is a §22.2 defamation exposure with a design
 * cause.
 */

/** How a status should read, independent of which surface is showing it. */
type Register =
  /** Moving through the pipeline as intended. */
  | "in-flight"
  /** A terminal state somebody agreed to. */
  | "settled"
  /** The citizen rejected a closure — the product's whole thesis. */
  | "contested"
  /** The system took a fallback path and is saying so. */
  | "degraded"
  /** Trust and safety. Hatched, never coloured. */
  | "flagged";

export function complaintRegister(status: ComplaintStatus): Register {
  switch (status) {
    case "submitted":
    case "verifying":
    case "classified":
    case "clustered":
    case "scored":
    case "routed":
    case "in_progress":
    case "pending_verification":
      return "in-flight";
    case "resolved":
    case "closed":
      return "settled";
    case "disputed":
      return "contested";
    case "pending_classification":
      return "degraded";
    case "flagged":
      return "flagged";
  }
}

export function workOrderRegister(status: WorkOrderStatus): Register {
  switch (status) {
    // `created` is the unassigned backlog — the column §15.7 omits and the one
    // §E19.1's breach strip is counting.
    case "created":
    case "assigned":
    case "in_progress":
    case "pending_verification":
      return "in-flight";
    case "closed":
      return "settled";
    case "disputed":
      return "contested";
  }
}

export interface StatusChipProps {
  readonly status: ComplaintStatus;
  readonly strings: Strings;
  /** Render the explanatory line the two abnormal states carry. */
  readonly explain?: boolean;
}

export function StatusChip({ status, strings, explain = false }: StatusChipProps) {
  const register = complaintRegister(status);
  const detail =
    register === "degraded" || register === "flagged" ? `status.${status}.detail` : null;

  return (
    <span className="status-chip" data-register={register} data-status={status}>
      <span className="status-chip__label">{t(strings, `status.${status}`)}</span>
      {explain && detail !== null ? (
        <span className="status-chip__detail type-caption">{t(strings, detail)}</span>
      ) : null}
    </span>
  );
}

export function WorkOrderChip({
  status,
  strings,
}: {
  readonly status: WorkOrderStatus;
  readonly strings: Strings;
}) {
  return (
    <span className="status-chip" data-register={workOrderRegister(status)} data-status={status}>
      <span className="status-chip__label">{t(strings, `workOrder.${status}`)}</span>
    </span>
  );
}
