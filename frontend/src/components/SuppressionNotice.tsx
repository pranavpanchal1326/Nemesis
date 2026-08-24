import { plural, t, type Strings } from "@/lib/i18n/strings";
import "./components.css";

/**
 * `<SuppressionNotice>` — §E26, §E18, ADR-0021.
 *
 * > *"Fewer than N reports — withheld to protect reporters."* **Never a blank
 * > cell.**
 *
 * §E18 states the reason without hedging: *"A k-anonymity hole that looks like
 * good news is worse than no data."* A ward with four reports and a blank cell
 * reads as a ward with no potholes, which is precisely the conclusion the
 * suppression exists to prevent anyone drawing.
 *
 * The threshold is shown, not hidden. A reader who can see *why* a number is
 * missing can reason about the dataset; a reader who cannot assumes it is
 * either zero or a bug, and both readings damage the thing this product is for.
 */
export function SuppressionNotice({
  threshold,
  strings,
  explain = false,
}: {
  readonly threshold: number;
  readonly strings: Strings;
  readonly explain?: boolean;
}) {
  return (
    <span className="suppression-notice">
      <span className="suppression-notice__text type-caption">
        {plural(strings, "suppression.withheld", threshold, { threshold })}
      </span>
      {explain ? (
        <span className="suppression-notice__why type-micro">{t(strings, "suppression.why")}</span>
      ) : null}
    </span>
  );
}
