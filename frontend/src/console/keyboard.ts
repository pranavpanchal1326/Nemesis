/**
 * The console's keyboard model — §E19, §E22.
 *
 * > Full keyboard path **including the map**: arrow-key pin traversal, `/`
 * > search, `j`/`k` queue, `e` evidence, `⌘K` palette.  — §E22
 *
 * That sentence is a specification, and it is implemented here as **one pure
 * function** rather than as a handful of `keydown` listeners spread across the
 * screens that need them. Three reasons, all of them things that went wrong in
 * products that did it the other way:
 *
 *   · **A shortcut must not fire while somebody is typing.** `j` is "next item"
 *     everywhere except inside the search field, where it is the letter j. One
 *     listener that forgets this is a search box that cannot spell "Kajgaon".
 *   · **The list must be reviewable.** `SHORTCUTS` below is also what the help
 *     screen and the palette render, so a shortcut that exists and is not
 *     documented is not possible — the documentation is the source.
 *   · **It must be testable without a browser.** `resolveShortcut` takes a
 *     plain object, so every rule in it is asserted in vitest at the cost of
 *     one call, and the E2E is left to prove the wiring rather than the table.
 *
 * **`⌘K` and `Ctrl-K` are the same shortcut.** Not a platform branch: the
 * console runs on whatever a municipality bought, and an officer who moves
 * between a Windows terminal in the ward office and a Mac at home should not
 * have to learn it twice.
 */

/** Every action a key can start. The palette and the help list read this. */
export const ACTIONS = ["palette", "search", "next", "previous", "evidence", "help"] as const;
export type Action = (typeof ACTIONS)[number];

export interface Shortcut {
  readonly action: Action;
  /** What the reader presses, for the help list. Not parsed — displayed. */
  readonly keys: string;
  /** `keys.<id>` in the console bundle. Never a literal (§E10.1). */
  readonly labelKey: string;
}

export const SHORTCUTS: readonly Shortcut[] = [
  { action: "palette", keys: "⌘K", labelKey: "keys.palette" },
  { action: "search", keys: "/", labelKey: "keys.search" },
  { action: "next", keys: "j", labelKey: "keys.queueDown" },
  { action: "previous", keys: "k", labelKey: "keys.queueUp" },
  { action: "evidence", keys: "e", labelKey: "keys.evidence" },
  { action: "help", keys: "?", labelKey: "keys.help" },
];

/** The fields of a `KeyboardEvent` this module reads. Declared so a test can
 *  build one without a DOM, and so the function cannot quietly start depending
 *  on something a test does not supply. */
export interface KeyStroke {
  readonly key: string;
  readonly ctrlKey?: boolean;
  readonly metaKey?: boolean;
  readonly altKey?: boolean;
  readonly shiftKey?: boolean;
  /** Whether the keystroke landed in a field the reader is typing into. */
  readonly typing?: boolean;
}

/**
 * Is this element one a person types into?
 *
 * `isContentEditable` is included because a policy rule in §E19.8 is edited as
 * a document, and a document editor that swallows `j` is a document editor
 * nobody can write Marathi in either.
 */
export function isTypingTarget(target: EventTarget | null): boolean {
  if (target === null || !(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
}

/**
 * Which action a keystroke means, or `null` for "none of ours — leave it".
 *
 * Returning `null` rather than throwing or defaulting matters: the console must
 * not eat a browser shortcut it does not own. `⌘L`, `⌘R` and Tab all fall
 * through here untouched, and Tab in particular must, because it is the
 * keyboard path this whole model is in service of.
 */
export function resolveShortcut(stroke: KeyStroke): Action | null {
  const chord = stroke.metaKey === true || stroke.ctrlKey === true;

  // `⌘K` is the one shortcut that works while typing — including inside the
  // palette's own field, where it closes what it opened. A palette you cannot
  // dismiss with the keys that opened it is a trap for the keyboard-only user
  // this model exists for.
  if (chord && stroke.key.toLowerCase() === "k" && stroke.altKey !== true) return "palette";

  // Any other modifier combination belongs to the browser or the OS.
  if (chord || stroke.altKey === true) return null;

  // Single letters are text when a text field has focus, and only then.
  if (stroke.typing === true) return null;

  switch (stroke.key) {
    case "/":
      return "search";
    case "j":
      return "next";
    case "k":
      return "previous";
    case "e":
      return "evidence";
    case "?":
      return "help";
    default:
      return arrowAction(stroke.key);
  }
}

/**
 * The arrow keys on their own — the half of the model that is *not* text.
 *
 * `resolveShortcut` refuses every single key while somebody is typing, and it
 * has to: `j` inside the queue's filter is the letter j, which is the whole
 * reason that rule exists. But `ArrowDown` inside a combobox's field is not a
 * letter — it is how the ARIA combobox pattern moves the highlight, and the
 * palette's field is a text field by construction. So a caller that *is* a
 * combobox resolves arrows through this, and every other caller keeps the
 * stricter rule.
 *
 * It is exported rather than inlined at the one call site so the key table
 * stays in this module: two places that both know `ArrowUp` means "previous"
 * is exactly how a keyboard model starts disagreeing with its own help screen.
 * A plain `<textarea>` — a §E19.8 policy rule is edited as a document — never
 * calls this, and its caret still moves.
 */
export function arrowAction(key: string): Action | null {
  if (key === "ArrowDown") return "next";
  if (key === "ArrowUp") return "previous";
  return null;
}

/**
 * Move a selection by one action, clamped.
 *
 * **Clamped rather than wrapped**, and that is a deliberate answer to a real
 * question. A queue sorted by SLA-remaining ascending (§E19.1) has a meaningful
 * top: the thing that breaches first. Wrapping from the last row to the first
 * would silently teach an officer holding `j` that they had reached the end
 * when they had reached the beginning again — in a list whose whole purpose is
 * ordering by urgency, that is a wrong answer to "what is next", not a
 * navigational nicety.
 *
 * `length === 0` yields `-1`: nothing is selected in an empty list, and
 * returning `0` would put a selection ring on a row that does not exist.
 */
export function moveSelection(current: number, length: number, action: Action): number {
  if (length === 0) return -1;
  if (action === "next") return Math.min(current + 1, length - 1);
  if (action === "previous") return Math.max(current - 1, 0);
  return current;
}
