import type { Metadata, Viewport } from "next";
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
      <body>{children}</body>
    </html>
  );
}
