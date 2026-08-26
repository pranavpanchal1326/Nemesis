import { describe, expect, it } from "vitest";

import { arrowAction, moveSelection, resolveShortcut, SHORTCUTS } from "../src/console/keyboard";

/**
 * §E22's keyboard model, asserted as a table rather than through a browser.
 *
 * > Full keyboard path including the map: arrow-key pin traversal, `/` search,
 * > `j`/`k` queue, `e` evidence, `⌘K` palette.
 *
 * The E2E in `console.spec.ts` proves the *wiring* — that pressing `j` in a
 * real console moves a real selection. These assertions prove the *rules*, and
 * they are separated because the rules are where the bugs are and a browser
 * test is the most expensive place to find them.
 */

describe("§E22 — which key means what", () => {
  it("names every action in SHORTCUTS, so the help list is the source", () => {
    // The palette renders `SHORTCUTS`; if an action existed without a row here,
    // it would be a shortcut nobody could discover. Asserted rather than
    // reviewed, because "we documented it" is exactly the kind of claim that
    // rots.
    const documented = new Set(SHORTCUTS.map((shortcut) => shortcut.action));
    for (const action of ["palette", "search", "next", "previous", "evidence", "help"] as const) {
      expect(documented, `${action} has no row in SHORTCUTS`).toContain(action);
    }
    for (const shortcut of SHORTCUTS) {
      // Never a literal. §E10.1 applies to the help screen too.
      expect(shortcut.labelKey).toMatch(/^keys\./);
    }
  });

  it.each([
    ["/", "search"],
    ["j", "next"],
    ["ArrowDown", "next"],
    ["k", "previous"],
    ["ArrowUp", "previous"],
    ["e", "evidence"],
    ["?", "help"],
  ] as const)("%s means %s", (key, action) => {
    expect(resolveShortcut({ key })).toBe(action);
  });

  it("⌘K and Ctrl-K are the same shortcut", () => {
    // Not a platform branch: a municipality buys whatever it buys, and an
    // officer who moves between a Windows terminal and a Mac should not have to
    // learn the palette twice.
    expect(resolveShortcut({ key: "k", metaKey: true })).toBe("palette");
    expect(resolveShortcut({ key: "K", ctrlKey: true })).toBe("palette");
  });

  it("does not fire while somebody is typing", () => {
    // `j` inside the search field is the letter j. One listener that forgets
    // this is a search box that cannot spell "Kajgaon".
    for (const key of ["/", "j", "k", "e", "?"]) {
      expect(resolveShortcut({ key, typing: true }), `${key} fired in a text field`).toBeNull();
    }
  });

  it("gives a combobox its arrows back, and nothing else", () => {
    // The typing rule above is right for letters and wrong for arrows in one
    // place: the palette's field is a combobox by construction, and ArrowDown
    // in a combobox is how the ARIA pattern moves the highlight rather than a
    // character somebody is trying to type. `arrowAction` is the narrow door,
    // and it stays narrow — a caller cannot accidentally reach `j` through it,
    // which is what would break the queue filter again.
    expect(arrowAction("ArrowDown")).toBe("next");
    expect(arrowAction("ArrowUp")).toBe("previous");
    for (const key of ["/", "j", "k", "e", "?", "Enter", "Tab", "ArrowLeft"]) {
      expect(arrowAction(key), `${key} came through the combobox door`).toBeNull();
    }
  });

  it("⌘K still fires while typing, including inside the palette", () => {
    // A palette that cannot be dismissed by the keys that opened it is a trap
    // for the keyboard-only user the whole model exists for.
    expect(resolveShortcut({ key: "k", metaKey: true, typing: true })).toBe("palette");
  });

  it("leaves the browser its own shortcuts", () => {
    // Tab in particular *must* fall through: it is the keyboard path this model
    // is in service of.
    expect(resolveShortcut({ key: "Tab" })).toBeNull();
    expect(resolveShortcut({ key: "l", metaKey: true })).toBeNull();
    expect(resolveShortcut({ key: "r", ctrlKey: true })).toBeNull();
    expect(resolveShortcut({ key: "j", altKey: true })).toBeNull();
    expect(resolveShortcut({ key: "k", metaKey: true, altKey: true })).toBeNull();
  });
});

describe("§E19.1 — the selection clamps and does not wrap", () => {
  it("moves within the list", () => {
    expect(moveSelection(0, 5, "next")).toBe(1);
    expect(moveSelection(3, 5, "previous")).toBe(2);
  });

  it("stops at both ends", () => {
    // Wrapping in a queue sorted by urgency would silently answer "what is
    // next" with "the most urgent thing you already dealt with".
    expect(moveSelection(4, 5, "next")).toBe(4);
    expect(moveSelection(0, 5, "previous")).toBe(0);
  });

  it("selects nothing in an empty list", () => {
    // `0` would put a selection ring on a row that does not exist.
    expect(moveSelection(0, 0, "next")).toBe(-1);
    expect(moveSelection(3, 0, "previous")).toBe(-1);
  });

  it("ignores actions that are not movement", () => {
    expect(moveSelection(2, 5, "evidence")).toBe(2);
    expect(moveSelection(2, 5, "palette")).toBe(2);
  });
});
