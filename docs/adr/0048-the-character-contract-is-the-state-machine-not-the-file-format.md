# 0048 — The character contract is the state machine, not the file format

- **Status:** Accepted
- **Date:** 2026-08-26
- **Owner:** PROD
- **Blueprint:** §E5, §E8, §E8.1, §E8.2, §E13 · §6 (Principle #6, #9)
- **Amends:** ADR-0041 (kept; this decides the *renderer*, not the behaviour)
- **Consumed by:** F15 (`docs/FRONTEND-PHASE-PLAN.md` §4, where it was reserved)

## Context

ADR-0041 settled what a character **is**: a state machine whose named inputs are
written from the event store, never a timeline something calls `play()` on. That
decision is about behaviour and it stands unchanged.

It also named Rive as the runtime, and Rive's authoring artefact is a `.riv`
file made in Rive's editor. `docs/FRONTEND-PHASE-PLAN.md` §4 states the problem
plainly: **there is no Rive designer on this project, and no `.riv` file exists.**
The same paragraph names three other asset classes in the same position and sets
the rule that governs all four — *a phase that discovers its input does not exist
on the morning it starts is a phase that stalls.*

So F15 opens with a choice that is not about art direction: either the four
figures of §E8.2 wait for an illustrator who is not coming, or the character
layer ships without the file format ADR-0041 assumed.

## Decision

**§E8.1 specifies named inputs and named states. It does not specify `.riv`.**
The contract is therefore the state machine, and the file format is a renderer
behind it.

Concretely:

1. `src/ink/machine.ts` declares §E8.1's eight inputs and eight states **by
   their exact blueprint names** — `walking`, `stopped`, `looking_down`,
   `raise_phone`, `shoulders_drop`, `shutter`, `relief`, `disappointed` — and a
   transition table. It is a pure reducer: no clock, no canvas, no React.
2. `src/ink/figures.ts` turns `(state, phase)` into a **posture**, and a posture
   into ink strokes. A pure function of the machine's current state and the
   12 fps stepped clock's frame number (§E7.2), evaluated **on twos**.
3. `src/ink/draw.ts` puts those strokes on a 2D canvas: a varying-weight line in
   `riso-black`, and one warm `riso-brown` fill offset by the press's own
   misregistration so it does not quite register with the line (§E8, §E6.1
   stage 3).
4. The day a `.riv` exists, it enters at step 2 and 3 — as a second renderer
   behind the same machine — and nothing above it changes.

**Snake-case input names are deliberate.** They are §E8.1's names, character for
character, because they are a contract with a document and with a future
artefact, not local variables. The grep that audits this layer greps for those
words.

## Alternatives considered

**Wait for a `.riv`.** Rejected. It blocks F15, F16 and F17 on an asset with no
owner and no date, and §E8.2's Field Hand is a *component of the offline field
app*, not decoration — F17 would ship an empty state where a figure was
specified.

**Ship no characters and mark §E8 unbuilt.** Honest, and it was the fallback if
this could not be done well. Rejected because the cost is not evenly spread:
the character layer is the only place in the product where the *citizen* is
represented at all, and a civic system whose interface contains no people is
making a statement it did not intend to make.

**A sprite sheet drawn frame by frame.** ADR-0041 already rejected this for
combinatorial asset count, and every word of that rejection still applies. Note
that what is shipped here is *not* that: a posture is computed, not indexed, so
there is no transition strip per state pair.

**SVG paths per state, animated in CSS.** Tempting — it is the cheapest path to
a nice line. Rejected on the §E11.1 audit: `story-motions.test.ts` reads every
stylesheet and fails on a duration or curve that is not a token, and eight
states of blended figure animation in CSS would either flood that audit with
exemptions or hide the animation from it. It also loses the stepped clock: CSS
animates smoothly and this world animates on twos.

**Three.js, in the clay scene.** Rejected by §E5's three-material law, exactly as
ADR-0041 rejected it. People are ink. They are never clay.

## Consequences

**Easy:** no binary asset, no editor dependency, no licence to audit, and the
figures regenerate from source on a clean checkout — the same property ADR-0047
bought for the city. The Phase 20 gate is satisfied by construction, because
there is still no `play()` for a button to call. Idle cost is one canvas draw
per two frames of a 12 fps clock, and zero while the machine's state is
unchanged.

**Hard, and stated rather than dressed up:** **the drawing is cruder than an
illustrator's.** §E8 asks for *"varying line weight"* and a figure that reads as
"a college student, an aunty, a delivery rider, anyone" — a computed posture
gets the weight variation and the anonymity, and does not get the character an
artist's hand would put in the line. This is a deviation from §E8's stated
quality bar, it is visible on the marketing surface, and it is the deviation in
Stage 4 most likely to be judged insufficient. It is recorded in §E28 and in
§44 rather than only here.

**Commits us to:** the input names as an interface. Renaming one is a breaking
change to §E8.1, to this module, and to any `.riv` that later implements it —
the same commitment ADR-0041 already made, now with a compiler that can check
one half of it.

## Revisit when

A `.riv` exists — at which point the decision is not reversed, it is *exercised*:
the file becomes a renderer behind the same machine, and the state and input
names it must declare are already written down in two places.
