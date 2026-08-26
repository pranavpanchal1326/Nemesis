# 0061 — A run is printed at an exposure, and the story run needs one

- **Status:** Accepted
- **Date:** 2026-08-26
- **Owner:** DESIGN · PROD
- **Blueprint:** §E5, §E6.1, §E7.1, §E9.2, §E16; ADR-0038, ADR-0055
- **Taken at:** after M8/Phase 19, on the open decision recorded in `docs/reports/story-press-flat.md`

## Context

The landing film printed as a flat field of brown ink with the city invisible
inside it. `docs/reports/story-press-flat.md` measured the cause and closed two
of the three faults; this ADR takes the third, which the report deliberately did
not guess at.

The world, the camera track, the scroll spine, the lens and the renderer are all
correct — `?stage=model` and `?stage=photograph` both show the city, and only
`?stage=print` loses it. **The press is where the model disappears, and the
reason is arithmetic rather than a bug.**

§E9.2 gives the film **brown + sunflower + aqua and no black plate**, so one
plate carries the entire model. §E7.1 says *"the clay body IS `ink.riso-brown`"*
— the subject and that plate's ink are the same colour. A plate's coverage is
`clamp(dot(row, log(sheet) − log(sample)), 0, 1)`, and across the clay's
rendered range that solved:

| Sample | Ungraded coverage (brown / sunflower / aqua) |
|---|---|
| `#B98A78` lit | 0.55 / 0.01 / 0.00 |
| `#925F52` body | 0.91 / 0.00 / 0.00 |
| `#6B4238` shadow | **1.27 → clamped** |

Everything from the body tone downward printed at or past full coverage. The two
chromatic plates contribute nothing to a brown subject — their coverages are
noise around zero. What reached the screen was one saturated plate plus stage
4's ink-density variation: soft blobs on a flat field, which is exactly what a
visitor saw and asked *"on which map?"* about.

Raising the renderer exposure was tried and measured (1.0 → 3.0): it lightens
the sheet and does not recover the model, because the shadow end saturates at
any exposure that keeps the highlights on the paper.

The report named three options and recommended the third. The other two were
rejected here for the reasons it gave: giving the story run a black plate
repaints §E9.2's authored art direction, which §E2 forbids (*"the inks are not
repainted — they are the premise of §E4"*); and letting the clay stay at
`photograph` on this surface makes the model the one exception to §E5's law that
one press runs over all three materials.

## Decision

**An ink set may state the exposure its run is printed at, and the press applies
it to the photograph before it solves the plates.**

`inkSet.<name>.gradeGamma` in `tokens.json`, generated onto `INK_SET`, carried
on `PressPlan`, spent once per plate in `press-tsl.ts`. It is a gamma about the
**sheet's own white point**:

    graded = sheet · (sample / sheet) ^ gamma

Two properties are the whole argument for this shape.

**It is one multiply.** In absorbance space the grade is a pure scale, because
`log(graded) = log(sheet) + gamma · (log(sample) − log(sheet))`, so
`log(sheet) − log(graded) = gamma · (log(sheet) − log(sample))`. The shader
spends one `mul` before the dot product it already performed. No second code
path, no per-surface branch.

**`gamma = 1` is exactly the identity.** Not approximately — the expression
reduces to the term that was already there. Every run that does not state a
grade is generated at 1.0 and its separation does not move by a single bit, so
this cannot quietly regrade the console, the public portal or the document
surface. The generator fills in the default explicitly rather than omitting it,
so a reader of `INK_SET` can see at a glance that exactly one run is graded.

**The story run is graded at 0.72**, chosen by measurement rather than by eye —
it is the largest compression that keeps the clay's entire rendered body range
off the clamp:

| Sample | Graded at 0.72 | For comparison — the `public` run, ungraded |
|---|---|---|
| `#B98A78` lit | 0.40 | 0.27 |
| `#925F52` body | 0.66 | 0.44 |
| `#6B4238` shadow | 0.89 | 0.58 |
| `#4A2D26` deep | clamped | 0.72 |
| near-black | clamped | clamped |

That is a real tonal ladder, in the same shape a run *with* a black plate gets
for free, and it still prints a solid where the subject is genuinely black —
which is what a print is supposed to do.

## Consequences

**The claim "the press prints what it is given" needs one more word**, and this
is the honest cost the report named. It now prints what it is given, *at the
exposure the run was chosen for*. A printer choosing an exposure for a run is a
real thing printers do, and it is a smaller deviation than repainting an ink set
or exempting one material from the press — but it is a second art-direction
knob, and pretending otherwise would be the kind of quiet widening this
repository writes ADRs to prevent.

**It is the only knob.** `gradeGamma` is a single scalar per run with a stated
default and a measured value in the one place that states one. It is not a
per-surface curve, not a lift/gain/gamma triple, and not a lookup table — each
of which was reachable from here and each of which would make "what the press
prints" a thing you have to read four numbers to predict.

**The nine act goldens are still failing, and they should now be regenerated.**
`story-press-flat.md` left them failing deliberately, because a regenerated
baseline would have baked a flat sheet in as the expected result. That reason
expires with this decision: the film now prints the city. Regenerating them is a
separate, reviewable step — the point of the rule was that the baseline follows
a decision rather than replacing one.

**`press-filter.ts` is untouched.** The 2D SVG-filter path is an affine
approximation that cannot take a logarithm, and it is already documented as
carrying a layer of the picture rather than the picture itself. Grading it would
mean approximating an approximation for no visible gain.
