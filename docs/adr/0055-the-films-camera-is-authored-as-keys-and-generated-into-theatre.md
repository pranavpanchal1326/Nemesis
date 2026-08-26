# 0055 — The film's camera is authored as keys and generated into Theatre.js

- **Status:** Accepted
- **Date:** 2026-08-26
- **Owner:** PROD
- **Blueprint:** §E15, §E16, §E24, §E25 Phase 20 · `docs/FRONTEND-PHASE-PLAN.md` F12

## Context

§E15 names the film's stack, and it is a locked section: *"`lenis` · `gsap` ·
`@theatre/core` + `@theatre/studio` (dev-only)"*. F12 ships the spine and the
camera, so the question is not *whether* Theatre.js is used but *what authors
the thing it plays*.

Theatre.js has one authoring path: its studio writes a **project-state JSON**,
the application ships that file, and `getProject(name, { state })` replays it.
The file is a flat map of generated keyframe ids, positions and raw bezier
handle pairs. It is correct, it is machine-written, and it is unreviewable —
there is nothing in it that says which *shot* a number belongs to.

Two commitments this repository has already made collide with that.

**§E24 — golden images at a fixed seed and camera.** F12's gate is a golden
image per act. When one moves, somebody has to find out which camera move
changed and decide whether the change was intended. A diff of
`"kf_7hd2": { "value": 1400 }` does not support that conversation.

**Every other generated artefact in this repository already works the other
way.** `design/tokens.json` → CSS custom properties and TSL constants;
`openapi.json` → the typed client; the two blueprints → §44's honesty table.
One authored source, a generator, and a drift check that fails CI. A
studio-written JSON committed as *source* would be the only artefact in the tree
whose authored form is machine output.

## Decision

**`src/story/camera-keys.ts` is the camera. The Theatre.js project state is
generated from it and drift-checked, exactly like every other generated artefact
here.**

1. The keys are TypeScript: fourteen poses in ENU metres, each with the §E16
   shot it belongs to written beside it (`"merge · pulls back to dusk"`), and
   derived where derivation is honest — Act 1's tracking distance comes from
   `SPINE.walkMetres`, Act 6's altitude from `WORLD.camera.heightMetres`, so the
   film arrives at exactly the shot the console and the public map already draw.
2. `scripts/generate-camera-track.ts` writes
   `src/story/generated/walk-camera.json`. The segment easing is read from
   `design/tokens.json` through `controlsOf("cine")`, so the film's camera and
   the product's chrome ease on the same curve by construction rather than by
   two people typing the same four numbers.
3. `npm run camera:check` regenerates and diffs. It is in `nem web-check` and in
   CI beside `tokens:check` and `api:types:check`.
4. **Theatre.js is still the runtime.** `story/camera.ts` builds the project,
   sets `sequence.position` from the spine's `t` and reads the eight numbers
   back; Theatre does the interpolation, the keyframe model and the bezier
   segments. `tests/story-camera.test.ts` asserts that loading the generated
   state reproduces every authored pose to a millimetre, in Node, with no DOM.
5. **Studio is an inspector, not a round trip.** It is dev-only and opt-in
   (`?studio=1`). It will show and scrub the real sequence; it cannot write back
   to the keys file, and a move found in studio is transcribed into
   `camera-keys.ts` by hand. Claiming a round trip that does not exist would be
   worse than not having one.

## Consequences

**Good.** A camera move is reviewable in a pull request. A golden image that
moves names a shot rather than a keyframe id. The film's easing cannot drift
from the product's easing. The camera is unit-testable without a browser, which
is why `cameraTrack()` carries no `typeof window` guard.

**Bad, and accepted.** An artist who knows Theatre's studio cannot save from it.
That is a real cost and it is the same cost ADR-0047 accepted for the city: this
build has no artist, and an authoring workflow nobody on the team can run is a
workflow that decays into a committed binary.

**The trigger to revisit.** An artist joins, or the camera outgrows fourteen
keys — a per-act sub-sequence, a second animated object, an easing that varies
by segment. At that point the honest move is to make studio's export the source
and add a *reader* that renders it back into named shots, rather than to keep
hand-transcribing.

## Alternatives considered

**Ship studio's JSON as source.** The straightforward reading of §E15, and
rejected on reviewability alone. It also fails the plan's own standard for
generated files: nothing in the tree is authored as machine output.

**Drop Theatre.js and interpolate the keys directly.** Forty lines, no
dependency, and it would have been defensible — but §E15 is locked, the
sequencer is genuinely better than a hand-rolled lerp at exactly the thing this
film does most (bezier segments between poses at a scrubbable position), and
"we replaced a named dependency with our own code" is precisely the unrecorded
drift `docs/adr/` exists to prevent. Recording the deviation we *did* take is
cheaper than taking the larger one.

**Author in JSON by hand.** Neither reviewable nor typed. `camera-keys.ts` gets
`CameraPose` checked by the compiler and the shot names checked by a reader.
