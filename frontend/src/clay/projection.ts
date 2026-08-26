/**
 * Web Mercator → local ENU metres — M8.2.
 *
 * The base map, the tiles, and every coordinate the platform publishes are Web
 * Mercator. The clay world is not, and cannot be, for one reason that has
 * nothing to do with taste:
 *
 * **Mercator metres do not fit in a float.** Pune sits at roughly
 * `x = 8 220 000 m`, `y = 2 100 000 m` in Web Mercator. A `float32` — which is
 * what a vertex attribute, an instance matrix and a shader uniform all are —
 * has about 24 bits of mantissa, so near 8.2 million the spacing between
 * representable values is ~0.5 m. A pin would snap to a half-metre lattice, two
 * pins 40 cm apart would land on the same point, and the camera would visibly
 * quantise as it moved. Every symptom would read as a bug in the renderer.
 *
 * So the world is a **local East-North-Up frame in real ground metres**, with
 * its origin at the city. Coordinates stay inside ±6 km (`WORLD.extent`), where
 * `float32` resolves to well under a millimetre, and the conversion happens
 * once in `float64` on the CPU — here.
 *
 * **Why the cosine.** Mercator is conformal, which it buys by stretching:
 * one Mercator metre at latitude φ is `cos φ` ground metres. Subtracting the
 * origin without correcting for that would give a frame whose "metres" are 5%
 * long at Pune and 20% long at Srinagar — and §E23's budgets, §16.4's SLA
 * radii and this module's own building heights are all in *ground* metres. The
 * scale correction is taken at the origin latitude and held constant across the
 * frame, which is exactly the approximation that stops being good far from the
 * origin; `contains()` is where that limit is stated rather than assumed.
 *
 * Pure functions, `float64` throughout, no three.js import. That is deliberate:
 * this is the one piece of the clay engine that a Node test can assert to the
 * bit, and the precision claim above is asserted in `tests/clay-projection.test.ts`
 * rather than described.
 */

import { WORLD } from "@/design/generated/tokens";

/** WGS-84 equatorial radius, as Web Mercator (EPSG:3857) uses it — the sphere,
 *  not the ellipsoid, because that is what the tile scheme is defined on. */
export const EARTH_RADIUS_METRES = 6378137;

/** Web Mercator is undefined at the poles and every tile scheme clips here. */
export const MERCATOR_MAX_LATITUDE = 85.05112877980659;

export interface GeoPoint {
  readonly lat: number;
  readonly lng: number;
}

/** East and north, in ground metres from the frame's origin. Up is omitted
 *  rather than zeroed: this repository has no elevation source, and a `0`
 *  would read as "sea level, measured" when it means "not known". */
export interface LocalPoint {
  readonly east: number;
  readonly north: number;
}

export interface MercatorPoint {
  readonly x: number;
  readonly y: number;
}

export interface Projection {
  readonly origin: GeoPoint;
  /**
   * Ground metres per Mercator metre at the origin — `cos(lat₀)`.
   *
   * Exposed because it is the number the approximation rests on. A reader who
   * wants to know how wrong this frame is at its edge multiplies it out; a
   * reader who wants it hidden is the reader who later trusts it too far.
   */
  readonly scale: number;
  toLocal: (point: GeoPoint) => LocalPoint;
  toGeo: (point: LocalPoint) => GeoPoint;
  /** Whether a point is inside the frame the origin is honest for. */
  contains: (point: LocalPoint) => boolean;
}

/** Spherical Web Mercator, in Mercator metres. */
export function toMercator(point: GeoPoint): MercatorPoint {
  const lat = clampLatitude(point.lat);
  const phi = (lat * Math.PI) / 180;
  return {
    x: (EARTH_RADIUS_METRES * point.lng * Math.PI) / 180,
    y: EARTH_RADIUS_METRES * Math.log(Math.tan(Math.PI / 4 + phi / 2)),
  };
}

export function fromMercator(point: MercatorPoint): GeoPoint {
  return {
    lat: ((2 * Math.atan(Math.exp(point.y / EARTH_RADIUS_METRES)) - Math.PI / 2) * 180) / Math.PI,
    lng: (point.x / EARTH_RADIUS_METRES) * (180 / Math.PI),
  };
}

export function clampLatitude(lat: number): number {
  return Math.min(MERCATOR_MAX_LATITUDE, Math.max(-MERCATOR_MAX_LATITUDE, lat));
}

/**
 * A frame centred on `origin`.
 *
 * The projection is a value, not a module-level singleton, because a tenant is
 * a value too: a browser that opens two tenants in two tabs must not share one
 * origin, and a test that projects Pune and Nagpur in the same run must not
 * have to reset anything between them.
 */
export function createProjection(origin: GeoPoint): Projection {
  const anchor = toMercator(origin);
  const scale = Math.cos((clampLatitude(origin.lat) * Math.PI) / 180);
  const half = WORLD.extent.halfMetres;

  return {
    origin,
    scale,
    toLocal: (point) => {
      const m = toMercator(point);
      return { east: (m.x - anchor.x) * scale, north: (m.y - anchor.y) * scale };
    },
    toGeo: (point) =>
      fromMercator({ x: anchor.x + point.east / scale, y: anchor.y + point.north / scale }),
    contains: (point) => Math.abs(point.east) <= half && Math.abs(point.north) <= half,
  };
}

/**
 * The origin for a set of published places.
 *
 * The midpoint of the bounding box, not the mean of the centroids: a tenant
 * with thirty wards in one district and one on the far edge would have its
 * frame dragged to the crowd by a mean, putting the outlier closest to the
 * precision limit — which is the opposite of what an origin is for.
 *
 * Returns `null` for an empty set rather than a default coordinate. A default
 * would put a tenant with no published centroids somewhere real, and a map
 * centred on the wrong city is worse than a map that says it has nothing to
 * draw (§E3.3).
 */
export function originOf(points: readonly GeoPoint[]): GeoPoint | null {
  if (points.length === 0) return null;

  let minLat = Infinity;
  let maxLat = -Infinity;
  let minLng = Infinity;
  let maxLng = -Infinity;

  for (const point of points) {
    minLat = Math.min(minLat, point.lat);
    maxLat = Math.max(maxLat, point.lat);
    minLng = Math.min(minLng, point.lng);
    maxLng = Math.max(maxLng, point.lng);
  }

  return { lat: (minLat + maxLat) / 2, lng: (minLng + maxLng) / 2 };
}
