import type { RealtimeEnvelope } from "@/lib/realtime/envelope";
import { notTranslatable, t, type Strings } from "@/lib/i18n/strings";
import "./components.css";

/**
 * `<EvidenceTrail>` — §E26, §E3.1, §E17.4.
 *
 * > The event ledger. Citizen, officer and public views differ **only by row
 * > filtering** — never by different code.
 *
 * That contract is the whole component. §E3.1 says *"a status badge is a claim;
 * an event ledger is evidence"*, and §E17.4 says *"'In Progress' is the enemy;
 * it is the exact failure the README opens with"*. If the three audiences got
 * three implementations, they would drift, and the day they disagreed nobody
 * could say which one was the record.
 *
 * So there is one row renderer and one predicate. `visibleIn` is the *only*
 * place a view is mentioned, and `tests/evidence-trail.test.ts` asserts that
 * every view's output is a subset of the officer's — which is what "differ only
 * by filtering" means when you write it down as something a machine can check.
 *
 * **The entries are envelopes**, the same shape `/ws/pipeline-events` delivers,
 * because a history endpoint replaying the log would return exactly that. No
 * type is invented here (Law 2). The endpoint itself does not exist yet — see
 * the execution plan's defect #18 — so today this renders the live session's
 * events and carries a `<NotWired>` chip where a surface shows history.
 */

export type TrailView = "citizen" | "officer" | "public";

/**
 * Who may see which rows.
 *
 * Data, not branches. A new event type defaults to **officer-only**, which is
 * the safe direction: a row nobody outside the department sees is a gap, and a
 * row a citizen sees that names another citizen is a §22 incident.
 */
const VISIBILITY: Readonly<Record<string, readonly TrailView[]>> = {
  complaint_submitted: ["citizen", "officer", "public"],
  exif_check_completed: ["citizen", "officer"],
  classification_scored: ["citizen", "officer", "public"],
  pipeline_stage_degraded: ["citizen", "officer", "public"],
  media_transcribed: ["citizen", "officer"],
  media_redacted: ["citizen", "officer"],
  safety_trigger_fired: ["citizen", "officer", "public"],
  perceptual_duplicate_detected: ["officer"],
  cluster_match_found: ["citizen", "officer", "public"],
  cluster_created: ["citizen", "officer", "public"],
  complaint_clustered: ["citizen", "officer", "public"],
  cluster_merge_reverted: ["officer"],
  severity_scored: ["citizen", "officer", "public"],
  review_queued: ["officer"],
  review_decided: ["officer"],
  abuse_pattern_flagged: ["officer"],
  work_order_created: ["citizen", "officer", "public"],
  work_order_assigned: ["citizen", "officer", "public"],
  ssim_verification_completed: ["citizen", "officer", "public"],
  citizen_confirmation_requested: ["citizen", "officer"],
  citizen_confirmed: ["citizen", "officer", "public"],
  citizen_disputed: ["citizen", "officer", "public"],
  admin_action: ["officer"],
};

export function visibleIn(envelope: RealtimeEnvelope, view: TrailView): boolean {
  return (VISIBILITY[envelope.event_type] ?? ["officer"]).includes(view);
}

export interface EvidenceTrailProps {
  readonly entries: readonly RealtimeEnvelope[];
  readonly view: TrailView;
  readonly strings: Strings;
  /** Rendered per row where a surface can link to the underlying record. */
  readonly evidenceHref?: (envelope: RealtimeEnvelope) => string | undefined;
}

export function EvidenceTrail({ entries, view, strings, evidenceHref }: EvidenceTrailProps) {
  const rows = entries.filter((entry) => visibleIn(entry, view));

  if (rows.length === 0) {
    return <p className="evidence-trail__empty type-caption">{t(strings, "evidence.empty")}</p>;
  }

  return (
    <ol className="evidence-trail" data-view={view}>
      {rows.map((entry) => {
        const href = evidenceHref?.(entry);
        return (
          <li key={`${entry.entity_id}:${String(entry.sequence)}`} className="evidence-trail__row">
            <time className="evidence-trail__time type-mono-data" dateTime={entry.timestamp}>
              {entry.timestamp}
            </time>
            <span className="evidence-trail__event type-caption">
              {t(strings, `event.${entry.event_type}`)}
            </span>
            {href === undefined ? null : (
              <a className="evidence-trail__evidence type-micro" href={href}>
                {/*
                 * §E19.6: evidence is attached *by reference*, each item with
                 * its own hash and source. You cannot paste a screenshot into a
                 * case; you attach a record. The same rule holds one level down,
                 * on the row that points at one.
                 */}
                {notTranslatable(entry.entity_id.slice(0, 8))}
              </a>
            )}
          </li>
        );
      })}
    </ol>
  );
}
