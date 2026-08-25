"use client";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

import { describeCoordinates, describePlace, resolvePlace } from "@/lib/api/places";
import type { PlaceResolution } from "@/lib/api/places";
import { notTranslatable, t, type Strings } from "@/lib/i18n/strings";

import "./citizen.css";

/**
 * §E17.1 step 2 — **Place**, stated as a card rather than asked as a question.
 *
 * > Auto-located and presented as a *card*, not a picker: **"Paud Road, near
 * > Karve Statue · Kothrud · Ward 14"**, with `adjust` opening a map and a 60 px
 * > draggable pin. Never ask someone standing in traffic beside a pothole to
 * > pinch-zoom a map. Reverse-geocode, state the guess, allow correction.
 *
 * **What this does not have, and says so.** The blueprint's example names a
 * road and a landmark. Producing that needs a street graph, and this product
 * has none and may not fetch one — §6 Principle #6 bans the off-origin request,
 * and a civic product sending every citizen's exact position to a third-party
 * geocoder is a worse idea than the missing line. So the card states the place
 * tree it genuinely resolves (`GET /places/resolve`, ADR-0043's sibling) and
 * the coordinates, and renders the street line **not at all** rather than
 * guessing at one — §E3.3, the omission is visible instead of faked. Recorded
 * as a defect against §E17.1 rather than absorbed silently.
 *
 * **`adjust` is a nudge pad, not a map.** §E17.1 asks for a map with a 60 px
 * pin; M8 owns MapLibre and it is not installed. What ships here is the *other*
 * half of the same requirement — a large-target, thumb-sized correction control
 * that never asks anybody to pinch-zoom — with the map behind it arriving at
 * M8. The pin target is 60 px because that is the number the blueprint gives
 * and because it is the number a gloved hand in §E21's outdoor mode needs.
 */

/** §E17.1. The blueprint's number, and §E22's minimum target size with room. */
export const PIN_TARGET_PX = 60;

/** One nudge, in degrees. ~11 m of latitude — the scale of *"that pothole, not
 *  the next one"*, which is the correction a person standing there is making. */
const NUDGE_DEGREES = 0.0001;

export interface Coordinates {
  readonly latitude: number;
  readonly longitude: number;
}

/**
 * Three states, and only one of them is stored.
 *
 * `located` is a function of `value`, and `locating` is a function of `value`
 * being absent with the fix not yet refused — so the only thing this component
 * genuinely has to *remember* is whether the browser said no. Deriving the rest
 * is not a style preference: an effect that mirrored `value` into state would
 * re-render on every nudge and, for one frame after each, disagree with the
 * prop it was mirroring.
 */
type LocationState =
  | "locating"
  | "located"
  /** Refused, unavailable, or timed out. Not an error state — a person can
   *  still nudge the pin to where the problem is, and saying so is better than
   *  a blocked flow. */
  | "unavailable";

export function PlaceCard({
  strings,
  value,
  onChange,
}: {
  readonly strings: Strings;
  readonly value: Coordinates | null;
  readonly onChange: (at: Coordinates) => void;
}) {
  const [fixRefused, setFixRefused] = useState(false);
  const [place, setPlace] = useState<PlaceResolution | null>(null);
  const [adjusting, setAdjusting] = useState(false);
  const resolveAbort = useRef<AbortController | null>(null);

  const canLocate = useSyncExternalStore(subscribeNever, hasGeolocation, () => true);

  const state: LocationState =
    value !== null ? "located" : fixRefused || !canLocate ? "unavailable" : "locating";

  // --- the fix ------------------------------------------------------------
  useEffect(() => {
    if (value !== null || !canLocate) return;

    let cancelled = false;
    navigator.geolocation.getCurrentPosition(
      (position) => {
        if (cancelled) return;
        onChange({
          latitude: position.coords.latitude,
          longitude: position.coords.longitude,
        });
      },
      () => {
        if (!cancelled) setFixRefused(true);
      },
      // High accuracy, because the difference between two potholes on one
      // street is inside a coarse fix. Ten seconds, because a person standing
      // in traffic will not wait thirty — and the nudge pad exists precisely so
      // a poor fix is correctable rather than fatal.
      { enableHighAccuracy: true, timeout: 10_000, maximumAge: 30_000 },
    );

    return () => {
      cancelled = true;
    };
  }, [canLocate, onChange, value]);

  // --- the name -----------------------------------------------------------
  useEffect(() => {
    if (value === null) return;
    resolveAbort.current?.abort();
    const controller = new AbortController();
    resolveAbort.current = controller;

    void resolvePlace(value.latitude, value.longitude, controller.signal)
      .then((resolution) => {
        if (!controller.signal.aborted) setPlace(resolution);
      })
      .catch(() => {
        // A named place is a courtesy; the coordinates are the report. Failing
        // to name it must never block a submission.
        if (!controller.signal.aborted) setPlace(null);
      });

    return () => {
      controller.abort();
    };
  }, [value]);

  const nudge = useCallback(
    (deltaLat: number, deltaLng: number) => {
      if (value === null) return;
      onChange({
        latitude: clamp(value.latitude + deltaLat, -90, 90),
        longitude: clamp(value.longitude + deltaLng, -180, 180),
      });
    },
    [onChange, value],
  );

  const named = place === null ? null : describePlace(place);

  return (
    <section className="place-card" aria-live="polite">
      <h2 className="place-card__title type-micro">{t(strings, "place.title")}</h2>

      {state === "locating" ? (
        <p className="place-card__locating type-body">{t(strings, "place.locating")}</p>
      ) : null}

      {named === null ? null : <p className="place-card__name type-heading">{named}</p>}

      {value === null ? null : (
        <p className="place-card__coordinates type-mono-data">
          {notTranslatable(describeCoordinates(value.latitude, value.longitude))}
        </p>
      )}

      {/*
       * The three honest empties, and they are three different sentences.
       * Collapsing them into one "location unknown" would be the §E3.3 failure
       * this component is otherwise built to avoid.
       */}
      {state === "unavailable" ? (
        <p className="place-card__note type-caption">{t(strings, "place.noFix")}</p>
      ) : null}
      {place !== null && place.boundaries_configured === false ? (
        <p className="place-card__note type-caption">{t(strings, "place.noBoundaries")}</p>
      ) : null}
      {place !== null &&
      place.boundaries_configured === true &&
      (place.units ?? []).length === 0 ? (
        <p className="place-card__note type-caption">{t(strings, "place.outsideCity")}</p>
      ) : null}

      {value === null ? null : (
        <>
          <button
            type="button"
            className="place-card__adjust type-micro"
            aria-expanded={adjusting}
            onClick={() => {
              setAdjusting((open) => !open);
            }}
          >
            {t(strings, adjusting ? "place.adjustDone" : "place.adjust")}
          </button>

          {adjusting ? (
            <div
              className="place-card__pad"
              role="group"
              aria-label={t(strings, "place.adjustGroup")}
            >
              <button
                type="button"
                className="place-card__nudge"
                data-direction="north"
                onClick={() => {
                  nudge(NUDGE_DEGREES, 0);
                }}
              >
                <span className="sr-only">{t(strings, "place.north")}</span>
              </button>
              <button
                type="button"
                className="place-card__nudge"
                data-direction="west"
                onClick={() => {
                  nudge(0, -NUDGE_DEGREES);
                }}
              >
                <span className="sr-only">{t(strings, "place.west")}</span>
              </button>
              <span className="place-card__pin" aria-hidden="true" />
              <button
                type="button"
                className="place-card__nudge"
                data-direction="east"
                onClick={() => {
                  nudge(0, NUDGE_DEGREES);
                }}
              >
                <span className="sr-only">{t(strings, "place.east")}</span>
              </button>
              <button
                type="button"
                className="place-card__nudge"
                data-direction="south"
                onClick={() => {
                  nudge(-NUDGE_DEGREES, 0);
                }}
              >
                <span className="sr-only">{t(strings, "place.south")}</span>
              </button>
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}

function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}

/**
 * Whether this browser can locate at all — read as external state, not as a
 * one-off check in an effect.
 *
 * **`in` rather than `!== undefined`.** TypeScript's DOM lib declares
 * `navigator.geolocation` as always present, and on an insecure origin it
 * genuinely is not. Trusting the type here would ship a `TypeError` to exactly
 * the deployments where a citizen is most likely to be — plain http, an
 * embedded webview inside a municipal app.
 *
 * **`useSyncExternalStore` rather than an effect.** The answer is a property of
 * the environment, so it wants to be read during render — but `navigator` does
 * not exist on the server, and a lazy `useState` initialiser would return one
 * answer there and another after hydration, which is a mismatch on the sentence
 * a person reads first. The server snapshot claims `true` (the common case) and
 * the client corrects it on hydration, which is what this hook exists for.
 */
function hasGeolocation(): boolean {
  return typeof navigator !== "undefined" && "geolocation" in navigator;
}

/** It cannot change within a session, so there is nothing to subscribe to.
 *  Returning a no-op unsubscribe is the honest expression of that. */
function subscribeNever(): () => void {
  return () => {
    // Intentionally empty — see `hasGeolocation`.
  };
}
