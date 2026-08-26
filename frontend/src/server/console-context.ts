import "server-only";

import { headers } from "next/headers";

import { cityNameFallback, publishedTenant } from "@/server/public-data";
import { loadStrings, negotiateLocale, SEEDED_LOCALES } from "@/server/strings";
import { resolveTenant } from "@/server/upstream";
import type { Strings } from "@/lib/i18n/strings";

/**
 * What every console screen needs before it renders anything — §E14.1, §E19.
 *
 * Locale, strings and the city's name, resolved once per request on the server.
 * A helper rather than a layout, because a Next layout cannot know which screen
 * is below it and `<ConsoleShell>` needs to (for `aria-current`, the heading
 * and the printed provenance line). So the layout owns the surface attributes
 * and each page owns its shell — and this function is what stops that being
 * twelve copies of the same four lines.
 *
 * **Two namespaces, `common` last is wrong here — `console` is.** `loadStrings`
 * merges later over earlier, and the surface's own namespace goes last so a
 * console-specific word for a shared key wins. Severity labels, statuses and
 * the degraded banner's sentences live in `common` because they are the same
 * sentences the citizen sees; the queue's own vocabulary lives in `console`.
 */
export interface ConsoleContext {
  readonly locale: string;
  readonly strings: Strings;
  readonly city: string;
}

export async function consoleContext(): Promise<ConsoleContext> {
  const requestHeaders = await headers();
  const locale = negotiateLocale({
    acceptLanguage: requestHeaders.get("accept-language"),
    available: SEEDED_LOCALES,
  });
  const strings = await loadStrings(["common", "console"], locale);

  return { locale, strings, city: cityName() };
}

/**
 * The tenant's name, for the masthead and the printed sheet.
 *
 * **Derived from the slug, and that is a gap rather than a design.** ADR-0052
 * gave every *public* response a reviewed `tenant_name`, and every public
 * surface uses it. The control plane publishes no read endpoint for a tenant —
 * `/api/v1/control-plane/tenants` is `POST` only — so the console, which serves
 * tenants that have not published anything and may never, has nothing
 * authoritative to ask.
 *
 * Reading it off the *public* index instead would be worse in a specific way:
 * it would make the console's masthead depend on a publication decision
 * (ADR-0046) that has nothing to do with whether officers can work today, and
 * an unpublished city would render a console with no name on it.
 *
 * So: title-cased slug, in one place, marked. F6 provisions tenants through
 * this surface and is the phase that should add the read side; this comment is
 * the note it should find.
 */
function cityName(): string {
  // **The published slug first, and this is a bug fix.** `resolveTenant()`
  // returns the tenant *id* — an opaque UUID (ADR-0040) — and title-casing one
  // produced the masthead this console actually shipped with:
  // *"672c8898 6103 49d2 B45d C04932c03873"*, on every screen, beside the
  // wordmark, where a city's name goes. It also reached the clay layer's own
  // caption, so the model announced itself as a model of a UUID.
  //
  // A slug is a name a city chose (ADR-0046) and is the right thing to
  // title-case. An id is not a name at all, and `cityNameFallback` now refuses
  // to dress one up as one.
  const published = publishedTenant();
  if (published !== undefined) return cityNameFallback(published);
  return cityNameFallback(resolveTenant() ?? "");
}
