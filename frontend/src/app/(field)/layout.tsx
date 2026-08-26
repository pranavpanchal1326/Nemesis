import { headers } from "next/headers";

import { FieldShell } from "@/field/FieldShell";
import { directionOf } from "@/lib/i18n/direction";
import { loadStrings, negotiateLocale, SEEDED_LOCALES } from "@/server/strings";

/**
 * §E21 — the field surface. PWA scope, and its own installable start point.
 *
 * The same shape `(report)/layout.tsx` has, for the same reasons: the locale is
 * negotiated on the server so the first paint is in the right language and the
 * right script, and the client boundary is the shell below it.
 *
 * `<main>` lives inside `<FieldScreen>` rather than here, because the shell
 * renders a bar and a warning above it and two landmarks nested is an `axe`
 * violation on the surface with the least forgiving conditions.
 */
export default async function FieldLayout({ children }: { children: React.ReactNode }) {
  const requestHeaders = await headers();
  const locale = negotiateLocale({
    acceptLanguage: requestHeaders.get("accept-language"),
    available: SEEDED_LOCALES,
  });
  const strings = await loadStrings("common", locale);

  return (
    <div data-surface="field" lang={locale} dir={directionOf(locale)}>
      <FieldShell strings={strings}>{children}</FieldShell>
    </div>
  );
}
