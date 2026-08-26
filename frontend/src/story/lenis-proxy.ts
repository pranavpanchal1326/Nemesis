"use client";

import Lenis from "lenis";

import { SPINE, type ScrollProxy } from "./spine";

/**
 * Lenis, and the only module in the application that names it — §E16, M9.1.
 *
 * The spine takes a `ScrollProxy`, not a `Lenis`, and this is the production
 * implementation of that interface. The indirection buys three things that are
 * each worth more than the ten lines it costs:
 *
 * · **The spine is unit-testable.** `new Lenis()` reads `window` and installs
 *   listeners in its constructor. A `StorySpine` that constructed one could
 *   only ever be asserted in a browser, and F12's *"stopping the scroll stops
 *   the walk"* gate is a property of the damping, not of a wheel event.
 *
 * · **The proof route needs no scrolling at all.** `?t=0.74` seeks the spine
 *   directly, with no proxy attached, which is what makes a golden image at a
 *   fixed `t` reproducible rather than a screenshot of wherever the runner's
 *   smooth-scroll had got to.
 *
 * · **`prefers-reduced-motion` never constructs one.** Tier C is nine riso
 *   prints with the browser's own scrolling (§E13), and hijacking the scroll of
 *   somebody who has asked their operating system for less motion is precisely
 *   the override that ladder exists to refuse.
 *
 * **`autoRaf` is off on purpose.** Lenis will happily run its own
 * `requestAnimationFrame` loop, and then the page has two: Lenis' and the
 * spine's. Two loops means the spine can read a scroll position from the frame
 * before, which is a one-frame lag that shows up as the camera trailing the
 * type. The spine pumps this one through `advance()`.
 */
export function bindLenis(reel: HTMLElement): ScrollProxy {
  const lenis = new Lenis({
    lerp: SPINE.lerp,
    // The film is the whole document; §E16 gives each act its own snap point
    // and the CSS does that with `scroll-snap-type`, which Lenis respects
    // because it animates the real scroll position rather than a transform.
    autoRaf: false,
    // Anchor jumps inside the film — the skip link, the receipts — must land,
    // not glide for two seconds. Lenis' own default is to animate them.
    anchors: false,
  });

  return {
    /**
     * Progress across **the reel**, not across the document.
     *
     * `lenis.progress` measures the whole page, and the whole page is the film
     * *plus* Act 9's receipts below the fold. Using it would mean `t` reached
     * about 0.8 at the end of Act 8 and the last two shots never played —
     * which is the kind of bug that looks like a camera problem for an hour
     * before anybody measures the page. §E16 puts the receipts below the film
     * on purpose, so the film measures itself.
     */
    progress: () => {
      const travel = reel.offsetHeight - window.innerHeight;
      // A reel shorter than the viewport has no scroll to report. Before
      // layout has settled — fonts, the canvas sizing itself — this is
      // legitimately zero rather than an error, and reporting 0 holds the film
      // at the cold open instead of dividing by it.
      if (!(travel > 0)) return 0;
      const value = (lenis.scroll - reel.offsetTop) / travel;
      return Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : 0;
    },
    advance: (nowMs) => {
      lenis.raf(nowMs);
    },
    destroy: () => {
      lenis.destroy();
    },
  };
}
