import type { components } from "@/generated/api";
import { t, type Strings } from "@/lib/i18n/strings";

import { ConsolePrint } from "../ConsoleShell";
import { ContractGap, FixtureNotice } from "./Fixture";
import "./roadmap.css";

/**
 * §E19.2 — the ward room. **ROADMAP (Phase 12, 23).**
 *
 * > Plus the panel nobody else builds, implementing §23.2:
 * >
 * > **⚠ Underreporting signal.** Ward 22 files 0.19 reports per road-km per
 * > month against a city median of 0.94 … **This is likely under-reporting, not
 * > good roads.**
 *
 * That panel is the reason this screen is worth building before its data
 * exists. §23.1 records reporting bias as a *documented concern*; §23.2 turns
 * it into an operational recommendation, and the difference between those two
 * things is entirely a matter of whether somebody built the panel.
 *
 * **What is real here.** `ZoneSummaryResponse` and its `CategoryCountResponse`
 * and `SeverityBreakdown` members are published today — the public surface
 * renders them. So the mix, the open/closed split and the severity breakdown
 * are the genuine shape with fixture numbers.
 *
 * **What has no contract.** Reports per road-kilometre, the city median, the
 * repeat-defect geometry and the time series behind "over time". None of those
 * is a field with a null in it; none of them exists. They are named by
 * `<ContractGap>` and not drawn, because drawing an invented shape is the one
 * thing execution-plan Law 2 forbids outright.
 */

/** Aliases of generated types. A name for the contract, never a redeclaration
 *  of it (`check-guards.ts`' fourth ban, and its own comment on aliases). */
type ZoneSummary = components["schemas"]["ZoneSummaryResponse"];
type CategoryCount = components["schemas"]["CategoryCountResponse"];

/**
 * Fixture *values* in the published *shape*.
 *
 * `satisfies` rather than a type annotation, deliberately: the object is
 * checked against the contract and keeps its literal types, so a field renamed
 * upstream fails to compile here on the next `nem web-types` — which is the
 * whole reason a fixture is written against a generated type instead of beside
 * one.
 */
const WARD = {
  zone_code: "W22",
  zone_name: "Ward 22",
  zone_kind: "ward",
  centroid: { lat: 18.5204, lng: 73.8567 },
  total_reports: 41,
  open_reports: 17,
  resolved_reports: 24,
  suppressed: false,
  suppression_threshold: 5,
  by_category: [
    { category: "road.pothole", category_name: "Potholes", count: 22 },
    { category: "water.leak", category_name: "Water leaks", count: 11 },
    { category: "waste.dump", category_name: "Illegal dumping", count: 8 },
  ] satisfies CategoryCount[],
} satisfies Partial<ZoneSummary>;

export function AreaView({ strings }: { readonly strings: Strings; readonly locale: string }) {
  return (
    <div className="roadmap">
      <FixtureNotice phase="12, 23" strings={strings} />

      <ConsolePrint title={t(strings, "area.mix")}>
        <table className="roadmap__table">
          <thead>
            <tr>
              <th scope="col" className="type-micro">
                {t(strings, "area.mix")}
              </th>
              <th scope="col" className="type-micro">
                {t(strings, "queue.count.other", { count: WARD.total_reports })}
              </th>
            </tr>
          </thead>
          <tbody>
            {WARD.by_category.map((row) => (
              <tr key={row.category}>
                <td className="type-caption">{row.category_name}</td>
                <td className="type-mono-data">{String(row.count)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <ContractGap what={t(strings, "gap.timeSeries")} strings={strings} />
      </ConsolePrint>

      <ConsolePrint title={t(strings, "area.openClosed")}>
        <dl className="roadmap__facts">
          <div>
            <dt className="type-micro">{t(strings, "item.open")}</dt>
            <dd className="type-mono-data">{String(WARD.open_reports)}</dd>
          </div>
          <div>
            <dt className="type-micro">{t(strings, "severity.resolved")}</dt>
            <dd className="type-mono-data">{String(WARD.resolved_reports)}</dd>
          </div>
        </dl>
      </ConsolePrint>

      {/*
       * §23.2, and the panel this screen exists for.
       *
       * It is a `<section>` with a heading rather than an alert, and it is not
       * painted in severity ink: §E9.4 rule 1 reserves that ink for incidents,
       * and a ward that is under-reporting is not an incident — it is a finding
       * about the *measurement*, which is a different and quieter claim.
       */}
      <ConsolePrint title={t(strings, "area.underreporting")}>
        <p className="type-body">
          {t(strings, "area.underreporting.body", { rate: "0.19", median: "0.94" })}
        </p>
        <p className="type-caption">{t(strings, "area.underreporting.suggested")}</p>
        <p className="roadmap__why type-caption">{t(strings, "area.underreporting.why")}</p>
        <ContractGap what={t(strings, "gap.underreporting")} strings={strings} />
      </ConsolePrint>

      <ConsolePrint title={t(strings, "area.repeat")}>
        <ContractGap what={t(strings, "gap.repeatDefect")} strings={strings} />
      </ConsolePrint>
    </div>
  );
}
