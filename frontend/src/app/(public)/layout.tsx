/**
 * §E18 — the public transparency surface. Server-rendered, indexable,
 * deep-linkable, suppression-aware. §16.2 wants these bookmarkable, which is
 * one of the reasons the BFF seam exists at all (§E14.1).
 */
export default function PublicLayout({ children }: { children: React.ReactNode }) {
  return <main data-surface="public">{children}</main>;
}
