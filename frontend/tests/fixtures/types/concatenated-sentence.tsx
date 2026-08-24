import { DegradedBanner } from "@/components/DegradedBanner";
import { makeStrings, t } from "@/lib/i18n/strings";

const strings = makeStrings("common", "en", {});

// §E10.1 — "never concatenate a sentence from fragments; a sentence is a
// translation unit". Concatenation widens `Translated` back to `string`.
export const concatenated = (
  <DegradedBanner cause={t(strings, "degraded.title") + t(strings, "degraded.since")} strings={strings} />
);
