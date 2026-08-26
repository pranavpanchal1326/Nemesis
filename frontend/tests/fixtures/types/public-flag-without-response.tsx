import { makeStrings, notTranslatable } from "@/lib/i18n/strings";
import { ContractorFlags } from "@/public/ContractorFlags";

// §E18, §16.4 — "flagged rows carry the hatch, the disclaimer, **and the
// contractor's response and appeal status in the same frame**". M4 proved the
// contract in a catalogue; this proves it on the composition a *public* route
// actually renders, which is where §6 Principle #8's failure would land.
export const flagWithoutResponse = (
  <ContractorFlags
    flags={[
      {
        detector: notTranslatable("Rate-card deviation"),
        threshold: notTranslatable("> 18% over schedule"),
        confidence: notTranslatable("0.82"),
      },
    ]}
    disclaimer={notTranslatable("A track record, not a score.")}
    strings={makeStrings("public", "en", {})}
  />
);
