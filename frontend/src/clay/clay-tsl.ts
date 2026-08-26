/**
 * The clay, as one TSL node material — M8.3, §E7.1, ADR-0037.
 *
 * §E7.1 lists six properties. Every one of them is in the graph below, in the
 * order it is listed there, and every number arrives from `tokens.json`:
 *
 *   1 · matte albedo, roughness 0.92, zero metalness, no PBR streaming
 *   2 · thumbprint normal, rotated per instance by a hash of the entity id
 *   3 · baked ambient occlusion, in vertex colours
 *   4 · the sub-surface cheat — a rim term, warm on the light side, cool in shadow
 *   5 · cut-card edges on every extruded footprint
 *   6 · **severity as a fired glaze, never as flat emissive**
 *
 * The sixth is the one with a test attached, because it is the one that would
 * be easiest to get subtly wrong and impossible to notice: an emissive severity
 * ramp looks *better* in isolation, and it would put a glow on the map. §E7.3
 * reserves the only glow in this product for `safety_trigger_fired`, and §E3.4
 * audits that reservation as a usage grep. A glazed pin is lit clay with a
 * specular coat and a darker rim — it is bright because light is falling on it.
 *
 * **One material, two backends.** Authored once here and compiled by
 * `WebGPURenderer` to WGSL or GLSL depending on the backend it took. That is
 * ADR-0037's whole argument, and it is why Phase 19's *"the WebGL 2 backend
 * renders the same scene as WebGPU, verified by golden image"* is a claim about
 * a compiler rather than about two hand-maintained shaders.
 *
 * **The severity ramp is a texture generated from the tokens**, not a chain of
 * comparisons. Five glazes are a lookup, and a lookup is one sample instead of
 * five mixes at every fragment of every pin — but the reason it is a *texture*
 * rather than a uniform array is §E24: `severityRamp()` reads `GLAZE_LINEAR`,
 * which is generated from the same line of `tokens.json` as the badge's colour.
 * The badge and the shader are the same number, and here is where that is
 * either true or a story we tell.
 */

import {
  DataTexture,
  FloatType,
  LinearFilter,
  MeshStandardNodeMaterial,
  RGBAFormat,
  RepeatWrapping,
  Vector3,
} from "three/webgpu";
import type { Texture } from "three/webgpu";
import {
  attribute,
  clamp,
  dot,
  float,
  mix,
  normalMap,
  normalView,
  normalWorld,
  oneMinus,
  positionLocal,
  positionViewDirection,
  pow,
  rotateUV,
  saturate,
  smoothstep,
  step,
  texture,
  uniform,
  uv,
  vec2,
  vec3,
  vertexColor,
} from "three/tsl";
import type { Node } from "three/webgpu";

import {
  CLAY,
  GLAZE_LINEAR,
  SEVERITY_DESCENDING,
  WEATHER,
  type SeverityLevel,
} from "@/design/generated/tokens";
import { thumbprintTile } from "./thumbprint";

type F = Node<"float">;

/**
 * A per-instance float attribute, as an operable node.
 *
 * `attribute("clayGrain", "float")` infers its node type from the *value* of
 * the second argument, which TypeScript widens to `string` — so the returned
 * node carries none of TSL's operator surface and `.div()` on it is an error
 * even though the runtime node is exactly right. Naming the type argument is
 * the fix, and doing it once here keeps four call sites readable.
 */
function floatAttribute(name: string): F {
  return attribute<"float">(name, "float");
}
type V3 = Node<"vec3">;

/**
 * Per-instance attribute names.
 *
 * Named here rather than spelled at each call site, because the buffer that
 * writes them and the shader that reads them are in different files and a typo
 * between them is a silently black material rather than an error.
 */
export const CLAY_ATTRIBUTES = {
  /** Which of `clay.thumbprint.rotationSteps` rotations this instance uses. */
  grain: "clayGrain",
  /** Index into the severity ramp, or −1 for unglazed clay. */
  severity: "claySeverity",
  /** Extra ambient occlusion for this instance, 0–1. */
  occlusion: "clayOcclusion",
  /** Height in metres, so the cut-card bevel stays a fixed physical width. */
  height: "clayHeight",
} as const;

/** The order the ramp texture is built in — ascending, so index 0 is the
 *  lowest row of §E9.4 and the mapping is readable rather than reversed. */
export const RAMP_ORDER = [...SEVERITY_DESCENDING].reverse() as readonly SeverityLevel[];

export function rampIndexOf(level: SeverityLevel): number {
  return RAMP_ORDER.indexOf(level);
}

/**
 * §E9.4's glazes, as a 5×1 float texture.
 *
 * `FloatType` rather than bytes: these are linear-sRGB values that the shader
 * multiplies into lit colour, and quantising them to 8 bits would put a
 * different number in the shader from the one in the badge — which is the exact
 * failure §E24 is written to prevent, arriving through the back door of a
 * texture format.
 *
 * `LinearFilter` is deliberate and is a *design* choice: a severity between two
 * bands does not exist (§13.1's bands are governed data), so the ramp is only
 * ever sampled at texel centres. Linear filtering costs nothing at a texel
 * centre and means a future continuous severity field — the compute-driven one
 * §E13 gives Tier S — gets a smooth ramp without a second texture.
 */
export function severityRamp(): DataTexture {
  const data = new Float32Array(RAMP_ORDER.length * 4);
  RAMP_ORDER.forEach((level, i) => {
    const [r, g, b] = GLAZE_LINEAR[level];
    data[i * 4] = r;
    data[i * 4 + 1] = g;
    data[i * 4 + 2] = b;
    data[i * 4 + 3] = 1;
  });
  const map = new DataTexture(data, RAMP_ORDER.length, 1, RGBAFormat, FloatType);
  map.minFilter = LinearFilter;
  map.magFilter = LinearFilter;
  map.needsUpdate = true;
  return map;
}

/** The thumbprint tile, wrapped for the GPU. Generated (see `thumbprint.ts`). */
export function createThumbprintTexture(): DataTexture {
  const size = CLAY.thumbprint.tilePx;
  const map = new DataTexture(thumbprintTile(size), size, size);
  map.wrapS = RepeatWrapping;
  map.wrapT = RepeatWrapping;
  map.needsUpdate = true;
  return map;
}

export interface ClayMaterialHandles {
  readonly material: MeshStandardNodeMaterial;
  /**
   * The sun, in **world** space, from the tenant's real local time (§E7.4).
   *
   * World rather than view space so the caller does not have to know about the
   * camera: the sun over a city does not move when somebody pans.
   */
  readonly setSun: (direction: Vector3) => void;
  /** 0 = dry clay, 1 = soaked. §E7.4's *"monsoon means … wet clay darkening"*,
   *  and the same monsoon fact that normalises contractor SLAs. */
  readonly setWetness: (value: number) => void;
  readonly dispose: () => void;
}

export interface ClayMaterialOptions {
  /**
   * Whether the graph reads per-instance attributes.
   *
   * The ground plane is one mesh with no instances and would fail to compile
   * against `attribute("claySeverity")`, so it takes the same material with
   * this off — same recipe, same tokens, one branch, stated here rather than
   * discovered as a shader error.
   */
  readonly instanced: boolean;
  /** How many metres of world the thumbprint tile covers. Larger than a
   *  building on purpose: the grain should read as the material the whole city
   *  is made of, not as a texture applied to each block. */
  readonly tileMetres?: number;
  readonly thumbprint?: Texture;
  readonly ramp?: Texture;
}

export function createClayMaterial(options: ClayMaterialOptions): ClayMaterialHandles {
  const thumbprint = options.thumbprint ?? createThumbprintTexture();
  const ramp = options.ramp ?? severityRamp();
  const tileMetres = options.tileMetres ?? 24;

  const sun = uniform(new Vector3(0.4, 0.8, 0.35).normalize());
  const wetness = uniform(0);

  const material = new MeshStandardNodeMaterial();

  // 1 · matte albedo. `MeshStandardNodeMaterial` with no map is exactly what
  // §E7.1's "no PBR texture streaming" asks for: there is nothing to stream.
  const body: V3 = vec3(CLAY.bodyLinear[0], CLAY.bodyLinear[1], CLAY.bodyLinear[2]);

  // 2 · the thumbprint, rotated per instance by a hash of the entity id. The
  // uv is scaled to world metres so two adjacent blocks continue one another's
  // grain instead of each restarting the tile.
  const grain: F = options.instanced
    ? floatAttribute(CLAY_ATTRIBUTES.grain).div(float(CLAY.thumbprint.rotationSteps))
    : float(0);
  const grainUv = rotateUV(
    uv().mul(float(tileMetres / CLAY.thumbprint.tilePx)),
    grain.mul(Math.PI * 2),
    vec2(0.5, 0.5),
  );
  material.normalNode = normalMap(texture(thumbprint, grainUv), vec2(1, 1));

  // 3 · ambient occlusion, baked into the geometry's vertex colours and
  // deepened per instance. `occlusion` is the crowding of this particular
  // block; the vertex colour is where on the block we are.
  const instanceOcclusion: F = options.instanced
    ? floatAttribute(CLAY_ATTRIBUTES.occlusion)
    : float(0);
  const baked: F = vertexColor().r;
  const ao = clamp(oneMinus(oneMinus(baked).add(instanceOcclusion.mul(0.35))), CLAY.ao.floor, 1);

  // 4 · the sub-surface cheat. One Fresnel term, tinted warm where the sun
  // falls and cool where it does not — §E7.1: "this single term carries most
  // of the read".
  const facing = saturate(dot(normalWorld, sun));
  // Fresnel proper: how edge-on this fragment is to the camera. Clay is
  // faintly translucent at its edges, so the term has to follow the *silhouette*
  // — a term built from the light direction alone would put the glow on the lit
  // face, which is a highlight and not sub-surface scattering.
  const fresnel = pow(
    oneMinus(saturate(dot(normalView, positionViewDirection))),
    float(CLAY.rim.power),
  );
  const rimTint = mix(
    vec3(CLAY.coolLinear[0], CLAY.coolLinear[1], CLAY.coolLinear[2]),
    vec3(CLAY.warmLinear[0], CLAY.warmLinear[1], CLAY.warmLinear[2]),
    facing,
  );
  const rimAmount = mix(float(CLAY.rim.coolness), float(CLAY.rim.warmth), facing).mul(fresnel);

  // 5 · cut-card edges. A darker band a fixed number of *metres* below the top
  // face, on the sides only — so a building keeps the same card thickness
  // however tall it is and however close the camera gets.
  const heightMetres: F = options.instanced ? floatAttribute(CLAY_ATTRIBUTES.height) : float(1);
  const metresFromTop = float(0.5).sub(positionLocal.y).mul(heightMetres);
  const sideness = oneMinus(saturate(normalWorld.y));
  const edge = smoothstep(float(CLAY.edge.bevelMetres), float(0), metresFromTop).mul(sideness);

  // 6 · the fired glaze. Sampled from the ramp, mixed into the *albedo* and
  // paid for in roughness — never added as emission.
  const severityIndex: F = options.instanced ? floatAttribute(CLAY_ATTRIBUTES.severity) : float(-1);
  // −1 means unglazed. `step` rather than a comparison because a branch per
  // fragment is a branch per fragment, and the half-step threshold is what
  // makes the test "is this index a real one" rather than "is it positive".
  const glazed = step(float(-0.5), severityIndex);
  const rampU = severityIndex.max(float(0)).add(float(0.5)).div(float(RAMP_ORDER.length));
  const glaze = texture(ramp, vec2(rampU, float(0.5))).rgb;

  const clay = mix(body, glaze, glazed.mul(float(CLAY.glaze.coat)));
  const withRim = clay.add(rimTint.mul(rimAmount));
  const withEdge = withRim.mul(oneMinus(edge.mul(float(CLAY.edge.darkening))));
  const withGlazeRim = withEdge.mul(
    oneMinus(fresnel.mul(glazed).mul(float(CLAY.glaze.rimDarkening))),
  );

  // Wet clay is darker and shinier. Not a weather *effect* laid over the
  // scene — the material itself is wet, which is what §E7.4 means by the art
  // and the fairness mechanism being one fact rendered twice.
  material.colorNode = withGlazeRim.mul(oneMinus(wetness.mul(float(WEATHER.wetDarkening)))).mul(ao);

  material.roughnessNode = clamp(
    float(CLAY.surface.roughness)
      .sub(glazed.mul(float(CLAY.glaze.sheen)))
      .sub(wetness.mul(float(WEATHER.wetGloss))),
    0.05,
    1,
  );
  material.metalnessNode = float(CLAY.surface.metalness);

  // Left untouched, and asserted: `material.emissive` stays black and
  // `emissiveNode` stays null. §E7.3's reservation is enforced in
  // `tests/clay-material.test.ts`, not by this comment.

  return {
    material,
    setSun: (direction) => {
      sun.value.copy(direction).normalize();
    },
    setWetness: (value) => {
      wetness.value = Math.min(1, Math.max(0, value));
    },
    dispose: () => {
      thumbprint.dispose();
      ramp.dispose();
      material.dispose();
    },
  };
}
