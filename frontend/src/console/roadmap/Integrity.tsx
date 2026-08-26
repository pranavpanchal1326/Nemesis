import { InkFigure } from "@/ink/InkFigure";
import { t, type Strings, type Translated } from "@/lib/i18n/strings";

import { ConsolePrint } from "../ConsoleShell";
import { ContractGap, FixtureNotice } from "./Fixture";
import "./roadmap.css";

/**
 * §E19.6 — where corruption becomes a case. **ROADMAP (Phase 17).**
 *
 * > Designed as an **investigation tool**, not an accusation machine —
 * > otherwise it is simultaneously a §22.2 liability and a political weapon.
 *
 * That sentence rules this screen, and two of its consequences are structural
 * rather than cosmetic.
 *
 * **Every signal card names its detector, threshold and confidence.** §E19.6:
 * *"If you are going to flag a named commercial entity, you show your method."*
 * So the card is a definition list of the method, and the finding is one line
 * inside it — not a headline with the method in a tooltip. A card that cannot
 * state its threshold does not render.
 *
 * **Blacklisting is not a button, and the requirements are shown rather than
 * the action hidden.** *"The UI enforces this by showing the unmet
 * requirements — the action is visible and disabled, reading '3 of 5
 * requirements met', which is far more honest than hiding it."* Same mechanism
 * as `<Closure>`, same reason: a rule you can read is a rule you learn.
 *
 * **What has no contract.** Everything the detectors produce. There is no
 * signal schema, no case file, no approver record, and no entity-resolution
 * graph. Phase 17 is where they arrive; until then the screen shows the shape
 * of the argument with fixtures and says plainly what is missing.
 */

interface Signal {
  readonly key: string;
  readonly detector: string;
  readonly threshold: string;
  readonly confidence: string;
  readonly finding: Translated;
}

export function Integrity({ strings }: { readonly strings: Strings; readonly locale: string }) {
  const signals: readonly Signal[] = [
    {
      key: "cost-variance",
      detector: "cost_variance_vs_rate_card",
      threshold: "> 25% over SoR",
      confidence: "0.71",
      finding: t(strings, "work.variance.over", { percent: "31%" }),
    },
    {
      key: "concentration",
      detector: "award_concentration",
      threshold: "> 40% of ward category",
      confidence: "0.64",
      finding: t(strings, "work.concentration", {
        name: "Shirke Infra",
        share: "61%",
        zone: "Ward 14",
      }),
    },
  ];

  /** §E19.6's five, in the order the section states them. */
  const requirements = [
    { key: "evidence", label: t(strings, "integrity.requirement.evidence"), met: true },
    { key: "response", label: t(strings, "integrity.requirement.response"), met: true },
    { key: "approvers", label: t(strings, "integrity.requirement.approvers"), met: false },
    { key: "duration", label: t(strings, "integrity.requirement.duration"), met: true },
    { key: "basis", label: t(strings, "integrity.requirement.basis"), met: false },
  ] as const;

  const met = requirements.filter((requirement) => requirement.met).length;

  return (
    <div className="roadmap">
      <FixtureNotice phase="17" strings={strings} />

      <ConsolePrint title={t(strings, "integrity.signals")}>
        {/*
          §E8.2 — the Auditor, and the register that table gives them: *"patient.
          Never smug, never accusatory — §22.2 is a design constraint on the
          character too."* So the figure stands at rest beside the disclaimer
          rather than pointing at a signal, and it is **not** `live`: a figure
          that reacted to events on this screen would be a character having an
          opinion about a named commercial entity, which is precisely what
          §E19.6 says this room must never do.
        */}
        <div className="roadmap__disclaimer">
          <InkFigure strings={strings} figure="auditor" className="ink--inline" fill={0.9} />
          <p className="roadmap__why type-caption">{t(strings, "integrity.disclaimer")}</p>
        </div>
        <ul className="roadmap__signals">
          {signals.map((signal) => (
            <li key={signal.key} className="roadmap__signal" data-flag="signal">
              <p className="type-body">{signal.finding}</p>
              <dl className="roadmap__method">
                <div>
                  <dt className="type-micro">{t(strings, "integrity.detector")}</dt>
                  <dd className="type-mono-data">{signal.detector}</dd>
                </div>
                <div>
                  <dt className="type-micro">{t(strings, "integrity.threshold")}</dt>
                  <dd className="type-mono-data">{signal.threshold}</dd>
                </div>
                <div>
                  <dt className="type-micro">{t(strings, "integrity.confidence")}</dt>
                  <dd className="type-mono-data">{signal.confidence}</dd>
                </div>
              </dl>
            </li>
          ))}
        </ul>
        <ContractGap what={t(strings, "gap.signals")} strings={strings} />
      </ConsolePrint>

      <ConsolePrint title={t(strings, "integrity.case")}>
        <p className="type-caption">{t(strings, "integrity.case.attach")}</p>
        <p className="type-caption">{t(strings, "integrity.response.clock", { time: "14 Sep" })}</p>
        <ContractGap what={t(strings, "gap.caseFile")} strings={strings} />
      </ConsolePrint>

      <ConsolePrint title={t(strings, "integrity.blacklist")}>
        <ul className="roadmap__conditions" id="blacklist-requirements">
          {requirements.map((requirement) => (
            <li
              key={requirement.key}
              className="roadmap__condition"
              data-met={requirement.met ? "true" : "false"}
            >
              <span aria-hidden="true" className="roadmap__condition-mark type-mono-data">
                {requirement.met ? "✓" : "—"}
              </span>
              <span className="type-caption">{requirement.label}</span>
            </li>
          ))}
        </ul>
        <p className="type-caption" id="blacklist-count">
          {t(strings, "integrity.blacklist.counter", { met, total: requirements.length })}
        </p>
        <button
          type="button"
          className="roadmap__action type-caption"
          disabled
          aria-describedby="blacklist-count blacklist-requirements"
        >
          {t(strings, "integrity.blacklist")}
        </button>
        <p className="roadmap__why type-caption">{t(strings, "integrity.blacklist.why")}</p>
      </ConsolePrint>
    </div>
  );
}
