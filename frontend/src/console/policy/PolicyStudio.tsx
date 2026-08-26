import Link from "next/link";

import { POLICY_KINDS } from "@/generated/enums";
import { formatLedgerTime } from "@/lib/i18n/datetime";
import { notTranslatable, t, type Strings } from "@/lib/i18n/strings";
import type { PolicyStudioData } from "@/server/policy-data";

import { ConsolePrint } from "../ConsoleShell";
import { ActivateControl } from "./ActivateControl";
import { diffDocuments } from "./diff";
import { TuningProposals } from "./TuningProposals";
import "./policy.css";

/**
 * §E19.8 — the policy studio. **REAL**, and the console's sharpest screen.
 *
 * > Rules as editable documents with revision history and diff. **The activate
 * > control is disabled without a backtest**: *"Run against a labelled set to
 * > see what this would have changed."* Then the result — *"this threshold
 * > would have merged 47 additional reports across 400 historical complaints, 6
 * > of which reviewers later reverted."*
 * >
 * > Retuning becomes an evidence-based act.
 *
 * Three things about this screen are worth arguing for explicitly, because each
 * of them is a place where the obvious implementation would be wrong.
 *
 * **The disabled activate control is not the control.** §E19.4's division holds
 * here as much as it does on closure: `policy/service.py`'s
 * `_require_certification` refuses an activation with no passing certificate,
 * and it does so *"at the single mutation path"* with a comment explaining that
 * a guardrail which could fail open is indistinguishable from no guardrail. So
 * this screen asks the same question the server asks — is there a published
 * evaluation set for this kind — and renders the rule. If the two ever
 * disagree, the server wins and the operator sees a refusal that states why.
 *
 * **The backtest result is stated in the report's own terms.** §E19.8's example
 * sentence is *"…47 additional reports across 400 historical complaints, 6 of
 * which reviewers later reverted"*, and those are three different numbers from
 * two different places. `affected` / `case_count` / `population` come from the
 * run; the reverted count comes from the tuning proposals, which is the only
 * place in the system where a human's reversal of a merge becomes a number. The
 * screen renders both and does not blend them into one sentence, because they
 * are answers to different questions.
 *
 * **The diff is structural.** See `diff.ts` — two documents differing only in
 * key order are the same policy, and a text diff would say otherwise on every
 * re-serialisation.
 */
export function PolicyStudio({
  data,
  strings,
  locale,
}: {
  readonly data: PolicyStudioData;
  readonly strings: Strings;
  readonly locale: string;
}) {
  const { selected, previous } = data;
  const changes = selected === null ? [] : diffDocuments(previous?.body ?? {}, selected.body);

  return (
    <div className="policy">
      <ConsolePrint title={t(strings, "policy.kinds")}>
        <ul className="policy__kinds">
          {POLICY_KINDS.map((kind) => (
            <li key={kind}>
              <Link
                href={`/console/policy?kind=${kind}`}
                className="policy__kind type-mono-data"
                aria-current={kind === data.kind ? "page" : undefined}
              >
                {notTranslatable(kind)}
              </Link>
            </li>
          ))}
        </ul>
        {data.active === null ? null : (
          <p className="type-caption">
            {data.active.is_baseline
              ? t(strings, "policy.baseline")
              : t(strings, "policy.revision", { revision: data.active.revision ?? 0 })}
          </p>
        )}
      </ConsolePrint>

      <ConsolePrint title={t(strings, "policy.revisions")}>
        {data.versions.length === 0 ? (
          <p className="type-caption">{t(strings, "policy.none")}</p>
        ) : (
          <table className="policy__table">
            <thead>
              <tr>
                <th scope="col" className="type-micro">
                  {t(strings, "policy.revisions")}
                </th>
                <th scope="col" className="type-micro">
                  {t(strings, "work.status")}
                </th>
                <th scope="col" className="type-micro">
                  {t(strings, "policy.changeReason")}
                </th>
                <th scope="col" className="type-micro">
                  {t(strings, "queue.filed")}
                </th>
              </tr>
            </thead>
            <tbody>
              {data.versions.map((version) => (
                <tr
                  key={version.revision}
                  aria-current={version.revision === selected?.revision ? "true" : undefined}
                >
                  <th scope="row" className="type-mono-data">
                    <Link
                      href={`/console/policy?kind=${data.kind}&revision=${String(version.revision)}`}
                    >
                      {notTranslatable(String(version.revision))}
                    </Link>
                  </th>
                  <td className="type-caption">{t(strings, `policy.status.${version.status}`)}</td>
                  <td className="type-caption">{notTranslatable(version.change_reason)}</td>
                  <td className="type-mono-data">{formatLedgerTime(version.created_at, locale)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </ConsolePrint>

      {selected === null ? null : (
        <>
          <ConsolePrint title={t(strings, "policy.document")}>
            <p className="type-caption">{notTranslatable(selected.change_reason)}</p>
            <p className="policy__hash type-mono-data">
              {t(strings, "policy.contentHash")} {notTranslatable(selected.content_hash)}
            </p>
            {/*
             * The document, as the record holds it. Pretty-printed with two
             * spaces and not reformatted in any other way: an operator reading
             * this screen is reading the thing the content hash was computed
             * over, and a studio that rewrote the document for display would be
             * showing something the hash does not attest to.
             */}
            <pre className="policy__document type-mono-data">
              {JSON.stringify(selected.body, null, 2)}
            </pre>
          </ConsolePrint>

          <ConsolePrint
            title={
              previous === null
                ? t(strings, "policy.diff", { revision: 0 })
                : t(strings, "policy.diff", { revision: previous.revision })
            }
          >
            {previous === null ? (
              <p className="type-caption">{t(strings, "policy.diff.first")}</p>
            ) : changes.length === 0 ? (
              <p className="type-caption">{t(strings, "policy.diff.none")}</p>
            ) : (
              <ul className="policy__diff">
                {changes.map((change) => (
                  <li key={change.path} className="policy__change" data-change={change.kind}>
                    <span className="type-micro">{t(strings, `policy.diff.${change.kind}`)}</span>
                    <span className="policy__path type-mono-data">
                      {notTranslatable(change.path)}
                    </span>
                    <span className="type-mono-data">
                      {notTranslatable(
                        change.kind === "added"
                          ? change.after
                          : change.kind === "removed"
                            ? change.before
                            : `${change.before} → ${change.after}`,
                      )}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </ConsolePrint>

          <ConsolePrint title={t(strings, "backtest.title")}>
            <p className="type-caption">{t(strings, "backtest.run")}</p>
            {data.runs.length === 0 ? (
              <p className="type-caption">{t(strings, "backtest.none")}</p>
            ) : (
              <ul className="policy__runs">
                {data.runs.map((run) => (
                  <li key={run.id} className="policy__run">
                    <p className="type-body">
                      {t(strings, "backtest.result", {
                        affected: run.affected,
                        cases: run.case_count,
                        population: run.population,
                      })}
                    </p>
                    <p className="type-mono-data">
                      {t(strings, "backtest.window")} {formatLedgerTime(run.window_start, locale)} —{" "}
                      {formatLedgerTime(run.window_end, locale)}
                    </p>
                    {run.failure_reason === null ? null : (
                      <p className="type-caption">
                        {t(strings, "backtest.failed", { reason: run.failure_reason })}
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            )}
            <p className="policy__why type-caption">{t(strings, "backtest.coverage")}</p>
          </ConsolePrint>

          <ConsolePrint title={t(strings, "activate.title")}>
            <ActivateControl
              kind={data.kind}
              revision={selected.revision}
              gateCode={data.gate?.code ?? null}
              strings={strings}
            />
          </ConsolePrint>
        </>
      )}

      {data.kind !== "dedup_thresholds" ? null : (
        <ConsolePrint title={t(strings, "tuning.title")}>
          <TuningProposals strings={strings} />
        </ConsolePrint>
      )}
    </div>
  );
}
