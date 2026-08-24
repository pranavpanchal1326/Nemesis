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
| M5 — Citizen loop | Not started | |
| M6 — Public surface | Not started | |
| M7 — The light table | Not started | |
| M8 — Clay engine | Not started | |
| M9 — The Walk | Not started | |
| M10 — Sound & the ladder | Not started | |
| M11 — Field & offline | Not started | |
| M12 — Reconciliation | Not started | |

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

Fixing 5 and 6 by rewriting the old text would erase the record of the change,
which is the thing this repository has consistently refused to do. Both are
amended with an explicit supersession note naming the ADR, exactly as §E2 keeps
its own critique log in the repository.

---

*Where this document and the frontend blueprint disagree, the blueprint governs
on direction and this document governs on sequence. Where either disagrees with
`docs/PHASES.md` on a Track E gate, the blueprint governs, per the note added to
Track E.*
