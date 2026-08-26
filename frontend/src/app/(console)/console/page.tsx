import { CommandView } from "@/console/CommandView";
import { ConsoleShell } from "@/console/ConsoleShell";
import { ReviewQueue } from "@/console/review/ReviewQueue";
import { screenById } from "@/console/screens";
import { fetchClayWorld } from "@/server/clay-data";
import { consoleContext } from "@/server/console-context";
import { fetchQueue } from "@/server/review-data";
import { resolveTenant } from "@/server/upstream";

/**
 * §E19.1 — command. Where an officer starts a shift.
 *
 * The queue is REAL and it is the same component the review screen renders, not
 * a second copy sized for a dashboard. Two implementations of one work list is
 * how the two start disagreeing about what is open.
 *
 * The map is REAL as of M8, and the two reads are issued together: a console
 * that waited for its city before showing its queue would make the least
 * important half of the screen decide when the most important half arrives.
 */
export default async function Page() {
  const { strings, locale, city } = await consoleContext();
  const screen = screenById("command");
  if (screen === undefined) throw new Error("unknown console screen: command");

  const [read, world] = await Promise.all([
    fetchQueue(),
    fetchClayWorld(resolveTenant() ?? "", locale),
  ]);
  const page = read.ok ? read.page : null;

  return (
    <ConsoleShell strings={strings} locale={locale} city={city} screen={screen}>
      <CommandView strings={strings} page={page} world={world} city={city}>
        <ReviewQueue strings={strings} locale={locale} initial={page} />
      </CommandView>
    </ConsoleShell>
  );
}
