/**
 * The plate on a front door — §E5, §E6.1, §E9.2.
 *
 * **Why this is drawn and not photographed.** The obvious way to make a page
 * look designed is to put a photograph on it, and it is the one move this
 * product cannot make. §E5 gives the whole application three materials — clay
 * for the world, ink for people, paper for documents — and a photograph is a
 * fourth. §E3.1 is sharper still: a picture of a pothole that is not *this
 * city's* pothole is a picture of evidence, on the surface where a resident is
 * about to file some. A stock image of a smiling official on a civic reporting
 * tool is not decoration; it is a claim, and it is not true.
 *
 * So the door gets a **print** instead, which is what this product makes. Every
 * shape below is drawn in the run's own inks, overprinted with a real
 * misregistration offset (§E6.1 stage 3), screened through a halftone pattern
 * (stage 2), and trimmed with the registration marks a press actually leaves.
 * It is the same six-stage process `press-tsl.ts` runs over the 3D city,
 * expressed in the one medium a server component can emit: static SVG.
 *
 * **A server component, and it has to be.** §E13's Tier D is *"JS disabled,
 * crawler, 2G"*, and the doors are the surface most likely to be opened on a
 * bad connection in the street. `<InkFigure>` is the product's other art
 * system and it is a canvas driven by a 12 fps clock — right for an act of the
 * film, wrong for a door, because Tier D would get a blank box where the
 * artwork is. This renders as markup, weighs nothing, and needs no bundle.
 *
 * **`aria-hidden`, deliberately.** §E22 makes an accessible peer a *peer* — but
 * a peer to a plate would be a description of a decorative print, and the
 * honest thing to tell a screen reader about furniture is nothing. Every word
 * on these pages is real text beside it; none of the meaning is in here.
 */

/**
 * What the plate depicts, not who it is for.
 *
 * Named this way because the receipts act (§E16 Act 9) wants the plan too, and
 * a type that said `"staff"` would have made a landing page ask for the
 * officer's artwork to get a picture of a city. The two are the same city at
 * two altitudes: `street` is standing on it, `plan` is looking down at it.
 */
export type PlateSubject = "street" | "plan";

/**
 * The misregistration, in user units.
 *
 * A slip is a direction and a distance, not two independent numbers — a sheet
 * moves through a press, it does not jitter on two axes. These are the same
 * shape `press-model.ts` generates, fixed here rather than seeded, because a
 * static plate has no frames to re-jitter across and a "random" offset that
 * never changes is just a number somebody should have chosen on purpose.
 */
const SLIP = { x: 1.6, y: -1.1 } as const;

export function PortalPlate({ subject }: { readonly subject: PlateSubject }) {
  const id = `plate-${subject}`;

  return (
    <svg
      className="portal__plate"
      viewBox="0 0 320 250"
      // `slice`: the band crops the drawing, the way a column crops a picture
      // in a newspaper. `meet` would letterbox it and put the thumbnail back.
      preserveAspectRatio="xMidYMid slice"
      role="presentation"
      aria-hidden="true"
      focusable="false"
      xmlns="http://www.w3.org/2000/svg"
    >
      <defs>
        {/*
          §E6.1 stage 2 — the screen. A dot on a 4-unit cell at the classic 15°
          angle, which is the first of `PRESS.screenAngles`. Two densities: the
          heavy one carries a shape, the light one carries a wash, and having
          exactly two is what keeps a drawing from turning into a gradient.
        */}
        <pattern
          id={`${id}-screen`}
          width="4"
          height="4"
          patternUnits="userSpaceOnUse"
          patternTransform="rotate(15)"
        >
          <circle cx="2" cy="2" r="1.35" fill="currentColor" />
        </pattern>
        <pattern
          id={`${id}-screen-light`}
          width="5"
          height="5"
          patternUnits="userSpaceOnUse"
          patternTransform="rotate(75)"
        >
          <circle cx="2.5" cy="2.5" r="0.9" fill="currentColor" />
        </pattern>
      </defs>

      {/* Stage 6 — the stock. Every plate multiplies onto this. */}
      <rect x="0" y="0" width="320" height="250" fill="var(--color-kraft-100)" />

      {subject === "street" ? <StreetPlate id={id} /> : <PlanPlate id={id} />}

      <TrimMarks />
    </svg>
  );
}

/**
 * The street plate — the city from the height a person stands at.
 *
 * §E16's own subject: *"a pothole gets reported"*. The pothole is the darkest
 * thing on the sheet and the pin standing in it is the only aqua, because a
 * severity mark is the one colour in this product that carries meaning (§E9.4
 * rule 1) and spending it anywhere else on a decorative plate would teach a
 * reader the wrong vocabulary before they reach a real one.
 */
function StreetPlate({ id }: { readonly id: string }) {
  return (
    <g>
      {/*
        The far skyline — sunflower, laid down first and offset hardest, because
        the plate a press runs first is the one the rest of the sheet is
        registered against and it is never the one that lands true.
      */}
      <g color="var(--color-riso-sunflower)">
        <path
          d="M0 150 L0 96 L26 96 L26 76 L58 76 L58 104 L92 104 L92 66 L128 66 L128 92 L166 92 L166 58 L206 58 L206 88 L246 88 L246 70 L286 70 L286 100 L320 100 L320 150 Z"
          fill={`url(#${id}-screen)`}
        />
      </g>

      {/*
        The near blocks, in brown. Flat and stepped tops, at four different
        heights: the first pass drew them as pentagons with a peak, which on a
        civic surface reads as a row of headstones — a good reason to look at a
        drawing rather than to reason about it.
      */}
      <g color="var(--color-riso-brown)">
        {/* Left block, with a setback storey. */}
        <path d="M16 150 L16 62 L58 62 L58 44 L86 44 L86 150 Z" fill={`url(#${id}-screen)`} />
        {/* Centre block, the tallest thing on the sheet. */}
        <path d="M112 150 L112 34 L168 34 L168 150 Z" fill={`url(#${id}-screen)`} />
        {/* Right block, low and long. */}
        <path d="M196 150 L196 82 L262 82 L262 70 L300 70 L300 150 Z" fill={`url(#${id}-screen)`} />
      </g>

      {/* The same silhouettes as hairlines, in register — the black plate over
          the screened brown, which is what gives a riso its edge. */}
      <g fill="none" stroke="var(--color-riso-black)" strokeWidth="1.25">
        <path d="M16 150 L16 62 L58 62 L58 44 L86 44 L86 150" />
        <path d="M112 150 L112 34 L168 34 L168 150" />
        <path d="M196 150 L196 82 L262 82 L262 70 L300 70 L300 150" />
      </g>

      {/* Windows, knocked out of the plates rather than drawn on them. A
          regular grid, because a building's windows are on a grid and the
          irregular ones in the first pass read as damage. */}
      <g fill="var(--color-kraft-100)">
        <rect x="26" y="76" width="9" height="13" />
        <rect x="42" y="76" width="9" height="13" />
        <rect x="26" y="100" width="9" height="13" />
        <rect x="42" y="100" width="9" height="13" />
        <rect x="26" y="124" width="9" height="13" />
        <rect x="64" y="58" width="9" height="13" />
        <rect x="64" y="88" width="9" height="13" />
        <rect x="64" y="118" width="9" height="13" />

        <rect x="122" y="48" width="10" height="14" />
        <rect x="140" y="48" width="10" height="14" />
        <rect x="122" y="76" width="10" height="14" />
        <rect x="140" y="76" width="10" height="14" />
        <rect x="122" y="104" width="10" height="14" />
        <rect x="140" y="104" width="10" height="14" />
        <rect x="122" y="130" width="10" height="12" />

        <rect x="206" y="96" width="9" height="13" />
        <rect x="222" y="96" width="9" height="13" />
        <rect x="206" y="120" width="9" height="13" />
        <rect x="272" y="86" width="9" height="13" />
        <rect x="272" y="112" width="9" height="13" />
      </g>

      {/* The road. A solid brown pass, un-screened, because the darkest plane in
          the picture is where the subject is and a screen there would make the
          pothole compete with its own texture. */}
      <path d="M0 150 L320 150 L320 250 L0 250 Z" fill="var(--color-riso-brown)" />

      {/* The kerb — one hairline, and the only straight line in the picture that
          is neither a building nor a lane. */}
      <rect x="0" y="150" width="320" height="2" fill="var(--color-riso-black)" />

      {/* Lane dashes, knocked out of the road rather than drawn on it. */}
      <g fill="var(--color-kraft-100)" opacity="0.5">
        <rect x="6" y="204" width="36" height="5" />
        <rect x="62" y="204" width="36" height="5" />
        <rect x="118" y="204" width="36" height="5" />
        <rect x="240" y="204" width="36" height="5" />
        <rect x="296" y="204" width="36" height="5" />
      </g>

      {/* The pothole. Irregular, because a traced circle reads as a manhole. */}
      <path
        d="M172 190 C186 181 210 183 219 194 C227 205 218 218 199 220 C180 222 166 213 165 203 C164 196 167 193 172 190 Z"
        fill="var(--color-riso-black)"
      />

      {/*
        The pin, standing in it — §E27: a pin's height is severity, not
        decoration. It is the only saturated mark on the sheet, because a
        severity glaze is the one colour in this product that carries meaning
        (§E9.4 rule 1) and spending it on scenery teaches the wrong vocabulary.
      */}
      <g color="var(--color-riso-aqua)">
        <path d="M188 200 L196 200 L194 128 L190 128 Z" fill="currentColor" />
        <circle cx="192" cy="122" r="11" fill="currentColor" />
        <circle cx="192" cy="122" r="4.5" fill="var(--color-kraft-100)" />
      </g>
    </g>
  );
}

/**
 * The plan plate — the same city, seen from above, on the table.
 *
 * §E9.3's light table is where this audience works, and a plan is what they
 * work on: wards as boxes, one of them live, the rest ruled. It is deliberately
 * the *same city* as the resident's plate at a different altitude, because the
 * two doors are two ways into one product and a pair of unrelated illustrations
 * would say the opposite.
 */
function PlanPlate({ id }: { readonly id: string }) {
  /** Ward boxes, on a 4×3 plan. Written out rather than generated: a plate is a
   *  drawing, and a loop here would be arithmetic pretending to be one. */
  const wards = [
    { x: 18, y: 40, w: 66, h: 54 },
    { x: 92, y: 40, w: 82, h: 54 },
    { x: 182, y: 40, w: 58, h: 54 },
    { x: 248, y: 40, w: 54, h: 54 },
    { x: 18, y: 102, w: 82, h: 46 },
    { x: 108, y: 102, w: 58, h: 46 },
    { x: 174, y: 102, w: 66, h: 46 },
    { x: 248, y: 102, w: 54, h: 46 },
    { x: 18, y: 156, w: 58, h: 54 },
    { x: 84, y: 156, w: 74, h: 54 },
    { x: 166, y: 156, w: 62, h: 54 },
    { x: 236, y: 156, w: 66, h: 54 },
  ];

  return (
    <g>
      {/* The sheet's own tint, slipped under everything — the wash a plan is
          printed on so the boxes have something to sit in. */}
      <g color="var(--color-riso-sunflower)">
        <rect x="10" y="32" width="300" height="186" fill={`url(#${id}-screen-light)`} />
      </g>

      {/* One ward is live. Screened, and laid down *before* the rules so the
          survey draws over it rather than the fill burying the survey — which
          is the order a plan is actually printed in. */}
      <g color="var(--color-riso-brown)">
        <rect x="108" y="102" width="58" height="46" fill={`url(#${id}-screen)`} />
      </g>

      {/* The ward boxes. Hairlines, in the brown plate, slipped. */}
      <g
        transform={`translate(${String(SLIP.x)} ${String(SLIP.y)})`}
        fill="none"
        stroke="var(--color-riso-brown)"
        strokeWidth="1.5"
      >
        {wards.map((w) => (
          <rect key={`${String(w.x)}-${String(w.y)}`} x={w.x} y={w.y} width={w.w} height={w.h} />
        ))}
      </g>

      {/* The same boxes in black, in register — two passes on one plan is what a
          plan printed twice looks like, and it is the cheapest way to say
          "this sheet came off a press". */}
      <g fill="none" stroke="var(--color-riso-black)" strokeWidth="1">
        {wards.map((w) => (
          <rect key={`${String(w.x)}-${String(w.y)}`} x={w.x} y={w.y} width={w.w} height={w.h} />
        ))}
      </g>

      {/*
        The road running through the plan — the same street the resident's plate
        is standing on, one altitude up.

        It was 2.5 units of solid black and it crossed the live ward, so the one
        square that is supposed to read as *populated* read as a smudge with a
        gash through it. A road on a plan is a route, not a boundary: it is
        thinner than the ward rules now, and in brown rather than black, so it
        passes behind the survey instead of cutting it.
      */}
      <path
        d="M10 128 L120 128 L150 96 L310 96"
        fill="none"
        stroke="var(--color-riso-brown)"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />

      {/* Pins on the plan, in severity aqua. Three, at the sizes §E27 gives
          them — a plan with one mark is a diagram, and a plan with thirty is
          wallpaper. */}
      <g fill="var(--color-riso-aqua)">
        <circle cx="137" cy="128" r="7" />
        <circle cx="212" cy="70" r="4.5" />
        <circle cx="58" cy="182" r="5.5" />
      </g>
      <g fill="var(--color-kraft-100)">
        <circle cx="137" cy="128" r="2.6" />
        <circle cx="212" cy="70" r="1.7" />
        <circle cx="58" cy="182" r="2" />
      </g>

      {/* A scale bar. The one piece of furniture on a plan that is not the plan,
          and the thing that makes the rest of it read as a survey. */}
      <g>
        <rect x="18" y="226" width="30" height="4" fill="var(--color-riso-black)" />
        <rect x="48" y="226" width="30" height="4" fill="var(--color-riso-brown)" />
        <rect x="78" y="226" width="30" height="4" fill="var(--color-riso-black)" />
      </g>
    </g>
  );
}

/**
 * The registration mark.
 *
 * Not a flourish: a registration cross is how a printer checks that the plates
 * landed on top of each other, and this sheet's plates deliberately did not
 * (see `SLIP`). Leaving the mark that would have caught it is the joke a press
 * makes about itself.
 *
 * **The corner trim marks that used to sit beside it are gone.** They were
 * correct on a plate with a margin around it and wrong the moment the artwork
 * became a band that bleeds off the sheet: a guillotine mark exists to show
 * where the paper gets cut, so one floating in the middle of a printed area
 * reads as a rendering artefact rather than as a press detail. A bled image has
 * no trim marks, because the trim went through the picture.
 */
function TrimMarks() {
  return (
    <g stroke="var(--color-riso-black)" strokeWidth="1" opacity="0.5">
      <g transform="translate(292 232)">
        <path d="M-7 0 L7 0 M0 -7 L0 7" fill="none" />
        <circle cx="0" cy="0" r="4.2" fill="none" />
      </g>
    </g>
  );
}
