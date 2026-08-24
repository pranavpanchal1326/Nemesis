import { FlaggedNotice } from "@/components/FlaggedNotice";
import { makeStrings, notTranslatable } from "@/lib/i18n/strings";

// §6 Principle #8 — the appeal path ships with the accountability feature, or
// the accountability feature does not ship.
export const missingResponse = (
  <FlaggedNotice
    disclaimer={notTranslatable("Not a finding.")}
    strings={makeStrings("common", "en", {})}
  />
);
