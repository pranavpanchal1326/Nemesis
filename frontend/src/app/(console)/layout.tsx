import { headers } from "next/headers";

import { directionOf } from "@/lib/i18n/direction";
import { negotiateLocale, SEEDED_LOCALES } from "@/server/strings";

/**
 * §E19 — "The Light Table". Dark-first, keyboard-first, dense, printable.
 *
 * **The layout carries the surface and nothing else.** `data-surface="console"`
 * is what switches the semantic role tokens to the dark ground (§E9.3), and
 * `lang` / `dir` have to sit on one element so the two cannot disagree (A11).
 * Everything with an opinion about the *screen* — the rail's current item, the
 * heading, the chip, the line the printed sheet carries — lives in
 * `<ConsoleShell>`, which each page renders itself.
 *
 * That split is forced by Next rather than chosen: a layout does not know which
 * page is below it, and a shell that guessed from `usePathname()` would be a
 * client component wrapping every server-rendered screen in the console. The
 * cost is one `<ConsoleShell>` line per page; the alternative was the whole
 * console on the client.
 *
 * **The density control moved.** F2 mounted it here temporarily, at the top of
 * the surface, with a comment saying F3 owned putting it in the §E19 chrome
 * beside the palette. It is there now — see `<ConsoleShell>`'s masthead.
 */
export default async function ConsoleLayout({ children }: { children: React.ReactNode }) {
  const requestHeaders = await headers();
  const locale = negotiateLocale({
    acceptLanguage: requestHeaders.get("accept-language"),
    available: SEEDED_LOCALES,
  });

  return (
    <div data-surface="console" lang={locale} dir={directionOf(locale)}>
      {children}
    </div>
  );
}
