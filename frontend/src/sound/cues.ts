/**
 * The whole sound vocabulary — §E12, §E3.4, F16, ADR-0050.
 *
 * **This file is the audit.** §E3.4:
 *
 * > Colour, motion, and sound each carry exactly one meaning, or none. A
 * > vocabulary that means two things means nothing. Severity ink never
 * > decorates. The stamp only confirms. Bloom only fires on the safety
 * > fail-safe. **Enforced in review, not by taste.**
 *
 * F16 replaces "in review" with a grep (`scripts/check-guards.ts`,
 * `single-meaning`), and this table is what the grep is checked against: every
 * cue in the product is declared here with **the one thing it means**, and no
 * two rows may mean the same thing. `tests/sound.test.ts` asserts
 * the meanings are distinct, which is the mechanical form of "a vocabulary that
 * means two things means nothing".
 *
 * **Nothing here is a file** (ADR-0050). A cue is a recipe: a shape, an
 * envelope and a handful of numbers out of `SOUND`, rendered into samples by
 * `synth.ts` at the moment it is first needed and cached for the session.
 *
 * **Four buses, and the split is §E12's own.** Ambient is the city; foley is
 * something a person did; positional is something a *defect* is doing, out
 * there in the model; alert is the one alarming sound in the product. They have
 * separate gains because they are separately meaningful — an operator who turns
 * the city down to hear the queue is doing something the product should let
 * them do.
 */

import { SOUND, WEATHER } from "@/design/generated/tokens";

/** §E12's four buses. */
export const BUSES = ["ambient", "foley", "positional", "alert"] as const;

export type Bus = (typeof BUSES)[number];

/**
 * How a cue is made.
 *
 * Four shapes, deliberately: every sound in §E12 is one of *something struck*,
 * *something brushed*, *something dropped*, or *the city*. Adding a fifth shape
 * should feel like a decision, because it is one.
 */
export type Recipe =
  /** A filtered noise burst. Paper, a roller, a shutter — anything brushed. */
  | {
      readonly kind: "brush";
      readonly ms: number;
      readonly lowHz: number;
      readonly highHz: number;
      /** 0 = flat, 1 = a hard percussive attack that decays immediately. */
      readonly bite: number;
    }
  /** A low body with a noise transient on it. Anything dropped or pressed. */
  | {
      readonly kind: "thud";
      readonly ms: number;
      readonly hz: number;
      readonly bite: number;
    }
  /** A struck bar: inharmonic partials, long decay. Metal, and only metal. */
  | {
      readonly kind: "struck";
      readonly ms: number;
      readonly hz: number;
      readonly partials: readonly number[];
    }
  /** Several cues in time. The merge is the only one. */
  | {
      readonly kind: "sequence";
      readonly parts: readonly { readonly atMs: number; readonly recipe: Recipe }[];
    }
  /** A loopable bed of filtered noise and a low drone. The city. */
  | {
      readonly kind: "bed";
      readonly seconds: number;
      readonly lowHz: number;
      readonly highHz: number;
      /** How much of the bed is tonal rather than air, 0–1. */
      readonly tone: number;
    };

export interface Cue {
  readonly bus: Bus;
  /**
   * **The one thing this sound means.**
   *
   * Prose, deliberately — the audit compares these for duplicates and a human
   * reads them in review, and a slug would let two cues share a meaning by
   * accident while looking different.
   */
  readonly means: string;
  readonly recipe: Recipe;
}

const F = SOUND.foley;

/**
 * The interaction foley §E12 lists, and nothing else.
 *
 * > Interaction foley, recorded from paper and clay: stamp thud, paper slide on
 * > panel open, pin push on select, page turn on route change, shutter on
 * > capture, and a roller pass on print transitions.
 *
 * Six, and the section names all six. ADR-0050 records what is lost by
 * synthesising rather than recording them.
 */
export const CUES = {
  stamp: {
    bus: "foley",
    means: "a decision was recorded — §E11.1's Stamp, and only the Stamp",
    recipe: { kind: "thud", ms: F.bodyMs + F.tailMs, hz: 96, bite: 0.85 },
  },
  paperSlide: {
    bus: "foley",
    means: "a panel opened",
    recipe: { kind: "brush", ms: F.tailMs, lowHz: 420, highHz: F.highHz, bite: 0.25 },
  },
  pinPush: {
    bus: "foley",
    means: "something on the map was selected",
    recipe: { kind: "thud", ms: F.bodyMs, hz: 210, bite: 0.6 },
  },
  pageTurn: {
    bus: "foley",
    means: "the route changed",
    recipe: { kind: "brush", ms: F.bodyMs + F.tailMs, lowHz: 300, highHz: 3600, bite: 0.4 },
  },
  shutter: {
    bus: "foley",
    means: "a photograph was taken",
    recipe: { kind: "brush", ms: F.bodyMs, lowHz: 900, highHz: F.highHz, bite: 0.95 },
  },
  rollerPass: {
    bus: "foley",
    means: "the press printed a transition",
    recipe: { kind: "brush", ms: 420, lowHz: F.lowHz, highHz: 1800, bite: 0.1 },
  },

  /**
   * §E12: *"the merge has its own cue: three soft taps converging into one low
   * thump."* Written as a sequence rather than as three calls, because the
   * spacing **is** the cue — three taps fired by three timers would be three
   * sounds that happened to be near each other.
   */
  merge: {
    bus: "foley",
    means: "two reports became one incident",
    recipe: {
      kind: "sequence",
      parts: [
        {
          atMs: 0,
          recipe: { kind: "thud", ms: SOUND.merge.tapMs, hz: SOUND.merge.tapHz, bite: 0.5 },
        },
        {
          atMs: SOUND.merge.gapMs,
          recipe: { kind: "thud", ms: SOUND.merge.tapMs, hz: SOUND.merge.tapHz * 0.84, bite: 0.5 },
        },
        {
          atMs: SOUND.merge.gapMs * 2,
          recipe: { kind: "thud", ms: SOUND.merge.tapMs, hz: SOUND.merge.tapHz * 0.7, bite: 0.5 },
        },
        {
          atMs: SOUND.merge.gapMs * 3,
          recipe: { kind: "thud", ms: SOUND.merge.thumpMs, hz: SOUND.merge.thumpHz, bite: 0.3 },
        },
      ],
    },
  },

  /**
   * §E12: *"`safety_trigger_fired` is the only alarming sound in the product,
   * and it is a single struck metal note, not a klaxon. §11.2's fail-safe
   * should feel grave, not panicked."*
   *
   * The partials are a struck bar's, not a piano's: 1 : 2.76 : 5.4 : 8.93 is
   * the classic inharmonic series, which is why it reads as *metal* rather than
   * as a note. Grave rather than panicked is the decay — two and a half seconds
   * of ring, with nothing repeating.
   */
  alarm: {
    bus: "alert",
    means: "the safety fail-safe fired",
    recipe: {
      kind: "struck",
      ms: SOUND.note.decayMs,
      hz: SOUND.note.hz,
      partials: [1, SOUND.note.partial2, SOUND.note.partial3, SOUND.note.partial4],
    },
  },
} as const satisfies Record<string, Cue>;

export type CueName = keyof typeof CUES;

/**
 * §E12's three ambient beds, on the model's own clock (§E7.4).
 *
 * > The city at the model's current time of day. Morning birds and a distant
 * > bell; midday traffic and a hawker; dusk crickets and a gully cricket match.
 * > Cross-faded on the real clock.
 *
 * Synthesised, so what these actually are is three differently-shaped bands of
 * air with a drone under them — brighter and thinner at morning, broad and low
 * at midday, narrow and warm at dusk. ADR-0050 is explicit that this is the
 * weakest part of the library: a synthesised bed is *a time of day* and is not
 * *birds*. It is here because cross-fading it on the SLA engine's own hour is
 * the thing §E7.4 asks for, and that part is real.
 */
export const BEDS = {
  morning: {
    bus: "ambient",
    means: "the model's clock is at morning",
    recipe: {
      kind: "bed",
      seconds: SOUND.ambient.loopSeconds,
      lowHz: 320,
      highHz: SOUND.ambient.highHz,
      tone: 0.25,
    },
  },
  midday: {
    bus: "ambient",
    means: "the model's clock is at midday",
    recipe: {
      kind: "bed",
      seconds: SOUND.ambient.loopSeconds,
      lowHz: SOUND.ambient.lowHz,
      highHz: 1400,
      tone: 0.4,
    },
  },
  dusk: {
    bus: "ambient",
    means: "the model's clock is at dusk",
    recipe: {
      kind: "bed",
      seconds: SOUND.ambient.loopSeconds,
      lowHz: 180,
      highHz: 900,
      tone: 0.55,
    },
  },
} as const satisfies Record<string, Cue>;

export type BedName = keyof typeof BEDS;

/**
 * The bed the model's time of day calls for — **from the sun the scene is lit
 * by**, not from a clock.
 *
 * §E7.4's rule is that the model's weather and the contractor's deadline are
 * *the same fact* rather than two correlated ones, and the hour is the same
 * kind of claim. `clay/sun.ts` computes a real solar position over the tenant's
 * own origin and the scene's key light comes from its altitude; taking the bed
 * from the same two numbers means the city cannot sound like morning while it
 * looks like dusk.
 *
 * `WEATHER.fullLightDeg` is the same threshold `keyIntensity()` ramps to, so
 * the bed changes on the frame the light stops climbing. Below it, the azimuth
 * decides: a sun in the eastern half of the sky is climbing, one in the western
 * half is going down, and that is the whole difference between the morning bed
 * and the dusk one.
 */
export function bedForSun(altitudeDeg: number, azimuthDeg: number): BedName {
  if (altitudeDeg >= WEATHER.fullLightDeg) return "midday";
  const azimuth = ((azimuthDeg % 360) + 360) % 360;
  return azimuth < 180 ? "morning" : "dusk";
}

/**
 * §E12's positional foley — *"an operator can hear where the problems are
 * before seeing them"*.
 *
 * Keyed by the defect families the taxonomy actually produces, and a report
 * whose category is not one of these gets **no loop** rather than a default
 * one. §6 Principle #9: a sound that fires for everything is decoration, and
 * decoration is what this section is explicitly not.
 */
export const DEFECT_LOOPS = {
  water: {
    bus: "positional",
    means: "standing water or a leak is at this location",
    recipe: {
      kind: "bed",
      seconds: SOUND.positional.loopSeconds,
      lowHz: 240,
      highHz: 2600,
      tone: 0.15,
    },
  },
  electrical: {
    bus: "positional",
    means: "a failed light or an electrical fault is at this location",
    recipe: {
      kind: "bed",
      seconds: SOUND.positional.loopSeconds,
      lowHz: 100,
      highHz: 320,
      tone: 0.9,
    },
  },
  waste: {
    bus: "positional",
    means: "uncollected waste is at this location",
    recipe: {
      kind: "bed",
      seconds: SOUND.positional.loopSeconds,
      lowHz: 140,
      highHz: 900,
      tone: 0.05,
    },
  },
} as const satisfies Record<string, Cue>;

export type DefectLoopName = keyof typeof DEFECT_LOOPS;

/** Every cue in the product, in one map — what the §E3.4 audit iterates. */
export const VOCABULARY: Readonly<Record<string, Cue>> = {
  ...CUES,
  ...BEDS,
  ...DEFECT_LOOPS,
};
