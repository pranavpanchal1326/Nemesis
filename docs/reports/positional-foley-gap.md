# Positional foley is built, tested, and wired to nothing

**Phase:** F16 · **Milestone:** M10.2 · **Date:** 2026-08-26
**Clause:** §E12 — *"every pin carries a quiet loop audible near the camera …
an operator can hear where the problems are before seeing them"*
**Status:** **implemented, unmounted.** No surface plays it.

---

## The clause, and why it matters more than the other four

§E12 has five bullets and four of them are texture. This one is not:

> **Positional foley** — every pin carries a quiet loop audible near the camera.
> Water in a pothole. A buzzing streetlight. A leaking main. **An operator can
> hear where the problems are before seeing them** — an affordance, not
> decoration, and therefore compliant with §6 Principle #9.

That last clause is the whole argument for having sound in this product at all.
§6 Principle #9 admits a sensory channel when it carries information; the
ambient bed and the interaction foley are admitted as *texture around* an
interface, and this one is admitted as **an interface**. It is therefore the one
sound cue whose absence is a missing capability rather than a missing flourish.

## Why it cannot be mounted

**The only positioned data the frontend receives is wards.**

`server/clay-data.ts` reads a tenant's published zones and
`clay/entities.ts::entitiesFromZones()` turns each into a `ClayEntity`:

| `ClayEntity` field | Source | Carries a defect category? |
|---|---|---|
| `label` | `zone.zoneName` — *"Kothrud"*, *"Ward 14"* | No. It is a place name |
| `point` | `zone.centroid` | No |
| `reports` | `zone.totalReports` | No. A count, not a kind |
| `severityScore` | **`null`** | — and see below |

That `null` is not an oversight, and the comment beside it in `entities.ts` is
the precedent this report follows:

> §13.1's bands are governed data and no endpoint publishes a per-place
> severity. Inventing one from open-report counts would be a decoration that
> looks like a measurement, which §E3.3 forbids by name.

The same sentence applies one word changed. Choosing *water* or *electrical* or
*waste* from a ward's **name** would be a sound asserting a fault type that no
classifier produced, at a location where nobody reported it — a decoration that
sounds like a measurement, on the one cue admitted specifically because it is
not decoration.

## What was refused

**Matching `loopForCategory` against the ward name.** Two lines, and it would
have made the feature "work": `voicesFor(entities.map(e => ({...e, category:
e.label})), listener)`. In practice a ward called *Water Works Road* would drip
and its neighbour would not, which is worse than silence — it is a false
positive an operator would learn to trust.

**A default loop for every pin.** §E12's own words rule it out (*"an
affordance, not decoration"*), and §E3.4 finishes the argument: a sound that
fires for everything means nothing.

**Synthesising a category from report counts.** Same failure as the severity
one `entities.ts` already refused, and refused for the same reason.

## What exists

Everything except the data:

- `src/sound/cues.ts` — three named loops (`water`, `electrical`, `waste`), each
  with its own timbre and its own stated meaning.
- `src/sound/world-sound.ts` — `loopForCategory()` matching the **tenant's own**
  category words rather than a closed list; `voicesFor()` computing which
  entities are audible, at what gain and what pan, capped at
  `SOUND.positional.maxVoices` and keeping the *nearest*; `reconcileVoices()`
  starting and stopping loops without restarting one that is already playing.
- `tests/sound.test.ts` — nine assertions over that mechanism: nearer is louder,
  east pans right, beyond the radius is silent, the cap keeps the nearest, an
  unmatched category is silent, and a moving camera does not restart a voice.

## What would close it

**One categorised, positioned read.** The contract is already most of the way
there: `ComplaintResponse` carries `category`, `latitude` and `longitude`. What
does not exist is a *published, tenant-scoped list* of open defects with those
three fields, at a granularity finer than a ward — the same shape the clay layer
would need to draw a pin per defect rather than a pin per ward.

Note that this is the **second** clause in Track E to want that read: §E28's
live-map row draws ward centroids for the same reason. So this is one backend
change serving two surfaces, not a feature request for a sound effect.

Until it exists, `world-sound.ts` has no caller and says so in its own docstring,
and this file is what a reviewer finds when they ask why the console is quiet.

## Where this is recorded

- `frontend/src/sound/world-sound.ts` — the module note.
- `docs/FRONTEND-EXECUTION-PLAN.md` — M10's row, and §E28.
- `docs/FRONTEND-PHASE-PLAN.md` — Stage 4's completion note.
