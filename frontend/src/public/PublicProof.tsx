import { shippedStrings } from "@/lib/i18n/bundles";
import { notTranslatable } from "@/lib/i18n/strings";
import { asDisclaimer, ContractorFlags, responseHrefFor } from "./ContractorFlags";
import { Figure, LabelledFigure } from "./Figure";
import { readZone, type PublishedZone } from "./figures";
import { PublicShell } from "./PublicShell";
import { ZonePanel } from "./ZonePanel";
import "./public.css";

/**
 * Every §E18 state, on one screen — the M6 gate's fixture.
 *
 * **Why a proof surface rather than an assertion against the live stack.** M6's
 * gate is *"a k-anonymity hole never renders as a zero"*, and the only way to
 * see that on a real ward page is to have a ward with between one and four
 * reports in it. That is a state the demo city cannot be put into on demand:
 * suppression is decided by the backend from data the pipeline produces, so an
 * E2E that waited for it would be asserting against whatever happened to be in
 * the database that morning — green on a Tuesday and vacuous on a Wednesday.
 *
 * So the four states are constructed here from **the generated response type**,
 * with fixture values and no fixture shape, and driven through exactly the
 * components the real pages use. `<ZonePanel>` and `<Figure>` do not know they
 * are being proved. That is the same position §E24 takes for every ROADMAP
 * screen: build against the real contract, never against an invented one.
 *
 * Dev-only, per §E24 — a proof surface is not a public URL, and the route calls
 * `devOnly()`.
 */

/**
 * A response body, typed as the contract types it.
 *
 * `readZone` takes `ZoneSummaryResponse`, so this function's return type is
 * checked against the published schema: a field the backend renames breaks this
 * fixture on the next `nem web-types`, which is the whole reason it is written
 * this way rather than as a hand-built `PublishedZone`.
 */
const NOTICE: Readonly<Record<string, string>> = {
  en:
    "System-computed from reported data and under human review. Figures are not " +
    "verified findings and must not be presented as proven fact.",
  mr:
    "नोंदवलेल्या माहितीवरून प्रणालीने काढलेले आकडे; मानवी पडताळणी सुरू आहे. " +
    "हे आकडे पडताळलेले निष्कर्ष नाहीत आणि त्यांना सिद्ध तथ्य म्हणून मांडू नये.",
};

/** The locales this proof surface stands a page up in.
 *
 *  `ar` is here for A11 and carries no Arabic copy: the demo tenant seeds it as
 *  *data* rather than as a product language, so the frame runs right to left
 *  around English words. That is the condition the RTL assertion needs, and it
 *  is all this deployment can honestly claim. */
const PROOF_LOCALES: readonly string[] = ["en", "mr", "ar"];

function response(locale: string, over: Partial<Parameters<typeof readZone>[0]>) {
  // ADR-0052: the notice is served by the API in the locale the page asked for,
  // and the surface renders it verbatim. These two strings are the fixture's
  // copy of what `nemesis.public.notices` sends — a *value*, like every other
  // value here. The claim that the server actually sends them is asserted
  // against the live stack in `tests/public.spec.ts`, because a fixture cannot
  // prove what a server does.
  const served = locale in NOTICE ? locale : "en";
  return readZone({
    api_version: "v1",
    generated_at: "2026-08-25T05:00:00+00:00",
    tenant: "proof",
    tenant_name: "Proof City",
    locale,
    locales: [...PROOF_LOCALES],
    notice: NOTICE[served] ?? "",
    notice_locale: served,
    notice_review: served === "en" ? "NEMESIS product copy, §22.2" : "unreviewed",
    zone_code: "PROOF",
    zone_name: "Proof",
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
  });
}

/**
 * The four states, and the one that is the gate.
 *
 * `withheld` is the trap: the backend sends `total_reports: 0` **with**
 * `suppressed: true` — *"publish the shape and the fact of suppression, and no
 * measure at all"* — so a surface that reached for the number would print a
 * zero about a ward with four reports. The assertion in `tests/public.spec.ts`
 * is that no `0` appears anywhere in this case's figures.
 */
function cases(locale: string): readonly { readonly id: string; readonly zone: PublishedZone }[] {
  return [
    {
      id: "withheld",
      zone: response(locale, {
        zone_code: "WITHHELD",
        zone_name: "Below the floor",
        suppressed: true,
        // Exactly what `aggregates.zone_summary` sends on this branch. Written
        // out rather than defaulted, so the trap is visible in the fixture.
        total_reports: 0,
        open_reports: 0,
        resolved_reports: 0,
        count_suppressed_buckets: 1,
      }),
    },
    {
      id: "genuine-zero",
      zone: response(locale, { zone_code: "QUIET", zone_name: "Genuinely quiet" }),
    },
    {
      id: "populated",
      zone: response(locale, {
        zone_code: "BUSY",
        zone_name: "Above the floor",
        total_reports: 41,
        open_reports: 12,
        resolved_reports: 29,
        auto_confirmed_resolutions: 7,
        resolution_rate: 0.7073,
        median_resolution_hours: 62.5,
        by_category: [
          {
            category: "pothole_or_road_damage",
            category_name: "pothole_or_road_damage",
            count: 22,
          },
          { category: "streetlight_outage", category_name: "streetlight_outage", count: 12 },
        ],
        // Three categories were dropped below the floor, so the visible rows sum
        // to less than the total above them. The sentence that explains the
        // shortfall is the assertion this case exists for.
        count_suppressed_buckets: 3,
      }),
    },
    {
      id: "unmeasured",
      zone: response(locale, {
        zone_code: "NEW",
        zone_name: "Nothing resolved yet",
        total_reports: 9,
        open_reports: 9,
        resolved_reports: 0,
        resolution_rate: null,
        median_resolution_hours: null,
        by_category: [{ category: "water_supply", category_name: "water_supply", count: 9 }],
      }),
    },
  ];
}

export function PublicProof({ locale }: { readonly locale: string }) {
  const strings = shippedStrings(["common", "public"], locale);
  const CASES = cases(locale);
  const first = CASES[0];

  return (
    <PublicShell
      city="Proof"
      citySlug="proof"
      strings={strings}
      locale={locale}
      locales={PROOF_LOCALES}
      generatedAt="2026-08-25T05:00:00+00:00"
      // Embedded in the developer surface, which owns the page's landmark.
      landmark="div"
      notice={first === undefined ? "" : first.zone.notice}
    >
      {CASES.map(({ id, zone }) => (
        <section key={id} data-proof-case={id}>
          <h2 className="type-heading">{notTranslatable(zone.zoneName)}</h2>
          <ZonePanel zone={zone} strings={strings} />
        </section>
      ))}

      {/*
       * The flagged frame, with a flag in it — the one state no real response
       * produces today, because §17's integrity signals are Phase 17. Built
       * from `ContractorFlag`, which is a view model over what a detector
       * reports rather than a re-declaration of a backend contract, so this is
       * a fixture *value* and not a fixture *shape*.
       */}
      <section data-proof-case="flagged">
        <ContractorFlags
          strings={strings}
          disclaimer={asDisclaimer(
            "A track record, not a score. NEMESIS does not collapse a contractor to a single rating (§16.1).",
          )}
          responseHref={responseHrefFor("proof", "00000000-0000-0000-0000-000000000001")}
          flags={[
            {
              detector: notTranslatable("Rate-card deviation"),
              threshold: notTranslatable("> 18% over schedule"),
              confidence: notTranslatable("0.82"),
              appeal: "pending",
            },
          ]}
        />
      </section>

      {/* Each figure state once more, standalone, so a failure points at the
          rendering rather than at the panel that composed it. */}
      <section data-proof-case="figures">
        <dl className="zone-panel__figures">
          <LabelledFigure
            label={notTranslatable("known")}
            figure={{ kind: "known", value: 41 }}
            strings={strings}
          />
          <LabelledFigure
            label={notTranslatable("zero")}
            figure={{ kind: "known", value: 0 }}
            strings={strings}
          />
          <LabelledFigure
            label={notTranslatable("withheld")}
            figure={{ kind: "withheld", threshold: 5 }}
            strings={strings}
          />
          <LabelledFigure
            label={notTranslatable("unknown")}
            figure={{ kind: "unknown" }}
            strings={strings}
          />
        </dl>
        <p>
          <Figure figure={{ kind: "known", value: 0.7073 }} strings={strings} format="percent" />
        </p>
      </section>
    </PublicShell>
  );
}
