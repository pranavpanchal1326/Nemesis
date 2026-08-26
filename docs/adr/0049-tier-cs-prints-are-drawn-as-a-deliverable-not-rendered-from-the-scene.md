# 0049 — Tier C's prints are drawn as a deliverable, not rendered from the scene

- **Status:** Accepted — **recorded after the fact; see *Provenance* below**
- **Date:** 2026-08-26 (decision taken at F14, 2026-08-25)
- **Owner:** PROD
- **Blueprint:** §E13, §E3.2, §E16 · §E11.1
- **Consumed by:** F14 (`docs/FRONTEND-PHASE-PLAN.md` §4, where it was reserved)

## Provenance

**This ADR is written a stage late and its status line says so.** The
reservation in `docs/FRONTEND-PHASE-PLAN.md` §4 set the rule that each of the
four asset decisions would be written *before* the phase that consumed it. F14
shipped Tier C's nine prints and did not write this one. It is reconstructed at
F15 from what F14 actually built (`frontend/src/story/Storyboard.tsx`, whose
module note argues the decision at length) and from the completion note the
phase plan records for Stage 3.

That is a weaker provenance than an ADR written at the time, and it is stated
here rather than left for a reader to infer from the date. What it records is
not in dispute — the code and the phase plan agree, and both predate this file —
but the reasoning is reconstructed rather than contemporaneous.

## Context

§E13 gives Tier C one line and a demanding one:

> **C — storyboard.** `prefers-reduced-motion`, or no WebGL. Nine art-directed
> **riso prints**, scroll-snapped, same copy. **Visually continuous with Tier S
> because it is the same process.**

And §E3.2 raises the stakes: *"Every fallback in §E13 is art-directed and
graded, not generated. The reduced-motion path is what an accessibility audit
and a reduced-motion reader actually see, which makes it the most consequential
edit, not the least."*

`docs/FRONTEND-PHASE-PLAN.md` §4 proposed one reading of *"the same process"*:
**render the prints from Tier S at a fixed seed and camera, then review them.**
Its argument was that rendering from the same scene is the *stronger* reading
of that sentence, not a shortcut, and that it makes the prints regenerable
instead of nine binaries nobody can reproduce.

F14 took the opposite reading, and shipped it.

## Decision

**The nine prints are drawn — hand-composed vector frames in flat ink — and are
not captured from the 3D scene.**

*"The same process"* is satisfied by sharing the **implementation**, not by
sharing the pixels:

- every fill is a generated ink custom property from `design/tokens.json`;
- the sheet is the surface's own ink set;
- the frames print through the same `<Press>` as every other surface.

So the continuity §E13 asks for is a shared press and a shared palette rather
than a resemblance — which is a stronger claim than a screenshot, because a
screenshot resembles the scene and a shared press *is* the scene's process.

Three consequences were taken deliberately with it:

1. **The prints carry the same words**, resolved through the same locale keys
   the acts resolve, so a translation lands in both tiers at once and a
   reduced-motion reader is never reading a shorter product.
2. **The tier is a server component that hydrates nothing.** Somebody who has
   asked their operating system for reduced motion has said something;
   answering it with an animation-free page that still needs a renderer, a
   scroll hijack and 300 KB of film would be answering a different request.
3. **It is therefore also Tier D**, one rung down, with the pictures dropping
   out and the words staying.

## Alternatives considered

**Render from Tier S at a fixed seed and camera** — §4's own proposal.
Rejected in the doing. A still of a scroll film is a *degraded photograph* of
it: the camera is mid-move in most of the nine acts, the composition of a frame
grabbed at `t = 0.27` is whatever the rig happened to be doing, and the
tilt-shift and gate weave that read as cinema in motion read as blur in a still.
§E3.2's *"art-directed and graded, not generated"* is hard to satisfy with an
artefact whose composition nobody chose. The regenerability argument still
stands and is met a different way: these frames are **source**, not binaries, so
a clean checkout reproduces them exactly.

**Nine committed images.** Rejected on the rule ADR-0047 and ADR-0050 both
apply — the artefact is the source, and a committed binary is one nobody can
review in a diff.

**Reuse the acts' DOM with the canvas hidden.** Rejected: that is not a
storyboard, it is the film with its pictures missing, and §E13 asks for nine
prints rather than nine empty stages.

## Consequences

**Easy:** no binaries, no capture step, and the tier renders on the server —
which makes it the cheapest surface in the product and the most robust. The
§E3.2 review can reject a frame and the fix is a diff.

**Hard, and stated:** the frames are *illustrations of* the film rather than
*images from* it, so visual continuity is a claim about palette and press
rather than about geometry. A reviewer comparing Tier S and Tier C side by side
will see two renderings of one story, not one rendering at two qualities. That
is the reading of §E13 this decision takes, and somebody could reasonably read
the sentence the other way — which is precisely why it needed an ADR, and why
this one being late is worth recording rather than quietly backfilling.

## Revisit when

The film's composition changes enough that a print and its act are telling
different stories — at which point the prints are edited like any other
deliverable, in a diff, with the §E3.2 review that F14 already established.
