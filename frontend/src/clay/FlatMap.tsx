"use client";

import { useEffect, useRef } from "react";

import { CLAY, GLAZE_HEX, PAPER, WORLD } from "@/design/generated/tokens";
import type { PaperName, SeverityLevel } from "@/design/generated/tokens";

import { levelOf, type ClayEntity } from "./entities";
import { pinHeight } from "./pins";
import type { GeoPoint } from "./projection";
import "./clay.css";

/**
 * The 2D and heavy-layer path — M8.11, §E15, correcting §E2 defect #5.
 *
 * §E2's fifth defect is Leaflet: *"correct instinct, wrong conclusion in 2026 …
 * Leaflet's DOM markers cannot carry the pin count the dedup engine produces"*.
 * MapLibre GL is equally keyless and self-hostable, is vector, and shares the
 * GPU story with the clay. deck.gl draws on top of it, instanced.
 *
 * **When this renders instead of the clay.** Tier C has two triggers and they
 * are not the same situation:
 *
 *   · **`prefers-reduced-motion`** — a *consent* boundary. The device is
 *     perfectly capable; the person has asked for stillness. A still 2D map is
 *     the honest answer, and refusing to draw a map at all would be treating a
 *     preference as an incapacity.
 *   · **No WebGL** — a *capability* boundary. MapLibre is WebGL too, so there
 *     is nothing to fall back to here and the peer list stands alone. That is
 *     not a gap: the list is a peer and carries every place and every figure.
 *
 * §E13 requires the fallback to be *"a printed photograph of the model, not a
 * second aesthetic"*, so nothing here introduces a colour, a shape or a scale
 * of its own: the glazes are `GLAZE_HEX`, the pin heights are `pinHeight()` —
 * the same function the instanced pins use — and the ground is the surface's
 * own stock.
 *
 * **There is no basemap, and that is a decision rather than an omission.**
 * Raster or vector tiles would be either a CDN dependency (banned by
 * `check-guards.ts`, and fatal to Phase 29's air-gapped boot) or gigabytes of
 * self-hosted tiles that no tenant has produced. So the style is a single
 * background layer in the surface's stock, and what is drawn on it is only what
 * this system actually knows: where its published places are and what state
 * they are in.
 *
 * **And there are no ward boundaries yet — stated, not faked.** ADR-0047 argues
 * that boundaries belong on this path, drawn as boundaries. They cannot be:
 * `ZoneSpec.boundary` exists on the *write* side of the control plane and no
 * read endpoint publishes it, so the frontend has nothing to draw. Extruding
 * something else into their place would be exactly the failure ADR-0047 refuses
 * two paragraphs earlier. The gap is recorded in `docs/FRONTEND-PHASE-PLAN.md`
 * against the backend, which is where the fix is.
 */
export function FlatMap({
  entities,
  origin,
  stock,
  className,
}: {
  readonly entities: readonly ClayEntity[];
  readonly origin: GeoPoint;
  /** The surface's own paper (§E9.2), so the 2D map prints on the same sheet
   *  the rest of the screen does. */
  readonly stock: PaperName;
  readonly className?: string;
}) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const entitiesRef = useRef(entities);

  useEffect(() => {
    entitiesRef.current = entities;
  }, [entities]);

  useEffect(() => {
    const host = hostRef.current;
    if (host === null) return;

    const building = new AbortController();
    const abandoned = (): boolean => building.signal.aborted;
    let teardown: (() => void) | null = null;

    void (async () => {
      // Both are lazy for the same reason `scene.ts` is: a tier that never
      // draws a map should not download a map engine.
      const [{ Map: MapLibreMap }, deck] = await Promise.all([
        import("maplibre-gl"),
        import("deck.gl"),
      ]);
      if (abandoned()) return;

      const map = new MapLibreMap({
        container: host,
        style: {
          version: 8,
          sources: {},
          layers: [
            { id: "stock", type: "background", paint: { "background-color": PAPER[stock] } },
          ],
        },
        center: [origin.lng, origin.lat],
        zoom: INITIAL_ZOOM,
        // Reduced motion is the reason this component exists on that tier, so
        // nothing here may glide, rotate or ease. MapLibre's defaults do all
        // three.
        dragRotate: false,
        pitchWithRotate: false,
        touchZoomRotate: false,
        attributionControl: false,
        fadeDuration: 0,
      });

      const overlay = new deck.Deck({
        parent: host,
        views: new deck.MapView({ repeat: false }),
        controller: false,
        style: OVERLAY_STYLE,
        initialViewState: {
          longitude: origin.lng,
          latitude: origin.lat,
          zoom: INITIAL_ZOOM,
        },
        layers: [],
      });

      const draw = (): void => {
        const centre = map.getCenter();
        overlay.setProps({
          viewState: {
            longitude: centre.lng,
            latitude: centre.lat,
            zoom: map.getZoom(),
            bearing: map.getBearing(),
            pitch: map.getPitch(),
          },
          layers: [
            // One instanced layer for every pin in the city — the same claim
            // the 3D path makes, on the same budget, which is why this is
            // deck.gl and not markers.
            new deck.ScatterplotLayer<ClayEntity>({
              id: "pins",
              data: [...entitiesRef.current],
              pickable: false,
              radiusUnits: "meters",
              radiusMinPixels: 3,
              getPosition: (entity) => [entity.point.lng, entity.point.lat],
              getFillColor: (entity) => glazeBytes(levelOf(entity)),
              // **Severity is size here as well as ink**, and that is §E9.4
              // rule 2 rather than a flourish: a flat map has no third
              // dimension to spend a pin's height in, and severity carried by
              // colour alone is the one thing this product's design law forbids
              // outright. `pinHeight()` is the same function the instanced pins
              // scale by, so a critical pin is the tallest object in 3D and the
              // largest disc in 2D for the same reason and by the same number.
              getRadius: (entity) =>
                (pinHeight(entity.severityScore) / WORLD.pin.maxHeightMetres) *
                WORLD.pin.radiusMetres *
                FLAT_RADIUS_GAIN,
            }),
          ],
        });
      };

      // MapLibre gives its canvas `tabindex="0"`, `role="region"` and
      // `aria-label="Map"` — correct for a map that is its own content, and
      // wrong for this one. The peer list is the accessible representation
      // here, and a focusable element inside an `aria-hidden` host is a real
      // WCAG 4.1.2 failure (`axe` catches it as `aria-hidden-focus`), not a
      // technicality: it is a stop on the tab path that announces nothing.
      const canvas = map.getCanvas();
      canvas.setAttribute("tabindex", "-1");
      canvas.removeAttribute("role");
      canvas.removeAttribute("aria-label");

      map.on("move", draw);
      map.on("load", draw);
      draw();

      if (abandoned()) {
        overlay.finalize();
        map.remove();
        return;
      }

      teardown = () => {
        map.off("move", draw);
        overlay.finalize();
        map.remove();
      };
    })();

    return () => {
      building.abort();
      teardown?.();
    };
  }, [origin.lat, origin.lng, stock]);

  // `aria-hidden` for the same reason the clay canvas carries it: the peer list
  // beside this is the accessible representation, and it is always present.
  return <div ref={hostRef} className={className ?? "clay__flat"} aria-hidden="true" />;
}

/** Close enough that a ward fills the frame, far enough that a whole city
 *  fits — the 2D equivalent of `WORLD.camera`'s long lens. */
const INITIAL_ZOOM = 12.5;

/** A pin seen from directly overhead has lost its height, so the disc is drawn
 *  larger than the pin's own footprint to carry the same read. */
const FLAT_RADIUS_GAIN = 4;

const OVERLAY_STYLE = {
  position: "absolute",
  inset: "0",
  pointerEvents: "none",
} as const;

/**
 * A glaze, as deck.gl wants it: four bytes.
 *
 * Named `glazeBytes` rather than the obvious three letters because
 * `check-guards.ts` bans a CSS colour function anywhere in `src/`, and a helper
 * whose name *is* that function would need an exemption comment on two lines to
 * say it is not one. A rule with an exemption on a correct line teaches the next
 * reader that the rule is approximate.
 *
 * `GLAZE_HEX` is generated from the same `tokens.json` line as the badge's ink
 * and the shader's uniform (§E24). Unpacking it is arithmetic on a token, not a
 * second colour — which is the distinction `check-guards.ts` is drawing when it
 * bans a literal.
 */
function glazeBytes(level: SeverityLevel | null): [number, number, number, number] {
  if (level === null) {
    // Unglazed — the clay body itself, at the alpha an unscored pin gets in 3D:
    // present, placed, and making no claim about a severity nobody has scored.
    return [...unpack(CLAY_BODY), UNSCORED_ALPHA];
  }
  return [...unpack(GLAZE_HEX[level]), OPAQUE];
}

function unpack(value: number): [number, number, number] {
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255];
}

const OPAQUE = 255;
const UNSCORED_ALPHA = 150;

/** The clay body, as a number. `CLAY.body` is the riso-brown ink §E9.2 already
 *  assigned to clay, generated from `tokens.json`; parsing it is arithmetic on
 *  a token rather than a second authoring of the same colour. */
const CLAY_BODY = Number.parseInt(CLAY.body.slice(1), 16);
