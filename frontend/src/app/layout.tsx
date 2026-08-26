import type { Metadata, Viewport } from "next";

import { DENSITY_BOOT_SCRIPT } from "@/lib/density";
import "./globals.css";

/**
 * The document root. §E14.4 — one application, five route groups, sharing the
 * token system, the press and the §E26 contracts, differing in shell, density
 * and auth posture.
 */
export const metadata: Metadata = {
  title: {
    default: "NEMESIS",
    template: "%s · NEMESIS",
  },
  description: "Prove, don't log.",
};

export const viewport: Viewport = {
  // The console is dense and printable; the citizen app is one-thumb. Neither
  // wants a browser deciding scale for it.
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* A10 · §E19: the officer's density, applied before the first paint.
            An effect would run after hydration, which is after the browser has
            painted — so an officer who chose `dense` would watch a `compact`
            console appear and reflow on every navigation. `suppressHydrationWarning`
            above is what lets this script legitimately disagree with the server's
            markup: the server cannot know a preference that lives on the device.

            Inline and synchronous, so it is not a network request the page waits
            on; `dangerouslySetInnerHTML` because that is how React renders script
            text, and the content is a build-time constant with no interpolation
            from anything a request can influence. */}
        <script dangerouslySetInnerHTML={{ __html: DENSITY_BOOT_SCRIPT }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
