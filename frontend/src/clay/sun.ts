/**
 * The sun and the season — M8.6, §E7.4, F11.
 *
 * > The model's sun position follows the tenant's actual local time. The
 * > model's weather follows the tenant's actual weather: monsoon means rain on
 * > the model, wet clay darkening, water in the gutters. **This is the same
 * > monsoon context that seasonally normalises contractor SLAs (§16.4). The art
 * > and the fairness mechanism are the same fact rendered twice.**
 *
 * That last sentence is a specification, not a flourish, and it rules out the
 * obvious implementation. A weather API would give the model rain that is
 * *correlated* with the monsoon and unrelated to it — two facts that agree most
 * of the time, which is worse than one fact, because the day they disagree is
 * the day an officer is looking at rain on a screen while the deadline clock
 * runs at dry-season speed. It is also a network dependency, against Phase 29's
 * air-gapped gate, and against §6 Principle #6.
 *
 * **So the weather is read from the SLA engine itself.** `POST
 * /api/v1/control-plane/calendars/preview-deadline` is read-only and token-free,
 * and its `adjustments` map is exactly the set of seasonal windows that are
 * stretching deadlines *right now*, with the tenant's own label and the
 * multiplier being applied. Asking it "what does a short budget starting now
 * cost" is asking the fairness mechanism what season it is. There is no second
 * source to drift.
 *
 * **The sun is computed, not fetched**, from the instant and the city's own
 * coordinates — the projection origin, which is derived from published zone
 * centroids. Thirty lines of astronomy is a smaller dependency than a sunrise
 * API and it works with the network unplugged.
 *
 * **What this module refuses to do.** It does not name a season. `label` is the
 * tenant's free-text word for the window — `monsoon`, `पावसाळा`, `shutdown` —
 * and it is rendered verbatim through `notTranslatable()`, never matched
 * against a keyword list. A frontend that decided a window was "the monsoon"
 * because its label contained the substring would be inventing a classification
 * the backend deliberately left to the customer (`db/models/calendar.py`: *"Free
 * text, tenant-defined … a label rather than a code"*).
 */

import { WEATHER } from "@/design/generated/tokens";
import type { GeoPoint } from "./projection";

/**
 * A unit vector in the local ENU frame.
 *
 * The scene converts it to three.js axes at the point it touches a light, so
 * nothing else in the clay layer has to remember that north is −z.
 */
export interface SunDirection {
  readonly east: number;
  readonly north: number;
  readonly up: number;
  /** Degrees above the horizon. Negative at night, and night is a real state:
   *  a city at 02:00 is dark, and lighting it anyway would be the model lying
   *  about the one thing it claims to be honest about. */
  readonly altitudeDeg: number;
  /** Degrees clockwise from north. */
  readonly azimuthDeg: number;
}

/**
 * The weather, as the SLA engine reports it.
 *
 * `multiplier` is the number a contractor's deadline is actually stretched by.
 * `wetness` is that number turned into a material property, and the conversion
 * is stated in tokens rather than here so the art direction can be tuned
 * without anybody touching the fairness mechanism to do it.
 */
export interface SeasonalWeather {
  /** The tenant's own word for the window, or `null` when none is in force. */
  readonly label: string | null;
  /** The SLA multiplier in force, or 1 when no window applies. */
  readonly multiplier: number;
  /** 0 = dry clay, 1 = soaked. */
  readonly wetness: number;
  /** Which of the three published words describes it — for the caption only. */
  readonly kind: "clear" | "rain" | "monsoon";
}

export const CLEAR: SeasonalWeather = {
  label: null,
  multiplier: 1,
  wetness: 0,
  kind: "clear",
};

/**
 * Turn a `DeadlineResponse.adjustments` map into weather.
 *
 * The map is `{ label: multiplier }` with the multiplier as a decimal *string*
 * — `Numeric(6,3)` on the way out, because a deadline a contractor may dispute
 * is not allowed to differ by a floating-point epsilon between two
 * computations. It is parsed here and used only for a shading term, which is
 * the one place that precision genuinely does not matter; the number that
 * matters never leaves the backend.
 *
 * **The strongest window wins**, not the first. A tenant may have a monsoon and
 * a local shutdown overlapping, and the model should show the one that is
 * actually costing the most time.
 */
export function weatherFromAdjustments(
  adjustments: Readonly<Record<string, string>> | null | undefined,
): SeasonalWeather {
  if (adjustments === null || adjustments === undefined) return CLEAR;

  let label: string | null = null;
  let multiplier = 1;

  for (const [name, raw] of Object.entries(adjustments)) {
    const value = Number(raw);
    // A window that stops the clock entirely (`is_working = false`) never
    // reaches this map, and a multiplier at or below 1 is not a hardship
    // window — it is a tenant recording that a season costs nothing.
    if (!Number.isFinite(value) || value <= multiplier) continue;
    multiplier = value;
    label = name;
  }

  if (label === null) return CLEAR;

  const span = Math.max(WEATHER.soakedMultiplier - 1, Number.EPSILON);
  const wetness = Math.min(1, (multiplier - 1) / span);
  return {
    label,
    multiplier,
    wetness,
    kind: wetness >= WEATHER.monsoonWetness ? "monsoon" : "rain",
  };
}

/**
 * Where the sun is, over this city, at this instant.
 *
 * NOAA's low-precision solar position: good to about a hundredth of a degree
 * over ±100 years, which is four orders of magnitude better than a shading term
 * can express. Written out rather than pulled from a package because it is
 * thirty lines, has no failure mode, and is one fewer dependency on the
 * critical path of an air-gapped boot.
 */
export function sunAt(origin: GeoPoint, when: Date): SunDirection {
  // Days since J2000.0, in UTC. Local time enters through longitude below —
  // solar position is a function of the *instant* and the place, and a
  // timezone is a label a government put on that pair.
  const days = when.getTime() / 86_400_000 + 2_440_587.5 - 2_451_545;

  const meanLongitude = radians(wrap360(280.46 + 0.985_647_4 * days));
  const meanAnomaly = radians(wrap360(357.528 + 0.985_600_3 * days));
  const eclipticLongitude =
    meanLongitude +
    radians(1.915) * Math.sin(meanAnomaly) +
    radians(0.02) * Math.sin(2 * meanAnomaly);
  const obliquity = radians(23.439 - 0.000_000_4 * days);

  const declination = Math.asin(Math.sin(obliquity) * Math.sin(eclipticLongitude));
  const rightAscension = Math.atan2(
    Math.cos(obliquity) * Math.sin(eclipticLongitude),
    Math.cos(eclipticLongitude),
  );

  // Greenwich mean sidereal time, in hours, then local — this is the step the
  // city's own longitude enters, and it is why a sun over Pune and a sun over
  // Nagpur are eighteen minutes apart.
  const greenwichSidereal = wrap24(18.697_374_558 + 24.065_709_824_419_08 * days);
  const localSidereal = radians(wrap360(greenwichSidereal * 15 + origin.lng));
  const hourAngle = localSidereal - rightAscension;

  const latitude = radians(origin.lat);
  const altitude = Math.asin(
    Math.sin(latitude) * Math.sin(declination) +
      Math.cos(latitude) * Math.cos(declination) * Math.cos(hourAngle),
  );
  const azimuth = Math.atan2(
    -Math.sin(hourAngle),
    Math.tan(declination) * Math.cos(latitude) - Math.sin(latitude) * Math.cos(hourAngle),
  );

  return {
    east: Math.cos(altitude) * Math.sin(azimuth),
    north: Math.cos(altitude) * Math.cos(azimuth),
    up: Math.sin(altitude),
    altitudeDeg: degrees(altitude),
    azimuthDeg: wrap360(degrees(azimuth)),
  };
}

/**
 * The sun the scene actually lights with.
 *
 * Clamped to a minimum altitude, and that clamp is a *stated* deviation rather
 * than a hidden one: a sun below the horizon would light the city from
 * underneath, which is not night — it is a lighting bug that looks like a
 * style. Night is rendered as the sun held at the horizon with the key light
 * turned down, which `keyIntensity()` owns, and the azimuth stays exactly where
 * the real sun is. So the shadows point the right way at 03:00 and a screenshot
 * at midnight is dark rather than upside down.
 */
export function lightingSun(origin: GeoPoint, when: Date): SunDirection {
  const sun = sunAt(origin, when);
  if (sun.up >= WEATHER.minSunUp) return sun;

  const horizontal = Math.hypot(sun.east, sun.north) || 1;
  const flat = Math.sqrt(Math.max(0, 1 - WEATHER.minSunUp * WEATHER.minSunUp));
  return {
    east: (sun.east / horizontal) * flat,
    north: (sun.north / horizontal) * flat,
    up: WEATHER.minSunUp,
    altitudeDeg: sun.altitudeDeg,
    azimuthDeg: sun.azimuthDeg,
  };
}

/**
 * How much key light there is, 0…1, from the real solar altitude.
 *
 * Civil twilight (−6°) is where the ramp bottoms out rather than 0°, because a
 * city fifteen minutes after sunset is not black, and rendering it black would
 * be as wrong as rendering it noon.
 */
export function keyIntensity(altitudeDeg: number): number {
  const span = WEATHER.fullLightDeg - WEATHER.twilightDeg;
  return Math.min(1, Math.max(0, (altitudeDeg - WEATHER.twilightDeg) / span));
}

/** The tenant's wall-clock hour, for the caption. Never used for the sun. */
export function localHour(when: Date, timeZone: string): number | null {
  try {
    const parts = new Intl.DateTimeFormat("en-GB", {
      timeZone,
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).formatToParts(when);
    const hour = Number(parts.find((part) => part.type === "hour")?.value);
    const minute = Number(parts.find((part) => part.type === "minute")?.value);
    if (!Number.isFinite(hour) || !Number.isFinite(minute)) return null;
    return hour + minute / 60;
  } catch {
    // An unknown IANA zone is a tenant configuration problem, not a reason for
    // the map to fail to draw. The sun is unaffected — it never used this.
    return null;
  }
}

function radians(deg: number): number {
  return (deg * Math.PI) / 180;
}

function degrees(rad: number): number {
  return (rad * 180) / Math.PI;
}

function wrap360(value: number): number {
  return ((value % 360) + 360) % 360;
}

function wrap24(value: number): number {
  return ((value % 24) + 24) % 24;
}
