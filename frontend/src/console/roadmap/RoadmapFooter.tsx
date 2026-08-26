import Link from "next/link";

import { t, type Strings } from "@/lib/i18n/strings";
import { roadmapPhase, screenById } from "@/console/screens";

import "./roadmap.css";

/**
 * How a roadmap screen ends — §E24, §E3.3.
 *
 * **The problem this solves is that the screens looked abandoned rather than
 * unfinished, and those are different things.** Nine of the console's twelve
 * screens are built against generated types with fixture values behind the §E24
 * chip; the chip says *not wired*, `<FixtureNotice>` says what that means, and
 * then the screen simply stopped — two or three cards at the top of a
 * nine-hundred-pixel void. A reader's honest conclusion from that layout is
 * "this is broken", which is the one thing it is not.
 *
 * So every roadmap screen closes with the same three facts, and every one of
 * them is read from something that already exists rather than written per
 * screen:
 *
 *   · **which phase populates it** — `console/screens.ts`, the same registry
 *     that feeds the rail, the palette, the route guard and the chip;
 *   · **which §E19 subsection it implements** — that registry's `traces`, so
 *     the screen names its own specification;
 *   · **where the same fact can be read today, if anywhere at all.** That last
 *     clause is the one worth arguing about, and the answer is a short map
 *     below with a comment per entry. Most of these screens have no published
 *     equivalent, and those render nothing rather than a link to something
 *     adjacent — a "see also" that does not see the same thing is worse than
 *     silence.
 *
 * Not a component that makes a screen look finished. A component that makes an
 * unfinished screen say so in the same words every time, at the bottom, where a
 * reader who has scrolled past the fixtures is asking the question.
 */

/**
 * Where the *same* fact is published today.
 *
 * Deliberately sparse. A link belongs here only when the public surface answers
 * the question this screen exists to answer — not when it is merely nearby.
 */
const PUBLISHED_TODAY: Readonly<Record<string, (tenant: string) => string>> = {
  // §E19.3's area view is a ward over time. §E18's ward page is the same ward,
  // published, with the same suppression applied — fewer fields, same subject.
  area: (tenant) => `/${tenant}/ward/W-AUNDH`,
  // §E19.5's money screen is allocation against spend. `/budget/{code}` is that
  // figure as a city publishes it (§26.4), which is the half that exists.
  money: (tenant) => `/${tenant}/budget/CITY`,
};

export function RoadmapFooter({
  screenId,
  strings,
  tenant,
}: {
  /** The `screens.ts` id. Everything else is looked up from it, so a screen
   *  cannot describe itself as a different screen. */
  readonly screenId: string;
  readonly strings: Strings;
  /** The published slug, or `null` where this deployment publishes nothing —
   *  in which case the "published today" line is absent rather than broken. */
  readonly tenant: string | null;
}) {
  const screen = screenById(screenId);
  if (screen === undefined) return null;

  const phase = roadmapPhase(screen);
  // A real screen has nothing to say here: its contract is populated, and a
  // footer explaining what will arrive would be describing the present tense.
  if (phase === undefined) return null;

  const published = PUBLISHED_TODAY[screenId];
  const href = published === undefined || tenant === null ? null : published(tenant);

  return (
    <footer className="roadmap-footer" aria-label={t(strings, "roadmap.what")}>
      <h2 className="roadmap-footer__title type-micro">{t(strings, "roadmap.what")}</h2>
      <p className="type-caption">{t(strings, "roadmap.phaseLine", { phase })}</p>
      <p className="roadmap-footer__traces type-micro">
        {t(strings, "roadmap.traces", { section: screen.traces })}
      </p>
      {href === null ? null : (
        <p className="type-caption">
          <Link href={href}>{t(strings, "roadmap.until")}</Link>
        </p>
      )}
    </footer>
  );
}
