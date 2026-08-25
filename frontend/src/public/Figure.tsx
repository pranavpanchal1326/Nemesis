import { SuppressionNotice } from "@/components/SuppressionNotice";
import { t, type Strings, type Translated } from "@/lib/i18n/strings";
import type { PublishedFigure } from "./figures";
import "./public.css";

/**
 * The only way a §E18 figure reaches a screen — §E18, ADR-0021.
 *
 * `PublishedFigure` is an object, so `{figure}` does not compile as a React
 * child. That is the mechanism: `readZone()` converts every count and rate into
 * one of these, and from that point on the raw number cannot be rendered by
 * accident. The k-anonymity rule is not a thing a component author has to
 * remember, it is a thing they cannot get wrong.
 *
 * Three renderings, because there are three facts:
 *
 * - **known** — the number, in the data face, tabular.
 * - **withheld** — `<SuppressionNotice>`: *"fewer than N reports — withheld to
 *   protect reporters"*, never a blank and never a zero.
 * - **unknown** — *"not measured"*. Distinct from withheld on purpose: a rate
 *   over zero resolutions was never computed, and saying it was withheld would
 *   imply data exists that does not.
 *
 * A genuine zero is a **known** figure and renders as *"None filed"* rather
 * than as `0`. Same reasoning one level down: a bare zero in a column of counts
 * is read as an absence of a problem, and *"none filed"* is read as an absence
 * of reports, which is the fact this page actually has.
 */
export type FigureFormat = "count" | "percent" | "hours";

export function Figure({
  figure,
  strings,
  format = "count",
}: {
  readonly figure: PublishedFigure;
  readonly strings: Strings;
  readonly format?: FigureFormat;
}) {
  if (figure.kind === "withheld") {
    return <SuppressionNotice threshold={figure.threshold} strings={strings} />;
  }

  if (figure.kind === "unknown") {
    return (
      <span className="figure figure--unknown type-caption" title={t(strings, "figure.unknownWhy")}>
        {t(strings, "figure.unknown")}
      </span>
    );
  }

  if (format === "count" && figure.value === 0) {
    return (
      <span className="figure figure--none type-caption" title={t(strings, "figure.noneWhy")}>
        {t(strings, "figure.none")}
      </span>
    );
  }

  return (
    <span className="figure type-mono-data" data-format={format}>
      {formatted(figure.value, format, strings)}
    </span>
  );
}

function formatted(value: number, format: FigureFormat, strings: Strings): Translated {
  if (format === "percent") {
    // `Intl.NumberFormat` per locale, not `toFixed` plus a literal `%`: the
    // decimal separator and the digits themselves differ between the scripts
    // this product typesets, and §E10.2's tabular rule is about columns lining
    // up rather than about which glyphs are in them.
    return t(strings, "figure.percent", {
      value: new Intl.NumberFormat(strings.locale, {
        minimumFractionDigits: 1,
        maximumFractionDigits: 1,
      }).format(value * 100),
    });
  }
  if (format === "hours") {
    return t(strings, "figure.hours", {
      value: new Intl.NumberFormat(strings.locale, { maximumFractionDigits: 1 }).format(value),
    });
  }
  return t(strings, "figure.count", {
    value: new Intl.NumberFormat(strings.locale).format(value),
  });
}

/**
 * A labelled figure in a definition list.
 *
 * `<dt>`/`<dd>` rather than a table row: these are four independent readings of
 * one place, not a grid of comparable cells, and a screen reader announcing
 * "Reports filed, 41" is the sentence a reader wants. `<ContractorLedger>` made
 * the same call and `axe` agreed with it.
 */
export function LabelledFigure({
  label,
  figure,
  strings,
  format,
  note,
}: {
  readonly label: Translated;
  readonly figure: PublishedFigure;
  readonly strings: Strings;
  readonly format?: FigureFormat;
  /** One sentence saying what the figure means, where the meaning is arguable.
   *  §E18's whole posture is that a published number states its own provenance. */
  readonly note?: Translated;
}) {
  return (
    <div className="published-figure">
      <dt className="published-figure__label type-caption">{label}</dt>
      <dd className="published-figure__value">
        <Figure figure={figure} strings={strings} {...(format ? { format } : {})} />
        {note === undefined ? null : (
          <span className="published-figure__note type-caption">{note}</span>
        )}
      </dd>
    </div>
  );
}
