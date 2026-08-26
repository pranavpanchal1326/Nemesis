/**
 * The four figures of §E8.2, and the posture each state puts them in — F15.
 *
 * §E8 asks for *"a hand-drawn ink figure — varying line weight, one warm fill
 * that does not quite register with the line"*, and **no face, ever**:
 *
 * > Not for cost — for meaning. The figure must read as a college student, an
 * > aunty, a delivery rider, anyone.
 *
 * **This module is a pure function and nothing else.** `posture(figure, state,
 * phase)` and `strokes(figure, posture)` take a state name and a frame number
 * and return geometry. There is no clock in here, no canvas, no React and no
 * interpolation between poses — a pose *holds* and then snaps, which is §E7.2's
 * twelve frames per second animated on twos, and it is why the audit in
 * `tests/ink-figures.test.ts` can assert that no timeline exists.
 *
 * **Why a posture and not a drawing per state.** Eight states times four
 * figures times a walk cycle is the combinatorial asset count ADR-0041 rejected
 * sprite sheets over. A posture is nine numbers; a figure is the skeleton those
 * numbers pose plus the one prop that tells you who it is. Adding a state costs
 * nine numbers, and adding a figure costs a build and a prop.
 *
 * **The coordinate space is the figure, not the canvas.** `x` runs across the
 * body from its own centre, `y` runs *up* from the ground at 0 to the crown at
 * roughly 1. `draw.ts` owns the flip and the fit, because the same geometry is
 * drawn at 96 px in a console empty state and at 480 px in Act 2 of the film.
 */

import type { FigureState } from "./machine";

/** §E8.2's cast, by the names that table uses. */
export const FIGURES = ["reporter", "officer", "field-hand", "auditor"] as const;

export type FigureName = (typeof FIGURES)[number];

/**
 * What a figure carries, and it is the only thing that tells you who they are.
 *
 * §E8's rule is that the figure has no face, so identity cannot be in the head
 * — it has to be in the silhouette and in the one object in the hands. A
 * clipboard is an officer; a tool bag is a field hand; a folder held still is
 * an auditor; nothing at all is a citizen, which is the point of the citizen.
 */
export type Prop = "none" | "bag" | "clipboard" | "toolbag" | "folder";

export interface FigureBuild {
  readonly name: FigureName;
  /** Crown height in figure units. The cast differ; people do. */
  readonly height: number;
  /** Half the shoulder span. */
  readonly shoulder: number;
  /** Head radius. */
  readonly head: number;
  readonly prop: Prop;
  /**
   * A constant lean, in radians, added to every posture.
   *
   * §E8.2's register column, as geometry. The Officer is *"competent, tired"* —
   * a few degrees of settle that never leaves, even in `idle`. The Auditor is
   * *"patient. Never smug, never accusatory"* and stands square: zero.
   */
  readonly restLean: number;
  /** A cap on the head's outline. The Field Hand works outdoors. */
  readonly cap: boolean;
}

export const BUILDS: Readonly<Record<FigureName, FigureBuild>> = {
  reporter: {
    name: "reporter",
    height: 1,
    shoulder: 0.105,
    head: 0.075,
    prop: "bag",
    restLean: 0.02,
    cap: false,
  },
  officer: {
    name: "officer",
    height: 0.99,
    shoulder: 0.12,
    head: 0.076,
    prop: "clipboard",
    restLean: 0.055,
    cap: false,
  },
  "field-hand": {
    name: "field-hand",
    height: 0.97,
    shoulder: 0.125,
    head: 0.074,
    prop: "toolbag",
    restLean: 0.04,
    cap: true,
  },
  auditor: {
    name: "auditor",
    height: 1.02,
    shoulder: 0.11,
    head: 0.075,
    prop: "folder",
    restLean: 0,
    cap: false,
  },
};

/**
 * A pose, as nine numbers.
 *
 * Angles are radians and every one of them is signed the same way: positive
 * leans, tilts and swings go *forward*, in the direction the figure faces.
 */
export interface Posture {
  /** Torso rotation from vertical. */
  readonly lean: number;
  /** Head rotation. Positive is §E8.1's `looking_down`. */
  readonly headTilt: number;
  /** How far the shoulders have dropped, 0–1. §E16 Act 2's entire beat. */
  readonly shoulderDrop: number;
  /** Forward arm swing. The other arm takes its negation. */
  readonly armSwing: number;
  /** Forward leg swing. Same rule. */
  readonly stride: number;
  /** Hip height offset — the bob of a walk, and the sag of a stop. */
  readonly bob: number;
  /** How far the phone is raised, 0–1. §E8.1's `raise_phone`. */
  readonly phone: number;
  /** Overall line weight multiplier. A held pose is drawn heavier. */
  readonly weight: number;
  /** How many frames of the stepped clock this state's cycle runs for. */
  readonly cycle: number;
}

/**
 * Animation *on twos* — §E7.2.
 *
 * > Poses hold two frames, then snap.
 *
 * The whole of the character layer's motion is this one line: a phase index
 * derived from the clock's step, changing every second frame. There is no
 * interpolation between phases anywhere in this module, and that absence is the
 * stop-motion read.
 */
export function phaseAt(step: number, cycle: number): number {
  if (cycle <= 1) return 0;
  return Math.floor(step / 2) % cycle;
}

/** A quarter-turn of a cycle, as a sine — the walk's swing and the idle breath. */
function cyclic(phase: number, cycle: number): number {
  return Math.sin((phase / cycle) * Math.PI * 2);
}

/**
 * The posture a state puts a figure in, at a given phase of its cycle.
 *
 * Every branch is one of §E8.1's states and the switch is exhaustive, which the
 * linter enforces (§E26): a state that renders no posture is a figure that
 * vanishes, and the compiler is a better place to find that than the screen.
 */
export function posture(build: FigureBuild, state: FigureState, step: number): Posture {
  const rest = build.restLean;

  switch (state) {
    case "idle": {
      // Two frames of breath. Not decoration: a figure with no cycle at all
      // reads as a bug in the canvas rather than as a person standing still.
      const phase = phaseAt(step, 2);
      return {
        lean: rest,
        headTilt: 0,
        shoulderDrop: 0,
        armSwing: 0.04,
        stride: 0.02,
        bob: phase === 0 ? 0 : 0.004,
        phone: 0,
        weight: 1,
        cycle: 2,
      };
    }

    case "walk": {
      // Four phases: contact, passing, contact, passing — the shortest cycle
      // that reads as a walk rather than as a wobble.
      const cycle = 4;
      const phase = phaseAt(step, cycle);
      const swing = cyclic(phase, cycle);
      return {
        lean: rest + 0.05,
        headTilt: 0.02,
        shoulderDrop: 0,
        armSwing: swing * 0.42,
        stride: swing * 0.38,
        bob: Math.abs(swing) * 0.012,
        phone: 0,
        weight: 1,
        cycle,
      };
    }

    case "halt":
      return {
        lean: rest,
        headTilt: 0.05,
        shoulderDrop: 0,
        armSwing: 0.06,
        stride: 0.12,
        bob: 0,
        phone: 0,
        // A held pose is drawn heavier, which is what a paused animator's line
        // does — it sits on the frame longer and the ink spreads.
        weight: 1.1,
        cycle: 1,
      };

    case "observe":
      return {
        lean: rest + 0.16,
        headTilt: 0.55,
        shoulderDrop: 0.1,
        armSwing: 0.02,
        stride: 0.1,
        bob: -0.008,
        phone: 0,
        weight: 1.1,
        cycle: 1,
      };

    case "dejected":
      // §E16 Act 2: *"The figure's shoulders drop — one movement, held a full
      // second. That is the entire disappointment beat."* Held: one phase.
      return {
        lean: rest + 0.1,
        headTilt: 0.42,
        shoulderDrop: 1,
        armSwing: -0.05,
        stride: 0.04,
        bob: -0.022,
        phone: 0,
        weight: 1.15,
        cycle: 1,
      };

    case "report": {
      // Two phases: the phone is up and the framing hand adjusts. Small,
      // because somebody photographing a pothole is not performing.
      const cycle = 2;
      const phase = phaseAt(step, cycle);
      return {
        lean: rest + 0.06,
        headTilt: 0.3,
        shoulderDrop: 0.15,
        armSwing: 0,
        stride: 0.08,
        bob: phase === 0 ? 0 : -0.004,
        phone: phase === 0 ? 1 : 0.96,
        weight: 1,
        cycle,
      };
    }

    case "wait":
      return {
        lean: rest + 0.02,
        headTilt: 0.18,
        shoulderDrop: 0.25,
        armSwing: 0.03,
        stride: 0.06,
        bob: -0.006,
        phone: 0.35,
        weight: 1,
        cycle: 1,
      };

    case "confirmed":
      // Relief, and §E8's constraint makes this hard on purpose: no face, so
      // the whole of it is the shoulders coming back up and the head lifting
      // past level. Nothing celebrates. §22.2 is a constraint on the character
      // too, and a figure punching the air would be the product congratulating
      // itself for doing its job.
      return {
        lean: rest - 0.04,
        headTilt: -0.12,
        shoulderDrop: -0.08,
        armSwing: 0.1,
        stride: 0.03,
        bob: 0.014,
        weight: 1.05,
        phone: 0,
        cycle: 1,
      };
  }
}

export interface InkPoint {
  readonly x: number;
  readonly y: number;
  /** Relative line weight at this point, 0–1.5. §E8's *varying line weight*. */
  readonly w: number;
}

export interface Stroke {
  readonly points: readonly InkPoint[];
  readonly closed: boolean;
}

export interface FigureDrawing {
  /** The line, in draw order. */
  readonly strokes: readonly Stroke[];
  /**
   * The one warm fill — §E8's *"one warm fill that does not quite register with
   * the line, which is free because the press does it"*.
   *
   * A single closed silhouette, not a fill per stroke. The press offsets it
   * (`draw.ts`); this module does not know how far, because that is the
   * misregistration token and it belongs to the press.
   */
  readonly fill: Stroke;
}

function rotate(x: number, y: number, angle: number): [number, number] {
  const sin = Math.sin(angle);
  const cos = Math.cos(angle);
  return [x * cos - y * sin, x * sin + y * cos];
}

/**
 * Pose a build and return ink.
 *
 * Read top to bottom it is a skeleton: hip, chest, shoulders, head, two arms,
 * two legs, and the prop. The one thing it never draws is a face — there is no
 * eye, no mouth and no nose in this file, `tests/ink-figures.test.ts` asserts
 * that the head is exactly one closed stroke, and §E8's rule survives a
 * refactor because a second stroke inside the skull fails a test rather than a
 * review.
 */
export function strokes(build: FigureBuild, p: Posture): FigureDrawing {
  const scale = build.height;
  const hipY = 0.46 * scale + p.bob;
  const chestLength = 0.28 * scale;
  const [chestDx, chestDy] = rotate(0, chestLength, p.lean);
  const chestX = chestDx;
  const chestY = hipY + chestDy;

  // The shoulders sit on the chest and drop straight down, because that is what
  // a shoulder drop is — the joint falls, the torso does not fold.
  const shoulderY = chestY - p.shoulderDrop * 0.055 * scale;
  const span = build.shoulder * scale * (1 - p.shoulderDrop * 0.12);

  const neckY = shoulderY + 0.035 * scale;
  const headR = build.head * scale;
  // The skull sits *on* the neck. Found by drawing it: an earlier version
  // offset the centre by a full radius plus a gap and the head floated above
  // the shoulders like a balloon — which is the single most common way a
  // stick-figure skeleton stops reading as a body.
  const [headDx, headDy] = rotate(0, headR * 0.88, p.lean + p.headTilt * 0.55);
  const headX = chestX + headDx;
  const headY = neckY + headDy;

  const line: Stroke[] = [];

  // --- spine ---------------------------------------------------------------
  line.push({
    closed: false,
    points: [
      { x: 0, y: hipY, w: 1.25 },
      { x: chestX * 0.55, y: hipY + chestLength * 0.55, w: 1.05 },
      { x: chestX, y: neckY, w: 0.7 },
    ],
  });

  // --- head. One closed stroke, and nothing inside it. ---------------------
  const skull: InkPoint[] = [];
  const SKULL_POINTS = 14;
  for (let i = 0; i < SKULL_POINTS; i += 1) {
    const a = (i / SKULL_POINTS) * Math.PI * 2;
    // Not a circle: a head is taller than it is wide, and the tilt shears it.
    const [dx, dy] = rotate(Math.cos(a) * headR * 0.86, Math.sin(a) * headR, p.headTilt * 0.4);
    // Weight peaks at the crown and thins under the jaw, which is where a
    // brush lifts.
    skull.push({ x: headX + dx, y: headY + dy, w: 0.55 + 0.5 * Math.max(0, Math.sin(a)) });
  }
  line.push({ closed: true, points: skull });

  if (build.cap) {
    const brim = rotate(headR * 1.15, headR * 0.15, p.headTilt * 0.4 + p.lean);
    line.push({
      closed: false,
      points: [
        { x: headX - headR * 0.8, y: headY + headR * 0.5, w: 1.1 },
        { x: headX, y: headY + headR * 1.05, w: 1.2 },
        { x: headX + brim[0], y: headY + brim[1], w: 0.8 },
      ],
    });
  }

  // --- arms ----------------------------------------------------------------
  // The hands are recorded as they are drawn, because a prop that is not in a
  // hand is a prop floating beside a person. Found by drawing it: the Field
  // Hand's tool bag hung in mid-air a hand's width from the arm that was
  // supposed to be carrying it.
  const armLength = 0.17 * scale;
  const hands: Record<"lead" | "off", { x: number; y: number }> = {
    lead: { x: chestX, y: shoulderY - armLength },
    off: { x: chestX, y: shoulderY - armLength },
  };
  for (const side of [1, -1] as const) {
    const swing = side === 1 ? p.armSwing : -p.armSwing;
    const shoulderX = chestX + side * span;
    if (p.phone > 0 && side === 1) {
      // Both hands to the phone. The near arm is drawn; the far arm follows
      // below with a shallower bend, which is what stops the pose reading as
      // one-armed.
      const handY = shoulderY - armLength * (1 - p.phone * 0.45);
      hands.lead = { x: chestX + 0.05 * scale, y: handY };
      line.push({
        closed: false,
        points: [
          { x: shoulderX, y: shoulderY, w: 1 },
          { x: shoulderX + 0.045 * scale, y: shoulderY - armLength * 0.55, w: 0.85 },
          { x: chestX + 0.05 * scale, y: handY, w: 0.7 },
        ],
      });
      continue;
    }
    const [ex, ey] = rotate(0, -armLength * 0.55, swing);
    const [hx, hy] = rotate(0, -armLength, swing * 1.35);
    hands[side === 1 ? "lead" : "off"] = { x: shoulderX + hx, y: shoulderY + hy };
    line.push({
      closed: false,
      points: [
        { x: shoulderX, y: shoulderY, w: 1 },
        { x: shoulderX + ex + side * 0.012, y: shoulderY + ey, w: 0.85 },
        { x: shoulderX + hx, y: shoulderY + hy, w: 0.6 },
      ],
    });
  }

  if (p.phone > 0) {
    // The phone. A rectangle, held at the far arm's hand, and the only prop in
    // this module that is *state* rather than identity.
    const px = chestX + 0.05 * scale;
    const py = shoulderY - armLength * (1 - p.phone * 0.45);
    const w = 0.035 * scale;
    const h = 0.06 * scale;
    line.push({
      closed: true,
      points: [
        { x: px - w / 2, y: py - h / 2, w: 0.9 },
        { x: px + w / 2, y: py - h / 2, w: 0.9 },
        { x: px + w / 2, y: py + h / 2, w: 0.9 },
        { x: px - w / 2, y: py + h / 2, w: 0.9 },
      ],
    });
    // The far arm, shallower.
    line.push({
      closed: false,
      points: [
        { x: chestX - span, y: shoulderY, w: 0.95 },
        { x: chestX - span * 0.35, y: shoulderY - armLength * 0.5, w: 0.8 },
        { x: px - w * 0.6, y: py + h * 0.2, w: 0.6 },
      ],
    });
  }

  // --- legs ----------------------------------------------------------------
  const legLength = hipY;
  for (const side of [1, -1] as const) {
    const swing = side === 1 ? p.stride : -p.stride;
    const [kx, ky] = rotate(0, -legLength * 0.52, swing);
    const [fx, fy] = rotate(0, -legLength, swing * 0.8);
    line.push({
      closed: false,
      points: [
        { x: 0, y: hipY, w: 1.15 },
        { x: kx + side * 0.02, y: hipY + ky, w: 0.95 },
        { x: fx, y: Math.max(0, hipY + fy), w: 0.75 },
      ],
    });
  }

  // --- the prop ------------------------------------------------------------
  const propStroke = propFor(build, {
    chestX,
    shoulderY,
    span,
    hipY,
    scale,
    armLength,
    hands,
    hidden: p.phone > 0,
  });
  if (propStroke !== null) line.push(propStroke);

  // --- the warm fill -------------------------------------------------------
  // One closed silhouette: the torso. Not the whole figure — §E8 asks for *one*
  // warm fill, and a fill that traced every limb would be a colouring-in rather
  // than a second ink pass.
  const fill: Stroke = {
    closed: true,
    points: [
      { x: chestX - span * 0.86, y: shoulderY - 0.008 * scale, w: 1 },
      { x: chestX + span * 0.86, y: shoulderY - 0.008 * scale, w: 1 },
      { x: span * 0.52, y: hipY - 0.015 * scale, w: 1 },
      { x: -span * 0.52, y: hipY - 0.015 * scale, w: 1 },
    ],
  };

  // The posture's own weight, applied last and to the line only.
  //
  // §E7.2's held poses are drawn heavier because that is what a paused
  // animator's line does — it sits on the frame longer and the ink spreads.
  // The fill is untouched: a second pass of the same warm ink is a *coverage*,
  // and coverage does not thicken with a held frame.
  return {
    strokes: p.weight === 1 ? line : line.map((stroke) => weigh(stroke, p.weight)),
    fill,
  };
}

function weigh(stroke: Stroke, factor: number): Stroke {
  return {
    closed: stroke.closed,
    points: stroke.points.map((point) => ({ ...point, w: point.w * factor })),
  };
}

interface PropAnchors {
  readonly chestX: number;
  readonly shoulderY: number;
  readonly span: number;
  readonly hipY: number;
  readonly scale: number;
  readonly armLength: number;
  /** Where the two hands ended up. A prop hangs from one of these. */
  readonly hands: Readonly<Record<"lead" | "off", { readonly x: number; readonly y: number }>>;
  /** A hand holding a phone is not also holding a clipboard. */
  readonly hidden: boolean;
}

function propFor(build: FigureBuild, a: PropAnchors): Stroke | null {
  if (a.hidden && build.prop !== "bag") return null;

  switch (build.prop) {
    case "none":
      return null;

    case "bag": {
      // A strap across the chest. §E8.2's Reporter is *"ordinary, unheroic"*,
      // and a strap is the least a person can be carrying and still be going
      // somewhere.
      return {
        closed: false,
        points: [
          { x: a.chestX - a.span * 0.9, y: a.shoulderY, w: 0.8 },
          { x: a.chestX * 0.4, y: a.hipY + 0.12 * a.scale, w: 0.95 },
          { x: a.span * 0.95, y: a.hipY + 0.04 * a.scale, w: 0.7 },
        ],
      };
    }

    case "clipboard": {
      const x = a.hands.lead.x + 0.02 * a.scale;
      const y = a.hands.lead.y - 0.03 * a.scale;
      const w = 0.06 * a.scale;
      const h = 0.085 * a.scale;
      return {
        closed: true,
        points: [
          { x: x - w / 2, y: y - h / 2, w: 0.85 },
          { x: x + w / 2, y: y - h / 2, w: 0.85 },
          { x: x + w / 2, y: y + h / 2, w: 0.85 },
          { x: x - w / 2, y: y + h / 2, w: 0.85 },
        ],
      };
    }

    case "toolbag": {
      // Hanging *from* the off hand, so the bag's top edge is the grip.
      const x = a.hands.off.x;
      const y = a.hands.off.y;
      const w = 0.1 * a.scale;
      const h = 0.055 * a.scale;
      return {
        closed: true,
        points: [
          { x: x - w / 2, y: y, w: 1 },
          { x: x + w / 2, y: y, w: 1 },
          { x: x + w / 2, y: y - h, w: 0.9 },
          { x: x - w / 2, y: y - h, w: 0.9 },
        ],
      };
    }

    case "folder": {
      // Held flat against the body with both hands, which is the posture of
      // somebody waiting to be shown something rather than about to present.
      const cx = (a.hands.lead.x + a.hands.off.x) / 2;
      const y = (a.hands.lead.y + a.hands.off.y) / 2;
      const w = 0.115 * a.scale;
      const h = 0.05 * a.scale;
      return {
        closed: true,
        points: [
          { x: cx - w / 2, y: y - h / 2, w: 0.9 },
          { x: cx + w / 2, y: y - h / 2, w: 0.9 },
          { x: cx + w / 2, y: y + h / 2, w: 0.9 },
          { x: cx - w / 2, y: y + h / 2, w: 0.9 },
        ],
      };
    }
  }
}

/**
 * The whole drawing for one figure at one frame.
 *
 * The single entry point every renderer uses, so "what is on screen" has one
 * definition. `step` is the stepped clock's frame; nothing else about time
 * reaches this module.
 */
export function drawingFor(name: FigureName, state: FigureState, step: number): FigureDrawing {
  const build = BUILDS[name];
  return strokes(build, posture(build, state, step));
}
