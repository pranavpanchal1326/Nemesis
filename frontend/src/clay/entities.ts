/**
 * What is in the clay — the one list the canvas and the DOM both render.
 *
 * §E22 states the requirement and states it as a peer relationship, twice:
 *
 * > **The 3D map always has a synchronised accessible list view in the DOM — a
 * > peer, not a fallback, always present.** A canvas is opaque to assistive
 * > technology; a list of the same entities is not.
 *
 * "Synchronised" is the load-bearing word, and it is the reason this module
 * exists rather than each renderer building its own array. The failure mode of
 * an accessible peer is not that it is missing — that gets caught in the first
 * review — it is that it **drifts**: the canvas filters by viewport and the list
 * does not, the canvas sorts by severity and the list by name, a merge removes a
 * pin and leaves a row. A screen-reader user then works from a different city to
 * the one on screen, and nothing in CI notices.
 *
 * So: one `resolveEntities()`, one order, one digest. `entityDigest()` is what
 * the Phase 19 gate actually asserts — the canvas publishes the digest of what
 * it uploaded to the GPU, the list publishes the digest of what it rendered,
 * and a test compares two strings in every tier. A drift becomes a failing
 * assertion instead of an accessibility audit finding two years later.
 *
 * **Nothing here invents a measure.** A zone's counts arrive as
 * `PublishedFigure`s and stay that way, so the k-anonymity rule ADR-0021
 * established for the public surface cannot be lost on the way into a tooltip
 * or a list row. A pin with no score has `severityScore: null` and renders as
 * plain clay — not as `low`, which would be the frontend deciding something the
 * rubric has not.
 */

import type { SeverityLevel } from "@/design/generated/tokens";
import { levelFor } from "@/lib/severity";
import type { PublishedFigure, PublishedZone } from "@/public/figures";
import type { GeoPoint } from "./projection";

/**
 * A **published place** is scenery with figures on it; a **cluster** is the
 * live thing the event stream creates and merges. Both are pins, and the
 * distinction matters because only one of them moves.
 */
export type ClayEntityKind = "zone" | "cluster";

/**
 * §E27's "per-instance severity, **state** and animation phase".
 *
 * Every member is reachable from an event this stream actually publishes —
 * which is Law 3 applied to an enum. `merging` is `cluster_match_found`,
 * `resolved` is `citizen_confirmed`, `flagged` is `abuse_pattern_flagged`, and
 * `settling` is the six steps after `cluster_created`. There is no `pending` or
 * `stale` here because nothing would ever set them.
 */
export type PinState = "settling" | "resting" | "merging" | "flagged" | "resolved";

export interface ClayEntity {
  readonly id: string;
  readonly kind: ClayEntityKind;
  /** The tenant's own words for this place, already localised upstream. */
  readonly label: string;
  readonly point: GeoPoint;
  /**
   * 0–100, or `null` where nothing has scored this entity.
   *
   * `null` is not a zero and is not "low". A pin with no score is unglazed
   * clay, and the peer list says *not scored* rather than showing a badge.
   */
  readonly severityScore: number | null;
  readonly state: PinState;
  /** How many reports, with suppression already decided (ADR-0021). */
  readonly reports: PublishedFigure;
  /** The step this entity entered the world, for the Settle motion. `null` for
   *  anything that was already there when the scene loaded — a pin that was
   *  already on the map must not re-settle on every navigation. */
  readonly arrivedAtStep: number | null;
  /** Where the peer list's row links to, when there is somewhere to go. */
  readonly href: string | null;
}

/** The glaze this entity fires, or `null` for bare clay. */
export function levelOf(entity: ClayEntity): SeverityLevel | null {
  if (entity.state === "resolved") return "resolved";
  return entity.severityScore === null ? null : levelFor(entity.severityScore);
}

/**
 * Published places, as entities.
 *
 * A place with no centroid is **dropped from both renderers, together**. That
 * is the only correct answer: it cannot be drawn, and a list row for a place
 * the map does not show is precisely the drift this module exists to prevent.
 * The place is still reachable — §E18's ward pages are server-rendered from the
 * same data and do not need a coordinate — so nothing is lost except a pin
 * nobody could have placed.
 */
export function entitiesFromZones(
  zones: readonly PublishedZone[],
  hrefFor?: (zone: PublishedZone) => string,
): readonly ClayEntity[] {
  const out: ClayEntity[] = [];
  for (const zone of zones) {
    // `PublishedZone.centroid` is already the reduced form: `readZone()`
    // collapses a nullable `Centroid` with two nullable members into one null,
    // because three nulls carry one meaning — there is no point to state.
    const centroid = zone.centroid;
    if (centroid === null) continue;
    out.push({
      id: `zone:${zone.zoneCode}`,
      kind: "zone",
      label: zone.zoneName,
      point: { lat: centroid.lat, lng: centroid.lng },
      // §13.1's bands are governed data and no endpoint publishes a per-place
      // severity. Inventing one from open-report counts would be a decoration
      // that looks like a measurement, which §E3.3 forbids by name.
      severityScore: null,
      state: "resting",
      reports: zone.totalReports,
      arrivedAtStep: null,
      href: hrefFor === undefined ? null : hrefFor(zone),
    });
  }
  return order(out);
}

/**
 * The one order, everywhere.
 *
 * Severity descending, then reports descending, then id — so the list reads
 * worst-first the way §E19.1's queue does, and so two entities that tie cannot
 * swap places between the canvas and the DOM because one of them iterated a
 * `Map` differently. The final tie-break on `id` is what makes this a *total*
 * order rather than a mostly-stable one.
 */
export function order(entities: readonly ClayEntity[]): readonly ClayEntity[] {
  return [...entities].sort((a, b) => {
    const severity = (b.severityScore ?? -1) - (a.severityScore ?? -1);
    if (severity !== 0) return severity;
    const reports = countOf(b.reports) - countOf(a.reports);
    if (reports !== 0) return reports;
    return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
  });
}

/** A sort key only. Withheld and unknown both sort last, and neither number
 *  ever reaches a screen from here — `<Figure>` is still the only way out. */
function countOf(figure: PublishedFigure): number {
  return figure.kind === "known" ? figure.value : -1;
}

/**
 * The synchronisation assertion, as one string.
 *
 * Every field that either renderer draws is in it: a pin that moved, changed
 * glaze, changed state or disappeared changes the digest, and a list that
 * disagrees about any of those produces a different one. Deliberately *not* a
 * cryptographic hash — this is a comparison between two things in the same
 * document, so a readable string is worth more than a short one when the
 * assertion fails and somebody has to see which entity diverged.
 */
export function entityDigest(entities: readonly ClayEntity[]): string {
  return entities
    .map((e) =>
      [
        e.id,
        e.state,
        e.severityScore === null ? "-" : e.severityScore.toFixed(1),
        e.point.lat.toFixed(4),
        e.point.lng.toFixed(4),
      ].join(":"),
    )
    .join("|");
}
