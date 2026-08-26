/**
 * The geometry the clay is made of — F9 and F10.
 *
 * Two shapes and one draw call each. §E23 budgets 32 draw calls for the whole
 * frame and the city is the part most likely to eat them, so the whole of a
 * ward's built form is **one** `InstancedMesh` of a unit box, and the whole of
 * its live state is **one** `InstancedMesh` of a low cylinder.
 *
 * **Ambient occlusion is baked into vertex colours, here, at build time.**
 * §E7.1 says *"baked AO"* and means it literally: no SSAO pass, no second
 * render target, no per-frame cost at all. A box's bottom ring is dark and its
 * top face is light, which is what a solid object sitting on a surface looks
 * like, and it costs one float per vertex — twenty-four of them per city.
 *
 * **Why a box and not a rounded box.** `RoundedBoxGeometry` is the obvious
 * reach for "handmade" and it is the wrong one at this scale: it multiplies the
 * vertex count by a factor of thirty for a fillet that is sub-pixel at 900 m of
 * camera height, and §E7.1's read comes from the *edge darkening* in the
 * material, not from real geometry. The cut-card term in `clay-tsl.ts` draws a
 * card's thickness in world metres and holds it as the camera pulls in, which a
 * fixed fillet radius would not.
 *
 * **The pin is a cylinder with a flat cap and no bottom.** Nothing ever sees a
 * pin from underneath — the camera is pitched 52° down and the pin is pressed
 * into the ground — so the bottom cap is two triangles per pin, five thousand
 * pins, for a surface that does not exist. Removing it is not a
 * micro-optimisation; it is the difference between the pins costing 60 k and
 * 100 k triangles.
 */

import { BoxGeometry, BufferAttribute, CylinderGeometry, PlaneGeometry } from "three/webgpu";

import { CLAY, WORLD } from "@/design/generated/tokens";
import type { CityKit } from "./city-kit";

/** The vertical AO ramp, as a fraction of a shape's own height. Below this the
 *  vertex colour is at `CLAY.ao.floor`; above it, unoccluded. */
const AO_RISE = 0.35;

/**
 * A unit box, 1 m on every side, centred at the origin — so an instance matrix
 * scales it straight into metres and the material's `clayHeight` attribute is
 * the same number as the scale on y.
 *
 * The vertex colour carries AO in its red channel only. Green and blue are
 * written too, because a three-component colour attribute is what three.js's
 * `vertexColor()` node reads, and leaving two channels undefined is how a
 * material starts depending on uninitialised memory.
 */
export function clayBoxGeometry(): BoxGeometry {
  const geometry = new BoxGeometry(1, 1, 1);
  bakeVerticalAo(geometry, -0.5, 1);
  return geometry;
}

/**
 * The pin: a cylinder of unit radius and unit height, centred at the origin.
 *
 * `WORLD.pin` gives the real radius and height, and both arrive through the
 * instance matrix, so this geometry is dimensionless on purpose — one shape,
 * five thousand sizes, no re-upload when a severity changes.
 */
export function pinGeometry(): CylinderGeometry {
  const geometry = new CylinderGeometry(
    1,
    // Very slightly tapered. A perfectly cylindrical pin reads as a bar chart;
    // a taper of a few percent reads as something pressed into clay by a
    // thumb, which is the whole conceit and costs nothing.
    1 - CLAY.edge.bevelMetres / WORLD.pin.radiusMetres / 8,
    1,
    PIN_SEGMENTS,
    1,
    // Open-ended: the caps are added below, top only.
    true,
  );
  bakeVerticalAo(geometry, -0.5, 1);
  return withTopCap(geometry);
}

/**
 * The ground the city sits on.
 *
 * One plane, sized to the frame's own extent, with no subdivision: it is lit by
 * a single directional light and shaded by a material with no displacement, so
 * every interior vertex would be a vertex that changes nothing. The AO here is
 * flat — the ground is what occludes, not what is occluded.
 */
export function groundGeometry(): PlaneGeometry {
  const side = WORLD.extent.halfMetres * 2;
  const geometry = new PlaneGeometry(side, side);
  geometry.rotateX(-Math.PI / 2);
  flatVertexColour(geometry, 1);
  return geometry;
}

/**
 * Per-instance data for the city, in the layout `clay-tsl.ts` reads.
 *
 * Returned as plain arrays rather than written into an `InstancedMesh` here,
 * because the scene owns three.js objects and this module owns numbers — which
 * is what lets `tests/clay-city.test.ts` assert the whole city without a GPU.
 */
export interface CityBuffers {
  readonly count: number;
  readonly matrix: Float32Array;
  readonly grain: Float32Array;
  readonly occlusion: Float32Array;
  readonly height: Float32Array;
  /** Every footprint is unglazed clay: severity is a property of a report, and
   *  a building is scenery (ADR-0047). Written as −1 across the board so the
   *  material's `step(-0.5, …)` test is false everywhere and the branch is not
   *  a special case in the shader. */
  readonly severity: Float32Array;
}

export function cityBuffers(kit: CityKit): CityBuffers {
  const count = kit.footprints.length;
  const matrix = new Float32Array(count * 16);
  const grain = new Float32Array(count);
  const occlusion = new Float32Array(count);
  const height = new Float32Array(count);
  const severity = new Float32Array(count).fill(-1);

  kit.footprints.forEach((footprint, i) => {
    const at = i * 16;
    const cos = Math.cos(footprint.rotation);
    const sin = Math.sin(footprint.rotation);

    // Column-major. Yaw about y, scale on the diagonal, translation last —
    // composed by hand for the same reason `pins.ts` does it: a matrix that is
    // only ever a yaw and a scale does not need a quaternion.
    matrix[at] = cos * footprint.width;
    matrix[at + 1] = 0;
    matrix[at + 2] = -sin * footprint.width;
    matrix[at + 3] = 0;

    matrix[at + 4] = 0;
    matrix[at + 5] = footprint.height;
    matrix[at + 6] = 0;
    matrix[at + 7] = 0;

    matrix[at + 8] = sin * footprint.depth;
    matrix[at + 9] = 0;
    matrix[at + 10] = cos * footprint.depth;
    matrix[at + 11] = 0;

    // North is −z, as everywhere else in this layer, and the box is centred on
    // its own origin so half its height puts it on the ground rather than
    // through it.
    matrix[at + 12] = footprint.east;
    matrix[at + 13] = footprint.height / 2;
    matrix[at + 14] = -footprint.north;
    matrix[at + 15] = 1;

    grain[i] = footprint.grain / CLAY.thumbprint.rotationSteps;
    occlusion[i] = footprint.occlusion;
    height[i] = footprint.height;
  });

  return { count, matrix, grain, occlusion, height, severity };
}

/** Enough segments to read as round at 900 m and few enough that five thousand
 *  of them stay inside the triangle budget. Twelve is the number where the
 *  silhouette stops being visibly faceted at this camera height. */
export const PIN_SEGMENTS = 12;

// --------------------------------------------------------------------------

/**
 * Write a vertical AO gradient into a geometry's vertex colours.
 *
 * `base` is the y at which the shape meets the ground and `span` its height in
 * the same units. Everything below `base + span * AO_RISE` is progressively
 * darkened toward `CLAY.ao.floor`, which is *not* black — §E7.1 again: clay in
 * a crease is darker clay.
 */
function bakeVerticalAo(
  geometry: BoxGeometry | CylinderGeometry,
  base: number,
  span: number,
): void {
  const position = geometry.getAttribute("position");
  const colours = new Float32Array(position.count * 3);
  const rise = Math.max(span * AO_RISE, Number.EPSILON);

  for (let i = 0; i < position.count; i += 1) {
    const height = (position.getY(i) - base) / rise;
    const t = Math.min(1, Math.max(0, height));
    // Squared, so the darkening hugs the ground rather than washing halfway up
    // the wall. A linear ramp reads as a gradient somebody applied; this reads
    // as contact.
    const ao = CLAY.ao.floor + (1 - CLAY.ao.floor) * (t * t);
    colours[i * 3] = ao;
    colours[i * 3 + 1] = ao;
    colours[i * 3 + 2] = ao;
  }

  geometry.setAttribute("color", new BufferAttribute(colours, 3));
}

function flatVertexColour(geometry: PlaneGeometry, value: number): void {
  const position = geometry.getAttribute("position");
  const colours = new Float32Array(position.count * 3).fill(value);
  geometry.setAttribute("color", new BufferAttribute(colours, 3));
}

/**
 * Add the top cap to an open-ended cylinder.
 *
 * `CylinderGeometry` offers caps only as a pair, and the bottom one is the
 * expensive half of a pin nobody can see. Building the top fan by hand is a
 * dozen lines and halves the pin's triangle count.
 */
function withTopCap(open: CylinderGeometry): CylinderGeometry {
  const position = open.getAttribute("position");
  const normal = open.getAttribute("normal");
  const uv = open.getAttribute("uv");
  const colour = open.getAttribute("color");
  const index = open.getIndex();
  if (index === null) return open;

  const base = position.count;
  const positions = Array.from(position.array as Float32Array);
  const normals = Array.from(normal.array as Float32Array);
  const uvs = Array.from(uv.array as Float32Array);
  const colours = Array.from(colour.array as Float32Array);
  const indices = Array.from(index.array as ArrayLike<number>);

  // Centre, then the rim, then a fan. The cap is fully lit: it is the face the
  // sun and the camera both see, and it is where a pin's glaze reads from.
  positions.push(0, 0.5, 0);
  normals.push(0, 1, 0);
  uvs.push(0.5, 0.5);
  colours.push(1, 1, 1);

  for (let i = 0; i <= PIN_SEGMENTS; i += 1) {
    const theta = (i / PIN_SEGMENTS) * Math.PI * 2;
    const x = Math.sin(theta);
    const z = Math.cos(theta);
    positions.push(x, 0.5, z);
    normals.push(0, 1, 0);
    uvs.push(x * 0.5 + 0.5, z * 0.5 + 0.5);
    colours.push(1, 1, 1);
  }

  for (let i = 0; i < PIN_SEGMENTS; i += 1) {
    indices.push(base, base + 1 + i + 1, base + 1 + i);
  }

  open.setAttribute("position", new BufferAttribute(new Float32Array(positions), 3));
  open.setAttribute("normal", new BufferAttribute(new Float32Array(normals), 3));
  open.setAttribute("uv", new BufferAttribute(new Float32Array(uvs), 2));
  open.setAttribute("color", new BufferAttribute(new Float32Array(colours), 3));
  open.setIndex(indices);
  return open;
}
