/**
 * The camera — §E7.2, M8.5.
 *
 * > Characters, props, flags, weather, pins: 12 fps stepped. **Camera:
 * > uncapped, damped, filmic.**
 *
 * The contrast is the effect. A smooth camera moving through a world that
 * animates on twos is how stop-motion is actually shot, and it is what makes
 * the result read as handmade rather than as a low frame rate. So this module
 * is the one part of the clay layer that is deliberately **not** stepped, and
 * `dampTowards()` is frame-rate independent so that property survives a machine
 * running at 144 Hz and the same machine running at 30 after the quality
 * manager has taken two rungs off.
 *
 * **A long lens, low over a table.** `WORLD.camera` is 900 m of height at a 52°
 * pitch through a 28° field of view, and all three numbers are one decision:
 * a wide lens close in would give a city with converging verticals and a
 * strong perspective, which reads as *architecture*. A narrow lens far back
 * flattens the verticals and compresses depth, which reads as *a model of a
 * city on a table* — and it is also what makes the tilt-shift in F11 believable
 * rather than a blur applied to the top and bottom of the screen.
 */

import { Vector3 } from "three/webgpu";
import type { PerspectiveCamera } from "three/webgpu";

import { WORLD } from "@/design/generated/tokens";
import type { LocalPoint } from "./projection";

/**
 * Exponential damping toward a target, independent of frame rate.
 *
 * The naive form — `current += (target - current) * 0.1` per frame — is the one
 * everybody writes and it makes the camera's speed a function of the display's
 * refresh rate: the same drag feels twice as fast on a 120 Hz laptop as on a
 * 60 Hz monitor. `1 - e^(-λt)` is the same curve sampled correctly, so
 * `WORLD.camera.dampingPerSecond` means a real, measurable thing.
 */
export function dampTowards(current: number, target: number, lambda: number, dt: number): number {
  if (dt <= 0) return current;
  return target + (current - target) * Math.exp(-lambda * dt);
}

/** The camera's resting offset from whatever it is looking at, in ENU metres
 *  converted to three.js axes (north is −z). */
export function cameraOffset(): Vector3 {
  const pitch = (WORLD.camera.pitchDegrees * Math.PI) / 180;
  const height = WORLD.camera.heightMetres;
  // Pitch is measured down from horizontal, so the ground distance is
  // `height / tan(pitch)` and the camera sits due south of its subject —
  // south, because a viewer in the northern hemisphere reads a scene lit from
  // the south as lit from the front, and every tenant this product has is
  // north of the tropic.
  const ground = height / Math.tan(pitch);
  return new Vector3(0, height, ground);
}

/**
 * A camera that follows a point on the ground, damped.
 *
 * Deliberately not a controls object. `OrbitControls` and friends own the DOM
 * events, the inertia and the constraints, and every one of those is a decision
 * §E7.2 has already made differently — the camera in this product is *filmic*,
 * which means it is led rather than flown.
 */
/**
 * An absolute camera placement, in ENU metres — the film's seam (§E16, M9.4).
 *
 * The rig's normal mode is *led*: it is given a ground point and works out
 * where to stand. The Walk needs the opposite — §E16 specifies the camera as
 * shots ("locked side-tracking", "ankle height", "pull back to dusk"), and a
 * shot is a position and a subject, not a subject alone. So a pose overrides
 * the rig entirely while it is set, and clearing it hands the camera back.
 *
 * **Undamped, and that is the point.** The film's weight comes from the damped
 * scroll proxy (`story/spine.ts`); damping the same signal again here would
 * make the camera lag the reader by a quarter of a second, which reads as
 * dropped frames rather than as inertia.
 */
export interface CameraPose {
  readonly eyeEast: number;
  readonly eyeNorth: number;
  readonly eyeUp: number;
  readonly targetEast: number;
  readonly targetNorth: number;
  readonly targetUp: number;
  /** Where the tilt-shift's plane of focus sits, in metres from the eye
   *  (§E7.3). The film's rack focus is this number moving. */
  readonly focusMetres: number;
}

export class CameraRig {
  readonly #target = new Vector3();
  readonly #eased = new Vector3();
  readonly #offset = cameraOffset();
  #settled = false;
  #pose: CameraPose | null = null;

  /**
   * Take the camera off its leash and place it, or hand it back with `null`.
   *
   * Only the film calls this. Every other surface leads the camera with
   * `lookAt`, which is why this is an override rather than the primary API: a
   * console that placed its own camera would be re-deciding §E7.2's long lens
   * on every screen.
   */
  setPose(pose: CameraPose | null): void {
    this.#pose = pose;
  }

  /** Where the camera should be looking, in ENU metres. */
  lookAt(point: LocalPoint): void {
    this.#target.set(point.east, 0, -point.north);
    if (!this.#settled) {
      // The first target is taken instantly. Easing in from the origin would
      // spend the first second of every page load flying across a city, which
      // is a title sequence and not a map.
      this.#eased.copy(this.#target);
      this.#settled = true;
    }
  }

  /** Advance the damping and write the camera. `dt` is in seconds. */
  update(camera: PerspectiveCamera, dt: number): void {
    const pose = this.#pose;
    if (pose !== null) {
      // North is −z (`projection.ts`), and the film authors in ENU so that the
      // renderer's axis convention stays in the one module that owns it.
      camera.position.set(pose.eyeEast, pose.eyeUp, -pose.eyeNorth);
      camera.lookAt(pose.targetEast, pose.targetUp, -pose.targetNorth);
      camera.updateMatrixWorld();
      // Keep the damped state under the pose rather than beside it. Without
      // this, releasing the film's grip mid-city would ease the camera back
      // from wherever the last `lookAt` had left it — a half-second flight
      // across the map at exactly the moment the reader has stopped watching a
      // film and started using a map.
      this.#eased.set(pose.targetEast, pose.targetUp, -pose.targetNorth);
      this.#target.copy(this.#eased);
      return;
    }
    const lambda = WORLD.camera.dampingPerSecond;
    this.#eased.set(
      dampTowards(this.#eased.x, this.#target.x, lambda, dt),
      dampTowards(this.#eased.y, this.#target.y, lambda, dt),
      dampTowards(this.#eased.z, this.#target.z, lambda, dt),
    );
    camera.position.copy(this.#eased).add(this.#offset);
    camera.lookAt(this.#eased);
    camera.updateMatrixWorld();
  }

  /** How far the camera is from what it is looking at — the tilt-shift's focal
   *  distance (§E7.3), so the focal plane follows the selected entity rather
   *  than sitting at a fixed depth. */
  focusDistance(): number {
    return this.#pose?.focusMetres ?? this.#offset.length();
  }

  /** Test seam and reset: drop the easing so the next `lookAt` snaps. */
  release(): void {
    this.#settled = false;
  }
}

/**
 * Set up a perspective camera for the clay world.
 *
 * The far plane is derived from the frame's own extent rather than picked, so a
 * tenant with a wider `WORLD.extent` does not silently clip its own city; the
 * near plane is metres rather than centimetres because nothing in this scene is
 * ever within a metre of the lens, and a near plane two orders of magnitude too
 * close is the classic cause of depth fighting on distant geometry — which here
 * would show up as the ground flickering through the buildings.
 */
export function configureCamera(camera: PerspectiveCamera, aspect: number): void {
  camera.fov = WORLD.camera.fovDegrees;
  camera.aspect = aspect > 0 ? aspect : 1;
  camera.near = 1;
  camera.far = WORLD.extent.halfMetres * 4;
  camera.updateProjectionMatrix();
}
