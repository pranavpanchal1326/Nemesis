import { notTranslatable, t, type Strings } from "@/lib/i18n/strings";

import { actStyle } from "../acts";

/**
 * Acts 0–3 — the cold open, the walk, the stop, the silence (§E16, F12).
 *
 * **These four render on the server and never hydrate.** Not an optimisation:
 * §E13's Tier D is *"semantic article, all copy present"*, and the only way to
 * guarantee that for the film's opening is for the opening to *be* semantic
 * markup that happens to have a camera behind it. Everything that moves here
 * moves in CSS, triggered by a `data-active` attribute that GSAP's
 * ScrollTrigger sets on the section (`Walk.tsx`) — so a reader with no
 * JavaScript gets four acts of copy, correctly ordered and correctly marked
 * up, and loses only the motion.
 *
 * **What is real in these four acts: none of it, and the markup says so.**
 * Act 3's nine ghost flags are §E16's own dates, and they are narrative — a
 * picture of the problem this product exists for, not a claim about this
 * deployment's backlog. The first real thing in the film arrives in Act 5,
 * where the stamps come off a complaint's own ledger. So every act carries
 * `data-real`, `tests/story.spec.ts` asserts which acts claim which, and §44's
 * REAL/SIMULATED line for the landing has something in the DOM to be checked
 * against. §6 Principle #8: the honest label is the differentiator, and a film
 * is exactly where a product is most tempted to blur it.
 */

/** §E16: *"`NEMESIS` sets in Gambarino glyph-by-glyph (24 ms stagger)"*. */
const GLYPH_STAGGER_MS = 24;

/**
 * §E16 Act 3: *"reported 14 Mar — no closure, 02 Apr, 19 Jun, 07 Aug. Nine of
 * them."* The four the blueprint names, and five more spread across the same
 * year so the stretch of road reads as a year of silence rather than a bad
 * fortnight. Dates rather than a range because a range is a statistic and a
 * date is a promise somebody remembers being made.
 */
const GHOST_DATES = [
  "14 Mar",
  "02 Apr",
  "28 Apr",
  "19 Jun",
  "05 Jul",
  "23 Jul",
  "07 Aug",
  "30 Aug",
  "16 Sep",
] as const;

/**
 * Split a word into the units a reader perceives as letters.
 *
 * Not `[...name]`, and not `.split("")`. The name is a translation unit like
 * every other string here, so a Devanagari deployment sets a Devanagari word —
 * and a Devanagari cluster is several code points that must animate as one
 * mark. Splitting by code point would stagger a vowel sign away from the
 * consonant it belongs to, which is §E10.1's whole complaint about treating
 * scripts as bytes. `Intl.Segmenter` asks the locale.
 */
function glyphs(word: string, locale: string): readonly string[] {
  return [...new Intl.Segmenter(locale, { granularity: "grapheme" }).segment(word)].map(
    (unit) => unit.segment,
  );
}

export function ColdOpen({ strings }: { readonly strings: Strings }) {
  const name = t(strings, "story.name");

  return (
    <section
      className="act cold-open"
      data-act="cold-open"
      data-real="false"
      aria-labelledby="act-cold-open"
      style={actStyle("cold-open")}
    >
      <div className="act__panel">
        {/*
         * One `<h1>` for the document, with the glyphs split inside it. Split
         * into spans for the stagger and re-joined for assistive technology by
         * `aria-label`, because a screen reader announcing "N E M E S I S" one
         * letter at a time is the accessibility cost this effect would
         * otherwise have.
         */}
        <h1 id="act-cold-open" className="cold-open__name type-poster" aria-label={name}>
          {glyphs(name, strings.locale).map((glyph, index) => (
            <span
              key={`${glyph}-${String(index)}`}
              className="cold-open__glyph"
              aria-hidden="true"
              style={
                { "--glyph-delay": `${String(index * GLYPH_STAGGER_MS)}ms` } as React.CSSProperties
              }
            >
              {glyph}
            </span>
          ))}
        </h1>

        {/*
         * §E16 says *"Courier Prime micro-caps expand the acronym"* — and
         * NEMESIS is not an acronym. §2 of the main blueprint is explicit that
         * the name is mythological and carries the mission rather than a set
         * of initials. So the line expands the *name* into what the product
         * is, in the face §E16 asks for. Inventing seven words to fit the
         * letters would be the one thing §2's brand mandate rules out: copy
         * that means nothing, on the surface that introduces the product.
         */}
        <p className="cold-open__expand type-micro">{t(strings, "story.expand")}</p>
        {/*
         * The motto is the film's thesis, and it was set in the interface face
         * at `type-heading` — 20px of the console's workhorse, under a 154px
         * masthead, with an eyebrow between them. The ladder had no second
         * voice: it went from the loudest thing on the page to the quietest but
         * one, and "Prove, don't log." is the sentence the whole nine acts are
         * an argument for.
         *
         * `type-display-2` is the narrative face, and it is exactly what Act 1
         * gives its three lines. Setting the motto at the same step is what
         * makes the cold open read as the first beat of the same film rather
         * than a title card in front of one: name, what it is, the promise, the
         * cue — four steps, one voice change, and the promise is audible.
         */}
        <p className="cold-open__motto type-display-2">{t(strings, "story.motto")}</p>

        <div className="cold-open__cue">
          <span className="cold-open__cue-rule" aria-hidden="true" />
          <p className="type-caption">{t(strings, "story.scroll")}</p>
        </div>
      </div>
    </section>
  );
}

export function TheWalk({ strings }: { readonly strings: Strings }) {
  return (
    <section
      className="act"
      data-act="walk"
      data-real="false"
      aria-labelledby="act-walk"
      style={actStyle("walk")}
    >
      <h2 id="act-walk" className="type-micro">
        {t(strings, "story.act.walk")}
      </h2>
      {/*
       * §E16: *"Copy at road level, a beat apart"*. The beat is the gap
       * between the lines and the gap is a scroll distance, not a delay —
       * which is the same rule the whole film is built on. A reader who stops
       * between two lines stays between them.
       */}
      <div className="walk-lines">
        <p className="walk-lines__line type-display-2">{t(strings, "story.walk.reported")}</p>
        <p className="walk-lines__line type-display-2">{t(strings, "story.walk.inProgress")}</p>
        <p className="walk-lines__line type-display-2">{t(strings, "story.walk.weeksPass")}</p>
      </div>
      <p className="type-caption">{t(strings, "story.walk.described")}</p>
    </section>
  );
}

export function TheStop({ strings }: { readonly strings: Strings }) {
  return (
    <section
      className="act"
      data-act="stop"
      data-real="false"
      aria-labelledby="act-stop"
      style={actStyle("stop")}
    >
      <div className="act__panel">
        <h2 id="act-stop" className="type-micro">
          {t(strings, "story.act.stop")}
        </h2>
        {/*
         * The act has no copy in §E16, deliberately: *"The figure's shoulders
         * drop — one movement, held a full second. That is the entire
         * disappointment beat."* Adding a line here would explain a beat whose
         * whole force is that it is not explained.
         *
         * What is here is the *description*, and it is not a caption — it is
         * what a reader who cannot see the canvas is owed. §E22 makes the
         * accessible peer a peer rather than a fallback, and a wordless act is
         * exactly where that promise is easiest to quietly break.
         */}
        <p className="type-body">{t(strings, "story.stop.described")}</p>
      </div>
    </section>
  );
}

export function TheSilence({ strings }: { readonly strings: Strings }) {
  return (
    <section
      className="act"
      data-act="silence"
      data-real="false"
      aria-labelledby="act-silence"
      style={actStyle("silence")}
    >
      <div className="act__panel">
        <h2 id="act-silence" className="type-micro">
          {t(strings, "story.act.silence")}
        </h2>

        <ul className="ghost-flags">
          {GHOST_DATES.map((date) => (
            // All nine dim at once (§E16) — no stagger, so no per-item delay.
            <li key={date} className="ghost-flags__flag type-hand">
              {t(strings, "story.silence.flag", { date: notTranslatable(date) })}
            </li>
          ))}
        </ul>

        <hr className="rule-draw" />

        <blockquote className="silence__quote type-title">
          {t(strings, "story.silence.quote")}
        </blockquote>
        <p className="type-caption">{t(strings, "story.silence.described")}</p>
      </div>
    </section>
  );
}
