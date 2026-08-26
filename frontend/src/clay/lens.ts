/**
 * The lens stack — M8.7, §E7.3, F11.
 *
 * §E7.3 lists four effects and one reservation, and the reservation is the part
 * with a test attached:
 *
 *   1 · tilt-shift depth of field, the focal plane following the selected entity
 *   2 · gate weave — ±0.4 px, resampled at 12 Hz
 *   3 · vignette and a slight barrel distortion
 *   4 · **selective bloom, reserved exclusively for `safety_trigger_fired`**
 *
 * **The lens is not the press.** They are two passes in a fixed order and the
 * order is the whole metaphor: the lens is what we photograph the model with,
 * the press is what we print the photograph on. Tilt-shift belongs to the
 * camera; halftone belongs to the paper. Merging them would produce a screened
 * blur, which is a thing no physical process produces and which looks, exactly,
 * like a filter.
 *
 * **Why the tilt-shift is first among equals.** It is what makes a city read as
 * a *miniature*. Human depth perception uses defocus as a size cue, and a
 * shallow depth of field over a wide scene is only physically possible when the
 * scene is small — which is why the effect works on photographs of real cities
 * and why it is the one effect in this stack that is doing semantic work rather
 * than atmosphere. `WORLD.camera`'s long lens exists to make it believable.
 *
 * **Selective bloom is structurally selective, not conditionally selective.**
 * The obvious implementation — bloom the whole frame while a fail-safe is
 * firing — is a *temporal* reservation, and it holds only as long as nobody
 * ever raises the brightness of anything else. This module instead takes a
 * second render pass restricted to `SAFETY_LAYER` and blooms **that**. Nothing
 * outside that layer can contribute a photon to the bloom, so §E3.4's audit —
 * *"no second use of bloom exists in `src/`"* — is enforced by which layer an
 * object is on, and `tests/clay-lens.test.ts` asserts the layer has exactly one
 * member.
 */

import { Vector2 } from "three/webgpu";
import type { Node, TextureNode } from "three/webgpu";
import { float, mix, smoothstep, uniform, vec2, vec3, vec4 } from "three/tsl";
import { bloom } from "three/addons/tsl/display/BloomNode.js";

import { LENS } from "@/design/generated/tokens";
import { mulberry32 } from "@/lib/stepped-clock";

type F = Node<"float">;
type V2 = Node<"vec2">;
type V4 = Node<"vec4">;

/**
 * The layer the fail-safe marker lives on, and the only thing bloom can see.
 *
 * Layer 1 rather than 0, because layer 0 is where three.js puts everything by
 * default and a reservation that shares a channel with the default is not a
 * reservation.
 */
export const SAFETY_LAYER = 1;

/** How many taps the circle of confusion is sampled with. Seven — a centre and
 *  a hexagonal ring — because a hexagon is what a real iris with six blades
 *  produces, and because seven taps is where the cost stops being free and the
 *  quality stops improving at this blur radius. */
export const COC_TAPS = 7;

/** The frame height the bloom radius is authored against, so the uniform has a
 *  sane value for the one frame between construction and the first resize. */
const REFERENCE_FRAME_HEIGHT = 1080;

export interface LensSource {
  /** The scene, already rendered. */
  readonly colour: TextureNode;
  /** View-space z, negative in front of the camera — `pass.getViewZNode()`. */
  readonly viewZ: F;
  /** The frame's pixel dimensions, so px-denominated tokens mean pixels. */
  readonly resolution: Node<"vec2">;
  /**
   * The fail-safe marker, rendered alone on `SAFETY_LAYER`.
   *
   * Supplied as a node rather than constructed here so the scene owns its
   * passes, and `null` where the tier has no bloom at all — in which case no
   * bloom node is built, which is materially cheaper than a bloom node with its
   * strength set to zero.
   */
  readonly flare: V4 | null;
}

export interface LensHandles {
  /**
   * The photographed frame at a given uv — the press's `sample`.
   *
   * A **function** rather than a finished node, and that shape is the whole
   * composition. §E6.1 stage 3 misregisters each plate by sampling the sheet
   * *where it was when that plate printed*, which means the press has to ask
   * for the photograph at an offset — a question a finished node cannot answer.
   *
   * The obvious alternative is to resolve the lens into a render target and let
   * the press sample the texture. That was the first implementation and it
   * printed a blank sheet: a `pass()` nested inside an `rtt()` inside the
   * output chain never renders, so the press faithfully laid no ink at all onto
   * a perfectly good piece of paper. Inlining costs three sets of taps instead
   * of one and saves a full-resolution render target, which is the better trade
   * on a budget (§E23) that is measured in VRAM.
   */
  readonly sampleAt: (uvNode: V2) => V4;
  /**
   * Add the fail-safe's glow to the **printed** frame — §E7.3's reservation.
   *
   * After the press rather than before it, and that is a correction rather than
   * a convenience: **you cannot print a glow.** A bloom is light, and the light
   * on this surface is the light table's own — §E9.3's prints are backlit on
   * glass. So the flare is light behind the sheet, which is where a struck
   * alarm belongs and what a reader actually sees.
   */
  readonly overlay: (printed: V4) => V4;
  /** Re-jitter the gate at 12 Hz, from the stepped clock (§E7.2). */
  readonly setStep: (step: number) => void;
  /** Metres from the camera to the entity the focal plane should sit on. */
  readonly setFocusMetres: (metres: number) => void;
  /**
   * How deep the plane of focus is, in metres — §E16 Act 6's snap to miniature.
   *
   * A uniform rather than the token constant it started as, and the reason is
   * the shot §E16 calls THE SHOT: *"tilt-shift snaps it to miniature"* is this
   * number falling, and nothing else. Everything outside the film leaves it at
   * `LENS.tiltShift.apertureMetres`, which is where it is initialised, so the
   * console and the public map are unaffected by its existence.
   */
  readonly setApertureMetres: (metres: number) => void;
  /** Fire the bloom, or stop it. Called only from `bloomActive()` in
   *  `live.ts`, which is only ever true because of `safety_trigger_fired`. */
  readonly setBloomFiring: (firing: boolean) => void;
  /** §E6.4's dial reaching the lens: the two effects the quality manager is
   *  allowed to switch off, at ladder position 2. Bloom is not among them. */
  readonly setEffects: (effects: { depthOfField: boolean; gateWeave: boolean }) => void;
  /**
   * The frame's height in device pixels.
   *
   * The bloom's radius is authored in pixels (`lens.bloom.radiusPx`) and the
   * node wants it as a fraction of the frame, so the conversion needs a number
   * that only exists once the canvas has a size. Kept as a uniform rather than
   * rebuilt on resize: a bloom node reconstructed on every drag of a window
   * edge recompiles a shader on every drag of a window edge.
   */
  readonly setFrameHeight: (pixels: number) => void;
}

export interface LensOptions {
  /** Fixed seed ⇒ reproducible weave ⇒ golden images (§E24). */
  readonly seed?: number;
}

/**
 * Build the lens.
 *
 * Nothing here writes to a render target or knows what a renderer is — which is
 * what lets the same stack composite the live console, an offscreen still for a
 * Tier C print, and a golden image, in exactly the arrangement `press-tsl.ts`
 * already established.
 */
export function createLensStack(source: LensSource, options: LensOptions = {}): LensHandles {
  const weave = uniform(new Vector2(0, 0));
  const focusMetres = uniform(1);
  const bloomStrength = uniform(0);
  const dofAmount = uniform(1);
  const aperture = uniform(LENS.tiltShift.apertureMetres);
  const weaveAmount = uniform(1);
  const bloomRadius = uniform(LENS.bloom.radiusPx / REFERENCE_FRAME_HEIGHT);
  const random = mulberry32(options.seed ?? 1);

  const texel = vec2(1, 1).div(source.resolution) as V2;

  // 1 · tilt-shift, as a *circle of confusion* computed once per fragment.
  //
  // `viewZ` is negative in front of the camera, so the distance is its
  // magnitude; `focusMetres` is the camera's distance to the entity it is
  // looking at, which is what makes the focal plane **follow** rather than sit
  // at a fixed depth. It is deliberately not re-read per plate: the plate
  // offsets are sub-pixel to about 1.5 px (§E6.1 stage 3), and a depth fetched
  // 1.5 px away is the same depth — three more depth fetches would buy a
  // difference nothing can see.
  const coc = source.viewZ
    .abs()
    .sub(focusMetres.add(float(LENS.tiltShift.focusMetres)))
    .abs()
    .div(aperture)
    .clamp(0, 1)
    .mul(float(LENS.tiltShift.maxBlurPx))
    .mul(dofAmount);

  /**
   * The photograph at one uv.
   *
   * The order inside is §E7.3's, and it is the order the physical stack has:
   * the glass distorts, the gate moves, the aperture defocuses, and the barrel
   * loses light toward the edges.
   */
  const photograph = (uvNode: V2): V4 => {
    // 3a · barrel first, because it is a property of the glass and everything
    // else is measured through it. A vignette applied before distortion would
    // come out an ellipse in the corners.
    const centred = uvNode.sub(vec2(0.5, 0.5));
    const radiusSquared = centred.dot(centred);
    const barrelled = centred
      .mul(float(1).add(radiusSquared.mul(float(LENS.barrel.amount))))
      .add(vec2(0.5, 0.5));

    // 2 · gate weave. The whole frame shifts by a fraction of a pixel and
    // re-lands on twos — the film in a projector gate never sits perfectly
    // still, and this is the cheapest signal in the product that what you are
    // looking at was photographed rather than composited.
    const woven = barrelled.add(weave.div(source.resolution).mul(weaveAmount)) as V2;

    let sum: Node<"vec3"> = source.colour.sample(woven).rgb;
    for (let i = 1; i < COC_TAPS; i += 1) {
      // A hexagonal ring, rotated by a sixth of a turn so the first tap is not
      // axis-aligned — an axis-aligned ring puts its artefacts on horizontals,
      // and this scene is nothing but horizontals.
      const theta = ((i - 1) / (COC_TAPS - 1)) * Math.PI * 2 + Math.PI / 6;
      const offset = vec2(Math.cos(theta), Math.sin(theta));
      sum = sum.add(source.colour.sample(woven.add(offset.mul(texel).mul(coc))).rgb);
    }
    const blurred = sum.div(float(COC_TAPS));

    // 3b · vignette. Multiplied into the frame rather than mixed toward black,
    // because a lens *loses light* at the edge — it does not add darkness.
    const falloff = smoothstep(
      float(LENS.vignette.radius),
      float(LENS.vignette.radius * 0.35),
      radiusSquared.sqrt().mul(2),
    );
    return vec4(blurred.mul(mix(float(1 - LENS.vignette.amount), float(1), falloff)), 1);
  };

  return {
    sampleAt: photograph,

    // 4 · the reservation. `flare` is a pass restricted to `SAFETY_LAYER`, so
    // nothing outside that layer can contribute a photon; where the tier has no
    // bloom the node is never built at all, which is materially cheaper than a
    // bloom node with its strength set to zero.
    overlay: (printed) =>
      source.flare === null
        ? printed
        : vec4(
            printed.rgb.add(
              bloom(source.flare, bloomStrength, bloomRadius, LENS.bloom.threshold).rgb,
            ),
            1,
          ),

    setStep: () => {
      // One draw from a seeded generator per step. Deterministic, so a golden
      // image at a fixed step has a fixed weave — the same property
      // `press-model.ts`'s `jitterAt` has, and for the same reason.
      const amplitude = LENS.gateWeave.amplitudePx;
      weave.value.set((random() * 2 - 1) * amplitude, (random() * 2 - 1) * amplitude);
    },
    setFocusMetres: (metres) => {
      focusMetres.value = Math.max(1, metres);
    },
    setApertureMetres: (metres) => {
      // Floored above zero: the aperture is a divisor, and a film that
      // keyframed it to zero would hand every fragment an infinite circle of
      // confusion — a white frame, on the one shot the whole direction rests on.
      aperture.value = Math.max(0.5, metres);
    },
    setBloomFiring: (firing) => {
      bloomStrength.value = firing ? LENS.bloom.strength : 0;
    },
    setFrameHeight: (pixels) => {
      bloomRadius.value = LENS.bloom.radiusPx / Math.max(1, pixels);
    },
    setEffects: ({ depthOfField, gateWeave }) => {
      dofAmount.value = depthOfField ? 1 : 0;
      weaveAmount.value = gateWeave ? 1 : 0;
      if (!gateWeave) weave.value.set(0, 0);
    },
  };
}

/**
 * The ink the fail-safe marker is drawn in.
 *
 * Not a colour literal and not a severity glaze: `safety_trigger_fired` is the
 * deterministic fail-safe, it is not a severity band, and §E9.4 rule 1 forbids
 * severity ink on a non-severity element. It is the fluorescent pink ADR-0039
 * already assigned to *"something the system is not sure about and has routed
 * to a person"*, at the intensity a bloom threshold needs to see it.
 */
export function flareIntensity(): number {
  // Above `LENS.bloom.threshold` by construction. Written as a function of the
  // token rather than as a number so raising the threshold cannot silently
  // stop the one effect the threshold exists to gate.
  return LENS.bloom.threshold * 2.4;
}

/** A vec3 helper the scene uses to tint the marker. Exported so the scene does
 *  not import `three/tsl` for one call and so the flare's construction stays
 *  next to the reservation it is part of. */
export function flareColour(linear: readonly [number, number, number]): Node<"vec3"> {
  const gain = flareIntensity();
  return vec3(linear[0] * gain, linear[1] * gain, linear[2] * gain);
}
