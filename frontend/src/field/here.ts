"use client";

import { useEffect, useState } from "react";

/**
 * Where the phone is — §E21, F17.
 *
 * §26.1 makes `latitude` and `longitude` required on a submission, and on the
 * field surface there is no place card to type into: the person is *standing*
 * at the thing they are photographing, which is the one situation where the
 * device's own answer is better than anything they could enter.
 *
 * **`watchPosition`, not `getCurrentPosition`.** A field hand walks between
 * jobs, and a coordinate read once when the app opened is the coordinate of the
 * previous street. The watch is cheap while the screen is on and is torn down
 * when this unmounts.
 *
 * **A refused or unavailable position is `null`, and the capture button
 * disables itself and says why.** Not a silent zero and not a guess: a
 * photograph filed at 0°N 0°E is worse than no photograph, because it is
 * evidence pointing at the wrong place and the pipeline has no way to know.
 */
export interface Here {
  readonly latitude: number;
  readonly longitude: number;
  /** Metres of uncertainty the device reports. Shown, not hidden (§E3.3). */
  readonly accuracyMetres: number;
}

export function useHere(): Here | null {
  const [here, setHere] = useState<Here | null>(null);

  useEffect(() => {
    if (typeof navigator === "undefined" || !("geolocation" in navigator)) return;

    const watch = navigator.geolocation.watchPosition(
      (position) => {
        setHere({
          latitude: position.coords.latitude,
          longitude: position.coords.longitude,
          accuracyMetres: position.coords.accuracy,
        });
      },
      () => {
        // Refused, unavailable, or timed out. All three mean the same thing to
        // this surface — we do not know where we are — and the difference
        // between them is not something a person in a back lane can act on.
        setHere(null);
      },
      {
        enableHighAccuracy: true,
        // A stale fix is worse than a slow one here: the whole point is that
        // the coordinate belongs to the thing in the photograph.
        maximumAge: 15_000,
        timeout: 20_000,
      },
    );

    return () => {
      navigator.geolocation.clearWatch(watch);
    };
  }, []);

  return here;
}
