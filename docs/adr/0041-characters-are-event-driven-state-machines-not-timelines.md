# 0041 — Characters are event-driven state machines, not timelines

- **Status:** Accepted
- **Date:** 2026-08-24
- **Owner:** PROD
- **Blueprint:** §6 (Principle #9), §19.1, §19.2, §20.3 · §E5, §E8

## Context

§6 Principle #9 states that every 3D and visual element must map to a real
pipeline event, and §19.2 sharpens it: the cluster-merge is the demo's core
moment **because it is wired to a live WebSocket event and not to a scripted
animation triggered by a button.** PHASES makes it a gate — a scene that can only
be fired by a button fails Phase 20.

A character animation is the easiest place in the entire product to violate that,
because the natural form of a character animation is a timeline, and the natural
way to use a timeline is to play it when something happens. `play()` on an event
is indistinguishable, in the code and to the viewer, from `play()` on a click.
The principle survives only if the character has no timeline to play.

## Decision

**Characters are Rive state machines whose inputs are set from the event store.**

The application never plays an animation. It sets named inputs — `walking`,
`shoulders_drop`, `relief` — and the machine decides what to transition to. Those
inputs are written by the same Zustand store the WebSocket feeds (§E14.2), so a
character pose is a *view over the event stream* in the same sense a map pin is.

Input names are a contract between the `.riv` file and the application, listed in
§E8.1 and covered by a test that asserts every declared input exists in the
loaded artefact.

## Alternatives considered

**Lottie.** Rejected. Its model is a timeline, and playing a timeline on an event
is exactly the shape §19.2 warns against — the code reads the same whether the
trigger is a pipeline event or a button, so the gate cannot be enforced by
inspection. dotLottie state machines exist and are newer and less proven; the
ecosystem advantage Lottie has is an After Effects workflow, which this project
does not have and does not want.

**Sprite sheets, hand-drawn frame by frame.** The cheapest option, and it fits
the 12 fps stepped clock (§E7.2) perfectly. Rejected because state blending is
impossible and the asset count is combinatorial — every state pair needs its own
transition strip, and the character has eight states.

**A rigged 3D character in the clay world.** Rejected on the three-material law
(§E5): people are never 3D. That rule exists so the human is the only drawn thing
in a sculpted world, which is what makes the composition read. It also walks
straight into the uncanny valley and the representation problem §E8 avoids by
keeping the figure faceless.

**CSS/SVG animation driven by class changes.** Viable for micro-states and used
for exactly that. Rejected for the character because eight states with blended
transitions in CSS is a state machine written badly in a language that has no
state machines.

## Consequences

**Easy:** the character passes the Phase 20 gate by construction — there is no
`play()` call for a button to make. Idle CPU approaches zero, because a machine
with no changing input has no ticker to run. A designer can re-author the
`.riv` — add states, retime transitions — and drop it in with no code change, as
long as the input names hold.

**Hard:** commits us to a binary format and its editor, which is a dependency on
a tool as well as a runtime. Input names become an interface, and renaming one is
a breaking change to an artefact that is not type-checked by the compiler — which
is why the existence test is not optional.

**Commits us to:** the rule that any future character or animated illustration
enters through the same door. An animation that cannot be expressed as inputs
over a state machine is an animation that is not driven by the system, and §6
Principle #9 already says what that is.

## Revisit when

An input name must change, which is a contract break and needs the same care as an
API rename; or the Rive runtime's bundle cost stops being justified by the number
of animated surfaces actually shipping.
