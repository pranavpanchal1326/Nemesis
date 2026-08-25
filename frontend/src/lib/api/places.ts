import type { components } from "@/generated/api";

import { ApiError } from "./complaints";

/**
 * Where the citizen is standing, named — §E17.1.
 *
 * > Auto-located and presented as a *card*, not a picker … Never ask someone
 * > standing in traffic beside a pothole to pinch-zoom a map.
 */

export type PlaceResolution = components["schemas"]["PlaceResolution"];
export type PlaceUnit = components["schemas"]["PlaceUnit"];

export async function resolvePlace(
  latitude: number,
  longitude: number,
  signal?: AbortSignal,
): Promise<PlaceResolution> {
  const query = new URLSearchParams({
    latitude: String(latitude),
    longitude: String(longitude),
  });
  const response = await fetch(`/api/places/resolve?${query.toString()}`, {
    signal: signal ?? null,
  });
  if (!response.ok) {
    throw new ApiError(response.status, "That location could not be placed.");
  }
  return (await response.json()) as PlaceResolution;
}

/**
 * The one-line description of a place — *"Kothrud · West Zone · Pune"*.
 *
 * Innermost first, because that is the order §E17.1's example is written in and
 * the order a person answers *"where are you?"* in. Joined with a middle dot
 * rather than a comma: §E10.2 reserves the comma for numbers, and a list of
 * places separated by commas reads as one address the system is claiming to
 * know precisely, which it is not.
 *
 * Returns `null` rather than a placeholder when nothing resolved. §E3.3 — the
 * caller renders the omission, and the omission is different depending on
 * *why*, which is what `boundaries_configured` is for.
 */
export function describePlace(resolution: PlaceResolution): string | null {
  // `units` is optional in the generated type because the server declares a
  // default. Read through it rather than asserting: a default on the server is
  // not a guarantee on the wire, and `noUncheckedIndexedAccess` is on precisely
  // so that distinction has to be handled rather than assumed.
  const units = resolution.units ?? [];
  if (units.length === 0) return null;
  return units.map((unit) => unit.name).join(" · ");
}

/** How precisely a coordinate is stated to a person.
 *
 *  Five decimals is about a metre, which is more precision than anybody can
 *  read and more than a GPS fix actually has. Four is roughly 11 m — the scale
 *  of *"that pothole, not the next one"*, which is the scale the citizen is
 *  actually pointing at. */
export function describeCoordinates(latitude: number, longitude: number): string {
  return `${latitude.toFixed(4)}, ${longitude.toFixed(4)}`;
}
