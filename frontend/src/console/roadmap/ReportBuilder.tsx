import { notTranslatable, t, type Strings } from "@/lib/i18n/strings";

import { ConsolePrint } from "../ConsoleShell";
import { ContractGap, FixtureNotice } from "./Fixture";
import "./roadmap.css";

/**
 * §E19.7 — the report builder. **ROADMAP (Phase 23).**
 *
 * > An officer's actual job is producing documents for someone above them …
 * > **A report that carries its own proof is a category difference from a
 * > report that carries a logo.** Export to PDF; the PDF is the same design as
 * > the screen.
 *
 * Two things are worth stating here rather than in Phase 23.
 *
 * **"The PDF is the same design as the screen" is a claim about this file's
 * relationship to `console.css`, not about a rendering library.** The print
 * stylesheet F3 shipped already turns any console screen into an A4 document,
 * and this screen is built inside that mechanism rather than beside it —
 * `<ConsolePrint>` sheets, the same provenance footer, the same page-break
 * rules. When Phase 23 adds an export it exports the print stylesheet's own
 * output, which is why there is no second design to keep in sync.
 *
 * **The verification footer is the whole feature.** A chain root and a verify
 * URL on the last page is what makes the document checkable by somebody who
 * does not trust the person who handed it to them — which is the audience a
 * report to a commissioner or an RTI applicant actually has.
 */
export function ReportBuilder({ strings }: { readonly strings: Strings; readonly locale: string }) {
  return (
    <div className="roadmap">
      <FixtureNotice phase="23" strings={strings} />

      <ConsolePrint title={t(strings, "reports.scope")}>
        <p className="type-caption">{t(strings, "reports.period")}</p>
        <ContractGap what={t(strings, "gap.report")} strings={strings} />
      </ConsolePrint>

      <ConsolePrint title={t(strings, "reports.footer")}>
        <p className="roadmap__why type-caption">{t(strings, "reports.footer.why")}</p>
        {/*
         * A fixture hash, and it is rendered in the data face with the same
         * treatment `<Receipt>` gives a real one — because the point of showing
         * it now is to check that a 64-character hex string fits the column at
         * all three densities and on paper, which is a question you answer
         * before the value is real or you answer it during Phase 23 under
         * pressure.
         */}
        <p className="roadmap__hash type-mono-data">
          {notTranslatable("0000000000000000000000000000000000000000000000000000000000000000")}
        </p>
      </ConsolePrint>
    </div>
  );
}
