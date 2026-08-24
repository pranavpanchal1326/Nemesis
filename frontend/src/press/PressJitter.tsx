"use client";

import { useEffect } from "react";

import { steppedClock } from "@/lib/stepped-clock";
import { jitterAt, type PressPlan } from "./press-model";

/**
 * §E6.1 stage 3 — misregistration, re-jittered at 12 Hz.
 *
 * > This one stage is the majority of "why does this look printed."
 *
 * It writes `dx`/`dy` straight onto the `feOffset` primitives, outside React's
 * render path. That is §E14.2's rule applied to the press: *transient
 * subscriptions that drive uniforms and transforms without a React re-render*.
 * Twelve attribute writes a second on two attributes is nothing; twelve React
 * renders a second of a subtree that contains a photograph is not.
 *
 * The component renders nothing. If it never mounts — JavaScript off, a
 * crawler, Tier D — the sheet stays at its seeded registration, which is a
 * print that is not moving rather than a page that is broken.
 */
export function PressJitter({ filterId, plan }: { filterId: string; plan: PressPlan }) {
  useEffect(() => {
    const nodes = plan.plates.map((_, i) =>
      document.getElementById(`${filterId}-offset-${String(i)}`),
    );

    // A reduced-motion reader gets the press, and gets it still. §E3.2: the
    // reduced-motion path is what an accessibility audit actually sees, which
    // makes it the most consequential edit, not the least.
    const reduced =
      typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced) return;

    return steppedClock.subscribe((step) => {
      const offsets = jitterAt(plan, step);
      offsets.forEach((offset, i) => {
        const node = nodes[i];
        if (!node) return;
        node.setAttribute("dx", String(offset[0]));
        node.setAttribute("dy", String(offset[1]));
      });
    });
  }, [filterId, plan]);

  return null;
}
