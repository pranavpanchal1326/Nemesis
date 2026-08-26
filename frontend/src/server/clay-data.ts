import "server-only";

import { entitiesFromZones, type ClayEntity } from "@/clay/entities";
import { originOf, type GeoPoint } from "@/clay/projection";
import { CLEAR, weatherFromAdjustments, type SeasonalWeather } from "@/clay/sun";
import { fetchCity } from "@/server/public-data";
import { upstream } from "@/server/upstream";

/**
 * What the clay engine needs before it can draw anything — M8, F11.
 *
 * Three reads, and each is a deliberate choice of *which* endpoint answers a
 * question the scene could have answered for itself.
 *
 * **The places come from the public zone index**, not from a console read.
 * There is no console endpoint that publishes a centroid, and — more to the
 * point — a map that showed officers places the city has chosen not to publish
 * would put ADR-0046's publication decision on one screen and not on another.
 * The same places, the same suppression, the same figures; §E18's ward pages
 * and this map are the same data seen twice, which is the property that makes
 * an officer and a citizen able to have a conversation about one number.
 *
 * **The origin is derived, not configured.** `originOf()` takes the mean of
 * every published centroid. A tenant setting would be one more thing to get
 * wrong on provisioning day and would drift from the data the moment a ward was
 * added; a tenant with no published centroid gets `null`, which the scene
 * renders as *there is nothing to draw* rather than as an empty city.
 *
 * **The weather comes from the SLA engine.** `sun.ts` argues that at length; in
 * short, §E7.4 requires the model's rain and the contractor's deadline to be
 * *the same fact*, and the only way to make that true rather than correlated is
 * to ask the thing that computes the deadline. `preview-deadline` is read-only
 * and token-free by design, and its `adjustments` map is the seasonal windows
 * in force right now, in the tenant's own words.
 *
 * **Every read degrades to a stated absence.** A failed zone read is an empty
 * map with an honest sentence; a failed calendar read is clear weather rather
 * than invented rain. Neither takes the console down, because §E3.3's rule is
 * that a screen says what it knows, and "the calendar did not answer" is a
 * thing it knows.
 */
export interface ClayWorld {
  readonly entities: readonly ClayEntity[];
  readonly origin: GeoPoint | null;
  readonly weather: SeasonalWeather;
  /** The city's own name for itself, as published (ADR-0052, C8). */
  readonly cityName: string | null;
}

/** A short budget: long enough that a seasonal window applies to it, short
 *  enough that it cannot run past the window's end and average two seasons. */
const PROBE_HOURS = 4;

export async function fetchClayWorld(slug: string, locale: string): Promise<ClayWorld> {
  const [city, weather] = await Promise.all([fetchCity(slug, locale), fetchWeather()]);

  if (!city.ok) {
    return { entities: [], origin: null, weather, cityName: null };
  }

  const entities = entitiesFromZones(city.value.zones, (zone) => `/${slug}/ward/${zone.zoneCode}`);

  return {
    entities,
    origin: originOf(entities.map((entity) => entity.point)),
    weather,
    cityName: city.value.cityName,
  };
}

/**
 * Ask the fairness mechanism what season it is.
 *
 * `start` is *now*, in UTC, because a seasonal window is evaluated against the
 * calendar's own timezone by the backend — sending a local time here would ask
 * the question twice in two zones and get the wrong answer on exactly the days
 * either side of a window's edge.
 */
async function fetchWeather(): Promise<SeasonalWeather> {
  try {
    const { data, error } = await upstream.POST(
      "/api/v1/control-plane/calendars/preview-deadline",
      {
        body: { start: new Date().toISOString(), budget_hours: PROBE_HOURS },
        cache: "no-store",
      },
    );
    if (error !== undefined) return CLEAR;
    return weatherFromAdjustments(data.adjustments);
  } catch {
    // A control plane that is down is not a reason for the map to fail to draw,
    // and it is certainly not a reason to guess at the weather.
    return CLEAR;
  }
}
