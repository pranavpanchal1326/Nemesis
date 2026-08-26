import { ClayProof } from "@/clay/ClayProof";
import type { SceneStage } from "@/clay/scene";
import { CLEAR, weatherFromAdjustments } from "@/clay/sun";
import { BUDGET } from "@/design/generated/tokens";
import { devOnly } from "@/lib/dev-only";
import { loadStrings } from "@/server/strings";

/**
 * The clay engine, proved rather than described — §E23, §E24, §E25 Phase 19.
 *
 * `tests/clay.spec.ts` drives this route to assert the three clauses of the
 * Phase 19 gate a machine can honestly check:
 *
 *   · draw calls under budget, from `renderer.info`
 *   · VRAM ≤ 512 MB, from `renderer.info`
 *   · the WebGL 2 backend renders the same scene as WebGPU, by golden image
 *
 * and to *report* the fourth — sustained frame rate at five thousand pins —
 * which is a property of a laptop and not of a CI runner (ADR-0002 puts a
 * language model on the same GPU by design).
 *
 * Every parameter is a query string so a reviewer can drive the same surface by
 * hand: `?pins=5000&tier=A&seed=1&wet=1.5`. The defaults are the gate's own
 * stated load, so opening the route with no parameters at all is running the
 * gate.
 *
 * Dev-only, per §E24 — a proof surface is not a public URL.
 */
export default async function ClayProofPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  devOnly();
  const params = await searchParams;
  const one = (key: string): string | undefined => {
    const value = params[key];
    return Array.isArray(value) ? value[0] : value;
  };

  const requested = Number(one("pins") ?? String(BUDGET.pins));
  const pins = Number.isFinite(requested)
    ? Math.max(0, Math.min(BUDGET.pins, Math.trunc(requested)))
    : BUDGET.pins;

  const seedParam = Number(one("seed") ?? "1");
  const seed = Number.isFinite(seedParam) ? seedParam : 1;

  // The weather is normally the SLA engine's answer (`server/clay-data.ts`).
  // Here it is a parameter, because a golden image of wet clay needs wet clay
  // on a day the tenant's calendar has no seasonal window — and faking it *at
  // the proof surface* is honest in a way that faking it in the console would
  // not be.
  const wet = one("wet");
  const weather = wet === undefined ? CLEAR : weatherFromAdjustments({ proof: wet });

  // A pinned step and a fixed instant are what make `?...&step=0&at=...` a
  // reproducible photograph rather than a screenshot of whenever the runner
  // got there. Absent, the scene runs live — which is what a reviewer opening
  // the route by hand wants to see.
  const stepParam = one("step");
  const step =
    stepParam === undefined || !Number.isFinite(Number(stepParam))
      ? null
      : Math.trunc(Number(stepParam));
  const at = one("at") ?? null;

  // `?stage=model|photograph|print`. The three stages of §E7's frame, so a
  // reviewer can see what the camera saw, what the lens did to it, and what
  // the press printed — separately, which is the only way to tell which of the
  // three is wrong.
  const stageParam = one("stage");
  const stage: SceneStage =
    stageParam === "model" || stageParam === "photograph" ? stageParam : "print";

  const strings = await loadStrings("common", "en");

  return (
    // `data-ground` is load-bearing, not decoration: the semantic role tokens
    // resolve per ground (§E9.3, §E22), and a dark sheet without it renders the
    // *light* theme's secondary ink on mitti-950 at 2.44:1 — which `axe` caught
    // on this route's first run, exactly as it is supposed to.
    <div
      data-ground="light-table"
      data-density="compact"
      style={{
        background: "var(--role-ground)",
        color: "var(--role-text-primary)",
        padding: "1rem",
        minBlockSize: "100dvh",
      }}
    >
      <ClayProof
        strings={strings}
        // Pune's centre. A real coordinate rather than 0,0 — the projection's
        // scale correction is latitude-dependent and a proof at the equator
        // would exercise the one latitude where it does nothing.
        origin={{ lat: 18.5204, lng: 73.8567 }}
        weather={weather}
        pins={pins}
        seed={seed}
        step={step}
        at={at}
        stage={stage}
      />
    </div>
  );
}
