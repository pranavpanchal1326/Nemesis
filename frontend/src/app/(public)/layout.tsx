import type { Metadata } from "next";

/**
 * §E18 — the public transparency surface. Server-rendered, indexable,
 * deep-linkable, suppression-aware. §16.2 wants these bookmarkable, which is
 * one of the reasons the BFF seam exists at all (§E14.1).
 *
 * **`<main>` is not here.** Each page renders `<PublicShell>`, which owns the
 * landmark, because the shell also owns the header and footer that must sit
 * outside it. Two nested `<main>` elements is an `axe` violation, and it was
 * the first thing the sweep found on the citizen surface for the same reason.
 *
 * **Indexable on purpose, and it is the only surface that is.** `/report` and
 * `/t/[id]` are `noindex` — a complaint id is a capability and a capability in
 * a search index is a leak. These pages are the opposite: §16.3 wants a
 * journalist to find a ward page by searching for the ward.
 */
export const metadata: Metadata = {
  robots: { index: true, follow: true },
};

export default function PublicLayout({ children }: { children: React.ReactNode }) {
  return children;
}
