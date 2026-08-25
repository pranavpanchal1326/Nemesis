import { readFile } from "node:fs/promises";
import { join } from "node:path";

import { INK, PAPER, ROLE } from "@/design/generated/tokens";

/**
 * The §E18 share card — `satori` + `resvg`, both bundled by `next/og`.
 *
 * > `satori` + `resvg` share cards, server-rendered per complaint and per ward.
 *
 * **What a share card is for, and what it must not do.** It is the ward page as
 * it appears in a message thread, and it is read by people who will never open
 * the link. So it carries the place, the headline figure, and the sentence that
 * qualifies it — and it carries the suppression state, because a card that
 * silently omits a withheld figure is the k-anonymity misreading escaping onto
 * a surface where nobody can click through to the explanation (ADR-0021).
 *
 * **Every colour comes from the generated tokens.** `satori` supports no CSS
 * custom properties — it takes inline style objects — so the card cannot use
 * `var(--color-…)` the way every stylesheet in this application does. It reads
 * the same numbers from `design/generated/tokens.ts` instead, which is the
 * other half of the same source. `scripts/check-guards.ts` would fail this file
 * on a literal, and it is the one place in the product where reaching for one
 * would have been easy.
 *
 * **Severity ink does not appear here either** (§E9.4 rule 1). A ward is not an
 * incident.
 */

/** The OG size everything expects. Fixed, because a card that is not this
 *  aspect gets cropped by the platforms that show it. */
export const SHARE_SIZE = { width: 1200, height: 630 } as const;
export const SHARE_CONTENT_TYPE = "image/png";

/**
 * The card's faces, in a format `satori` can parse.
 *
 * The 51 woff2 files this application serves are unreadable to `satori`, which
 * takes TTF/OTF/WOFF — so `scripts/fetch_fonts.py --og` builds four TTFs from
 * them, offline, and commits them. The cost and the reasoning are recorded on
 * `OgFace` in that script rather than here, where they would be discovered by
 * somebody debugging a missing glyph.
 */
const FONT_DIR = join(process.cwd(), "public", "fonts", "og");

interface Face {
  readonly file: string;
  readonly name: string;
  readonly weight: 400 | 500 | 600;
}

const FACES: readonly Face[] = [
  { file: "panchang-600.ttf", name: "Panchang", weight: 600 },
  { file: "switzer-400.ttf", name: "Switzer", weight: 400 },
  { file: "jetbrains-mono-500.ttf", name: "JetBrains Mono", weight: 500 },
  // Registered under the *same* family name as Switzer at a different weight
  // would be wrong — it is a different script, not a different weight. `satori`
  // falls through its font list per glyph, so a Devanagari codepoint finds this
  // face and a Latin one does not reach it.
  { file: "noto-sans-devanagari-400.ttf", name: "Noto Sans Devanagari", weight: 400 },
];

export async function shareFonts() {
  const loaded = await Promise.all(
    FACES.map(async (face) => ({
      name: face.name,
      data: await readFile(join(FONT_DIR, face.file)),
      weight: face.weight,
      style: "normal" as const,
    })),
  );
  return loaded;
}

/** The stack `satori` walks per glyph. Latin first, Devanagari behind it. */
const TEXT_STACK = "Switzer, Noto Sans Devanagari";
const DISPLAY_STACK = "Panchang, Noto Sans Devanagari";
const DATA_STACK = "JetBrains Mono, Noto Sans Devanagari";

/** One headline figure, already decided by `readZone` before it got here. */
export interface ShareFigure {
  readonly label: string;
  /** The rendered text — a number, "none filed", or the withheld sentence.
   *  Decided by the caller from a `PublishedFigure`, never from a raw count. */
  readonly value: string;
  /** Withheld figures are set in the text face rather than the data face, so
   *  the card carries the same shape distinction the page does. */
  readonly withheld: boolean;
}

/**
 * The card.
 *
 * Returned as an element rather than an `ImageResponse` so the two routes that
 * use it — a city and a place — share one composition and cannot drift into two
 * cards that look like different products.
 */
export function ShareCard({
  city,
  title,
  kicker,
  figures,
  notice,
}: {
  readonly city: string;
  readonly title: string;
  /** The place's code, or the contract version. Small, mono, above the title. */
  readonly kicker: string;
  readonly figures: readonly ShareFigure[];
  /** `SYSTEM_FLAGGED_NOTICE`, trimmed to the card. §E18 keeps it first-class
   *  even here: a figure travelling without its qualifier is the assertion
   *  §22.2 is about. */
  readonly notice: string;
}) {
  return (
    <div
      style={{
        width: "100%",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        justifyContent: "space-between",
        backgroundColor: PAPER["paper-50"],
        color: ROLE.light["text-primary"].value,
        padding: "56px 64px",
        fontFamily: TEXT_STACK,
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <div
          style={{
            display: "flex",
            fontFamily: DATA_STACK,
            fontSize: 22,
            letterSpacing: 2,
            color: ROLE.light["text-secondary"].value,
          }}
        >
          {kicker}
        </div>
        <div style={{ display: "flex", fontFamily: DISPLAY_STACK, fontSize: 76, lineHeight: 1.05 }}>
          {title}
        </div>
        <div style={{ display: "flex", fontSize: 28, color: ROLE.light["text-secondary"].value }}>
          {city}
        </div>
      </div>

      <div style={{ display: "flex", gap: 48 }}>
        {figures.map((figure) => (
          <div
            key={figure.label}
            style={{
              display: "flex",
              flexDirection: "column",
              gap: 4,
              maxWidth: 340,
              borderTop: `2px solid ${ROLE.light.rule.value}`,
              paddingTop: 10,
            }}
          >
            <div
              style={{ display: "flex", fontSize: 22, color: ROLE.light["text-secondary"].value }}
            >
              {figure.label}
            </div>
            <div
              style={{
                display: "flex",
                fontFamily: figure.withheld ? TEXT_STACK : DATA_STACK,
                fontSize: figure.withheld ? 24 : 54,
                lineHeight: 1.15,
                color: figure.withheld
                  ? ROLE.light["text-secondary"].value
                  : ROLE.light["text-primary"].value,
              }}
            >
              {figure.value}
            </div>
          </div>
        ))}
      </div>

      <div
        style={{
          display: "flex",
          borderLeft: `6px solid ${INK["riso-aqua"]}`,
          paddingLeft: 16,
          fontSize: 20,
          lineHeight: 1.35,
          color: ROLE.light["text-secondary"].value,
          maxWidth: 940,
        }}
      >
        {notice}
      </div>
    </div>
  );
}
