import { ConsoleShell } from "@/console/ConsoleShell";
import { screenById } from "@/console/screens";
import { devOnly } from "@/lib/dev-only";
import { consoleContext } from "@/server/console-context";
import { Money } from "@/console/roadmap/Money";

/**
 * §E19.5 — ROADMAP (Phase 14, 23).
 *
 * `devOnly()` first, before anything else runs. §E24: a screen whose contract
 * returns nulls today **cannot be routed to a public URL**, and that is a
 * property of the route rather than of the navigation — in a production build
 * this is a 404, and the rail's link to it is not rendered either.
 */
export default async function Page() {
  devOnly();
  const { strings, locale, city } = await consoleContext();
  const screen = screenById("money");
  if (screen === undefined) throw new Error("unknown console screen: money");

  return (
    <ConsoleShell strings={strings} locale={locale} city={city} screen={screen}>
      <Money strings={strings} locale={locale} />
    </ConsoleShell>
  );
}
