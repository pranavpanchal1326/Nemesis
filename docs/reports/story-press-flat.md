# The film prints flat — §E6 over §E9.2's story run

- **Found by:** opening the landing and watching it, after a report that "no animation is working"
- **Status:** **closed** by [ADR-0061](../adr/0061-a-run-is-printed-at-an-exposure-and-the-story-run-needs-one.md), which took option (3) below — the run is graded at an exposure before the plates are solved, and the story run states 0.72. The cause as measured here is unchanged; only the decision was outstanding.
- **Reproduce:** `/developers/proof/story?t=0.12&seed=1` against `?stage=model` and `?stage=photograph`

---

## What a visitor sees

The landing renders a flat field of clay-coloured ink with a few soft blobs in
it. Scrolling moves the type and the model does not change. §E16's nine acts of
authored camera — the tracking shot, the push to ankle height, the lift and pan,
the pull back to the model at dusk — are all running, and none of them is
visible.

**Two things were wrong. One of them is fixed and it was the larger.**

---

## Fixed · the stage was never fixed to the viewport

`.walk__stage` was `position: fixed; inset: 0`, which reads as *the viewport*
and was not. `.press__text` carries `transform: translateZ(0)` — ADR-0038's
rasterisation guarantee, gate-tested in `press-text-exempt.spec.ts` — and **a
transformed ancestor becomes the containing block for every fixed descendant
inside it**. So `inset: 0` meant *the sheet*, and the sheet is the whole film:

    .walk__stage measured 1440 × 25 248 px

The clay rendered once into a box twenty-eight screens tall, and scrolling moved
the model up and away instead of moving the camera through it. Past the first
viewport there was nothing on screen to animate. No amount of camera work would
have shown through that.

**Now `position: sticky`**, with the viewport's height stated. Sticky holds for
exactly as long as `.walk` is on screen and releases when Act 9's receipts
arrive, which is what §E16 asks for anyway — and which the comment that used to
sit above this rule described as sticky's *flaw*. The note was right about
sticky's semantics and wrong about which one the film wanted.

---

## Fixed · Phase 19's ACES clause, in the place the press can see it

Phase 19's ship line asks for *"correct colour management (`SRGBColorSpace`),
ACES Filmic tone mapping"*. Neither was configured — three's default is
`NoToneMapping`.

Setting `renderer.toneMapping` does not fix it, and finding out why is the
useful part: the renderer's tone map runs on the way **out** of the pipeline,
and §E6's press sits **inside** it — the separation samples the lens, not the
framebuffer. An output-side tone map leaves the press reading raw linear
radiance. So the map is now a node in the graph, applied once to the photograph,
and the press, the `photograph` stage and the final frame all see the same
display-referred image. `renderer.outputColorSpace` is stated; `toneMapping`
stays `NoToneMapping` so nothing is mapped twice.

---

## Open · the story run cannot carry the clay

With the stage fixed and the frame tone-mapped, the film still prints flat. The
three stages separate the question, and the answer is unambiguous:

| Stage | What it shows |
|---|---|
| `?stage=model` | **The city, correctly**, at the film's own authored camera — blocks, streets, shading, pins |
| `?stage=photograph` | **The city, correctly**, through the lens: tilt-shift, vignette, gate weave |
| `?stage=print` (default) | **A flat field of brown** with the model invisible inside it |

So the world, the camera track, the scroll spine, the lens and the renderer are
all working. The press is where the city disappears.

**The measurement.** `separationRows()` solves each plate's row once, and the
coverage a plate carries is `clamp(dot(row, log(stock) − log(sample)), 0, 1)`.
For the clay body across its rendered range, on the two runs:

| Sample | Story run — brown / sunflower / aqua | Console run — black / federal blue |
|---|---|---|
| `#B98A78` (lit) | **0.58** / 0.01 / −0.01 | −0.57 / −0.41 |
| `#925F52` (body) | **0.95** / −0.01 / 0.01 | −0.39 / −0.50 |
| `#6B4238` (shadow) | **1.27** → clamped to 1 | −0.24 / −0.50 |

§E9.2 gives the film **brown + sunflower + aqua and no black plate**, so one
plate carries the entire model, and the clay's own colour *is* that plate's ink
(§E7.1: "the clay body IS `ink.riso-brown`"). Everything from the body tone
downward solves at or past full coverage and prints solid. The two chromatic
plates contribute nothing to a brown subject — their coverages are noise around
zero. What remains on screen is one saturated plate plus stage 4's ink-density
variation, which is exactly the "soft blobs on a flat field" a visitor sees.

Raising the exposure was tried and measured: 1.0 → 3.0 lightens the sheet and
does **not** recover the model, because the shadow end saturates at any exposure
that keeps the highlights on the paper.

### The decision this needs

Not a patch. Three honest options, and each costs something the blueprint
currently promises:

1. **Give the story run a dark plate.** The film prints black + brown +
   sunflower. Costs: §E9.2's table is authored art direction and §E2 records
   that *"the inks are not repainted — they are the premise of §E4"*. This is
   repainting one.
2. **Do not print the clay through the DOM-scale separation at all** — let the
   scene's own stage stay `photograph` on the story surface, and leave §E6's
   six stages to the sheet the type sits on. Costs: §E5's three-material law
   says one press runs over all three materials, and this makes the clay the
   exception.
3. **Grade the photograph into the run's range before separating** — a per-run
   lift/gamma authored beside the ink set, so a three-ink chromatic run gets a
   subject it can hold. Costs: a second art-direction knob, and a claim that the
   press prints what it is given stops being quite true.

My reading is that **(3) is the smallest true statement** — a printer choosing
an exposure for a run is a real thing printers do, and it leaves both §E9.2 and
§E5 intact. But it is an art-direction decision with a §E-level consequence, and
this repository's standard is that those are argued in an ADR rather than
guessed at by whoever noticed.

### What shipped with this report

- `?stage=model|photograph|print` on `/developers/proof/story`, which is how the
  three rows of the table above were produced. The clay proof has had it since
  M8; the film did not, and a frame that arrives as a flat wash cannot be
  attributed to the model, the lens or the press without it.
- The golden images for the nine acts are **left failing**. They photograph the
  film, the film is wrong, and a regenerated baseline would bake this defect in
  as the expected result — which is the one thing a golden image must never do.

---

## Also fixed while the film was open, and worth recording separately

These are composition, not colour. They were found the same way — by scrolling
the landing rather than by reading it.

**The film was twenty screens long, and the shortest act set that.** The cold
open is five per cent of the spine, one screen was the floor for a snap point,
and 1 / 0.05 = 20. Nine acts were spread over twenty viewports, most of them one
paragraph suspended in the middle of three empty ones. The multiplier is a token
now (`story.viewports`) and it is **10**; scroll and `t` stay linear in each
other, which is the property the sizing exists to preserve.

**The act copy is sticky.** It used to sit at the centre of its section, so a
three-screen act showed a screen of nothing, a screen of type, and a screen of
nothing. It now holds above centre for the whole act — which is the right
reading of §E16 anyway: an act is a beat the camera plays, and the caption
belongs to the beat rather than to one screen inside it.

**Act 9 printed the whole honesty table — eighty-one rows, 6 500 px.** The
landing spent more height on the table than on the film, and a reader scrolling
for the receipts arrived at a wall of rows. It now publishes the counts —
generated from the same `HONESTY_COUNTS` the table is built from, so it cannot
drift into a flattering summary — names all five statuses including CUT and
ROADMAP, and links to `/{tenant}/honesty`, which is the canonical, indexable,
deep-linkable home for it.

**The model now fills the frame.** `<ClayScene>`'s stage is `aspect-ratio:
16 / 10`, which is right inside a console panel and wrong behind a film: it left
the clay in the top-left sixty per cent of the viewport with an empty band under
it.

Together: **19.1 screens → 11.9**, and the empty stretches are gone.

The nine act goldens fail against these changes as well as against the flat
print. They stay failing for the reason above: the film is still wrong, and a
regenerated baseline would make it the expected result.
