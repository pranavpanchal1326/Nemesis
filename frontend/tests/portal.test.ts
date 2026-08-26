import { describe, expect, it } from "vitest";

import { SCREENS, roadmapPhase } from "@/console/screens";
import {
  allDestinations,
  citizenDestinations,
  staffDestinations,
  type Destination,
} from "@/portal/destinations";
import { baseKeys } from "@/server/strings";

/**
 * The two front doors, asserted against the lists they claim to be generated
 * from — ADR-0059.
 *
 * `src/portal/destinations.ts` makes two promises that are cheap to write and
 * would be expensive to discover broken: that the staff door contains every
 * console screen with the same chip the rail gives it, and that every card on
 * either door has words to render. Both are the same class of failure — a door
 * that quietly stops offering a room — and neither shows up in a screenshot of
 * the door, because a missing card looks exactly like a door with fewer rooms.
 *
 * The route-level half (every href actually answers, and the receipt field
 * behaves) is `tests/portal.spec.ts`: a link that resolves is a question about
 * the router, not about this list.
 */

/** The bundles each door loads, merged the way its page merges them. */
function bundleFor(audience: "citizen" | "staff"): ReadonlySet<string> {
  const namespaces =
    audience === "citizen" ? (["common", "public"] as const) : (["common", "console"] as const);
  return new Set(namespaces.flatMap((namespace) => baseKeys(namespace)));
}

const CITY = "pune-demo";

describe("the staff door is the console's own list", () => {
  it("offers every console screen", () => {
    const offered = new Set(staffDestinations().flatMap((group) => group.items.map((i) => i.id)));
    for (const screen of SCREENS) {
      expect(offered, `${screen.id} is in the rail and not on the door`).toContain(screen.id);
    }
  });

  it("points at the same href the rail does", () => {
    const byId = new Map(
      staffDestinations()
        .flatMap((group) => group.items)
        .map((item) => [item.id, item.href]),
    );
    for (const screen of SCREENS) {
      expect(byId.get(screen.id), `${screen.id}'s door and rail disagree`).toBe(screen.href);
    }
  });

  it("carries the same §E24 chip the rail carries, and carries it nowhere else", () => {
    // The direction that matters is *this* one: a door that dropped the chip
    // would be a door presenting nine fixture screens as finished work, which
    // is the §E24 failure the chip exists to make impossible.
    const byId = new Map(
      staffDestinations()
        .flatMap((group) => group.items)
        .map((item) => [item.id, item.phase]),
    );
    for (const screen of SCREENS) {
      expect(byId.get(screen.id), `${screen.id}'s chip`).toBe(roadmapPhase(screen));
    }
  });

  it("adds the field app, which the rail cannot contain", () => {
    // §E21 is a separate route group with a separate posture. It is the one
    // staff surface with no console screen, so it is the one entry here that is
    // written rather than generated — and therefore the one worth asserting.
    const field = staffDestinations()
      .flatMap((group) => group.items)
      .find((item) => item.id === "fieldApp");
    expect(field?.href).toBe("/field");
    // The bare number, in the format `screens.ts` uses: `<NotWired>` renders it
    // after the words it already carries, so "Phase 14" reads *NOT WIRED Phase
    // 14* beside twelve chips reading *NOT WIRED 14*.
    expect(field?.phase, "the job list is Phase 14 and the door should say so").toBe("14");
  });
});

describe("the citizen door offers nothing it cannot honour", () => {
  it("drops the public destinations when no tenant is configured", () => {
    // §E3.3: a surface does not offer an affordance it cannot deliver. Without
    // a tenant slug there is no `/{tenant}` to link to, and a card pointing at
    // a 404 is worse than a card that is not there.
    const ids = citizenDestinations(undefined).flatMap((group) => group.items.map((i) => i.id));
    expect(ids).toEqual(["file"]);
    expect(citizenDestinations("")).toEqual(citizenDestinations(undefined));
  });

  it("scopes the public destinations to the configured tenant", () => {
    const hrefs = citizenDestinations(CITY).flatMap((group) => group.items.map((i) => i.href));
    expect(hrefs).toContain(`/${CITY}`);
    expect(hrefs).toContain(`/${CITY}/honesty`);
  });
});

describe("every card has words", () => {
  const cases: readonly [audience: "citizen" | "staff", items: readonly Destination[]][] = [
    ["citizen", citizenDestinations(CITY).flatMap((group) => group.items)],
    ["staff", staffDestinations().flatMap((group) => group.items)],
  ];

  for (const [audience, items] of cases) {
    const keys = bundleFor(audience);

    it.each(items.map((item) => [item.id, item.labelKey]))(
      `${audience}: %s has a label and a hint`,
      (_id, labelKey) => {
        // A missing key renders `⟦portal.file⟧` — visible, which is the design
        // (§E3.3), and shipped, which is not. `tests/strings-rendered.spec.ts`
        // sweeps the rendered pages; this catches the same fault one layer
        // earlier, where the fix is obvious.
        expect(keys, `${labelKey} is not in the ${audience} door's bundles`).toContain(labelKey);
        expect(keys, `${labelKey}.hint is not in the ${audience} door's bundles`).toContain(
          `${labelKey}.hint`,
        );
      },
    );
  }

  it("every group heading has words", () => {
    for (const [audience, groups] of [
      ["citizen", citizenDestinations(CITY)],
      ["staff", staffDestinations()],
    ] as const) {
      const keys = bundleFor(audience);
      for (const group of groups) {
        expect(keys, `${group.titleKey} is missing`).toContain(group.titleKey);
      }
    }
  });

  it("the doors' own chrome has words", () => {
    const common = new Set(baseKeys("common"));
    for (const key of [
      "portal.skip",
      "portal.wordmark",
      "portal.citizen.title",
      "portal.citizen.standfirst",
      "portal.staff.title",
      "portal.staff.standfirst",
      "portal.track",
      "portal.track.hint",
      "portal.track.label",
      "portal.track.placeholder",
      "portal.track.submit",
      "portal.track.rejected",
      "portal.toStaff",
      "portal.toStaff.hint",
      "portal.toCitizen",
      "portal.toCitizen.hint",
      // The landing and the citizen surface label their way-in nav with this.
      "story.ways",
    ]) {
      expect(common, `${key} is missing from common`).toContain(key);
    }
  });
});

describe("the flattened list the tests share", () => {
  it("is every card on both doors, and no duplicates", () => {
    const all = allDestinations(CITY);
    const ids = all.map((item) => item.id);
    expect(new Set(ids).size, `two cards share an id: ${ids.join(", ")}`).toBe(ids.length);
    // Every console screen, plus the field app, plus the citizen door's three:
    // report, the city index and the honesty table. The receipt field is not
    // counted — it is a form, not a destination, for the reason `TrackForm`
    // gives.
    expect(all.length).toBe(SCREENS.length + 4);
  });
});
