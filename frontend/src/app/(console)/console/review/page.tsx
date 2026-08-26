import { ConsoleShell } from "@/console/ConsoleShell";
import { ReviewQueue } from "@/console/review/ReviewQueue";
import { screenById } from "@/console/screens";
import { consoleContext } from "@/server/console-context";
import { fetchQueue } from "@/server/review-data";

/**
 * §E19.1 — the review queue. **REAL**: `/api/v1/review` is fully shipped,
 * media included.
 *
 * No `devOnly()`. This screen is backed end to end, and marking it dev-only
 * would be as dishonest in the other direction as shipping a fixture would be
 * in this one.
 *
 * The first page is read here, on the server, so the queue is present before
 * hydration — at Tier D, in the Lighthouse run, and to a screen reader in the
 * first second. `<ReviewQueue>` takes over afterwards.
 */
export default async function Page() {
  const { strings, locale, city } = await consoleContext();
  const screen = screenById("review");
  if (screen === undefined) throw new Error("unknown console screen: review");

  const read = await fetchQueue();

  return (
    <ConsoleShell strings={strings} locale={locale} city={city} screen={screen}>
      <ReviewQueue strings={strings} locale={locale} initial={read.ok ? read.page : null} />
    </ConsoleShell>
  );
}
