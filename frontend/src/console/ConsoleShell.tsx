import Link from "next/link";
import type { ReactNode } from "react";

import { DensityControl } from "@/components/DensityControl";
import { SoundControl } from "@/sound/SoundControl";
import { NotWired } from "@/components/NotWired";
import { formatLedgerTime } from "@/lib/i18n/datetime";
import { notTranslatable, t, type Strings } from "@/lib/i18n/strings";

import { CommandPalette } from "./CommandPalette";
import { ConsoleRuntime } from "./ConsoleRuntime";
import { roadmapPhase, SCREENS, SECTIONS, screensIn, type Screen } from "./screens";
import "./console.css";

/**
 * The §E19 shell — the chrome every console screen wears.
 *
 * > Ground `mitti-950`, prints backlit on glass (§E9.3). Switzer at 15 px,
 * > JetBrains Mono for all data, **three density modes** … persisted per user,
 * > command palette on `⌘K`. Dark-first, keyboard-first, dense, and
 * > **printable — print is a first-class target with its own stylesheet**,
 * > because officers print.
 *
 * That paragraph is a specification and this component implements it. Four
 * decisions in it are worth the reader's time:
 *
 * **The rail is generated from `screens.ts`, not written out.** A hand-written
 * rail and a `⌘K` palette are two lists of the same screens, and the day they
 * disagree is the day an officer cannot reach something the palette says
 * exists. One list, four consumers — see that module.
 *
 * **A roadmap screen is in the rail, with its chip.** Hiding the nine unwired
 * screens would make the console look finished, which is precisely the §E3.3
 * failure this product is written against; and §E24's rule is that they cannot
 * be *routed to a public URL*, which is a property of the route (`devOnly()`),
 * not of the navigation. In a production build the chip renders nothing and the
 * route 404s — so what a citizen could ever see is a link that is not there,
 * rather than a screen that lies.
 *
 * **The density control lives here now.** F2 shipped it at the top of the
 * console surface with a comment saying that was temporary and that F3 owned
 * moving it into the chrome beside the palette. This is that move; the control
 * itself is unchanged, and `tests/density.spec.ts` follows it here.
 *
 * **`<main id>` and a skip link.** Thirty tab stops of chrome sit between the
 * top of the document and the screen, and §E22's promise is a full keyboard
 * path rather than a long one.
 */
export function ConsoleShell({
  strings,
  locale,
  city,
  screen,
  children,
}: {
  readonly strings: Strings;
  readonly locale: string;
  /** The tenant's display name. Passed in rather than read here: the shell
   *  serves any tenant and the name is a fact the server already holds. */
  readonly city: string;
  /** Which screen is being rendered. Drives `aria-current`, the heading, the
   *  chip, and the line the printed page carries. */
  readonly screen: Screen;
  readonly children: ReactNode;
}) {
  const phase = roadmapPhase(screen);
  // The page's own generation time, stated as that. Not "printed at": a server
  // render cannot know when somebody reached for the printer, and a sheet that
  // claims a print time it guessed is the sort of small dishonesty §E3.3 is
  // about. What the reader needs is the age of the figures, and that is this.
  const asAt = new Date().toISOString();

  return (
    <div className="console">
      <a className="console__skip type-caption" href="#console-screen">
        {t(strings, "console.skip")}
      </a>

      <ConsoleRuntime strings={strings}>
        <header className="console__masthead" aria-label={t(strings, "console.chrome")}>
          {/* The wordmark is the way out. An officer working a shift moves
              between the console and the field app, and before ADR-0059 the only
              route between them was the address bar — so the masthead, which
              every console screen already carries, is where the staff door
              belongs. */}
          <p className="console__wordmark type-micro">
            <Link href="/staff">{t(strings, "console.masthead")}</Link>
          </p>
          <p className="console__city type-caption">{notTranslatable(city)}</p>
          <div className="console__masthead-end">
            <DensityControl strings={strings} className="console__density" />
            {/* §E12's unmute, *designed rather than hidden* — in the masthead
                beside the density control, because both are the same kind of
                thing: a preference about how this tool behaves for the person
                using it for nine hours. */}
            <SoundControl strings={strings} />
            <CommandPalette strings={strings} />
          </div>
        </header>

        <nav className="console__rail" aria-label={t(strings, "console.nav")}>
          {SECTIONS.map((section) => (
            <section key={section} className="console__section">
              <h2 className="console__section-title type-micro">
                {t(strings, `section.${section}`)}
              </h2>
              <ul className="console__links">
                {screensIn(section).map((item) => (
                  <li key={item.id}>
                    <Link
                      href={item.href}
                      className="console__link type-caption"
                      aria-current={item.id === screen.id ? "page" : undefined}
                    >
                      {t(strings, `nav.${item.id}`)}
                      {roadmapPhase(item) === undefined ? null : (
                        <span className="console__link-roadmap">
                          <NotWired phase={roadmapPhase(item) ?? ""} strings={strings} />
                        </span>
                      )}
                    </Link>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </nav>

        <main className="console__screen" id="console-screen" tabIndex={-1}>
          <div className="console__heading">
            <h1 className="type-heading">{t(strings, `nav.${screen.id}`)}</h1>
            <p className="console__hint type-caption">{t(strings, `nav.${screen.id}.hint`)}</p>
            {phase === undefined ? null : <NotWired phase={phase} strings={strings} />}
          </div>
          {children}
        </main>

        {/*
          Provenance, printed on every sheet and hidden on screen.

          §E19.7's argument about the report builder — *"a report that carries
          its own proof is a category difference from a report that carries a
          logo"* — applies to an ordinary printout of an ordinary queue too, at
          a smaller scale. Which city, which screen, how old the figures are.
        */}
        <footer className="console__provenance type-mono-data">
          <p>
            {t(strings, "console.provenance", {
              city,
              screen: t(strings, `nav.${screen.id}`),
              time: formatLedgerTime(asAt, locale),
            })}
          </p>
          <p>{t(strings, "console.provenanceWhy")}</p>
        </footer>
      </ConsoleRuntime>
    </div>
  );
}

/**
 * A sheet on the light table.
 *
 * Every console screen is made of these, and they exist as one component for
 * the same reason `<ConsoleShell>` reads `screens.ts`: the print stylesheet has
 * exactly one rule to write about page breaks, and it works on every screen
 * including the ones not written yet.
 */
export function ConsolePrint({
  title,
  children,
}: {
  readonly title: ReactNode;
  readonly children: ReactNode;
}) {
  return (
    <section className="console__print">
      <h2 className="console__print-title type-micro">{title}</h2>
      {children}
    </section>
  );
}

/** Every screen the console knows about. Re-exported so a page imports one
 *  module rather than two. */
export { SCREENS };
