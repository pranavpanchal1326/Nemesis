import { describe, expect, it } from "vitest";

import type { components } from "@/generated/api";
import {
  formatAmount,
  orderZones,
  readBudget,
  readContractor,
  readZone,
  type PublishedZone,
} from "@/public/figures";

/**
 * ADR-0021 at the conversion boundary — M6's gate, proved over the mapping.
 *
 * The browser half of this gate is `tests/public.spec.ts`, which asserts that no
 * zero reaches a rendered suppressed place. This half asserts the reason it
 * cannot: `readZone` never produces a `known` figure from a suppressed response,
 * whatever the numbers beside `suppressed` happen to say.
 *
 * That distinction matters because **the backend deliberately sends zeros**.
 * `public/aggregates.py` returns `total_reports: 0` on the suppressed branch —
 * *"publish the shape and the fact of suppression, and no measure at all"* — so
 * the trap is not a hypothetical shape somebody might send one day. It is the
 * shape the server sends today, on the branch this rule exists for.
 */

type ZoneSummary = components["schemas"]["ZoneSummaryResponse"];

const NOTICE = "System-computed from reported data and under human review.";

/** ADR-0052's envelope, identical on every public body. Spelled out once. */
const ENVELOPE = {
  tenant: "t",
  tenant_name: "Testville",
  locale: "en",
  // A2/A11: the language switch is built from the tenant's own list, so the
  // list is part of every envelope now. Three entries, one of them
  // right-to-left, because a single-entry list exercises nothing.
  locales: ["en", "mr", "ar"] as string[],
  // A2/A11: the switch is built from the tenant's own list, so it is part of
  // every envelope now. Two entries and one of them right-to-left, because a
  // one-entry list would exercise nothing.
  notice: NOTICE,
  notice_locale: "en",
  notice_review: "NEMESIS product copy, §22.2",
} as const;

function summary(over: Partial<ZoneSummary> = {}): ZoneSummary {
  return {
    api_version: "v1",
    generated_at: "2026-08-25T05:00:00+00:00",
    ...ENVELOPE,
    zone_code: "W-01",
    zone_name: "Ward One",
    zone_kind: "ward",
    centroid: { lat: 18.52, lng: 73.85 },
    total_reports: 0,
    open_reports: 0,
    resolved_reports: 0,
    auto_confirmed_resolutions: 0,
    resolution_rate: null,
    median_resolution_hours: null,
    by_category: [],
    suppressed: false,
    suppression_threshold: 5,
    count_suppressed_buckets: 0,
    ...over,
  };
}

/** Every figure on a place, so a new one cannot escape the sweep by being new. */
function figuresOf(zone: PublishedZone) {
  return {
    totalReports: zone.totalReports,
    openReports: zone.openReports,
    resolvedReports: zone.resolvedReports,
    autoConfirmedResolutions: zone.autoConfirmedResolutions,
    resolutionRate: zone.resolutionRate,
    medianResolutionHours: zone.medianResolutionHours,
  };
}

describe("ADR-0021 — a suppressed place publishes no measure at all", () => {
  it("turns the backend's zeros into withheld, not into known", () => {
    // The exact body `aggregates.zone_summary` sends below the floor.
    const zone = readZone(
      summary({ suppressed: true, total_reports: 0, count_suppressed_buckets: 1 }),
    );

    for (const [name, figure] of Object.entries(figuresOf(zone))) {
      expect(figure.kind, `${name} escaped suppression`).toBe("withheld");
    }
  });

  it("carries the threshold, so the notice can state it", () => {
    const zone = readZone(summary({ suppressed: true, suppression_threshold: 12 }));
    expect(zone.totalReports).toEqual({ kind: "withheld", threshold: 12 });
  });

  it("suppresses a figure even when the response carries a real number beside the flag", () => {
    /**
     * A defensive case, and worth its own test: nothing in the *contract*
     * forbids `suppressed: true` next to `total_reports: 41`. The backend does
     * not send that today, and a future change, a cache, or a second
     * implementation might. The rule is "suppressed means withheld", not
     * "suppressed usually comes with zeros", and the mapping has to hold the
     * strong version or it is holding a coincidence.
     */
    const zone = readZone(summary({ suppressed: true, total_reports: 41, resolved_reports: 29 }));
    expect(zone.totalReports.kind).toBe("withheld");
    expect(zone.resolvedReports.kind).toBe("withheld");
  });
});

describe("a genuine zero is not a withheld one", () => {
  it("stays known, so the surface can say 'none filed' rather than a notice", () => {
    // `Suppression.hide` is `0 < count < threshold`, so a truly empty place is
    // never suppressed — and must not be reported as though it were.
    const zone = readZone(summary({ suppressed: false, total_reports: 0 }));
    expect(zone.totalReports).toEqual({ kind: "known", value: 0 });
  });

  it("distinguishes a rate that was never computed from one that was withheld", () => {
    const quiet = readZone(summary({ total_reports: 9, resolution_rate: null }));
    expect(quiet.resolutionRate.kind).toBe("unknown");

    const hidden = readZone(summary({ suppressed: true, resolution_rate: null }));
    expect(hidden.resolutionRate.kind).toBe("withheld");
  });
});

describe("the category breakdown", () => {
  it("keeps the count of buckets held back, so the shortfall can be explained", () => {
    const zone = readZone(
      summary({
        total_reports: 41,
        by_category: [{ category: "pothole_or_road_damage", category_name: "Potholes", count: 22 }],
        count_suppressed_buckets: 3,
      }),
    );
    expect(zone.suppressedBuckets).toBe(3);
    // A bucket that reached the array is above the floor by construction —
    // `_category_breakdown` drops the others rather than zeroing them.
    expect(zone.byCategory[0]?.count).toEqual({ kind: "known", value: 22 });
  });
});

describe("centroids", () => {
  it("treats a null member as no centroid rather than as zero degrees", () => {
    // 0,0 is in the Gulf of Guinea. A ward rendered there is a defect that
    // looks like data, which is the kind this whole module exists to refuse.
    expect(readZone(summary({ centroid: { lat: null, lng: 73.85 } })).centroid).toBeNull();
    expect(readZone(summary({ centroid: null })).centroid).toBeNull();
  });
});

describe("contractors — §16.1, a ledger and never a rating", () => {
  it("publishes no field a caller could read as an overall score", () => {
    /**
     * The mechanism §16.1 relies on is that *the API does not publish a
     * collapsible number*. Asserted against the generated schema rather than
     * against a hand-written list, so the day somebody adds `rating` the
     * argument happens before it ships rather than after.
     */
    const profile = readContractor({
      api_version: "v1",
      generated_at: "2026-08-25T05:00:00+00:00",
      ...ENVELOPE,
      rating_disclaimer: "A track record, not a score.",
      rating_disclaimer_locale: "en",
      rating_disclaimer_review: "NEMESIS product copy, §16.1",
      locales: ["en", "mr", "ar"],
      contractor_id: "00000000-0000-0000-0000-000000000001",
      contractor_name: "Acme",
      registration_id: "REG-1",
      active_since: null,
      work_orders_completed: 4,
      work_orders_open: 1,
      on_time_rate: 0.75,
      disputed_count: 0,
      certified_categories: [],
      suppressed: false,
      suppression_threshold: 5,
    });

    expect(Object.keys(profile)).not.toContain("rating");
    expect(Object.keys(profile)).not.toContain("score");
    expect(profile.ratingDisclaimer).toBe("A track record, not a score.");
  });

  it("withholds every metric when the profile is below the floor", () => {
    const profile = readContractor({
      api_version: "v1",
      generated_at: "2026-08-25T05:00:00+00:00",
      ...ENVELOPE,
      rating_disclaimer: "A track record, not a score.",
      rating_disclaimer_locale: "en",
      rating_disclaimer_review: "NEMESIS product copy, §16.1",
      locales: ["en", "mr", "ar"],
      contractor_id: "00000000-0000-0000-0000-000000000001",
      contractor_name: "Acme",
      registration_id: "REG-1",
      active_since: null,
      work_orders_completed: 0,
      work_orders_open: 0,
      on_time_rate: null,
      disputed_count: 0,
      certified_categories: ["roads.pothole"],
      suppressed: true,
      suppression_threshold: 5,
    });

    expect(profile.completed.kind).toBe("withheld");
    expect(profile.disputed.kind).toBe("withheld");
    // A certification is a capability, not a measure about anybody, so it is
    // not withheld — the same reasoning that leaves budget lines unsuppressed.
    expect(profile.certifiedCategories).toEqual(["roads.pothole"]);
  });
});

describe("money — §17.2, and never a float", () => {
  it("keeps amounts as the strings the API sent", () => {
    const budget = readBudget({
      api_version: "v1",
      generated_at: "2026-08-25T05:00:00+00:00",
      ...ENVELOPE,
      zone_code: "W-01",
      fiscal_year: "2026-27",
      currency: "INR",
      allocations: [
        {
          funding_source: "15th Finance Commission",
          allocated_amount: "12500000.75",
          spent_amount: "9990000.10",
          utilisation_rate: 0.799,
        },
      ],
    });

    const line = budget.allocations[0];
    expect(line?.allocated).toBe("12500000.75");
    expect(typeof line?.allocated).toBe("string");
  });

  it("formats a decimal string without routing it through a double", () => {
    const inr = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR" });
    const exact = "9007199254740993.75";

    // More precision than a double carries: `Number("9007199254740993.75")` is
    // 9007199254740992, and the .75 is gone before anything formats it. Given
    // the string, `Intl` keeps every digit — which is why `readBudget` never
    // parses an amount and why this asserts the *digits* rather than a grouping
    // pattern that varies by locale.
    const digits = formatAmount(exact, inr).replace(/[^\d]/g, "");
    expect(digits).toBe(exact.replace(".", ""));
    expect(String(Number(exact))).not.toBe(exact);
  });

  it("renders a malformed amount as itself rather than as NaN", () => {
    const inr = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR" });
    expect(formatAmount("not-a-number", inr)).toBe("not-a-number");
  });

  it("never withholds a budget line", () => {
    // `aggregates.budget_summary`: "withholding a line because only one scheme
    // funded a ward would hide precisely the thing an RTI applicant is looking
    // for." A single-source year must publish.
    const budget = readBudget({
      api_version: "v1",
      generated_at: "2026-08-25T05:00:00+00:00",
      ...ENVELOPE,
      zone_code: "W-01",
      fiscal_year: "2026-27",
      currency: "INR",
      allocations: [
        {
          funding_source: "Ward fund",
          allocated_amount: "100.00",
          spent_amount: "40.00",
          utilisation_rate: 0.4,
        },
      ],
    });
    expect(budget.allocations[0]?.utilisation.kind).toBe("known");
  });
});

describe("the place index is a directory, not a league table", () => {
  it("orders wards first, then alphabetically, and never by count", () => {
    const zones = [
      readZone(summary({ zone_code: "CITY", zone_name: "Pune", zone_kind: "city" })),
      readZone(summary({ zone_code: "W-B", zone_name: "Bhavani", total_reports: 2 })),
      readZone(summary({ zone_code: "W-A", zone_name: "Aundh", total_reports: 400 })),
    ];

    expect(orderZones(zones).map((zone) => zone.zoneCode)).toEqual(["W-A", "W-B", "CITY"]);
  });
});
