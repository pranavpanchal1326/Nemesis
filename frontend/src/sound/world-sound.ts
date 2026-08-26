"use client";

/**
 * The city, heard — §E12, §E7.4, F16.
 *
 * Two of §E12's five bullets are about the *world* rather than about an
 * interaction, and both are here because both are loops rather than one-shots.
 *
 * **The ambient bed is on the model's clock, not the browser's.**
 *
 * > The city at the model's current time of day … cross-faded on the real clock
 * > (§E7.4).
 *
 * §E7.4's rule is that the model's weather and the contractor's deadline are
 * *the same fact*, and the hour is the same kind of claim: the bed is chosen by
 * the hour the scene is **lit** by, which `clay/sun.ts` computes from the
 * tenant's own local time. A bed cross-faded on `new Date()` in the visitor's
 * timezone would be a different city's evening.
 *
 * **Positional foley is an affordance and is treated as one.**
 *
 * > Every pin carries a quiet loop audible near the camera. Water in a pothole.
 * > A buzzing streetlight. A leaking main. **An operator can hear where the
 * > problems are before seeing them** — an affordance, not decoration, and
 * > therefore compliant with §6 Principle #9.
 *
 * Which imposes three things this module implements. Only entities near the
 * camera sound, because a city of five thousand pins droning at once is noise
 * rather than information. Only defect families with a *named* loop sound,
 * because a default loop for everything would mean nothing. And the pan and the
 * gain come from the entity's real position relative to the camera, so "where"
 * in "hear where the problems are" is true rather than decorative.
 *
 * **And it has no caller, deliberately — see
 * `docs/reports/positional-foley-gap.md`.** The only positioned data the
 * frontend receives is **wards**: `entitiesFromZones()` builds a `ClayEntity`
 * from a published zone, which carries a centroid, a name and a report count,
 * and carries no defect category — the same file already refuses to invent a
 * per-place severity for exactly this reason. A loop chosen from a ward's
 * *name* would be a sound claiming a fault type nobody detected, on the one
 * surface §6 Principle #9 admits sound onto because it carries information.
 *
 * So the mechanism is built, asserted (`tests/sound.test.ts`) and wired to
 * nothing, and the gap is named where it actually lives: in the read contract.
 * `ComplaintResponse` already carries `category`, `latitude` and `longitude` —
 * a categorised, positioned read is a small backend change and not a redesign.
 */

import { SOUND } from "@/design/generated/tokens";

import { BEDS, DEFECT_LOOPS, bedForSun, type BedName, type DefectLoopName } from "./cues";
import { sound } from "./graph";

/** What this module needs to know about an entity: where it is, and what kind. */
export interface AudibleEntity {
  readonly id: string;
  /** Metres east of the scene origin. */
  readonly x: number;
  /** Metres north of the scene origin. */
  readonly z: number;
  /** The taxonomy category, as the tenant spells it. */
  readonly category: string | null;
}

/** Where the listener is, in the same metres. */
export interface Listener {
  readonly x: number;
  readonly z: number;
}

/**
 * Which loop a category gets, or `null` for the categories that get silence.
 *
 * Matched on substrings of the *tenant's own* category name, because §5's whole
 * claim is that a city invents its own taxonomy and the frontend never ships a
 * closed list of defect types. A category nobody matched is silent — which is
 * the honest outcome and, on a tenant with an unusual taxonomy, the common one.
 */
export function loopForCategory(category: string | null): DefectLoopName | null {
  if (category === null) return null;
  const name = category.toLowerCase();
  if (/water|leak|drain|sewer|pothole|flood/.test(name)) return "water";
  if (/light|electric|pole|wire|transformer/.test(name)) return "electrical";
  if (/waste|garbage|refuse|litter|dump|sanit/.test(name)) return "waste";
  return null;
}

export interface Voice {
  readonly id: string;
  readonly loop: DefectLoopName;
  /** −1 to 1. */
  readonly pan: number;
  /** 0 to 1, before the bus gain. */
  readonly gain: number;
}

/**
 * Which entities should be audible, and how loud, from where.
 *
 * A pure function so `tests/sound.test.ts` can assert the affordance —
 * that a nearer defect is louder, that one to the left pans left, and that the
 * voice count is capped — without an `AudioContext`. The cap is `maxVoices`
 * and the *nearest* survive it: an operator listening for the worst problem
 * nearby is not helped by twelve arbitrary ones.
 */
export function voicesFor(
  entities: readonly AudibleEntity[],
  listener: Listener,
): readonly Voice[] {
  const { refDistanceMetres, maxDistanceMetres, rolloff, maxVoices } = SOUND.positional;

  const candidates: { voice: Voice; distance: number }[] = [];
  for (const entity of entities) {
    const loop = loopForCategory(entity.category);
    if (loop === null) continue;
    const dx = entity.x - listener.x;
    const dz = entity.z - listener.z;
    const distance = Math.hypot(dx, dz);
    if (distance > maxDistanceMetres) continue;

    // Inverse-distance rolloff, the same law a Web Audio `PannerNode` uses —
    // written out rather than delegated because the gain has to be knowable
    // without a context, and because a panner per pin is twelve panners.
    const gain =
      refDistanceMetres / (refDistanceMetres + rolloff * Math.max(0, distance - refDistanceMetres));
    // Pan by bearing across the listener, clamped. A defect directly ahead is
    // centred; one to the east is to the right.
    const pan = distance === 0 ? 0 : Math.max(-1, Math.min(1, dx / Math.max(distance, 1)));
    candidates.push({ voice: { id: entity.id, loop, pan, gain }, distance });
  }

  candidates.sort((a, b) => a.distance - b.distance);
  return candidates.slice(0, maxVoices).map((candidate) => candidate.voice);
}

/**
 * Reconcile the playing voices against the voices that *should* be playing.
 *
 * Returns the new set of handles. Called whenever the camera or the entity list
 * changes, and it deliberately does not restart a voice that is already
 * playing: a loop that restarted every time the camera moved would be a
 * stutter, which is the exact opposite of "you can hear where it is".
 */
export function reconcileVoices(
  playing: Map<string, () => void>,
  wanted: readonly Voice[],
): Map<string, () => void> {
  const next = new Map<string, () => void>();
  const keep = new Set(wanted.map((voice) => voice.id));

  for (const [id, stop] of playing) {
    if (keep.has(id)) next.set(id, stop);
    else stop();
  }

  for (const voice of wanted) {
    if (next.has(voice.id)) continue;
    const cue = DEFECT_LOOPS[voice.loop];
    next.set(
      voice.id,
      sound.loop(voice.loop, cue.bus, cue.recipe, {
        gain: voice.gain,
        pan: voice.pan,
        fadeMs: SOUND.crossfadeMs / 4,
      }),
    );
  }

  return next;
}

/**
 * Start one ambient bed and return the stopper.
 *
 * The caller cross-fades by starting the new bed *before* stopping the old one
 * — both ramps are `SOUND.crossfadeMs`, so the two sum to a constant and the
 * city does not dip at the seam between morning and midday.
 */
export function startBed(name: BedName): () => void {
  const bed = BEDS[name];
  return sound.loop(name, bed.bus, bed.recipe, { fadeMs: SOUND.crossfadeMs });
}

export { bedForSun };
