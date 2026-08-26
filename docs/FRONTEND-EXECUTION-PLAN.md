# NEMESIS — Track E Execution Plan

**The operational companion to [`NEMESIS-Frontend-Blueprint.md`](../NEMESIS-Frontend-Blueprint.md).**

The blueprint says *what the frontend is*. `docs/PHASES.md` Track E says *what
each phase must prove*. This document says **what gets built, in what order, and
why that order and no other** — the sequence a single owner works through, with
an entry condition, a deliverable list, and a machine-checkable exit gate for
every step.

- **Owner:** PROD — project owner, sole
- **Governs:** `frontend/` · Phases 18–22
- **Governed by:** the frontend blueprint (§E-prefixed references below point at it; bare `§n` points at `NEMESIS-Blueprint-v2.md`)
- **Baseline commit:** `d8afb4f`
- **Status labels:** REAL / SIMULATED / ROADMAP, used exactly as §44 and §E28 use them

---

## 0. The three ordering laws

The milestone order below is a dependency graph, not a preference. Three laws
produce it, and each one exists because violating it has a known, specific cost.

**Law 1 — The press precedes everything it processes (§E25 M1).**
Ink separation, halftone, misregistration and paper are not a finishing pass;
they are the substrate every other surface inherits. A component built before
the press exists is a component built against the wrong ground, and it will be
re-made. So M1 ships the press with nothing to print, deliberately.

**Law 2 — Nothing is built against a shape the frontend invented (§E24).**
`work_orders`, `budget_allocations`, `contractors`, `contractor_certifications`
and `users` exist as tables today; `severity_score`, `severity_breakdown` and
`severity_policy_version` exist as fields on the v1 read schema and return null.
Screens for unlanded phases are therefore built against **generated types with
fixture values** — never against a hand-written interface describing a future
contract. A hand-written interface describing a backend contract is a review
failure. This law is why M3 (the seam) precedes every surface milestone.

**Law 3 — No scene before the event that drives it (Phase 20 gate).**
*A scene that can only be fired by a button fails the gate.* The clay engine
(M8) and the film (M9) therefore land **after** the WebSocket bus, the store and
the reconciliation rule (M3), because a merge animation that cannot be triggered
by a real `cluster_match_found` is a demo, not a feature.

A fourth rule is not an ordering law but applies at every step: **§E3.5 — the
tool outranks the show.** Where a cinematic requirement and an operator
requirement conflict, the operator wins, and the conflict is recorded rather
than resolved by taste.

---

## 1. Toolchain decisions taken before M0

Two choices in §E15 needed re-deciding against the field as it stands on
2026-08-24. Both are recorded as ADRs rather than settled silently, because
§E15 is a locked section and an unrecorded deviation from a locked section is
exactly the drift `docs/adr/` exists to prevent.

| §E15 says | We ship | Recorded in |
|---|---|---|
| Next.js 15 App Router | **Next.js 16** App Router | ADR-0042 |
| (TypeScript version unstated, "TS strict") | **TypeScript 5.9**, not the 7.0 native compiler | ADR-0042 |

The asymmetry is the point and it is argued in full in the ADR: we take the
current framework major because §E2 defect #6 is precisely the cost of shipping
a stale decision, and we decline the current TypeScript major because the gates
in §E25 ("zero `any` in application source", "generated-client drift fails CI")
are enforced by lint and codegen tooling that must actually run. A compiler the
tooling has not caught up with buys speed we do not need and risks a gate we
cannot fail. The upgrade trigger is stated in `docs/UPGRADES.md`.

Everything else in §E15 ships as written.

---

## 2. Repository shape

```
frontend/
  src/
    app/                    route groups: (story) (report) (public) (console) (dev)
    server/                 BFF only — the sole holder of the tenant header (ADR-0040)
    press/                  §E6 — CSS/SVG implementation and TSL pass, one token source
    design/                 tokens.json, generators, the type stack
    components/             §E26 contracts
    clay/                   §E7 — renderer, materials, projection, scenes
    story/                  §E16 — the nine acts
    lib/                    store, transport, i18n, formatting
    generated/              openapi types — never hand-edited, never committed by hand
  public/fonts/             self-hosted woff2 subsets (§E10, §6 Principle #6)
  tests/                    Playwright, golden images, gate assertions
```

`frontend/src/generated/` is written by `nem web-types` and by nothing else. A
manual edit there is caught by the drift check, which regenerates and diffs.

---

## 3. Milestones

Each milestone lists **Blocked by**, **Ships**, **Gate**, and **Traces**. A
milestone is done when its gate passes in CI, not when its screens look right.

---

### M0 — Ground · toolchain, lanes, and the empty application

**Blocked by:** nothing.

**Ships**
- `frontend/` workspace: Next.js 16 App Router, React 19, TypeScript 5.9 strict with `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`, Turbopack.
- Tailwind v4 configured to consume generated custom properties only — **no colour literals in `tailwind.config`**, because M1 owns colour.
- ESLint flat config with the project's own rules: no `any`, no colour literal in `src/`, no GLSL string, no CDN URL, no hand-written type in `generated/`.
- `nem web`, `nem web-check`, `nem web-build`, `nem web-types` tasks in `tasks.py`, following the existing task conventions exactly.
- A `frontend` job in `.github/workflows/ci.yml` running the same set `nem web-check` runs, so the local command and CI cannot diverge.
- The five route groups exist as empty, correctly-nested layouts.

**Gate**
- `nem web-check` passes on a clean checkout: typecheck, lint, format, build.
- The lint rules *fail* on a seeded violation of each of the four bans — verified by a fixture file that CI lints in isolation and expects to fail.

**Traces:** §E14.4, §E15, §E24.

---

### M1 — The press · the token pipeline and the print process

**Blocked by:** M0. **This is §E25 M1 and it is built before any scene exists.**

**Ships**
- `design/tokens.json` — the single source: paper stocks, the eight inks, the five severity rows with their four channels each (`ink` / `tint` / `glaze` / `ink pass`), the motion curves and the six durations, the density steps, the type scale per script (§E9, §E10.2, §E11).
- Two generators from that one file: **CSS custom properties** for the 2D layer and **TSL uniform constants** for the shader layer. The badge and the glaze are literally the same number (§E9.4 rule 3, §E24).
- The press, 2D: CSS/SVG halftone, per-ink offset, ink-density noise, multiply overprint, paper grain and deckle — six stages in §E6.1 order.
- The press, 3D: the same six stages as a TSL post-processing pass, WebGPU compute where an adapter exists (ADR-0037).
- The quality dial (§E6.4): `full` / `reduced` / `flat`, driven later by the adaptive quality manager, exposed now as an explicit prop and a URL override for testing.
- **The text exemption**, implemented as a compositing rule rather than as a convention: text renders in an unprocessed layer (ADR-0038, §E6.2).
- `<Press>` per §E26.

**Gate** *(both are Phase 18 gate lines from §E25)*
- The press renders identically in 2D and 3D at a fixed seed — golden image, both implementations, same frame.
- **Text layers are byte-identical with the press on and off.** Asserted by test, not by review.
- A hand-written colour literal anywhere in `src/` fails CI.
- Every severity pair passes 4.5:1 on both grounds **after** the press, because halftone changes effective contrast (§E22).

**Traces:** §E6, §E9, §E24, §E25 Phase 18, ADR-0037, ADR-0038.

---

### M2 — Type · six faces, two scripts, no CDN

**Blocked by:** M1 (the scale lives in `tokens.json`).

**Ships**
- Six Latin faces and five Devanagari faces, self-hosted as woff2, subset with `fonttools`, Devanagari subset separately (§E10, §E10.1).
- A `nem web-fonts` task that fetches, subsets and records licences into `frontend/public/fonts/LICENSES.md`. The build works without running it, on metric-matched fallbacks, so a clean checkout is never broken by a network failure — but CI asserts the real faces are present before a release build.
- Per-script type scale. Devanagari line-height **+0.15** over Latin, applied by the scale generator, never by a stylesheet override (§E10.1).
- `font-variant-numeric: tabular-lining` as a global default on every numeric context; 68ch prose measure including tooltips (§E10.2).
- i18n plumbing: sentences as translation units, string resolution from the Phase 5 locale registry, RTL-ready layout primitives.

**Gate**
- **A locale added in the control plane appears in the UI with no code change** — the Phase 18 gate, exercised end to end by adding one over HTTP and asserting the rendered output.
- No network request leaves the origin for a font, asserted by a Playwright network assertion (§6 Principle #6).
- Latin and Devanagari render at every scale step in Storybook without clipping the shirorekha.

**Traces:** §E10, §E22, §E25 Phase 18.

---

### M3 — The seam · types, BFF, socket, reconciliation

**Blocked by:** M0. Runs alongside M1/M2 where convenient, but **must complete
before any surface milestone**, by Law 2.

**Ships**
- `nem web-types`: exports the FastAPI OpenAPI document, runs `openapi-typescript`, writes `frontend/src/generated/`. **Drift fails CI** (§E15, Phase 18 gate).
- The BFF: every browser-to-API read and mutation goes through Next route handlers. The server holds `X-Tenant-ID` today and the bearer token after Phase 13; the client never names its own tenant (ADR-0040, §E14.1, corrects §E2 defect #11).
- The WebSocket client, connecting **directly** — the deliberate exception in ADR-0040 — with `?since=<cursor>` gap replay on reconnect and heartbeat-as-envelope liveness detection.
- The Zustand event bus with **transient subscriptions** that drive shader uniforms and marker transforms without a React re-render (§E14.2).
- **The reconciliation rule (§E14.3, corrects §E2 defect #12):** the socket is a hint, not a source of truth. Event → optimistic patch → refetch the affected entity from the read path on idle. Implemented once, in one module, so no surface can invent its own.
- Server actions for mutations, carrying a client-generated idempotency key end to end — the property that makes M11's offline queue tractable.
- A refused upgrade (the `realtime_websocket_hub` kill switch) falls to polling with a calm `<DegradedBanner>` and **never retries in a loop against a deliberately disabled capability**.

**Gate**
- Generated-client drift fails CI; zero `any` in application source.
- Killing the socket mid-stream and reconnecting replays the gap: no pin lost, no pin duplicated — asserted against a live stack.
- Flipping the hub kill switch produces the polling path and the banner, and produces **no** reconnect storm, asserted by counting handshake attempts.
- An open-but-dead socket is detected by heartbeat absence within one stated interval.

**Traces:** §E14, §E15, ADR-0040.

---

### M4 — The contracts · §E26 primitives and the Storybook matrix

**Blocked by:** M1, M2, M3.

**Ships** — every component in §E26 Appendix A, with the required props enforced
by the type system rather than by review:
- `<SeverityBadge>` — ink + shape + label, never colour alone.
- `<EvidenceTrail>` — citizen, officer and public views differ **only by row filtering**, never by different code.
- `<BeforeAfter>` — slider + SSIM + capture metadata, identical on all three surfaces.
- `<FlaggedNotice>` — fluorescent hatch, **required** `disclaimer`, **required** `responseHref`; cannot compile without them (§16.4, §22.2, ADR-0039).
- `<SuppressionNotice>` — never a blank cell (ADR-0021).
- `<Receipt>`, `<ContractorLedger>` (no single-score variant exists), `<DegradedBanner>`, `<Stamp>`.
- **The status vocabulary (§E26.1)** as a generated union from `backend/nemesis/domain/lifecycle.py` — all thirteen `ComplaintStatus` members including `pending_classification` and `flagged`, all six `WorkOrderStatus` members including `created` and `disputed`. A chip that cannot render a member is a compile error.
- Storybook across **three densities × two themes × two scripts**.
- The **"not wired" chip** (§E24): a dev-only badge on any screen whose contract returns nulls today, plus the routing guard that prevents such a screen reaching a public URL.

**Gate**
- Exhaustiveness: removing a status from a chip's switch fails typecheck.
- `<FlaggedNotice>` without a disclaimer or a response href fails typecheck.
- `axe` clean across the whole matrix; visual regression baselines captured.
- A "not wired" screen cannot be routed publicly — asserted by a route test, not by convention.

**Traces:** §E26, §E26.1, §E24, ADR-0021, ADR-0039.

---

### M5 — The citizen loop · REAL end to end

**Blocked by:** M4.

**Ships** (§E17)
- **Capture** — the app opens in the viewfinder, not on a form. One shutter, voice input against the real `media_transcribed` pipeline, four-second undo, and one optional field. **The flow completes with that field empty** (§26.1).
- **Place** — reverse-geocoded and stated as a card, not a picker; `adjust` opens a map with a 60 px pin.
- **Send** — optimistic, queued locally, confirmed before the round-trip.
- **The pipeline theatre** (§E17.2) — the six Act 5 gates in clay-and-paper miniature on the phone, driven by real events, including the **third outcome**: `pending_classification` stamps *CLASSIFIER UNAVAILABLE · PARKED FOR HUMAN REVIEW* and the card continues (§24.2, §E16.1).
- **The dedup payoff** — *"You're the 4th person to report this"* — generated by the real engine.
- **The receipt** (§E17.3) — a document, deckled, carrying id and chain hash and the append-only sentence.
- **Track** (§E17.4) — a paper ledger of events, with `why? →` opening the real rubric weights and version. *Component REAL, data SIMULATED until Phase 12.*
- **The Ledger** retention mechanic as a stamped passport (§E17.6), printable.
- §E17.5 closure and §E17.6's ward film are **ROADMAP**, scaffolded against real types, carrying the not-wired chip.

**Gate**
- Submit → receipt → tracked, end to end against a live stack, with a real chain hash on the receipt.
- Every one of the six gates is driven by its real event (§E27), verified in an E2E test.
- The degraded classifier path renders the third outcome and does not stall.
- Lighthouse ≥ 90 performance and accessibility on the citizen route (Phase 18 gate).

**Traces:** §E17, §E16.1, §E27, §E28.

---

### M6 — The public surface · SSR, indexable, suppression-aware

**Blocked by:** M4.

**Ships** (§E18)
- Server-rendered zone / ward / contractor / budget pages, deep-linkable and indexable.
- `rating_disclaimer` and `SYSTEM_FLAGGED_NOTICE` rendered as **first-class UI**, never as tooltips or footnotes.
- `suppressed` / `suppression_threshold` rendered as an honest empty state.
- **Contractor profiles as ledgers, never ratings** — four arguable metrics, each tracing to records; flagged rows carry hatch, disclaimer, **and the contractor's response and appeal status in the same frame** (§16.4).
- `satori` + `resvg` share cards, server-rendered per complaint and per ward.
- Act 9's honesty table published here as data, not as prose (§E16.2).

**Gate**
- A k-anonymity hole never renders as a zero.
- A flagged row cannot render without its disclaimer and response link — the same compile-time contract as M4, exercised on a public route.
- Public pages render correctly with JavaScript disabled (§E13 Tier D).

**Traces:** §E18, §E13, ADR-0021.

---

### M7 — The light table · the municipal console

**Blocked by:** M4. §E3.5 puts this ahead of the film.

**Ships** (§E19)
- The shell: `mitti-950` ground, backlit prints, Switzer 15 px, JetBrains Mono for all data, three persisted density modes, `⌘K` palette, **a first-class print stylesheet**.
- **REAL today:** the review queue; the **policy studio and simulation** (§E19.8) — the activate control disabled without a backtest, and the backtest result stated in reverted-decision terms; control-plane admin; the developer portal.
- **Command view** (§E19.1) — the split, the queue sorted by SLA-remaining then severity, and the breach strip that says what to do before it says how you are doing. *Queue REAL; countdowns ROADMAP (Phase 12).*
- **ROADMAP, built against real types with fixture values and the not-wired chip:** role shells (13), area view and the underreporting signal (12, 23), work order and the contractor picker with the concentration warning (14), the milestone gate strip (14), closure gates rendering the **unmet** conditions (15), money (14, 23), the integrity room and case file and the blacklist requirement counter (17), the report builder (23).
- §E19.4's division is preserved exactly: **the backend enforces closure; the UI renders the rule legibly in its disabled state.** No client-side check is ever the control.

**Gate**
- Full keyboard path including the map: arrow-key pin traversal, `/`, `j`/`k`, `e`, `⌘K`.
- A policy cannot be activated without a backtest, and the refusal states why.
- The print stylesheet produces a usable document on A4 in grayscale, with severity legible by shape (§E9.4 rule 2).
- Lighthouse ≥ 90 on the department route (Phase 18 gate).
- No ROADMAP screen is reachable from a public URL.

**Traces:** §E19, §E19.8, §E24, §E28.

---

### M8 — The clay engine · Phase 19

**Blocked by:** M1 (the press) and M3 (the events that drive it).

**Ships** (§E7, §E13, ADR-0037)
- `WebGPURenderer` in every tier; Tier S and Tier A are the renderer's own backend selection, not our branch.
- **Web Mercator → local ENU-metre projection**, so precision holds at city scale.
- The clay material as one TSL node material: matte albedo, thumbprint normal hashed per entity id, AO baked to vertex colours, the rim sub-surface cheat, cut-card edges, **severity as a fired glaze, never as flat emissive**.
- `InstancedMesh` pins — one draw call for the city — with per-instance severity, state and animation phase; GPU picking and `three-mesh-bvh`.
- **The 12 fps stepped clock** driving every instance and state update, with the render loop uncapped and the camera damped (§E7.2).
- The lens stack: tilt-shift, gate weave, vignette, and **selective bloom reserved exclusively for `safety_trigger_fired`** (§E7.3).
- Real sun from the tenant's local time and real weather, wired to the **same monsoon context that seasonally normalises contractor SLAs** (§E7.4).
- The adaptive quality manager, turning the press's quality dial **before** it touches frame rate (§E6.4, §E23).
- `webglcontextlost` recovery without a page reload; a second loss in one session drops permanently to Tier C and says so calmly.
- **The accessible peer list** — a synchronised DOM list of the same entities, present in every tier, a peer and not a fallback (§E22).
- MapLibre GL + deck.gl for the 2D and heavy-layer path (§E15, corrects §E2 defect #5).
- `<ClayScene>` per §E26.

**Gate** *(Phase 19, §E25)*
- **60 fps sustained with 5 000 instanced pins plus extruded buildings on this laptop, measured, with Ollama running.**
- **VRAM ≤ 512 MB asserted in CI**; draw calls under budget from `renderer.info`.
- Forced context loss recovers to a correct scene without a page reload.
- The WebGL2 backend renders the same scene as WebGPU, verified by golden image.
- `prefers-reduced-motion` and a no-WebGL device both render a correct, usable map.
- The accessible list view is present and synchronised **in every tier**.

**Traces:** §E7, §E13, §E22, §E23, §E25 Phase 19, ADR-0037.

**Gate result — five of six pass; the frame rate is a recorded deviation.**

| Clause | Budget | Measured | |
|---|---:|---:|---|
| Draw calls, from `renderer.info` | 32 | 14–17 | ✅ |
| VRAM, from `renderer.info` | 512 MB | 14–22 MB | ✅ |
| Forced context loss recovers without a page reload | — | asserted in a browser | ✅ |
| Both backends render the same scene | — | golden image per backend, at a pinned step and a fixed instant | ✅ |
| `prefers-reduced-motion` and no-WebGL both render a usable map | — | asserted; the 2D path answers the first, the peer list the second | ✅ |
| **60 fps with 5 000 pins on this laptop** | 60 | **~21** (60 holds to ~1 500) | ⚠️ |

`BUDGET.fps` and `BUDGET.pins` are unchanged at 60 and 5 000 — the budget is
what the product wants, and [`reports/clay-frame-rate.md`](reports/clay-frame-rate.md)
is what the hardware gave, with the scaling curve, the two optimisations tried,
and the three routes still open. Law 4: the shortfall is published as a number,
not absorbed into a token.

**What the gate caught**, each fixed and each argued in the source: headless
Chromium defaults to `prefers-reduced-motion: reduce`, so every browser test had
been running at Tier C and the clay had never rendered in CI; `tierFor(rung,
null)` correctly answers *C* before a renderer exists, which meant the host that
consulted it never built one; the press's ink separation only inked pixels
darker than the ink itself, so the 3D press printed a blank sheet; and §E9.3's
light table publishes the *room* as its stock, which a 3D press cannot print on.
The first two are in `clay/tier.ts`, the second two in `press/press-model.ts`
and `design/tokens.json`.

**ADR-0047** — the clay city is generated from the tenant's own origin, not
modelled — was written before F9, which is what §4 of the phase plan asks for.

---

### M9 — The Walk · Phase 20

**Blocked by:** M8, and M5 (Act 4 pushes into the real `<ReportCapture>`).

**Ships** (§E16)
- One normalised `t ∈ [0,1]` from a damped Lenis proxy driving a Theatre.js camera and uniform sequence; GSAP ScrollTrigger driving DOM and type; scroll-snap per act. **Scroll controls distance travelled, not playback.**
- Acts 0–9 as specified, including the clay→paper transition the direction rests on (Act 4) and **the merge** (Act 6): converge, overprint, thumbprint, count stamp, registration rings remaining — because deduplication is not deletion and the visual must say so.
- The five signature motions and only five (§E11.1).
- Rive characters as **event-driven state machines**, not timelines: `citizen_confirmed` fires `relief` (§E8.1, ADR-0041).
- Act 9's receipts, including §44's honesty table published on the marketing surface.
- **Tier C** — nine art-directed riso prints, scroll-snapped, same copy, reviewed as a design deliverable in its own right (§E3.2, §E13).

**Gate** *(Phase 20, §E25)*
- **Every scene is triggered by a genuine backend event in an E2E test — a scene that can only be fired by a button fails the gate.**
- Every fallback tier is exercised in CI by forcing its trigger.
- Golden-image regression passes per scene at fixed seed and camera.
- Frame budget held with all effects enabled.
- Tier C prints reviewed, not generated.

**Traces:** §E16, §E8, §E11, §E13, §E25 Phase 20, ADR-0041.

---

### M10 — Sound, motion audit, and the ladder proven

**Blocked by:** M9.

**Ships**
- The Web Audio graph (§E12): ambient bed cross-faded on the real clock, **positional foley so an operator can hear where the problems are**, interaction foley recorded from paper and clay, the merge cue, and the single struck metal note for `safety_trigger_fired` — the only alarming sound in the product. Muted by default, unmute designed rather than hidden, state persisted.
- The §E3.4 audit, run as a review pass: colour, motion and sound each carry exactly one meaning or none. Severity ink never decorates; the stamp only confirms; bloom only fires on the fail-safe.
- Every tier S/A/B/C/D exercised in CI by forcing its trigger.

**Gate**
- All buses respect `prefers-reduced-motion` as a sensory-sensitivity proxy.
- No second use of bloom, the stamp, or a severity colour exists in `src/` — asserted by a usage grep with an allowlist.
- Every tier's forced trigger produces its documented rendering.

**Traces:** §E12, §E3.4, §E13.

---

### M11 — Field & offline · Phase 22

**Blocked by:** M5 (idempotency keys from M3 are what make this safe).

**Ships** (§E21)
- PWA with installability, service worker, and an offline submission and evidence queue that survives app restart.
- Three jobs and a camera for field staff. **They never see a kanban.**
- **Outdoor mode** — near-monochrome, heavier weights, larger type, designed for gloves and glare.
- Client-side compression with EXIF preservation; background upload with visible per-item state so nothing fails silently.
- Conflict-free sync on reconnect, made safe by server-side idempotency.

**Gate** *(Phase 22, §E25)*
- A complaint and a closure photo captured fully offline sync correctly on reconnect.
- A killed app mid-upload resumes without duplicating or losing the submission.
- The flow is usable end to end on a throttled 2G profile.
- **Outdoor mode passes contrast at 7:1 for primary text.**

**Traces:** §E21, §E25 Phase 22.

---

### M12 — Reconciliation

**Blocked by:** every preceding milestone.

**Ships**
- §E28's table verified line by line against what is actually running, and §44 updated to match — the same ritual Phase 29 applies to the whole system.
- §E27's event-to-surface table verified: **a visual element not on that list, and not classifiable as chrome, is a defect.**
- ADR index updated; `docs/PHASES.md` Track E amended where this build corrected it.
- The usability session with real field staff and department users, with task-success measured and findings tracked — the Phase 18 gate that is a *practice*, not a component library.

**Gate**
- Every claim on the frontend rows of §44 traces to a passing test or a shipped artefact.
- No REAL row is backed by a fixture.

**Traces:** §E27, §E28, §44, §E24.

---

## 3a. Progress

| Milestone | State | Gate |
|---|---|---|
| **M0 — Ground** | **Done** | `nem web-check` green on a clean checkout. Four design-law bans each verified *failing* against a seeded violation in `tests/fixtures/lint/` |
| **M1 — The press** | **Done** | Token artefacts generated from one source and drift-checked; 48 contrast assertions across every role × ground, before and after the press; 14 press-plan assertions; and the browser gate — **the text layer is byte-identical with the press on and off**, across every quality tier and every severity pass, with the imagery asserted to change so the claim is not vacuous |
| **M2 — Type** | **Done** | Ten families fetched, subset and committed — 51 woff2 files, 2.0 MB, zero CDN requests asserted in a real browser. Six type gates: no face leaves the origin, the shipped faces are the faces that load, per-script leading holds at every step, Devanagari ink clears its line box measured by real ink extents, numerals are tabular, prose caps at 68ch. **The i18n half — string resolution from the Phase 5 locale registry — moves to M3**, because it needs the generated client and the BFF, and asserting it before those exist would mean asserting it against a mock |
| **M3 — The seam** | **Done** | `openapi.json` committed beside the contract lock and drift-checked in `nem check`; 5 600 lines of generated client plus 11 runtime enum companions, drift-checked in `nem web-check`; the BFF holds the tenant header and `server-only` makes a client-bundle import a build error; 14 realtime assertions — a reconnect replays from its cursor, a replayed envelope cannot move the cursor backwards, every envelope reaches transient subscribers past the ring limit, close code 1008 produces **one** handshake and a calm banner rather than a storm, and silence past three heartbeats closes an open-but-dead socket. **Four contract defects found and fixed** (rows 14–17) |
| **M4 — Contracts** | **Done** | Every §E26 primitive, plus the string layer they need. **`Translated` is not `string`** — only `t()` and `plural()` produce it, so a literal in a component and a sentence built from fragments both fail to compile (§E10.1 made mechanical). Six must-not-compile fixtures assert the required props, each against its exact TS diagnostic. `axe` clean across all **twelve** combinations — three densities × two grounds × two scripts — plus Storybook building the same `<ContractMatrix>` the sweep drives. **Six defects found and fixed** (rows 18–23), three of them mine and caught by the browser rather than by the token tests |
| **M5 — Citizen loop** | **Done** | `/report` and `/t/[id]` ship against a live stack. Submit → receipt → tracked, with the receipt's hash asserted to be the `complaint_submitted` event's own and the chain verified link by link in the browser (ADR-0044). Five §E16.1 gates read from the log rather than a timer, including §24.2's third outcome and — found by running it — a **held** state for gates the pipeline will never reach, because a parked report left two rows saying *not compared yet* forever. Place resolves a coordinate to a real ward through `GET /places/resolve`. Nine E2E assertions plus three measured-contrast ones; **eleven defects found and fixed** (rows 24–34), five of them in the backend and four of them mine |
| **M6 — Public surface** | **Done** | `/[tenant]`, `/ward/[code]`, `/contractor/[id]`, `/budget/[code]` and `/honesty` ship server-rendered against the live stack, and the demo city publishes over HTTP for the first time (**ADR-0046** — publication is an act somebody takes, logged as an `admin_action` with a required justification, revocable through the same door). The gate turns on one rule and it is closed by the type system rather than by care: `readZone` converts every count into a `PublishedFigure`, which **cannot be interpolated into JSX**, so the zero the backend deliberately sends beside `suppressed: true` has no path to a screen. Twenty-one browser assertions including Tier D with JavaScript switched off, `axe` clean in both scripts, and a share card that is a real PNG from `satori` + `resvg` on four self-hosted faces. §44 and §E28 are **generated** from the two blueprints and drift-checked, which is how the honesty table stays true — and how `REFRAMED` was found. **Ten defects found** (rows 34–43): four in the backend, one a repository-wide gate that had been failing on a clean checkout since M1, two cross-document inconsistencies in the honesty tables themselves, and two of mine — a date published with a time on it, and a ledger that credited closures nobody confirmed. Both of mine were found by reading the rendered page |
| **M7 — The light table** | **Done** | The console ships: the §E19 shell on `mitti-950` with three persisted densities, `⌘K`, the full keyboard model and a first-class print stylesheet; the four screens the backend already stands behind — **review queue, policy studio and simulation, control-plane admin, developer portal** — and nine ROADMAP screens built against generated types with fixture values behind the §E24 chip. Gated by **44 browser tests across three files** — `console.spec.ts`, `console-real.spec.ts`, `console-roadmap.spec.ts`, and 26 of them new here: F3's shell and keyboard path with no mouse, `axe` clean across three densities × two scripts and over the open palette; F4's decision written from the browser and read back off the complaint's own chain, with the officer's evidence trail asserted to be the citizen's rows plus its filtered ones **by comparing renders**; F5's activation refused by the live guardrail in the server's own words; F6's tenant provisioned, given an invented category and published **entirely through the UI**; F7's closure control disabled with its unmet conditions attached and the blacklist counting *n of m requirements met* rather than hiding. **Nine defects found and fixed**, five of them in this milestone's own work: the skip link was unreachable because the queue's mount-time `scrollIntoView` moved the document's focus-navigation starting point, so an officer's first Tab landed in the middle of the item pane; the palette's arrow keys never moved the highlight, because the keyboard model refuses every key while somebody is typing and the palette's field is a text field; the printed provenance never printed, because an unqualified `display: none` after `@media print` wins the cascade; **the console could not write at all in a default local setup** — `NEMESIS_CONTROL_PLANE_TOKEN` was undocumented in `frontend/.env.example`, so every control-plane write returned the backend's *"supply the header"* and nothing on our side said so; `seed_demo.py` read the public index's `tenant` field as a tenant id when it has always been the slug, which turned a re-run into sixteen 400s that read like a broken control plane. Two more were found by writing F5's gate rather than by running it: **no tenant on this stack gated anything**, so the activation guardrail was a branch nobody had ever seen run — `nem seed-demo` now publishes an evaluation set, and the refusal is asserted; and the golden harness's two live-pipeline baselines were non-deterministic by size, because masking hides content and not layout. And two outside Track E: `worker-ml`'s SIGKILL loop was **not** the OOM ADR-0051 diagnosed but billiard's four-second `worker_proc_alive_timeout` (**ADR-0054**), and the repository's own `ruff format` gate was red on two backend test files |
| **M8 — Clay engine** | **Done** | `WebGPURenderer` in every tier with the backend as the renderer's own selection (ADR-0037), ENU-metre projection, the clay TSL material, instanced pins in one draw call, the 12 fps stepped world under an uncapped camera, the lens stack with bloom reserved for `safety_trigger_fired`, real sun and weather wired to the same monsoon context that normalises contractor SLAs, adaptive quality, context-loss recovery without a reload, the accessible peer list asserted synchronised by digest in every tier, MapLibre + deck.gl on the 2D path, and `<ClayScene>` — the last §E26 contract (A7). **ADR-0047**: the city is generated from the tenant's own origin and is scenery; everything that carries meaning comes from events. **The frame-rate clause failed and the number is published** — ~21 fps at 5 000 pins on this laptop's Radeon 780M against a 60 fps gate, with `BUDGET.fps` and `BUDGET.pins` deliberately unchanged ([`docs/reports/clay-frame-rate.md`](reports/clay-frame-rate.md)). Every other clause passes with room: 14–17 draw calls against 32, 14–22 MB against 512, both backends photographed. **Four defects found**, including headless Chromium reporting `prefers-reduced-motion: reduce` by default — which meant every browser test had been running at Tier C and the clay had never once rendered in CI |
| **M9 — The Walk** | **Done** | §E16's nine acts ship on one damped `t ∈ [0,1]`: a Lenis proxy at `lerp 0.075`, a **Theatre.js** camera whose fourteen keys are authored as source and generated into a drift-checked project state (**ADR-0055**), GSAP's ScrollTrigger on the DOM, and scroll-snap per act. Gated by **26 browser assertions** in `tests/story.spec.ts` plus 36 unit assertions across four files. The two clauses that decide whether this is a film or a demo are both taken: **scroll controls distance travelled, not playback** — the scroll is held still for two seconds of frames and the walked distance is asserted unchanged, in the browser and again as a property of the damping in `tests/story-spine.test.ts` — and **Act 4 mounts the citizen loop's own `<ReportFlow>`**, asserted by comparing what the film renders against what `/report` renders rather than by looking at it. Act 5's five gates are stamped from a complaint filed *through the film*, read off its own ledger by M5's own reader. **Ten golden images** at a fixed `t`, seed, step and sun — nine acts and the storyboard. Every tier forced: Tier C by `prefers-reduced-motion` and again by `?tier=C`, Tier D with JavaScript switched off and all ten acts' copy still present, `axe` clean in both scripts on both. §E11.1's five motions are **audited as five** by a test that reads every stylesheet in `src/` and fails on a duration or curve that is not a token. **One clause is unexercised and published rather than skipped quietly:** Act 6 asserts that it renders nothing until a real `cluster_match_found` arrives, and the case that would watch one arrive skips by name because the committed fixture scores 0.142 against a 0.150 classifier floor and parks before dedup — [`docs/reports/story-merge-gate.md`](reports/story-merge-gate.md). **Five defects found and fixed**, three of them outside this milestone: an unreachable control plane 500ed every non-source locale (`loadStrings` caught the error *response* and not the thrown connection, contradicting its own docstring since M2); Act 4 threw on the server render because a component reused across surfaces brought a `QueryClientProvider` requirement with it; a device probe that throws held the film at *undecided* instead of falling to the storyboard; a pinned film re-snapped its own scroll; and two pre-existing type errors were red on a clean typecheck |
| M10 — Sound & the ladder | Not started | |
| M11 — Field & offline | Not started | |
| M12 — Reconciliation | Not started | |

---

## 3b. The outstanding register

Everything Track E still owes, in one place. Kept here rather than in a second
document so it cannot drift from the milestones it belongs to.

> **Execution detail lives in `docs/FRONTEND-PHASE-PLAN.md`** — M7–M12 and this
> register's open rows, broken into eighteen phases (F1–F18) that can each be
> finished, gated and stopped at. That document owns *order and gate*; this one
> owns *what and why*. The split is only safe because of the rule it opens with:
> **every ship line in M7–M12 and every open row below is claimed by exactly one
> phase there**, and `scripts/check_phase_coverage.py` asserts it in CI from F1
> onward. A second planning document with no such check is how two roadmaps end
> up describing different products.

Three groups, because they fail differently. **Group A is debt inside work
already called done** — the most dangerous kind, because the progress table says
"Done" next to it. **Group B is the milestones.** **Group C is backend work the
frontend cannot proceed without**, each item needing an argued decision rather
than a patch.

---

### A. Owed from M0–M4 — named gaps inside completed milestones

| # | Owed | Where it was promised | Consequence today |
|---|---|---|---|
| ~~A1~~ **Closed — F2, ADR-0053** | ~~Metric-matched fallback faces~~ — ten adjusted `@font-face` rules, one per role, generated by `scripts/fetch_fonts.py --fallbacks` from the committed woff2 and recomputed by `--verify`, so an override cannot drift from the face it describes. Three of the four named descriptors: `size-adjust` is a *ratio* against whatever face the reader's machine resolves, which this repository cannot measure — ADR-0053 argues the omission rather than shipping a table nothing can check | §E10, §E15 "metric-matched fallbacks" | Layout shifts when a face swaps in. Worst on the citizen route, where §E23 budgets LCP < 2.0 s and CLS is the metric nobody notices until Lighthouse says so. Gated in a browser by `tests/type-metrics.spec.ts`: every adjusted face occupies its real face's line box within a pixel at a 100 px em, and CLS on `/report` stays under 0.1 with the real faces delayed |
| ~~A2~~ **Closed — F2** | ~~The locale gate is not asserted~~ — `nem gate-phase18-locale` adds Konkani to a running tenant over HTTP, imports its strings, and asserts the words reach the rendered page. Asserting it found two things that made the clause unmeetable rather than unmet: **no route could add a locale to an existing tenant** (`PUT /control-plane/tenants/{slug}/locales`, `control_plane/locales.py`), and **the language switch was a two-element array in a component**, so a locale added upstream appeared in no switch — it is now built from what the tenant declares, published on every public body | §E25 Phase 18: *"a locale added in the control plane appears in the UI with no code change"* | The single Phase 18 gate clause that cannot be checked without a running stack, and therefore the one most likely to be assumed |
| ~~A3~~ **Closed** | ~~Mutations do not exist~~ — `POST /api/complaints` through the BFF, a key minted with the *draft* rather than with the request, and an optimistic send. A route handler rather than a server action, and §E14.1 already said so: M11 replays a queued submission from IndexedDB after a restart, and a server action's payload is an opaque encoding bound to a build id | M3 ships list: *"Server actions for mutations, carrying a client-generated idempotency key end to end"* | Nothing can be submitted. Also blocks M11: server-side idempotency is what makes the offline queue tractable, and the key has to be generated somewhere |
| ~~A4~~ **Closed** | ~~The polling fallback is a state, not a behaviour~~ — §27.3's five seconds, on the interval the read path's own `Cache-Control` states, driven by `refetchInterval` rather than by a second registry of what is interesting. `refused` is promoted to `polling` only once something polls, so the state and the behaviour agree | §E14.3, §27.3's 5-second poll | A refused upgrade currently degrades to *nothing updating at all*, behind a banner saying the saved state is being refreshed. The banner is honest about the intent and wrong about the fact, which is worse than either |
| ~~A5~~ **Closed** | ~~Nothing mounts the socket~~ — `lib/realtime/bridge.ts` mounts it, `RealtimeProvider` is thirty lines of React around it, and the citizen shell renders the banner. The bridge is a plain function with injected collaborators, so every gate clause is asserted without a component render | §E14.2 | The event bus is a library with no consumer. First consumer is M5's pipeline theatre |
| ~~A6~~ **Closed** | ~~`resyncRequired` has no consumer~~ — the bridge drops every cached read once per transition and clears the flag, because clearing it is a claim that somebody acted on it | §E14.3 | A client past the replay window silently stops being current |
| ~~A7~~ **Closed — F8** | ~~**`<ClayScene>`** — the one §E26 contract not built~~ — the 3D host: `WebGPURenderer` in every tier with the backend as the renderer's own selection (ADR-0037), the adaptive quality manager, context-loss recovery without a page reload, and **the accessible peer list, asserted present and synchronised in every tier** by comparing the digest the canvas was handed against the digest the list rendered. Server-rendered by a client component, so a reader whose JavaScript never runs gets every place and every figure and loses only the highlight | §E26 | Belongs to M8; listed here so the contract table is not read as complete |
| A8 | **No golden images.** `playwright.config.ts` sets `toHaveScreenshot` to zero tolerance and there is nothing to compare against | §E24, and the Phase 20 gate | Visual regression is configured, not running |
| A9 | **Storybook is built, not diffed.** CI builds the catalogue and uploads it | §E24: *"Every visual PR posts its Storybook diff and a five-second scene capture"* | A reviewer can browse it; a pull request does not show what changed |
| ~~A10~~ **Closed — F2** | ~~Density is not persisted, and has no control~~ — `src/lib/density.ts` stores the choice per device and a synchronous script in the document applies it **before first paint**, so the console does not render one mode and reflow into another; `<DensityControl>` is the affordance. F3 moves the control into the §E19 chrome; it sits at the top of the console surface until then | §E19: *"three density modes … persisted per user"* | An officer sets it once per page load, which is not setting it. `tests/density.spec.ts` asserts the choice survives a reload and a new tab, and reads the attribute at `domcontentloaded` — before React has run — because an effect-based implementation passes every other assertion in that file |
| ~~A11~~ **Closed — F2** | ~~RTL is unasserted~~ — `lib/i18n/direction.ts` derives a direction from the locale and the public, citizen and console surfaces set `dir` beside `lang`; `ar` is declared by `nem seed-demo` as tenant *data* and by the application as a locale it can render but ships no words for. `tests/rtl.spec.ts` asserts the frame **by geometry** rather than by the attribute — the same element measured in both directions has to land at the mirror of where it started | §E22: *"RTL-ready layout primitives"* | Ready is a claim until a locale proves it |
| A12 **Accepted** | **The paper is generated, not scanned.** §E6.1 stage 6 asks for *"a scanned paper texture with fibre grain"*; the press composites `feTurbulence` | §E6.1 stage 6 | **Accepted as a deviation, not planned work** — no phase claims it and none should, which is why F1 gave the row an explicit disposition rather than leaving it to read as debt. Revisit only if the generated texture is judged insufficient. Defensible — a generated texture is one the product cannot fail to have, which is §6 Principle #6 — but it is a deviation, and it is recorded rather than assumed |
| A17 | **The control plane cannot carry product copy, and the frontend implies it can.** `loadStrings` resolves three tiers — base, seed, control plane — and the third fetches `/api/v1/control-plane/translations/{namespace}/{locale}` for `common`, `citizen`, `console` and `public`. The registry carries none of them: `db/models/i18n.py` lists `taxonomy`, `organisation`, `zone` and `calendar`, and an import into `public` is refused. So that tier resolves to `{}` on every request it will ever make | §E10.1, §E22, §E25 Phase 18 | Found by writing A2's gate, which is the argument for writing gates. **Nothing is broken and nothing renders wrong** — the seed bundles carry the words and the merge degrades exactly as designed — but a tier that cannot ever return anything is a capability the code claims and does not have, and §E3.3 is a rule about precisely that. The decision is not a patch: either product copy becomes tenant-importable, which reopens *"a tenant could overwrite the wording of a legal notice"* that `db/models/i18n.py` settled — weakened, but not answered, by C7 moving the §22.2 notices into code — or the frontend stops implying it can and the tier is removed. F18 is where the honesty table is verified line by line, which is where a claim the product cannot support belongs |
| A13 | **The no-off-origin assertion covers fonts only.** `tests/type.spec.ts` fails on a font fetched from a third party; images, scripts and audio are covered only by the source-level CDN grep | §6 Principle #6, §E24 | A runtime fetch the grep cannot see would pass |

### Cross-cutting gates not yet wired

| # | Gate | Source | State |
|---|---|---|---|
| ~~A14~~ **Closed — F1 + F3** | ~~Lighthouse ≥ 90 performance and accessibility on citizen and department routes~~ — `frontend/lighthouserc.json` asserts both category scores in CI on three routes, three runs and the median, against a **production** build on its own port, because a Lighthouse run that silently measured the dev server would report ~0.65 for reasons that have nothing to do with the product. F1 landed the citizen and public routes; F3 built the department shell, which is what turned the third URL from a passing check with nothing behind it into a measurement. Measured on the reference machine under the same mobile emulation as the other two — the stricter reading, since an officer's console is a desktop surface: **/console 97–99 performance, 100 accessibility**; /report 93–99 and 100; /pune-demo/honesty 86–97 and 100, which clears the budget on the median and is the one route sitting near the line — it is a forty-row document and its LCP is that document. The console's accessibility is additionally swept by `console.spec.ts` across three densities × two scripts and over the open `⌘K` palette, and by `console-roadmap.spec.ts` over all seven fixture screens, because a category score is an average and §E22's floor is not |
| A15 | **WCAG 2.2 AA verified by audit, not only by automated scan** | §E25 Phase 18, §E22 | The scan half is done and clean across twelve combinations. The audit half is a person, and it has not happened |
| A16 | **Measured task-success rate from a usability session** with real field staff and department users, findings tracked | §E25 Phase 18 — *"the plan calls this a design practice, not a component library, and that distinction is the gate"* | Not started. The only Phase 18 clause that no amount of code closes |

---

### B. The milestones

**M5 — the citizen loop.** Capture in the viewfinder; place as a stated card with
a 60 px pin; optimistic send; the pipeline theatre with its **third outcome**;
the dedup payoff from the real engine; the receipt; the tracking ledger with
`why? →` opening the real rubric; the Ledger passport. Closure (§E17.5) and the
ward film (§E17.6) scaffold against real types behind the not-wired chip.
Carries **C1, C2 and C3** below, and closes **A3, A4, A5, A6**.

~~**M6 — the public surface.**~~ **Done.** SSR place / contractor / budget
pages, `rating_disclaimer` and `SYSTEM_FLAGGED_NOTICE` as first-class UI,
suppression as an honest empty state, contractor ledgers with the response frame
present before there is anything in it, `satori` + `resvg` share cards, and §44 +
§E28 generated from the blueprints and served as JSON. Tier D asserted with
JavaScript switched off. Carried **C5** (ADR-0046) and surfaced **C6–C8**.

**M7 — the light table.** The console shell, `⌘K`, three persisted densities
(A10), a first-class print stylesheet. REAL today: review queue, policy studio
and simulation, control plane, developer portal. ROADMAP behind the chip: role
shells (13), area view and the underreporting signal (12, 23), work order and
contractor picker (14), milestone strip (14), closure gates (15), money (14, 23),
integrity room and case file (17), report builder (23).

> **The ship lines, named.** From F1 onward every line below carries an id, and
> `scripts/check_phase_coverage.py` asserts each is claimed by exactly one phase
> in `docs/FRONTEND-PHASE-PLAN.md`. The ids exist for that check and for no
> other reason: a prose paragraph cannot be mechanically compared against a
> plan, and a rule that cannot be executed is the rule that lets two roadmaps
> describe different products.

- **M7.1** The console shell
- **M7.2** `⌘K`
- **M7.3** Three persisted densities (A10)
- **M7.4** A first-class print stylesheet
- **M7.5** Review queue — REAL
- **M7.6** Policy studio and simulation — REAL
- **M7.7** Control plane — REAL
- **M7.8** Developer portal — REAL
- **M7.9** Role shells (13) — ROADMAP behind the chip
- **M7.10** Area view and the underreporting signal (12, 23) — ROADMAP
- **M7.11** Work order and contractor picker (14) — ROADMAP
- **M7.12** Milestone strip (14) — ROADMAP
- **M7.13** Closure gates (15) — ROADMAP
- **M7.14** Money (14, 23) — ROADMAP
- **M7.15** Integrity room and case file (17) — ROADMAP
- **M7.16** Report builder (23) — ROADMAP

**M8 — the clay engine.** `WebGPURenderer`; ENU-metre projection; the clay TSL
material; instanced pins in one draw call; the 12 fps stepped clock driving the
world with an uncapped camera; tilt-shift, gate weave, and bloom reserved for
`safety_trigger_fired`; real sun and weather wired to the monsoon SLA context;
adaptive quality turning the press dial first; context-loss recovery; **the
accessible peer list in every tier**; MapLibre + deck.gl; `<ClayScene>` (A7).
Gate: 60 fps with 5 000 pins **with Ollama running**, VRAM ≤ 512 MB asserted in
CI.

- **M8.1** `WebGPURenderer`
- **M8.2** Web Mercator → local ENU-metre projection
- **M8.3** The clay TSL material
- **M8.4** Instanced pins in one draw call
- **M8.5** The 12 fps stepped clock driving the world, camera uncapped
- **M8.6** Tilt-shift, gate weave, and bloom reserved for `safety_trigger_fired`
- **M8.7** Real sun and weather wired to the monsoon SLA context
- **M8.8** Adaptive quality turning the press dial first
- **M8.9** Context-loss recovery
- **M8.10** The accessible peer list in every tier
- **M8.11** MapLibre + deck.gl
- **M8.12** `<ClayScene>` (A7)
- **M8.13** The gate: 60 fps with 5 000 pins with Ollama running, VRAM ≤ 512 MB in CI

**M9 — the Walk.** Nine acts on one damped `t`; Theatre.js camera; GSAP for DOM
and type; Rive characters as event-driven state machines; the merge with its
overprint, thumbprint and registration rings; Tier C's nine prints as a reviewed
design deliverable. Gate: **every scene fired by a genuine backend event — a
scene that can only be fired by a button fails.**

- **M9.1** Acts 0–3 on one damped `t`
- **M9.2** Acts 4–5
- **M9.3** Acts 6–9
- **M9.4** Theatre.js camera
- **M9.5** GSAP for DOM and type
- **M9.6** Rive characters as event-driven state machines
- **M9.7** The merge with its overprint, thumbprint and registration rings
- **M9.8** Tier C's nine prints as a reviewed design deliverable
- **M9.9** The gate: every scene fired by a genuine backend event

**M10 — sound and the ladder proven.** The Web Audio graph, positional foley, the
merge cue, the single struck note. The §E3.4 audit as a usage grep: no second use
of bloom, the stamp, or a severity colour. Every tier S/A/B/C/D forced in CI.

- **M10.1** The Web Audio graph
- **M10.2** Positional foley
- **M10.3** The merge cue
- **M10.4** The single struck note
- **M10.5** The §E3.4 audit as a usage grep
- **M10.6** Every tier S/A/B/C/D forced in CI

**M11 — field and offline.** PWA, offline queue surviving restart, outdoor mode
at 7:1, EXIF-preserving compression, conflict-free sync.

- **M11.1** PWA
- **M11.2** Offline queue surviving restart
- **M11.3** Outdoor mode at 7:1
- **M11.4** EXIF-preserving compression
- **M11.5** Conflict-free sync

**M12 — reconciliation.** §E28 and §44 verified line by line; §E27 audited; the
usability session (A16); ADR index and PHASES amended. Gate: **no REAL row backed
by a fixture.**

- **M12.1** §E28 and §44 verified line by line
- **M12.2** §E27 audited
- **M12.3** The usability session (A16)
- **M12.4** ADR index and `docs/PHASES.md` amended
- **M12.5** The gate: no REAL row backed by a fixture

---

### C. Backend work the frontend needs

Each of these is a decision, not a patch, and each gets an ADR. They are listed
apart from the milestones because none of them can be taken unilaterally by
Track E.

| # | Needed | Blocks | The decision to argue |
|---|---|---|---|
| ~~C1~~ **Landed — ADR-0043** | ~~A per-complaint event history endpoint~~ | §E17.4's ledger, `<EvidenceTrail>` with real data, §E28's "Tracking ledger from the event log: REAL" row | What may a citizen see of their own complaint's history? That is a different question from ADR-0016's default-deny, which governs a *broadcast* stream to anyone. The log is hash-chained per entity, so the data exists and the shape is the envelope already published |
| ~~C2~~ **Landed — ADR-0044** | ~~The chain hash, published~~ | §E17.3's receipt | Which hash: the event's, or the entity's chain head? The receipt's claim is *"this record cannot be edited"*, so it wants the head — and the head has to be readable without leaking the log |
| ~~C3~~ **Landed — ADR-0045** | ~~Payload shapers for `exif_check_completed` and `media_redacted`~~ | §E16.1's gates 2 and 5 — *"EXIF INTACT · DEVICE NOT ON WATCHLIST"* and *"a face visibly blurs on the photograph itself"* | ADR-0016 is default-deny by construction. What may a browser learn about an EXIF check, or about where a face was? Booleans and counts, certainly; coordinates and boxes, certainly not. The line between needs writing down |
| ~~C5~~ **Landed — ADR-0046** | ~~A tenant cannot be published over HTTP~~ | Every §E18 page; `nem seed-demo` | `public_deps.py` calls this *"a disclosure decision no engineer is entitled to make on their behalf"* and then left `psql` as the only way to make it. Is publication a field on `TenantSpec`, or a separate act? Separate: a tenant that publishes from the instant it is provisioned has published before anybody looked at what is in it |
| **C6** **Owned by backend Phase 12** | **A complaint is never assigned a place.** `complaints.ward` is nullable and unwritten, so `zone_summary`'s `Complaint.ward == zone_code` can never match | §E18's place figures; §E28's *"Public zone / ward / contractor / budget pages: data REAL"* row, which is corrected to ROADMAP until this lands | Where does a report acquire its ward — at ingest, in the log, or recomputed at read time? The projection cannot do it without breaking its own rule that every column derives from an event, and read-time recomputation puts a PostGIS join on an unauthenticated endpoint. It looks like Phase 12's `ROUTING`, which is a declared stage with no provider and whose job is exactly assigning department and place. Defect row 35 |
| **C9** **Owned by backend** | **A ward's boundary is write-only.** `ZoneSpec.boundary` is on the control plane's write side and **no read endpoint publishes it** — not `OrgUnitResponse`, not the public zone index. So the 2D map path can draw a tenant's *places* and not its *shapes* | §E15's 2D and heavy-layer path (`clay/FlatMap.tsx`); ADR-0047, which argues that boundaries belong on that path drawn as boundaries rather than extruded into scenery | Surfaced by F11, which needed them and drew none rather than putting something else in their place. Not a large change — the geometry is already stored — but a real question about which surface may read it: a ward outline is a disclosure a municipality makes, and ADR-0046 established that publication is an act somebody takes. Probably the same door as the public zone index, behind the same publication decision |
| ~~C7~~ **Landed — ADR-0052** | ~~The public surface cannot speak Marathi.~~ `SYSTEM_FLAGGED_NOTICE`, `RATING_DISCLAIMER` and `zones.name` are English constants on every response; `NAMESPACE_ZONE` exists and the endpoints never consult it | §E18's *"first-class UI"* rule, and §16.2's audience | What may a §22.2 disclaimer be translated *by*? Not by the client — that is the client asserting its own legal text, which is why the frontend renders the server's words verbatim through `notTranslatable()`. So the registry has to carry them, and somebody has to own the translation as a legal artefact rather than as a string. Defect row 36 |
| ~~C8~~ **Landed — ADR-0052** | ~~A tenant's display name is not published~~ — only its slug | Every §E18 page heading, which currently title-cases the slug | Trivial as a change and listed here anyway, because the alternative the frontend was tempted into is a lookup table of cities we happen to know about, which is a second source of truth for a fact the platform holds. Defect row 37 |
| ~~C4~~ **Done** | ~~§E28's row corrections~~ — restated with two columns, *Component* and *Data*, because the single column was answering two questions at once | Nothing — but it is currently inaccurate | *"Tracking ledger from the event log: REAL"* is false until C1 lands. §E28 is the honesty table; an honesty table that is wrong is worse than no table |

**Raised by M5, owed by M6.**

| # | Needed | Blocks | The decision to argue |
|---|---|---|---|
| C5 | **A tenant can opt into the public API over HTTP** | §E18's whole surface. Every public route is `/api/v1/public/{tenant_slug}/…` and answers 404 unless `public_api_enabled` is true | ADR-0021 makes publishing an opt-in per customer, defaulting to off, and argues it well: *"a municipality that has not decided its disclosure posture is not a municipality that has decided yes."* But **nothing can set it** — `TenantSpec` has no field and no endpoint writes one — so a provisioned tenant can only be published with SQL, and the decision the ADR protects cannot actually be taken. The default stays `false`; the argument is about giving the customer a way to say yes |
| C6 | **A slug resolves to a tenant id** | Any operator tooling that runs twice. `nem seed-demo` needs `--tenant-id` on a re-run for exactly this reason | Small, and it is a real question rather than an oversight: an unauthenticated endpoint mapping slugs to ids is an enumeration surface, and the control-plane token is the only control available before Phase 13. Probably a token-gated `GET /control-plane/tenants/{slug}` |
| C9 | **A ward's boundary is readable** | The 2D map path, which draws a tenant's places today and would draw its wards | The geometry exists; only a read does not. See the row above for the disclosure question it carries |

### The demo tenant

`nem seed-demo` provisions **Pune (demo)** through the control plane — no SQL,
no fixtures, no code change, which is Phase 5's own claim exercised as a
side effect. Nine wards with approximate boundaries labelled as such in the
data, a locality nested inside one so §E17.1's card renders a real chain, twelve
taxonomy nodes, sixteen prompt sets, and `en` + `mr` + `ar` — the third for
A11, seeded as *data* rather than shipped as a product language, because
NEMESIS publishes no Arabic copy and a right-to-left frame around English words
is exactly what the assertion needs and all the demo can honestly claim.

`--reports N` then submits reports **through `POST /api/v1/complaints`**, which
is the part that matters: the demo's complaints carry real hash chains, real
projections and real outbox rows, and the pipeline decides what happens to them.
The seeder chooses what was reported and where; it does not choose the outcomes.
Several reports park at `pending_classification`, and that is §24.2's third
outcome appearing in the demo because it appears in the product.

**Its imagery is procedurally drawn, and the classifier's opinion of it is
correspondingly weak.** `scripts/demo_imagery.py` states why in full: this
repository does not ship third-party media it has not licensed, and §22.1's
face-blur stage is real — seeding it with photographs of identifiable people, in
order to demonstrate removing them, is not a trade worth making. The cost is
that a generated street is not a street, so reports classify on the strength of
their *text* and some abstain. Stated rather than tuned around: lowering the
tenant's confidence floor until synthetic evidence passed would be manufacturing
the demo's own result, which is what ADR-0034 exists to forbid.

---

### Stack not yet installed

Deliberate — each arrives with the milestone that needs it, so `npm ci` stays
fast and an unused dependency cannot rot in the lockfile.

`@react-three/fiber` · `@react-three/drei` · `three-mesh-bvh` · `r3f-perf` (M8) ·
`maplibre-gl` · `deck.gl` (M8) · `lenis` · `gsap` · `@theatre/core` +
`@theatre/studio` (M9) · `@rive-app/react-canvas` (M9) · `gltf-transform`
(M8 assets) · opus sprites (M10).

`three` **is** installed — `press-tsl.ts` compiles against it today.
`@tanstack/react-query` **is** in use since M5, driving §27.3's polling fallback
(A4).

**`satori` + `@resvg/resvg-js` were never installed, and M6 shipped the share
cards anyway.** `next/og` bundles both — satori as vendored source and resvg as
`resvg.wasm` — so adding them as direct dependencies would have put a second
copy of each in the lockfile to do what the framework already does. The one cost
is real and is recorded on `OgFace` in `scripts/fetch_fonts.py`: satori parses
TTF/OTF/WOFF and **not** WOFF2, so the four faces the card uses are built offline
from the shipped WOFF2 and committed — ~360 kB that would otherwise not exist.

### Assets to author

Not code, and none of it exists yet. Listed because a milestone that assumes it
will appear is a milestone that stalls.

- **The modular clay city kit** — Blender → `gltf-transform` (Draco/Meshopt) → KTX2, AO baked to vertex colours (§E15, M8).
- **Four Rive state machines** — the Reporter, the Officer, the Field Hand, the Auditor, with the named inputs §E8.1 specifies (M9).
- **Tier C's nine riso prints** — a design deliverable with its own review, not a generated fallback (§E3.2, §E13, M9).
- **The sound library** — three ambient beds on the clock, positional foley per defect type, interaction foley recorded from paper and clay, the merge cue, the struck metal note (§E12, M10).
- **A scanned paper texture** (A12), if the generated one is ever judged insufficient.

---

## 4. Standing rules — checked every milestone, not at the end

| Rule | Enforced by | Source |
|---|---|---|
| A hand-written colour literal in `src/` | ESLint rule, CI | §E24 |
| A hand-written type describing a backend contract | Review + `generated/` drift check | §E24, Law 2 |
| A GLSL string or `ShaderMaterial` in `src/` | CI grep with an explicit vendored allowlist | ADR-0037 |
| A CDN-hosted asset of any kind | CI grep + Playwright network assertion | §6 Principle #6 |
| `any` in application source | `tsc` + lint | §E25 Phase 18 |
| A ROADMAP screen on a public URL | Route test | §E24 |
| A flagged item rendered in a severity colour | Type contract + review | ADR-0039, §22.2 |
| Text touched by the press | Byte-identity test | ADR-0038, §E6.2 |
| A severity shown by colour alone | Component contract | §E9.4 rule 2 |

---

## 5. Defects found in the source documents while planning

Recorded here in the pattern §E2 established, and fixed in place rather than
worked around. A cross-reference that points at the wrong document is a defect
in a plan whose whole method is traceability.

| # | Defect | Where | Correction |
|---|---|---|---|
| 1 | §E14.1 records the BFF seam as **ADR-0039**; 0039 is the flag-colour decision. The BFF is **ADR-0040** | `NEMESIS-Frontend-Blueprint.md` §E14.1 | Reference corrected to ADR-0040 |
| 2 | §E2 defect #11's "Corrected by" column cites **ADR-0039** for the same reason | §E2 row 11 | Corrected to ADR-0040 |
| 3 | §E15 credits Rive to **ADR-0040**; the character decision is **ADR-0041** | §E15 character-animation row | Corrected to ADR-0041 |
| 4 | §E6.2's text exemption is the subject of ADR-0038 but does not cite it | §E6.2 | Citation added |
| 5 | `docs/PHASES.md` Phase 20 still specifies **custom GLSL** and a **Leaflet DOM marker** fallback, both superseded by §E15 and ADR-0037 | `docs/PHASES.md` Track E | Amended, with the supersession stated rather than the text silently swapped |
| 6 | `docs/PHASES.md` Phase 19 specifies `WebGLRenderer`; §E15 and ADR-0037 ship `WebGPURenderer` with an automatic WebGL2 backend | `docs/PHASES.md` Phase 19 | Amended the same way |
| 7 | **§E9's own role labels do not clear §E22's floor.** Measured: `riso-aqua` as *SIGNAL — links, focus rings, primary action* is **2.51:1** on paper-50; `mitti-500` as *secondary text on light* is **3.80:1**; severity ink is **4.17:1** on kraft-200 | §E9.1, §E9.2 vs §E22 | Fixed without repainting the Riso inks, which are the premise of the direction (§E4). A `role` layer in `tokens.json` derives text-safe values by **overprint** (§E6.3) — `aqua × federal blue` = 10.60:1, `mitti-500 × mitti-300` = 6.54:1 — so the accessible colour is produced by the press's own mechanic rather than invented. Severity type sits on its `tint` field, never on a table stock, which is what the four-channel model in §E9.4 was already for |
| 8 | **`mitti-300` as a rule is 1.36:1 on kraft-200** — a hairline nobody can see on the zebra stock | §E9.1 | Rules moved to `mitti-500` (2.79:1 worst case). WCAG asks nothing of a decorative rule; this project sets its own floor, because an invisible rule is still a defect |
| 9 | **§E6.2's text exemption is not achieved by layering alone.** A blended or filtered sibling promotes the stacking context to a compositor layer, and Chromium answers by switching type from subpixel to grayscale antialiasing — so text rendered *differently* with the press on, without the press ever touching it | §E6.2, ADR-0038 | The text layer forces grayscale antialiasing unconditionally. Correct on its own terms too: subpixel AA works by putting **colour fringes** on glyph edges, and colour fringing is exactly what misregistration is — which is reserved for imagery. Verified by removing the rule and watching the gate fail |
| 10 | **`high` and `medium` sit 1.4% apart in grayscale.** On a monochrome printout they are the same grey | §E9.4 | Not a palette defect — it is the case §E9.4 rule 2 exists for, and the rule holds because the two carry a *filled* and a *hollow* circle. Now asserted: any severity pair within 5% grey must differ in mark, not merely in colour. §E19.7 establishes that officers print, so this is a shipping condition, not a hypothetical |
| 11 | **Kaana is not obtainable.** §E10.1 names it as the Devanagari display face; it is not in Fontshare's 100-font catalogue and is not available under a free commercial licence | §E10.1 | **Sarpanch** takes the role — Indian Type Foundry, OFL, Devanagari and Latin, squared and straight-lined and wide. §E10.1 asked for a face *"built from straight lines and triangular geometries"* that *"pairs with Panchang's width"*; the requirement is met, the name was not available. The ITF argument — an Indian foundry for an Indian civic product — survives intact |
| 12 | **`font-variant-numeric: tabular-lining` is not a CSS value.** It computes to `normal` | §E10.2 | So §E10.2's first "hard rule" — *every number uses tabular figures* — was, as written, doing nothing at all. On a product whose proposition is trust in columns of costs, that is the most expensive one-word defect in the document. The real keywords are `lining-nums tabular-nums`. Caught by `tests/type.spec.ts` reading the computed style |
| 13 | **A flat +0.15 Devanagari leading delta clips the matras.** Measured across the whole scale, Devanagari ink runs to **1.289em** at `display-2` and 1.286em at `doc`, against Latin display leading of 0.94–1.04 | §E10.1 | The rule becomes a delta **and a floor**: `max(latin + 0.15, 1.35)`, where 1.35 is the measured ink ceiling plus clearance. The consequence is real and correct — Devanagari display type is *looser* than Latin display type, because the script stacks matras above the shirorekha and below the baseline. You cannot set Devanagari at 0.86. §E2 defect #4 said shirorekha clearance is not retrofittable; this is what that costs when you actually measure it |
| 14 | **The complaint read schema was never published.** `GET /api/v1/complaints/{id}` and `POST /api/v1/complaints` return a raw `Response` — they own their own ETag and 304 — so FastAPI inferred nothing and the OpenAPI document recorded `{}` for the two hottest reads in the product | `backend/nemesis/api/v1/complaints.py` | §E1 says the severity fields *"are already fields on the v1 complaint read schema"*. True of the Pydantic model, false of the published contract — and the published contract is the only thing a generated client can see, which makes Law 2 unenforceable exactly where it matters most. Fixed by declaring the models in `responses`; `api_contract_lock.json` now protects **15 fields** on that path where it protected none |
| 15 | **The realtime envelope had no published shape at all.** OpenAPI 3.1 cannot describe a WebSocket, so the stream that drives the map, the pipeline theatre and the merge contributed nothing to the document | `backend/nemesis/realtime/envelope.py` | Every browser client would have had to hand-write it, which §E24 makes a review failure — and which fails *silently* the day a field moves, because the client keeps type-checking and starts lying. `RealtimeEnvelope`, `RealtimeHeartbeat` and `RealtimeResyncRequired` are now models merged into the exported document as components |
| 16 | **§E26.1's status vocabulary was not published either** — `ComplaintResponse.status` is typed `str` | `backend/nemesis/api/v1/schemas.py` | §E2 defects #15 and #16 are both instances of this vocabulary going unwritten, and §E26.1 answers by writing it into the blueprint. A list in a document is still a list somebody retypes. All six lifecycle enums are now published as components and generated into **both** a union type (so a chip stops compiling when a member is added) and a runtime array (so a matrix can enumerate them) |
| 17 | **Only 8 of 33 registered event types carry a payload on the wire.** ADR-0016 makes realtime payloads default-deny; the shaped set is `citizen_confirmed`, `classification_scored`, `cluster_created`, `cluster_match_found`, `pipeline_stage_degraded`, `safety_trigger_fired`, `severity_scored`, `work_order_created` | §E27 vs ADR-0016 | **The most consequential finding so far, and it is not a bug — it is a design collision.** §E27 maps twenty-four event types to visuals and calls a visual element not on that list a defect. §E16.1's pipeline theatre stages six gates. Of those six, `exif_check_completed` and `media_redacted` reach the browser with an *empty payload*, so *"EXIF INTACT · DEVICE NOT ON WATCHLIST"* and *"a face visibly blurs on the photograph itself"* cannot be driven from the stream as specified. Adding a shaper is a small change and a real privacy decision — what may a browser learn about an EXIF check, or about where a face was? — so it is **not** being made silently. It is scheduled into M5 with its own ADR, because that is where the theatre lives and where the decision has to be argued. The generated `RealtimeShapedEventType` union means any surface that assumes otherwise now fails to compile |
| 18 | **Nothing serves a complaint's event history.** The log is append-only and hash-chained per entity, and there is no read path for it — `GET /complaints/{id}` returns the projection, and the two bulk-export datasets are `complaints` and `work-orders` | §E17.4, §E26, §E28 | §E28 marks *"Tracking ledger from the event log"* **REAL**. It is not: `<EvidenceTrail>` can be built — and is, against the published envelope type, so Law 2 holds — but it can only show the events that arrive while the page is open, which is not a ledger of what happened before you got there. §E17.4's whole argument is that *"'In Progress' is the enemy"*; a ledger that starts when you open it is a status badge with extra steps. The endpoint is scheduled into M5, and §E28's row needs correcting to **component REAL, data ROADMAP** |
| 19 | **The chain hash is not published.** §E17.3's receipt *"carr[ies] the complaint id and chain hash"*; `ComplaintSubmissionResponse` publishes `complaint_id`, `status` and `estimated_processing_time_seconds`, and no endpoint exposes an entity's chain head | §E17.3 | *"Nobody reads the hash. Everybody feels that this system keeps records."* The feeling is the product; the hash is what makes the feeling true. `<Receipt>` takes it as an optional prop and renders **nothing** where it would go rather than a placeholder — §E3.3 — so the omission is visible instead of faked. Owed at M5 alongside #18, since both are the same read path |
| 20 | **`opacity: 0.8` on the severity score dropped all five below the floor** — 3.59:1 at worst. Mine, not the blueprint's | `components.css` | Worth recording as a *process* finding rather than a typo. The token-level contrast suite measured design intent and passed; only a real engine measured what shipped. §E22 says WCAG 2.2 AA is *"audited rather than only scanned"* — this is the case for having both, and `axe` is now in the gate rather than in a plan |
| 21 | **The light-table badge rendered a pair nobody had measured.** The CSS filled the badge with the glaze and set the tint on it: **2.63–4.37:1** across the five levels, failing all twelve matrix combinations | `components.css` vs §E9.3 | The token test asserted tint-on-**room** (13.48–15.04:1); the component rendered tint-on-**glaze**. Two different pairs, one of them tested. It was also the wrong picture — a solid coloured chip is the inverted palette §E9.3 explicitly refuses. Corrected to what §E9.3 actually describes: the ink glows, the room stays behind it, and the glaze draws the mark and a printed edge at 3.09–5.72:1, clearing WCAG 1.4.11's 3:1 for a meaningful graphic. The contrast suite now asserts **both** pairs, and asserts that tint-on-glaze stays *below* 4.5 so the glaze is never asked to carry a word |
| 22 | **`<dl>` with a direct `<p>` child** in the contractor ledger | `ContractorLedger.tsx` | Invalid list structure, so a screen reader reads a contractor's public record wrongly. Caught by `axe`. Not cosmetic on the one surface in this product where a named commercial entity's numbers are published |
| 23 | **The severity label was in the token file.** §E9.4's table pairs ink, shape and label, and a first pass carried all three across | `tokens.json` | A token file cannot be translated by the Phase 5 locale registry, so a severity labelled from there reads *"Critical"* on a Marathi console forever — and Phase 18's gate is that a locale added in the control plane appears with no code change. Colour and shape are design; words are content. The label moved to `src/i18n/base/common.json`, and keeping it in both would have been two sources for one string |
| 24 | **The dead-letter table could not record why a stage failed.** `tasks.py` built `failure_mode=f"abstained:{exc}"[:200]` against a `varchar(128)` | `nemesis/pipeline/tasks.py`, `db/models/outbox.py` | Not a truncation — a **refusal**. Postgres raises `StringDataRightTruncationError` rather than trimming, so the insert failed and **every abstained classification lost its dead letter**, leaving a SQLAlchemy traceback in a worker log as the only trace. §24.2's claim is that the pipeline *degrades rather than loses*, and that claim rests entirely on the parked report being findable. Fixed at the shape rather than the number: `failure_mode` is a short greppable label, the sentence goes to `last_error` (which is `Text`), the clamp lives at the single write site against the column's own exported constant, and two regression tests pin it |
| 25 | **`PUT /control-plane/taxonomy/prompt-sets` 500'd on every update.** `upsert_prompt_set` ended with `session.expire(existing); return existing` | `nemesis/control_plane/taxonomy.py` | An expired instance defers its reload to whoever touches an attribute next, and in an async session that reload is blocking IO outside a greenlet — so the *caller* got `MissingGreenlet` instead of a row. The endpoint only ever inserted until a seeding script re-ran it, which is why nothing had noticed. `await session.refresh()` instead: returning an object whose next attribute access raises is a trap for every future caller, so it is fixed at the source rather than worked around at the call site |
| 26 | **Generic negative prompts made the classifier abstain on everything.** A first prompt set attached three universal negatives — *"an ordinary intact road surface"*, *"a clean and undamaged street"* — to all eight nodes | `scripts/seed_demo.py` | `perception/scoring.py` states the mechanism precisely: a negative enters the **same softmax** as an extra competitor, so *"a negative that matches strongly suppresses the confidence of every category, not only the one whose prompt set it belongs to."* Every street photograph matches a universal negative, so all eight contrast logits ran high and the winner collapsed to **0.120** against a 0.150 floor — chance, for eight categories. The scoring layer was behaving exactly as documented and the prompts were authored wrongly. Rewritten as *near-misses of their own category* — a shadow is not a pothole, a closed manhole is not an open drain — and the same report then scored **0.214** and clustered |
| 27 | **The press screened blank stock, and §E17.3's receipt measured 2.86:1.** `.press__screens` painted a full-strength halftone field across the whole press box whether or not there was imagery | `press.css`, `Press.tsx` | Every layer was individually correct: near-black ink, pale stock, type exempt from the press per ADR-0038. The *paper underneath the type* was not exempt, which is half an exemption — and no assertion covered the difference, because `contrast.test.ts` measures design intent from the token file and the press is applied afterwards, over a ground the tokens never name. §E6.1's stages split into ink (1–3, 5) and paper (4, 6); a sheet with no imagery has paper and no ink. Screens now stop where there is nothing to screen, grain and deckle stay, and the sentence reads at **6.14:1**. New gate: `tests/press-contrast.spec.ts` measures rendered pixels, because §E2 defect #20 is the same lesson and it did not stick |
| 28 | **`opacity: 0.75` on the ledger's hash column: 3.75:1.** Mine, and a verbatim repeat of defect #20 | `components.css` | `--role-text-secondary` is an *overprint* measured to sit just above the floor (§E2 defect #7), so any transparency on top of it spends exactly the margin that measurement bought. Caught within a minute by the gate written for defect #27, which is the argument for writing it. Removed here and in two other places; quietness comes from ink and size, never from alpha |
| 29 | **The M5 gate was asserting against a report the pipeline had stopped processing.** The E2E fixture was a JFIF header plus 512 zero bytes | `tests/citizen.spec.ts` | Enough for §25.1's magic-byte sniffer — all the *upload* path inspects — and not decodable. Phase 8 decodes: ADR-0032 halts when the face detector cannot run and §22.1 fails closed, so the trust stage degraded and the report parked at `pending_classification` having never reached classification or dedup. *"Every one of the six gates is driven by its real event"* was therefore being asserted against the failure path while claiming to test the success one. Replaced with a committed, procedurally generated, reproducible photograph and a README that says what it is and is not |
| 30 | **`check-guards.ts` fired on two correct shapes.** `import { type Complaint }` inside a multi-line import block, and `type X = components["schemas"]["Y"]` | `scripts/check-guards.ts` | Importing a type is not declaring one, and naming a generated type is what Law 2 *asks for* — `realtime/envelope.ts` has done it since M3 and escaped only because `RealtimeEnvelope` is not on the entity list, which is luck rather than design. Fixed in the guard rather than exempted at the call sites: an exemption comment on a correct line teaches the next reader that the rule is approximate. Two fixtures added, including a positive one — an alias *intersected* with extra fields is still a re-declaration, and it is the case a naive fix would have waved through |
| 31 | **The degradation banner's sentence was an English literal in `src/`.** `socket.ts` set `cause: "Live updates are switched off…"` | `realtime/socket.ts`, `store.ts` | Which made the one banner a citizen sees when the system is degraded the single piece of copy the Phase 5 locale registry could never reach — against Phase 18's own gate, on the surface where being understood matters most. `Degradation.cause` is now a **key**, resolved where a `Strings` is in hand, and both bundles carry the sentence |
| 32 | **A parked report left its downstream gates waiting forever.** Found by running the product, not by reading it | `citizen/gates.ts` | Classification abstains → §24.2 parks the report → the stages after it never run → the dedup gate sat at *"not compared yet"* indefinitely while the theatre polled every 1.2 s. §24.2's rule is that the card **continues** and §E17.2's is that the wait is legible; an unreachable gate pretending to still be waiting is neither. Gates now read the two halting signals off the chain — `safety_trigger_fired`, and a `pipeline_stage_degraded` carrying a halting fallback — and report **held** instead, which settles the theatre and stops the poll |
| 33 | **Nothing published a read of the place tree.** `zones` has carried a `MULTIPOLYGON` boundary and a GiST index since Phase 5; `GET /control-plane/zones` returns name, code, kind, parent and path — enough to draw the tree, nothing about where any of it is | `nemesis/api/v1/places.py` (new) | So a browser could learn Ward 14 exists and could not learn it was standing in it, and §E17.1's *Place* card — *"presented as a card, not a picker"* — had nothing to say. Track E cannot answer it client-side: point-in-polygon needs the polygons, publishing every ward boundary is a payload in megabytes, and a third-party geocoder is banned outright by §6 Principle #6. `GET /places/resolve` answers it where the geometry already lives, returning the chain innermost-first. **It does not produce *"Paud Road, near Karve Statue"***: street-level reverse geocoding needs a street graph this product does not have and may not fetch, so the card renders the street line not at all rather than guessing at one (§E3.3) |
| 34 | **No endpoint maps a tenant slug to its id, and `public_api_enabled` cannot be set over HTTP at all** | `control_plane`, `TenantSpec` | Surfaced by a seeding script that had to re-run against an existing tenant. The public index publishes `tenant`, but only for a tenant that opted into the public API — which ADR-0021 makes a deliberate per-customer decision defaulting to off, and which `TenantSpec` has no field for and no endpoint sets. So a provisioned tenant can never be published without SQL. Recorded rather than patched: it is M6's blocker, not M5's. **Closed at M6 by ADR-0046, and not the way this row predicted.** A `TenantSpec` field was the obvious fix and is the wrong one: provisioning applies a template to a tenant with zero complaints in it, so a tenant that publishes from the instant it exists has published before anybody looked at what is in it — and it collapses an operations act and a municipality's disclosure decision into one call. `PUT /control-plane/tenants/{slug}/publication` instead, token-gated, revocable through the same door, appending an `admin_action` with a required justification. The prediction is left in place rather than rewritten, because the argument that changed it is the record |
| 35 | **`complaints.ward` is never written, so no ward summary can ever count anything.** `public.aggregates.zone_summary` filters on `Complaint.ward == zone_code`; the column is nullable, and the only writer in the tree is `sandbox.py`. The projection cannot fill it either — `projections/writer.py` reads `state.get("ward")` and no event payload carries one | `public/aggregates.py`, `projections/writer.py`, `events/catalog.py` | **The most consequential M6 finding, and it is a data defect rather than a surface one.** Every §E18 place page renders honest zeros for a city with sixty-two complaints in it, because the join can never match. Not patched here, and the reason is that the two cheap fixes are both wrong: resolving the ward *in the projection* would make a replayable projection depend on mutable `zones` geometry, which `projections/writer.py` explicitly forbids — *"every column here is derived from a projected state, which is derived from events"* — and resolving it *at read time* would put a PostGIS join over the whole complaint table on an unauthenticated endpoint. The ward is a fact recorded **about** a report and belongs in the log, which makes it Phase 12's: `ROUTING` is a declared stage with no provider, and assigning department and place is exactly what it is for. §E28's row is corrected to **data ROADMAP** in the meantime, because a published figure that is structurally always zero is worse than no figure |
| 36 | **`SYSTEM_FLAGGED_NOTICE` and `rating_disclaimer` cannot be translated, and neither can a zone's name.** All three are what §E18 calls *"first-class UI, never tooltips"*, and all three arrive from the API as English constants — the two notices from module-level strings in `public/aggregates.py`, the zone name from `zones.name`, and a category from `by_category[].category` which is the immutable taxonomy **key** rather than the node's translated `display_name` — while `NAMESPACE_ZONE` exists in the i18n schema and the public endpoints never consult it | `public/aggregates.py`, `api/v1/public.py` | So a Marathi ward page carries an English legal sentence and a breakdown that reads `pothole_or_road_damage`, on the surface §16.2 aims at residents. The frontend deliberately does **not** paper over it: `notTranslatable()` renders the server's exact words, because a §22.2 disclaimer paraphrased by a client is the client asserting its own legal text. The right fix is upstream — the notices resolve through the Phase 5 locale registry and the public endpoints negotiate a locale — and it is a contract change, so it is recorded rather than improvised |
| 37 | **The public contract publishes a tenant's slug and never its name.** `ZoneIndexResponse.tenant` is the slug; `tenants.name` is not on any public model | `api/v1/public.py` | Every §E18 page is headed *"Published by Pune Demo"* — the slug, title-cased by `cityName()`. The alternative was a lookup table of cities we happen to know about, which would be a second source of truth for a fact the platform already holds. One field on the index response closes it |
| 38 | **§44 uses five status labels; the execution plan claims three.** §0 of this document says the vocabulary is *"REAL / SIMULATED / ROADMAP, used exactly as §44 and §E28 use them"*. §44 also uses `CUT PERMANENTLY (for MVP)` and — the one nobody had written down — **`REFRAMED`**, for *"Intake/Classification/Ops as 'agents'"* | §0 vs §44 | Found by `scripts/generate-honesty.ts` refusing to parse a label it did not recognise, which is the behaviour that made it worth writing that way. `REFRAMED` is not a synonym for any of the other four: they say how far a claim got, and it says the claim was **withdrawn and renamed**. It is the most honest row in §44 and it is precisely the row a parser that mapped unknown labels onto `ROADMAP` would have deleted. The vocabulary is corrected to five, and `honesty.test.ts` asserts that row survives |
| 39 | **Every drift check fails on a clean checkout on Windows.** `core.autocrlf=true` is the platform default and the repository shipped no `.gitattributes`, so `tokens.ts`, `api.ts` and `honesty.ts` are checked out with CRLF while their generators write LF | repository root | M0's gate is *"`nem web-check` passes on a clean checkout"*, and it did not — for a reason with nothing to do with anything the check protects, which is the worst kind of failing gate because it teaches people to ignore it. Latent since M1 and invisible because nobody had re-cloned; surfaced by the third drift check landing on a file that had been through a `git stash`. A `.gitattributes` pins text to LF, names the six generated artefacts explicitly, and marks `*.cmd` CRLF because `cmd.exe` genuinely wants it || 40 | **§E28 said RTI draft generation closes at M6; M6's own ships list never contained it.** §3 and §3b both define M6 as the SSR pages, the two notices, suppression, contractor ledgers, share cards and the honesty table | §E28 vs §3, §3b | Found by shipping M6 and reading the table back. §16.1's RTI draft is a template auto-fill with no filing integration and belongs with the Phase 23 reporting work; the row's *Closes at* is corrected to Phase 23 rather than the milestone quietly being marked incomplete. A row whose closing milestone is wrong is the same failure as a row whose status is wrong — it is the artefact a reader trusts instead of checking |
| 41 | **A SQL `DATE` was being published with a time on it.** `contractor.active_since` arrives as `"2019-06-01"`; `Date.parse` reads a bare ISO date as UTC midnight and `Intl` then renders it in the reader's zone, so the contractor's public record said *"On the register since June 1, 2019 at 5:30 AM"* | `datetime.ts`, the §E18 contractor page | Mine, and found by reading the rendered page rather than by a test. The 5:30 is the IST offset wearing a clock face; west of UTC the same code would have published the **previous day**. A wrong date on a named commercial entity's public record is exactly the small inaccuracy §22.2 makes expensive. `formatDateOnly` formats in UTC, so the rendered day equals the stored one — and the distinction between a timestamp and a date is now in the type of the helper rather than in whoever picks it |
| 42 | **The contractor ledger was crediting closures nobody confirmed.** §16.1's third metric is *"confirmed by reporters, versus disputed"*, and the public profile publishes `work_orders_completed` and `disputed_count` — but not a confirmation count. The first pass wired `work_orders_completed` in as *confirmed* | `ContractorLedger.tsx`, the §E18 contractor page | Also mine, and wrong in the direction that matters: `auto_confirmed_resolutions` exists on the *place* summary precisely because a closure nobody confirmed is a weaker fact than one somebody did, and this collapsed the two on a named contractor's record. §16.1's rule is that a ledger must not flatter. The row now renders null behind a `Phase 15` chip — the phase that lands §21.2's confirmation loop — rather than a number that reads as vindication |
| 43 | **§E28's M5 rows were wrong for the length of a whole milestone.** `Report capture → submit → receipt`, `Pipeline theatre — six gates`, `The third outcome`, `Dedup payoff` and `Severity breakdown panel` all still read **component ROADMAP** after M5 shipped every one of them | §E28 vs §3a | The progress table in this document was updated when M5 landed and §E28 was not, so the two honesty artefacts disagreed and the *published* one was the flattering-in-reverse direction — understating what exists, which is the same class of error as overstating it and just as disqualifying for a table whose only value is being accurate. **Found by M6 generating the public honesty page from §E28 and somebody reading the output.** That is the argument for generating it rather than transcribing it: a hand-kept table drifts, and the drift stays invisible until the table is put somewhere people look. Corrected, with the failure recorded in §E28's own header rather than silently repaired |

Fixing 5 and 6 by rewriting the old text would erase the record of the change,
which is the thing this repository has consistently refused to do. Both are
amended with an explicit supersession note naming the ADR, exactly as §E2 keeps
its own critique log in the repository.

---

*Where this document and the frontend blueprint disagree, the blueprint governs
on direction and this document governs on sequence. Where either disagrees with
`docs/PHASES.md` on a Track E gate, the blueprint governs, per the note added to
Track E.*
