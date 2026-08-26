import { roadmapPhase, SCREENS, SECTIONS, screensIn } from "@/console/screens";

/**
 * What the two front doors contain — the one list, again.
 *
 * §E14.4 describes this application as *"one application, five route groups,
 * sharing the token system, the press and the §E26 contracts, differing in
 * shell, density and auth posture."* Every one of those groups was built and
 * every one of them was reachable only by typing its URL. A citizen who wanted
 * to file a report and then read what the city publishes had to be told two
 * paths; an officer moving between the console and the field app had to be told
 * two more. The surfaces were finished and the product was not, because a
 * product is also how somebody gets from one part of it to the next.
 *
 * So: two doors, one for each audience the blueprint already separates —
 * §E17/§E18 for the person who lives in the city, §E19/§E21 for the person paid
 * to answer them. Not a third product; a way into the ones that exist.
 *
 * **The staff door is generated from `console/screens.ts`.** That module's own
 * docstring makes the argument — *"a hand-written rail and a `⌘K` palette are
 * two lists of the same screens, and the day they disagree is the day an
 * officer cannot reach something the palette says exists"* — and a hand-written
 * portal would be the fifth list to disagree. It reads the same registry, keeps
 * the same section order, and carries the same §E24 chip, so a screen added to
 * the console appears here with no edit at all.
 *
 * **The citizen door is written out, because there is no registry to read.**
 * Its four destinations live in three different route groups and one of them
 * takes a tenant slug; there is no existing list that means "what a resident
 * can do". This is that list, and it is data for the same reason the console's
 * is: `tests/portal.test.ts` reads it, and a destination whose route does not
 * exist fails there rather than in somebody's browser.
 */

/** Where a portal card points, and what the chip beside it says. */
export interface Destination {
  /** Stable id. React's key, and the test's handle on the destination. */
  readonly id: string;
  readonly href: string;
  /**
   * The string key for the label; the hint is `${labelKey}.hint`.
   *
   * Carried rather than derived, because the two doors quote **different
   * vocabularies and that is the point**. A console screen's name belongs to
   * the console (`nav.review`, in `console.json`), and copying those thirteen
   * names into a `portal.*` namespace would be the fifth list to disagree with
   * `screens.ts` — the exact failure that module was written to remove. The
   * citizen door's four destinations have no such owner, so they are
   * `portal.*`, authored here.
   */
  readonly labelKey: string;
  /**
   * The phase that populates this destination's contract, when it is not
   * populated yet — printed on the §E24 chip.
   *
   * Absent means *the data behind this is real*. It does not mean "finished":
   * §E28 is the table that says what finished means, and this field is a
   * pointer to it rather than a second opinion about it.
   */
  readonly phase?: string;
}

export interface DestinationGroup {
  /** Stable id. React's key, and half of the `aria-labelledby` the section's
   *  heading answers. */
  readonly id: string;
  /** The string key for the heading. The console's sections keep their own
   *  (`section.operate`), for the same reason their screens do. */
  readonly titleKey: string;
  readonly items: readonly Destination[];
}

/**
 * The resident's door — §E17, §E18.
 *
 * Ordered by what somebody standing in front of a broken thing wants, not by
 * what the system finds interesting: report it, then find out what happened to
 * it, then read what the city publishes about the place it is in, then read
 * what this product will not claim about itself.
 *
 * `tenant` is the published slug. When it is absent — an unconfigured
 * deployment — the two public destinations are **left out rather than pointed
 * at a guess**: a card that leads to a 404 is worse than a card that is not
 * there, and §E3.3's rule is that a surface does not offer an affordance it
 * cannot honour.
 */
export function citizenDestinations(tenant: string | undefined): readonly DestinationGroup[] {
  const city: Destination[] =
    tenant === undefined || tenant === ""
      ? []
      : [
          { id: "city", href: `/${tenant}`, labelKey: "portal.city" },
          { id: "honesty", href: `/${tenant}/honesty`, labelKey: "portal.honesty" },
        ];

  return [
    {
      id: "report",
      titleKey: "portal.group.report",
      items: [
        { id: "file", href: "/report", labelKey: "portal.file" },
        // §E17.4's ledger is read at `/t/{id}`, and the id is a capability
        // (ADR-0043) — so the door is a field the reporter types their receipt
        // into, not a link. `<TrackForm>` is that field; this entry is what
        // labels the section it sits in.
      ],
    },
    ...(city.length === 0 ? [] : [{ id: "city", titleKey: "portal.group.city", items: city }]),
  ];
}

/**
 * The staff door — §E19, §E21.
 *
 * The console's own five sections, in the console's own order, plus the field
 * app: §E21 is a separate route group with a separate shell because it is a
 * separate posture (one thumb, gloves, no network), and it is the one staff
 * surface the console rail cannot contain.
 */
export function staffDestinations(): readonly DestinationGroup[] {
  const consoleGroups: DestinationGroup[] = SECTIONS.map((section) => ({
    id: section,
    titleKey: `section.${section}`,
    items: screensIn(section).map((screen): Destination => {
      const phase = roadmapPhase(screen);
      const card = { id: screen.id, href: screen.href, labelKey: `nav.${screen.id}` };
      // Spread-with-`undefined` would satisfy the reader and not the compiler:
      // `exactOptionalPropertyTypes` distinguishes *absent* from *present and
      // undefined*, and the chip's whole meaning rests on that distinction.
      return phase === undefined ? card : { ...card, phase };
    }),
  })).filter((group) => group.items.length > 0);

  return [
    ...consoleGroups,
    {
      id: "field",
      titleKey: "portal.group.field",
      items: [
        {
          id: "fieldApp",
          href: "/field",
          labelKey: "portal.fieldApp",
          // The job list is the one part of §E21 with no contract behind it,
          // and its own screen says so with this chip. Repeating the phase here
          // rather than hiding it keeps the door as honest as the room.
          //
          // `"14"` and not `"Phase 14"`: the chip prints the string it is given
          // beside the word it already carries, so the console's twelve chips
          // read *NOT WIRED 14* and this one read *NOT WIRED Phase 14* until
          // somebody looked at the two doors side by side.
          phase: "14",
        },
      ],
    },
  ];
}

/** Every destination both doors offer, flattened — for the tests that assert
 *  each one resolves and each one is translated. */
export function allDestinations(tenant: string): readonly Destination[] {
  return [...citizenDestinations(tenant), ...staffDestinations()].flatMap((group) => group.items);
}

/** The console screens, re-exported so a test can assert the staff door covers
 *  every one of them without importing two modules to compare. */
export { SCREENS };
