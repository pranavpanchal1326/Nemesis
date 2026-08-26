"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { BUDGET } from "@/design/generated/tokens";
import type { Strings } from "@/lib/i18n/strings";
import { steppedClock } from "@/lib/stepped-clock";

import { ClayScene } from "./ClayScene";
import type { ClayEntity } from "./entities";
import type { GeoPoint } from "./projection";
import type { SceneSample, SceneStage } from "./scene";
import type { SeasonalWeather } from "./sun";

/**
 * The Phase 19 gate, made readable — §E23, §E24.
 *
 * > **60 fps sustained with 5 000 instanced pins plus extruded buildings on
 * > this laptop, measured, with Ollama running.**
 * > **VRAM ≤ 512 MB asserted in CI**; draw calls under budget from
 * > `renderer.info`.
 *
 * Three of those four clauses are numbers a machine can read, and this
 * component is where it reads them: the scene publishes a sample about once a
 * second and the sample is written into the DOM as text, so the assertion in
 * `tests/clay.spec.ts` is a comparison against `renderer.info` rather than
 * against a screenshot or a stopwatch.
 *
 * **The fourth clause is a laptop, and no CI runner is one.** A headless
 * Chromium on SwiftShader will not hit 60 fps with five thousand pins and it
 * should not be asked to; the frame-rate number is *reported* here and the
 * budget assertions that are device-independent — draw calls, VRAM, backend
 * parity — are what the build fails on. Law 4: an honest measurement, published
 * as a number, beats a threshold quietly relaxed until it passes.
 *
 * **The entities are synthetic and say so.** This is the one surface in the
 * product where that is true, and the reason is the gate's own wording: five
 * thousand pins is a *load*, not a city, and no tenant has five thousand
 * published zone centroids to lend. Nothing here is reachable from a public
 * URL — `devOnly()` on the route sees to that — and no other surface may
 * construct a `ClayEntity` that did not come from a read or an event.
 */
export function ClayProof({
  strings,
  origin,
  weather,
  pins,
  seed,
  step,
  at,
  stage,
}: {
  readonly strings: Strings;
  readonly origin: GeoPoint;
  readonly weather: SeasonalWeather;
  readonly pins: number;
  readonly seed: number;
  /**
   * Freeze the 12 fps clock at this step, or `null` to let it run.
   *
   * §E24 asks for golden images "at a fixed seed **and camera**", and a fixed
   * seed alone does not give one: the gate weave, the press's misregistration
   * and every pin's Settle are functions of the *step*, so a running clock
   * photographs a different frame every time. `steppedClock.pin()` exists for
   * exactly this and for the reduced-motion path.
   */
  readonly step: number | null;
  /** The instant the sun is computed from, or `null` for the real one. */
  readonly at: string | null;
  /** Which stage of §E7's frame to photograph: the model, the photograph of
   *  it, or the print. */
  readonly stage: SceneStage;
}) {
  const [sample, setSample] = useState<SceneSample | null>(null);

  const onSample = useCallback((measured: SceneSample) => {
    setSample(measured);
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

  const entities = useMemo(() => syntheticEntities(pins, origin, seed), [pins, origin, seed]);

  return (
    <div data-proof="clay">
      <ClayScene
        entities={entities}
        strings={strings}
        city="Proof"
        origin={origin}
        weather={weather}
        surface="console-night"
        seed={seed}
        headingId="clay-proof-peers"
        {...(now === undefined ? {} : { now })}
        stage={stage}
        onSample={onSample}
      />

      {/*
        Rendered as text rather than as `data-` attributes on a div, so a person
        opening this route sees the same numbers the test reads. A proof surface
        whose proof is only legible to a test is a debug endpoint.
      */}
      <dl
        className="type-mono-data"
        data-proof="clay-stats"
        data-clay-fps={sample === null ? "" : sample.fps.toFixed(1)}
        data-clay-draws={sample === null ? "" : String(sample.drawCalls)}
        data-clay-memory-mb={sample === null ? "" : sample.memoryMb.toFixed(2)}
        data-clay-triangles={sample === null ? "" : String(sample.triangles)}
        data-clay-backend={sample?.backend ?? ""}
        data-clay-measured-tier={sample?.tier ?? ""}
        data-clay-pins={String(pins)}
        data-clay-stage={stage}
      >
        <Row label="pins" value={String(pins)} />
        <Row label="stage" value={stage} />
        <Row label="backend" value={sample?.backend ?? "…"} />
        <Row label="tier" value={sample?.tier ?? "…"} />
        <Row
          label={`fps (budget ${String(BUDGET.fps)})`}
          value={sample === null ? "…" : sample.fps.toFixed(1)}
        />
        <Row
          label={`draw calls (budget ${String(BUDGET.drawCalls)})`}
          value={sample === null ? "…" : String(sample.drawCalls)}
        />
        <Row
          label={`VRAM MB (budget ${String(BUDGET.vramMb)})`}
          value={sample === null ? "…" : sample.memoryMb.toFixed(2)}
        />
        <Row label="triangles" value={sample === null ? "…" : String(sample.triangles)} />
      </dl>
    </div>
  );
}

function Row({ label, value }: { readonly label: string; readonly value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd data-proof-value={label}>{value}</dd>
    </div>
  );
}

/**
 * A load, spread over the frame's own extent.
 *
 * Deterministic from `seed`, so the golden images this route backs photograph
 * the same city and the same pins on every machine (§E24). The distribution is
 * a spiral rather than a grid: a grid would let a whole row fall outside the
 * frustum together and make the draw-call count depend on the camera's yaw.
 */
function syntheticEntities(count: number, origin: GeoPoint, seed: number): readonly ClayEntity[] {
  const out: ClayEntity[] = [];
  const golden = Math.PI * (3 - Math.sqrt(5));

  for (let i = 0; i < count; i += 1) {
    const radius = Math.sqrt((i + 1) / count) * SPREAD_DEGREES;
    const theta = i * golden + seed;
    out.push({
      id: `proof:${String(i)}`,
      kind: "cluster",
      label: `Proof pin ${String(i)}`,
      point: {
        lat: origin.lat + radius * Math.cos(theta),
        lng: origin.lng + radius * Math.sin(theta),
      },
      // Every band, evenly, so the ramp texture is exercised across its whole
      // width rather than at one texel.
      severityScore: (i % 5) * 24 + 4,
      state: "resting",
      reports: { kind: "known", value: (i % 17) + 1 },
      // No arrival: a settling pin is a moving pin, and a golden image of a
      // moving pin is a golden image of whichever frame it caught.
      arrivedAtStep: null,
      href: null,
    });
  }
  return out;
}

/** About 1.6 km at Pune's latitude — the same order as the generated city's
 *  own radius, so the pins land on the clay rather than beyond it. */
const SPREAD_DEGREES = 0.015;
