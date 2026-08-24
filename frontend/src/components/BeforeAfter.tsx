"use client";

import { useCallback, useId, useRef, useState } from "react";

import { notTranslatable, t, type Strings } from "@/lib/i18n/strings";
import "./components.css";

/**
 * `<BeforeAfter>` — §E26, §E17.5, §E19.4.
 *
 * > Slider + SSIM score + capture metadata. **Identical on all three surfaces,
 * > because a contractor's evidence must look the same to everyone who sees
 * > it.**
 *
 * That sentence is the contract and it is the reason there are no view-specific
 * props: the citizen deciding whether their street is fixed, the officer
 * verifying a closure, and the public reading a contractor's record all see one
 * component with one presentation. A version that flattered on the public
 * profile and told the truth internally would be the exact failure §E19.5's
 * *"what citizens see"* toggle exists to make impossible elsewhere.
 *
 * **The SSIM score prints honestly, including when it is ambiguous.** §E19.4:
 * *"SSIM verification has run — **and its score is printed honestly, including
 * when ambiguous**"*. A score in the band where the comparison cannot decide is
 * labelled as such rather than rounded into a verdict. §E3.3: confidence
 * figures show their runner-up; detectors show their threshold; a number that
 * cannot decide says so.
 *
 * **The UI does not enforce closure.** `WorkOrder.ssim_score`'s own comment
 * says Phase 15's state machine refuses `resolved` without it, *"not the UI"*.
 * This component renders the score. It never gates on it.
 */

/** Below this the images are different; above it they are the same. Between
 *  them, SSIM is not deciding and neither should a label. */
const AMBIGUOUS = { low: 0.45, high: 0.62 } as const;

export interface BeforeAfterProps {
  readonly before: { readonly src: string; readonly capturedAt?: string };
  readonly after: { readonly src: string; readonly capturedAt?: string };
  /** `null` until verification has run — not zero, which would read as a fail. */
  readonly ssim: number | null;
  readonly strings: Strings;
}

export function BeforeAfter({ before, after, ssim, strings }: BeforeAfterProps) {
  const [position, setPosition] = useState(50);
  const frame = useRef<HTMLDivElement>(null);
  const labelId = useId();

  const moveTo = useCallback((clientX: number) => {
    const box = frame.current?.getBoundingClientRect();
    if (box === undefined || box.width === 0) return;
    setPosition(Math.min(100, Math.max(0, ((clientX - box.left) / box.width) * 100)));
  }, []);

  return (
    <figure className="before-after">
      <div
        ref={frame}
        className="before-after__frame"
        style={{ "--before-after-position": `${String(position)}%` } as React.CSSProperties}
        onPointerDown={(event) => {
          event.currentTarget.setPointerCapture(event.pointerId);
          moveTo(event.clientX);
        }}
        onPointerMove={(event) => {
          if (event.buttons !== 1) return;
          moveTo(event.clientX);
        }}
      >
        {/*
         * Evidence, not decoration — so both images carry real alt text, and
         * both are plain `<img>`.
         *
         * `next/image` is refused here deliberately, and the reason is not
         * performance. A closure photograph is the record: §E19.4 makes it what
         * a work order is verified against, §E26 requires this component to look
         * *"the same to everyone who sees it"*, and §E18 puts it on a public
         * contractor profile. An optimiser that re-encodes, resamples or
         * re-compresses it changes the evidence between the officer's screen and
         * the citizen's — quietly, and in a product whose entire proposition is
         * that the record can be trusted.
         */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          className="before-after__image"
          src={before.src}
          alt={t(strings, "beforeAfter.before")}
        />
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          className="before-after__image before-after__image--after"
          src={after.src}
          alt={t(strings, "beforeAfter.after")}
        />

        {/*
         * §E22 requires a full keyboard path, and a drag handle that only
         * responds to a pointer excludes exactly the users an accessibility
         * audit is for. A range input *is* the slider: arrow keys, Home and
         * End, and a real accessible name, all for free and all correct.
         */}
        <input
          className="before-after__handle"
          type="range"
          min={0}
          max={100}
          step={1}
          value={position}
          aria-labelledby={labelId}
          onChange={(event) => {
            setPosition(Number(event.target.value));
          }}
        />
      </div>

      <figcaption className="before-after__caption">
        <span id={labelId} className="type-micro">
          {t(strings, "beforeAfter.handle")}
        </span>

        {ssim === null ? null : (
          <span className="before-after__ssim type-mono-data" data-ambiguous={isAmbiguous(ssim)}>
            {isAmbiguous(ssim)
              ? t(strings, "beforeAfter.ssimAmbiguous", { score: ssim.toFixed(3) })
              : t(strings, "beforeAfter.ssim", { score: ssim.toFixed(3) })}
          </span>
        )}

        {[before, after].map((capture, index) =>
          capture.capturedAt === undefined ? null : (
            <time
              key={index === 0 ? "before" : "after"}
              className="before-after__captured type-micro"
              dateTime={capture.capturedAt}
            >
              {notTranslatable(capture.capturedAt)}
            </time>
          ),
        )}
      </figcaption>
    </figure>
  );
}

export function isAmbiguous(ssim: number): boolean {
  return ssim >= AMBIGUOUS.low && ssim <= AMBIGUOUS.high;
}
