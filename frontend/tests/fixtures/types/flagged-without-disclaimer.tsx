import { FlaggedNotice } from "@/components/FlaggedNotice";
import { makeStrings } from "@/lib/i18n/strings";

// §16.4, §22.2 — a flag rendered without a disclaimer does not compile.
export const missingDisclaimer = (
  <FlaggedNotice responseHref="/response" strings={makeStrings("common", "en", {})} />
);
