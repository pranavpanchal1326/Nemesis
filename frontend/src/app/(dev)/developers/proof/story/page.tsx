import { Press } from "@/press/Press";
import { devOnly } from "@/lib/dev-only";
import { atAct, ACT_IDS, clampT, type ActId } from "@/story/acts";
import { shotOf } from "@/story/camera-keys";
import { Storyboard } from "@/story/Storyboard";
import { StoryShell } from "@/story/StoryShell";
import { Walk } from "@/story/Walk";
import type { StoryZone } from "@/story/acts/close";
import { fetchClayWorld } from "@/server/clay-data";
import { cityNameFallback, fetchCity, publishedTenant } from "@/server/public-data";
import { loadStrings } from "@/server/strings";

/**
 * The film, proved rather than described — §E24, §E25 Phase 20.
 *
 * `tests/story.spec.ts` drives this route to take the Phase 20 gate's
 * golden-image line: *"golden-image regression per scene at fixed seed and
 * camera."* Both halves of "fixed" are query parameters here, because both
 * halves are otherwise unfixable from the outside:
 *
 * · **`?act=merge` or `?t=0.74`** pins the spine. The film normally reaches a
 *   position by damping a smooth-scroll library toward it, which means a
 *   screenshot taken at any particular moment is a screenshot of an easing
 *   curve. A pinned spine attaches no scroll proxy at all (`Walk.tsx`).
 *
 * · **`?seed=`** pins the generated city, the gate weave and the press
 *   registration — the same seed the clay proof route takes, for the same
 *   reason.
 *
 * `?f=` is where inside the act to stand, `[0,1]`, so a reviewer can walk a
 * shot by hand: `?act=merge&f=0.55` is the frame the pull-back lands on.
 * `?tier=C` forces the storyboard, which is how the Phase 20 line *"every
 * fallback tier is exercised in CI by forcing its trigger"* is exercised for
 * this surface.
 *
 * Dev-only, per §E24 — a proof surface is not a public URL.
 */
/**
 * The hour the proof route lights its city from.
 *
 * Dusk, because §E16 Act 6 — the shot the whole direction rests on — is *"pull
 * back to the model at dusk"*, and a golden set photographed at noon would be
 * checking every act except the one that matters most. Fixed in UTC so the
 * runner's timezone is not part of the picture.
 */
const FIXED_INSTANT = "2026-08-26T13:10:00.000Z";

export default async function StoryProof({
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

  const actParam = one("act");
  const isAct = (value: string | undefined): value is ActId =>
    value !== undefined && (ACT_IDS as readonly string[]).includes(value);

  const fraction = Number(one("f") ?? "0.5");
  const position = isAct(actParam)
    ? atAct(
        actParam === "receipts" ? "table" : actParam,
        Number.isFinite(fraction) ? fraction : 0.5,
      )
    : clampT(Number(one("t") ?? "0"));

  const seedParam = Number(one("seed") ?? "1");
  const seed = Number.isFinite(seedParam) ? seedParam : 1;

  /**
   * A frozen world, by default.
   *
   * The clay proof route takes `?step=` and `?at=` and defaults to a live
   * scene, because a reviewer opening it by hand wants to see the engine run.
   * This route defaults the other way: its whole purpose is the golden image
   * per act, and a film photographed against a running 12 fps clock and a
   * moving sun never produces the same frame twice. `?step=live` releases both
   * for anybody who wants to watch a shot play.
   */
  const live = one("step") === "live";
  const stepParam = Number(one("step") ?? "0");
  const step = live ? null : Number.isFinite(stepParam) ? Math.trunc(stepParam) : 0;
  const at = live ? null : (one("at") ?? FIXED_INSTANT);

  /**
   * Which city to photograph.
   *
   * `?tenant=` first, then the deployment's own `NEMESIS_STORY_TENANT`. The
   * parameter exists because the film's gate has to be runnable on a checkout
   * that has not chosen a landing tenant — `nem seed-demo` publishes
   * `pune-demo` and `tests/story.spec.ts` photographs that — and because a
   * reviewer comparing two cities' establishing shots should not have to edit
   * an environment file between them. It is safe here for the same reason
   * every other parameter on this route is: the surface is dev-only, and the
   * slug names a city that has already *published* (ADR-0046), which is a
   * public fact by construction.
   */
  // `?stage=model|photograph|print` — the same control the clay proof has had
  // since M8, on the film. See the prop's own note in `Walk.tsx`: a frame that
  // arrives as a flat wash cannot be attributed to the model, the lens or the
  // press without one.
  const stageParam = one("stage");
  const slug = one("tenant") ?? publishedTenant() ?? null;
  const strings = await loadStrings(["common", "public"], "en");

  if (slug === null) {
    return (
      <div data-ground="paper" data-story-proof="storyboard">
        <Press quality="full" surface="story">
          <Storyboard strings={strings} city={cityNameFallback("")} />
        </Press>
      </div>
    );
  }

  const [world, city] = await Promise.all([fetchClayWorld(slug, "en"), fetchCity(slug, "en")]);
  const zones: readonly StoryZone[] = city.ok
    ? city.value.zones.map((zone) => ({ code: zone.zoneCode, name: zone.zoneName }))
    : [];

  return (
    // `data-ground` is load-bearing: the semantic role tokens resolve per
    // ground (§E9.3, §E22), and the proof route is the one surface that renders
    // outside a shell that would otherwise set it.
    <div
      data-ground="paper"
      data-story-proof={isAct(actParam) ? actParam : "t"}
      // The shot the camera track calls this, so a golden image's failure names
      // a move a reviewer can find in `camera-keys.ts` rather than a number.
      data-story-shot={isAct(actParam) ? shotOf(actParam) : ""}
    >
      <Press quality="full" surface="story">
        <StoryShell strings={strings}>
          <Walk
            strings={strings}
            locale="en"
            entities={world.entities}
            origin={world.origin}
            weather={world.weather}
            surface="story"
            city={world.cityName ?? cityNameFallback(slug)}
            citySlug={slug}
            zones={zones}
            publicApiBase={null}
            seed={seed}
            pinnedT={position}
            step={step}
            at={at}
            {...(stageParam === "model" || stageParam === "photograph" || stageParam === "print"
              ? { stage: stageParam }
              : {})}
          />
        </StoryShell>
      </Press>
    </div>
  );
}
