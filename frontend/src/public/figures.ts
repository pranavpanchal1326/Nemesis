import type { components } from "@/generated/api";

/**
 * The one rule §E18 states as a rule rather than as a preference:
 *
 * > `suppressed` and `suppression_threshold` render as *"Fewer than N reports —
 * > withheld to protect reporters,"* never as a blank cell that reads as zero.
 * > **A k-anonymity hole that looks like good news is worse than no data**
 * > (ADR-0021).
 *
 * **The backend hands us a zero on purpose, and that is the trap.**
 * `public/aggregates.py` returns `total_reports: 0` alongside `suppressed:
 * true`, with the comment *"publish the shape and the fact of suppression, and
 * no measure at all"*. So the number is right there, typed `number`, ready to
 * be rendered by any component that reaches for it — and a surface that renders
 * it has published *"Kothrud: 0 potholes"* about a ward with four.
 *
 * A convention would not survive. So the numbers do not leave this module as
 * numbers: `readZone()` converts a response into `PublishedFigure`s, and a
 * `PublishedFigure` cannot be interpolated into JSX — it is an object, and
 * TypeScript refuses `{figure}` as a React child. The only way to put one on a
 * screen is `<Figure>`, which knows about suppression. The gate is closed by
 * the type system rather than by remembering.
 *
 * Three states, not two. **`withheld` is not `unknown`.** A withheld figure was
 * measured and is being kept back to protect the people who reported it; an
 * unknown one was never measured — a resolution rate over zero resolutions, a
 * median over an empty set. Collapsing them would mean a ward with no data and
 * a ward with protected data read the same, which is the same failure one step
 * along.
 */

type ZoneSummary = components["schemas"]["ZoneSummaryResponse"];
type ContractorProfile = components["schemas"]["ContractorProfileResponse"];
type BudgetSummary = components["schemas"]["BudgetSummaryResponse"];
type BudgetLine = components["schemas"]["BudgetLineResponse"];
type CategoryCount = components["schemas"]["CategoryCountResponse"];

export type PublishedFigure =
  /** Measured, above the floor, publishable. */
  | { readonly kind: "known"; readonly value: number }
  /** Measured and held back — `suppressed`, with the floor that decided it. */
  | { readonly kind: "withheld"; readonly threshold: number }
  /** Never measured. `null` upstream: a rate with no denominator, a median with
   *  no sample. §E3.3 renders the gap rather than a zero standing in for it. */
  | { readonly kind: "unknown" };

const UNKNOWN: PublishedFigure = { kind: "unknown" };

function figure(value: number | null | undefined, withheld: boolean, threshold: number) {
  if (withheld) return { kind: "withheld", threshold } as const;
  if (value === null || value === undefined) return UNKNOWN;
  return { kind: "known", value } as const;
}

/**
 * One place, with every count and rate already decided.
 *
 * `zoneCode`, `zoneName` and `zoneKind` stay strings: they are not measures, no
 * threshold applies to them, and a ward's *name* is not a disclosure. Only the
 * things that count people are converted.
 */
export interface PublishedZone {
  readonly zoneCode: string;
  readonly zoneName: string;
  readonly zoneKind: string;
  readonly centroid: { readonly lat: number; readonly lng: number } | null;

  readonly totalReports: PublishedFigure;
  readonly openReports: PublishedFigure;
  readonly resolvedReports: PublishedFigure;
  readonly autoConfirmedResolutions: PublishedFigure;
  readonly resolutionRate: PublishedFigure;
  readonly medianResolutionHours: PublishedFigure;

  readonly byCategory: readonly { readonly category: string; readonly count: PublishedFigure }[];

  /** True when the whole place is below the floor. */
  readonly suppressed: boolean;
  readonly suppressionThreshold: number;
  /**
   * Categories held back *inside* an otherwise publishable place. Rendered as
   * its own sentence, because a breakdown that silently omits six of nine
   * categories is a breakdown that adds up to less than its own total and
   * invites the reader to conclude the difference is zero.
   */
  readonly suppressedBuckets: number;

  /** §22.2, required on every figure this system computed rather than observed. */
  readonly notice: string;
  readonly generatedAt: string;
}

export function readZone(summary: ZoneSummary): PublishedZone {
  const withheld = summary.suppressed;
  const threshold = summary.suppression_threshold;
  const asFigure = (value: number | null | undefined) => figure(value, withheld, threshold);

  return {
    zoneCode: summary.zone_code,
    zoneName: summary.zone_name,
    zoneKind: summary.zone_kind,
    // `Centroid` itself is nullable *and* both its members are, because a zone
    // may have a boundary with no computed centre. Three nulls, one meaning:
    // there is no point to state.
    centroid:
      summary.centroid?.lat == null || summary.centroid.lng == null
        ? null
        : { lat: summary.centroid.lat, lng: summary.centroid.lng },

    totalReports: asFigure(summary.total_reports),
    openReports: asFigure(summary.open_reports),
    resolvedReports: asFigure(summary.resolved_reports),
    autoConfirmedResolutions: asFigure(summary.auto_confirmed_resolutions),
    resolutionRate: asFigure(summary.resolution_rate),
    medianResolutionHours: asFigure(summary.median_resolution_hours),

    // Not `asFigure`: a bucket that reached this array is above the floor by
    // construction — `_category_breakdown` drops the ones that are not and
    // counts them into `count_suppressed_buckets` instead. Passing `withheld`
    // here would be correct only by accident, since a suppressed place has no
    // buckets at all.
    byCategory: summary.by_category.map((row: CategoryCount) => ({
      category: row.category,
      count: { kind: "known", value: row.count } as const,
    })),

    suppressed: withheld,
    suppressionThreshold: threshold,
    suppressedBuckets: summary.count_suppressed_buckets,

    notice: summary.notice,
    generatedAt: summary.generated_at,
  };
}

/**
 * §16.1's ledger — four metrics that cannot be collapsed to one.
 *
 * Two of the four are backed today; `<ContractorLedger>` renders the other two
 * as nulls behind a not-wired chip rather than dropping the rows, and this
 * shape keeps that honest by carrying them.
 */
export interface PublishedContractor {
  readonly contractorId: string;
  readonly contractorName: string;
  readonly registrationId: string;
  readonly activeSince: string | null;

  readonly completed: PublishedFigure;
  readonly open: PublishedFigure;
  readonly onTimeRate: PublishedFigure;
  readonly disputed: PublishedFigure;
  readonly certifiedCategories: readonly string[];

  readonly suppressed: boolean;
  readonly suppressionThreshold: number;

  /** §22.2 and §16.1, both required fields upstream. Never a tooltip (§E18). */
  readonly notice: string;
  readonly ratingDisclaimer: string;
  readonly generatedAt: string;
}

export function readContractor(profile: ContractorProfile): PublishedContractor {
  const withheld = profile.suppressed;
  const threshold = profile.suppression_threshold;
  const asFigure = (value: number | null | undefined) => figure(value, withheld, threshold);

  return {
    contractorId: profile.contractor_id,
    contractorName: profile.contractor_name,
    registrationId: profile.registration_id,
    activeSince: profile.active_since ?? null,

    completed: asFigure(profile.work_orders_completed),
    open: asFigure(profile.work_orders_open),
    onTimeRate: asFigure(profile.on_time_rate),
    disputed: asFigure(profile.disputed_count),
    certifiedCategories: profile.certified_categories,

    suppressed: withheld,
    suppressionThreshold: threshold,

    notice: profile.notice,
    ratingDisclaimer: profile.rating_disclaimer,
    generatedAt: profile.generated_at,
  };
}

/**
 * §17.6's ward budget.
 *
 * **No suppression here, and the backend says why:** *"A budget allocation is a
 * published public-finance figure about a municipality, not an observation
 * about any citizen … withholding a line because only one scheme funded a ward
 * would hide precisely the thing an RTI applicant is looking for."* So the
 * amounts stay as they arrive.
 *
 * They arrive as **strings**, and they stay strings. They are `NUMERIC` in the
 * database for §17.2's rate-card arithmetic, and the one place a reader will
 * hold the figure against a printed document is the last place to introduce a
 * float. `Number()` is never called on them here.
 */
export interface PublishedBudget {
  readonly zoneCode: string;
  readonly fiscalYear: string;
  readonly currency: string;
  readonly allocations: readonly {
    readonly fundingSource: string;
    readonly allocated: string;
    readonly spent: string;
    readonly utilisation: PublishedFigure;
  }[];
  readonly notice: string;
  readonly generatedAt: string;
}

export function readBudget(summary: BudgetSummary): PublishedBudget {
  return {
    zoneCode: summary.zone_code,
    fiscalYear: summary.fiscal_year,
    currency: summary.currency,
    allocations: summary.allocations.map((line: BudgetLine) => ({
      fundingSource: line.funding_source,
      allocated: line.allocated_amount,
      spent: line.spent_amount,
      utilisation: figure(line.utilisation_rate, false, 0),
    })),
    notice: summary.notice,
    generatedAt: summary.generated_at,
  };
}

/**
 * Zones worth linking to, in the order a reader wants them.
 *
 * Wards first and then everything else, each alphabetical: the index is a
 * directory, and a directory ordered by whatever the database returned is a
 * directory people scan twice.
 */
export function orderZones(zones: readonly PublishedZone[]): readonly PublishedZone[] {
  const rank = (zone: PublishedZone): number => (zone.zoneKind === "ward" ? 0 : 1);
  return [...zones].sort((a, b) => rank(a) - rank(b) || a.zoneName.localeCompare(b.zoneName, "en"));
}

/**
 * A decimal string, formatted as money without ever becoming a float.
 *
 * `Intl.NumberFormat.format` accepts a decimal *string* at runtime and formats
 * it exactly — no double, no sub-rupee ghost. TypeScript types that overload as
 * `StringNumericLiteral`, a template-literal type it cannot prove an arbitrary
 * string inhabits, so the narrowing has to be earned rather than asserted.
 *
 * This earns it: the shape is checked, and a value that does not match is
 * returned **as it arrived** rather than coerced. §E3.3 — a malformed amount on
 * a public finance page is a fact worth seeing, and `NaN` or a silently dropped
 * digit is not an improvement on it.
 */
const DECIMAL = /^-?\d+(?:\.\d+)?$/;

export function formatAmount(amount: string, format: Intl.NumberFormat): string {
  if (!DECIMAL.test(amount)) return amount;
  return format.format(amount as `${number}`);
}
