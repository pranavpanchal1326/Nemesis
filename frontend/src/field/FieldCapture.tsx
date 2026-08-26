"use client";

import { useCallback, useRef, useState } from "react";

import { InkFigure } from "@/ink/InkFigure";
import type { ComplaintDraft } from "@/lib/api/complaints";
import { newIdempotencyKey } from "@/lib/api/idempotency";
import { deviceFingerprint } from "@/lib/device";
import { t, type Strings } from "@/lib/i18n/strings";
import { compressPhoto, type CompressionResult } from "@/lib/media/compress";
import { drain, enqueue } from "@/lib/offline/queue";
import { CUES } from "@/sound/cues";
import { sound } from "@/sound/graph";

/**
 * The camera button — §E21, F17.
 *
 * > Three jobs on a list, **a large camera button**, offline queue with visible
 * > per-item state. … Camera-first capture with client-side compression and
 * > EXIF preservation.
 *
 * **Everything a field hand does goes into the outbox first**, online or not
 * (ADR-0056). There is no "try the network, fall back to the queue" branch,
 * because a durability path that only runs when the network is down is a path
 * nobody exercises. Capture writes the row; `drain()` sends it; a dead zone
 * simply means the drain finds nothing to do yet.
 *
 * **The photograph is compressed before it is stored, not before it is sent.**
 * A 3 MB capture in IndexedDB is 3 MB the device is carrying around until the
 * upload succeeds, and on a phone in a basement that could be a day. Compressing
 * at capture also means the person is standing there when it happens, so the
 * one honest thing that can go wrong — the metadata not surviving (ADR-0057) —
 * can be *told to them* while they can still retake it.
 *
 * **`<input capture="environment">` rather than `getUserMedia`.** §E17.1's
 * viewfinder opens a live stream because a citizen's report begins with the
 * camera; a field hand is working a job and taking a photograph of it, and the
 * platform camera app is faster, handles focus and flash better in a back lane,
 * and is what their thumb already knows. It is also the path that works when the
 * page is served from a service worker cache with no live permissions prompt.
 */
export function FieldCapture({
  strings,
  coordinates,
  locale,
}: {
  readonly strings: Strings;
  /** Where the job is. Passed in rather than read here — the list knows. */
  readonly coordinates: { readonly latitude: number; readonly longitude: number } | null;
  readonly locale: string;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [last, setLast] = useState<CompressionResult | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const onFile = useCallback(
    async (file: File | undefined) => {
      if (file === undefined || coordinates === null) return;
      setBusy(true);
      setProblem(null);
      try {
        // §E12's shutter is bound to the moment a photograph is taken. On this
        // surface the platform camera already made the noise; what this marks
        // is the capture entering *this* system, which is the same meaning the
        // citizen viewfinder gives it.
        sound.play("shutter", CUES.shutter.bus, CUES.shutter.recipe);

        const compressed = await compressPhoto(file);
        setLast(compressed);

        const draft: ComplaintDraft = {
          // Minted with the draft, never with the request — the property the
          // whole offline story rests on (`lib/api/idempotency.ts`).
          idempotencyKey: newIdempotencyKey(),
          latitude: coordinates.latitude,
          longitude: coordinates.longitude,
          deviceFingerprint: deviceFingerprint(),
          photo: compressed.blob,
          locale,
        };

        const queued = await enqueue(draft);
        if (!queued) {
          setProblem(t(strings, "field.noQueue"));
          return;
        }
        // Fire and forget: the drain reports progress through the queue's own
        // rows, which is where §E21 asks for it to be visible.
        void drain();
      } finally {
        setBusy(false);
        if (input.current !== null) input.current.value = "";
      }
    },
    [coordinates, locale, strings],
  );

  return (
    <section className="capture" aria-labelledby="field-capture">
      <h2 id="field-capture" className="type-title">
        {t(strings, "field.capture.title")}
      </h2>

      <div className="capture__row">
        {/* §E8.2's Field Hand — *"practical, in motion"* — on the surface that
            table assigns them: "mobile capture, offline states". Not `live`:
            this figure stands for the person holding the phone, and there is no
            event about them. */}
        <InkFigure strings={strings} figure="field-hand" className="ink--inline" fill={0.92} />

        <label className="capture__button type-heading">
          <input
            ref={input}
            type="file"
            accept="image/*"
            capture="environment"
            className="capture__input"
            disabled={busy || coordinates === null}
            onChange={(event) => {
              void onFile(event.target.files?.[0]);
            }}
          />
          {t(strings, busy ? "field.capture.working" : "field.capture.button")}
        </label>
      </div>

      {coordinates === null ? (
        <p className="type-caption">{t(strings, "field.capture.noPlace")}</p>
      ) : null}

      {problem === null ? null : (
        <p className="field__warning type-body" role="alert">
          {problem}
        </p>
      )}

      {last === null ? null : (
        <dl className="capture__result type-caption">
          <div>
            <dt>{t(strings, "field.capture.size")}</dt>
            <dd className="type-mono-data">
              {kb(last.originalBytes)} → {kb(last.bytes)}
            </dd>
          </div>
          <div>
            {/*
              §11.1 treats absent metadata as reduced trust rather than as a
              rejection, so this line is not a warning — it is the person being
              told what the city will see, while they are still standing where
              they could take the photograph again (ADR-0057).
            */}
            <dt>{t(strings, "field.capture.metadata")}</dt>
            <dd>
              {t(
                strings,
                last.metadataPreserved ? "field.capture.exifKept" : "field.capture.exifGone",
              )}
            </dd>
          </div>
        </dl>
      )}
    </section>
  );
}

function kb(bytes: number): string {
  return `${String(Math.max(1, Math.round(bytes / 1024)))} kB`;
}
