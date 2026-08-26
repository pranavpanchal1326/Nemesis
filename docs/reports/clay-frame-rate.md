# The clay engine — measured frame rate, and a gate this hardware refuses

**Date:** 2026-08-26 · **Phase:** 19 (Track E, F11) · **Owner:** PROD
**Reproduce:** `npx playwright test tests/clay.spec.ts` for the budgets that CI asserts;
the frame-rate figures below come from driving `/developers/proof/clay` in a
**headed** Chromium so that a real adapter is used (see *Method*).

Phase 19's exit gate opens with the hardest number in the frontend plan:

> **60 fps sustained with 5 000 instanced pins plus extruded buildings on this
> laptop, measured, with Ollama running.**

`docs/FRONTEND-PHASE-PLAN.md` §F11 already flags it as *"the one gate in this
plan that hardware can simply refuse. If it fails after honest optimisation, the
answer is a recorded deviation with a measured number — not a quietly relaxed
budget (Law 4)."*

**It failed. This is the number.**

---

## Result

| Instanced pins | Median fps | Worst sampled second |
|---:|---:|---:|
| 0 | **144** (display cap) | 144 |
| 1 000 | **63–107** | 63 |
| 2 000 | **48.7** | 41.7 |
| 3 000 | **38.0** | 35.6 |
| **5 000** | **~21** | 8.9 |

**60 fps holds to roughly 1 500 pins on this machine. The gate asks for 5 000,
so the shortfall is about 2.9× in load, or 2.9× in frame time.**

Everything else in the gate passes, and passes with room:

| Clause | Budget | Measured at 5 000 pins | |
|---|---:|---:|---|
| Draw calls (`renderer.info`) | 32 | **14–17** | ✅ |
| VRAM (`renderer.info.memory.total`) | 512 MB | **13.8–21.6 MB** | ✅ |
| Backend | — | WebGPU (Tier S) on the laptop, WebGL 2 (Tier A) headless | ✅ |
| Context loss recovers without a reload | — | asserted in `tests/clay.spec.ts` | ✅ |
| Accessible list present and synchronised in every tier | — | asserted per tier | ✅ |

---

## Method

| | |
|---|---|
| Machine | Windows 11, **AMD Radeon 780M** integrated graphics (`ANGLE … Direct3D11`) |
| Browser | Chromium (Playwright), **headed**, 1280 × 800, `reducedMotion: no-preference` |
| Backend taken | WebGPU (`navigator.gpu` adapter present) → Tier S |
| Surface | `/developers/proof/clay?pins=N&seed=1&at=2026-06-21T06:30:00Z` |
| Sampling | The scene publishes a frame count over each wall-clock second (`SceneSample`). The first three samples are discarded — shader compilation and the first buffer upload land in them, and the gate's word is *sustained* — and the median of the remaining eleven is reported. |
| Ollama | **Not running.** See *What this measurement does not include*. |

**The frame rate is deliberately not asserted in CI.** A headless runner uses a
software rasteriser; at 5 000 pins it reports about 1 fps, and a threshold
lowered until that passes would be a threshold that measures nothing. CI asserts
the two clauses that are properties of the *scene* — draw calls and VRAM, both
read from `renderer.info` — and prints the frame rate to the log. That division
is written into `tests/clay.spec.ts`'s header.

---

## Where the time goes

The proof route can stop the frame after any of §E7's three stages
(`?stage=model|photograph|print`), which localises the cost precisely:

| Pins | `model` (scene only) | `print` (scene + lens + press) |
|---:|---:|---:|
| 0 | 144 | 144 |
| 1 000 | 108 | 106 |
| 5 000 | 26 | 24 |

**The lens and the press are free at this resolution.** With no pins the whole
chain runs at the display's cap; with 5 000 pins the model alone is already at
26 fps and adding the entire post chain costs about two. The generated city —
~4 800 extruded footprints in one instanced draw call — costs nothing
measurable either: the 0-pin row includes it.

So the cost is the pins, and it is close to linear in their number. Five
thousand 12-segment cylinders is ~180 000 triangles, which is not a triangle
problem on this part; the profile is consistent with per-instance fragment work
over a dense, heavily overlapping footprint at a 52° camera pitch.

---

## Two optimisations tried, one kept, neither sufficient

**1 · Resolve the lens into a render target instead of inlining it per plate.**
§E6.1 stage 3 has each of three plates sample the photograph at its own offset,
so the depth-of-field kernel runs three times — 21 texture fetches per pixel
instead of 7. Resolving it once into a half-float target should have been
strictly cheaper.

> Measured: **~21 fps inlined, ~2 fps resolved.** The extra `setRenderTarget`
> between two render pipelines costs an order of magnitude more on this backend
> than the taps it saves.

**Reverted.** The inlined arrangement is in `scene.ts` with this number beside
it, so the next person to have the same good idea has the result already.

**2 · Stop re-uploading instance buffers on steps where nothing changed.**
`pinInstances()` is a pure function of (entities, projection, step), and the
step only enters through the Settle — so outside a Settle window the buffers
being uploaded twelve times a second are identical.

> Measured: **no change**, ~21 fps either way. The bottleneck is the drawing,
> not the bus.

**Kept**, because it is nonetheless right — traffic that carries no news is
traffic — and because the arithmetic differs on a machine with a slower bus.
It is documented as *not* having moved this number.

---

## What this measurement does not include

**Ollama was not running.** The gate says *"with Ollama running"*, and ADR-0002
puts the Investigation Agent on this same GPU by design. The honest reading of
this report is therefore that **21 fps is the optimistic figure** and the gate's
own conditions would produce a lower one. Re-measuring under load is the first
thing to do when the number is revisited; it was not done here because a
contended measurement on top of a failing one adds a second unknown to a result
that is already unambiguous.

---

## Disposition

**Recorded as a deviation, not closed.** `BUDGET.fps` and `BUDGET.pins` in
`src/design/tokens.json` are unchanged at 60 and 5 000: the budget is what the
product wants, and this document is what the hardware gave. Relaxing the token
would make the shortfall invisible to the next reader, which is the specific
failure Law 4 names.

Three routes are open and none has been taken:

1. **Reduce the pin geometry.** A 12-segment cylinder per pin is the obvious
   suspect and the cheapest experiment: an impostor — one camera-facing quad
   with the same clay material — would cut the fragment work sharply and would
   still carry severity as height, glaze and grain. It changes the silhouette,
   so it is a design decision and not only an engineering one.
2. **Cull.** Everything in the scene has `frustumCulled = false` today, which is
   deliberate at this stage (`entities.ts` refuses to cull *entities*, because a
   canvas that culls and a list that does not is the desynchronisation the whole
   layer is built to prevent). Frustum culling the *instances* while keeping
   every entity in the peer list is compatible with that rule and has not been
   implemented.
3. **Accept the ladder.** The adaptive quality manager already exists and works:
   at 21 fps it walks down §E23's rungs, and a machine that cannot hold 60 fps
   at five thousand pins gets the lite tier, which is a designed edit and not a
   failure state. The gate's number would then describe the *ceiling* rather
   than the floor.

The measurement is what decides between them, and this is the measurement.
