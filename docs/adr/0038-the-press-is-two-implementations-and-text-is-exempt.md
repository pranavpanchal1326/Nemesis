# 0038 — The press is one token source in two implementations, and text is exempt from it

- **Status:** Accepted
- **Date:** 2026-08-24
- **Owner:** PROD
- **Blueprint:** §19.3 · §E4, §E6, §E24

## Context

The art direction (§E4) requires every frame in the product — the 3D model city
and the flat DOM record alike — to look risograph-printed: separated into two or
three spot inks, halftoned, misregistered by a fraction of a pixel, composited
onto paper.

The product has two rendering surfaces with nothing in common. The console is
dense DOM text that must stay selectable, findable, screen-readable, and
printable. The map is a canvas. Any approach that treats them as one surface
sacrifices one of them.

There is also a trap inside the requirement itself. "Every frame looks printed"
naively includes text — and halftoned 13px type in an operator console is not a
style, it is a defect. It fails contrast, it fails print, and it fails anyone
reading a column of costs for nine hours.

## Decision

**One token source, two implementations, and text composites unprocessed.**

- Halftone geometry, ink vectors, misregistration offsets, density-noise parameters, and the ink sets per surface live in the token file (§E24).
- That file generates **both** a TSL post-processing pass for canvas surfaces and a CSS/SVG layer for DOM surfaces. Neither implementation carries a literal.
- **Text renders in a separate compositing layer at 100% ink density: no halftone, no channel offset, no density noise.** This is also what a real risograph does — type is solid ink, images are screened.

Enforced by a test asserting that **text layers are byte-identical with the press
enabled and disabled** (§E25, Phase 18 gate). Any change that lets the press
touch a glyph fails the build.

## Alternatives considered

**Render the entire UI through WebGL so there is only one implementation.**
Rejected outright. It destroys text selection, find-in-page, screen-reader
access, browser zoom, and printing — and §E19 establishes that officers print,
while §E22 sets WCAG 2.2 AA as a floor. A single implementation is not worth any
of those.

**CSS filters only; leave the 3D surface unprocessed.** Rejected because the
unification argument in §E4.2 is the entire justification for combining MITTI and
RISO. If the map does not share the press, the drawn, the rendered, and the
printed stop cohering and the direction reduces to two unrelated styles adjacent
to each other.

**Apply the press to text as well, for purity.** Rejected as described above.
Purity that produces illegible data is not purity, and §E3.5 settles it: where the
cinematic surface and the operator surface conflict, the operator wins.

**Bake the print treatment into assets in Blender rather than doing it at
runtime.** Rejected because misregistration is animated at 12 Hz (§E6.1 stage 3)
and because the press's quality dial is what the adaptive-quality manager turns
first (§E6.4). A baked treatment cannot degrade, and degradation is the property
that makes the fallback ladder beautiful instead of apologetic.

## Consequences

**Easy:** 2D and 3D cohere by construction; the fallback ladder becomes a print
run rather than a downgrade; the whole aesthetic has a single quality dial the
performance manager can turn before it touches frame rate.

**Hard:** two implementations of one effect must be kept in visual sync forever.
This is the cost we are accepting, and golden-image regression across both
surfaces (§E24) is the control that makes it survivable rather than the thing
that makes it fine.

**Commits us to:** never introducing a third rendering surface without a third
implementation of the press, and to treating the token file as the only place any
print parameter may be written.

## Revisit when

The two implementations drift visually in a way the shared tokens cannot
reconcile — which would mean the abstraction is in the wrong place, not that the
sync discipline failed.
