import type { ReactNode } from "react";

import { t, type Strings, type Translated } from "@/lib/i18n/strings";
import "./components.css";

/**
 * `<FlaggedNotice>` — §E26, §16.4, §22.2, ADR-0039.
 *
 * > Fluorescent hatch + **required** `disclaimer` prop + **required**
 * > `responseHref`. **Cannot render without them.**
 *
 * This is the component §E2 defect #14 exists to prevent from being a mockup.
 * §6 Principle #8 requires the appeal path to ship in the same phase as the
 * accountability feature, and the blueprint's answer is not a convention — it
 * is that *"every flagged-content component in this document takes the
 * disclaimer and the response link as required props"*. So they are required
 * here, in the type, and a flag rendered without a way for its subject to
 * answer does not compile.
 *
 * **Never red.** ADR-0039: an unproven anomaly rendered in urgent red is a
 * §22.2 defamation exposure with a design cause. Fluorescent pink on a 45°
 * hatch reads as a *provisional print proof*, which is exactly what it is — and
 * it is a real risograph ink, so it belongs to the system rather than being
 * bolted on.
 *
 * **The words are black.** The hatch carries the colour; the type does not.
 * Measured, `riso-flu-pink` is 2.91:1 on the document stock — unreadable as
 * text — which is another way of saying the pink was never meant to be a
 * typeface.
 */
export interface FlaggedNoticeProps {
  /**
   * The API returns `rating_disclaimer` and `SYSTEM_FLAGGED_NOTICE` as
   * *required* fields. §E18: they *"render as first-class UI, never as tooltips
   * or footnotes"*. Passing one is not optional here because it is not optional
   * there.
   */
  readonly disclaimer: Translated;
  /**
   * Where the flagged party's response lives. §E20: every system-flagged
   * anomaly has a response affordance with a clock, and the response renders on
   * the public profile **alongside the flag**.
   */
  readonly responseHref: string;
  readonly strings: Strings;
  /** The detector's own account of itself, where a surface shows one (§E19.6). */
  readonly detector?: {
    readonly name: Translated;
    readonly threshold: Translated;
    readonly confidence: Translated;
  };
  readonly appeal?: "pending" | "upheld" | "rejected";
  readonly children?: ReactNode;
}

export function FlaggedNotice({
  disclaimer,
  responseHref,
  strings,
  detector,
  appeal,
  children,
}: FlaggedNoticeProps) {
  return (
    <aside className="flagged-notice" data-appeal={appeal}>
      <p className="flagged-notice__label type-micro">{t(strings, "flag.systemFlagged")}</p>

      {children}

      {/*
       * §E19.6: "Every signal card shows the detector name, its threshold, and
       * its confidence. If you are going to flag a named commercial entity, you
       * show your method." Optional as a prop because not every surface shows a
       * signal card — but where one is shown, all three come together or none
       * do, which is why it is one object rather than three loose props.
       */}
      {detector === undefined ? null : (
        <dl className="flagged-notice__detector type-caption">
          <dt>{detector.name}</dt>
          <dd className="type-mono-data">{detector.threshold}</dd>
          <dd className="type-mono-data">{detector.confidence}</dd>
        </dl>
      )}

      <p className="flagged-notice__disclaimer type-caption">{disclaimer}</p>

      <p className="flagged-notice__response">
        <a href={responseHref}>{t(strings, "flag.respond")}</a>
        {appeal === undefined ? null : (
          <span className="flagged-notice__appeal type-micro">
            {t(strings, `flag.appeal${appeal.charAt(0).toUpperCase()}${appeal.slice(1)}`)}
          </span>
        )}
      </p>
    </aside>
  );
}
