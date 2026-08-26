import { ClayScene } from "@/clay/ClayScene";
import type { ClayWorld } from "@/server/clay-data";
import { NotWired } from "@/components/NotWired";
import { InkFigure } from "@/ink/InkFigure";
import { plural, t, type Strings } from "@/lib/i18n/strings";

import { ConsolePrint } from "./ConsoleShell";
import { reasonKey, summarise, type QueuePage } from "./review/queue";
import "./console.css";

/**
 * §E19.1 — the command view. The split, the strip, and the queue.
 *
 * > Above it, one strip, and it is *not* vanity metrics …
 * > **7 breach in the next 24 hours.** 2 unassigned. 1 has no contractor
 * > certified for this category.
 * >
 * > A dashboard should tell you what to do before it tells you how you are
 * > doing.
 *
 * **The strip is split in two, and that split is the honest part of this
 * screen.** The sentence the blueprint quotes needs three things the contract
 * does not carry: an SLA deadline (Phase 12), an assignment (Phase 14), and a
 * contractor certification per category (Phase 14). Rendering it from fixtures
 * beside real figures would produce a strip where some numbers are measurements
 * and some are decoration, with nothing on screen saying which — the exact
 * §E3.3 failure, on the one surface whose whole job is to tell an officer what
 * to do first.
 *
 * So: what is real is stated as fact, and the breach line is rendered in its
 * own block with the chip on it. A reviewer reading this screen can tell at a
 * glance which half of it the system actually knows.
 *
 * **The left half of the split is the clay engine, and it landed at M8.** The
 * panel holds `<ClayScene>` — the §E26 A7 contract — which brings its own
 * accessible peer list with it. That list is why the panel is worth its space
 * even on the day a tenant has published no coordinates at all: with no origin
 * there is no canvas, and what remains is a list of the same places with the
 * same figures, which is content rather than a placeholder.
 *
 * **The map shows the city's *published* places** (`server/clay-data.ts`), not
 * a console-only view of them. An officer and a citizen looking at the same
 * ward see the same number, which is the property that makes a conversation
 * about that number possible.
 */
export function CommandView({
  strings,
  page,
  world,
  city,
  children,
}: {
  readonly strings: Strings;
  /** The queue, as the server read it, or `null` when that read failed. */
  readonly page: QueuePage | null;
  /** The places, the frame's origin and the season — `fetchClayWorld()`. */
  readonly world: ClayWorld;
  /** The city's own name, for the sentence naming what the model models. */
  readonly city: string;
  /** The queue itself, rendered by the caller so this component stays a
   *  server component and the client boundary stays where §E14.1 put it. */
  readonly children: React.ReactNode;
}) {
  const summary = summarise(page?.items ?? []);

  return (
    <div className="command">
      <ConsolePrint title={t(strings, "strip.title")}>
        {page === null ? (
          <p className="console__note type-caption">{t(strings, "queue.unavailable")}</p>
        ) : (
          <>
            <p className="type-body">
              {plural(strings, "queue.count", summary.open, { count: summary.open })}
            </p>
            {/*
              §E8.2 puts the Officer in *"console empty states, onboarding,
              'nothing breaches today'"* — and an empty queue is the one state
              this screen reaches that is genuinely good news. `live`, so the
              figure straightens up when a `citizen_confirmed` lands while
              somebody is looking at a quiet queue: the only figure in the
              product that answers to the whole stream rather than to one
              report, because on this screen any confirmation is the officer's.
            */}
            {summary.open === 0 ? (
              <div className="command__quiet">
                <InkFigure
                  strings={strings}
                  figure="officer"
                  className="ink--inline"
                  live
                  fill={0.9}
                />
                <p className="type-caption">{t(strings, "queue.quiet")}</p>
              </div>
            ) : null}
            <ul className="command__reasons">
              {summary.byReason.map(({ reason, count }) => (
                <li key={reason} className="command__reason type-caption">
                  <span className="command__reason-count type-mono-data">{String(count)}</span>
                  {t(strings, reasonKey(reason))}
                </li>
              ))}
            </ul>
          </>
        )}

        {/*
          The line §E19.1 actually quotes, and the reason it is not a number.
          Phase 12 lands the deadline; until then this says what it would say
          and does not pretend to know the answer.
        */}
        <p className="command__breach type-caption">
          <NotWired phase="12" strings={strings} />
          {t(strings, "strip.breachPending")}
        </p>
      </ConsolePrint>

      <ConsolePrint title={t(strings, "map.title")}>
        {world.origin === null && world.entities.length === 0 ? (
          <p className="console__note type-caption">{t(strings, "map.empty")}</p>
        ) : null}
        <ClayScene
          entities={world.entities}
          strings={strings}
          city={city}
          origin={world.origin}
          weather={world.weather}
          // §E9.3's light table. The console is the one surface whose ground is
          // the room rather than the paper, and the press the clay is printed
          // through has to be the same press the rest of the screen runs.
          surface="console-night"
          headingId="command-map-peers"
        />
      </ConsolePrint>

      <ConsolePrint title={t(strings, "queue.title")}>{children}</ConsolePrint>
    </div>
  );
}
