import Image from "next/image";
import Link from "next/link";

import mark from "../../public/brand/nemesis-mark.png";

import { t, type Strings } from "@/lib/i18n/strings";

/**
 * The NEMESIS lockup — the supplied mark, set beside live type.
 *
 * **The mark is the brand's own file and the word is not.** `public/brand/
 * nemesis-mark.png` is the monogram from `assets/nemesis-mark-transparent.png`,
 * trimmed to the glyph and scaled once at build time rather than shipped at
 * 1004 × 533. The wordmark beside it is **text**, not the lockup's baked
 * lettering, and that is a deliberate split:
 *
 *   · §E13 Tier D is *"JS disabled, crawler, 2G"*. A word in a bitmap is a word
 *     a crawler cannot index and a translation cannot reach.
 *   · §E10.1 makes Devanagari a design partner rather than a fallback. A
 *     rasterised Latin wordmark has nothing to offer a Marathi reader.
 *   · It stays sharp at every density with no `@2x` to keep in sync.
 *
 * The mark itself goes through `next/image` from a **static import**, so its
 * intrinsic size is known at build time and the element reserves its box before
 * the bitmap arrives. A raw `<img>` here would be a layout shift on the first
 * thing on the page.
 *
 * The trade is the supplied wordmark's reversed-E letterforms, which live type
 * cannot reproduce. Worth it: the monogram is what carries the identity, and it
 * is the part that is here as artwork.
 *
 * **What was trimmed, and why it is not vandalism.** The source is a
 * presentation render — an embossed mark with a soft cast shadow under it. The
 * crop takes the glyph at full alpha and leaves the shadow behind, because §E6
 * is a print process and *"a press has no cast light in it"* — the same rule
 * that keeps a drop shadow off the film's phone frame. The mark's own geometry
 * is untouched.
 */
export function Wordmark({ strings }: { readonly strings: Strings }) {
  return (
    <Link className="portal__lockup" href="/">
      {/*
        `alt=""`, deliberately. The word is right beside it in text, so a
        screen reader that announced both would read the brand twice — the
        commonest way a logo lockup becomes an accessibility defect.
      */}
      <Image
        className="portal__lockup-mark"
        src={mark}
        alt=""
        // A static import, so the intrinsic size is read at build time and the
        // box is reserved before the bitmap lands — no reflow on the masthead,
        // which is the first thing on the page and the worst place for one.
        priority
      />
      <span className="portal__wordmark type-micro">{t(strings, "portal.wordmark")}</span>
    </Link>
  );
}

/**
 * The way back out of a door.
 *
 * A link to the landing, not `history.back()`. The doors are server-rendered
 * and must work with no JavaScript at all (§E13 Tier D), and a history call is
 * both unavailable there and wrong where it *is* available: somebody who
 * arrived from a search result or a printed poster has no history to go back
 * to, and the control would take them off the product entirely.
 *
 * Labelled with its destination rather than a bare "Back", for the same reason.
 * A reader who lands here from outside has no idea what "back" means; "back to
 * the start" is true whichever way they arrived.
 */
export function BackLink({ strings }: { readonly strings: Strings }) {
  return (
    <Link className="portal__back type-caption" href="/">
      <span aria-hidden="true" className="portal__back-mark" />
      {t(strings, "portal.back")}
    </Link>
  );
}
