"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { t, type Strings } from "@/lib/i18n/strings";
import { CUES } from "@/sound/cues";
import { sound } from "@/sound/graph";

import "./citizen.css";

/**
 * §E17.1 step 1 — **the app opens in the viewfinder, not on a form.**
 *
 * > Full-bleed camera, one shutter, a microphone for voice — many users would
 * > sooner speak than type, more so in Marathi and Hindi, and the transcription
 * > pipeline exists (`media_transcribed`). Then the photo, a four-second undo,
 * > and one optional field: *"What's wrong here?"*
 *
 * Four decisions in here are not obvious and each has a cost if reversed.
 *
 * **The camera is a live stream, not a file picker — and the file picker is
 * still there.** `getUserMedia` needs a secure context and a permission the
 * person may refuse. When it is unavailable the surface falls to
 * `<input type="file" capture="environment">`, which on a phone opens the
 * camera app and returns a photograph. That is §E13's ladder applied to
 * capture: a designed lower tier, not an error state. It is also the *only*
 * path on a desktop browser with no camera, which is where this gets developed.
 *
 * **Voice records audio; it does not transcribe.** The obvious client-side
 * answer is the Web Speech API, and in Chrome that ships the audio to Google's
 * servers — an off-origin dependency §6 Principle #6 bans outright, on the one
 * input a citizen is most likely to use to say something private. So the clip
 * is uploaded and `media_transcribed` does the work, which is the pipeline
 * §8.4 already built and the only one that can honestly claim to handle
 * Marathi.
 *
 * **The undo is four seconds and it is a countdown, not a toast.** §E17.1 fixes
 * the duration. A toast that disappears is a promise the person has to catch;
 * a visible remaining-time bar is a promise they can watch expire.
 *
 * **The optional field is optional in the type system.** `onCapture` hands
 * `description` as `string | undefined`, and §26.1's contract is that a report
 * with a photograph and no words is complete. The flow is asserted to finish
 * with it empty (`tests/citizen.spec.ts`).
 */

export interface Capture {
  readonly photo?: Blob | undefined;
  readonly audio?: Blob | undefined;
  readonly description?: string | undefined;
}

/** §E17.1. The window in which a photograph can be taken back. */
export const UNDO_MS = 4_000;

type Stage =
  /** The live camera, or the fallback picker. */
  | "framing"
  /** A photograph exists and the undo window is open. */
  | "review";

export function Viewfinder({
  strings,
  onCapture,
  busy = false,
}: {
  readonly strings: Strings;
  /** Called when the person commits. `photo` may be absent only if `audio` is
   *  present — §26.1's pair rule, which the caller enforces and the server
   *  enforces again. */
  readonly onCapture: (capture: Capture) => void;
  readonly busy?: boolean;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const stream = useRef<MediaStream | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);

  const [stage, setStage] = useState<Stage>("framing");
  const [cameraLive, setCameraLive] = useState(false);
  /**
   * The photograph and its object URL, together, because they have exactly one
   * lifetime between them.
   *
   * Deriving the URL in an effect looked tidier and was wrong twice: it wrote
   * state synchronously during the effect body (a cascading render), and it put
   * `URL.createObjectURL` — which allocates and must be paired with a revoke —
   * a render away from the event that produced the blob. Creating it where the
   * photograph is created keeps the pair adjacent and makes "revoke the one
   * this replaces" a single line in an event handler rather than a cleanup that
   * has to reason about which render it belongs to.
   */
  const [shot, setShot] = useState<{ readonly blob: Blob; readonly url: string } | null>(null);
  const [audio, setAudio] = useState<Blob | null>(null);
  const [recording, setRecording] = useState(false);
  const [description, setDescription] = useState("");
  const [undoRemaining, setUndoRemaining] = useState(0);

  // --- the live camera ----------------------------------------------------
  useEffect(() => {
    let cancelled = false;

    async function open(): Promise<void> {
      // `in` rather than `=== undefined`: TypeScript's DOM lib declares
      // `mediaDevices` as always present, and on an insecure origin it is not.
      // Trusting the type would ship a `TypeError` to plain-http deployments
      // and to embedded webviews, which is where this is most likely to run.
      if (typeof navigator === "undefined" || !("mediaDevices" in navigator)) return;
      try {
        const media = await navigator.mediaDevices.getUserMedia({
          // The rear camera. A citizen photographing a pothole is not taking a
          // selfie, and a front-facing default is the single most common way a
          // capture flow announces that nobody tried it on a phone.
          video: { facingMode: { ideal: "environment" } },
          audio: false,
        });
        if (cancelled) {
          for (const track of media.getTracks()) track.stop();
          return;
        }
        stream.current = media;
        if (video.current !== null) {
          video.current.srcObject = media;
          await video.current.play().catch(() => undefined);
        }
        setCameraLive(true);
      } catch {
        // Refused, unavailable, or insecure context. The fallback picker is
        // already rendered; nothing to report and nothing to apologise for.
        setCameraLive(false);
      }
    }

    void open();
    return () => {
      cancelled = true;
      const media = stream.current;
      stream.current = null;
      if (media !== null) for (const track of media.getTracks()) track.stop();
    };
  }, []);

  // --- the undo countdown -------------------------------------------------
  useEffect(() => {
    if (stage !== "review" || undoRemaining <= 0) return;
    const timer = setInterval(() => {
      setUndoRemaining((remaining) => Math.max(0, remaining - 100));
    }, 100);
    return () => {
      clearInterval(timer);
    };
  }, [stage, undoRemaining]);

  /*
   * The last revoke: whatever URL is still held when this unmounts.
   *
   * Every *other* revoke happens in the handler that replaces or clears the
   * shot, which is where it belongs. This one cannot: an unmount cleanup that
   * closed over `shot` would revoke whichever value the last render happened to
   * see, and the whole point of `[]` deps is that there is no last render to
   * ask. So a ref tracks the current URL — written in an effect, never during
   * render — and the unmount cleanup reads it.
   */
  const shotRef = useRef<string | null>(null);
  useEffect(() => {
    shotRef.current = shot?.url ?? null;
  }, [shot]);
  useEffect(
    () => () => {
      if (shotRef.current !== null) URL.revokeObjectURL(shotRef.current);
    },
    [],
  );

  const accept = useCallback((blob: Blob) => {
    setShot((previous) => {
      // A blob URL that is never revoked keeps the whole image alive for the
      // tab's lifetime. On a phone taking four photographs of one street, that
      // is the difference between a working app and a reload.
      if (previous !== null) URL.revokeObjectURL(previous.url);
      return { blob, url: URL.createObjectURL(blob) };
    });
    setStage("review");
    setUndoRemaining(UNDO_MS);
  }, []);

  const shutter = useCallback(() => {
    const element = video.current;
    if (element === null || element.videoWidth === 0) return;

    // §E12's shutter, on the frame the photograph is actually taken and
    // nowhere else. A no-op while muted. It fires before the encode rather
    // than in the callback, because a shutter that lands 40 ms after the
    // button reads as lag rather than as a camera.
    sound.play("shutter", CUES.shutter.bus, CUES.shutter.recipe);

    const canvas = document.createElement("canvas");
    canvas.width = element.videoWidth;
    canvas.height = element.videoHeight;
    const context = canvas.getContext("2d");
    if (context === null) return;
    context.drawImage(element, 0, 0, canvas.width, canvas.height);

    // JPEG at 0.85. §E21 will add real client-side compression with EXIF
    // preservation; this is the honest interim — a canvas re-encode *removes*
    // EXIF, which is why §11.1 treats absent metadata as reduced trust rather
    // than as a rejection, and why live-capture-only mode is a tenant switch
    // rather than a default.
    canvas.toBlob(
      (blob) => {
        if (blob !== null) accept(blob);
      },
      "image/jpeg",
      0.85,
    );
  }, [accept]);

  const undo = useCallback(() => {
    setShot((previous) => {
      if (previous !== null) URL.revokeObjectURL(previous.url);
      return null;
    });
    setStage("framing");
    setUndoRemaining(0);
  }, []);

  const toggleRecording = useCallback(async () => {
    if (recording) {
      recorder.current?.stop();
      return;
    }
    if (typeof navigator === "undefined" || !("mediaDevices" in navigator)) return;
    try {
      const media = await navigator.mediaDevices.getUserMedia({ audio: true });
      const chunks: Blob[] = [];
      const instance = new MediaRecorder(media);
      instance.ondataavailable = (event) => {
        if (event.data.size > 0) chunks.push(event.data);
      };
      instance.onstop = () => {
        for (const track of media.getTracks()) track.stop();
        setAudio(new Blob(chunks, { type: instance.mimeType }));
        setRecording(false);
      };
      recorder.current = instance;
      instance.start();
      setRecording(true);
    } catch {
      setRecording(false);
    }
  }, [recording]);

  const commit = useCallback(() => {
    onCapture({
      photo: shot?.blob,
      audio: audio ?? undefined,
      // Empty means absent. §26.1 distinguishes them and only one is worth
      // storing; a description somebody typed and deleted is not a description.
      description: description.trim() === "" ? undefined : description.trim(),
    });
  }, [audio, description, onCapture, shot]);

  const undoOpen = stage === "review" && undoRemaining > 0;

  return (
    <section className="viewfinder" data-stage={stage} aria-live="polite">
      <div className="viewfinder__frame">
        {stage === "framing" ? (
          <>
            <video
              ref={video}
              className="viewfinder__video"
              playsInline
              muted
              // The stream is the person's own camera output, not content. A
              // screen-reader user is not served by being told a video is here;
              // they are served by the shutter button's label and by the
              // fallback picker, which is a first-class control rather than a
              // secondary one.
              aria-hidden="true"
            />
            {cameraLive ? null : (
              <p className="viewfinder__fallback-note type-caption">
                {t(strings, "capture.cameraUnavailable")}
              </p>
            )}
          </>
        ) : (
          /*
           * A plain `<img>`, and it has to be. The source is an object URL for a
           * blob the person's own camera produced a moment ago; `next/image`
           * optimises remote and bundled assets through a loader, cannot accept
           * an object URL, and would have nothing to optimise if it could. The
           * LCP concern the rule exists for does not apply either — these bytes
           * never crossed a network.
           */
          // eslint-disable-next-line @next/next/no-img-element
          <img className="viewfinder__still" src={shot?.url} alt={t(strings, "capture.stillAlt")} />
        )}
      </div>

      <div className="viewfinder__controls">
        {stage === "framing" ? (
          <>
            <button
              type="button"
              className="viewfinder__shutter"
              onClick={shutter}
              disabled={!cameraLive}
            >
              <span className="viewfinder__shutter-ring" aria-hidden="true" />
              <span className="sr-only">{t(strings, "capture.shutter")}</span>
            </button>

            <label className="viewfinder__pick type-micro">
              {t(strings, "capture.choosePhoto")}
              <input
                type="file"
                accept="image/*"
                // On a phone this opens the camera rather than the gallery,
                // which is why the fallback is a capture path and not a
                // consolation prize.
                capture="environment"
                className="sr-only"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file !== undefined) accept(file);
                }}
              />
            </label>

            <button
              type="button"
              className="viewfinder__voice type-micro"
              data-recording={recording}
              onClick={() => void toggleRecording()}
            >
              {t(strings, recording ? "capture.voiceStop" : "capture.voiceStart")}
            </button>
          </>
        ) : (
          <>
            {undoOpen ? (
              <button type="button" className="viewfinder__undo type-micro" onClick={undo}>
                {t(strings, "capture.undo", {
                  seconds: Math.ceil(undoRemaining / 1000),
                })}
                <span
                  className="viewfinder__undo-bar"
                  style={{ "--remaining": undoRemaining / UNDO_MS } as React.CSSProperties}
                  aria-hidden="true"
                />
              </button>
            ) : (
              <button type="button" className="viewfinder__retake type-micro" onClick={undo}>
                {t(strings, "capture.retake")}
              </button>
            )}

            <label className="viewfinder__field">
              <span className="viewfinder__field-label type-micro">
                {t(strings, "capture.describe")}
              </span>
              <textarea
                className="viewfinder__field-input type-body"
                value={description}
                rows={2}
                maxLength={2000}
                onChange={(event) => {
                  setDescription(event.target.value);
                }}
                placeholder={t(strings, "capture.describePlaceholder")}
              />
              {/* §26.1, stated rather than implied: the flow completes empty. */}
              <span className="viewfinder__field-optional type-caption">
                {t(strings, "capture.describeOptional")}
              </span>
            </label>

            {audio === null ? (
              <button
                type="button"
                className="viewfinder__voice type-micro"
                data-recording={recording}
                onClick={() => void toggleRecording()}
              >
                {t(strings, recording ? "capture.voiceStop" : "capture.voiceStart")}
              </button>
            ) : (
              <p className="viewfinder__voice-done type-caption">
                {t(strings, "capture.voiceAttached")}
              </p>
            )}

            <button
              type="button"
              className="viewfinder__send type-heading"
              onClick={commit}
              disabled={busy}
            >
              {t(strings, "capture.send")}
            </button>
          </>
        )}
      </div>
    </section>
  );
}
