"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { DegradedBanner } from "@/components/DegradedBanner";
import { notTranslatable, t, type Strings } from "@/lib/i18n/strings";
import { bedForSun, CUES } from "@/sound/cues";
import { sound } from "@/sound/graph";
import { startBed } from "@/sound/world-sound";
import { subscribeToEvents } from "@/lib/realtime/store";
import { steppedClock } from "@/lib/stepped-clock";
import { INK_SET, type InkSetName } from "@/design/generated/tokens";

import type { ClayEntity } from "./entities";
import { FlatMap } from "./FlatMap";
import { applyEnvelope, liveEntities, seedLive, type LiveClay } from "./live";
import { PeerList } from "./PeerList";
import type { GeoPoint } from "./projection";
import type { ClayScene as SceneHandle, SceneSample, SceneStage } from "./scene";
import { sunAt, type SeasonalWeather } from "./sun";
import {
  capabilitiesFor,
  forcedTier,
  ladderRung,
  pressQualityFor,
  readSignals,
  rungOf,
  rungRendersClay,
  tierFor,
  type Rung,
  type Tier,
} from "./tier";
import "./clay.css";

/**
 * `<ClayScene>` — §E26's last contract (A7), and M8's front door.
 *
 * > **3D host: renderer, adaptive quality, context-loss recovery, the
 * > accessible peer list.**
 *
 * Four responsibilities in one line, and the fourth decides the component's
 * shape. §E22 requires *"a synchronised accessible list view in the DOM — a
 * peer, not a fallback, always present"*, and "always present" includes the
 * tier where JavaScript never runs. So this is a client component that
 * **server-renders its list**: Next renders client components on the server,
 * the list arrives in the HTML, and a Tier D reader gets every place and every
 * figure with no script at all. What hydration adds is the canvas and the
 * selection — not the content.
 *
 * **The canvas is built in an effect, never in render.** A renderer is a device
 * handle, `WebGPURenderer.init()` is a promise, and React may render this
 * component twice in development. Every one of those is a reason the scene is
 * created imperatively in `scene.ts` and torn down by the effect that made it.
 *
 * **The tier is decided twice, and that is deliberate.** Before the renderer
 * exists we know only what the device *claims* — `ladderRung()` reads the
 * browser. Once the renderer has initialised we know which backend it actually
 * took, and S and A separate. ADR-0037's whole argument is that the second half
 * of that answer belongs to the renderer and not to us.
 *
 * **What this component does not do.** It does not fetch. Entities arrive as a
 * prop from a server read (`server/clay-data.ts`) and the live stream amends
 * them through `live.ts` — the socket is a hint and the read path is the
 * authority (§E14.3). It also does not decide what a pin *means*: `live.ts` is
 * where an event becomes a state, and the honest gap about cluster severity is
 * documented there rather than papered over here.
 */
/**
 * How often the ambient bed asks the sun where it is, in frames of the 12 fps
 * clock. 720 is one minute — three orders of magnitude more often than the bed
 * can actually change, and still nothing next to a frame budget.
 */
const BED_CHECK_STEPS = 720;

export function ClayScene({
  entities,
  strings,
  city,
  origin,
  weather,
  surface,
  seed,
  headingId = "clay-peers",
  now,
  stage,
  onSample,
  onReady,
}: {
  readonly entities: readonly ClayEntity[];
  readonly strings: Strings;
  /** The tenant's own name for itself, for the sentence that says what the
   *  model is a model of. */
  readonly city: string;
  /**
   * The frame's origin, or `null` where no place has a published centroid.
   *
   * `null` is a real and correct state, not an error: a tenant that has
   * published no coordinates has nothing to draw, the list says so in words,
   * and no canvas is created. Inventing an origin would put a generated city on
   * screen with no relationship to anywhere — scenery presented as a map.
   */
  readonly origin: GeoPoint | null;
  readonly weather: SeasonalWeather;
  readonly surface: InkSetName;
  readonly seed?: number;
  readonly headingId?: string;
  /**
   * The instant the sun is computed from.
   *
   * Injected only by the proof route. §E24 asks for golden images "at a fixed
   * seed and camera", and a scene lit by `new Date()` has neither: the sun
   * moves, so the same city photographed twice is two different photographs.
   */
  readonly now?: () => Date;
  /** Where to stop the frame. Only the proof route passes anything but the
   *  default; see `scene.ts`. */
  readonly stage?: SceneStage;
  /**
   * A frame-rate and cost sample, about once a second.
   *
   * The seam §E23's budgets are asserted through. Supplied only by the dev-only
   * proof route (`/developers/proof/clay`), which is where the Phase 19 gate's
   * five thousand pins live — a console showing an officer's real ward is not
   * the place to run a benchmark.
   */
  readonly onSample?: (sample: SceneSample) => void;
  /**
   * The live scene handle, or `null` when there is none — §E16, M9.
   *
   * Supplied only by the film, which drives the camera from Theatre.js once per
   * frame (`story/camera.ts`). It is an imperative handle rather than a prop
   * for the reason §E14.2 gives: a camera pose delivered through React state
   * would re-render the film sixty times a second, and the film is a page with
   * nine acts of DOM in it.
   *
   * Called with `null` on teardown, on a rebuild, and on a drop to a tier that
   * draws no canvas — so a holder can never keep a disposed renderer.
   */
  readonly onReady?: (scene: SceneHandle | null) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const sceneRef = useRef<SceneHandle | null>(null);

  const [live, setLive] = useState<LiveClay>(() => seedLive(entities));
  const [selectedId, setSelected] = useState<string | null>(null);
  /**
   * How far up the ladder this device may go. `null` until the probe has run.
   *
   * The rung and the tier are two different facts and both are kept, because
   * the rung is knowable before a renderer exists and the tier is not: S and A
   * differ only by the backend the renderer took, which is the renderer's
   * answer and not ours (ADR-0037). Collapsing them is how a host ends up
   * asking `tierFor(rung, null)` — correctly told "C" — and never building the
   * renderer that would have said otherwise.
   */
  const [ceiling, setCeiling] = useState<Rung | null>(null);
  /**
   * Bumped when a scene finishes building.
   *
   * The feed effect below runs on `shown`, and a scene that is still
   * initialising when it runs would never be fed — nor publish its digest —
   * until the next event arrived. On a quiet city that is *never*, which is
   * how the §E22 seam ends up empty on exactly the surface it exists to
   * check. Found by `tests/clay.spec.ts`.
   */
  const [built, setBuilt] = useState(0);
  const [tier, setTier] = useState<Tier | null>(null);
  const [cause, setCause] = useState<string | null>(null);
  /** Whether this device could draw a 2D map even though it is not drawing a
   *  3D one. `null` until the probe has run. */
  const [hasWebgl, setHasWebgl] = useState<boolean | null>(null);

  // The one array both renderers read, in the one order (`entities.ts`).
  // Deriving it once and handing the same reference to the list below and to
  // `scene.setEntities()` is the whole of §E22's "synchronised".
  const shown = useMemo(() => liveEntities(live), [live]);

  // The scene is built once and then *fed*. These refs are how the builder
  // reads current values without listing them as dependencies — a renderer
  // rebuilt because a pin arrived would drop every GPU resource in the scene
  // twelve times a second.
  // Updated in effects rather than during render, and declared *above* the
  // builder so React's ordering guarantees they are current by the time it
  // runs. A ref written during render is a ref whose value depends on whether
  // React kept the render, which is exactly the bug the rule catches.
  const shownRef = useRef(shown);
  const weatherRef = useRef(weather);
  const sampleRef = useRef(onSample);
  const nowRef = useRef(now);
  const readyRef = useRef(onReady);

  useEffect(() => {
    sampleRef.current = onSample;
  }, [onSample]);

  useEffect(() => {
    readyRef.current = onReady;
  }, [onReady]);

  useEffect(() => {
    nowRef.current = now;
  }, [now]);

  useEffect(() => {
    shownRef.current = shown;
  }, [shown]);

  useEffect(() => {
    weatherRef.current = weather;
  }, [weather]);

  // A refetch replaces the world. The stream's amendments are dropped on
  // purpose: §E14.3 makes the socket a hint and the read path the authority, so
  // a fresh read wins over anything a hint said about the same entity.
  //
  // Adjusted during render rather than in an effect. React's own guidance for
  // "reset state when a prop changes" is exactly this, and the effect version
  // is worse in a way that shows: it renders the *old* world once, then the
  // new one, so a refetch that removed a pin paints the removed pin for a
  // frame.
  const [seededFrom, setSeededFrom] = useState(entities);
  if (seededFrom !== entities) {
    setSeededFrom(entities);
    setLive(seedLive(entities));
  }

  // Tier, first pass — what the device claims, before any renderer exists.
  //
  // Deferred by one frame, and not for tidiness: `readSignals()` probes WebGL 2
  // by creating and discarding a real context (`tier.ts`), which is tens of
  // milliseconds of device work. Doing it in the effect body would spend them
  // in the frame that has just hydrated, which is the one frame in the page's
  // life that is already full.
  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      const signals = readSignals();
      const forced = forcedTier(window.location.search);
      const rung = forced === null ? ladderRung(signals) : rungOf(forced);
      setHasWebgl(signals.webgpu || signals.webgl2);
      setCeiling(rung);
      // **Null, not `tierFor(rung, null)`, while a canvas is being attempted.**
      // S and A differ only by the backend the renderer took, so until it has
      // taken one the tier is genuinely not known — and `tierFor(rung, null)`
      // answers "C", which would both mislabel the surface and flash the 2D
      // path in front of a canvas that was about to appear.
      setTier(forced ?? (rungRendersClay(rung) ? null : tierFor(rung, null)));
    });
    return () => {
      cancelAnimationFrame(frame);
    };
  }, []);

  // The live stream, outside React's render path (§E14.2). `applyEnvelope` is
  // pure, and the step is read from the clock rather than passed in, because an
  // event arrives between steps and the Settle it starts is measured from the
  // step it landed in.
  useEffect(
    () =>
      subscribeToEvents((envelope) => {
        setLive((current) => applyEnvelope(current, envelope, steppedClock.step));
      }),
    [],
  );

  const wantsCanvas = ceiling !== null && rungRendersClay(ceiling) && origin !== null;
  /**
   * The 2D path — §E15, M8.11.
   *
   * Tier C only, and only where the device *could* have drawn WebGL. That
   * separates the tier's two triggers, which are not the same situation:
   * `prefers-reduced-motion` is a person asking for stillness on a capable
   * machine, and a still 2D map answers it honestly; no WebGL at all leaves the
   * peer list standing alone, because MapLibre is WebGL too. `FlatMap.tsx`
   * carries the full argument.
   */
  const wantsFlat = !wantsCanvas && tier === "C" && origin !== null && hasWebgl === true;
  // A primitive key rather than the object, so a parent that rebuilds an
  // identical origin literal on every render does not rebuild the renderer.
  const originKey = origin === null ? null : `${String(origin.lat)},${String(origin.lng)}`;

  useEffect(() => {
    // `wantsCanvas` already carries `origin !== null`, and TypeScript's aliased
    // condition analysis carries the narrowing through — so `origin` is a
    // `GeoPoint` below without a second null check to fall out of step.
    if (!wantsCanvas) return;
    const canvas = canvasRef.current;
    if (canvas === null) return;

    // An `AbortController` rather than a captured boolean. A scene takes an
    // adapter, two awaits and a compile to build, and an effect that re-ran
    // while one was in flight would otherwise leak a renderer per re-run —
    // which on a device that is also running Ollama (ADR-0002) is the kind of
    // leak that ends a session rather than slowing it.
    const building = new AbortController();
    // Read through a call rather than off the property twice: two reads of the
    // same field either side of an `await` look identical to a narrowing
    // analysis, and the second one is the one that matters.
    const abandoned = (): boolean => building.signal.aborted;
    let handle: SceneHandle | null = null;

    void (async () => {
      // Imported lazily so three.js, the TSL graph and `three-mesh-bvh` are not
      // in the bundle of a tier that will never draw. A reduced-motion visitor
      // downloads the list and nothing else, which is the difference between a
      // fallback ladder and a fallback ladder that costs nothing.
      const { createClayScene } = await import("./scene");
      if (abandoned()) return;

      handle = await createClayScene({
        canvas,
        origin,
        entities: shownRef.current,
        surface,
        ceiling,
        weather: weatherRef.current,
        ...(seed === undefined ? {} : { seed }),
        ...(nowRef.current === undefined ? {} : { now: nowRef.current }),
        ...(stage === undefined ? {} : { stage }),
        onPlan: (plan) => {
          setTier(plan.tier);
          setCause(plan.cause);
        },
        onSample: (measured) => {
          sampleRef.current?.(measured);
        },
        onLostPermanently: (lostCause) => {
          // §E13: a second loss drops to Tier C **calmly**. The canvas goes,
          // the list stays exactly where it was, and the banner says what
          // happened in a sentence somebody can act on.
          setTier("C");
          setCause(lostCause);
        },
      });

      if (abandoned()) {
        handle.dispose();
        return;
      }
      sceneRef.current = handle;
      readyRef.current?.(handle);
      // Tier S and Tier A separate here and only here — on the backend the
      // renderer actually took (ADR-0037).
      setTier(handle.plan().tier);
      setBuilt((count) => count + 1);
    })();

    return () => {
      building.abort();
      readyRef.current?.(null);
      handle?.dispose();
      sceneRef.current = null;
    };
  }, [wantsCanvas, ceiling, origin, originKey, surface, seed, stage]);

  // Feeding the scene. One effect, one direction: React state in, instance
  // buffers out, no render.
  useEffect(() => {
    const scene = sceneRef.current;
    if (scene === null) return;
    scene.setEntities(shown);
    scene.setBloomUntilStep(live.bloomUntilStep);

    // **The assertion seam** (§E22). The canvas publishes the digest of the
    // array it was actually handed, and `<PeerList>` publishes the digest of
    // the array it actually rendered. `tests/clay.spec.ts` requires them to be
    // equal in every tier, which is how "synchronised" stops being a promise
    // and starts being a string comparison.
    const canvas = canvasRef.current;
    if (canvas !== null) canvas.dataset["clayDigest"] = scene.digest();
  }, [shown, live.bloomUntilStep, built]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (canvas === null) return;
    const observer = new ResizeObserver(() => {
      sceneRef.current?.resize(canvas.clientWidth, canvas.clientHeight);
    });
    observer.observe(canvas);
    return () => {
      observer.disconnect();
    };
  }, [wantsCanvas]);

  /**
   * §E12's ambient bed, cross-faded on the model's own time of day.
   *
   * The bed is chosen from the **same solar position the scene is lit by**
   * (`bedForSun`), so the city cannot sound like morning while it looks like
   * dusk — §E7.4's "the same fact, not two correlated ones", applied to the
   * hour.
   *
   * Re-evaluated on the stepped clock rather than on a timer: the bed changes
   * three times a day, so it is checked once every `BED_CHECK_STEPS` frames of
   * the 12 fps clock, which is the clock this whole world already runs on. A
   * `setInterval` here would be a second clock keeping worse time.
   *
   * The cross-fade is *start then stop*: both ramps are `SOUND.crossfadeMs`, so
   * the two sum to a constant and the city does not dip at the seam.
   */
  useEffect(() => {
    if (origin === null) return;
    let stop: (() => void) | null = null;
    let current: string | null = null;

    const evaluate = (): void => {
      const sun = sunAt(origin, now?.() ?? new Date());
      const wanted = bedForSun(sun.altitudeDeg, sun.azimuthDeg);
      if (wanted === current) return;
      const previous = stop;
      stop = startBed(wanted);
      current = wanted;
      previous?.();
    };

    evaluate();
    const unsubscribe = steppedClock.subscribe((step) => {
      if (step % BED_CHECK_STEPS === 0) evaluate();
    });

    return () => {
      unsubscribe();
      stop?.();
    };
  }, [origin, now]);

  /**
   * §E12's *"roller pass on print transitions"*.
   *
   * A print transition in this product is the press's quality dial changing —
   * §E6.4's dial is the ladder's first move, and when it turns, the picture is
   * genuinely re-printed at a different ink count and screen. Bound to that
   * rather than to a page change, which is what the page turn is for (§E3.4:
   * two cues that both meant "something changed" would mean nothing).
   *
   * The first quality is not a transition: a scene that rolled the press on
   * mount would make the sound every time somebody opened the console.
   */
  const printedAt = useRef<string | null>(null);
  useEffect(() => {
    if (tier === null) return;
    const quality = pressQualityFor(tier);
    const previous = printedAt.current;
    printedAt.current = quality;
    if (previous === null || previous === quality) return;
    sound.play("rollerPass", CUES.rollerPass.bus, CUES.rollerPass.recipe);
  }, [tier]);

  const select = useCallback((id: string | null) => {
    setSelected(id);
    sceneRef.current?.select(id);
    // §E12: *"pin push on select"*. Fired on selection rather than on
    // deselection — pushing a pin in is the act; taking one out is undoing it,
    // and a cue that fired for both would mean "something was clicked", which
    // is the §E3.4 failure.
    if (id !== null) sound.play("pinPush", CUES.pinPush.bus, CUES.pinPush.recipe);
  }, []);

  const onSelect = useCallback(
    (id: string) => {
      select(id);
    },
    [select],
  );

  const onPointerDown = useCallback(
    (event: React.PointerEvent<HTMLCanvasElement>) => {
      const scene = sceneRef.current;
      if (scene === null) return;
      const bounds = event.currentTarget.getBoundingClientRect();
      void scene.pickAt(event.clientX - bounds.left, event.clientY - bounds.top).then((id) => {
        select(id);
      });
    },
    [select],
  );

  const shownTier: Tier = tier ?? "D";
  const capabilities = capabilitiesFor(shownTier);

  return (
    <div
      className="clay"
      data-tier={tier ?? "pending"}
      data-clay-origin={originKey ?? "none"}
      /*
       * §E13's *renders* column, published where a test can read it — F16's
       * gate clause *"every tier S/A/B/C/D produces its documented rendering
       * when its trigger is forced"*.
       *
       * Derived here from `capabilitiesFor` and `pressQualityFor` rather than
       * re-stated, so the attribute cannot drift from the behaviour: if the
       * scene stops building a bloom node, this attribute stops saying it has
       * one, and `tests/ladder.spec.ts` fails on the tier whose row changed.
       * A seam, in the pattern of `data-clay-digest`.
       */
      data-clay-press={tier === null ? "pending" : pressQualityFor(tier)}
      data-clay-effects={
        tier === null
          ? "pending"
          : [
              capabilities.clay ? "clay" : null,
              capabilities.depthOfField ? "dof" : null,
              capabilities.bloom ? "bloom" : null,
              capabilities.gateWeave ? "weave" : null,
              capabilities.movingSun ? "sun" : null,
              capabilities.weather ? "weather" : null,
            ]
              .filter((flag) => flag !== null)
              .join(" ")
      }
    >
      {wantsCanvas ? (
        <div className="clay__stage">
          <canvas
            ref={canvasRef}
            className="clay__canvas"
            // Not in the tab order and not announced. The list beside it is the
            // accessible representation and carries every entity the canvas
            // draws — which the shared digest asserts rather than promises.
            aria-hidden="true"
            onPointerDown={onPointerDown}
          />
        </div>
      ) : null}

      {wantsFlat ? (
        <div className="clay__stage" data-clay-path="flat">
          <FlatMap entities={shown} origin={origin} stock={INK_SET[surface].stock} />
        </div>
      ) : null}

      <div className="clay__panel">
        {cause === null ? null : <DegradedBanner cause={t(strings, cause)} strings={strings} />}

        <PeerList
          entities={shown}
          strings={strings}
          tier={shownTier}
          selectedId={selectedId}
          headingId={headingId}
          // Supplied only once the client half is running. On the server render
          // this is undefined and the list is links, which is the correct
          // progressive answer rather than a degraded one.
          {...(tier === null ? {} : { onSelect })}
        />

        {/* Named only once it is known. A surface that announced a tier while
            its renderer was still starting would be stating a guess in the one
            place the product promises not to (§E3.3). */}
        {tier === null ? null : (
          <p className="clay__caption type-micro">
            {t(strings, "clay.tier", { tier: t(strings, `clay.tier.${tier}`) })}
          </p>
        )}

        {capabilities.weather && weather.label !== null ? (
          <p className="clay__caption type-micro">
            {t(strings, `clay.weather.${weather.kind}`)}{" "}
            {/* The tenant's own word for the season, verbatim. `sun.ts` refuses
                to classify it and so does this. */}
            <span className="clay__season">{notTranslatable(weather.label)}</span>
          </p>
        ) : null}

        <p className="clay__note type-caption">{t(strings, "clay.weather.note")}</p>
        <p className="clay__note type-caption">{t(strings, "clay.canvasLabel", { city })}</p>
      </div>
    </div>
  );
}
