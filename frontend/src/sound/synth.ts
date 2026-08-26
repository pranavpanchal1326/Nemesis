/**
 * The sound library, computed — §E12, ADR-0050, F16.
 *
 * **Nothing in this module touches the Web Audio API.** It takes a recipe and
 * returns a `Float32Array` of samples, using arithmetic and a seeded PRNG. That
 * is a deliberate seam and it buys three things:
 *
 * · **Determinism.** The same recipe and the same seed produce the same
 *   samples on every machine, so a cue can be asserted — `tests/sound.test.ts`
 *   checks the merge's four transients land where §E12 says they land, and a
 *   change to an envelope shows up as a failing number rather than as a vague
 *   sense that it sounds different.
 * · **Testability with no browser.** `OfflineAudioContext` exists and would
 *   have been the obvious tool; it is asynchronous, it is not in jsdom, and its
 *   filters are implementation-defined, which makes it exactly the wrong place
 *   to put a claim somebody has to check.
 * · **Nothing to commit.** The artefact is this file (ADR-0050).
 *
 * **The filters are one-pole and that is a decision, not a shortcut.** A
 * biquad would be crisper and would need state per channel per stage and a
 * stability argument; a one-pole low-pass and its high-pass complement are four
 * lines each, are unconditionally stable, and at 24 kHz over a 200 ms burst the
 * difference is inaudible. The sample rate is `SOUND.sampleRate` — 24 kHz
 * rather than 48 — because every cue here is band-limited well below 12 kHz and
 * halving the rate halves the render cost and the memory.
 */

import { SOUND } from "@/design/generated/tokens";
import { mulberry32 } from "@/lib/stepped-clock";

import type { Recipe } from "./cues";

const RATE = SOUND.sampleRate;

/** Samples for a duration in milliseconds. */
function samples(ms: number): number {
  return Math.max(1, Math.round((ms / 1000) * RATE));
}

/**
 * A percussive envelope.
 *
 * `bite` moves the attack from a slow swell (0) to an immediate transient (1).
 * One knob rather than the usual four, because every sound in §E12 is a *thing
 * happening once* and attack-decay-sustain-release is a shape for notes that
 * are held.
 */
function envelope(index: number, total: number, bite: number): number {
  const t = index / total;
  const attack = Math.max(0.0015, (1 - bite) * 0.35);
  if (t < attack) return t / attack;
  const decayed = (t - attack) / (1 - attack);
  // Exponential decay, steeper the more bite it has. `+ 1` keeps a soft cue
  // from ringing for its whole length.
  return Math.exp(-decayed * (3 + bite * 9));
}

/** One-pole low-pass, in place. */
function lowPass(buffer: Float32Array, hz: number): void {
  const alpha = 1 - Math.exp((-2 * Math.PI * hz) / RATE);
  let previous = 0;
  for (let i = 0; i < buffer.length; i += 1) {
    previous += alpha * ((buffer[i] ?? 0) - previous);
    buffer[i] = previous;
  }
}

/** One-pole high-pass, in place — the low-pass's complement. */
function highPass(buffer: Float32Array, hz: number): void {
  const alpha = 1 - Math.exp((-2 * Math.PI * hz) / RATE);
  let previous = 0;
  for (let i = 0; i < buffer.length; i += 1) {
    const input = buffer[i] ?? 0;
    previous += alpha * (input - previous);
    buffer[i] = input - previous;
  }
}

/** Peak-normalise, so a cue's loudness is its gain rather than its arithmetic. */
function normalise(buffer: Float32Array, to: number): void {
  let peak = 0;
  for (const value of buffer) peak = Math.max(peak, Math.abs(value));
  if (peak === 0) return;
  const scale = to / peak;
  for (let i = 0; i < buffer.length; i += 1) buffer[i] = (buffer[i] ?? 0) * scale;
}

/**
 * How long a bed's *loop* is, in samples.
 *
 * Shorter than its nominal length by the cross-fade, and that subtraction is
 * the whole point. **Found by `tests/sound.test.ts`:** the first version
 * returned the nominal length and rendered the faded tail as silence, so a
 * `source.loop = true` bed played eight seconds of city followed by four
 * hundred milliseconds of nothing, once a lap, forever. The seam fold is only
 * a seam fold if the folded part is then dropped.
 */
function bedLoopLength(seconds: number): { readonly total: number; readonly fade: number } {
  const total = Math.round(seconds * RATE);
  const fade = Math.min(samples(400), Math.floor(total / 4));
  return { total, fade };
}

/**
 * How long a recipe lasts, in samples.
 *
 * Separate from rendering because a sequence has to size its own buffer before
 * it can render its parts into it.
 */
export function lengthOf(recipe: Recipe): number {
  switch (recipe.kind) {
    case "brush":
    case "thud":
    case "struck":
      return samples(recipe.ms);
    case "bed": {
      const { total, fade } = bedLoopLength(recipe.seconds);
      return total - fade;
    }
    case "sequence": {
      let end = 0;
      for (const part of recipe.parts) {
        end = Math.max(end, samples(part.atMs) + lengthOf(part.recipe));
      }
      return end;
    }
  }
}

/**
 * Render a recipe to samples.
 *
 * `seed` defaults to the token, so two calls for the same cue produce
 * bit-identical audio — which is what makes a cue cacheable and a test
 * meaningful.
 */
export function render(recipe: Recipe, seed: number = SOUND.seed): Float32Array {
  const out = new Float32Array(lengthOf(recipe));
  into(out, 0, recipe, seed);
  return out;
}

function into(out: Float32Array, offset: number, recipe: Recipe, seed: number): void {
  switch (recipe.kind) {
    case "brush": {
      const total = samples(recipe.ms);
      const scratch = new Float32Array(total);
      const random = mulberry32(seed);
      for (let i = 0; i < total; i += 1) scratch[i] = random() * 2 - 1;
      lowPass(scratch, recipe.highHz);
      highPass(scratch, recipe.lowHz);
      for (let i = 0; i < total; i += 1) {
        scratch[i] = (scratch[i] ?? 0) * envelope(i, total, recipe.bite);
      }
      normalise(scratch, 1);
      add(out, offset, scratch);
      return;
    }

    case "thud": {
      const total = samples(recipe.ms);
      const scratch = new Float32Array(total);
      const random = mulberry32(seed + 1);
      for (let i = 0; i < total; i += 1) {
        const t = i / RATE;
        // A body that drops in pitch as it decays, which is what a struck
        // surface does and what makes a sine read as an object rather than as
        // a tone.
        const hz = recipe.hz * (1 - 0.25 * (i / total));
        const body = Math.sin(2 * Math.PI * hz * t);
        // The transient: a few milliseconds of noise on the front, which is
        // where a listener actually locates the material.
        const transient = i < samples(6) ? (random() * 2 - 1) * recipe.bite : 0;
        scratch[i] = (body * 0.85 + transient * 0.6) * envelope(i, total, recipe.bite);
      }
      normalise(scratch, 1);
      add(out, offset, scratch);
      return;
    }

    case "struck": {
      const total = samples(recipe.ms);
      const scratch = new Float32Array(total);
      for (let i = 0; i < total; i += 1) {
        const t = i / RATE;
        let value = 0;
        recipe.partials.forEach((ratio, index) => {
          // Higher partials decay faster — the reason a real bar's tone gets
          // purer as it rings out, and the difference between metal and a
          // synthesiser pretending to be metal.
          const decay = Math.exp((-t * (2.2 + index * 2.6) * 1000) / recipe.ms);
          value += Math.sin(2 * Math.PI * recipe.hz * ratio * t) * decay * (1 / (index + 1));
        });
        scratch[i] = value;
      }
      normalise(scratch, 1);
      add(out, offset, scratch);
      return;
    }

    case "bed": {
      const { total, fade } = bedLoopLength(recipe.seconds);
      const scratch = new Float32Array(total);
      const random = mulberry32(seed + 2);
      for (let i = 0; i < total; i += 1) scratch[i] = random() * 2 - 1;
      lowPass(scratch, recipe.highHz);
      highPass(scratch, recipe.lowHz);

      // The drone. One low partial, tuned to the band's floor, at whatever
      // share `tone` asks for.
      for (let i = 0; i < total; i += 1) {
        const t = i / RATE;
        const drone = Math.sin(2 * Math.PI * recipe.lowHz * 0.5 * t);
        scratch[i] = (scratch[i] ?? 0) * (1 - recipe.tone) + drone * recipe.tone * 0.4;
      }

      // **The seam is cross-faded into itself so the loop does not click.** A
      // bed that restarts on a discontinuity is a metronome, and a metronome in
      // an ambient bed is the most irritating sound in any product. The faded
      // tail is then dropped rather than kept as silence — see `bedLoopLength`.
      for (let i = 0; i < fade; i += 1) {
        const mix = i / fade;
        const head = scratch[i] ?? 0;
        const tail = scratch[total - fade + i] ?? 0;
        scratch[i] = head * mix + tail * (1 - mix);
      }
      const looped = scratch.subarray(0, total - fade);
      normalise(looped, 1);
      add(out, offset, looped);
      return;
    }

    case "sequence": {
      for (const part of recipe.parts) {
        // Each part gets its own seed derived from where it starts, so the
        // three taps of a merge are not three copies of one noise burst.
        into(out, offset + samples(part.atMs), part.recipe, seed + part.atMs);
      }
      normalise(out, 1);
      return;
    }
  }
}

function add(out: Float32Array, offset: number, source: Float32Array): void {
  const limit = Math.min(source.length, out.length - offset);
  for (let i = 0; i < limit; i += 1) {
    out[offset + i] = (out[offset + i] ?? 0) + (source[i] ?? 0);
  }
}
