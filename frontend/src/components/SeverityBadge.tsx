import { SEVERITY_DESCENDING, type SeverityLevel } from "@/design/generated/tokens";
import { t, type Strings } from "@/lib/i18n/strings";
import { SeverityMark } from "./SeverityMark";
import "./components.css";

/**
 * `<SeverityBadge>` — §E26.
 *
 * > Ink + shape + label. **Never colour alone.**
 *
 * Three properties are contracts rather than styling choices:
 *
 * **The ink comes from the token, and so does the glaze.** §E9.4 rule 3: *"the
 * badge and the shader are literally the same number"*. Neither this component
 * nor the TSL severity ramp contains a colour; both descend from the same line
 * of `tokens.json`, and `check-guards.ts` fails the build on a literal here.
 *
 * **The type sits on its own field, never on a table stock.** Measured, the
 * severity ink is 4.17:1 on `kraft-200` — below the floor — and 5.31:1 on its
 * own tint. That is not a palette defect; it is what §E9.4's four-channel model
 * exists for, and rendering the tint as the badge's ground is how the component
 * makes it impossible to get wrong.
 *
 * **An unscored complaint says so.** `severity_score` is null until Phase 12,
 * and a badge that rendered `low` for "we have not looked yet" would be the
 * §E3.3 violation this whole document is built against. `level={null}` renders
 * *"not yet scored"* in secondary ink, with no mark.
 */
export interface SeverityBadgeProps {
  /** `null` means unscored — a real state, not a missing prop. */
  readonly level: SeverityLevel | null;
  readonly strings: Strings;
  /** Show the score itself alongside the label, where one exists. */
  readonly score?: number | null;
  readonly density?: "comfortable" | "compact" | "dense";
}

export function SeverityBadge({ level, strings, score, density }: SeverityBadgeProps) {
  if (level === null) {
    return (
      <span className="severity-badge severity-badge--unscored" data-density={density}>
        {t(strings, "severity.unscored")}
      </span>
    );
  }

  return (
    <span
      className="severity-badge"
      data-severity={level}
      data-density={density}
      // The label is inside the badge, so the accessible name is the visible
      // text. No `aria-label` paraphrase to drift out of step with it.
    >
      <SeverityMark level={level} />
      <span className="severity-badge__label">{t(strings, `severity.${level}`)}</span>
      {score === null || score === undefined ? null : (
        <span className="severity-badge__score type-mono-data">{score.toFixed(2)}</span>
      )}
    </span>
  );
}

/**
 * The legend, for surfaces that carry one.
 *
 * Ordered most severe first, from the token file's own ordering — so a level
 * inserted between `high` and `medium` appears in the right place everywhere
 * without anyone re-sorting a list by hand.
 */
export function SeverityLegend({ strings }: { strings: Strings }) {
  return (
    <ul className="severity-legend" aria-label={t(strings, "severity.label")}>
      {SEVERITY_DESCENDING.map((level) => (
        <li key={level}>
          <SeverityBadge level={level} strings={strings} />
        </li>
      ))}
    </ul>
  );
}
