import { DegradedBanner } from "@/components/DegradedBanner";
import { makeStrings } from "@/lib/i18n/strings";

// Phase 18's gate: a locale added in the control plane appears with no code
// change. A literal in a component makes that impossible, so it does not
// compile.
export const literal = (
  <DegradedBanner cause="The socket is down." strings={makeStrings("common", "en", {})} />
);
