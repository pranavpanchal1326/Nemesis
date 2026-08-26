import { headers } from "next/headers";

import { directionOf } from "@/lib/i18n/direction";
import { negotiateLocale, SEEDED_LOCALES } from "@/server/strings";

/**
 * The two front doors — §E14.4.
 *
 * A sixth route group, and it carries no shell of its own: a door is not a
 * surface with a posture, it is the choice of which posture you are about to
 * enter. So this layout does what every other group's layout does and nothing
 * more — negotiate the locale on the server, put `lang` and `dir` on one
 * element so the two cannot disagree (A11), and name the surface so the
 * semantic role tokens resolve (§E9.3).
 *
 * **`data-surface="portal"` grounds on paper, for both doors.** The staff door
 * could have worn the console's light table, and it deliberately does not: an
 * officer arriving at a door has not started a shift yet, and §E9.3's dark
 * ground is a working condition rather than a brand. The console is still dark
 * the moment they are inside it.
 */
export default async function PortalLayout({ children }: { children: React.ReactNode }) {
  const requestHeaders = await headers();
  const locale = negotiateLocale({
    acceptLanguage: requestHeaders.get("accept-language"),
    available: SEEDED_LOCALES,
  });

  return (
    <div data-surface="portal" lang={locale} dir={directionOf(locale)}>
      {children}
    </div>
  );
}
