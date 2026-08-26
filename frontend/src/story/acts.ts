/**
 * The nine acts — §E16, M9.
 *
 * §E16 is a table with a `t` column, and this module is that column and nothing
 * else. Every other part of the film asks it questions: which act is on screen,
 * how far through that act we are, and where an act begins so a golden image
 * can be taken at a fixed point inside it.
 *
 * **Why the boundaries are data and not layout.** The obvious implementation is
 * a stack of full-height sections and an `IntersectionObserver` — the act is
 * whichever section is on screen. It reads well and it cannot be tested: the
 * answer then depends on the viewport, the scrollbar, the browser's rounding of
 * `100dvh`, and on whether fonts had loaded when the observer fired. The Phase
 * 20 gate wants *"golden-image regression per scene at fixed seed and camera"*,
 * and "fixed" has to mean a number somebody can write down. So the spine owns
 * one normalised `t ∈ [0,1]`, this table turns `t` into an act, and the DOM
 * follows — never the other way round.
 *
 * **Act 9 is not on the spine, and that is §E16's own decision.** The receipts
 * are *"below fold"* — deliberately boring, deliberately not a scene. It is
 * listed here because it is one of the nine and the register counts it, and it
 * carries `null` for its range because a scene that is not on the timeline has
 * no `t`. Code that iterates the film asks for {@link FILM_ACTS}; code that
 * iterates the page asks for {@link ACTS}.
 */

import type { CSSProperties } from "react";

/** The act ids, in the order §E16 lists them. */
export const ACT_IDS = [
  "cold-open",
  "walk",
  "stop",
  "silence",
  "report",
  "pipeline",
  "merge",
  "city-awake",
  "table",
  "receipts",
] as const;

export type ActId = (typeof ACT_IDS)[number];

export interface Act {
  readonly id: ActId;
  /** §E16's own numbering, 0–9, kept because every reference to this film in
   *  the blueprint and in the plan is by number rather than by name. */
  readonly index: number;
  /**
   * Where the act sits on the spine, or `null` for the one act that is not on
   * it. Half-open — `[from, to)` — so a `t` belongs to exactly one act and the
   * boundary belongs to the act that is starting, not to the one that is
   * ending. The last act on the spine closes at 1 inclusive, because there is
   * nothing after it to hand the boundary to.
   */
  readonly range: { readonly from: number; readonly to: number } | null;
}

/**
 * The table in §E16, transcribed. The numbers are the blueprint's numbers and
 * changing one here changes the film — which is why they are here once rather
 * than in nine components.
 */
export const ACTS: readonly Act[] = [
  { id: "cold-open", index: 0, range: { from: 0.0, to: 0.05 } },
  { id: "walk", index: 1, range: { from: 0.05, to: 0.22 } },
  { id: "stop", index: 2, range: { from: 0.22, to: 0.32 } },
  { id: "silence", index: 3, range: { from: 0.32, to: 0.43 } },
  { id: "report", index: 4, range: { from: 0.43, to: 0.55 } },
  { id: "pipeline", index: 5, range: { from: 0.55, to: 0.7 } },
  { id: "merge", index: 6, range: { from: 0.7, to: 0.83 } },
  { id: "city-awake", index: 7, range: { from: 0.83, to: 0.93 } },
  { id: "table", index: 8, range: { from: 0.93, to: 1.0 } },
  { id: "receipts", index: 9, range: null },
];

/** An act with a place on the timeline. Narrowed so callers that interpolate
 *  do not each re-check the `null` the type system already knows about. */
export interface FilmAct extends Act {
  readonly range: { readonly from: number; readonly to: number };
}

export const FILM_ACTS: readonly FilmAct[] = ACTS.filter(
  (act): act is FilmAct => act.range !== null,
);

const BY_ID = new Map<ActId, Act>(ACTS.map((act) => [act.id, act]));

export function act(id: ActId): Act {
  const found = BY_ID.get(id);
  // Unreachable through the exported types — `ActId` is derived from the same
  // array — and thrown rather than defaulted, because a silent default here
  // would put the wrong act on screen and look like a scroll bug.
  if (found === undefined) throw new Error(`unknown act: ${id}`);
  return found;
}

/**
 * The share of the film an act occupies, as the stylesheet's own variable.
 *
 * `story.css` sizes each section as `--act-span * 20 * 100dvh`, which is what
 * makes scroll position and `t` linear in each other: an act that is eleven per
 * cent of the spine is eleven per cent of the scrollable height. Derived here
 * rather than written into nine components, because two copies of the §E16
 * table is one copy and one bug.
 */
export function actStyle(id: ActId): CSSProperties {
  const range = act(id).range;
  const span = range === null ? 0 : range.to - range.from;
  return { "--act-span": span.toFixed(4) } as CSSProperties;
}

/** Clamp to the spine's own domain. `t` arrives from a scroll position and a
 *  rubber-banding browser can hand out a negative one. */
export function clampT(t: number): number {
  if (!Number.isFinite(t)) return 0;
  return Math.min(1, Math.max(0, t));
}

/** Which act is on screen at `t`. Total: every `t ∈ [0,1]` has one. */
export function actAt(t: number): FilmAct {
  const clamped = clampT(t);
  for (const candidate of FILM_ACTS) {
    if (clamped >= candidate.range.from && clamped < candidate.range.to) return candidate;
  }
  // `t === 1` exactly. The last act owns its closing boundary; see the type's
  // note above.
  const last = FILM_ACTS.at(-1);
  if (last === undefined) throw new Error("the film has no acts");
  return last;
}

/** How far through its own act `t` is, normalised to `[0,1]`. What every scene
 *  animates against, so a scene never has to know where on the spine it sits. */
export function localT(t: number): number {
  const current = actAt(t);
  const span = current.range.to - current.range.from;
  if (span <= 0) return 0;
  return clampT((clampT(t) - current.range.from) / span);
}

/** The spine position of a point `fraction` of the way through an act. The
 *  seam the golden images are taken through: `atAct("merge", 0.5)` is a number
 *  a test can write down and a reviewer can re-open by hand. */
export function atAct(id: ActId, fraction = 0): number {
  const target = act(id);
  if (target.range === null) {
    throw new Error(`act ${id} is not on the spine — see ACTS`);
  }
  const span = target.range.to - target.range.from;
  return clampT(target.range.from + span * clampT(fraction));
}
