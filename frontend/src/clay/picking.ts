/**
 * Which pin is under the pointer — M8.4, F10.
 *
 * Two paths, and the division between them is not an optimisation story.
 *
 * **GPU picking is the pointer path.** One extra render of the pins alone into
 * a **1×1** target, with the camera's view offset narrowed to the pixel under
 * the cursor, into a material whose only job is to write the instance index as
 * a colour. It costs one pixel, it is exact at silhouette edges — where a
 * cylinder's real outline and its bounding volume disagree most — and it never
 * touches the five thousand instance matrices on the CPU.
 *
 * **`three-mesh-bvh` is the ray path.** A raycast is what the *keyboard* needs:
 * §E22's arrow-key pin traversal moves a selection through the world without a
 * pointer, and it needs to answer "what is nearest along this direction", which
 * a picking buffer cannot. It is also the only path available where there is no
 * renderer to read back from — the jsdom unit tests are the honest example, and
 * a picking implementation that only works in a browser is a picking
 * implementation with no test.
 *
 * **Why not raycast for both.** `Raycaster` against an `InstancedMesh` with
 * five thousand members walks every instance and inverts every matrix, per
 * move, on the main thread. The BVH removes the per-triangle half of that and
 * not the per-instance half. At pointer rates, on a machine that is also
 * running Ollama (ADR-0002), that is a frame budget spent on a hit test.
 */

import { BufferGeometry, Mesh, MeshBasicNodeMaterial, RenderTarget, Vector2 } from "three/webgpu";
import type { InstancedMesh, PerspectiveCamera, Raycaster, WebGPURenderer } from "three/webgpu";
import { float, instanceIndex, vec4 } from "three/tsl";
import { acceleratedRaycast, computeBoundsTree, disposeBoundsTree } from "three-mesh-bvh";

/**
 * Install the BVH raycast, once.
 *
 * `three-mesh-bvh` patches three.js prototypes, which is the API it publishes
 * and the API every consumer of it uses. Doing it in a named function called
 * from the scene's setup — rather than as a side effect of importing this
 * module — keeps that patch somewhere a reader can find it, because a
 * prototype mutation that happens on import is the kind of thing that is
 * invisible until it conflicts with something.
 */
export function installBvh(): void {
  const geometry = BufferGeometry.prototype as unknown as {
    computeBoundsTree?: unknown;
    disposeBoundsTree?: unknown;
  };
  if (geometry.computeBoundsTree !== undefined) return;
  geometry.computeBoundsTree = computeBoundsTree;
  geometry.disposeBoundsTree = disposeBoundsTree;
  (Mesh.prototype as unknown as { raycast: unknown }).raycast = acceleratedRaycast;
}

/**
 * The picking material.
 *
 * `instanceIndex + 1` so that zero means *nothing* — the cleared background of
 * the picking target is black, and a scheme where instance 0 is also black
 * would make the first pin in the city unselectable in a way that looked like
 * an off-by-one somewhere else entirely.
 *
 * Twenty-four bits across three channels covers 16.7 M instances against a
 * budget of five thousand, so the encoding will not need revisiting; it is
 * written as three channels rather than two because a two-channel scheme saves
 * nothing and costs a reader ten seconds working out why.
 */
export function pickingMaterial(): MeshBasicNodeMaterial {
  const material = new MeshBasicNodeMaterial();
  const id = float(instanceIndex).add(1);

  const high = id.div(65_536).floor();
  const mid = id.sub(high.mul(65_536)).div(256).floor();
  const low = id.sub(high.mul(65_536)).sub(mid.mul(256));

  material.colorNode = vec4(high.div(255), mid.div(255), low.div(255), 1);
  // The picking pass is data, not a picture: nothing may tone-map it, fog it,
  // or light it, and every one of those is on by default somewhere.
  material.fog = false;
  material.toneMapped = false;
  material.transparent = false;
  return material;
}

/** Decode one RGBA byte quadruple written by `pickingMaterial()`. */
export function decodePick(pixel: Uint8Array | Uint8ClampedArray): number | null {
  const high = pixel[0] ?? 0;
  const mid = pixel[1] ?? 0;
  const low = pixel[2] ?? 0;
  const id = high * 65_536 + mid * 256 + low;
  return id === 0 ? null : id - 1;
}

/**
 * A 1×1 picking buffer, and the read that uses it.
 *
 * Held open for the lifetime of the scene rather than created per pick: a
 * render target is an allocation and a pointer move is sixty of them a second.
 */
export class Picker {
  readonly #target = new RenderTarget(1, 1);
  readonly #material = pickingMaterial();
  readonly #pixel = new Uint8Array(4);
  readonly #size = new Vector2();

  get material(): MeshBasicNodeMaterial {
    return this.#material;
  }

  /**
   * Which instance is at this canvas pixel, or `null` for empty sky.
   *
   * The camera's view offset is what makes this a 1×1 render rather than a
   * full-frame one: it tells the projection matrix to render *only* the tile
   * the cursor is over, at one pixel, which is the whole trick and the reason
   * this costs nothing.
   *
   * The mesh's own material is swapped and restored around the render. Cheaper
   * and less surprising than a second scene: a second scene would need every
   * instance buffer kept in step, which is the synchronisation bug this
   * codebase spends `entities.ts` avoiding elsewhere.
   */
  async pick(
    renderer: WebGPURenderer,
    camera: PerspectiveCamera,
    mesh: InstancedMesh,
    x: number,
    y: number,
  ): Promise<number | null> {
    renderer.getDrawingBufferSize(this.#size);
    if (this.#size.x <= 0 || this.#size.y <= 0) return null;

    const previousMaterial = mesh.material;
    const previousTarget = renderer.getRenderTarget();

    // `setViewOffset` counts y from the top, which is also how a pointer event
    // counts it, so no flip belongs here — and a flip added "to be safe" is how
    // picking ends up mirrored about the horizontal axis.
    camera.setViewOffset(this.#size.x, this.#size.y, x, y, 1, 1);
    mesh.material = this.#material;
    renderer.setRenderTarget(this.#target);

    try {
      renderer.render(mesh, camera);
      const pixel = await renderer.readRenderTargetPixelsAsync(this.#target, 0, 0, 1, 1);
      this.#pixel.set(pixel.slice(0, 4));
      return decodePick(this.#pixel);
    } finally {
      renderer.setRenderTarget(previousTarget);
      mesh.material = previousMaterial;
      camera.clearViewOffset();
    }
  }

  dispose(): void {
    this.#target.dispose();
    this.#material.dispose();
  }
}

/**
 * The nearest instance along a ray, using the BVH.
 *
 * Returns the `instanceId` three.js reports, which is the same index the GPU
 * path encodes — so a keyboard selection and a pointer selection are the same
 * number and the peer list cannot disagree with the canvas about which pin is
 * current.
 */
export function raycastPins(raycaster: Raycaster, mesh: InstancedMesh): number | null {
  const hits = raycaster.intersectObject(mesh, false);
  for (const hit of hits) {
    if (hit.instanceId !== undefined) return hit.instanceId;
  }
  return null;
}
