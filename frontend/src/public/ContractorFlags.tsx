import { FlaggedNotice } from "@/components/FlaggedNotice";
import { NotWired } from "@/components/NotWired";
import { notTranslatable, t, type Strings, type Translated } from "@/lib/i18n/strings";
import "./public.css";

/**
 * A flagged row on a contractor's public record — §E18, §16.4, §22.2, ADR-0039.
 *
 * > Flagged rows carry the fluorescent hatch, the disclaimer, **and the
 * > contractor's response and appeal status in the same frame** (§16.4). §16.4
 * > ships as a design element, not as a later phase.
 *
 * **"In the same frame" is the whole requirement, and it is why this component
 * exists rather than the page calling `<FlaggedNotice>` directly.** §6
 * Principle #8 says the appeal path ships with the accountability feature; the
 * failure mode it is written against is a flag published in one place and a
 * response published somewhere a reader will not go. So a flag and its response
 * are one component, and the component cannot be constructed without both:
 * `disclaimer` and `responseHref` are required on `<FlaggedNotice>` and
 * required again here, so the M4 contract is exercised on a *public* route
 * rather than only in a catalogue.
 *
 * `tests/fixtures/types/public-flag-without-response.tsx` is the proof: it
 * renders this component without a response href and CI asserts the exact
 * diagnostic.
 *
 * **Nothing is flagged today, and the empty state says so rather than being
 * absent.** The public contract publishes no anomaly rows — §17's integrity
 * signals are Phase 17 — so `flags` is empty on every real response and the
 * component renders the "not asked to respond to anything" sentence. That is
 * the §E3.3 position: the absence is stated, not implied by a missing section a
 * reader cannot distinguish from a section that was never built.
 */
export interface ContractorFlag {
  /** Which detector fired, in the detector's own words (§E19.6). */
  readonly detector: Translated;
  readonly threshold: Translated;
  readonly confidence: Translated;
  readonly appeal?: "pending" | "upheld" | "rejected";
}

export function ContractorFlags({
  flags,
  disclaimer,
  responseHref,
  strings,
}: {
  readonly flags: readonly ContractorFlag[];
  /**
   * `rating_disclaimer` from the response, verbatim.
   *
   * Required, because §22.2's reasoning is that a system-derived figure about a
   * named commercial entity published without its provenance stated is an
   * assertion. Not translated, for the reason `<PublicShell>` gives about
   * `SYSTEM_FLAGGED_NOTICE`: it is the platform's legal text and the frontend
   * does not get to paraphrase it.
   */
  readonly disclaimer: Translated;
  /** Where this contractor's answer lives. Required (§6 Principle #8). */
  readonly responseHref: string;
  readonly strings: Strings;
}) {
  if (flags.length === 0) {
    return (
      <section className="contractor__flags" aria-labelledby="contractor-response">
        <h2 id="contractor-response" className="type-heading">
          {t(strings, "contractor.response")}
        </h2>
        <p className="type-caption">
          {t(strings, "contractor.responseNone")}
          <NotWired phase="Phase 17" strings={strings} />
        </p>
      </section>
    );
  }

  return (
    <section className="contractor__flags" aria-labelledby="contractor-response">
      <h2 id="contractor-response" className="type-heading">
        {t(strings, "contractor.response")}
      </h2>
      {flags.map((flag) => (
        <FlaggedNotice
          key={flag.detector}
          disclaimer={disclaimer}
          responseHref={responseHref}
          strings={strings}
          detector={{
            name: flag.detector,
            threshold: flag.threshold,
            confidence: flag.confidence,
          }}
          {...(flag.appeal === undefined ? {} : { appeal: flag.appeal })}
        />
      ))}
    </section>
  );
}

/**
 * Where a contractor's response to a flag lives.
 *
 * A function rather than a string constant, because the href is per contractor
 * and §6 Principle #8's requirement is that the path *exists*, not that a
 * placeholder does. Today it points at the contractor's own public record,
 * anchored at the response section — which is where the answer will be
 * published when Phase 17 builds the portal that lets them write one. That is a
 * real destination rather than a `#`, and it is the destination the answer will
 * actually appear at, so the link does not move when the portal lands.
 */
export function responseHrefFor(tenant: string, contractorId: string): string {
  return `/${tenant}/contractor/${contractorId}#contractor-response`;
}

/** Exported so the ledger and the flags cannot disagree about the sentence. */
export function asDisclaimer(text: string): Translated {
  return notTranslatable(text);
}
