"use client";

import { useState } from "react";

import { NotWired } from "@/components/NotWired";
import { SeverityBadge } from "@/components/SeverityBadge";
import type { Complaint } from "@/lib/api/complaints";
import { notTranslatable, t, type Strings } from "@/lib/i18n/strings";
import { levelFor } from "@/lib/severity";

import "./citizen.css";

/**
 * §E17.4's `why? →` — §13.1's transparent rubric, rendered.
 *
 * > The severity row carries `why? →`, which opens the **actual rubric weights,
 * > the actual contributing factors, and the rubric version**. A citizen who can
 * > see why their pothole scored below someone's collapsed drain stops believing
 * > the system is rigged, which is worth more than any amount of polish.
 *
 * **Every number here is the server's own.** `severity_breakdown` is stored as
 * `components` (what was measured) and `weights` (what each was multiplied by),
 * deliberately kept as two maps rather than one flattened object, because
 * Phase 12's gate is that *a scored complaint reproduces its score from its own
 * breakdown*. So this panel does not compute anything: it multiplies for
 * display and shows the product beside the two factors, and a reader can check
 * the arithmetic. A panel that recomputed the score from the components would
 * be a second implementation of the rubric living in a browser.
 *
 * **`severity_policy_version` is rendered, always.** §13.1's whole argument is
 * that a score is only arguable against the exact document that produced it.
 * A breakdown with no version is a number with a plausible explanation attached.
 *
 * **The not-wired chip is here on purpose.** §E24: the fields are on the
 * published v1 schema and return `null` until Phase 12 lands. The component is
 * REAL, the data is SIMULATED, and §E28 now says so in two columns. Rendering
 * an invented score behind a real-looking panel is the precise failure §6
 * Principle #8 exists to prevent.
 */
export function SeverityWhy({
  complaint,
  strings,
}: {
  readonly complaint: Complaint;
  readonly strings: Strings;
}) {
  const [open, setOpen] = useState(false);

  const score = complaint.severity_score;
  const breakdown = complaint.severity_breakdown;
  const version = complaint.severity_policy_version;

  if (score === null || score === undefined) {
    return (
      <div className="severity-why" data-state="unscored">
        <span className="severity-why__label type-micro">{t(strings, "severity.label")}</span>
        <span className="severity-why__unscored type-body">{t(strings, "severity.unscored")}</span>
        <NotWired phase="Phase 12" strings={strings} />
      </div>
    );
  }

  const rows = breakdownRows(breakdown);

  return (
    <div className="severity-why" data-state="scored">
      <span className="severity-why__label type-micro">{t(strings, "severity.label")}</span>
      <SeverityBadge level={levelFor(score)} score={score} strings={strings} />

      <button
        type="button"
        className="severity-why__toggle type-micro"
        aria-expanded={open}
        onClick={() => {
          setOpen((current) => !current);
        }}
      >
        {t(strings, "severity.why")}
      </button>

      {open ? (
        <div className="severity-why__panel">
          {rows.length === 0 ? (
            <>
              <p className="severity-why__empty type-caption">
                {t(strings, "severity.noBreakdown")}
              </p>
              <NotWired phase="Phase 12" strings={strings} />
            </>
          ) : (
            <table className="severity-why__table">
              <caption className="type-micro">{t(strings, "severity.factors")}</caption>
              <thead>
                <tr>
                  <th scope="col" className="type-micro">
                    {t(strings, "severity.factor")}
                  </th>
                  <th scope="col" className="type-micro">
                    {t(strings, "severity.measured")}
                  </th>
                  <th scope="col" className="type-micro">
                    {t(strings, "severity.weight")}
                  </th>
                  <th scope="col" className="type-micro">
                    {t(strings, "severity.contribution")}
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.factor}>
                    {/*
                     * The factor key is the rubric's own name for it, from the
                     * tenant's governed document. Not translated, because
                     * translating a policy's field names would mean a Marathi
                     * console and an English one disagreeing about what the
                     * rubric says — §E10.1's line between design and content
                     * runs the other way here.
                     */}
                    <th scope="row" className="type-mono-data">
                      {notTranslatable(row.factor)}
                    </th>
                    <td className="type-mono-data">{notTranslatable(row.measured.toFixed(3))}</td>
                    <td className="type-mono-data">{notTranslatable(row.weight.toFixed(3))}</td>
                    <td className="type-mono-data">
                      {notTranslatable(row.contribution.toFixed(3))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <p className="severity-why__version type-caption">
            {version === null || version === undefined
              ? t(strings, "severity.noVersion")
              : t(strings, "severity.version", { version })}
          </p>
        </div>
      ) : null}
    </div>
  );
}

interface FactorRow {
  readonly factor: string;
  readonly measured: number;
  readonly weight: number;
  readonly contribution: number;
}

/**
 * Pair the two maps, ordered by how much each factor actually moved the score.
 *
 * Descending by contribution, because the question a citizen is asking is *"why
 * is mine lower than theirs"* and the answer is the top row. Alphabetical order
 * would bury it.
 *
 * A component with no weight, or a weight with no component, is **rendered as
 * zero rather than dropped**: a rubric that measured something and gave it no
 * weight is a fact about the rubric, and silently omitting the row would hide
 * exactly the kind of drift Phase 12's reproducibility gate exists to catch.
 */
function breakdownRows(breakdown: Complaint["severity_breakdown"]): readonly FactorRow[] {
  if (breakdown === null || breakdown === undefined) return [];
  const components = breakdown.components ?? {};
  const weights = breakdown.weights ?? {};

  const factors = new Set([...Object.keys(components), ...Object.keys(weights)]);
  return [...factors]
    .map((factor) => {
      const measured = components[factor] ?? 0;
      const weight = weights[factor] ?? 0;
      return { factor, measured, weight, contribution: measured * weight };
    })
    .sort((a, b) => b.contribution - a.contribution);
}
