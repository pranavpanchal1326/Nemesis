/**
 * The console's screens, in one list — §E19, §E24.
 *
 * Four things need to agree about what the console contains: the navigation
 * rail, the `⌘K` palette, the route guard that keeps an unwired screen off a
 * public URL, and the honesty chip that says which phase populates it. Before
 * this file they would have agreed by somebody remembering, which is the
 * failure mode §E24 exists to remove — a screen quietly reachable at a URL
 * while its chip says otherwise is exactly the kind of lie the chip is there to
 * prevent.
 *
 * So the list is data, and the four consumers read it:
 *
 *   · `<ConsoleShell>` renders the rail from it,
 *   · `<CommandPalette>` searches it,
 *   · `tests/console-screens.test.ts` asserts that every `roadmap` screen's
 *     `page.tsx` calls `devOnly()` and every `real` screen's does not,
 *   · `<NotWired>` takes its phase string from it.
 *
 * **`wiring` is a fact about the backend, not about our progress.** `real`
 * means an endpoint in `openapi.json` answers with the values this screen
 * shows. `roadmap` means the *types* exist and the values are fixtures — which
 * is a materially different and much safer position than mocking, and is the
 * distinction §E1 insists on.
 */

/** Which §E19 group a screen belongs to. The rail's headings, in order. */
export const SECTIONS = ["command", "operate", "money", "integrity", "administer"] as const;
export type Section = (typeof SECTIONS)[number];

export type Wiring =
  | { readonly kind: "real" }
  /** `phase` is the ROADMAP phase that populates the contract, and it is
   *  printed on the chip. A chip that says "later" is not a pointer. */
  | { readonly kind: "roadmap"; readonly phase: string };

export interface Screen {
  /** Stable id. The string key half of `console.json` (`nav.<id>`) and the
   *  test's own handle on the screen, so a rename is one edit. */
  readonly id: string;
  readonly href: string;
  readonly section: Section;
  readonly wiring: Wiring;
  /** The §E19 subsection this screen implements, quoted in its own source. */
  readonly traces: string;
}

export const SCREENS: readonly Screen[] = [
  {
    id: "command",
    href: "/console",
    section: "command",
    wiring: { kind: "real" },
    traces: "§E19.1",
  },
  {
    id: "review",
    href: "/console/review",
    section: "operate",
    wiring: { kind: "real" },
    traces: "§E19.1, §E26, §E27",
  },
  {
    id: "area",
    href: "/console/area",
    section: "operate",
    wiring: { kind: "roadmap", phase: "12, 23" },
    traces: "§E19.2",
  },
  {
    id: "work",
    href: "/console/work",
    section: "operate",
    wiring: { kind: "roadmap", phase: "14" },
    traces: "§E19.3",
  },
  {
    id: "closure",
    href: "/console/closure",
    section: "operate",
    wiring: { kind: "roadmap", phase: "15" },
    traces: "§E19.4",
  },
  {
    id: "money",
    href: "/console/money",
    section: "money",
    wiring: { kind: "roadmap", phase: "14, 23" },
    traces: "§E19.5",
  },
  {
    id: "integrity",
    href: "/console/integrity",
    section: "integrity",
    wiring: { kind: "roadmap", phase: "17" },
    traces: "§E19.6",
  },
  {
    id: "reports",
    href: "/console/reports",
    section: "integrity",
    wiring: { kind: "roadmap", phase: "23" },
    traces: "§E19.7",
  },
  {
    id: "policy",
    href: "/console/policy",
    section: "administer",
    wiring: { kind: "real" },
    traces: "§E19.8",
  },
  {
    id: "control",
    href: "/console/control",
    section: "administer",
    wiring: { kind: "real" },
    traces: "§E19, §E14.4, ADR-0046",
  },
  {
    id: "developers",
    href: "/console/developers",
    section: "administer",
    wiring: { kind: "real" },
    traces: "§E19, §E14.4",
  },
  {
    id: "roles",
    href: "/console/roles",
    section: "administer",
    wiring: { kind: "roadmap", phase: "13" },
    traces: "§E19.0",
  },
];

export function screenById(id: string): Screen | undefined {
  return SCREENS.find((screen) => screen.id === id);
}

/**
 * The screens in one section, in declaration order.
 *
 * Order is authored rather than alphabetical: §E19.1 puts command first because
 * it is where an officer starts a shift, and an alphabetical rail would put
 * "Area view" there instead — a small thing that changes what the product looks
 * like it is for.
 */
export function screensIn(section: Section): readonly Screen[] {
  return SCREENS.filter((screen) => screen.section === section);
}

/** The phase string for a screen's chip, or `undefined` when it is wired. */
export function roadmapPhase(screen: Screen): string | undefined {
  return screen.wiring.kind === "roadmap" ? screen.wiring.phase : undefined;
}

/**
 * Which screen a pathname is on.
 *
 * Longest match wins, so `/console/review/42` is the review screen rather than
 * command — `/console` is a prefix of every console URL and a naive
 * `startsWith` would mark the rail's first item current on every page.
 */
export function screenForPath(pathname: string): Screen | undefined {
  let best: Screen | undefined;
  for (const screen of SCREENS) {
    if (pathname === screen.href || pathname.startsWith(`${screen.href}/`)) {
      if (best === undefined || screen.href.length > best.href.length) best = screen;
    }
  }
  return best;
}
