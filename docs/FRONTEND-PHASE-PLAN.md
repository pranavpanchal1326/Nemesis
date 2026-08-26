# NEMESIS — Track E Phase Plan · F1 through F18

**What this document is.** `docs/FRONTEND-EXECUTION-PLAN.md` owns the *milestones*
— what Track E builds and why, in thirteen steps from M0 to M12. Seven of those
are done. This document owns the *execution* of the six that are not, broken into
**eighteen phases that can each be finished, gated and stopped at**.

**Why a second document rather than more rows in the first.** M7 is thirteen
screens. M8 is a rendering engine. M9 is a nine-act film. Each is a milestone-sized
claim and none of them is a unit of work anybody can start on a Tuesday and finish.
A milestone answers *is this done?*; a phase answers *what do I do next, and how
will I know it worked?* Those are different questions and conflating them is how
a plan becomes a wish.

**The two documents cannot be allowed to drift**, which is the standing objection
to any second planning artefact in this repository. The rule that prevents it:

> **Every ship line in M7–M12, and every open row in the outstanding register
> (Group A and Group C), is claimed by exactly one phase below.** A line claimed
> by none is unplanned work; a line claimed by two is an ownership dispute.
> `scripts/check_phase_coverage.py` asserts this in CI and lands in **F1**.

Until that script exists this rule is a promise, and it is written here as a
promise rather than as a fact — which is the same distinction §E24's "not wired"
chip makes about screens.

---

## 0. What is already true

Established by audit against the blueprint on 2026-08-25, not from memory.

| | |
|---|---|
| Milestones done | **M0–M9** (10 of 13) |
| §E28 capabilities | M8 moved *Live map, instanced pins* to **REAL / REAL** and finished the §E26 contract row |
| §E5 materials | **PAPER** partial · **CLAY** shipped at M8 · **INK** does not exist — Stage 4 owns it |
| Register debt inside "done" milestones | **A9, A12, A15, A16** open — F1 closed A8 and A13; F2 closed A1, A2, A10 and A11; F3 closed A14; **F8 closed A7** |
| Backend the frontend needs | **C6** open — C7 and C8 landed with ADR-0052; **a new one is named below** |
| Stack for M10 | **none of it installed** — deliberate; M9's four (`lenis`, `gsap`, `@theatre/core`, `@theatre/studio`) landed with F12 |
| Assets for M10 | **none of them exist** — and this is still the largest risk in the plan (§4) |

The clay exists and the film runs on it. The ink does not, and Stage 4 is what
needs it: F15's four figures are the last §E5 material.

> **Stage 0 is done.** F1 and F2 have both landed, and the register rows above
> are struck out in `FRONTEND-EXECUTION-PLAN.md` rather than only here. Two
> things were argued rather than patched on the way through, and both are
> recorded where a reader will meet them rather than in a commit message:
> **ADR-0051** (the memory budget is a checked artefact) and **ADR-0053** (a
> fallback face is adjusted only by what this repository can measure — which is
> why A1 closes with three of its four named descriptors). F2 also found that
> Phase 18's locale clause was *unmeetable* rather than unmet: nothing could add
> a locale to a tenant that already existed. `PUT /control-plane/tenants/{slug}/locales`
> is the door that clause needed, and `nem gate-phase18-locale` walks through it.

> **Stage 1 is done.** F3–F7 have landed and M7 is closed; the milestone's own
> row in `FRONTEND-EXECUTION-PLAN.md` carries the gate and the defect list. Two
> things are worth carrying forward into Stage 2 rather than leaving in a
> commit:
>
> **F7 shipped eight of its nine screens and refused the ninth, deliberately.**
> The ship line reads *"SLA countdowns (12)"*, and the command view does not
> render one. It carries real queue figures, and a fixture countdown standing
> beside a measured backlog would put a measurement and a decoration in one
> strip with nothing on screen saying which is which — the §E3.3 failure, on the
> screen whose whole job is to say what to do first. The breach line renders its
> Phase 12 chip and the sentence it *will* say instead. This is a deviation from
> the plan, taken on the plan's own reasoning, and §E28's row says so in the
> table a reader actually meets.
>
> **ADR-0054** was written on the way through: `worker-ml`'s SIGKILL loop is
> billiard's four-second `worker_proc_alive_timeout`, not the out-of-memory kill
> ADR-0051 diagnosed. ADR-0051's budget work stands and is amended in place
> rather than superseded. It matters to Stage 2 because F8's gate — 60 fps with
> the stack running — is measured on the same machine, under the same load that
> made this intermittent.


> **Stage 2 is done, and one clause of its gate is a recorded deviation.**
> F8–F11 have landed and M8 is closed. `<ClayScene>` is the last §E26 contract
> (A7), and **ADR-0047** — the clay city is generated from the tenant's own
> origin, not modelled — was written before the phase that consumes it, as §4
> asks. Five things are worth carrying forward rather than leaving in a commit.
>
> **The frame-rate clause failed, and the number is published.** 60 fps with
> 5 000 instanced pins is not reachable on this laptop's Radeon 780M: it
> measures ~21 fps at 5 000, 48.7 at 2 000, and holds 60 to roughly 1 500.
> Every other clause of the Phase 19 gate passes with room — 14–17 draw calls
> against a budget of 32, 14–22 MB against 512, context loss recovering without
> a reload, both backends photographed. `BUDGET.fps` and `BUDGET.pins` are
> **unchanged**, because a relaxed token is a shortfall the next reader cannot
> see. [`docs/reports/clay-frame-rate.md`](reports/clay-frame-rate.md) has the
> method, the scaling curve, the two optimisations tried (one reverted with its
> number) and the three routes still open.
>
> **The gate caught four real defects, which is the argument for having it.**
> Each is fixed and each has its reason in the source rather than here:
>
> * **Headless Chromium reports `prefers-reduced-motion: reduce` by default.**
>   Every browser test was silently running at Tier C. The fallback ladder
>   looked exercised and the clay had never once been rendered in CI.
> * **`tierFor(rung, null)` answers "C" before a renderer exists** — correctly,
>   and catastrophically for a host that used it to decide whether to *build*
>   one. `rungRendersClay()` now separates "will this device try" from "which
>   backend did it take", which is ADR-0037's division applied one step earlier.
> * **The press printed a blank sheet.** Its ink separation was
>   `1 − (source · ink) / (ink · ink)`, which only lays ink where the source is
>   darker than the ink itself — so against `riso-black` a mid-tone pixel scored
>   thirteen and nothing printed. Overprint is multiplicative, so the separation
>   now solves least-squares in absorbance space (`separationRows()`). It was
>   invisible on the 2D surfaces, where screened overlays carry the picture, and
>   total in 3D, where the separation *is* the picture.
> * **The light table's ground is the room, not the paper** (§E9.3). A 3D press
>   handed `mitti-950` as its sheet multiplies three inks onto near-black. Ink
>   sets now carry `sheet` beside `stock`; they differ on exactly one surface.
>
> **A new backend gap, named where the frontend met it.** `ZoneSpec.boundary`
> exists on the control plane's **write** side and no read endpoint publishes
> it, so the 2D path (`FlatMap.tsx`) can draw a tenant's places but not its
> ward boundaries. ADR-0047 argues boundaries belong there, drawn as
> boundaries; until a read exists the surface draws none rather than extruding
> something else into their place. **Owned by the backend**, not by F12–F18.
>
> **The weather is the SLA engine's own answer.** §E7.4 requires the model's
> rain and the contractor's deadline to be *the same fact*, and the only way to
> make that true rather than correlated is to ask the thing that computes the
> deadline: `POST /control-plane/calendars/preview-deadline` is read-only and
> token-free, and its `adjustments` map is the seasonal windows in force right
> now, in the tenant's own words. No weather API, no second source, and it
> works with the network unplugged.


> **Stage 3 is done, and one clause of its gate is honestly unexercised.**
> F12–F14 have landed and M9 is closed. The film is nine acts on one damped
> `t ∈ [0,1]`, a Theatre.js camera, GSAP for the DOM, and Act 4 mounting the
> citizen loop's *own* `<ReportFlow>` rather than a picture of one. Five things
> are worth carrying forward rather than leaving in a commit.
>
> **The merge cannot be fired on this checkout, and the report says why rather
> than the suite going quiet.** Act 6 is asserted to render *nothing* until a
> real `cluster_match_found` arrives, and the case that would watch one arrive
> skips **by name**: the committed test photograph is procedurally drawn, CLIP
> scores it 0.142 against the 0.150 floor its own calibration sets, §24.2's
> third outcome fires, and the report parks before deduplication. Publishing a
> synthetic envelope from the test would have passed the gate by violating it —
> *"a scene that can only be fired by a button fails"*.
> [`docs/reports/story-merge-gate.md`](reports/story-merge-gate.md) has the log
> line, what it is not, and the three routes that would take it.
>
> **ADR-0055 was written on the way through.** §E15 names Theatre.js and
> Theatre's only authoring artefact is a machine-written project state. The
> camera is therefore authored as *keys* — fourteen poses in ENU metres, each
> beside the §E16 shot it belongs to — and the project state is **generated and
> drift-checked** like every other artefact in this repository. Theatre is still
> the runtime: it interpolates, and `tests/story-camera.test.ts` asserts that
> loading the generated state reproduces every authored pose to a millimetre.
>
> **The gate caught five real defects, three of them outside this stage.**
>
> * **An unreachable control plane took the whole page down in every non-source
>   locale.** `loadStrings` handled an error *response* and not a thrown
>   connection failure, so `/?locale=mr` answered 500 on any machine with no
>   backend while English rendered perfectly. The function's own docstring had
>   promised the opposite since M2. Found by F12, because the landing is the
>   first surface that negotiates a locale from `Accept-Language`.
> * **A component reused across surfaces brings its dependencies with it.** Act
>   4 mounted `<ReportFlow>` with no `QueryClientProvider` above it and threw on
>   the server render. `<StoryShell>` is the film's one client boundary, the
>   same shape `<CitizenShell>` has and for the same reasons.
> * **A device probe that throws must answer, not go quiet.** `readSignals()`
>   asks for a WebGL 2 context to find out whether it can have one, and a
>   browser out of contexts can throw. The film now falls to the storyboard
>   rung rather than holding at *undecided*, which rendered a scroll film with
>   no camera behind it.
> * **A pinned film must not snap.** `scroll-snap-type` re-snaps after any
>   programmatic scroll, so the proof route's elements could be scrolled to and
>   moved again a frame later. A pinned spine is a still frame and now says so.
> * **Two pre-existing type errors** in `scripts/generate-tokens.ts` and
>   `tests/contracts.test.ts` were red on a clean typecheck in this tree and are
>   fixed in place.
>
> **The film says which of its acts are real, in the DOM.** Acts 0–3 and 8 carry
> `data-real="false"` — they are narrative, and Act 3's nine dates are §E16's
> own art direction rather than this deployment's backlog. Acts 4–7 and 9 carry
> `data-real="true"`, and `tests/story.spec.ts` asserts the split. §6 Principle
> #8 says the honest label is the differentiator; a film is where a product is
> most tempted to blur it.
>
> **Tier C is a deliverable, not a fallback, and it now carries §44.** The nine
> riso prints are drawn rather than captured from the 3D scene, they carry the
> same copy through the same keys, and the honesty table renders in that tier
> too — because §E16.2's promise is about the marketing *surface*, not about one
> of its tiers.

---

## 1. The ordering laws still govern

The three laws in `FRONTEND-EXECUTION-PLAN.md` §0 are not restated here because
they have not changed. Two of them decide this plan's shape:

- **Law 2 — nothing is built against a shape the frontend invented.** F7's nine
  unlanded console screens are built against generated types with fixture
  *values*, behind the not-wired chip, exactly as M4 and M5 did it.
- **Law 3 — no scene before the event that drives it.** F12–F14's acts land after
  the clay (F8–F11), which lands after the event bus (M3, done). A merge that
  cannot be fired by a real `cluster_match_found` fails its gate.

A fourth rule is added by this plan and applies to every phase:

**Law 4 — a phase may not lower a gate that already passes.** Not a style
preference: M0–M6 plus Stage 0 ship **312 automated tests** — 181 in vitest, 131 in Playwright — and the failure mode of a long
build is that a later phase quietly relaxes an earlier one to get green. Every
phase below runs the **full** `nem check` and `nem web-check`, and a phase that
needs an existing gate changed must change it **in its own commit, with the
argument written down**, never as a side effect of shipping a screen.

---

## 2. The stages

| Stage | Phases | What it buys | Milestones closed |
|---|---|---|---|
| **0 · Ground truth** | F1–F2 | The net that catches everything after it | register Group A |
| **1 · The light table** | F3–F7 | The console — the product's actual daily surface | **M7** |
| **2 · The clay** | F8–F11 | The city as a material | **M8** |
| **3 · The film** | F12–F14 | The Walk | **M9** |
| **4 · People, sound, field** | F15–F17 | Ink, ears, and the phone in a basement | **M9, M10, M11** |
| **5 · Close** | F18 | The honesty table verified line by line | **M12** |

**Stage 0 is first and it is not negotiable.** Stages 2 and 3 are the most visual
work in the project and there is currently **no visual regression baseline at
all** — `playwright.config.ts` sets `toHaveScreenshot` to zero tolerance against
nothing. Building the clay engine first and capturing baselines afterwards means
the baselines encode whatever shipped, including its bugs. Capturing them now,
while M0–M6 are known-good and green, is the difference between a regression net
and a photograph of a regression.

---

## 3. The phases

Each phase carries **Blocked by · Ships · Gate · Closes · Traces · Risk**, the
format the milestone document already uses. "Closes" names the register rows and
§E28 capabilities that stop being open.

---

### Stage 0 — Ground truth

---

#### F1 · The safety rails

**Blocked by:** nothing. This is the next thing to do.

**Ships**
- **Golden-image baselines** for every surface that exists today — the press proof (both implementations, every quality tier), the §E26 contract matrix across all twelve combinations, the citizen flow's four screens, the §E18 proof surface's four suppression states. Captured at fixed seed, fixed camera, animations disabled, `deviceScaleFactor: 1`.
- **The Storybook diff.** §E24: *"every visual PR posts its Storybook diff and a five-second scene capture."* CI builds the catalogue today and uploads it; this makes it compare against the base ref and fail loudly on an unreviewed visual change.
- **Lighthouse in CI**, budgets from §E23: ≥ 90 performance and accessibility on the citizen route (`/report`) and the public route. The department route joins at F3.
- **The off-origin assertion, generalised** — currently `tests/type.spec.ts` fails on a font fetched from a third party, and M6 added an all-request check on one public route. This makes it a shared fixture applied to **every** route, covering images, scripts, audio and XHR (A13's actual complaint).
- `scripts/check_phase_coverage.py` — asserts every M7–M12 ship line and every open register row is claimed by exactly one phase in this document (§0's rule, made mechanical).
- **The ML worker's memory failure, fixed or formally accepted.** `worker-ml` fork children are SIGKILLed at model load: the Docker VM has 7.4 GiB and the sum of declared `mem_limit`s is ≈ 7.2 GiB, so CLIP's load tips it and the OOM killer takes the child. This is why M5's *"stamps come from the log"* gate is red on this machine. Three candidate fixes, to be argued in a short ADR rather than picked: raise the VM allocation; lower the non-ML limits for local runs; or move `worker-ml` to `--pool=solo`, which with `--concurrency=1` removes a fork that buys nothing and costs a second copy of the weights — at the price of losing per-task isolation, which is a real tradeoff and the reason this is an ADR and not a one-line change.

**Gate**
- Every golden image passes on a clean run, and **each one is verified to fail** against a deliberately perturbed render — a baseline nobody has watched fail is a screenshot, not a gate.
- A seeded visual change fails CI and posts a diff.
- Lighthouse ≥ 90 on both routes, failing the build below it.
- A seeded off-origin `<img>` fails the assertion on a route that is not the public one.
- `nem check` and `nem web-check` both green, **including** M5's pipeline gate.

**Closes:** A8, A9, A13, A14 (citizen + public half).
**Traces:** §E24, §E23, §E25 Phase 18.
**Risk:** golden images on a 3D-free codebase are cheap; they get expensive at F8 when SwiftShader enters. Establishing the harness now is deliberate — the hard renderer case then lands into a working mechanism instead of inventing one under pressure.

---

#### F2 · The debt inside "done"

**Blocked by:** F1 (so every change here is caught by a baseline).

**Ships**
- **Metric-matched fallback faces** (A1). `size-adjust`, `ascent-override`, `descent-override`, `line-gap-override` computed from the real faces' metrics by `scripts/fetch_fonts.py` and written into the generated `fonts.css` — generated, never hand-tuned, because a hand-tuned override is a number that silently stops matching the next time a face is re-subset.
- **The locale gate, asserted against a live control plane** (A2). A `nem gate-phase18-locale` task in the pattern of `gate-phase5`: add a locale over HTTP, assert it appears in the rendered UI with no code change. This is the one Phase 18 clause that cannot be checked without a running stack and is therefore the one most likely to be assumed true.
- **Density persisted, with a control** (A10). Three modes, stored per user, restored before first paint so the console does not flash a density the officer did not choose. A hard prerequisite for F3.
- **RTL, seeded and asserted** (A11). One RTL locale in the seed bundle and a browser assertion that the public and citizen surfaces mirror correctly. Every stylesheet already uses logical properties — zero physical `left`/`right` in `src/` — so this proves a claim rather than making a change.
- **C7 and C8 unblocked** (backend, small): the tenant's display name published on the public index, and `SYSTEM_FLAGGED_NOTICE` / `RATING_DISCLAIMER` / zone names / taxonomy display names resolved through the Phase 5 locale registry. Without these the public surface **cannot speak Marathi**, which makes A2's and A11's gates half-assertions.

**Gate**
- CLS < 0.1 on the citizen route measured with the fallback face forced.
- A locale added over HTTP appears in the UI, asserted end to end against a live stack.
- Density survives a reload and a new tab.
- The RTL locale renders mirrored with no physical-property regressions.
- A Marathi public page carries a Marathi disclaimer, not an English one.

**Closes:** A1, A2, A10, A11, C7, C8.
**Traces:** §E10, §E15, §E22, §E25 Phase 18.
**Risk:** C7 is a contract change on a published surface. It is additive (a locale-negotiated field beside the existing one) and re-locks by addition, but it needs the argument written before it is made.

---

### Stage 1 — The light table · M7

---

#### F3 · The shell

**Blocked by:** F2 (density).

**Ships** — §E19's opening paragraph, which is a specification and not a mood:
`mitti-950` ground with prints backlit on glass (§E9.3) · Switzer 15 px · JetBrains
Mono for **all** data · three persisted density modes · `⌘K` command palette ·
**a first-class print stylesheet** · the full keyboard model (`/` search, `j`/`k`
queue, `e` evidence, arrow-key traversal) · `<DegradedBanner>` mounted at the
shell · the not-wired routing guard extended so no ROADMAP console screen can
reach a public URL.

**Gate**
- Full keyboard path with no mouse, including a screen-reader pass over the palette.
- The print stylesheet produces a usable A4 document **in grayscale**, with severity legible by shape (§E9.4 rule 2).
- Lighthouse ≥ 90 on the department route (completes A14).
- `axe` clean across three densities × two scripts.

**Closes:** A14 (department half), the §E19 shell.
**Claims:** M7.1 · M7.2 · M7.3 · M7.4
**Traces:** §E19, §E9.3, §E22.
**Risk:** the print stylesheet is routinely deferred and then never written. It is in this phase, with a gate, for that reason.

---

#### F4 · The review queue · REAL

**Blocked by:** F3. **Backend: fully shipped** (`/api/v1/review`, media included).

**Ships:** the queue, the item, the decision. `review_queued` / `review_decided`
rendered per §E27. Media served through the redacted path only. `<EvidenceTrail>`
in its officer filtering — *the same component as the citizen's, differing only by
row filtering, never by different code* (§E26).

**Gate:** a decision taken in the browser appears in the event log and moves the
item, asserted against a live stack. The trail renders identical rows to the
citizen view minus the filtered ones, asserted by comparing renders rather than by
reading the code.

**Closes:** §E28 *Review queue*.
**Claims:** M7.5
**Traces:** §E19.1, §E26, §E27.

---

#### F5 · The policy studio and simulation · REAL

**Blocked by:** F3. **Backend: fully shipped.**

**Ships:** §E19.8, which the blueprint calls *"the console's sharpest screen, and
it is fully backed today"* — rules as editable documents with revision history and
diff; **the activate control disabled without a backtest**; the backtest result
stated in reverted-decision terms (*"would have merged 47 additional reports
across 400 historical complaints, 6 of which reviewers later reverted"*); shadow
mode; rollback.

**Gate:** a policy cannot be activated without a backtest **and the refusal states
why** — asserted in the browser against the live guardrail, not against a
client-side check. §E19.4's division holds here too: the backend enforces, the UI
renders the rule legibly in its disabled state.

**Closes:** §E28 *Policy studio + simulation*.
**Claims:** M7.6
**Traces:** §E19.8, §13.3, ADR-0028, ADR-0029, ADR-0030.

---

#### F6 · Control plane and developer portal · REAL

**Blocked by:** F3. **Backend: fully shipped** (35 control-plane paths, 8 integration paths).

**Ships:** taxonomy tree with prompt sets · zones with boundaries · departments ·
calendars with deadline preview · locales and translation coverage · tenant
provisioning · **the publication control from ADR-0046, with its justification
field as a first-class input rather than a hidden parameter** · API keys · webhooks
with delivery log and secret rotation · usage · the version registry and its
deprecation clock.

**Gate:** a tenant is provisioned, given an invented taxonomy, and published —
entirely through the UI, no SQL, no code change. That is Phase 5's own gate,
re-run through the surface a solutions engineer would actually use.

**Closes:** §E28 *Control-plane admin*, *Developer portal*.
**Claims:** M7.7 · M7.8
**Traces:** §E19, §E14.4, ADR-0046.

---

#### F7 · The console that is not wired yet

**Blocked by:** F3. **Backend: absent by design** — every screen here is built
against generated types with fixture *values* behind the §E24 chip.

**Ships**, each with its ROADMAP phase named on the chip: role shells (13) · area
view and **the underreporting signal** (12, 23) · work order with the contractor
picker and **the concentration warning** (14) · budget entry with live rate-card
variance (14) · the milestone gate strip (14) · **closure gates rendering the
unmet conditions** (15) · money with the *"what citizens see"* toggle (14, 23) ·
the integrity room, the case file and the blacklist requirement counter (17) ·
the report builder with its verification footer (23) · SLA countdowns (12).

**Gate**
- **No ROADMAP screen is reachable from a public URL** — a route test, not a convention.
- Every screen compiles against generated types; a hand-written interface describing a backend contract fails `check-guards`.
- The closure screen renders `Resolved` **disabled with its unmet conditions attached**, and no client-side check is ever the control (§E19.4).
- The blacklist action renders *"3 of 5 requirements met"* rather than hiding itself (§E19.6).

**Closes:** nine §E28 capabilities move to **component REAL, data ROADMAP** — which is the honest half of a two-column table and is the whole reason it has two columns.
**Claims:** M7.9 · M7.10 · M7.11 · M7.12 · M7.13 · M7.14 · M7.15 · M7.16
**Traces:** §E19.0–§E19.7, §E24.
**Risk:** the largest phase in the plan by screen count. It is deliberately last in Stage 1 so the four REAL screens ship first and the console is useful before it is complete.

---

### Stage 2 — The clay · M8

**Installs:** `@react-three/fiber` · `@react-three/drei` · `three-mesh-bvh` ·
`r3f-perf` · `maplibre-gl` · `deck.gl`. `three` is already present.

---

#### F8 · The renderer and the ladder

> **Landed.** Closes A7. The two defects it caught — headless reduced-motion, and a rung mistaken for a tier — are in §0.

**Blocked by:** F1 (the golden-image harness must exist before a renderer does).

**Ships:** `<ClayScene>` — the last unbuilt §E26 contract (A7) · `WebGPURenderer`
in every tier with the WebGL2 backend as the renderer's own selection, not our
branch (ADR-0037) · **Web Mercator → local ENU-metre projection** so precision
holds at city scale · `webglcontextlost` recovery without a page reload, and a
second loss dropping permanently to Tier C **calmly** · the S/A/B/C/D tier
detector · the adaptive quality manager, **turning the press's quality dial before
it touches frame rate** (§E6.4) · **the accessible peer list — a synchronised DOM
list of the same entities, present in every tier, a peer and not a fallback.**

**Gate:** forced context loss recovers to a correct scene. `prefers-reduced-motion`
and a no-WebGL device both render a correct, usable map. **The accessible list is
present and synchronised in every tier** — asserted per tier, by forcing each
trigger.

**Closes:** A7.
**Claims:** M8.1 · M8.2 · M8.8 · M8.9 · M8.10 · M8.12
**Traces:** §E7, §E13, §E22, §E25 Phase 19, ADR-0037.
**Risk:** SwiftShader headless WebGL in CI is the piece most likely to fight back. F1's harness exists precisely so this phase debugs one new thing instead of two.

---

#### F9 · The material and the city

> **Landed.** ADR-0047 written first, as §4 asks. The material's emissive reservation is asserted in `tests/clay-material.test.ts`, not reviewed.

**Blocked by:** F8.

**Ships:** the clay as **one TSL node material** — matte albedo at roughness 0.92,
zero metalness, no PBR streaming · thumbprint normal hashed per entity id · AO
baked to vertex colours · the rim sub-surface cheat · cut-card edges on every
extruded footprint · **severity as a fired glaze, never flat emissive** · and
**the clay city kit, generated rather than modelled** — see §4 and the ADR it
proposes.

**Gate:** golden image per material feature at fixed seed and camera, in both
backends. A severity glaze and its badge ink are the same token, asserted — §E24's
central claim, extended into the third dimension.

**Claims:** M8.3
**Traces:** §E7.1, §E9.4, §E24.

---

#### F10 · Pins, picking and the stepped clock

> **Landed.** One draw call for the city and one for its pins; GPU picking on the pointer, `three-mesh-bvh` on the ray path and in the tests.

**Blocked by:** F9.

**Ships:** `InstancedMesh` pins — **one draw call for the city** — with
per-instance severity, state and animation phase · GPU picking · `three-mesh-bvh`
· **the 12 fps stepped clock** driving every instance and state update with the
render loop uncapped and the camera damped (§E7.2) · the Settle motion · pins
driven by real `complaint_submitted` / `severity_scored` events off the M3 bus.

**Gate:** draw calls under budget from `renderer.info`. A pin appears from a
genuine backend event in an E2E test — Law 3, arriving early because it is
cheaper to hold than to retrofit.

**Claims:** M8.4 · M8.5
**Traces:** §E7.2, §E11.1, §E27.

---

#### F11 · The lens, the sky, and the 2D path

> **Landed, with the frame-rate clause recorded as a deviation** — see §0 and `docs/reports/clay-frame-rate.md`. The 2D path draws places but not boundaries: the read contract publishes none.

**Blocked by:** F10.

**Ships:** tilt-shift depth of field with the focal plane following the selected
entity · gate weave at ±0.4 px resampled at 12 Hz · vignette and slight barrel
distortion · **selective bloom reserved exclusively for `safety_trigger_fired`** ·
real sun from the tenant's local time and real weather, **wired to the same
monsoon context that seasonally normalises contractor SLAs** — the art and the
fairness mechanism as one fact rendered twice (§E7.4) · MapLibre GL + deck.gl for
the 2D and heavy-layer path.

**Gate — the Phase 19 gate, and the hardest number in the project**
- **60 fps sustained with 5 000 instanced pins plus extruded buildings on this laptop, measured, with Ollama running.**
- **VRAM ≤ 512 MB asserted in CI.**
- The WebGL2 backend renders the same scene as WebGPU, verified by golden image.

**Closes:** **M8**, §E28 *Live map, instanced pins*.
**Claims:** M8.6 · M8.7 · M8.11 · M8.13
**Traces:** §E7.3, §E7.4, §E23, §E25 Phase 19.
**Risk:** the frame-rate gate shares a GPU with Ollama by design (ADR-0002) and is the one gate in this plan that hardware can simply refuse. If it fails after honest optimisation, the answer is a recorded deviation with a measured number — not a quietly relaxed budget (Law 4).

---

### Stage 3 — The film · M9

**Installs:** `lenis` · `gsap` · `@theatre/core` + `@theatre/studio` (dev-only).

---

#### F12 · The spine and the cold open

**Blocked by:** F11.

**Ships:** one normalised `t ∈ [0,1]` from a damped Lenis proxy (`lerp ≈ 0.075`)
driving a Theatre.js camera and uniform sequence · GSAP ScrollTrigger driving DOM
and type · scroll-snap per act · **scroll controls distance travelled, not
playback** · Acts 0–3: the cold open, the walk, the stop, the silence.

**Gate:** golden image per act at fixed `t`, seed and camera. Stopping the scroll
stops the walk — asserted, because it is the property that makes this a walk
rather than a video.

**Claims:** M9.1 · M9.4 · M9.5
**Traces:** §E16, §E11.

---

#### F13 · The transition and the gates

**Blocked by:** F12, and M5 (done).

**Ships:** **Act 4** — the camera pushes through the phone screen into the *real*
`<ReportCapture>` in DOM, the shutter fires, the 3D pothole freezes to a
photograph and **the photograph peels off the world into a paper card**. Clay
becomes paper: the transition the whole direction rests on. **Act 5** — the six
gates in physical form, driven by the same event reads M5's theatre already uses,
including the third outcome.

**Gate:** Act 4 renders the actual capture component, not a picture of one. Every
Act 5 gate is fired by its genuine backend event in an E2E test.

**Claims:** M9.2
**Traces:** §E16 Acts 4–5, §E16.1, §E17.1, §E5.

---

#### F14 · The merge, the table, and Tier C

**Blocked by:** F13.

**Ships:** **Act 6 — THE SHOT.** Pull back to dusk, tilt-shift snaps the city to
miniature, three flags on one road lean and converge, the survivor grows, the
second ink overprints, **a thumbprint presses in**, and **registration rings
remain — because deduplication is not deletion and the visual must say so.** Then
Acts 7–9: the survey frame, the workbench, and §44 published on the marketing
surface (the honesty data M6 already generates, rendered a second way). Plus the
five signature motions audited as five, and **Tier C's nine riso prints**.

**Gate — the Phase 20 gate**
- **Every scene is triggered by a genuine backend event in an E2E test. A scene that can only be fired by a button fails.**
- Every fallback tier exercised in CI by forcing its trigger.
- Golden-image regression per scene at fixed seed and camera.
- Frame budget held with all effects enabled.
- **Tier C prints reviewed as a design deliverable, not merely generated.**

**Closes:** **M9**, §E28 *Cluster-merge hero, live*.
**Claims:** M9.3 · M9.7 · M9.8 · M9.9
**Traces:** §E16 Acts 6–9, §E11.1, §E13, §E25 Phase 20.

---

### Stage 4 — People, sound, field

---

#### F15 · The characters · INK

**Blocked by:** F14. **Installs:** `@rive-app/react-canvas` *if* a `.riv` exists — see §4.

**Ships:** the four figures of §E8.2 — the Reporter, the Officer, the Field Hand,
the Auditor — as **event-driven state machines with §E8.1's exact named inputs**
(`walking` · `stopped` · `looking_down` · `shoulders_drop` · `raise_phone` ·
`shutter` · `relief` · `disappointed`), animated **on twos** off F10's stepped
clock. **No face, ever.** `citizen_confirmed` on the WebSocket fires `relief`.

**Gate:** a real `citizen_confirmed` event moves a character, asserted E2E. Not one
timeline exists in the source — the audit is a grep, because §E8.1's whole claim
is that these are inputs rather than playback.

**Closes:** §E28 role for the character layer.
**Claims:** M9.6
**Traces:** §E8, ADR-0041, and the ADR proposed in §4.

---

#### F16 · Sound, and the ladder proven · M10

**Blocked by:** F15.

**Ships:** the §E12 Web Audio graph — ambient bed cross-faded on the real clock ·
**positional foley so an operator can hear where the problems are** · interaction
foley (stamp thud, paper slide, pin push, page turn, shutter, roller pass) · the
merge cue as three soft taps into one low thump · **the single struck metal note
for `safety_trigger_fired`, the only alarming sound in the product** · muted by
default, unmute designed rather than hidden, state persisted, master duck on modal
open. Plus **the §E3.4 audit**: colour, motion and sound each carry exactly one
meaning or none.

**Gate**
- All buses respect `prefers-reduced-motion` as a sensory-sensitivity proxy.
- **No second use of bloom, the stamp, or a severity colour exists in `src/`** — a usage grep with an explicit allowlist, in the pattern of `check-guards`.
- Every tier S/A/B/C/D produces its documented rendering when its trigger is forced.

**Closes:** **M10**.
**Claims:** M10.1 · M10.2 · M10.3 · M10.4 · M10.5 · M10.6
**Traces:** §E12, §E3.4, §E13.

---

#### F17 · Field and offline · M11

**Blocked by:** M5's idempotency keys (done). Independent of Stages 2–3 — **this
phase can be pulled forward** if a pilot needs it before the film.

**Ships:** PWA with installability and a service worker · an offline submission
and evidence queue **surviving app restart** · three jobs and a camera for field
staff, who **never see a kanban** · **outdoor mode** — near-monochrome, heavier
weights, larger type, designed for gloves and glare · client-side compression with
EXIF preservation · background upload with visible per-item state · conflict-free
sync made safe by server-side idempotency.

**Gate — the Phase 22 gate**
- A complaint and a closure photo captured fully offline sync correctly on reconnect.
- A killed app mid-upload resumes without duplicating or losing the submission.
- Usable end to end on a throttled 2G profile.
- **Outdoor mode passes contrast at 7:1 for primary text.**

**Closes:** **M11**.
**Claims:** M11.1 · M11.2 · M11.3 · M11.4 · M11.5
**Traces:** §E21, §E25 Phase 22.

---

### Stage 5 — Close

---

#### F18 · Reconciliation · M12

**Blocked by:** every preceding phase.

**Ships:** §E28 verified line by line against what is actually running, and §44
updated to match · §E27's event-to-surface table audited, where **a visual element
not on that list and not classifiable as chrome is a defect** · the ADR index and
`docs/PHASES.md` Track E amended where this build corrected them · **the usability
session with real field staff and department users**, task-success measured and
findings tracked (A16) · **the WCAG 2.2 AA audit by a person** (A15) · **the
string tier that cannot resolve, decided either way** (A17) — `loadStrings`'
control-plane tier fetches namespaces the registry does not carry and can only
ever return `{}`, so either product copy becomes tenant-importable or the tier
goes; either answer is an argued change, and a claim the code makes and cannot
support is exactly what this phase exists to find.

**Gate**
- Every claim on the frontend rows of §44 traces to a passing test or a shipped artefact.
- **No REAL row is backed by a fixture.**
- `check_phase_coverage.py` reports zero unclaimed lines.

**Closes:** **M12**, A15, A16, A17.
**Claims:** M12.1 · M12.2 · M12.3 · M12.4 · M12.5
**Traces:** §E27, §E28, §44, §E24.
**Risk:** A15 and A16 are the two clauses no amount of code closes. They need people booked, and booking them is a lead-time item that should start at F3 rather than at F18.

---

## 4. The assets do not exist, and that is the largest risk in this plan

`FRONTEND-EXECUTION-PLAN.md` §3b lists five asset classes and says of them: *"Not
code, and none of it exists yet. Listed because a milestone that assumes it will
appear is a milestone that stalls."* F9, F14, F15 and F16 each assume one.

A solo build has no Blender artist, no Rive designer, no illustrator and no foley
recordist. Pretending otherwise is how this plan fails in month three. **Four
decisions, each needing an ADR, each with the cost stated rather than hidden** —
and each following a precedent this repository has already set twice, in
`scripts/demo_imagery.py` and in the generated paper texture (A12).

| # | Asset | Decision to argue | Precedent |
|---|---|---|---|
| **ADR-0047** | The modular clay city kit | **Generate it, do not model it.** Procedural geometry seeded from the tenant's own zone boundaries and a deterministic building distribution, through the same `gltf-transform` → KTX2 pipeline. §6 Principle #6 is zero-cost, self-hosted, offline-capable; a committed Blender kit is a binary nobody on the team can regenerate, and a clean checkout that depends on it is one asset loss away from unbuildable. **Cost:** it is a *model* of a city rather than a survey of one — which is exactly what §E5 says the clay is | the generated paper texture (A12); `demo_imagery.py`'s procedural streets |
| **ADR-0048** | The four Rive state machines | **The contract is the state machine, not the file format.** §E8.1 specifies *named inputs and states*, not `.riv`. Ship the input interface plus a canvas sprite implementation animated on twos off F10's clock; Rive becomes a swappable renderer the day a `.riv` exists. Law 3 is untouched — the character still reacts to real events. **Cost:** the drawing is cruder than an illustrator's, and the §E8 line-weight quality is a deviation to record | ADR-0041 already fixes the *behaviour*, not the vendor |
| **ADR-0049** | Tier C's nine riso prints | **Render them from Tier S at fixed seed and camera, then review them.** §E13 requires Tier C be *"visually continuous with Tier S because it is the same process"* — rendering from the same scene is the **stronger** reading of that sentence, not a shortcut, and it makes the prints regenerable instead of nine binaries nobody can reproduce. The §E3.2 review still happens and can still reject them | §E13's own wording |
| **ADR-0050** | The sound library | **Synthesise deterministically and commit the opus.** §E12 asks for foley *"recorded from paper and clay"*; a repository that ships no third-party media it has not licensed synthesises instead, from a seeded generator that is itself committed. **Cost:** synthesised paper is not paper, and §E12's tactility is the point — so this is the deviation most likely to be judged insufficient, and the ADR should say so plainly | the licensing argument in `demo_imagery.py` |

**None of these four is required before F8.** They are listed here, at plan time,
because a phase that discovers its input does not exist on the morning it starts
is a phase that stalls — and §6 Principle #8's rule is that a limitation is stated
in advance rather than dressed up afterwards.

---

## 5. Sequencing, and what can move

**The critical path is F1 → F2 → F3 → F8 → F9 → F10 → F11 → F12 → F13 → F14.**
Everything else hangs off it.

| Can move | Why |
|---|---|
| **F4, F5, F6, F7** | Only F3 blocks them. Any order; any subset can be deferred without blocking Stage 2. F5 is the best demo screen in the console and the best candidate to pull forward |
| **F17** | Blocked only by M5, which is done. Can be pulled ahead of Stage 2 entirely if a pilot needs the field app before the film |
| **F15, F16** | Blocked by F14 in this plan, but F16's `prefers-reduced-motion` work and the §E3.4 grep could land with F2 |
| **The four ADRs in §4** | Should be written before their phase starts, not during it |
| **A15 / A16 bookings** | Lead-time items. Start at F3, land at F18 |

**Nothing above changes the two facts that decide everything else:** the console
is the product's daily surface and it is entirely unbuilt, and the clay is what
makes this NEMESIS rather than a competent civic web app. Stage 1 makes the
product useful. Stage 2 makes it itself.

---

*Where this document and `docs/FRONTEND-EXECUTION-PLAN.md` disagree, the execution
plan governs on **what and why**, and this document governs on **order and gate**.
Where either disagrees with `NEMESIS-Frontend-Blueprint.md` on direction, the
blueprint governs.*
