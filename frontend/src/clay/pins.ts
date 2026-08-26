/**
 * The pins — M8.4, M8.5, §E27, §E11.1.
 *
 * > `InstancedMesh` pins — **one draw call for the city** — with per-instance
 * > severity, state and animation phase.
 *
 * One draw call is a budget line (§E23) and it is also the reason this file is
 * arithmetic rather than a scene graph: five thousand `Mesh` objects would be
 * five thousand draw calls, five thousand matrix updates and a frame time
 * measured in whole seconds. Everything below writes into four flat typed
 * arrays that are uploaded once per stepped frame.
 *
 * **What a pin means, and what it must not.** §E27 makes the mapping explicit:
 * `complaint_submitted` pushes a pin into the clay with the Settle motion,
 * `severity_scored` sets its glaze *and its height*, `cluster_match_found` is
 * the merge. A pin is therefore never decoration — which cuts the other way
 * too: an entity with no score gets `severityIndex: -1` and the shader leaves
 * it as bare clay. Rendering it in the lowest glaze would be the frontend
 * asserting a severity the rubric has not produced.
 *
 * **Height is severity, and the mapping is stated.** §E27 lists *"glaze colour;
 * pin height"* against `severity_scored`, so both channels carry the same fact
 * — which is §E9.4 rule 2 ("colour is never the only channel") applied in three
 * dimensions, and it is what makes the map readable in the grayscale of a
 * photocopied printout the same way the badge is.
 *
 * **The Settle motion runs on the 12 fps stepped clock** and is the `stamp`
 * easing token — the same curve the paper stamp uses. §E11.1 allows five
 * signature motions and only five; the pin arriving and the stamp landing are
 * one of them wearing two materials, and `lib/easing.ts` is what makes that a
 * fact rather than a resemblance.
 */

import { WORLD } from "@/design/generated/tokens";
import { easing } from "@/lib/easing";
import { levelFor, severityFraction } from "@/lib/severity";
import { levelOf, type ClayEntity } from "./entities";
import { rampIndexOf } from "./clay-tsl";
import type { Projection } from "./projection";
import { thumbprintRotation } from "./thumbprint";

const settle = easing("stamp");

export interface PinInstance {
  readonly id: string;
  /** Local ENU metres. */
  readonly east: number;
  readonly north: number;
  /** Metres, already scaled by the Settle progress. */
  readonly height: number;
  /** Metres below zero — how far into the clay this pin has been pushed. The
   *  Settle overshoots and comes back, so this is briefly negative-going. */
  readonly depth: number;
  /** Index into the severity ramp, or −1 for unglazed clay. */
  readonly severityIndex: number;
  readonly grain: number;
  /** 0…1, and >1 during the overshoot. Written to the instance buffer so the
   *  material can use it later without a second CPU pass. */
  readonly phase: number;
}

/** The full height a pin reaches once settled, from its score. */
export function pinHeight(score: number | null): number {
  const { minHeightMetres, maxHeightMetres } = WORLD.pin;
  if (score === null) return minHeightMetres;
  return minHeightMetres + severityFraction(score) * (maxHeightMetres - minHeightMetres);
}

/**
 * How far through its Settle a pin is at `step`.
 *
 * `arrivedAtStep === null` means the pin was already on the map when the scene
 * loaded, and it is fully settled from frame zero. Re-playing the arrival of
 * every existing pin on every navigation would turn a page load into a
 * fireworks display and would tell the officer that five thousand reports had
 * just come in.
 */
export function settleProgress(arrivedAtStep: number | null, step: number): number {
  if (arrivedAtStep === null) return 1;
  const elapsed = step - arrivedAtStep;
  if (elapsed <= 0) return 0;
  if (elapsed >= WORLD.pin.settleSteps) return 1;
  return settle(elapsed / WORLD.pin.settleSteps);
}

/**
 * Every entity, as an instance, at one step of the clock.
 *
 * Pure: `(entities, projection, step) → instances`. A golden image at a fixed
 * step is therefore reproducible without running a clock, which is the same
 * property `press-model.ts`'s `jitterAt` has and for the same reason (§E24).
 *
 * Entities outside the frame are **kept**, not culled. Culling here would
 * desynchronise the canvas from the peer list — the failure `entities.ts` is
 * written to prevent — and the projection's own `contains()` is the honest
 * place to decide that a tenant needs a different origin.
 */
export function pinInstances(
  entities: readonly ClayEntity[],
  projection: Projection,
  step: number,
): readonly PinInstance[] {
  return entities.map((entity) => {
    const local = projection.toLocal(entity.point);
    const phase = settleProgress(entity.arrivedAtStep, step);
    const level = levelOf(entity);
    const full = pinHeight(entity.severityScore);

    return {
      id: entity.id,
      east: local.east,
      north: local.north,
      // The overshoot is spent going *down*: a pin pressed into clay goes past
      // its resting depth and the material pushes it back. Spending it on
      // height instead would make the pin grow taller than its severity, which
      // would be an animation lying about a measurement.
      height: full * Math.min(1, phase),
      depth: Math.max(0, phase - 1) * WORLD.pin.radiusMetres,
      severityIndex: level === null ? -1 : rampIndexOf(level),
      grain: thumbprintRotation(entity.id),
      phase,
    };
  });
}

/**
 * The four flat arrays an `InstancedMesh` needs, allocated once.
 *
 * `capacity` rather than `entities.length`: `InstancedMesh` cannot grow, and
 * reallocating on every arrival would drop and re-upload every buffer on the
 * frame a pin appears — a visible hitch at exactly the moment the product is
 * trying to show something arriving. The buffers are sized to the budget
 * (`BUDGET.pins`) and `count` is what moves.
 */
export interface PinBuffers {
  readonly capacity: number;
  readonly matrix: Float32Array;
  readonly severity: Float32Array;
  readonly grain: Float32Array;
  readonly occlusion: Float32Array;
  readonly height: Float32Array;
  count: number;
}

export function allocatePins(capacity: number): PinBuffers {
  return {
    capacity,
    matrix: new Float32Array(capacity * 16),
    severity: new Float32Array(capacity),
    grain: new Float32Array(capacity),
    occlusion: new Float32Array(capacity),
    height: new Float32Array(capacity),
    count: 0,
  };
}

/**
 * Write instances into the buffers.
 *
 * The matrix is composed by hand rather than through `Object3D.updateMatrix()`,
 * because a pin is a cylinder that is only ever translated and scaled on one
 * axis: writing twelve of sixteen floats directly avoids five thousand
 * quaternion multiplications per stepped frame for a rotation that is always
 * identity.
 *
 * Returns the number written, which is `min(instances.length, capacity)` — and
 * the overflow is **silent by design here and loud one level up**: this
 * function's job is to fill a buffer, and `ClayPins` is where a city with more
 * pins than budget says so rather than quietly drawing four thousand of five.
 */
export function writePins(buffers: PinBuffers, instances: readonly PinInstance[]): number {
  const count = Math.min(instances.length, buffers.capacity);
  const radius = WORLD.pin.radiusMetres;

  for (let i = 0; i < count; i += 1) {
    const pin = instances[i];
    if (pin === undefined) break;
    const at = i * 16;

    // Column-major, as three.js and both graphics APIs want it. Scale on the
    // diagonal, translation in the last column.
    buffers.matrix[at] = radius;
    buffers.matrix[at + 1] = 0;
    buffers.matrix[at + 2] = 0;
    buffers.matrix[at + 3] = 0;

    buffers.matrix[at + 4] = 0;
    buffers.matrix[at + 5] = Math.max(pin.height, 1e-3);
    buffers.matrix[at + 6] = 0;
    buffers.matrix[at + 7] = 0;

    buffers.matrix[at + 8] = 0;
    buffers.matrix[at + 9] = 0;
    buffers.matrix[at + 10] = radius;
    buffers.matrix[at + 11] = 0;

    // Three.js is y-up and the ENU frame is north-up, so north maps to −z:
    // looking down the −z axis from above puts north at the top of the screen,
    // which is what everybody means by a map.
    buffers.matrix[at + 12] = pin.east;
    buffers.matrix[at + 13] = pin.height / 2 - pin.depth;
    buffers.matrix[at + 14] = -pin.north;
    buffers.matrix[at + 15] = 1;

    buffers.severity[i] = pin.severityIndex;
    buffers.grain[i] = pin.grain;
    buffers.height[i] = Math.max(pin.height, 1e-3);
    // A pin sits *in* the clay, so its own base is occluded by the ground it
    // displaced. Constant rather than computed: the alternative is a
    // neighbourhood search per pin per frame for a shading term nobody can
    // point at.
    buffers.occlusion[i] = 0.25;
  }

  buffers.count = count;
  return count;
}

/**
 * The severity band a live `severity_scored` payload lands in.
 *
 * The event carries `score` on a 0–10 scale (`events/catalog.py`) while a
 * complaint read carries 0–100. The conversion is in `lib/severity.ts` and this
 * re-export exists so the clay layer never does the multiplication itself —
 * a factor of ten applied in two places is a factor of ten applied wrongly in
 * one of them.
 */
export { levelForEventScore } from "@/lib/severity";
export { levelFor };
