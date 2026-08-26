import { ConsoleShell } from "@/console/ConsoleShell";
import { screenById } from "@/console/screens";
import { devOnly } from "@/lib/dev-only";
import { consoleContext } from "@/server/console-context";
import { WorkOrder } from "@/console/roadmap/WorkOrder";

/**
 * §E19.3 — ROADMAP (Phase 14).
 *
 * `devOnly()` first, before anything else runs. §E24: a screen whose contract
 * returns nulls today **cannot be routed to a public URL**, and that is a
 * property of the route rather than of the navigation — in a production build
 * this is a 404, and the rail's link to it is not rendered either.
 */
export default async function Page() {
  devOnly();
  const { strings, locale, city } = await consoleContext();
  const screen = screenById("work");
  if (screen === undefined) throw new Error("unknown console screen: work");

  return (
    <ConsoleShell strings={strings} locale={locale} city={city} screen={screen}>
      <WorkOrder strings={strings} locale={locale} />
    </ConsoleShell>
  );
}
