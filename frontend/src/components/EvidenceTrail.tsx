import type { ComplaintHistoryEvent } from "@/lib/api/complaints";
import type { RealtimeEnvelope } from "@/lib/realtime/envelope";
import { formatLedgerTime } from "@/lib/i18n/datetime";
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
 * place a view is mentioned, and `tests/contracts.test.ts` asserts that every
 * view's output is a subset of the officer's — which is what "differ only by
 * filtering" means when you write it down as something a machine can check.
 *
 * **Two sources, one row shape, and the difference matters.** ADR-0043 landed
 * `GET /complaints/{id}/events`, so this now renders the *log* — every event on
 * the chain, in sequence, each carrying the hash link to the one before it.
 * Before that it could only render the envelopes that happened to arrive while
 * the page was open, which §E17.4 itself calls the enemy: a ledger that starts
 * when you open it is a status badge with extra steps.
 *
 * `TrailEntry` is a **view model**, not a contract — the one kind of local type
 * execution-plan Law 2 permits, and the guard's own comment says so. Neither
 * adapter invents a field: `trailFromHistory` names what the endpoint returns
 * and `trailFromEnvelopes` names what the socket delivers, and where the two
 * disagree the history wins, because §E14.3 says the socket is a hint.
 */

export type TrailView = "citizen" | "officer" | "public";

/**
 * One row, from either source.
 *
 * `chainHash` is present only for rows read from the log. An envelope carries
 * no hash — the stream publishes a cursor, not a link — so a live row renders
 * without one rather than with a placeholder. §E3.3.
 */
export interface TrailEntry {
  readonly key: string;
  readonly eventType: string;
  /** RFC 3339. Business time — when it happened, not when it was written. */
  readonly occurredAt: string;
  readonly sequence: number;
  /** False where the server disclosed the row and withheld its payload
   *  (ADR-0043). Rendered, because "carries no data" and "carries data you are
   *  not being shown" are different facts. */
  readonly payloadDisclosed: boolean;
  readonly chainHash?: string | undefined;
}

/**
 * Who may see which rows.
 *
 * Data, not branches. A new event type defaults to **officer-only**, which is
 * the safe direction: a row nobody outside the department sees is a gap, and a
 * row a citizen sees that names another citizen is a §22 incident.
 *
 * **This is a second filter, on top of the server's.** ADR-0043's disclosure
 * table decides what a *payload* may say; this decides which rows a *surface*
 * shows. They are not redundant and they are not interchangeable: the server's
 * runs where a citizen cannot reach it, and this one runs so an officer's
 * screen and a citizen's screen can be the same code.
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

/**
 * Structural on purpose: anything with an `event_type` can be filtered.
 *
 * Both an envelope and a history row satisfy it, so the predicate — the one
 * place a view is named — never had to learn that a second source existed.
 */
export function visibleIn(entry: { readonly event_type: string }, view: TrailView): boolean {
  return (VISIBILITY[entry.event_type] ?? ["officer"]).includes(view);
}

/** The log, from `GET /complaints/{id}/events` — the authority (ADR-0043). */
export function trailFromHistory(events: readonly ComplaintHistoryEvent[]): readonly TrailEntry[] {
  return events.map((event) => ({
    key: `seq:${String(event.sequence)}`,
    eventType: event.event_type,
    occurredAt: event.occurred_at,
    sequence: event.sequence,
    // Optional in the generated type because the server declares a default of
    // `true`. Defaulting the *other* way here would be worse than useless: it
    // would mark every row as withholding something, and a marker that is
    // always on says nothing.
    payloadDisclosed: event.payload_disclosed ?? true,
    chainHash: event.event_hash,
  }));
}

/**
 * The stream, from `/ws/pipeline-events` — a hint (§E14.3).
 *
 * Used where no history endpoint applies: the `<ContractMatrix>` catalogue, and
 * any future surface watching an entity type whose log is not published. Rows
 * carry no hash, because the envelope has none to carry.
 */
export function trailFromEnvelopes(envelopes: readonly RealtimeEnvelope[]): readonly TrailEntry[] {
  return envelopes.map((envelope) => ({
    key: `${envelope.entity_id}:${String(envelope.sequence)}`,
    eventType: envelope.event_type,
    occurredAt: envelope.timestamp,
    sequence: envelope.sequence,
    // The stream shapes payloads too (ADR-0016), but an envelope does not say
    // whether it withheld anything — so this claims nothing rather than
    // claiming a disclosure it cannot verify.
    payloadDisclosed: true,
  }));
}

export interface EvidenceTrailProps {
  readonly entries: readonly TrailEntry[];
  readonly view: TrailView;
  readonly strings: Strings;
  /** Rendered per row where a surface can link to the underlying record. */
  readonly evidenceHref?: (entry: TrailEntry) => string | undefined;
}

export function EvidenceTrail({ entries, view, strings, evidenceHref }: EvidenceTrailProps) {
  const rows = entries.filter((entry) => visibleIn({ event_type: entry.eventType }, view));

  if (rows.length === 0) {
    return <p className="evidence-trail__empty type-caption">{t(strings, "evidence.empty")}</p>;
  }

  return (
    <ol className="evidence-trail" data-view={view}>
      {rows.map((entry) => {
        const href = evidenceHref?.(entry);
        return (
          <li key={entry.key} className="evidence-trail__row" data-event={entry.eventType}>
            {/*
             * The readable time in the text, the exact one in the attribute.
             * `occurredAt` is the value the hash chain attests to, so it stays
             * on `dateTime` where a crawler, a screen reader and a copy-paste
             * all still reach it — and the visible column becomes something a
             * person reads rather than a wire format they decode.
             */}
            <time className="evidence-trail__time type-mono-data" dateTime={entry.occurredAt}>
              {formatLedgerTime(entry.occurredAt, strings.locale)}
            </time>
            <span className="evidence-trail__event type-caption">
              {t(strings, `event.${entry.eventType}`)}
            </span>
            {entry.payloadDisclosed ? null : (
              /*
               * ADR-0043. The row is disclosed and its payload is not — a
               * distinction the server draws deliberately and this renders
               * rather than flattens. §E3.3: the omission is visible instead of
               * faked, and ADR-0021's rule that a suppressed value is never a
               * blank cell applies one level down, to a suppressed payload.
               */
              <span className="evidence-trail__withheld type-micro">
                {t(strings, "evidence.withheld")}
              </span>
            )}
            {entry.chainHash === undefined ? null : (
              <span
                className="evidence-trail__hash type-mono-data"
                title={entry.chainHash}
                /*
                 * §E17.3: nobody reads the hash; everybody feels that this
                 * system keeps records. Twelve characters is enough to *look*
                 * like a fingerprint and not enough to be mistaken for one you
                 * are meant to compare by eye — the full value is on the title
                 * and on the wire, and `chain_head` is the one to compare.
                 */
              >
                {notTranslatable(entry.chainHash.slice(0, 12))}
              </span>
            )}
            {href === undefined ? null : (
              <a className="evidence-trail__evidence type-micro" href={href}>
                {/*
                 * §E19.6: evidence is attached *by reference*, each item with
                 * its own hash and source. You cannot paste a screenshot into a
                 * case; you attach a record. The same rule holds one level down,
                 * on the row that points at one.
                 */}
                {t(strings, "evidence.open")}
              </a>
            )}
          </li>
        );
      })}
    </ol>
  );
}
