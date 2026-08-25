import { CitizenShell } from "@/citizen/CitizenShell";
import { loadStrings, negotiateLocale, SEEDED_LOCALES } from "@/server/strings";
import { headers } from "next/headers";

/**
 * §E17 — the citizen product. PWA scope (§E21). One thumb, three screens, and
 * the app opens in the viewfinder rather than on a form.
 *
 * **The layout is a server component and it stays one.** Locale negotiation
 * happens here, on the server, for the reason `src/server/strings.ts` gives: a
 * page whose language is chosen after hydration ships the wrong `lang` to a
 * screen reader — and on a Devanagari surface the wrong `lang` means the wrong
 * per-script type scale *and* the wrong voice. `<CitizenShell>` is the client
 * boundary, and it receives resolved strings rather than fetching them.
 *
 * `<main>` is inside the shell rather than here: §E17's three screens each own
 * their own landmark, and two nested `<main>` elements is an `axe` violation on
 * the surface with the least forgiving audience.
 */
export default async function ReportLayout({ children }: { children: React.ReactNode }) {
  const requestHeaders = await headers();
  const locale = negotiateLocale({
    acceptLanguage: requestHeaders.get("accept-language"),
    available: SEEDED_LOCALES,
  });
  const strings = await loadStrings("common", locale);

  return (
    <div data-surface="report" lang={locale}>
      <CitizenShell strings={strings}>{children}</CitizenShell>
    </div>
  );
}
