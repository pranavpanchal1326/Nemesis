/**
 * The clay city kit, generated rather than modelled — F9, §4 of the phase plan.
 *
 * The phase plan calls missing art the largest risk in Stage 2, and a modelled
 * city is the largest piece of it: a hand-built ward is weeks of work, is
 * specific to one city, and cannot be checked into a repository whose Phase 29
 * gate is a clean checkout that boots air-gapped. So the kit is a function.
 *
 * **What this geometry is, said plainly: it is scenery.** It is not a survey,
 * it is not derived from any cadastral source, and no number on any screen is
 * computed from it. That distinction is load-bearing, because §6 Principle #9
 * requires every visual element to map to a real pipeline event and §E5's third
 * corollary says *"data is never decorative"* — which cuts both ways. Buildings
 * here are the *table the model sits on*: they establish scale and give the
 * tilt-shift something to be shallow against. The things that carry meaning —
 * pins, their glaze, their height — arrive from events and are built elsewhere,
 * in `pins.ts`, on purpose.
 *
 * §E13 already commits to this reading: *"even the 2D fallback map is a printed
 * photograph of the model, not a second aesthetic"*. A photograph of a table
 * needs a table.
 *
 * **Deterministic, from the tenant's own origin.** The seed is derived from the
 * projection origin, so Pune's clay is Pune's clay on every machine and in
 * every run — which is what makes a golden image at a fixed seed and camera
 * (§E24) mean anything at all. It also means the city does not re-roll when a
 * pin arrives, which a per-frame or per-index generator would do.
 *
 * **Cut-card, not extruded.** Every footprint is a rectangle with a small yaw
 * and a whole number of storeys. §E7.1's cut-card edges are what make that read
 * as cardboard and clay rather than as a data visualisation of nothing, and the
 * cheapness is the point: one box geometry, one instanced draw call for the
 * whole city.
 *
 * Roads are **not geometry**. They are the gaps the blocks leave, with the
 * ground plane showing through — which is both how a paper model is actually
 * cut and one fewer draw call than a road mesh would cost.
 */

import { WORLD } from "@/design/generated/tokens";
import { mulberry32 } from "@/lib/stepped-clock";
import type { GeoPoint } from "./projection";

export interface Footprint {
  /** Local ENU metres from the frame origin. */
  readonly east: number;
  readonly north: number;
  readonly width: number;
  readonly depth: number;
  readonly height: number;
  /** Yaw in radians. Small: a hand-cut card is never quite square. */
  readonly rotation: number;
  /** Which thumbprint rotation this block takes (§E7.1). */
  readonly grain: number;
  /** 0–1, baked into vertex colour as ambient occlusion (§E7.1). Blocks with
   *  close neighbours are darker at the base, which is the cheapest possible
   *  version of the thing that "sells handmade solid object". */
  readonly occlusion: number;
}

export interface CityKit {
  readonly footprints: readonly Footprint[];
  readonly seed: number;
  /** The radius actually filled, after the cap below took effect. */
  readonly radiusMetres: number;
}

export interface CityKitOptions {
  /** Only blocks within this distance of the origin are built. */
  readonly radiusMetres?: number;
  /**
   * A hard ceiling on instances.
   *
   * §E23 budgets 5 000 pins *plus* extruded buildings, and an unbounded
   * generator would happily produce eighty thousand of them for a wide extent
   * and blow the VRAM assertion in a way that looked like a renderer problem.
   * The cap is the honest place for that limit to live.
   */
  readonly maxFootprints?: number;
  readonly seed?: number;
}

export const DEFAULT_RADIUS_METRES = 1800;
export const DEFAULT_MAX_FOOTPRINTS = 4800;

/**
 * A seed that is a property of the place.
 *
 * Rounded to four decimals — about 11 m — so a centroid that shifts by a metre
 * when a ward is re-published does not re-roll the entire city, and two tenants
 * in different cities never collide.
 */
export function seedForOrigin(origin: GeoPoint): number {
  const lat = Math.round(origin.lat * 1e4);
  const lng = Math.round(origin.lng * 1e4);
  return (Math.imul(lat, 73856093) ^ Math.imul(lng, 19349663)) >>> 0;
}

export function generateCity(origin: GeoPoint, options: CityKitOptions = {}): CityKit {
  const radiusMetres = options.radiusMetres ?? DEFAULT_RADIUS_METRES;
  const maxFootprints = options.maxFootprints ?? DEFAULT_MAX_FOOTPRINTS;
  const seed = options.seed ?? seedForOrigin(origin);

  const pitch = WORLD.kit.blockMetres + WORLD.kit.roadMetres;
  const half = Math.floor(radiusMetres / pitch);
  const footprints: Footprint[] = [];

  // Concentric rings outward, so a city that hits the cap is a smaller city
  // rather than a quadrant of one. Row-major iteration truncated by a cap
  // produces a rectangle with a bite out of it, which reads as a bug.
  for (let ring = 0; ring <= half && footprints.length < maxFootprints; ring += 1) {
    for (const [bx, by] of ringCells(ring)) {
      if (footprints.length >= maxFootprints) break;
      const east = bx * pitch;
      const north = by * pitch;
      if (Math.hypot(east, north) > radiusMetres) continue;
      footprints.push(...blockFootprints(east, north, seed, bx, by));
    }
  }

  return { footprints: footprints.slice(0, maxFootprints), seed, radiusMetres };
}

/** The cells at exactly Chebyshev distance `ring` from the origin, in a stable
 *  order — so the same cap produces the same city on every machine. */
function ringCells(ring: number): readonly (readonly [number, number])[] {
  if (ring === 0) return [[0, 0]];
  const cells: [number, number][] = [];
  for (let x = -ring; x <= ring; x += 1) {
    cells.push([x, -ring], [x, ring]);
  }
  for (let y = -ring + 1; y <= ring - 1; y += 1) {
    cells.push([-ring, y], [ring, y]);
  }
  return cells;
}

/**
 * One block, subdivided.
 *
 * Two to four plots per block with a setback between them, because a block that
 * is one solid extrusion reads as a bar chart and a block cut into thirty reads
 * as noise. The subdivision axis alternates with the block's own parity so the
 * grain of the city is not uniformly north-south.
 */
function blockFootprints(
  east: number,
  north: number,
  seed: number,
  bx: number,
  by: number,
): readonly Footprint[] {
  // Seeded from the block's own coordinates, not from an iteration counter:
  // block (3, −7) is the same block whatever order it was reached in, and
  // whatever the radius was.
  const random = mulberry32(seed ^ (Math.imul(bx, 374761393) + Math.imul(by, 668265263)));

  const plots = 2 + Math.floor(random() * 3);
  const alongEast = (bx + by) % 2 === 0;
  const block = WORLD.kit.blockMetres;
  const setback = WORLD.kit.setbackMetres;
  const span = (block - setback * (plots - 1)) / plots;

  const out: Footprint[] = [];
  for (let i = 0; i < plots; i += 1) {
    const offset = -block / 2 + i * (span + setback) + span / 2;
    const storeys =
      WORLD.kit.minStoreys +
      Math.floor(random() * (WORLD.kit.maxStoreys - WORLD.kit.minStoreys + 1));

    // Distance from the centre thins the city out: a ward's edge is lower and
    // sparser than its middle, which is true of most places and is the cheapest
    // way to stop a generated grid reading as a chessboard.
    const falloff = 1 - Math.min(1, Math.hypot(east, north) / DEFAULT_RADIUS_METRES) * 0.55;
    const height = Math.max(WORLD.kit.storeyMetres, storeys * WORLD.kit.storeyMetres * falloff);

    out.push({
      east: east + (alongEast ? offset : 0),
      north: north + (alongEast ? 0 : offset),
      width: alongEast ? span : block,
      depth: alongEast ? block : span,
      height,
      // ±1.7°. Enough that no two edges are parallel, small enough that the
      // grid still reads as a grid.
      rotation: (random() - 0.5) * 0.06,
      grain: Math.floor(random() * 16),
      // Taller neighbours in a tighter plot occlude more. A crease between two
      // seven-storey blocks is darker than one beside a bungalow.
      occlusion: Math.min(1, (storeys / WORLD.kit.maxStoreys) * 0.7 + (1 - span / block) * 0.5),
    });
  }
  return out;
}
