/**
 * §E16 — "The Walk". A scroll-driven film in nine acts that never cuts to the
 * product; it pulls back until the product is what you are already looking at.
 *
 * The shell is deliberately bare: the film owns the whole viewport, and every
 * piece of chrome it needs is drawn inside an act rather than around them.
 */
export default function StoryLayout({ children }: { children: React.ReactNode }) {
  return <main data-surface="story">{children}</main>;
}
