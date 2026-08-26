/**
 * The thumbprint normal — §E7.1, generated rather than authored.
 *
 * > **Thumbprint normal** — a single tiling 512² normal at low amplitude
 * > (~0.06), rotated per instance by a hash of the entity id, so nothing tiles
 * > visibly.
 *
 * §4 of the phase plan names the missing art as the largest risk in Stage 2,
 * and this is one of the tiles it names. It is generated here for two reasons,
 * and only one of them is that the asset does not exist.
 *
 * The first is that a normal map is a *function*, and this one's parameters are
 * already tokens: amplitude and tile size are in `tokens.json` because the
 * material recipe put them there. Shipping a PNG would mean the amplitude lived
 * in two places — a token nothing reads and a baked-in pixel value — which is
 * exactly the drift §E24 exists to prevent. Changing `clay.thumbprint.amplitude`
 * changes the tile, because the tile is derived from it.
 *
 * The second is that the tile has to **wrap**. A clay surface with a visible
 * seam every 512 texels is worse than no normal at all, and a seam is very easy
 * to introduce and very hard to notice in review. The noise here is periodic by
 * construction — the lattice indices are taken modulo the period — so the wrap
 * is a property of the algorithm rather than of the care taken while painting
 * it, and `tests/clay-material.test.ts` asserts the opposite edges match
 * exactly.
 *
 * **This is a height field turned into normals**, which is how clay actually
 * behaves: a thumb pressed into a surface displaces it, and the shading follows
 * from the displacement. Generating the normals directly would let the x and y
 * channels disagree about a surface that cannot exist.
 *
 * Pure, `Uint8Array` out, no three.js import — so the seam and the determinism
 * are assertable in a Node test. `createThumbprintTexture()` in `clay-tsl.ts`
 * is the two lines that wrap it in a `DataTexture`.
 */

import { CLAY } from "@/design/generated/tokens";
import { mulberry32 } from "@/lib/stepped-clock";

/**
 * Lattice periods, coarse to fine.
 *
 * Each divides 512, which is what makes every octave wrap on the same
 * boundary. A "nicer" set like 6/13/29 would give better-looking noise and a
 * seam, and a seam is not a trade this surface can make.
 */
export const OCTAVE_PERIODS = [8, 16, 32, 64] as const;

/** RGBA, because a `DataTexture` wants four channels and the alpha is free. */
export const CHANNELS = 4;

/**
 * One tiling normal map, as bytes.
 *
 * `amplitude` scales the *height field*, not the encoded normal — a surface
 * gets bumpier, rather than the same surface being lied about more loudly.
 */
export function thumbprintTile(
  size: number = CLAY.thumbprint.tilePx,
  amplitude: number = CLAY.thumbprint.amplitude,
  seed = 1,
): Uint8Array {
  const height = heightField(size, seed);
  const out = new Uint8Array(size * size * CHANNELS);

  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      // Central differences with wrapped neighbours. The wrap is the whole
      // point: sampling a clamped edge here would put a bright line down two
      // sides of every tile, which is the seam by another route.
      const left = height[index(x - 1, y, size)] ?? 0;
      const right = height[index(x + 1, y, size)] ?? 0;
      const down = height[index(x, y - 1, size)] ?? 0;
      const up = height[index(x, y + 1, size)] ?? 0;

      const dx = (right - left) * amplitude * size * 0.5;
      const dy = (up - down) * amplitude * size * 0.5;

      const length = Math.hypot(-dx, -dy, 1);
      const at = (y * size + x) * CHANNELS;
      out[at] = encode(-dx / length);
      out[at + 1] = encode(-dy / length);
      out[at + 2] = encode(1 / length);
      out[at + 3] = 255;
    }
  }
  return out;
}

/** Tangent-space normals are stored biased into 0…1; this is that bias, at
 *  byte precision, and it is why a flat surface encodes as `(128, 128, 255)`. */
function encode(component: number): number {
  return Math.max(0, Math.min(255, Math.round((component * 0.5 + 0.5) * 255)));
}

function index(x: number, y: number, size: number): number {
  return (((y % size) + size) % size) * size + (((x % size) + size) % size);
}

/**
 * Periodic value noise, four octaves, each amplitude-halved.
 *
 * Value noise rather than gradient noise on purpose. Perlin's characteristic
 * signature is a field with zeros *at* every lattice point, which reads as a
 * regular grid of flat spots — visible on a matte surface at grazing angles,
 * and this material is nothing but grazing angles. Value noise has no such
 * structure, and at four octaves its blockiness is gone.
 */
function heightField(size: number, seed: number): Float64Array {
  const field = new Float64Array(size * size);
  let amplitude = 1;
  let total = 0;

  OCTAVE_PERIODS.forEach((period, octave) => {
    const lattice = latticeFor(period, seed + octave * 7919);
    for (let y = 0; y < size; y += 1) {
      for (let x = 0; x < size; x += 1) {
        const at = y * size + x;
        field[at] =
          (field[at] ?? 0) +
          amplitude * sample(lattice, period, (x / size) * period, (y / size) * period);
      }
    }
    total += amplitude;
    amplitude *= 0.5;
  });

  for (let i = 0; i < field.length; i += 1) field[i] = (field[i] ?? 0) / total;
  return field;
}

function latticeFor(period: number, seed: number): Float64Array {
  const random = mulberry32(seed);
  const lattice = new Float64Array(period * period);
  for (let i = 0; i < lattice.length; i += 1) lattice[i] = random() * 2 - 1;
  return lattice;
}

function sample(lattice: Float64Array, period: number, x: number, y: number): number {
  const x0 = Math.floor(x);
  const y0 = Math.floor(y);
  const tx = fade(x - x0);
  const ty = fade(y - y0);

  const at = (ix: number, iy: number) =>
    lattice[(((iy % period) + period) % period) * period + (((ix % period) + period) % period)] ??
    0;

  const top = lerp(at(x0, y0), at(x0 + 1, y0), tx);
  const bottom = lerp(at(x0, y0 + 1), at(x0 + 1, y0 + 1), tx);
  return lerp(top, bottom, ty);
}

/** Ken Perlin's quintic. Its second derivative is zero at both ends, which is
 *  what keeps the *normals* — a derivative of this field — continuous across a
 *  lattice boundary. A cubic would wrap without a seam in height and with one
 *  in shading, which is the seam that actually shows. */
function fade(t: number): number {
  return t * t * t * (t * (t * 6 - 15) + 10);
}

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

/**
 * Which of `clay.thumbprint.rotationSteps` rotations an entity gets.
 *
 * §E7.1 says *"rotated per instance by a hash of the entity id"*, and the
 * emphasis is on **id**: rotating by instance index would re-roll every
 * building's grain whenever the list re-sorted, so a ward would visibly
 * shimmer when a pin arrived somewhere else entirely.
 */
export function thumbprintRotation(id: string): number {
  let hash = 2166136261;
  for (let i = 0; i < id.length; i += 1) {
    hash ^= id.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  const step = (hash >>> 0) % CLAY.thumbprint.rotationSteps;
  return (step / CLAY.thumbprint.rotationSteps) * Math.PI * 2;
}
