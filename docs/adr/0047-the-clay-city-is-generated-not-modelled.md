# 0047 — The clay city is generated from the tenant's own origin, not modelled

- **Status:** Accepted
- **Date:** 2026-08-25
- **Owner:** PROD
- **Blueprint:** §E5, §E7.1, §E13, §E23, §E24 · §6 Principle #6, Principle #9

## Context

`docs/FRONTEND-PHASE-PLAN.md` §4 reserves this number and states the problem in
one sentence: *"the assets do not exist, and that is the largest risk in this
plan."* F9 ships the clay material and the city it is made of. The material is
code. The city, on every reference project of this kind, is a Blender kit.

There is no Blender artist on this build, and there will not be one. That is the
practical pressure, but it is not the argument — a kit could be bought. Three
properties this repository has already committed to are what actually decide it.

**§6 Principle #6 — zero-cost, self-hosted, offline-capable.** Phase 29's gate is
a clean checkout that boots air-gapped. A committed `.glb` kit is a binary that
nobody on the team can regenerate, and a checkout that depends on one is a single
asset loss away from unbuildable.

**§E24 — golden images at a fixed seed and camera.** A golden image is only
meaningful if the thing it photographs is reproducible. A modelled city is
reproducible by *copying a file*, which is reproducibility of the artefact and
not of the process; a generated city is reproducible by running the generator.

**§E5, and this is the load-bearing one.** *"Data is never decorative."* A
modelled city would tempt every future reader into believing the buildings mean
something — that this footprint is that building, that this height is that
storey count. They would not. NEMESIS has no cadastral source, publishes no
building-level data, and computes no number from geometry.

## Decision

**The clay city is generated at runtime from the tenant's projection origin, and
it is scenery.**

1. `src/clay/city-kit.ts` produces footprints deterministically from a seed
   derived from the projection origin, rounded to ~11 m. Pune's clay is Pune's
   clay on every machine and in every run; a re-published centroid that moves by
   a metre does not re-roll the city.
2. **The geometry is stated to be scenery, in source, next to the generator.**
   Buildings establish scale and give the tilt-shift something to be shallow
   against. Everything that carries meaning — pins, their glaze, their height —
   comes from events and is built in `pins.ts`, in a different file, on purpose.
3. The generator is capped (`DEFAULT_MAX_FOOTPRINTS`) so §E23's VRAM budget is
   enforced at the point geometry is *created*, not discovered at the point it is
   uploaded. A city that hits the cap is a smaller city, not a quadrant of one.
4. No `gltf-transform` → KTX2 pipeline is introduced. §4 proposed routing
   generated geometry through it for parity with a modelled kit; there is nothing
   to compress — one box geometry and one instanced draw call — and an asset
   pipeline with no assets in it is a build step that can only rot.

## Alternatives considered

**Buy or model a kit.** Rejected on the three properties above. The strongest
version of this argument is that a modelled city simply looks better, and that is
true; §E7.1's read is carried by the *material* — thumbprint normal, baked AO,
the rim term — and those apply to a generated box exactly as well as to a
sculpted one.

**Extrude the tenant's real zone boundaries.** Rejected, and this is the
alternative that took longest to reject because it sounds like the honest one.
Ward polygons are real data, and extruding them into buildings would render real
data as something it is not — a boundary is not a building. §E5's corollary cuts
both ways: data is never decorative, and decoration must never look like data.
Zone boundaries belong on the 2D path (F11's deck.gl layer), drawn as what they
are.

**OpenStreetMap building footprints.** Rejected. It is a network dependency
against Phase 29's air-gapped gate, it carries an attribution obligation onto
every surface that renders the map, and it would make the scenery *look*
authoritative — the §E3.3 failure, in three dimensions.

## Consequences

- The city is a model of a city rather than a survey of one. §E5 already says
  this is what the clay is; it is recorded here as a cost, not smuggled in as a
  feature.
- A tenant with no published zone centroid gets no origin, and therefore no
  city — the scene falls to the peer list, which is the correct outcome and not
  a degraded one.
- Golden images photograph a generator. A change to the distribution changes
  every Stage 2 and Stage 3 snapshot at once, which is loud, and that loudness is
  the property being bought.
- If an artist ever joins, `generateCity()` is one function behind one interface
  and a modelled kit can replace it without touching the material, the pins, the
  lens, or any test that asserts a budget.
