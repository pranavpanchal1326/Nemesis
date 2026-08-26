import type { components } from "@/generated/api";
import { notTranslatable, t, type Strings } from "@/lib/i18n/strings";

import { ConsolePrint } from "../ConsoleShell";
import { ContractGap, FixtureNotice } from "./Fixture";
import "./roadmap.css";

/**
 * §E19.5 — money. **ROADMAP (Phase 14, 23).**
 *
 * > A **"what citizens see"** toggle renders the same figures with suppression
 * > applied. Knowing that your internal number and your public number are the
 * > same number is the entire point of §16.2.
 *
 * **The toggle is the feature.** Everything else on this screen is a table of
 * money, which every municipal system already has. What no municipal system has
 * is a control that shows an officer, at their desk, exactly what a resident
 * sees of the same figure — and the value of that is not transparency theatre:
 * it is that an officer who has *looked* at the public view stops being
 * surprised by it, and a department that is never surprised by its own public
 * page stops trying to manage it.
 *
 * **`BudgetSummaryResponse` and `BudgetLineResponse` are published today.** The
 * public budget page renders them, so the shape here is real and only the
 * numbers are fixtures. What has no contract is the *slice* — by scheme, by
 * department, by contractor — and it is named rather than drawn.
 *
 * The toggle is rendered here as two rendered states side by side rather than
 * as an interactive control, and that is deliberate for a fixture screen: an
 * interactive toggle over invented numbers would be a demonstration of the
 * widget rather than of the idea. Phase 14 makes it a control.
 */

type BudgetSummary = components["schemas"]["BudgetSummaryResponse"];
type BudgetLine = components["schemas"]["BudgetLineResponse"];

/** Fixture values in the published shape. `satisfies` so a renamed field fails
 *  to compile on the next `nem web-types`. */
const INTERNAL = {
  currency: "INR",
  fiscal_year: "2026-27",
  zone_code: "W22",
  allocations: [
    {
      funding_source: "AMRUT",
      allocated_amount: "12000000.00",
      spent_amount: "11800000.00",
      utilisation_rate: 0.983,
    },
    {
      funding_source: "Ward fund",
      allocated_amount: "8400000.00",
      spent_amount: "2100000.00",
      utilisation_rate: 0.25,
    },
  ] satisfies BudgetLine[],
} satisfies Partial<BudgetSummary>;

/** The wire carries decimal *strings* — `allocated_amount: "12000000.00"` —
 *  because a rupee figure that has been through a float is a rupee figure
 *  somebody will eventually have to reconcile. Parsed once, here, at the edge
 *  of the render and never stored back. */
function amount(value: string): number {
  return Number(value);
}

export function Money({ strings, locale }: { readonly strings: Strings; readonly locale: string }) {
  const money = new Intl.NumberFormat(locale, {
    style: "currency",
    currency: INTERNAL.currency,
    maximumFractionDigits: 0,
  });
  // The exact decimal is the record and the formatted figure is presentation —
  // the same split `datetime.ts` makes between the RFC 3339 attribute and the
  // readable text.
  const rupees = (value: string) => notTranslatable(money.format(amount(value)));

  /** Summed across funding sources. A total the server does not publish is
   *  computed here rather than invented as a field on the fixture — the shape
   *  stays the contract's. */
  const total = (field: "allocated_amount" | "spent_amount"): string =>
    String(INTERNAL.allocations.reduce((sum, line) => sum + amount(line[field]), 0));

  return (
    <div className="roadmap">
      <FixtureNotice phase="14, 23" strings={strings} />

      <ConsolePrint title={t(strings, "money.allocated")}>
        <dl className="roadmap__facts">
          <div>
            <dt className="type-micro">{t(strings, "money.allocated")}</dt>
            <dd className="type-mono-data">{rupees(total("allocated_amount"))}</dd>
          </div>
          <div>
            <dt className="type-micro">{t(strings, "money.spent")}</dt>
            <dd className="type-mono-data">{rupees(total("spent_amount"))}</dd>
          </div>
          <div>
            <dt className="type-micro">{t(strings, "money.variance")}</dt>
            <dd className="type-mono-data">
              {notTranslatable(
                money.format(amount(total("allocated_amount")) - amount(total("spent_amount"))),
              )}
            </dd>
          </div>
        </dl>
      </ConsolePrint>

      <ConsolePrint title={t(strings, "money.publicToggle")}>
        <p className="roadmap__why type-caption">{t(strings, "money.publicToggle.why")}</p>
        <table className="roadmap__table">
          <thead>
            <tr>
              <th scope="col" className="type-micro">
                {t(strings, "money.slice.scheme")}
              </th>
              <th scope="col" className="type-micro">
                {t(strings, "money.allocated")}
              </th>
              <th scope="col" className="type-micro">
                {t(strings, "money.spent")}
              </th>
            </tr>
          </thead>
          <tbody>
            {INTERNAL.allocations.map((line) => (
              <tr key={line.funding_source}>
                <td className="type-caption">{notTranslatable(line.funding_source)}</td>
                <td className="type-mono-data">{rupees(line.allocated_amount)}</td>
                <td className="type-mono-data">{rupees(line.spent_amount)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <ContractGap what={t(strings, "gap.money")} strings={strings} />
      </ConsolePrint>
    </div>
  );
}
