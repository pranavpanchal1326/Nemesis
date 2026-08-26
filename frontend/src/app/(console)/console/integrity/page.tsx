import { ConsoleShell } from "@/console/ConsoleShell";
import { RoadmapFooter } from "@/console/roadmap/RoadmapFooter";
import { screenById } from "@/console/screens";
import { devOnly } from "@/lib/dev-only";
import { consoleContext } from "@/server/console-context";
import { publishedTenant } from "@/server/public-data";
import { Integrity } from "@/console/roadmap/Integrity";

/**
 * §E19.6 — ROADMAP (Phase 17).
 *
 * `devOnly()` first, before anything else runs. §E24: a screen whose contract
 * returns nulls today **cannot be routed to a public URL**, and that is a
 * property of the route rather than of the navigation — in a production build
 * this is a 404, and the rail's link to it is not rendered either.
 */
export default async function Page() {
  devOnly();
  const { strings, locale, city } = await consoleContext();
  const screen = screenById("integrity");
  if (screen === undefined) throw new Error("unknown console screen: integrity");

  return (
    <ConsoleShell strings={strings} locale={locale} city={city} screen={screen}>
      <Integrity strings={strings} locale={locale} />
      <RoadmapFooter screenId="integrity" strings={strings} tenant={publishedTenant() ?? null} />
    </ConsoleShell>
  );
}
