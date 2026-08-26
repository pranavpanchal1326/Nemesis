"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { ClayEntity } from "@/clay/entities";
import { ClayScene } from "@/clay/ClayScene";
import type { GeoPoint } from "@/clay/projection";
import type { ClayScene as SceneHandle, SceneStage } from "@/clay/scene";
import type { SeasonalWeather } from "@/clay/sun";
import {
  forcedTier,
  ladderRung,
  readSignals,
  rungOf,
  rungRendersClay,
  type Rung,
} from "@/clay/tier";
import type { InkSetName } from "@/design/generated/tokens";
import { t, type Strings } from "@/lib/i18n/strings";
import { steppedClock } from "@/lib/stepped-clock";
import { SoundControl } from "@/sound/SoundControl";

import type { ActId } from "./acts";
import { ColdOpen, TheSilence, TheStop, TheWalk } from "./acts/opening";
import { TheReceipts } from "./acts/receipts";
import { TheMerge, TheCityAwake, TheTable, type StoryZone } from "./acts/close";
import { ThePipeline, TheReport } from "./acts/report";
import { openStudio, poseAt } from "./camera";
import { followTheBus } from "./live-story";
import { bindLenis } from "./lenis-proxy";
import { Storyboard } from "./Storyboard";
import { StoryFigure } from "./StoryFigure";
import { StorySpine, type SpineState } from "./spine";
import "./story.css";

/**
 * `<Walk>` — the film, assembled (§E16, M9).
 *
 * One fixed stage carrying the clay world, one scrolling reel of nine acts over
 * it, and a spine that turns the reel's scroll position into the `t` the camera
 * is driven by. Everything else in `story/` is a piece this component wires up.
 *
 * **The three loops, and why they are three.**
 *
 * · **The spine** damps the scroll into `t` (`spine.ts`) and hands it to
 *   Theatre.js, which interpolates the camera track (`camera.ts`), which is
 *   written to the renderer through `setCameraPose`. All of that happens
 *   outside React — §E14.2 — because it happens sixty times a second and the
 *   reel below it is nine acts of DOM.
 *
 * · **GSAP's ScrollTrigger** owns the DOM and the type, which is what §E16
 *   asks it to own. It sets `data-active` on the act that is on screen and the
 *   stylesheet does the rest; nothing here animates a property directly,
 *   because a motion written in JavaScript is a motion the §E11.1 audit cannot
 *   read.
 *
 * · **The bus** feeds `live-story.ts` from the realtime stream, and the acts
 *   that render events subscribe to *that* — never to the spine. A scene fired
 *   by a scroll position would fail the Phase 20 gate by definition.
 *
 * **The tier decision is deferred by one frame, deliberately, and it is the
 * same shape `<ClayScene>` uses.** The server renders the reel — nine acts of
 * real copy, which is §E13's Tier D — and a client effect then asks the device
 * what it can do. A device that has asked for reduced motion, or that has no
 * WebGL at all, is switched to the storyboard: §E11.1 says the landing switches
 * to *"the storyboard edit"* and §E13 makes that tier a design deliverable
 * rather than a fallback. The swap costs one frame of the reel being on screen,
 * and on precisely that device the reel is not animating, because
 * `prefers-reduced-motion` has already collapsed every animation in
 * `story.css` to an 84 ms fade.
 *
 * **A city with nothing published gets the storyboard too.** `origin === null`
 * means this deployment has published no centroid — `server/clay-data.ts`
 * argues why that is a real state rather than an error — and a film whose
 * establishing shot is a generated city bearing no relation to anywhere would
 * be scenery presented as a map (ADR-0047). The prints carry the same nine
 * acts and claim nothing about a place.
 */
export function Walk({
  strings,
  locale,
  entities,
  origin,
  weather,
  surface,
  city,
  citySlug,
  zones,
  publicApiBase,
  seed,
  pinnedT,
  step = null,
  at = null,
  stage,
}: {
  readonly strings: Strings;
  readonly locale: string;
  readonly entities: readonly ClayEntity[];
  readonly origin: GeoPoint | null;
  readonly weather: SeasonalWeather;
  readonly surface: InkSetName;
  readonly city: string;
  readonly citySlug: string;
  readonly zones: readonly StoryZone[];
  readonly publicApiBase: string | null;
  readonly seed?: number;
  /**
   * Which of §E7's three stages the clay renders — dev-only, and it exists
   * because of what it found.
   *
   * The clay proof route has had `?stage=model|photograph|print` since M8: a
   * reviewer can see what the camera saw, what the lens did to it, and what the
   * press printed, *separately*, which is the only way to tell which of the
   * three is wrong. The film had no such control, so a frame that arrived as a
   * flat wash could not be attributed to the model, the lens or the press
   * without editing the source. `/developers/proof/story?stage=model` is that
   * control, on the surface whose ink set (§E9.2: brown, sunflower, aqua — no
   * black plate) is the one under suspicion.
   */
  readonly stage?: SceneStage;
  /**
   * Hold the film at a fixed point on the spine.
   *
   * Supplied only by the proof route. §E24 wants golden images *"per act at
   * fixed `t`, seed and camera"*, and "fixed" has to survive a smooth-scroll
   * library that is still easing when the screenshot is taken — so the proof
   * route seeks the spine directly and attaches no scroll proxy at all.
   */
  readonly pinnedT?: number | null;
  /**
   * Freeze the 12 fps clock at this step, or `null` to let it run.
   *
   * The other half of "fixed camera", and the proof route is its only caller.
   * A pinned `t` fixes where the camera *is*; it does not fix the world it is
   * photographing — the gate weave, the press's misregistration and every pin's
   * Settle are functions of the step, so a running clock photographs a
   * different frame every time and a golden image never stabilises. Found by
   * exactly that: nine screenshot assertions timing out at "generating new
   * stable screenshot expectation".
   */
  readonly step?: number | null;
  /** The instant the sun is computed from, or `null` for the real one. A scene
   *  lit by `new Date()` is a different scene every hour. */
  readonly at?: string | null;
}) {
  const root = useRef<HTMLDivElement>(null);
  const reel = useRef<HTMLDivElement>(null);
  const scene = useRef<SceneHandle | null>(null);

  const [ceiling, setCeiling] = useState<Rung | null>(null);

  /**
   * The act the spine is on, in React state — the only thing on this surface
   * that is.
   *
   * Everything else the spine publishes goes straight to the DOM, sixty times a
   * second, for the reason the module note gives. The act is different: it
   * changes nine times in a whole film and §E8.1's character inputs are a
   * *declaration* per act rather than a value per frame, so the figure is
   * driven by props like any other component and the clock stays out of React.
   */
  const [act, setAct] = useState<ActId>("cold-open");

  const onReady = useCallback((handle: SceneHandle | null) => {
    scene.current = handle;
  }, []);

  // --- the ladder, one frame after hydration (see the note above) ----------
  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      try {
        const forced = forcedTier(window.location.search);
        setCeiling(forced === null ? ladderRung(readSignals()) : rungOf(forced));
      } catch {
        // **A probe that throws answers the storyboard, not silence.**
        // `readSignals()` asks the browser for a WebGL 2 context to find out
        // whether it can have one, and a browser that has run out of contexts
        // — the cap is around sixteen per process — can throw rather than
        // return null. Leaving `ceiling` at `null` would hold the surface at
        // "undecided" for the rest of the page's life, which renders the film's
        // reel with no camera behind it: a scroll film that does not move.
        //
        // Found by the gate, which is the only place a browser is asked to
        // build this scene sixteen times in eight minutes — but the same
        // condition is a phone with several tabs open, and the honest answer
        // there is the same one. If we cannot find out what the device can do,
        // we do not run the film.
        setCeiling(rungOf("C"));
      }
    });
    return () => {
      cancelAnimationFrame(frame);
    };
  }, []);

  useEffect(() => {
    if (step === null) return;
    steppedClock.pin(step);
    return () => {
      steppedClock.pin(null);
    };
  }, [step]);

  const now = useMemo(() => {
    if (at === null) return undefined;
    const instant = new Date(at);
    if (Number.isNaN(instant.getTime())) return undefined;
    return () => instant;
  }, [at]);

  // --- the bus. Subscribed for every tier, because the events are facts about
  //     the system and the storyboard reader is owed them too.
  useEffect(() => followTheBus(), []);

  // --- Theatre's studio, opt-in and dev-only (§E15, §E24) ------------------
  useEffect(() => {
    if (!new URLSearchParams(window.location.search).has("studio")) return;
    void openStudio();
  }, []);

  const filmable = origin !== null && ceiling !== null && rungRendersClay(ceiling);

  // --- the spine, the camera, and the DOM ----------------------------------
  useEffect(() => {
    if (!filmable) return;
    const element = root.current;
    if (element === null) return;

    const walk = new StorySpine();

    let lastAct = "";
    const unsubscribe = walk.subscribe((state) => {
      write(element, state);
      // The act's own attribute, changed only when the act changes. GSAP owns
      // this while the film is scrolling (below); this is what owns it when the
      // film is pinned, and keeping one writer per situation is cheaper than a
      // querySelectorAll sixty times a second.
      if (state.act.id !== lastAct) {
        lastAct = state.act.id;
        for (const section of element.querySelectorAll<HTMLElement>("[data-act]")) {
          section.dataset["current"] = String(section.dataset["act"] === state.act.id);
        }
        // The one React write in this loop, and it happens nine times in a
        // film rather than sixty times a second. See `act` above.
        setAct(state.act.id);
      }
      // Theatre interpolates, the rig places, the lens racks. One call, once a
      // frame, and no React render anywhere in it.
      scene.current?.setCameraPose(poseAt(state.t));
    });

    if (pinnedT === undefined || pinnedT === null) {
      const reelElement = reel.current;
      if (reelElement !== null) walk.attach(bindLenis(reelElement));
    } else {
      walk.seek(pinnedT);
    }

    return () => {
      unsubscribe();
      walk.detach();
      scene.current?.setCameraPose(null);
    };
  }, [filmable, pinnedT]);

  // --- GSAP ScrollTrigger: the DOM and the type (§E16) ----------------------
  useEffect(() => {
    if (!filmable) return;
    const container = reel.current;
    if (container === null) return;
    // A pinned film is a still frame; ScrollTrigger would find no scroll to
    // trigger on and would mark whichever act happens to be at the top active,
    // which is not the act the proof route asked for. `write()` sets the
    // active act from `t` in that case, which is the correct source anyway.
    if (pinnedT !== undefined && pinnedT !== null) return;

    let triggers: { kill: () => void }[] = [];
    // A cell rather than a `let`. The only writer is the cleanup below, which
    // the async body cannot see, so a local boolean narrows to `false` and both
    // TypeScript and the linter conclude the guard is dead code — which is
    // exactly wrong: it is the guard that stops a torn-down effect from
    // installing triggers on a detached tree.
    const abandoned = { current: false };

    void (async () => {
      // Imported lazily so a device on the storyboard rung never downloads a
      // motion library it will not run — the same argument `<ClayScene>` makes
      // for three.js, and the reason both are dynamic imports.
      const { gsap } = await import("gsap");
      const { ScrollTrigger } = await import("gsap/ScrollTrigger");
      if (abandoned.current) return;
      gsap.registerPlugin(ScrollTrigger);

      triggers = [...container.querySelectorAll<HTMLElement>("[data-act]")].map((section) =>
        ScrollTrigger.create({
          trigger: section,
          start: "top center",
          end: "bottom center",
          onToggle: (self) => {
            section.dataset["active"] = String(self.isActive);
          },
        }),
      );
    })();

    return () => {
      abandoned.current = true;
      for (const trigger of triggers) trigger.kill();
    };
  }, [filmable, pinnedT]);

  // --- the storyboard rung -------------------------------------------------
  // `ceiling === null` is *before the probe*, and it renders the reel — which
  // is what the server rendered, so hydration matches. Once the probe answers,
  // a device that cannot or should not run the film gets the prints.
  if (ceiling !== null && !filmable) {
    return <Storyboard strings={strings} city={city} />;
  }

  return (
    <div
      className="walk"
      ref={root}
      data-story="walk"
      data-tier={ceiling ?? "pending"}
      // A pinned film is a still frame, and a still frame has no beats to land,
      // so the reel's scroll snapping is switched off with it. Not cosmetic:
      // `scroll-snap-type` re-snaps after any programmatic scroll, so an
      // element on the proof route could be scrolled to and then moved again a
      // frame later — which a browser automation harness correctly reports as
      // "the element never became stable", and which cost an afternoon to see.
      data-pinned={pinnedT === undefined || pinnedT === null ? "false" : "true"}
    >
      {/* The skip link is first in the document and goes to the receipts,
          because §E16 Act 9 is where a reader who does not want a film finds
          the evidence. A nine-viewport scroll with no way past it would be the
          one accessibility failure this surface could not argue away. */}
      <a className="walk__skip type-micro" href="#act-receipts">
        {t(strings, "story.skip")}
      </a>

      {/* The two front doors, first in the document after the skip link.
          §E16's film is an argument, not a menu — and until ADR-0059 that was the
          whole problem: the landing argued beautifully and then left a visitor
          to guess a URL. These are the two ways in, in the tab order ahead of
          nine acts of scroll, for the same reason the skip link is. */}
      <nav className="walk__ways type-micro" aria-label={t(strings, "story.ways")}>
        <Link href="/citizen">{t(strings, "portal.citizen.title")}</Link>
        <Link href="/staff">{t(strings, "portal.staff.title")}</Link>
      </nav>

      {/*
        §E12's unmute, beside the skip link and before the film. *"Designed
        rather than hidden"* means a reader decides about sound **before** it
        could have happened to them, which on this surface means above the
        fold and in the tab order ahead of nine acts of scroll.
      */}
      <div className="walk__sound">
        <SoundControl strings={strings} />
      </div>

      {/* The film — the stage, the figure and the reel. A box of its own, so the
          sticky stage releases when the film ends rather than riding over Act 9's
          receipts, which are a printed page and not part of the shot. */}
      <div className="walk__film">
        {origin === null ? null : (
          <div className="walk__stage" aria-hidden={false}>
            <ClayScene
              entities={entities}
              strings={strings}
              city={city}
              origin={origin}
              weather={weather}
              surface={surface}
              {...(stage === undefined ? {} : { stage })}
              onReady={onReady}
              {...(seed === undefined ? {} : { seed })}
              {...(now === undefined ? {} : { now })}
            />
          </div>
        )}

        {/*
        The Reporter — §E8.2, F15. Above the clay and below the reel: the figure
        is ink and the city is clay, and §E5's three-material law is a
        compositing order as much as an art direction. Outside the reel, because
        the reel scrolls and the figure is on the stage.
      */}
        <div className="walk__figure" aria-hidden={false}>
          <StoryFigure strings={strings} act={act} />
        </div>

        <div className="walk__reel" ref={reel}>
          <ColdOpen strings={strings} />
          <TheWalk strings={strings} />
          <TheStop strings={strings} />
          <TheSilence strings={strings} />
          <TheReport strings={strings} locale={locale} />
          <ThePipeline strings={strings} />
          <TheMerge strings={strings} />
          <TheCityAwake strings={strings} city={city} citySlug={citySlug} zones={zones} />
          <TheTable strings={strings} />
        </div>
      </div>

      <TheReceipts
        strings={strings}
        citySlug={citySlug}
        zones={zones}
        publicApiBase={publicApiBase}
      />
    </div>
  );
}

/**
 * Publish the spine's state onto the DOM.
 *
 * Attributes rather than React state, for the reason the whole module is built
 * on — and because they are the seam the gates read. `data-walk-metres` in
 * particular is F12's gate: *"stopping the scroll stops the walk"* is asserted
 * by holding the scroll still and reading this number twice, which is only
 * possible because the number is a pure function of `t` and is written where a
 * test can see it.
 */
function write(element: HTMLElement, state: SpineState): void {
  element.dataset["storyT"] = state.t.toFixed(4);
  element.dataset["storyAct"] = state.act.id;
  element.dataset["walkMetres"] = state.walked.toFixed(2);
  element.style.setProperty("--story-t", state.t.toFixed(4));
}
