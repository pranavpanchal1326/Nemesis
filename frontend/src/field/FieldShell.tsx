"use client";

import Link from "next/link";
import { useCallback, useEffect, useState, useSyncExternalStore } from "react";

import { t, type Strings } from "@/lib/i18n/strings";
import { drainOnReconnect, resume } from "@/lib/offline/queue";
import { queueAvailable } from "@/lib/offline/db";

import { outdoor, OUTDOOR_ATTRIBUTE } from "./outdoor";
import "./field.css";

/**
 * The field app's client boundary — §E21, F17.
 *
 * > Field staff work in basements, back lanes, and dead zones. The people
 * > expected to upload closure evidence have the worst connectivity in the
 * > system.
 *
 * Four things happen here and each is one line of §E21.
 *
 * **The outbox is resumed before anything is drawn.** §E25's Phase 22 gate:
 * *"a killed app mid-upload resumes without duplicating or losing the
 * submission."* Resuming is reading the store and re-marking anything caught
 * mid-flight as retriable (`queue.ts`); the safety of doing so is the
 * idempotency key travelling with the row (ADR-0056).
 *
 * **Reconnect drains.** `online` and `visibilitychange`, because a phone in a
 * pocket does not fire `online` until somebody looks at it — and a field hand
 * walking out of a basement looks at the phone.
 *
 * **Outdoor mode is a ground, not a theme toggle.** It sets
 * `data-ground="outdoor"` and the generated role tokens do the rest, which is
 * why every component below is correct in sunlight without knowing it is in
 * sunlight — the same mechanism §E9.3's light table already uses.
 *
 * **A device with no queue says so, in words, before anybody walks into a
 * basement relying on it.** A private window, a locked-down webview, storage
 * blocked: `openQueue()` answers `null` and this banner is what a person gets
 * instead of a silent failure three hours later (§E3.3).
 */
export function FieldShell({
  strings,
  children,
}: {
  readonly strings: Strings;
  readonly children: React.ReactNode;
}) {
  const [durable, setDurable] = useState<boolean | null>(null);

  const isOutdoor = useSyncExternalStore(
    // Bound rather than passed by reference: `unbound-method` is on, and it is
    // right to be — these read module state rather than `this`, and saying so
    // with an arrow is cheaper than annotating the object.
    (listener) => outdoor.subscribe(listener),
    () => outdoor.enabled(),
    // The server cannot know, and guessing would flash the wrong palette on
    // first paint at exactly the moment somebody is squinting at it.
    () => false,
  );

  useEffect(() => {
    outdoor.hydrate();
    void queueAvailable().then(setDurable);
    void resume();
    return drainOnReconnect();
  }, []);

  useEffect(() => {
    // Registered here rather than in a `<script>`: the shell is the only thing
    // on this surface that is guaranteed to have mounted, and a worker
    // registered from a page that then navigates away is a worker in an
    // unknown state. Failure is silent *to the person* and loud in the console
    // — an unregistered worker costs them offline shell caching, not the
    // queue, which is the part that actually matters (ADR-0056).
    if (!("serviceWorker" in navigator)) return;
    void navigator.serviceWorker.register("/sw.js").catch((error: unknown) => {
      console.warn("service worker did not register", error);
    });
  }, []);

  const toggle = useCallback(() => {
    outdoor.set(!outdoor.enabled());
  }, []);

  return (
    <div
      className="field"
      {...{ [OUTDOOR_ATTRIBUTE]: isOutdoor ? "outdoor" : undefined }}
      data-outdoor={String(isOutdoor)}
    >
      <header className="field__bar">
        {/* The way back to the staff door. The outbox survives the trip: it is
            IndexedDB and a service worker, not component state, which is the
            whole point of ADR-0056 and the reason leaving this screen is safe
            in the first place. */}
        {/* An `<h1>`, not a `<p>`. This surface had three `<h2>`s and no level
            one at all, so a screen reader's heading list started halfway down
            the document — and the word that *is* the page's title was marked up
            as body text sitting above it. */}
        <h1 className="field__wordmark type-micro">
          <Link href="/staff">{t(strings, "field.title")}</Link>
        </h1>
        <button
          type="button"
          className="field__outdoor type-heading"
          aria-pressed={isOutdoor}
          onClick={toggle}
        >
          {t(strings, isOutdoor ? "field.outdoor.off" : "field.outdoor.on")}
        </button>
      </header>

      {durable === false ? (
        <p className="field__warning type-body" role="alert">
          {t(strings, "field.noQueue")}
        </p>
      ) : null}

      {children}
    </div>
  );
}
