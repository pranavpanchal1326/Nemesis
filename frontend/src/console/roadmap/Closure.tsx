import { t, type Strings, type Translated } from "@/lib/i18n/strings";

import { ConsolePrint } from "../ConsoleShell";
import { ContractGap, FixtureNotice } from "./Fixture";
import "./roadmap.css";

/**
 * §E19.4 — closure: evidence or nothing. **ROADMAP (Phase 15).**
 *
 * This is the screen §E19.4 writes a whole paragraph of warning about, and the
 * warning is the specification:
 *
 * > **The backend enforces this; the UI renders it.** … a client-side check is
 * > a convenience, and if it is ever mistaken for the control, someone will
 * > eventually ship a path around it.
 * >
 * > The UI's job is to make the rule **legible before it is hit**. `Resolved`
 * > renders visibly disabled **with the unmet conditions attached**.
 * >
 * > Making an integrity rule visible in its disabled state is what teaches an
 * > organisation the rule. A validation that fires on submit teaches nothing,
 * > and a validation the UI *owns* teaches something false.
 *
 * Three things follow from that, and all three are visible in the markup below.
 *
 * **The control is `disabled` and it is legible.** Not hidden, not greyed to
 * illegibility. `review.css`' note about disabled controls applies here first:
 * a rule you can read is a rule you learn.
 *
 * **The conditions are attached to it, not in a tooltip.** `aria-describedby`
 * points the button at the list, so a screen-reader user hears *why* it is
 * disabled at the moment they reach it rather than having to hunt for a
 * paragraph elsewhere on the page.
 *
 * **The count is stated.** *"2 of 3 conditions met"* — the same honesty §E19.6
 * asks for on the blacklist action, for the same reason: a number tells an
 * officer how far away they are, and a bare disabled control tells them
 * nothing.
 *
 * **The ambiguous score is printed.** §E19.4 says the SSIM result is printed
 * *"honestly, including when ambiguous"*. A verification that came back
 * undecided is not a failure and not a pass, and rounding it into either would
 * be the product deciding something the algorithm declined to.
 */

interface Condition {
  readonly key: string;
  readonly label: Translated;
  readonly met: boolean;
  /** What the record says, where there is something to print. */
  readonly detail?: Translated;
}

export function Closure({ strings }: { readonly strings: Strings; readonly locale: string }) {
  const conditions: readonly Condition[] = [
    { key: "beforeAfter", label: t(strings, "closure.beforeAfter"), met: true },
    {
      key: "ssim",
      label: t(strings, "closure.ssim"),
      met: false,
      // Printed as the number it is, beside the sentence that says the number
      // did not decide anything.
      detail: t(strings, "closure.ssim.ambiguous"),
    },
    { key: "confirmation", label: t(strings, "closure.confirmation"), met: true },
  ];

  const met = conditions.filter((condition) => condition.met).length;

  return (
    <div className="roadmap">
      <FixtureNotice phase="15" strings={strings} />

      <ConsolePrint title={t(strings, "closure.title")}>
        <p className="roadmap__why type-caption">{t(strings, "closure.enforced")}</p>

        <ul className="roadmap__conditions" id="closure-conditions">
          {conditions.map((condition) => (
            <li
              key={condition.key}
              className="roadmap__condition"
              data-met={condition.met ? "true" : "false"}
            >
              {/*
               * Met-ness carries a mark as well as a state attribute, for the
               * same reason severity does (§E9.4 rule 2): this screen is
               * printed, and on paper a colour difference between met and unmet
               * is no difference at all.
               */}
              <span aria-hidden="true" className="roadmap__condition-mark type-mono-data">
                {condition.met ? "✓" : "—"}
              </span>
              <span className="type-caption">{condition.label}</span>
              {condition.detail === undefined ? null : (
                <span className="roadmap__condition-detail type-caption">{condition.detail}</span>
              )}
            </li>
          ))}
        </ul>

        <p className="type-caption" id="closure-count">
          {t(strings, "closure.unmet", { met, total: conditions.length })}
        </p>

        <button
          type="button"
          className="roadmap__action type-caption"
          disabled
          aria-describedby="closure-count closure-conditions"
        >
          {t(strings, "closure.resolved")}
        </button>

        <ContractGap what={t(strings, "gap.ssim")} strings={strings} />
      </ConsolePrint>
    </div>
  );
}
