import { makeStrings, notTranslatable } from "@/lib/i18n/strings";
import { ContractorFlags } from "@/public/ContractorFlags";

// §22.2 — a system-derived figure about a named commercial entity, published
// without its provenance stated, is an assertion. The disclaimer is a required
// field upstream and a required prop here, on the public surface too.
export const flagWithoutDisclaimer = (
  <ContractorFlags
    flags={[
      {
        detector: notTranslatable("Rate-card deviation"),
        threshold: notTranslatable("> 18% over schedule"),
        confidence: notTranslatable("0.82"),
      },
    ]}
    responseHref="/pune-demo/contractor/abc#contractor-response"
    strings={makeStrings("public", "en", {})}
  />
);
