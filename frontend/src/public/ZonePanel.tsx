import { SuppressionNotice } from "@/components/SuppressionNotice";
import { notTranslatable, plural, t, type Strings, type Translated } from "@/lib/i18n/strings";
import { Figure, LabelledFigure } from "./Figure";
import type { PublishedZone } from "./figures";
import "./public.css";

/**
 * One place's figures — §E18, §16.2.
 *
 * Six readings, each labelled, none collapsed into a headline. The reasoning is
 * §16.1's about contractors applied one level up: a single "performance" number
 * for a ward is a number somebody optimises, and the four that make it up are
 * the four an argument can actually be had about.
 *
 * **`auto_confirmed_resolutions` is on the page rather than folded into
 * `resolved`**, and that is the whole reason this product publishes at all. A
 * closure nobody confirmed is a weaker fact than one somebody did — §21.2's
 * trust-collapse fix — and a summary that adds them together has published the
 * strong number and kept the weak one.
 *
 * When the place is below the floor, every figure renders `<SuppressionNotice>`
 * because `readZone` made them all `withheld`. The notice appears once at the
 * top as well, because six copies of the same sentence is not six facts.
 */
export function ZonePanel({
  zone,
  strings,
}: {
  readonly zone: PublishedZone;
  readonly strings: Strings;
}) {
  return (
    <section className="zone-panel" data-suppressed={zone.suppressed}>
      {zone.suppressed ? (
        <p className="zone-panel__suppressed">
          <SuppressionNotice threshold={zone.suppressionThreshold} strings={strings} explain />
        </p>
      ) : null}

      <dl className="zone-panel__figures">
        <LabelledFigure
          label={t(strings, "figure.total")}
          figure={zone.totalReports}
          strings={strings}
        />
        <LabelledFigure
          label={t(strings, "figure.open")}
          figure={zone.openReports}
          strings={strings}
        />
        <LabelledFigure
          label={t(strings, "figure.resolved")}
          figure={zone.resolvedReports}
          strings={strings}
        />
        <LabelledFigure
          label={t(strings, "figure.autoConfirmed")}
          figure={zone.autoConfirmedResolutions}
          strings={strings}
          note={t(strings, "figure.autoConfirmedWhy")}
        />
        <LabelledFigure
          label={t(strings, "figure.resolutionRate")}
          figure={zone.resolutionRate}
          strings={strings}
          format="percent"
        />
        <LabelledFigure
          label={t(strings, "figure.medianHours")}
          figure={zone.medianResolutionHours}
          strings={strings}
          format="hours"
        />
      </dl>

      <CategoryBreakdown zone={zone} strings={strings} />
    </section>
  );
}

/**
 * What was reported, by category.
 *
 * **The suppressed-bucket sentence is not optional.** `_category_breakdown`
 * drops categories below the floor and counts them into
 * `count_suppressed_buckets`, so the visible rows sum to *less* than the total
 * above them. A reader who can see the shortfall and not its cause concludes
 * the difference is zero — the same k-anonymity misreading one level down, and
 * the one an aggregate table is most likely to produce by accident.
 */
function CategoryBreakdown({
  zone,
  strings,
}: {
  readonly zone: PublishedZone;
  readonly strings: Strings;
}) {
  if (zone.suppressed) return null;

  const hidden = zone.suppressedBuckets;

  return (
    <section className="zone-panel__breakdown" aria-labelledby={`breakdown-${zone.zoneCode}`}>
      <h3 id={`breakdown-${zone.zoneCode}`} className="type-caption">
        {t(strings, "breakdown.title")}
      </h3>

      {zone.byCategory.length === 0 ? (
        <p className="type-caption">{t(strings, "breakdown.empty")}</p>
      ) : (
        <div className="zone-panel__scroll">
          <table className="zone-panel__categories">
            <caption className="type-caption">{t(strings, "breakdown.title")}</caption>
            <thead>
              <tr>
                <th scope="col">{t(strings, "breakdown.category")}</th>
                <th scope="col">{t(strings, "breakdown.count")}</th>
              </tr>
            </thead>
            <tbody>
              {zone.byCategory.map((row) => (
                <tr key={row.category}>
                  {/*
                   * The tenant's own display name for the taxonomy key, in the
                   * locale this page asked for — C7, ADR-0052. Still
                   * `notTranslatable`: it is the tenant's words, resolved by
                   * the server through the Phase 5 registry, and the client
                   * asserting its own translation of a customer's vocabulary is
                   * exactly what §E10 forbids.
                   *
                   * It falls back to the bare key upstream when the tenant has
                   * not named the category in this locale, which reads as
                   * `pothole_or_road_damage` — an untranslated entry rather
                   * than a blank cell, and legible as one. Rendered in the data
                   * face for the same reason: a key looks like a key.
                   */}
                  <th scope="row" className="type-mono-data">
                    {notTranslatable(row.categoryName)}
                  </th>
                  <td>
                    <Figure figure={row.count} strings={strings} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {hidden > 0 ? (
        <p className="zone-panel__hidden type-caption">
          {hiddenSentence(strings, hidden, zone.suppressionThreshold)}
        </p>
      ) : null}
    </section>
  );
}

function hiddenSentence(strings: Strings, hidden: number, threshold: number): Translated {
  return plural(strings, "breakdown.hidden", hidden, { threshold });
}
