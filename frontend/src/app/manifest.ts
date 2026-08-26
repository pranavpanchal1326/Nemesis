import type { MetadataRoute } from "next";

/**
 * The web app manifest — §E21, §E25 Phase 22, F17.
 *
 * > PWA with installability and a service worker.
 *
 * **`start_url` is `/field`, not `/`.** An installed icon is a tool somebody
 * taps while standing in a back lane, and what they need on tap is the camera
 * and the queue. The landing page is a film; installing a film is not what
 * §E21 is asking for.
 *
 * **`display: "standalone"`** removes the browser chrome, which on a phone in
 * sunlight is the difference between a usable capture button and a capture
 * button under an address bar.
 *
 * **The icons are a maskable SVG, generated from the same tokens as everything
 * else.** No PNG set is committed: §6 Principle #6 and the rule ADR-0047 and
 * ADR-0050 both settled — the artefact is the source. A single SVG marked
 * `"any maskable"` is what current Android and iOS both accept.
 *
 * **No `theme_color` from a literal.** It is `--color-role-outdoor-ground`'s own
 * value, imported from the generated tokens, because `check-guards.ts` fails on
 * a hand-written colour anywhere in `src/` and this file is no exception —
 * which is exactly right: the chrome around the field app is the same sheet the
 * field app prints on.
 */
import { PAPER, INK } from "@/design/generated/tokens";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "NEMESIS Field",
    short_name: "NEMESIS",
    description: "Photograph the job, offline. It sends itself when there is signal.",
    start_url: "/field",
    scope: "/",
    display: "standalone",
    orientation: "portrait",
    background_color: PAPER.chalk,
    theme_color: INK["riso-black"],
    icons: [
      {
        src: "/icon.svg",
        sizes: "any",
        type: "image/svg+xml",
        purpose: "any",
      },
      {
        src: "/icon.svg",
        sizes: "any",
        type: "image/svg+xml",
        purpose: "maskable",
      },
    ],
  };
}
