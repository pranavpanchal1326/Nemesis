/**
 * The diff between two policy documents — §E19.8.
 *
 * > Rules as editable documents with revision history and **diff**.
 *
 * A policy body is `{ [key: string]: unknown }` on the contract — a JSON
 * document whose interior shape belongs to the policy kind rather than to the
 * API — so the diff is structural rather than textual, and that is a decision
 * worth stating.
 *
 * **Why not a text diff of the pretty-printed JSON.** Two documents that differ
 * only in key order are the same policy, and a text diff would report every
 * line of a re-serialised document as changed. An operator comparing revision 7
 * to revision 8 needs to see *"dedup.threshold: 0.82 → 0.88"*, not four hundred
 * green lines. The content hash upstream is computed over a canonical form for
 * the same reason.
 *
 * **Paths, not trees.** Each leaf is addressed by its dotted path, so a change
 * nested six levels down reads as one row rather than as six nested panels.
 * Arrays are addressed by index — `rules.2.threshold` — which is right for a
 * rule list where position is meaningful and would be wrong for a set; policy
 * bodies are the former.
 */

export type Change =
  | { readonly kind: "added"; readonly path: string; readonly after: string }
  | { readonly kind: "removed"; readonly path: string; readonly before: string }
  | {
      readonly kind: "changed";
      readonly path: string;
      readonly before: string;
      readonly after: string;
    };

/**
 * Every leaf that differs, in path order.
 *
 * Sorted by path rather than by kind so a reader scans the document's own
 * structure once, instead of reading all the additions and then hunting back
 * through for the removal that explains one of them.
 */
export function diffDocuments(
  before: Readonly<Record<string, unknown>>,
  after: Readonly<Record<string, unknown>>,
): readonly Change[] {
  const changes: Change[] = [];
  const left = flattenDocument(before);
  const right = flattenDocument(after);

  for (const [path, value] of left) {
    const other = right.get(path);
    if (other === undefined) {
      changes.push({ kind: "removed", path, before: value });
    } else if (other !== value) {
      changes.push({ kind: "changed", path, before: value, after: other });
    }
  }
  for (const [path, value] of right) {
    if (!left.has(path)) changes.push({ kind: "added", path, after: value });
  }

  return changes.sort((a, b) => a.path.localeCompare(b.path));
}

/**
 * The document, flattened from its entries rather than from itself.
 *
 * **The root is never a leaf.** `flatten({})` would answer `{"": "{}"}` — an
 * empty *policy body* is a legitimate state, and comparing one against a
 * populated document would report a phantom row at the empty path alongside the
 * real ones. Found by a test written for the opposite case (an empty *array*
 * inside a document, which genuinely is a leaf), which is the useful kind of
 * near-miss: the two look identical in the recursion and mean opposite things.
 */
function flattenDocument(document: Readonly<Record<string, unknown>>): Map<string, string> {
  const into = new Map<string, string>();
  for (const [key, value] of Object.entries(document)) flatten(value, key, into);
  return into;
}

/**
 * Every leaf as `path → rendered value`.
 *
 * The value is rendered to a string here rather than kept as `unknown`, because
 * the comparison this module performs is "would an operator see a difference",
 * and `1` versus `"1"` is a difference they should see. `JSON.stringify` keeps
 * the two distinguishable; `String()` would collapse them.
 *
 * An empty object or array is a leaf. Recursing into it would produce no rows,
 * and *"rules: []"* being removed is a change an operator very much needs.
 */
function flatten(
  value: unknown,
  prefix = "",
  into = new Map<string, string>(),
): Map<string, string> {
  if (Array.isArray(value)) {
    if (value.length === 0) into.set(prefix, "[]");
    else value.forEach((item, index) => flatten(item, join(prefix, String(index)), into));
    return into;
  }
  if (typeof value === "object" && value !== null) {
    const entries = Object.entries(value);
    if (entries.length === 0) into.set(prefix, "{}");
    else for (const [key, item] of entries) flatten(item, join(prefix, key), into);
    return into;
  }
  // `value ?? null` rather than a fallback on the result: `JSON.stringify`
  // answers `undefined` for `undefined`, and a leaf that exists with no value
  // must still produce a row.
  into.set(prefix, JSON.stringify(value ?? null));
  return into;
}

function join(prefix: string, key: string): string {
  return prefix === "" ? key : `${prefix}.${key}`;
}
