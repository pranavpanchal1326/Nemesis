import { plural, t, type Strings } from "@/lib/i18n/strings";
import { NotWired } from "./NotWired";
import { SuppressionNotice } from "./SuppressionNotice";
import "./components.css";

/**
 * `<ContractorLedger>` — §E26, §E18, §16.1.
 *
 * > Four metrics. **Cannot be collapsed to one score** — no single-value
 * > variant exists in the API.
 *
 * §16.1's rule is that a contractor profile is a **ledger, never a rating**:
 * four independent metrics anyone can argue with, each tracing to records. The
 * blueprint's enforcement mechanism is unusual and worth stating plainly — the
 * component cannot collapse them because *the API does not publish a
 * collapsible number*. `tests/contractor-ledger.test.ts` asserts that against
 * the generated schema, so the day somebody adds a `rating` field the test
 * fails and the argument happens before the field ships rather than after.
 *
 * **Two of the four are backed today.** `on_time_rate` and the
 * confirmed/disputed pair are on `ContractorProfileResponse`. Cost variance
 * needs Phase 14's budget ledger and repeat-defect rate needs Phase 17's
 * integrity signals, so those two render their `<NotWired>` chip and a null —
 * not a zero, and not a hidden row. A ledger that quietly drops the metrics it
 * does not have yet is a ledger that flatters.
 */
export interface ContractorMetrics {
  /** Within-SLA rate, 0–1. */
  readonly onTimeRate: number | null;
  /** Cost against the rate card, as a proportion. Phase 14. */
  readonly costVariance: number | null;
  readonly confirmedCount: number | null;
  readonly disputedCount: number | null;
  /** Same defect again within 90 days of closure. Phase 17. */
  readonly repeatDefectRate: number | null;
}

export function ContractorLedger({
  metrics,
  strings,
  suppressed = false,
  suppressionThreshold,
}: {
  readonly metrics: ContractorMetrics;
  readonly strings: Strings;
  readonly suppressed?: boolean;
  readonly suppressionThreshold?: number;
}) {
  if (suppressed && suppressionThreshold !== undefined) {
    return <SuppressionNotice threshold={suppressionThreshold} strings={strings} explain />;
  }

  return (
    // The note sits outside the list, not inside it. `<dl>` admits only
    // `<dt>`, `<dd>` and grouping `<div>`s, and axe was right to say so — an
    // invalid list is a list a screen reader reads wrongly, which on a
    // contractor's public record is not a cosmetic problem.
    <div className="contractor-ledger">
      <dl className="contractor-ledger__metrics">
        <Metric
          label={t(strings, "ledger.withinSla")}
          value={metrics.onTimeRate === null ? null : percent(metrics.onTimeRate)}
          strings={strings}
        />
        <Metric
          label={t(strings, "ledger.costVariance")}
          value={metrics.costVariance === null ? null : percent(metrics.costVariance)}
          phase="Phase 14"
          strings={strings}
        />
        <Metric
          label={t(strings, "ledger.confirmedDisputed")}
          value={
            metrics.confirmedCount === null || metrics.disputedCount === null
              ? null
              : `${String(metrics.confirmedCount)} / ${String(metrics.disputedCount)}`
          }
          // §21.2's citizen confirmation is what makes this pair mean anything,
          // and no public endpoint publishes a *confirmed* count today —
          // `work_orders_completed` includes closures nobody confirmed. Passing
          // that as "confirmed by reporters" would be the flattering ledger
          // §16.1 refuses, so the row renders null behind its chip until the
          // count exists.
          phase="Phase 15"
          strings={strings}
        />
        <Metric
          label={t(strings, "ledger.repeatDefect")}
          value={metrics.repeatDefectRate === null ? null : percent(metrics.repeatDefectRate)}
          phase="Phase 17"
          strings={strings}
        />
      </dl>
      <p className="contractor-ledger__note type-caption">{t(strings, "ledger.noSingleScore")}</p>
    </div>
  );
}

function Metric({
  label,
  value,
  phase,
  strings,
}: {
  readonly label: string;
  readonly value: string | null;
  readonly phase?: string;
  readonly strings: Strings;
}) {
  return (
    <div className="contractor-ledger__metric">
      <dt className="type-caption">{label}</dt>
      <dd className="type-mono-data">
        {value ?? <span className="contractor-ledger__absent">{t(strings, "common.unknown")}</span>}
        {value === null && phase !== undefined ? (
          <NotWired phase={phase} strings={strings} />
        ) : null}
      </dd>
    </div>
  );
}

/** Money is not urgency (§E9.4 rule 4), so nothing here reaches for severity. */
function percent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

/** Exported so a caller can count reports without inventing its own plural. */
export function reportCount(strings: Strings, count: number): string {
  return plural(strings, "suppression.withheld", count, { threshold: count });
}
