/**
 * The clay scene — the imperative half of `<ClayScene>` (§E26, A7).
 *
 * Everything three.js touches is here, and **nothing React touches is**. That
 * split is §E14.2's rule applied at the largest scale it applies at: the scene
 * subscribes to the stepped clock and to the realtime bus outside React, writes
 * uniforms and instance buffers directly, and never causes a render. A city
 * that re-rendered a component tree twelve times a second to move five thousand
 * pins would spend its entire frame budget in the reconciler.
 *
 * It is also what makes the Phase 19 gate measurable. `createClayScene()` takes
 * a canvas and returns a handle; a Playwright test can drive it, count draw
 * calls out of `renderer.info`, force a context loss, and read the entity
 * digest — none of which is reachable through a component.
 *
 * **The order of the frame, which is the whole of §E7.**
 *
 *   scene   →  lens  →  press  →  canvas
 *   the model   the camera   the print
 *
 * The lens photographs the table; the press prints the photograph. They are two
 * passes because they are two physical processes, and collapsing them would
 * produce a screened blur — something no process produces and which reads,
 * unmistakably, as a filter.
 *
 * **What is real here and what is scenery.** The pins, their glaze, their
 * height and their arrival are real: every one comes from an event this system
 * published (`live.ts`). The buildings are generated and mean nothing, and
 * ADR-0047 is where that is argued rather than assumed. The sun and the rain
 * are real and come from the same SLA engine that stretches a contractor's
 * deadline (`sun.ts`). Nothing on this canvas is a decoration that looks like a
 * measurement, which is the §E3.3 line the whole layer is built against.
 */

import {
  AmbientLight,
  Color,
  DirectionalLight,
  DynamicDrawUsage,
  InstancedInterleavedBuffer,
  InstancedMesh,
  InterleavedBufferAttribute,
  Layers,
  Mesh,
  MeshBasicNodeMaterial,
  PerspectiveCamera,
  Raycaster,
  RenderPipeline,
  Scene,
  SphereGeometry,
  Vector2,
  Vector3,
} from "three/webgpu";
import type { BufferGeometry, Node } from "three/webgpu";
import { acesFilmicToneMapping, float, pass, screenUV, uniform, vec4 } from "three/tsl";

import {
  BUDGET,
  INK_LINEAR,
  INK_SET,
  LENS,
  PAPER_LINEAR,
  WEATHER,
  WORLD,
  RENDER,
} from "@/design/generated/tokens";
import type { InkSetName } from "@/design/generated/tokens";
import { steppedClock } from "@/lib/stepped-clock";
import { createPressPass, type PressPassHandles } from "@/press/press-tsl";
import { planPress } from "@/press/press-model";

import { CameraRig, configureCamera, type CameraPose } from "./camera";
import { generateCity } from "./city-kit";
import { CLAY_ATTRIBUTES, createClayMaterial } from "./clay-tsl";
import {
  canRender,
  INITIAL_LOSS_STATE,
  noteLoss,
  noteRestore,
  type ContextLossState,
} from "./context-loss";
import { entityDigest, type ClayEntity } from "./entities";
import { cityBuffers, clayBoxGeometry, groundGeometry, pinGeometry } from "./geometry";
import { createLensStack, flareColour, SAFETY_LAYER } from "./lens";
import { allocatePins, pinInstances, writePins } from "./pins";
import { installBvh, Picker, raycastPins } from "./picking";
import { createProjection, type GeoPoint } from "./projection";
import { advance, INITIAL_QUALITY, planFor, type QualityPlan, type QualityState } from "./quality";
import {
  backendOf,
  BYTES_PER_MB,
  createClayRenderer,
  frameStats,
  type FrameStats,
} from "./renderer";
import { keyIntensity, lightingSun, type SeasonalWeather } from "./sun";
import type { Backend, Rung, Tier } from "./tier";

/**
 * The four per-instance floats, in the order they sit inside one interleaved
 * vertex buffer.
 *
 * The shader reads them by name, so this order is an upload detail rather than
 * a contract — but it is written once, here, because the pack loop and the
 * attribute offsets have to agree and two lists that must match are one list.
 */
const PACKED_ORDER = [
  CLAY_ATTRIBUTES.severity,
  CLAY_ATTRIBUTES.grain,
  CLAY_ATTRIBUTES.occlusion,
  CLAY_ATTRIBUTES.height,
] as const;

const PACKED_STRIDE = PACKED_ORDER.length;

export interface ClaySceneOptions {
  readonly canvas: HTMLCanvasElement;
  /** The frame's origin — the tenant's own centre, from published centroids. */
  readonly origin: GeoPoint;
  readonly entities: readonly ClayEntity[];
  /** Which ink set the surface around the canvas is printing in (§E9.2). */
  readonly surface: InkSetName;
  /** How far up the ladder this device may go — `ladderRung()`'s answer. */
  readonly ceiling: Rung;
  readonly weather: SeasonalWeather;
  /** Gate-only: take the WebGL 2 backend even where WebGPU exists (ADR-0037). */
  readonly forceWebGL?: boolean;
  /** Fixed seed ⇒ reproducible city, weave and registration (§E24). */
  readonly seed?: number;
  /**
   * Where to stop the frame — the review seam, and the debugging one.
   *
   * `model` is the scene as the camera sees it, `photograph` adds the lens, and
   * `print` (the default, and the only one the product ships) adds the press.
   * The same idea as `<Press bypass>`: a pipeline whose stages cannot be looked
   * at one at a time is a pipeline that can only be debugged by opinion — and
   * the first blank sheet this scene produced took three guesses to explain
   * precisely because this did not exist.
   */
  readonly stage?: SceneStage;
  /** Injected so a golden image can photograph a fixed hour of a fixed day. */
  readonly now?: () => Date;
  readonly onPlan?: (plan: QualityPlan) => void;
  /**
   * A frame-rate and cost sample, about once a second.
   *
   * §E23's budgets are *measured*, and the only honest place to measure them is
   * the running scene. `tests/clay.spec.ts` reads these through the proof route
   * and fails the build on `renderer.info` rather than on an estimate — which
   * is what "asserted in CI" in the Phase 19 gate has to mean.
   */
  readonly onSample?: (sample: SceneSample) => void;
  readonly onSelect?: (id: string | null) => void;
  /** Fired when the device has dropped the scene for the second time and the
   *  ladder has moved permanently (§E13). */
  readonly onLostPermanently?: (cause: string) => void;
}

/** How far down the frame's pipeline to go (§E7). */
export type SceneStage = "model" | "photograph" | "print";

/** One second of frames, and what they cost. */
export interface SceneSample extends FrameStats {
  readonly fps: number;
  readonly tier: Tier;
  readonly backend: Backend;
  /** How much of §E23's VRAM budget the renderer says it is holding. */
  readonly memoryMb: number;
}

export interface ClayScene {
  readonly backend: Backend;
  /** Replace the world. Called from the realtime layer on every applied event
   *  and from the read path on every refetch — the same array both times, in
   *  the same order, which is what §E22's "synchronised" is made of. */
  readonly setEntities: (entities: readonly ClayEntity[]) => void;
  /** The step the fail-safe's bloom stops at, or `null`. §E7.3's reservation
   *  arrives here and nowhere else. */
  readonly setBloomUntilStep: (step: number | null) => void;
  readonly select: (id: string | null) => void;
  /**
   * Place the camera absolutely, or hand it back to the rig with `null` —
   * §E16, M9.4.
   *
   * The film's one seam into this module. §E16 authors the camera as *shots*,
   * which is a position and a subject rather than the subject alone the rig
   * takes; `story/camera.ts` drives this from Theatre.js once per frame. It
   * also carries the aperture, because Act 6's snap to miniature is a lens
   * change made by the camera and not by a selection — see `lens.ts`.
   *
   * Nothing outside `story/` calls it, and nothing inside `scene.ts` does: the
   * scene stays a scene, and the film stays the thing that photographs it.
   */
  readonly setCameraPose: (pose: (CameraPose & { apertureMetres: number }) | null) => void;
  /** Pointer pick, in canvas pixels. Resolves to an entity id or `null`. */
  readonly pickAt: (x: number, y: number) => Promise<string | null>;
  readonly resize: (width: number, height: number) => void;
  readonly stats: () => FrameStats;
  readonly digest: () => string;
  readonly plan: () => QualityPlan;
  readonly dispose: () => void;
}

/**
 * Build a scene on a canvas.
 *
 * Async because `WebGPURenderer.init()` is, and because a scene that rendered
 * one frame before its adapter arrived is the black flash every WebGPU
 * integration ships first and removes later.
 */
export async function createClayScene(options: ClaySceneOptions): Promise<ClayScene> {
  installBvh();

  const seed = options.seed ?? 1;
  const projection = createProjection(options.origin);
  const kit = generateCity(options.origin, { seed });
  const clock = options.now ?? (() => new Date());

  let entities = options.entities;
  let selectedId: string | null = null;
  let bloomUntilStep: number | null = null;
  /** Set whenever *what* is on the map changes, so the next stepped tick
   *  uploads. Cleared by the upload itself. */
  let pinsDirty = true;
  let quality: QualityState = INITIAL_QUALITY;
  /** The film's camera, while it holds one (§E16). `null` on every other
   *  surface, which is all of them but one. */
  let filmPose: (CameraPose & { apertureMetres: number }) | null = null;
  let loss: ContextLossState = INITIAL_LOSS_STATE;
  let disposed = false;

  // ------------------------------------------------------------------ scene

  const scene = new Scene();
  const camera = new PerspectiveCamera();
  const rig = new CameraRig();
  const picker = new Picker();
  const raycaster = new Raycaster();
  const ndc = new Vector2();
  const drawingBuffer = new Vector2();
  const resolution = uniform(new Vector2(1, 1));

  // The **sheet**, not the page ground. They differ on exactly one surface —
  // §E9.3's light table, where the ground is the room and the print on it is
  // backlit — and the difference is not cosmetic: a press given mitti-950 as
  // its paper multiplies three inks onto near-black and returns a black frame.
  // That is what the first golden image of this scene actually was, and it is
  // why `sheet` exists in `tokens.json` at all.
  const sheet = PAPER_LINEAR[INK_SET[options.surface].sheet];
  const pinBuffers = allocatePins(BUDGET.pins);

  let renderer = await create();
  renderer.renderer.info.autoReset = false;
  let built = build();

  rig.lookAt({ east: 0, north: 0 });
  seat();
  writeWorld(steppedClock.step);

  const unsubscribe = steppedClock.subscribe((step) => {
    writeWorld(step);
    built.press.setStep(step);
    built.lens.setStep(step);
    built.lens.setBloomFiring(bloomUntilStep !== null && step < bloomUntilStep);
  });

  let frame = requestAnimationFrame(tick);
  let lastFrameMs = performance.now();
  let sampleStartMs = lastFrameMs;
  let framesInSample = 0;

  return {
    backend: backendOf(renderer.renderer),

    setEntities: (next) => {
      entities = next;
      pinsDirty = true;
      writeWorld(steppedClock.step);
    },

    setBloomUntilStep: (step) => {
      bloomUntilStep = step;
    },

    setCameraPose: (pose) => {
      filmPose = pose;
      rig.setPose(pose);
      if (pose !== null) built.lens.setApertureMetres(pose.apertureMetres);
      else built.lens.setApertureMetres(LENS.tiltShift.apertureMetres);
    },

    select: (id) => {
      selectedId = id;
      const entity = entities.find((candidate) => candidate.id === id);
      if (entity !== undefined) rig.lookAt(projection.toLocal(entity.point));
      options.onSelect?.(id);
    },

    pickAt: async (x, y) => {
      if (!canRender(loss) || disposed) return null;
      const tier = currentPlan().tier;
      if (tier !== "S" && tier !== "A") return pickRay(x, y);
      const index = await picker.pick(renderer.renderer, camera, built.pins, x, y);
      return index === null ? null : (entities[index]?.id ?? null);
    },

    resize: (width, height) => {
      applySize(width, height);
    },

    stats: () => frameStats(renderer.renderer),
    digest: () => entityDigest(entities),
    plan: () => currentPlan(),

    dispose: () => {
      disposed = true;
      cancelAnimationFrame(frame);
      unsubscribe();
      teardown();
      picker.dispose();
      renderer.detach();
      renderer.renderer.dispose();
    },
  };

  // ------------------------------------------------------------------ setup

  async function create() {
    return createClayRenderer({
      canvas: options.canvas,
      ...(options.forceWebGL === undefined ? {} : { forceWebGL: options.forceWebGL }),
      onContextLost: onLost,
      onContextRestored: onRestored,
    });
  }

  /**
   * Everything that lives on the GPU, in one function.
   *
   * Written as a single builder rather than as constructor lines so that
   * context-loss recovery is *the same code path as first boot*. §E13 requires
   * recovery "without a page reload"; the only recovery that can be trusted is
   * one that runs the same builder, because a separate rebuild path is a second
   * implementation that is exercised once a year.
   */
  function build() {
    const material = createClayMaterial({ instanced: true });
    const groundMaterial = createClayMaterial({ instanced: false, tileMetres: 96 });

    const ground = new Mesh(groundGeometry(), groundMaterial.material);
    ground.frustumCulled = false;
    scene.add(ground);

    // One draw call for the city, and one for its live state. §E23's 32-call
    // budget is spent almost entirely on the post chain, which is the correct
    // place for it: geometry is cheap and compositing is not.
    const city = new InstancedMesh(clayBoxGeometry(), material.material, kit.footprints.length);
    const buffers = cityBuffers(kit);
    attach(city.geometry, buffers);
    city.instanceMatrix.set(buffers.matrix);
    city.instanceMatrix.needsUpdate = true;
    city.count = buffers.count;
    city.frustumCulled = false;
    scene.add(city);

    const pins = new InstancedMesh(pinGeometry(), material.material, BUDGET.pins);
    attach(pins.geometry, pinBuffers);
    pins.frustumCulled = false;
    // The BVH is what the keyboard path raycasts against (see `picking.ts`).
    // Built once here: the pin *geometry* never changes, only its instances.
    (pins.geometry as unknown as { computeBoundsTree?: () => void }).computeBoundsTree?.();
    scene.add(pins);

    const sun = new DirectionalLight(new Color(1, 1, 1), 1);
    sun.name = "sun";
    scene.add(sun);

    // A dim, colourless fill. Not an environment map: §E7.1 refuses PBR
    // texture streaming and an IBL is exactly that, one cube at a time.
    const fill = new AmbientLight(new Color(1, 1, 1), WEATHER.fillFraction);
    scene.add(fill);

    // §E7.3's reservation, as an object rather than as a condition. Nothing
    // else in this product is ever placed on `SAFETY_LAYER`, which is what
    // makes "bloom fires only for safety_trigger_fired" a structural claim.
    const flareMaterial = new MeshBasicNodeMaterial();
    flareMaterial.colorNode = flareColour(INK_LINEAR["riso-flu-pink"]);
    flareMaterial.toneMapped = false;
    const flare = new Mesh(new SphereGeometry(WORLD.pin.radiusMetres * 9, 16, 12), flareMaterial);
    flare.position.set(0, WORLD.camera.heightMetres / 3, 0);
    flare.layers.set(SAFETY_LAYER);
    scene.add(flare);

    // Two passes over one scene, separated by layer. The main pass may not see
    // the flare and the flare pass may not see anything else.
    const world = new Layers();
    world.enableAll();
    world.disable(SAFETY_LAYER);
    const scenePass = pass(scene, camera).setLayers(world);

    const safety = new Layers();
    safety.set(SAFETY_LAYER);
    const flarePass = currentPlan().capabilities.bloom
      ? pass(scene, camera).setLayers(safety)
      : null;
    // A glow is low-frequency by definition, so the bloom chain runs at a
    // quarter of the frame's resolution — sixteen times fewer pixels for an
    // effect nobody can point at the resolution of.
    flarePass?.setResolutionScale(0.25);

    const lens = createLensStack(
      {
        colour: scenePass.getTextureNode(),
        viewZ: scenePass.getViewZNode(),
        resolution,
        flare: flarePass === null ? null : flarePass.getTextureNode(),
      },
      { seed },
    );

    const stage = options.stage ?? "print";

    // **scene → lens → press → light.** The press asks the lens for the
    // photograph at each plate's own offset (§E6.1 stage 3), and the fail-safe's
    // glow is added to the *printed* sheet rather than to the photograph,
    // because a glow is light and light is not something a press can lay down.
    //
    // **The lens is inlined into each plate rather than resolved into a target,
    // and that is a measured decision rather than the obvious one.** Resolving
    // it once looks strictly cheaper — seven depth-of-field taps instead of
    // twenty-one — so it was implemented and measured on this laptop's Radeon
    // 780M at five thousand pins: **~21 fps inlined, ~2 fps resolved.** An extra
    // `setRenderTarget` between two render pipelines costs far more on this
    // backend than the taps it saves. `docs/reports/clay-frame-rate.md` has the
    // run; the arrangement stays inlined until a measurement says otherwise.
    /*
     * **Tone-mapped here, before the press reads it — Phase 19's ACES clause.**
     *
     * The press's separation asks *how much light must this sheet lose to reach
     * the photographed colour*, and it asks it of whatever it is handed. Handed
     * raw linear radiance it was asking an unanswerable question: on the story
     * run — brown, sunflower and aqua, and no black plate (§E9.2) — every clay
     * mid-tone solved past 1.0 coverage on the one plate that carries the clay,
     * clamped, and printed solid. The film was a flat brown field with the
     * model invisible in it, at every camera position in the track.
     *
     * A renderer-level `toneMapping` cannot fix that, because it runs on the
     * way *out* of the pipeline and the press is inside it. So the map is a
     * node, applied once to the photograph, and everything downstream — the
     * press, and the `photograph` stage a reviewer inspects — sees the same
     * display-referred frame. `RENDER.exposure` is authored in
     * `design/tokens.json`: an exposure is an art-direction decision, and it
     * belongs beside the inks it has to land inside.
     */
    const exposure = float(RENDER.exposure);
    // `as Node<"vec3">`: three's TSL functions are typed as the general `Node`
    // and the composers want a typed one. The same narrowing `lens.ts` and
    // `press-tsl.ts` do at their own boundaries, for the same reason — this is
    // the one place the graph's types are looser than the code around it.
    const photograph = (
      uvNode: Parameters<typeof lens.sampleAt>[0],
    ): ReturnType<typeof lens.sampleAt> =>
      vec4(acesFilmicToneMapping(lens.sampleAt(uvNode).rgb, exposure) as Node<"vec3">, 1);

    const plan = planPress({ surface: options.surface, quality: currentPlan().press, seed });
    const press: PressPassHandles = createPressPass(
      { sample: photograph, uv: screenUV, resolution },
      plan,
      sheet,
    );

    const output =
      stage === "model"
        ? scenePass.getTextureNode()
        : stage === "photograph"
          ? photograph(screenUV)
          : lens.overlay(press.node);

    const pipeline = new RenderPipeline(renderer.renderer, output);

    return {
      material,
      groundMaterial,
      ground,
      city,
      pins,
      sun,
      fill,
      flare,
      flareMaterial,
      lens,
      press,
      pipeline,
    };
  }

  function teardown() {
    scene.remove(built.ground, built.city, built.pins, built.sun, built.fill, built.flare);
    built.ground.geometry.dispose();
    built.city.geometry.dispose();
    built.pins.geometry.dispose();
    built.flare.geometry.dispose();
    built.flareMaterial.dispose();
    built.material.dispose();
    built.groundMaterial.dispose();
    built.pipeline.dispose();
  }

  // ------------------------------------------------------------------ frame

  function tick(nowMs: number): void {
    if (disposed) return;
    frame = requestAnimationFrame(tick);
    if (!canRender(loss)) {
      lastFrameMs = nowMs;
      return;
    }

    const elapsedMs = Math.max(0, nowMs - lastFrameMs);
    lastFrameMs = nowMs;
    // A first frame after a stall reports an absurd frame rate in both
    // directions. Clamped rather than skipped, because skipping it would let a
    // scene that stutters every second look healthy to the quality manager.
    const dt = Math.min(elapsedMs / 1000, 0.25);

    const before = quality.position;
    quality = advance(quality, { fps: dt > 0 ? 1 / dt : BUDGET.fps, elapsedMs });
    if (quality.position !== before) applyPlan();

    rig.update(camera, dt);
    // The film's aperture is re-applied after a rebuild rather than only when
    // it is set: `build()` constructs a new lens with the token's resting
    // value, and a context loss during Act 6 would otherwise restore the city
    // at full size in the middle of the miniature shot.
    if (filmPose !== null) built.lens.setApertureMetres(filmPose.apertureMetres);
    // The focal plane follows the camera's own subject (§E7.3). Not the
    // selected *pin's* depth: a tilt-shift focused on a 12 m object rather than
    // on the ground plane it stands on is a macro lens, not a miniature.
    built.lens.setFocusMetres(rig.focusDistance());

    // **Counted across the whole frame, not the last render inside it.**
    // `renderer.info` resets itself at the start of every `render()`, and the
    // lens and press chain performs several — so a scene that reads
    // `info.render.drawCalls` after the pipeline returns is reading the cost of
    // the final full-screen quad and reporting *one*. With `autoReset` off, the
    // counter spans the scene pass, the flare pass, the bloom chain, the
    // resolve and the print, which is the number §E23 budgets.
    renderer.renderer.info.reset();
    built.pipeline.render();
    sample(nowMs);
  }

  /**
   * Publish one second of frames, and what they cost.
   *
   * Counted over a wall-clock second rather than derived from the last frame's
   * delta: a single frame's reciprocal is noise, and the number this reports is
   * the one a gate is allowed to fail on.
   */
  function sample(nowMs: number): void {
    framesInSample += 1;
    const elapsed = nowMs - sampleStartMs;
    if (elapsed < 1000) return;

    const stats = frameStats(renderer.renderer);
    options.onSample?.({
      ...stats,
      fps: (framesInSample * 1000) / elapsed,
      tier: currentPlan().tier,
      backend: backendOf(renderer.renderer),
      memoryMb: stats.memoryBytes / BYTES_PER_MB,
    });
    sampleStartMs = nowMs;
    framesInSample = 0;
  }

  /**
   * Is anything still arriving at this step?
   *
   * A pin's Settle lasts `WORLD.pin.settleSteps`; outside that window
   * `pinInstances()` returns exactly what it returned last step, because it is
   * a pure function of (entities, projection, step) and the step only enters
   * through the Settle. So on a quiet city — which is most seconds of most
   * days — re-uploading five thousand instances twelve times a second uploads
   * twelve identical buffers a second.
   *
   * **It did not move the frame rate**, and that is worth writing down rather
   * than quietly keeping: at five thousand pins the measurement is ~21 fps
   * either way on this laptop (`docs/reports/clay-frame-rate.md`), because the
   * bottleneck is the drawing and not the upload. It stays because it is
   * nonetheless right — traffic that carries no news is traffic — and because
   * on a machine with a slower bus than this one the arithmetic changes.
   * The stepped clock still runs and the sun still moves.
   */
  function settling(step: number): boolean {
    for (const entity of entities) {
      if (entity.arrivedAtStep === null) continue;
      if (step - entity.arrivedAtStep <= WORLD.pin.settleSteps) return true;
    }
    return false;
  }

  /**
   * Write the world at one step of the clock.
   *
   * Pure inputs, in: entities, projection, step. Everything the GPU sees about
   * *live* state is written here and only here, which is why a golden image at
   * a fixed step is reproducible.
   */
  function writeWorld(step: number): void {
    // `force` on every path that changes *what* is on the map — a refetch, an
    // event, a rebuild after context loss. The skip below is only ever about a
    // step on which nothing happened.
    if (pinsDirty || settling(step)) {
      const count = writePins(pinBuffers, pinInstances(entities, projection, step));
      built.pins.count = count;
      built.pins.instanceMatrix.set(pinBuffers.matrix);
      built.pins.instanceMatrix.needsUpdate = true;
      markUpdated(built.pins.geometry, pinBuffers);
      pinsDirty = false;
    }

    const when = clock();
    const sun = lightingSun(options.origin, when);
    // ENU → three.js: east is +x, up is +y, north is −z. Stated once, here,
    // and nowhere else in the lighting path.
    built.sun.position.set(sun.east, sun.up, -sun.north).multiplyScalar(WORLD.camera.heightMetres);
    built.sun.intensity = Math.max(WEATHER.keyFloor, keyIntensity(sun.altitudeDeg));
    built.material.setSun(new Vector3(sun.east, sun.up, -sun.north));
    built.groundMaterial.setSun(new Vector3(sun.east, sun.up, -sun.north));

    const wetness = currentPlan().capabilities.weather ? options.weather.wetness : 0;
    built.material.setWetness(wetness);
    built.groundMaterial.setWetness(wetness);

    built.flare.visible = bloomUntilStep !== null && step < bloomUntilStep;

    if (selectedId !== null) {
      const entity = entities.find((candidate) => candidate.id === selectedId);
      if (entity !== undefined) rig.lookAt(projection.toLocal(entity.point));
    }
  }

  function currentPlan(): QualityPlan {
    return planFor(quality.position, options.ceiling, backendOf(renderer.renderer));
  }

  /**
   * The ladder moved. §E6.4's dial turns **first** — the press drops an ink and
   * coarsens its screen before a single frame is given up on — and that is the
   * one degradation in this product that improves the picture.
   */
  function applyPlan(): void {
    const plan = currentPlan();
    built.lens.setEffects({
      depthOfField: plan.capabilities.depthOfField,
      gateWeave: plan.capabilities.gateWeave,
    });
    // The press's plates are a *plan*, not a uniform, so a change of quality
    // rebuilds the node graph. Rare — four positions over a session at most —
    // and the alternative is three plate uniforms that are dead in two of the
    // three tiers.
    const rebuilt = planPress({ surface: options.surface, quality: plan.press, seed });
    // The handle is replaced, not just the node: the stepped clock calls
    // `setStep` on whatever `built.press` is, and leaving the old handle in
    // place would jitter a set of plate uniforms nothing is reading any more —
    // a press that has visibly stopped misregistering, with no error anywhere.
    built.press = createPressPass(
      { sample: built.lens.sampleAt, uv: screenUV, resolution },
      rebuilt,
      sheet,
    );
    if ((options.stage ?? "print") === "print") {
      built.pipeline.outputNode = built.lens.overlay(built.press.node);
    }
    built.pipeline.needsUpdate = true;
    options.onPlan?.(plan);
  }

  // ------------------------------------------------------------------- size

  function seat(): void {
    const width = options.canvas.clientWidth || options.canvas.width || 1;
    const height = options.canvas.clientHeight || options.canvas.height || 1;
    applySize(width, height);
  }

  function applySize(width: number, height: number): void {
    const w = Math.max(1, Math.floor(width));
    const h = Math.max(1, Math.floor(height));
    renderer.renderer.setSize(w, h, false);
    // Capped at 2. Above that the pixels are smaller than the halftone cell
    // and the press is printing a screen nobody can resolve, at four times the
    // fragment cost — which is the §E23 budget spent on nothing.
    renderer.renderer.setPixelRatio(Math.min(globalThis.devicePixelRatio || 1, 2));
    configureCamera(camera, w / h);
    renderer.renderer.getDrawingBufferSize(drawingBuffer);
    resolution.value.copy(drawingBuffer);
    built.lens.setFrameHeight(drawingBuffer.y);
  }

  // --------------------------------------------------------------- recovery

  function onLost(): void {
    const { state, response } = noteLoss(loss);
    loss = state;
    if (response.action === "degrade") {
      options.onLostPermanently?.(response.cause);
      return;
    }
    // Nothing else to do here. The rebuild happens on restore, because until
    // the context comes back there is no device to build anything on.
  }

  function onRestored(): void {
    if (disposed || loss.permanent) return;
    void rebuild();
  }

  /**
   * Rebuild after a recoverable loss — the Phase 19 gate, in one function.
   *
   * The renderer is replaced rather than reused. A `WebGPURenderer` whose
   * backend device has gone away holds a pipeline cache keyed to a device that
   * no longer exists; reusing it is how a "recovered" scene renders a black
   * frame and reports no error at all. The canvas is untouched, which is the
   * part the gate is actually about: no page reload, no remount, no lost
   * selection.
   */
  async function rebuild(): Promise<void> {
    teardown();
    renderer.detach();
    renderer.renderer.dispose();
    renderer = await create();
    renderer.renderer.info.autoReset = false;
    built = build();
    loss = noteRestore(loss);
    // Every GPU resource is new, so nothing on the card knows where the pins
    // are. The rebuild is exactly the case the skip above must not survive.
    pinsDirty = true;
    seat();
    writeWorld(steppedClock.step);
    applyPlan();
  }

  // ------------------------------------------------------------------ utils

  /**
   * The four per-instance floats, interleaved into **one** vertex buffer.
   *
   * Bound by the names `clay-tsl.ts` reads them under — the buffer and the
   * shader are in different files and a typo between them is a black material
   * rather than an error. What changed is the packing, and the reason is a
   * hard device limit rather than a performance preference.
   *
   * **WebGPU allows a pipeline eight vertex buffers, and this mesh wanted
   * nine:** `position`, `normal`, `uv`, `color`, `instanceMatrix`, and four
   * separate `InstancedBufferAttribute`s. Chromium refused the pipeline —
   * *"Vertex buffer count (9) exceeds the maximum number of vertex buffers
   * (8)"* — and the clay rendered nothing at all on the tier §E13 calls S.
   *
   * **CI could not see it, and that is the part worth recording.** The
   * headless harness runs on SwiftShader, which advertises no WebGPU adapter,
   * so `WebGPURenderer` selects its WebGL 2 backend (ADR-0037) — where the
   * limit is sixteen attributes and nine buffers is unremarkable. Every golden
   * image, every tier assertion and every frame-rate figure in
   * `docs/reports/clay-frame-rate.md` was measured on the backend that does not
   * have this limit. The scene was broken on real hardware for as long as it
   * has existed, and it was found by opening the page in a browser with a GPU.
   *
   * `three` groups attributes that share an `InterleavedBuffer` into a single
   * `arrayStride` entry (`WebGPUAttributeUtils.createShaderVertexBuffers`), so
   * four attributes over one buffer cost one slot instead of four: nine
   * becomes six, with two to spare. The CPU-side arrays in `pins.ts` and
   * `geometry.ts` are unchanged and stay the source of truth — this is the
   * upload layout, and `markUpdated` is where the two are reconciled.
   */
  function attach(
    geometry: BufferGeometry,
    buffers: {
      severity: Float32Array;
      grain: Float32Array;
      occlusion: Float32Array;
      height: Float32Array;
    },
  ): void {
    const interleaved = new InstancedInterleavedBuffer(
      new Float32Array(buffers.severity.length * PACKED_STRIDE),
      PACKED_STRIDE,
      1,
    );
    // `setUsage(DynamicDrawUsage)`: the pins half of this buffer is rewritten
    // whenever a report arrives or a settle animation is mid-flight, which is
    // the definition the flag exists for. The city half never changes and pays
    // nothing for the hint.
    interleaved.setUsage(DynamicDrawUsage);

    for (const [offset, name] of PACKED_ORDER.entries()) {
      geometry.setAttribute(name, new InterleavedBufferAttribute(interleaved, 1, offset));
    }
    pack(geometry, buffers);
  }

  /**
   * Copy the CPU arrays into the interleaved upload buffer.
   *
   * Derived state, refreshed at exactly the moments the old code flagged
   * `needsUpdate` — so a pin whose height changed on this step reaches the GPU
   * on this step, and nothing else is touched. `count` rather than `capacity`
   * is not available here and is not wanted: the buffers are allocated to
   * `BUDGET.pins` and the instances past `count` are not drawn, so packing all
   * of them writes zeroes nobody reads rather than branching per frame.
   */
  function pack(
    geometry: BufferGeometry,
    buffers: {
      severity: Float32Array;
      grain: Float32Array;
      occlusion: Float32Array;
      height: Float32Array;
    },
  ): void {
    const target = geometry.getAttribute(CLAY_ATTRIBUTES.severity) as InterleavedBufferAttribute;
    const packed = target.data.array as Float32Array;
    const sources = [buffers.severity, buffers.grain, buffers.occlusion, buffers.height];

    for (let i = 0; i < buffers.severity.length; i += 1) {
      const at = i * PACKED_STRIDE;
      for (let slot = 0; slot < PACKED_STRIDE; slot += 1) {
        packed[at + slot] = sources[slot]?.[i] ?? 0;
      }
    }
    target.data.needsUpdate = true;
  }

  function markUpdated(
    geometry: BufferGeometry,
    buffers: {
      severity: Float32Array;
      grain: Float32Array;
      occlusion: Float32Array;
      height: Float32Array;
    },
  ): void {
    pack(geometry, buffers);
  }

  /**
   * The ray path — `three-mesh-bvh`, on the CPU, synchronous.
   *
   * Used wherever the picking render cannot be afforded or cannot be read:
   *
   *   · **Tier B.** The picking pass is a whole extra render of the pins. On
   *     the lite tier, where the ladder has already given up depth of field to
   *     hold a frame rate, spending a draw call on a hit test is the wrong
   *     trade — and a BVH raycast against one instanced mesh is cheap enough
   *     that a pointer move does not show up in a profile.
   *   · **Anywhere without a device to read back from**, which is every unit
   *     test in `tests/clay-*.test.ts`. A picking implementation that only
   *     works in a browser is a picking implementation with no test.
   *
   * Both paths return the same index, so a selection made either way lands on
   * the same entity and the peer list cannot disagree with the canvas.
   */
  function pickRay(x: number, y: number): string | null {
    const width = options.canvas.clientWidth || 1;
    const height = options.canvas.clientHeight || 1;
    ndc.set((x / width) * 2 - 1, -(y / height) * 2 + 1);
    raycaster.setFromCamera(ndc, camera);
    const index = raycastPins(raycaster, built.pins);
    return index === null ? null : (entities[index]?.id ?? null);
  }
}
