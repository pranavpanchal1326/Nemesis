import { notTranslatable, t, type Strings } from "@/lib/i18n/strings";
import { HonestyTable } from "@/public/HonestyTable";

import { ACTS, type ActId } from "./acts";
import "./story.css";

/**
 * Tier C — the nine riso prints (§E13, §E3.2, M9.8).
 *
 * > **C — storyboard.** `prefers-reduced-motion`, or no WebGL. Nine
 * > art-directed **riso prints**, scroll-snapped, same copy. **Visually
 * > continuous with Tier S because it is the same process.**
 *
 * And, one line further down: *"Tier C is a **design deliverable with its own
 * review**, per §E3.2."* That is the sentence this file is written against, so
 * three things are true of it deliberately:
 *
 * **It is drawn, not screenshotted.** Nine hand-composed frames in flat ink —
 * the road, the pothole, the nine flags, the phone, the gates, the merge, the
 * survey frame, the bench. A tier built from stills of the 3D scene would be a
 * degraded photograph of the film; §E13 asks for a *print of the same story*,
 * which is a different artefact and is why it is authored rather than captured.
 *
 * **It uses the same inks and the same press as every other surface.** Every
 * fill is a generated ink custom property, the sheet is the surface's own ink
 * set, and the frames sit under the same `<Press>` the rest of the product
 * prints through. That is the whole of "visually continuous because it is the
 * same process" — not a resemblance, a shared implementation.
 *
 * **It carries the same words.** The copy in these prints resolves through the
 * same keys the acts resolve, so a translation lands in both tiers at once and
 * a reader on the reduced-motion path is never reading a shorter product.
 *
 * **Why it is a server component with no motion at all.** Somebody who has
 * asked their operating system for reduced motion has said something (`tier.ts`
 * argues that at length), and answering it with an animation-free page that
 * still needs a renderer, a scroll hijack and 300 KB of film would be answering
 * a different request. This tier renders on the server and hydrates nothing —
 * which also makes it exactly what §E13 Tier D needs, one rung down, with the
 * pictures dropping out and the words staying.
 */

const FRAME_VIEWBOX = "0 0 320 240";

export function Storyboard({
  strings,
  city,
}: {
  readonly strings: Strings;
  /** The tenant's own name for itself, for the frame that is a survey of it. */
  readonly city: string;
}) {
  return (
    <div className="storyboard" data-tier-surface="storyboard">
      <h1 className="type-display-1">{t(strings, "story.storyboard.heading")}</h1>
      <p className="type-body">{t(strings, "story.storyboard.note")}</p>
      <p className="type-heading">{t(strings, "story.motto")}</p>

      <ol className="storyboard__prints">
        {ACTS.map((act) => (
          <li
            key={act.id}
            className="storyboard__print"
            data-act={act.id}
            data-print={String(act.index)}
          >
            <span className="storyboard__index type-micro">
              {t(strings, "story.storyboard.frame", { index: act.index })}
            </span>
            <Frame id={act.id} label={t(strings, `story.act.${act.id}`)} />
            <h2 className="type-heading">{t(strings, `story.act.${act.id}`)}</h2>
            <p className="storyboard__copy type-body">{copy(strings, act.id, city)}</p>
          </li>
        ))}
      </ol>

      {/*
       * §E16.2 — *"Act 9 renders §44 on the marketing surface"* — holds in this
       * tier too, and it costs nothing to keep: the table describes the
       * platform rather than the city, so it needs no tenant data and makes no
       * upstream call. A storyboard that dropped it would be the one tier where
       * the product quietly stopped publishing its own limitations, which is
       * the opposite of what §E13 means by *"same copy"*.
       */}
      <section data-story-honesty="true" aria-labelledby="storyboard-receipts">
        <h2 id="storyboard-receipts" className="type-heading">
          {t(strings, "story.receipts.honesty")}
        </h2>
        <HonestyTable strings={strings} />
      </section>
    </div>
  );
}

/**
 * The line each print carries.
 *
 * The same keys the acts use, so the two tiers cannot drift into two products.
 * Where §E16 gives an act no copy — Act 2 is a held movement and nothing else —
 * the print carries the description the accessible peer already carries, which
 * is the honest text for a frame whose content is a gesture.
 */
function copy(strings: Strings, id: ActId, city: string) {
  switch (id) {
    case "cold-open":
      return t(strings, "story.motto");
    case "walk":
      return t(strings, "story.walk.inProgress");
    case "stop":
      return t(strings, "story.stop.described");
    case "silence":
      return t(strings, "story.silence.quote");
    case "report":
      return t(strings, "story.report.described");
    case "pipeline":
      return t(strings, "story.pipeline.note");
    case "merge":
      return t(strings, "story.merge.live");
    case "city-awake":
      return t(strings, "story.city.surveyOf", { city: notTranslatable(city) });
    case "table":
      return t(strings, "story.table.note");
    case "receipts":
      return t(strings, "story.receipts.note");
  }
}

/**
 * One print.
 *
 * Flat ink on a sheet: no gradients, no strokes finer than the press can hold,
 * and no more than three inks in a frame — riso's own constraint, and the
 * reason §E6's palette is what it is. `aria-hidden` because the frame carries
 * no information the copy beside it does not; the print is the picture and the
 * paragraph is the text, which is the correct division for a storyboard.
 */
function Frame({ id, label }: { readonly id: ActId; readonly label: string }) {
  return (
    <svg
      className="storyboard__frame"
      viewBox={FRAME_VIEWBOX}
      role="img"
      aria-label={label}
      xmlns="http://www.w3.org/2000/svg"
    >
      <rect x="0" y="0" width="320" height="240" fill="var(--color-role-ground)" />
      {scene(id)}
      {/* The margin. Every print is registered inside a drawn frame, which is
          what makes nine of them read as a sequence rather than as nine
          pictures. */}
      <rect
        x="8"
        y="8"
        width="304"
        height="224"
        fill="none"
        stroke="var(--color-role-rule)"
        strokeWidth="1"
      />
    </svg>
  );
}

/** The horizon every frame is composed against. */
const GROUND = 176;

function scene(id: ActId) {
  const ink = "var(--color-riso-black)";
  const second = "var(--color-riso-fed-blue)";
  const third = "var(--color-riso-flu-pink)";

  switch (id) {
    case "cold-open":
      // Black, grain, a distant city. Silhouettes only — nothing is legible
      // yet, which is the whole instruction for the frame.
      return (
        <>
          <rect x="8" y={GROUND} width="304" height="56" fill={ink} opacity="0.9" />
          {[40, 74, 96, 132, 168, 196, 232, 268].map((x, index) => (
            <rect
              key={x}
              x={x}
              y={GROUND - 18 - index * 4}
              width="18"
              height={18 + index * 4}
              fill={ink}
              opacity="0.45"
            />
          ))}
        </>
      );

    case "walk":
      // A locked side view: road, hoarding, a tea stall, sagging wires, and a
      // figure with no face — §E8.2's rule, which holds in every tier.
      return (
        <>
          <rect x="8" y={GROUND} width="304" height="56" fill={ink} opacity="0.12" />
          <path d="M8 118 Q 160 138 312 118" fill="none" stroke={ink} strokeWidth="1.5" />
          <rect x="36" y="112" width="52" height="34" fill={second} opacity="0.5" />
          <rect x="214" y="126" width="44" height="24" fill={ink} opacity="0.2" />
          <Figure x={150} ink={ink} />
        </>
      );

    case "stop":
      // Ankle height. The pothole fills the lower third; the water in it is the
      // second ink, because water is the thing you notice.
      return (
        <>
          <rect x="8" y="150" width="304" height="82" fill={ink} opacity="0.12" />
          <ellipse cx="160" cy="196" rx="88" ry="26" fill={ink} opacity="0.85" />
          <ellipse cx="160" cy="198" rx="66" ry="17" fill={second} opacity="0.7" />
          <Figure x={250} ink={ink} slump />
        </>
      );

    case "silence":
      // Nine flags on one stretch of road, all the same weight. The evenness is
      // the point: no single one is the story.
      return (
        <>
          <path d="M8 190 L312 152" fill="none" stroke={ink} strokeWidth="1.5" />
          {Array.from({ length: 9 }, (_, index) => {
            const x = 28 + index * 32;
            const y = 188 - index * 4.4;
            return (
              <g key={index}>
                <line x1={x} y1={y} x2={x} y2={y - 34} stroke={ink} strokeWidth="1" />
                <rect x={x} y={y - 34} width="20" height="12" fill={ink} opacity="0.35" />
              </g>
            );
          })}
        </>
      );

    case "report":
      // The camera pushing through the screen: the phone is the frame, and what
      // is inside it is the only thing in focus.
      return (
        <>
          <rect x="8" y={GROUND} width="304" height="56" fill={ink} opacity="0.1" />
          <rect x="108" y="44" width="104" height="164" rx="10" fill={ink} opacity="0.9" />
          <rect x="116" y="56" width="88" height="140" fill="var(--color-role-ground)" />
          <ellipse cx="160" cy="150" rx="34" ry="12" fill={ink} opacity="0.6" />
          <circle cx="160" cy="88" r="10" fill={third} />
        </>
      );

    case "pipeline":
      // Five gates and a card travelling through them. Five, not six: §E16.1's
      // degraded perception is a third outcome of one gate, not another gate.
      return (
        <>
          {Array.from({ length: 5 }, (_, index) => {
            const x = 34 + index * 56;
            return (
              <g key={index}>
                <rect x={x} y="80" width="6" height="90" fill={ink} opacity="0.8" />
                <rect x={x + 32} y="80" width="6" height="90" fill={ink} opacity="0.8" />
              </g>
            );
          })}
          <rect x="140" y="110" width="40" height="28" fill={second} opacity="0.65" />
        </>
      );

    case "merge":
      // Three flags leaning into one, the survivor taller, and two registration
      // rings left where the absorbed reports were. The rings are the frame's
      // subject, not its background.
      return (
        <>
          <rect x="8" y={GROUND} width="304" height="56" fill={ink} opacity="0.1" />
          <line x1="120" y1={GROUND} x2="150" y2="96" stroke={ink} strokeWidth="1" />
          <line x1="200" y1={GROUND} x2="170" y2="96" stroke={ink} strokeWidth="1" />
          <line x1="160" y1={GROUND} x2="160" y2="72" stroke={ink} strokeWidth="2" />
          <rect x="160" y="72" width="34" height="18" fill={third} opacity="0.8" />
          <circle
            cx="120"
            cy={GROUND}
            r="12"
            fill="none"
            stroke={ink}
            strokeWidth="1"
            opacity="0.5"
          />
          <circle
            cx="200"
            cy={GROUND}
            r="12"
            fill="none"
            stroke={ink}
            strokeWidth="1"
            opacity="0.5"
          />
        </>
      );

    case "city-awake":
      // The survey frame: margins, a scale bar, a north arrow. The film has
      // become a drawing of the city.
      return (
        <>
          <rect
            x="28"
            y="28"
            width="264"
            height="184"
            fill="none"
            stroke={ink}
            strokeWidth="1"
            opacity="0.6"
          />
          {[70, 120, 170, 220].map((x) => (
            <line
              key={x}
              x1={x}
              y1="28"
              x2={x}
              y2="212"
              stroke={ink}
              strokeWidth="0.5"
              opacity="0.25"
            />
          ))}
          <line x1="44" y1="196" x2="104" y2="196" stroke={ink} strokeWidth="2" />
          <path d="M272 60 L266 78 L272 72 L278 78 Z" fill={ink} />
          <circle cx="150" cy="120" r="5" fill={third} />
          <circle cx="196" cy="150" r="5" fill={second} />
        </>
      );

    case "table":
      // The model on a workbench: cutting mat, scalpel, a stack of paper, a
      // riso proof drying on the corner.
      return (
        <>
          <rect x="8" y="120" width="304" height="112" fill={ink} opacity="0.08" />
          <rect x="60" y="132" width="140" height="72" fill={second} opacity="0.25" />
          <rect
            x="216"
            y="140"
            width="56"
            height="40"
            fill="var(--color-role-ground)"
            stroke={ink}
          />
          <line x1="220" y1="200" x2="276" y2="188" stroke={ink} strokeWidth="2" />
        </>
      );

    case "receipts":
      // Deliberately boring: a table of rows. It is meant to look like a
      // document, because it is one.
      return (
        <>
          {Array.from({ length: 7 }, (_, index) => (
            <g key={index}>
              <line
                x1="32"
                y1={56 + index * 22}
                x2="288"
                y2={56 + index * 22}
                stroke={ink}
                strokeWidth="0.75"
                opacity="0.5"
              />
              <rect
                x="32"
                y={44 + index * 22}
                width={index % 3 === 0 ? 56 : 34}
                height="7"
                fill={ink}
                opacity="0.35"
              />
            </g>
          ))}
        </>
      );
  }
}

/** The figure — §E8.2, and §E16's own rule: **no face, ever.** */
function Figure({
  x,
  ink,
  slump = false,
}: {
  readonly x: number;
  readonly ink: string;
  readonly slump?: boolean;
}) {
  const shoulder = slump ? 128 : 122;
  return (
    <g>
      <circle cx={x} cy={shoulder - 16} r="7" fill={ink} />
      <rect x={x - 8} y={shoulder} width="16" height="34" fill={ink} />
      <line x1={x - 6} y1={shoulder + 34} x2={x - 10} y2={GROUND} stroke={ink} strokeWidth="3" />
      <line x1={x + 6} y1={shoulder + 34} x2={x + 10} y2={GROUND} stroke={ink} strokeWidth="3" />
    </g>
  );
}
