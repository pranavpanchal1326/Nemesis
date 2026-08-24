import type { ReactNode } from "react";

import type { InkSetName, PressQuality, SeverityLevel } from "@/design/generated/tokens";
import { filterId, rootStyle, screenStyle, separationMatrix } from "./press-filter";
import { planPress } from "./press-model";
import { PressJitter } from "./PressJitter";
import "./press.css";

export interface PressProps {
  /** Which ink set this surface runs (§E9.2). */
  readonly surface: InkSetName;
  /** §E6.4's dial. The adaptive quality manager turns this before it touches
   *  frame rate — the one degradation that improves the picture. */
  readonly quality?: PressQuality;
  /** The third pass, where the ink set names `severity` (§E9.2). */
  readonly severity?: SeverityLevel;
  /** Fixed seed ⇒ reproducible registration ⇒ golden images (§E24). */
  readonly seed?: number;
  /**
   * What goes through the press: photographs, maps, fields, the clay world.
   */
  readonly imagery?: ReactNode;
  /**
   * What does **not**: text and data.
   *
   * This is a separate prop rather than a convention because ADR-0038 and
   * §E6.2 make it a structural rule — "text prints solid, 100% density, no
   * halftone, no offset" — and a rule enforced by where a node is mounted
   * cannot be forgotten the way a rule enforced by review can. Children are
   * siblings of every processed layer, never descendants of one.
   */
  readonly children?: ReactNode;
  readonly className?: string;
}

/**
 * `<Press>` — §E26.
 *
 * > The print pipeline. 2D CSS/SVG layer and 3D TSL pass from one token
 * > source. Text composites unprocessed.
 *
 * Server-rendered by default, and correct with JavaScript disabled: the sheet
 * prints at its seeded registration and simply does not move, which is §E13
 * Tier D behaving like a print rather than like a broken page. `<PressJitter>`
 * mounts only when the quality tier animates, and all it does is re-jitter the
 * plates on the 12 Hz stepped clock (§E6.1 stage 3, §E7.2).
 */
export function Press({
  surface,
  quality = "full",
  severity,
  seed = 1,
  imagery,
  children,
  className,
}: PressProps) {
  const plan = planPress(
    severity === undefined ? { surface, quality, seed } : { surface, quality, severity, seed },
  );
  const id = filterId(plan);

  return (
    <div
      className={className === undefined ? "press" : `press ${className}`}
      data-quality={plan.quality}
      data-plates={plan.plates.length}
      style={rootStyle(plan)}
    >
      <svg className="press__defs" aria-hidden="true" focusable="false">
        <defs>
          <filter id={id} colorInterpolationFilters="sRGB">
            {plan.plates.map((plate, i) => (
              <g key={plate.id}>
                {/* stage 1 — how much of this ink the pixel is missing */}
                <feColorMatrix
                  in="SourceGraphic"
                  type="matrix"
                  values={separationMatrix(plate.linear)}
                  result={`density${String(i)}`}
                />
                <feFlood floodColor={plate.hex} result={`ink${String(i)}`} />
                <feComposite
                  in={`ink${String(i)}`}
                  in2={`density${String(i)}`}
                  operator="in"
                  result={`plate${String(i)}`}
                />
                {/* stage 3 — the sheet slipped */}
                <feOffset
                  id={`${id}-offset-${String(i)}`}
                  in={`plate${String(i)}`}
                  dx={plate.offsetPx[0]}
                  dy={plate.offsetPx[1]}
                  result={`offset${String(i)}`}
                />
              </g>
            ))}
            {/* stage 5 — overprint. Multiply, never alpha: overlapping inks
                produce a genuine third colour, exactly as they do on paper. */}
            {plan.plates.slice(1).map((plate, i) => (
              <feBlend
                key={plate.id}
                in={i === 0 ? "offset0" : `blend${String(i - 1)}`}
                in2={`offset${String(i + 1)}`}
                mode="multiply"
                result={`blend${String(i)}`}
              />
            ))}
          </filter>
        </defs>
      </svg>

      <div className="press__imagery" style={{ filter: `url(#${id})` }}>
        {imagery}
      </div>

      <div className="press__screens" aria-hidden="true">
        {plan.plates.map((plate, i) => (
          <div key={plate.id} className="press__screen" style={screenStyle(plate, plan, i)} />
        ))}
      </div>

      <div className="press__grain" aria-hidden="true" />
      <div className="press__deckle" aria-hidden="true" />

      {/* ADR-0038. Everything above this line is processed; nothing below it is. */}
      <div className="press__text" data-press-exempt="true">
        {children}
      </div>

      {plan.animated ? <PressJitter filterId={id} plan={plan} /> : null}
    </div>
  );
}
