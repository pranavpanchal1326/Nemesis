import Link from "next/link";
import { Fragment, type ReactNode } from "react";

import { NotWired } from "@/components/NotWired";
import { t, type Strings, type Translated } from "@/lib/i18n/strings";

import type { DestinationGroup } from "./destinations";
import { PortalPlate } from "./PortalPlate";
import { BackLink, Wordmark } from "./Wordmark";
import "./portal.css";

/**
 * A front door — §E14.4, §E3.3.
 *
 * One component for both audiences, because the two doors differ in *what is
 * behind them* and in nothing else: same ground, same type scale, same chip,
 * same landmark structure. Two components would be two chances for the staff
 * door to quietly become the better-designed one, and the surface that gets
 * less attention here is the one a resident sees first.
 *
 * **A server component with no interactivity, deliberately.** §E13's Tier D is
 * *"JS disabled, crawler, 2G — semantic article, all copy present"*. A page
 * whose entire job is to point at other pages has no excuse for needing a
 * bundle to do it: everything below is `<a>`, `<h2>` and text, so the door
 * works on the tier where somebody is most likely to be standing in the street
 * on a bad connection.
 *
 * **The chip is not decoration and it is not hidden.** A destination whose data
 * is a fixture carries the same §E24 chip the screen itself carries, in
 * development, and renders nothing in production where the route 404s anyway
 * (`devOnly()`). What a citizen can reach from here is what actually answers.
 */
/**
 * How many destinations the groups before this one held.
 *
 * The numbering runs across the whole door rather than restarting per section,
 * because that is what a table of contents does and because the staff door has
 * six sections — six little "01"s down one page reads as six lists, which is
 * precisely the impression `destinations.ts` exists to prevent.
 */
function offsetOf(groups: readonly DestinationGroup[], upTo: number): number {
  let total = 0;
  for (let i = 0; i < upTo; i += 1) total += groups[i]?.items.length ?? 0;
  return total;
}

/** Zero-padded to two, so a column of numerals aligns on the same stem. */
function indexLabel(n: number): string {
  return n < 10 ? `0${String(n)}` : String(n);
}

export function PortalHome({
  strings,
  audience,
  groups,
  children,
  footer,
}: {
  readonly strings: Strings;
  /** `citizen` or `staff` — the string key half of `portal.<audience>.title`
   *  and the `data-portal` attribute the stylesheet grounds itself from. */
  readonly audience: "citizen" | "staff";
  readonly groups: readonly DestinationGroup[];
  /** Anything the audience needs *inside* the page rather than beside it — the
   *  citizen door's receipt field is the only one today. */
  readonly children?: ReactNode;
  /** The other door, and the honesty table. Passed in because the citizen page
   *  and the staff page point at different things and neither should be
   *  guessing what the other offers. */
  readonly footer?: ReactNode;
}) {
  return (
    <div className="portal" data-portal={audience}>
      <a className="portal__skip type-caption" href="#portal-main">
        {t(strings, "portal.skip")}
      </a>

      {/*
        The masthead. A rule, a wordmark, the title, and a heavier rule under
        the lot — which is what the top of a printed document looks like, and
        this product is a print process (§E5, §E6) that had not reached its own
        front doors. What was here was three stacked lines and a hairline: type
        that was correct and composition that was absent.
      */}
      <header className="portal__masthead">
        <div className="portal__masthead-copy">
          {/* The utility row. Two links to one destination, and that is the
              convention rather than a duplicate: the lockup is identity and the
              back link is navigation, and they carry different accessible names
              for exactly that reason. */}
          <div className="portal__utility">
            <Wordmark strings={strings} />
            <BackLink strings={strings} />
          </div>

          <h1 className="portal__title type-display-1">{t(strings, `portal.${audience}.title`)}</h1>
          <p className="portal__standfirst type-body">
            {t(strings, `portal.${audience}.standfirst`)}
          </p>
        </div>

        {/* The plate. Drawn in the run's own inks rather than photographed —
            see `PortalPlate` for why a stock image is the one move this
            product cannot make. */}
        <PortalPlate subject={audience === "citizen" ? "street" : "plan"} />
      </header>

      <main className="portal__main" id="portal-main">
        {groups.map((group, index) => (
          <Fragment key={group.id}>
            <section className="portal__group" aria-labelledby={`group-${group.id}`}>
              {/* The eyebrow and a rule that runs to the end of the line — the
                  oldest sectioning device in print, and the cheapest way to
                  make a page of destinations read as a document rather than as
                  a stack of controls. The rule is drawn by the stylesheet, so
                  it is never in the accessible name. */}
              <h2 className="portal__group-title type-micro" id={`group-${group.id}`}>
                <span>{t(strings, group.titleKey)}</span>
              </h2>

              <ul className="portal__cards">
                {group.items.map((item, position) => (
                  <li key={item.id}>
                    {/* The whole card is the link. A card with a link *inside* it
                      gives a pointer a large target and a keyboard a small one,
                      which is the §E22 failure that never shows up in a
                      screenshot. */}
                    <Link className="portal__card" href={item.href}>
                      {/* An index numeral, in the data face §E10.2 assigns to
                          figures. It is a printed table of contents' own
                          marker, and it is `aria-hidden` because it numbers the
                          page rather than naming the destination — a screen
                          reader announcing "zero four, ward pages" would be
                          reading the furniture. */}
                      <span className="portal__card-index type-mono-data" aria-hidden="true">
                        {indexLabel(offsetOf(groups, index) + position + 1)}
                      </span>
                      <span className="portal__card-body">
                        <span className="portal__card-label type-heading">
                          {t(strings, item.labelKey)}
                        </span>
                        <span className="portal__card-hint type-caption">
                          {t(strings, `${item.labelKey}.hint`)}
                        </span>
                        {item.phase === undefined ? null : (
                          <span className="portal__card-chip">
                            <NotWired phase={item.phase} strings={strings} />
                          </span>
                        )}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            </section>

            {/* The receipt field sits *inside* the first group rather than above
                everything, because "follow a report" is the same errand as
                "report it" and the two belong under one heading. Passing it as
                a child keeps this component from knowing what a receipt is. */}
            {index === 0 ? children : null}
          </Fragment>
        ))}
      </main>

      {footer === undefined ? null : <footer className="portal__footer">{footer}</footer>}
    </div>
  );
}

/**
 * One line of the footer — a link out of this door, labelled and explained.
 *
 * Separate from the cards above on purpose: a card is somewhere this audience
 * is meant to go, and these are somewhere they might need to go. Rendering them
 * identically would put *"staff sign-in"* beside *"report a problem"* at the
 * same weight on a page a resident opened in a hurry.
 */
export function PortalAside({
  href,
  label,
  hint,
}: {
  readonly href: string;
  readonly label: Translated;
  readonly hint: Translated;
}) {
  return (
    <p className="portal__aside type-caption">
      <Link href={href}>{label}</Link>
      <span className="portal__aside-hint"> {hint}</span>
    </p>
  );
}
