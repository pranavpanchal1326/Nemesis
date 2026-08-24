import { SeverityBadge } from "@/components/SeverityBadge";
import { makeStrings } from "@/lib/i18n/strings";

// `level` is required and `null` is a real value — "unscored" — so omitting it
// is not the same as passing null, and does not compile.
export const noLevel = <SeverityBadge strings={makeStrings("common", "en", {})} />;
