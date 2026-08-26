import { MILESTONE_STAGES, WORK_ORDER_STATUSES } from "@/generated/enums";
import { notTranslatable, t, type Strings } from "@/lib/i18n/strings";

import { ConsolePrint } from "../ConsoleShell";
import { ContractGap, FixtureNotice } from "./Fixture";
import "./roadmap.css";

/**
 * §E19.3 — assignment made fair. **ROADMAP (Phase 14).**
 *
 * > **Contractor selection is a transparency feature, not a dropdown** (§15.3)
 * > … and beneath the list sits the line that does the work —
 * >
 * > ⚠ Shirke has received 61% of road work in Ward 14 this quarter.
 * >
 * > It blocks nothing. It makes the pattern impossible to not-know at the
 * > moment of the decision, and it is logged that the assigner saw it.
 *
 * The concentration warning is the whole point of building this screen early.
 * A dropdown is five minutes' work at any time; a picker that puts the
 * distribution of awards in front of the person making the award is a design
 * decision that has to survive the moment somebody asks for "just a dropdown
 * for now".
 *
 * **What is real.** `WorkOrderStatus`, `MilestoneStage` and `AssigneeType` are
 * published enums — closed sets the backend already serves — so the status
 * chips and the milestone strip are rendered from `generated/enums.ts` rather
 * than from a list written here. Add a status upstream and it appears.
 *
 * **What has no contract.** There is no work-order response schema, no rate
 * card, and no award-concentration figure. The contractor rows below are
 * fixtures with no generated container to sit in, so they are drawn as an
 * explicit fixture table under the chip and named as a gap — an invented
 * `WorkOrderResponse` interface would fail `check-guards.ts`, and correctly.
 */

/** The picker's rows. §15.3's three columns, and the reason they are columns
 *  rather than a summary: a within-SLA rate beside a cost variance beside a
 *  workload is a comparison; the same three facts in prose is an argument. */
const CANDIDATES = [
  { name: "Shirke Infra", withinSla: "92%", variance: "+4%", workload: 11 },
  { name: "Kalyani Roadworks", withinSla: "88%", variance: "−2%", workload: 4 },
  { name: "Deshmukh & Sons", withinSla: "71%", variance: "+18%", workload: 2 },
] as const;

export function WorkOrder({ strings }: { readonly strings: Strings; readonly locale: string }) {
  return (
    <div className="roadmap">
      <FixtureNotice phase="14" strings={strings} />

      <ConsolePrint title={t(strings, "work.status")}>
        {/* The closed set, from the generated enum. Not a list typed here. */}
        <ul className="roadmap__chips">
          {WORK_ORDER_STATUSES.map((status) => (
            <li key={status} className="roadmap__chip type-mono-data">
              {notTranslatable(status)}
            </li>
          ))}
        </ul>
        <ContractGap what={t(strings, "gap.workOrder")} strings={strings} />
      </ConsolePrint>

      <ConsolePrint title={t(strings, "work.contractor.pick")}>
        <table className="roadmap__table">
          <thead>
            <tr>
              <th scope="col" className="type-micro">
                {t(strings, "work.contractor")}
              </th>
              <th scope="col" className="type-micro">
                {t(strings, "work.contractor.within")}
              </th>
              <th scope="col" className="type-micro">
                {t(strings, "work.contractor.variance")}
              </th>
              <th scope="col" className="type-micro">
                {t(strings, "work.contractor.workload")}
              </th>
            </tr>
          </thead>
          <tbody>
            {CANDIDATES.map((candidate) => (
              <tr key={candidate.name}>
                <td className="type-caption">{notTranslatable(candidate.name)}</td>
                <td className="type-mono-data">{notTranslatable(candidate.withinSla)}</td>
                <td className="type-mono-data">{notTranslatable(candidate.variance)}</td>
                <td className="type-mono-data">{String(candidate.workload)}</td>
              </tr>
            ))}
          </tbody>
        </table>

        {/*
         * The line that does the work. Below the list, not above it: it is a
         * fact about the choice being made, and it belongs where the eye lands
         * after reading the options rather than before.
         *
         * Flagged ink, not severity ink — §E9.4 rule 1. A concentration is not
         * an incident and painting it as one would make every assignment feel
         * like an accusation, which is exactly the §22.2 liability §E19.6
         * warns the integrity room against.
         */}
        <p className="roadmap__warning type-body" data-flag="concentration">
          {t(strings, "work.concentration", {
            name: "Shirke Infra",
            share: "61%",
            zone: "Ward 14",
          })}
        </p>
        <p className="roadmap__why type-caption">{t(strings, "work.concentration.why")}</p>
      </ConsolePrint>

      <ConsolePrint title={t(strings, "work.budget")}>
        <p className="roadmap__warning type-body">
          {t(strings, "work.variance.over", { percent: "30%" })}
        </p>
        <p className="type-caption">{t(strings, "work.variance.justify")}</p>
        <ContractGap what={t(strings, "gap.rateCard")} strings={strings} />
      </ConsolePrint>

      {/*
       * §15.5's 30/40/30, as a physical gate strip.
       *
       * > You cannot drag a milestone open. Only evidence opens it.
       *
       * So the stages are rendered as list items with a locked state and no
       * control on them at all — not a disabled button, which would imply that
       * a permission is what stands between the officer and the money. The
       * stages come from `MILESTONE_STAGES`, which is the published set.
       */}
      <ConsolePrint title={t(strings, "work.milestones")}>
        <ol className="roadmap__gates">
          {MILESTONE_STAGES.map((stage, index) => (
            <li key={stage} className="roadmap__gate" data-open={index === 0 ? "true" : "false"}>
              <span className="type-mono-data">{notTranslatable(stage)}</span>
              <span className="type-caption">
                {t(strings, index === 0 ? "work.milestone.open" : "work.milestone.locked")}
              </span>
            </li>
          ))}
        </ol>
        <ContractGap what={t(strings, "gap.milestones")} strings={strings} />
      </ConsolePrint>
    </div>
  );
}
