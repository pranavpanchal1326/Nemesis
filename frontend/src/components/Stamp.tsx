"use client";

import { useEffect, useRef, useState } from "react";

import type { Translated } from "@/lib/i18n/strings";
import "./components.css";

/**
 * `<Stamp>` — §E26, §E11.1 motion #1.
 *
 * > **The one confirmation primitive.**
 *
 * > Confirmations land; they do not fade. Scale 1.18 → 1.0 over 168 ms on
 * > `--ease-stamp`, −1.5° rotation, 3 px offset, a one-frame ink spread, and a
 * > soft thud on the foley bus. Used for: complaint accepted, evidence
 * > verified, closure confirmed, policy activated, case approved. **It is the
 * > sound of a decision being made.**
 *
 * §E3.4 governs: *"Colour, motion, and sound each carry exactly one meaning, or
 * none."* `--ease-stamp` is the only overshoot curve in the 2D product and this
 * is its only caller — a second use would make the stamp mean "something
 * happened" instead of "a decision was made", and a vocabulary that means two
 * things means nothing.
 *
 * The kinds are a closed set for the same reason. Adding one is a decision
 * about what counts as a decision.
 */
export type StampKind = "accepted" | "verified" | "confirmed" | "activated" | "approved";

export function Stamp({
  kind,
  label,
  /** Replay on mount. False where the stamp is historical rather than fresh —
   *  a ledger of past decisions should not re-enact all of them on page load. */
  animate = true,
}: {
  readonly kind: StampKind;
  readonly label: Translated;
  readonly animate?: boolean;
}) {
  const [landed, setLanded] = useState(!animate);
  const node = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!animate) return;
    // One frame, so the initial transform is painted before the transition
    // begins. Without it the browser coalesces both states and the stamp fades,
    // which is the one thing §E11.1 says it must never do.
    const frame = requestAnimationFrame(() => {
      setLanded(true);
    });
    return () => {
      cancelAnimationFrame(frame);
    };
  }, [animate]);

  return (
    <span ref={node} className="stamp" data-kind={kind} data-landed={landed}>
      <span className="stamp__label type-doc">{label}</span>
    </span>
  );
}
