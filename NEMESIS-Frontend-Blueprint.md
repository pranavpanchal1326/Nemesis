# NEMESIS — The Experience Blueprint

## Track E: Design Language, Frontend Architecture & the 3D Layer

**Codename:** MITTI × RISO *(the art direction; see §E4)*
**Version:** 1.0
**Status:** Direction locked. Every claim in this document is labeled REAL, SIMULATED, or ROADMAP against commit `d8afb4f`. Nothing is ambiguous.
**Owner:** PROD — project owner, sole
**Peer of:** [`NEMESIS-Blueprint-v2.md`](NEMESIS-Blueprint-v2.md) · **Governs:** [`docs/PHASES.md`](docs/PHASES.md) Track E, Phases 18–22
**Supersedes:** main blueprint §8.1 (frontend stack), §19 (design language), §20 (the wow-moment). Those sections were written before the backend existed and before the 2026 field moved; §E2 states precisely where they were wrong and why.

**Section numbering:** this document uses **§E-prefixed** numbers. A bare `§n` anywhere in this file refers to the *main* blueprint; `§En` refers to this one. The two documents cross-reference constantly and an ambiguous reference is a defect.

---

> *"3D/shader work is used only where it carries meaning, never as decoration."*
> — main blueprint §19.1, Design Principle #9
>
> This document takes that principle further than §19 did: **the art direction is not applied to the product, it is derived from the product.** The city is a handmade model because the system's claim is that somebody built a record by hand and kept it. The image is printed because a print is a thing that exists after the press stops. Every choice below traces to a sentence in the main blueprint or to an event in the log.

---

## Table of Contents

**Part I — Direction**
- [E1. Executive summary](#e1-executive-summary)
- [E2. Critique log — where §8.1, §19 and §20 were wrong](#e2-critique-log--where-81-19-and-20-were-wrong)
- [E3. Design principles — extending §6](#e3-design-principles--extending-6)
- [E4. MITTI × RISO — the art direction](#e4-mitti--riso--the-art-direction)
- [E5. The three-material law](#e5-the-three-material-law)
- [E6. The press — the print pipeline](#e6-the-press--the-print-pipeline)
- [E7. Rendering the clay](#e7-rendering-the-clay)
- [E8. Drawing the people](#e8-drawing-the-people)
- [E9. Colour — real risograph inks](#e9-colour--real-risograph-inks)
- [E10. Typography](#e10-typography)
- [E11. Motion](#e11-motion)
- [E12. Sound](#e12-sound)
- [E13. The fallback ladder](#e13-the-fallback-ladder)

**Part II — Architecture**
- [E14. Frontend architecture](#e14-frontend-architecture)
- [E15. Tech stack — supersedes §8.1](#e15-tech-stack--supersedes-81)

**Part III — Surfaces**
- [E16. The landing — "The Walk"](#e16-the-landing--the-walk)
- [E17. The citizen product](#e17-the-citizen-product)
- [E18. The public transparency surface](#e18-the-public-transparency-surface)
- [E19. The municipal console — "The Light Table"](#e19-the-municipal-console--the-light-table)
- [E20. The contractor portal](#e20-the-contractor-portal)
- [E21. Field & offline](#e21-field--offline)

**Part IV — Standards**
- [E22. Accessibility & internationalisation](#e22-accessibility--internationalisation)
- [E23. Performance budgets](#e23-performance-budgets)
- [E24. Design ops](#e24-design-ops)
- [E25. Phase gates — 18 through 22, restated](#e25-phase-gates--18-through-22-restated)

**Appendices**
- [E26. Appendix A — component contracts](#e26-appendix-a--component-contracts)
- [E27. Appendix B — event-to-surface traceability](#e27-appendix-b--event-to-surface-traceability)
- [E28. Appendix C — REAL / SIMULATED / ROADMAP, frontend rows](#e28-appendix-c--real--simulated--roadmap-frontend-rows)

---

# Part I — Direction

## E1. Executive summary

NEMESIS has ten gated backend phases, ~45k lines, 34 registered event types, a hash-chained
append-only event log, a live WebSocket pipeline stream, and a working deduplication engine. It
has no frontend.

This document specifies one. Not a skin over the API — a **second half of the product**, built to
the same standard the backend was: every visual element traceable to a real event, every
degradation designed rather than apologised for, every claim about what is built stated honestly.

Three decisions carry the rest:

1. **The art direction is a synthesis, not a style.** The product is *a risograph-printed document about a clay model city* (§E4). Subject and process. This single sentence unifies hand-drawn people, rendered geography, and flat records — they cohere because they came off the same press — and it turns the entire fallback ladder from an apology into a print run (§E13).
2. **The film becomes the product.** The landing is a nine-act scroll-driven film that never cuts to a dashboard; it pulls back until the dashboard is what you are already looking at, with live pins in it (§E16). There is no "Get started" transition to design because there is no transition.
3. **The console is a tool, not a showcase.** The municipal surface is dark-first, keyboard-first, dense, and printable, and its most valuable screens — review queue, policy studio, simulation — are fully backed by shipped backend today (§E19, §E28).

**What ships against the current backend, with nothing faked:** the report loop end to end, the pipeline theatre, the receipt and tracking ledger, the cluster-merge hero (`cluster_match_found` fires today), the review queue, the policy and simulation console, the public transparency endpoints, and the developer portal.

**What is designed here but not yet backed:** severity values (Phase 12), the work-order workflow and closure (14–15), contractor metrics and integrity signals (17), every role in §E19 (Phase 13 — today tenancy is a header, not an identity), and **temporal replay (Phase 21 — the event log is complete, but nothing reads it back yet; there is no replay endpoint)**.

**The distinction that matters, and that an earlier draft of this section got wrong.** "Not backed" here almost never means "no contract." The `work_orders`, `budget_allocations`, `contractors`, `contractor_certifications`, and `users` tables already exist with their columns defined, and `severity_score`, `severity_breakdown` and `severity_policy_version` are already fields on the v1 complaint read schema (`backend/nemesis/api/v1/schemas.py`). So these screens are **not built against a shape the frontend invented** — they build against the real generated TypeScript types and render null values until the phase that populates them lands. That is a materially different and much safer position than mocking, and §E24's "not wired" chip means *this contract returns nulls today*, not *this contract does not exist*.

---

## E2. Critique log — where §8.1, §19 and §20 were wrong

Kept in the repository deliberately, in the pattern `docs/PHASES.md` established. A document that
hides its own revision history teaches nobody, and the same mistakes get made again by the next
person.

| # | Defect in §8.1 / §19 / §20 | Consequence if shipped | Corrected by |
|---|---|---|---|
| 1 | **"Design system notes" was three bullet points.** Severity colours, "clean and utilitarian," and a warning against over-design. That is a mood, not a system | No token pipeline, no type scale, no motion language, no density model. Every screen re-decides everything, and the twentieth screen looks nothing like the first | §E9–E11, §E24 |
| 2 | **No art direction at all.** §19 specified what *not* to do (decoration) and never said what the thing looks like | The default fills the vacuum — dark glass, neon gradients, generic "AI product." Which in 2026 is the *generic* choice, and reads as marketing to the department-head persona §5.1 says is the hardest to win | §E4 |
| 3 | **Typography unspecified.** §8.1 named Tailwind and shadcn/ui and stopped | Ships Inter. A government tool for Maharashtra, typeset in the most-used typeface on the internet, with Devanagari as an afterthought fallback | §E10 |
| 4 | **Devanagari was a localisation task, not a design task.** §8.1 does not mention type at all; PHASES Phase 18 says "Devanagari typography" in a bullet | Latin gets designed and Devanagari gets whatever the fallback stack does. Line-height, shirorekha clearance, and per-script scale are not retrofittable | §E10, §E22 |
| 5 | **Leaflet + OSM tiles as the map layer.** Chosen in §8.1 for "no API key, no rate-limit surprise" | Correct instinct, wrong conclusion in 2026. MapLibre GL is equally keyless, self-hostable, vector, and shares the GPU story with the 3D layer. Leaflet's DOM markers cannot carry the pin count the dedup engine produces | §E15 |
| 6 | **Custom GLSL via `ShaderMaterial`.** §8.1 and §20.1 both assume hand-written GLSL | WebGPURenderer has been production-ready since three.js r171, and TSL authors a shader **once** into both WGSL and GLSL with automatic WebGL2 fallback. Hand-writing GLSL in 2026 means writing the shader twice or forfeiting WebGPU and its compute stage — which §E6's print pipeline needs | §E15, ADR-0037 |
| 7 | **"One scene, built well" was a hedge against a team that might not exist.** §20 budgets 10–14 hours for the shader path contingent on prior R3F experience | The reasoning was sound for a hackathon with an unknown frontend owner. It is wrong for a local-only build with no deploy deadline and a dedicated GPU. The constraint that actually binds is VRAM shared with Ollama (ADR-0002), not developer hours | §E7, §E23 |
| 8 | **The fallback was framed as a downgrade to be un-framed.** §20.3 instructs the team to ship the fallback "without framing it as a downgrade" | Managing the *narrative* about a worse experience instead of designing an experience that is not worse. §E6's print pipeline makes reduced quality read as a bolder print, so there is nothing to re-frame | §E13 |
| 9 | **No sound design anywhere in the blueprint** | Every site the 2026 award galleries rewarded has an audio layer. More importantly: positional foley over a map means an operator can *hear* where the problems are, which is a genuine affordance, not decoration | §E12 |
| 10 | **The department UI was specified as a kanban (§15.7) with no acknowledgement that Phases 12–17 do not exist** | The most-specified screen in the blueprint is the one with the least backend behind it. A frontend built in blueprint order would begin with four weeks of mocks | §E19, §E25, §E28 |
| 11 | **No BFF, and §18.4's auth stack assumed a browser talking to FastAPI directly** | Today `X-Tenant-ID` is a header that proves nothing (see `backend/nemesis/api/deps.py`). A browser client that names its own tenant ships a fake trust boundary and rewrites in Phase 13 | §E14, ADR-0040 |
| 12 | **"Zustand fed by the WebSocket" with no reconciliation rule** | An event stream and a read API disagreeing, with no stated authority, produces a UI that is confidently wrong. `?since=` exists precisely so this is solvable | §E14 |
| 13 | **No design ops.** No token pipeline, no visual regression, no Storybook matrix | §19.3 promises severity colour "defined once" for both 2D and shader. Without generation from a single source that promise is a convention, and conventions drift | §E24 |
| 14 | **Fairness features were listed as ROADMAP UI mockups (§16.4)** | §6 Principle #8 requires the appeal path to ship in the same phase as the accountability feature. A mockup is not a path. Every flagged-content component in this document takes the disclaimer and the response link as *required props* | §E19.6, §E26 |
| 15 | **§15.7's kanban columns are missing two states.** It specifies "Assigned → In Progress → Pending Verification → Closed". `domain.lifecycle.WorkOrderStatus` has six members: `created` and `disputed` are also real | The board omits the unassigned backlog — which is exactly what §E19.1's breach strip is counting — and it omits the citizen rejecting a closure, which is the product's whole thesis. A board that cannot display its own failures is a board that launders them | §E26.1 |
| 16 | **The status vocabulary was never written down anywhere.** `ComplaintStatus` has thirteen members, including `pending_classification` (the §24.2 degraded path) and `flagged` | Every surface invents its own status chips, and the two states that only occur when something has gone wrong are the two most likely to be forgotten — so the UI is at its least trustworthy exactly when the system is degraded | §E26.1 |

**Errors in this document's own first draft**, corrected in place and recorded here rather than
quietly overwritten, because §E3.3 applies to this file too:

| Claim | Reality |
|---|---|
| §E1 and §E19.9 listed **temporal replay** as backed by shipped backend | There is no replay endpoint. Phase 21 is not started. Corrected in both places |
| §E1 and §E24 prescribed building unbacked screens against **typed fixtures shaped like the future contract** | Understated what exists. `work_orders`, `budget_allocations`, `contractors`, `users` and the severity fields on the v1 read schema are already defined. Fixtures supply *values*, never *shapes* |
| §E19.4 said the **UI** enforces the closure rule | `WorkOrder.ssim_score`'s own comment says Phase 15's state machine does, "not the UI". The UI renders the unmet state |
| §E14.1 and §E2 defect #11 recorded the **BFF seam** as ADR-0039 | 0039 is the flag-colour decision; the BFF is **ADR-0040**. Corrected in both places. A traceability document whose references do not resolve is failing at its own method |
| §E15 credited **Rive** to ADR-0040 | The character decision is **ADR-0041**. Corrected |
| §E6.2 stated the text exemption without citing the ADR that decided it | **ADR-0038** now cited inline |
| §E15 pinned **Next.js 15** | Superseded by **ADR-0042**: Next.js 16 ships, TypeScript is held at 5.9. The reasoning is §E2 defect #6's, applied to this document's own choice |
| §E9.2 labelled `riso-aqua` **SIGNAL — links, focus rings, primary action**, and §E9.1 labelled `mitti-500` **secondary text on light** | Neither clears §E22's own 4.5:1 floor: aqua measures **2.51:1** on paper-50, mitti-500 **3.80:1**. The inks are not repainted — they are the premise of §E4 — so the text-safe values are **overprints** (§E6.3): `aqua × federal blue` at 10.60:1, `mitti-500 × mitti-300` at 6.54:1. The press produces its own accessible palette |
| §E9.1 assigned `mitti-300` to **rules, dividers** | 1.36:1 on kraft-200 — a hairline nobody can see on the zebra stock. Rules are `mitti-500` |
| §E9.4's severity `ink` read as usable on any ground | It is **4.17:1 on kraft-200**. Severity type sits on its own `tint` field, never directly on a table stock — which is what the four-channel model was always for, now stated and tested |
| §E9.4 rule 2 read as belt-and-braces | `high` and `medium` are **1.4% apart in grayscale**. On the printouts §E19.7 says officers make, the shape channel is not a redundancy — it is the only channel that survives |
| §E10.1 named **Kaana** as the Devanagari display face | Not in Fontshare's catalogue and not available under a free commercial licence, so it cannot ship. **Sarpanch** takes the role — ITF, OFL, Devanagari and Latin, squared and straight-lined and wide, which is what §E10.1 actually described |
| §E10 and §E15 ask for **metric-matched fallbacks** and name four descriptors | Three of them — `ascent-override`, `descent-override`, `line-gap-override` — are copied from the real face and hold whichever installed face the browser resolves. `size-adjust` is a *ratio between two faces*, and the second face is whatever is on the reader's machine: Arial on the developer's Windows host, Liberation Sans in CI's container, neither on a locked-down municipal terminal. This repository cannot measure it, and a ratio computed for a face that did not resolve scales text **away** from the real face's width. So it is not declared, and the omission is argued in **ADR-0053** rather than papered over with a hard-coded metrics table that nothing in `nem check` could ever recompute |
| §E10.2 wrote the tabular-figures rule as `font-variant-numeric: tabular-lining` | Not a CSS value. It computes to `normal`, so the first of §E10.2's *"two hard rules"* was doing nothing. The real declaration is `lining-nums tabular-nums` |
| §E10.1 set the Devanagari leading rule as a flat **+0.15** | Measured, Devanagari ink runs to **1.289em** at `display-2`, against a Latin display leading of 1.04. The rule is a delta **and a floor**: `max(latin + 0.15, 1.35)`. Devanagari display type is genuinely looser than Latin display type — the script stacks matras above the shirorekha and below the baseline, and you cannot set it at 0.86 |
| §E1 said the severity fields *"are already fields on the v1 complaint read schema"* | True of the Pydantic model, false of the **published contract**: the route returns a raw `Response` for its ETag path, so OpenAPI recorded `{}` and `api_contract_lock.json` protected nothing on the hottest read in the product. A generated client can only see what is published. Fixed in `complaints.py`; the lock now covers fifteen fields |
| §E27's traceability table assumes every listed event arrives with data | **ADR-0016 makes realtime payloads default-deny, and only 8 of 33 registered event types are shaped for the wire.** Two of §E16.1's six pipeline gates — `exif_check_completed` and `media_redacted` — reach the browser with an empty payload, so *"EXIF INTACT"* and *"a face visibly blurs"* cannot be driven from the stream as specified. Adding a shaper is a privacy decision, not a formality, so it is argued in its own ADR at M5 rather than slipped in. The published `RealtimeShapedEventType` enum makes the distinction a compile error rather than an empty pin. **Landed as ADR-0045**: `exif_present` on the broadcast, the distance and the reason to the id-holder only; both face counts on both, because §22.1 promises *every* face and one boolean cannot express failing it |
| §E28 marks **"Tracking ledger from the event log"** REAL | Nothing reads the log back per entity. `GET /complaints/{id}` returns the projection; the two export datasets are `complaints` and `work-orders`. `<EvidenceTrail>` is built and correct, but with no history endpoint it can only show what arrives while the page is open — which §E17.4 itself calls the enemy. The row should read **component REAL, data ROADMAP** — and §E28 as a whole conflates *what Track E has built* with *what backs it*, which is why every row read REAL. **Landed as ADR-0043**: `GET /complaints/{id}/events` serves the chain, every row disclosed as a row with its hash links, payloads shaped by a second default-deny table. §E28 is restated below with the two questions in two columns |
| §E17.3 says the receipt carries **"the complaint id and chain hash"** | The submission response publishes neither the hash nor a way to fetch it. `<Receipt>` renders nothing where it would go rather than a placeholder, so the gap is visible instead of faked. **Landed as ADR-0044**: the 202 carries `chain_hash` and the history endpoint carries the live `chain_head`. It is deliberately **not** on `GET /complaints/{id}` — the head advances on every append and `version` does not, so a hash served under that ETag would be stale behind a 304, and a stale hash on a document claiming *"this record cannot be edited"* fails in the exact direction the hash exists to prevent |
| §E9.3's light table was implemented as *the glaze fills the field, the tint carries the type* | That measures **2.63–4.37:1** and failed `axe` in all twelve matrix combinations. §E9.3's own words are that the prints are backlit and *"the page ground is mitti-950 because that is the room, not the paper"* — so the ink glows and the room stays behind it: **13.48–15.04:1**. The glaze draws the mark and a printed edge at 3.09–5.72:1, which clears 3:1 for a meaningful graphic and not 4.5:1 for text, which is exactly why it never carries a word |
| §E9.4's table gives each severity a **label** alongside its ink and shape | A token file cannot be translated by the Phase 5 locale registry, so a label held there reads *"Critical"* on a Marathi console forever — against Phase 18's own gate. Colour and shape are design; words are content. Labels moved to the locale bundle |

---

## E3. Design principles — extending §6

§6's nine principles govern. These five are the frontend's, and they are subordinate to §6, never
in tension with it.

**E3.1 — The interface is the evidence.** A status badge is a claim; an event ledger is evidence.
Wherever a state could be shown as a label, show the record that produced it instead. This is §6
Principle #1 ("prove, don't log") applied to pixels, and it is why `<EvidenceTrail>` appears on
every surface in this document.

**E3.2 — Degradation is a design deliverable.** Every fallback in §E13 is art-directed and
graded, not generated. The reduced-motion path is what an accessibility audit and a
reduced-motion reader actually see, which makes it the *most* consequential edit, not the least.

**E3.3 — Honesty is rendered.** Confidence figures show their runner-up. Detectors show their
threshold. Suppressed buckets say they are suppressed. Unverified flags are visually
distinguishable from facts, permanently and unmissably (§E9). §6 Principle #8 is a UI
requirement, not a copy requirement.

**E3.4 — Colour, motion, and sound each carry exactly one meaning, or none.** A vocabulary that
means two things means nothing. Severity ink never decorates. The stamp only confirms. Bloom only
fires on the safety fail-safe. Enforced in review, not by taste.

**E3.5 — The tool outranks the show.** Where the cinematic surface and the operator surface
conflict, the operator wins. An officer works in this for nine hours; a judge looks at it for
ninety seconds. The film exists to earn the meeting; the console exists to survive the pilot.

---

## E4. MITTI × RISO — the art direction

> ## **MITTI** — मिट्टी · *earth, clay, soil*
> ### printed on a **RISOGRAPH**

Two directions were considered as alternatives and adopted as a **synthesis**, because they
operate at different layers:

- **MITTI is what we photograph.** A handmade clay model of the city on a workbench. Thumbprints, cut cardboard, masking tape, pins with handwritten paper flags.
- **RISO is how the image reaches the eye.** Every frame — 3D and 2D alike — separated into two or three spot inks, halftoned, misregistered by half a pixel, printed onto paper with grain and uneven ink density.

> **The entire product is a risograph-printed document about a clay model city.**

### E4.1 Why the word

*Mitti* is the material the model is made of, the ground the roads are laid on, and the thing a
pothole exposes when the tar fails. The city and the defect are made of the same stuff. That is
the product in one noun, in the users' own language, and it is the answer to §5.1's hardest
objection — a system that looks like it was *made*, by people, for a place.

### E4.2 What the synthesis buys

**It unifies 2D and 3D.** The hardest problem in any hybrid direction is making drawn and
rendered elements look like they belong. They belong because they came off the same press: the
clay city, the ink figure, and the complaint receipt share a paper stock, an ink set, a halftone
grid, and a registration slip. Nothing else is required.

**It makes the fallback ladder invisible.** Print quality is the degradation dial. Fewer inks and
a coarser halftone read as a *bolder* print, not a worse one. §E2 defect #8 dissolves: there is
no downgrade to re-frame (§E13).

**It gives dark mode physics.** Riso ink is translucent and sits on paper; printing on black
stock is not a real thing. Inverting the palette would break the premise the whole direction
rests on. Instead the console at night is a **light table** — the same prints, backlit on glass —
which is precisely the surface a planner or an architect works on (§E9.3).

**It is defensible against the category.** §29's competitive landscape is complaint-routing
software. None of it looks like anything. The 2026 field moved toward tactile, handmade, warm
work as a reaction against machine-polish; this direction is both current and, in this category,
unoccupied.

### E4.3 What it is not

Not Pixar smoothness. Not photoreal architectural visualisation. Not neon cyber dashboard. Not
"cartoon" in the sense of comedy — the world is warm but the register is serious, because the
subject is.

---

## E5. The three-material law

Every pixel in NEMESIS is exactly one of three materials. This is the rule that resolves a design
argument without escalating it.

| Material | Is | Rendered as | Governs |
|---|---|---|---|
| **INK** — drawn | **People** | Hand-drawn 2D, varying line weight, animated **on twos** (12 fps), Rive state machines | Citizens, officers, contractors, field staff, empty states |
| **CLAY** — sculpted | **The city** | 3D matte clay, thumbprint normals, baked AO, tilt-shift | Maps, wards, buildings, roads, pins, weather |
| **PAPER** — printed | **The record** | Flat, ruled, stamped, solid ink, deckled edges | Complaints, receipts, ledgers, work orders, policies, case files, reports |

And over all three, one process: **the press** (§E6).

> **Ink meets clay, and paper is what's left.**

A person encounters the city, and a record is created. That is §6 Principle #1 expressed as
physics. Anything that cannot be classified as ink, clay, or paper does not belong on screen.

**Enforceable corollaries:**

- **People are never 3D.** A person shown on the map is a drawn figure composited in. No avatars in the clay world.
- **The city is never flat.** Even the 2D fallback map is a printed photograph of the model, not a second aesthetic.
- **Data is never decorative.** A number lives on paper — ruled, tabular, solid ink.
- **A material never borrows another's affordances.** Paper does not have depth shadows; clay does not have text on it; ink does not carry data.

---

## E6. The press — the print pipeline

A TSL post-processing pass for 3D and a matching CSS/SVG layer for 2D, generated from the same
token values. **This is built first, before any scene exists** (§E25 M1), because everything
inherits it.

### E6.1 Stages, in order

1. **Ink separation.** Project the rendered frame onto an ink basis — two or three inks per context (§E9.2). Not CMYK; risograph has no process colour. Each ink carries its own density channel.
2. **Halftone.** Dither each channel through a rotated grid at classic screen angles (15° / 45° / 75°) so the rosette reads as real print. Round dot, slightly soft edge.
3. **Misregistration.** Offset each ink channel 0.5–1.5 px along a per-ink vector, **re-jittered at 12 Hz**. This one stage is the majority of "why does this look printed."
4. **Ink density variation.** Low-frequency noise per channel — risograph ink is famously uneven, roller-streaked, denser at the leading edge of a pass.
5. **Overprint composite.** Multiply blend, never alpha. Overlapping inks produce a genuine third colour, exactly as they do on paper.
6. **Paper.** Composite onto a scanned paper texture with fibre grain and a faint deckle at the frame edge.

### E6.2 The rule that keeps it usable

> **The press applies to imagery and fields. Never to text or data.**

Text prints **solid — 100% density, no halftone, no offset**, which is also physically true of
risograph output: type is solid ink, images are screened. Halftoned 13 px type in an operator
console would be a defect, not a style. Enforced by compositing all text in a separate,
unprocessed layer, and verified by a test asserting text layers are byte-identical with the press
on and off (§E25). Recorded in ADR-0038.

**Layering alone is not sufficient, and the gate is what proved it.** A blended or filtered sibling
promotes the stacking context to a compositor layer, and the browser answers by switching type from
subpixel to grayscale antialiasing — so text renders differently with the press on *without the
press ever touching it*. The text layer therefore forces grayscale antialiasing unconditionally,
which is also correct on its own terms: subpixel antialiasing works by putting **colour fringes** on
glyph edges, and colour fringing is precisely what misregistration is. Misregistration belongs to
imagery. Type prints in one ink, with no fringe.

### E6.3 Overprint is a data mechanic

Two inks overlapping create a third colour by multiply. So **severity escalation is a second ink
pass**: a complaint whose severity is recomputed upward is literally overprinted, and the
resulting colour is the physically correct multiply of the two glazes. A real print mechanic
mapped one-to-one onto a real data transition — not a metaphor for one.

The merge (§E16, Act 6) speaks the same language: **three prints registered onto one sheet.**

### E6.4 Print quality is the fallback dial

| Quality | Inks | Halftone | Misregistration |
|---|---|---|---|
| Full | 3 | fine | animated at 12 Hz |
| Reduced | 2 | coarse | static |
| Flat | 1 | none | none — solid ink, poster register |

The adaptive quality manager (§E23) turns this dial **before** it touches frame rate, satisfying
PHASES Phase 19's requirement that effects degrade before performance does.

---

## E7. Rendering the clay

**Look targets:** stop-motion feature animation, architectural planning models, risograph-printed
civic posters.

### E7.1 Material recipe

One TSL node material, compiled to both WGSL and GLSL:

- Flat matte albedo, roughness 0.92, zero metalness. **No PBR texture streaming.**
- **Thumbprint normal** — a single tiling 512² normal at low amplitude (~0.06), rotated per instance by a hash of the entity id, so nothing tiles visibly.
- **Baked ambient occlusion from Blender into vertex colours** for static geometry. Cheap, and it is what sells "handmade solid object."
- **Sub-surface cheat** — a rim term, warm on the light side, cool in shadow. Clay is faintly translucent at its edges; this single term carries most of the read.
- **Cut-card edges** — a thin darker bevel band on every extruded footprint, so buildings read as cardboard and clay rather than as extruded geometry.
- **Fired-glaze severity** — severity applied as a glaze coat with specular sheen and a darker rim, never as flat emissive. Severity looks like fired ceramic, and is then printed.

### E7.2 Time is stepped; the camera is not

- **Characters, props, flags, weather, pins: 12 fps stepped.** Poses hold two frames, then snap.
- **Camera: uncapped, damped, filmic.**

A smooth camera moving through a world that animates on twos is how stop-motion is actually shot,
and the contrast is what makes the result read as *handmade* rather than as *a low frame rate*.

Implementation: a global 12 Hz stepped clock drives every instance and state update; the render
loop stays uncapped. This is **cheaper** than smooth animation — four of every five animation
updates are skipped.

### E7.3 Lens treatment (before the press)

- **Tilt-shift depth of field** — the single most important effect; it is what makes a city read as a miniature. Focal plane follows the hovered or selected entity.
- **Gate weave** — ±0.4 px sub-pixel camera jitter resampled at 12 Hz. Not consciously perceptible; makes the frame feel projected.
- **Selective bloom via render layers**, reserved exclusively for `safety_trigger_fired`. The deterministic fail-safe (§11.2) is the only thing in this product permitted to glow.
- **Vignette and slight barrel distortion** — we are looking through a macro lens at a table.

### E7.4 Weather and time are real, and they are the same fact as the logic

The model's sun position follows the tenant's actual local time. The model's weather follows the
tenant's actual weather: monsoon means rain on the model, wet clay darkening, water in the
potholes.

This is the **same monsoon context that seasonally normalises contractor SLAs** (§16.4). The art
and the fairness mechanism are one fact rendered twice, not a decoration that happens to resemble
one. Toggling monsoon normalisation in the console changes the weather on the model.

---

## E8. Drawing the people

The citizen is a **hand-drawn ink figure** — varying line weight, one warm fill that does not
quite register with the line, which is free because the press does it (§E6.1 stage 3).

**No face, ever.** Not for cost — for meaning. The figure must read as a college student, an
aunty, a delivery rider, anyone. It also sidesteps every representation and uncanny-valley
problem in a product intended for deployment across Indian cities.

### E8.1 The character is bound to the event log

Built as a Rive state machine with named inputs the application drives directly:

```
inputs  walking(bool) · stopped(bool) · looking_down(bool) · shoulders_drop(trigger)
        raise_phone(bool) · shutter(trigger) · relief(trigger) · disappointed(trigger)
states  idle → walk → halt → observe → dejected → report → wait → confirmed
```

Because these are inputs and not timelines, the character **reacts to real backend events**. When
`citizen_confirmed` arrives on the WebSocket, `relief` fires. The character is not a mascot; it
is a view over the event stream, and it is subject to §6 Principle #9 exactly like the shader
scenes are.

### E8.2 Cast

| Figure | Appears in | Register |
|---|---|---|
| **The Reporter** | Landing story, report flow, tracking | The citizen. Ordinary, unheroic |
| **The Officer** | Console empty states, onboarding, "nothing breaches today" | Competent, tired, not a bureaucrat joke |
| **The Field Hand** | Mobile capture, offline states | Practical, in motion |
| **The Auditor** | The integrity room | Patient. Never smug, never accusatory — §22.2 is a design constraint on the character too |

---

## E9. Colour — real risograph inks

Risograph ink is the semantic system. These are the published Riso ink colours (approximated to
sRGB), which is why the palette holds together — it was mixed by a manufacturer for overprinting,
not by us for a screen.

### E9.1 Paper stocks — ground, carries no meaning

```
paper-50    #F4EDE1   newsprint warm — public surfaces, day
kraft-100   #EADCC7   card ground
kraft-200   #DCCBB4   table zebra, cardboard
mitti-300   #BFAE9B   rules, dividers, disabled
mitti-500   #8A7462   secondary text on light
mitti-950   #17120E   the workbench at night — console ground
bone-200    #E4DCCE   body text on dark (never pure white)
```

### E9.2 Inks

```
riso-black      #16130F   base ink — all text, all rules
riso-brown      #925F52   the clay ink — MITTI's body colour
riso-sunflower  #FFB511   warmth, light, the sun
riso-aqua       #0FA9A0   SIGNAL — links, focus rings, primary action, selection
riso-fed-blue   #3D5588   shadow ink, cool pass
riso-orange     #FF6C2F
riso-green      #00A95C
riso-flu-pink   #FF48B0   FLUORESCENT — flagged / unverified ONLY, always hatched
```

Risograph's real constraint is two or three inks per run. Honouring it is what makes every
surface look deliberately designed rather than arbitrarily coloured:

| Surface | Stock | Ink set |
|---|---|---|
| Landing / story | paper-50 | brown + sunflower + aqua |
| Public transparency | paper-50 | black + aqua + severity as third pass |
| Citizen app | kraft-100 | black + brown + severity |
| Console — day | kraft-100 | black + federal blue + severity |
| Console — night | light table (§E9.3) | as day, backlit |
| Case file / report | chalk white | black + fluorescent pink (flags only) |

### E9.3 The console at night is a light table

Riso ink is translucent and sits on paper, so inverting the palette for dark mode would break the
premise the direction rests on. Instead: **the same prints, backlit on glass.** Inks glow through
the sheet, paper fibre becomes visible as texture, and the page ground is `mitti-950` because
that is the room, not the paper.

Physically coherent, immediately legible, and it produces **warm dark instead of cold dark** —
the clearest available differentiator against the operator-console idiom every competitor is
shipping.

### E9.4 Severity — fired glazes, printed as inks

```
                ink (text)  tint (field)  glaze (3D)  ink pass          shape
sev-critical    #8E2A1C     #F0D6CF       #B03A2B     orange × red OVER  ◆ filled diamond
sev-high        #8A4E12     #F7E4C8       #C97A1E     orange             ● filled circle
sev-medium      #6E5A11     #F2E7C2       #A98C1F     sunflower          ○ hollow circle
sev-low         #2A4E60     #D9E5EC       #3E6E86     federal blue       · small dot
sev-resolved    #2F5436     #DCE9DC       #4E7D53     green              ✓ check
```

**Rules, enforced in review:**

1. **A severity colour never appears on a non-severity element.** Destructive actions are `riso-black` on `kraft-200` with hold-to-confirm. There are no red delete buttons in this product.
2. **Colour is never the only channel.** Every severity carries a shape and a label. Colour-blind readers work; grayscale printouts work, and §E19.7 establishes that officers print.
3. The `glaze` values feed the TSL severity ramp, **generated from the same token file as the CSS**. The badge and the shader are literally the same number (§E24).
4. **No severity colour in financial charts.** Money is not urgency; budget renders as black on kraft.
5. **Flagged is fluorescent pink, hatched, and never red.** An unproven anomaly rendered in urgent red is a §22.2 defamation exposure with a design cause. Fluorescent pink on a 45° hatch reads as *provisional print proof*, which is exactly what it is — and it is a real risograph ink, so it belongs to the system rather than being bolted on.

---

## E10. Typography

Six faces, six jobs, **all self-hosted** — no CDN, satisfying §6 Principle #6 (zero-cost,
self-hosted, offline-capable) at the type layer. Sourced predominantly from **Fontshare / Indian
Type Foundry**: an Indian foundry for an Indian civic product, free for commercial use.

| Role | Face | Source | Why |
|---|---|---|---|
| **Narrative display** | **Gambarino** | Fontshare (ITF) | Condensed Garalde serif, very fine serifs, teardrop terminals. Editorial gravity with real personality. Landing prose, report covers |
| **Institutional display** | **Panchang** | Fontshare (ITF) | Wide, panoramic, industrial. The *municipal signage voice* — `WARD 14`, act titles. Variable |
| **Interface** | **Switzer** | Fontshare (ITF) | Neutral grotesque, variable, strong tabular figures at 13–15 px. Console workhorse |
| **Data / record** | **JetBrains Mono** | OFL | Ids, chain hashes, coordinates, timestamps |
| **Official document** | **Courier Prime** | OFL | Typewriter. Stamps, receipts, RTI drafts, case files, report footers. Typewriter is the visual language of officialdom |
| **The human hand** | **Kalam** | ITF, OFL — Latin *and* Devanagari | Handwriting. Paper flags on pins, margin notes, citizen annotations. One hand across both scripts |

### E10.1 Devanagari is a design partner, not a fallback

This corrects §E2 defect #4 directly.

| Role | Face | Why |
|---|---|---|
| Display | **Kaana** | Built from straight lines and triangular geometries. Devanagari-first; pairs with Panchang's width |
| Long-form Marathi / Hindi | **Tiro Devanagari Marathi** | Purpose-built for Marathi; the closest thing to a serious long-form default |
| Interface | **Noto Sans Devanagari** | Matches Switzer's neutrality at small sizes |
| Handwriting | **Kalam** | The same face as the Latin hand |
| Celebration | **Modak** | Inflated, joyful. Used exactly once: closure confirmed (§E17.5) |

**Rules:**
- Line-height **+0.15** over the Latin value. Devanagari sits taller and the shirorekha needs air.
- The type scale is defined **per script**, never globally scaled from the Latin values.
- Never concatenate a sentence from fragments — a sentence is a translation unit.
- Every string resolves from the Phase 5 locale registry, so a locale added in the control plane appears in the UI with **no code change** (a Phase 18 gate).

### E10.2 Scale

```
poster      clamp(4rem, 12vw, 11rem)   Panchang 700, -0.04em, lh 0.86, uppercase
display-1   clamp(3rem, 7vw, 6.5rem)   Gambarino 400, -0.02em, lh 0.94
display-2   clamp(2rem, 4vw, 3.5rem)   Gambarino 400, -0.015em, lh 1.04
title       1.75rem     Switzer 500          lh 1.20
heading     1.25rem     Switzer 560          lh 1.30
body        0.9375rem   Switzer 400          lh 1.55      ← 15px, console default
caption     0.8125rem   Switzer 450          lh 1.42
micro       0.6875rem   Switzer 600  0.08em  uppercase
mono-data   0.8125rem   JetBrains Mono 400   0.02em
doc         0.875rem    Courier Prime 400    lh 1.60
hand        1rem        Kalam 400            lh 1.40  rotate(-0.7deg)
```

**Two hard rules.** Every number uses `font-variant-numeric: tabular-lining` — a column of costs
that does not align is a column nobody trusts, and this product's entire proposition is trust in
columns of costs. Prose measure caps at 68ch, tooltips included.

---

## E11. Motion

A stop-motion world has its own physics. **No spring overshoot anywhere in the console** —
overshoot reads as toy, and this is a tool people are audited on.

```
--ease-out    cubic-bezier(0.22, 1, 0.36, 1)      entrances, panels
--ease-cine   cubic-bezier(0.65, 0, 0.35, 1)      camera, transforms
--ease-stamp  cubic-bezier(0.34, 1.28, 0.64, 1)   ONLY the stamp
--ease-clay   cubic-bezier(0.50, 0, 0.10, 1)      heavy objects: slow in, fast out

--t-snap  84ms (1 step)   --t-fast 168ms (2)   --t-base 250ms (3)
--t-slow 420ms (5)        --t-cine 900ms       --t-film 2400ms
```

Durations are **multiples of the 12 fps step (83.3 ms)**. The whole product beats to the same
clock as the animation; the coherence is felt even when it is not noticed.

### E11.1 Five signature motions, and only five

1. **The Stamp.** Confirmations land; they do not fade. Scale 1.18 → 1.0 over 168 ms on `--ease-stamp`, −1.5° rotation, 3 px offset, a one-frame ink spread, and a soft thud on the foley bus. Used for: complaint accepted, evidence verified, closure confirmed, policy activated, case approved. It is the sound of a decision being made.
2. **The Merge.** The hero (§20, §E16 Act 6). Paper flags lean; pins bend toward the centroid on a shared `uMergeProgress`; the survivor grows with recomputed severity; **the second ink overprints** as severity climbs. 900 ms on `--ease-cine`, a 168 ms hold, then the count stamps — and **a thumbprint presses into the merged pin**. Fingerprints are the stop-motion tell; the merged incident bears the mark of the hand that made it. Absorbed reports leave faint registration rings, because **deduplication is not deletion and the visual must say so.**
3. **The Rule-Draw.** Borders and dividers draw left-to-right over 168 ms instead of fading. Drafting-table language; free; makes a static page feel constructed.
4. **The Lift.** Panels rise 8 px and fade over 250 ms. That is the entire chrome vocabulary.
5. **The Settle.** Clay objects arriving drop with a single 2 px overshoot on `--ease-clay` — the only overshoot permitted anywhere in the product, and only in the 3D world, because clay has mass.

`prefers-reduced-motion` collapses all five to opacity at 84 ms and switches the landing to the
storyboard edit (§E13).

---

## E12. Sound

Muted by default, with an unmute affordance that is designed rather than hidden; state persists
per user. Corrects §E2 defect #9.

- **Ambient bed** — the city at the model's current time of day. Morning birds and a distant bell; midday traffic and a hawker; dusk crickets and a gully cricket match. Cross-faded on the real clock (§E7.4).
- **Positional foley** — every pin carries a quiet loop audible near the camera. Water in a pothole. A buzzing streetlight. A leaking main. **An operator can hear where the problems are before seeing them** — an affordance, not decoration, and therefore compliant with §6 Principle #9.
- **Interaction foley**, recorded from paper and clay: stamp thud, paper slide on panel open, pin push on select, page turn on route change, shutter on capture, and a **roller pass** on print transitions.
- **The merge** has its own cue: three soft taps converging into one low thump.
- **`safety_trigger_fired`** is the only alarming sound in the product, and it is a single struck metal note, not a klaxon. §11.2's fail-safe should feel grave, not panicked.
- Web Audio graph with a master duck on modal open. All buses respect `prefers-reduced-motion` as a proxy for sensory sensitivity.

---

## E13. The fallback ladder

Replaces §20.4. Corrects §E2 defect #8.

| Tier | Trigger | Renders |
|---|---|---|
| **S — WebGPU** | adapter present | Full: compute-driven severity field, 3-ink press with animated misregistration, full lens stack |
| **A — WebGL2** | automatic TSL backend fallback | Same scenes; precomputed severity texture; 3-ink press, static misregistration |
| **B — lite** | `deviceMemory < 4`, or < 40 fps measured for 3 s | 2-ink press, coarse halftone, no DOF or bloom, static sun. **Reads as a bolder print, not a worse one** |
| **C — storyboard** | `prefers-reduced-motion`, or no WebGL | Nine art-directed **riso prints**, scroll-snapped, same copy. Visually continuous with Tier S because it is the same process |
| **D — text** | JS disabled, crawler, 2G | Semantic article. All copy present. Public data server-rendered |

Every tier is exercised in CI by forcing its trigger — a Phase 20 gate requirement. Tier C is a
**design deliverable with its own review**, per §E3.2.

**WebGL context loss** reconstructs the scene without a page reload (a Phase 19 gate). A second
loss in one session drops permanently to Tier C and says so calmly (§E26, `<DegradedBanner>`).

---

# Part II — Architecture

## E14. Frontend architecture

### E14.1 The BFF seam — corrects §E2 defect #11

`backend/nemesis/api/deps.py` resolves the tenant from an `X-Tenant-ID` header and states plainly
that this is not authentication; Phase 13 owns identity. A browser client that names its own
tenant would ship a trust boundary that is not one.

**Every browser-to-API request goes through Next.js route handlers.** The server holds the tenant
header today and the bearer token tomorrow. Phase 13's arrival changes one server module, not the
client. This also gives us server-side rendering for the public surfaces (§16.2 wants them
bookmarkable and indexable), a place for locale negotiation, and a cache boundary.

**Exception:** the WebSocket connects directly (or through a thin proxy), because `/ws/pipeline-events`
is an unauthenticated one-directional stream by construction and proxying it would add a hop with
no security benefit. Recorded in ADR-0040.

### E14.2 Three transports, and which pixels each owns

| Transport | Owns | Mechanism |
|---|---|---|
| **Server reads** | All page-load state | RSC / route handler → generated TS client. Public dashboards, review bundles, policy documents |
| **WebSocket** | *Change*, not data | Events → Zustand → transient subscriptions that drive shader uniforms and marker transforms **without a React re-render** |
| **Server actions** | Mutations | Submit, review decide, policy transition. Client-generated idempotency key carried through; the backend makes replay a provable no-op, so the client may retry freely — which is what makes Phase 22's offline queue tractable |

### E14.3 The reconciliation rule — corrects §E2 defect #12

> **The WebSocket is a hint, not a source of truth.**

An event arrives → optimistic store patch → the affected entity is refetched from the read path
on idle. Because `?since=<cursor>` exists, a reconnect replays the gap rather than silently
dropping pins; heartbeats arriving as ordinary envelopes let the client detect a socket that is
open but dead.

**A refused upgrade is normal degraded mode, not an error.** When the `realtime_websocket_hub`
kill switch refuses the handshake, the client falls to polling and shows a calm banner. It must
never retry in a loop against a capability somebody deliberately switched off.

### E14.4 Route structure

```
(story)    /                          the film — Acts 0–9
(report)   /report  /t/{id}           citizen capture + tracking, PWA scope
(public)   /{tenant}/...              SSR, indexable, shareable, suppression-aware
(console)  /console/...               operator; role determines the shell, not just permissions
(dev)      /developers                keys, webhooks, usage, deprecation clock
```

One Next.js application, five route groups. They share the token system, the press, and the
component contracts in §E26; they differ in shell, density, and auth posture.

---

## E15. Tech stack — supersedes §8.1

| Layer | Choice | Status | Note |
|---|---|---|---|
| Framework | **Next.js 16 App Router**, TypeScript 5.9 strict, `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`, Turbopack | REAL | Host Node 24. Amends this row's original "Next.js 15" and declines the TypeScript 7 native compiler. ADR-0042 |
| 3D | **three.js WebGPURenderer + TSL node materials** | REAL | Production-ready since r171; TSL compiles once to WGSL and GLSL; automatic WebGL2 fallback. Corrects §E2 #6. ADR-0037 |
| 3D React layer | `@react-three/fiber` v9, `@react-three/drei` | REAL | |
| Post-processing | three.js node post-processing — **the press** (§E6) | REAL | |
| Choreography | **Theatre.js** (`@theatre/core`; `studio` dev-only, state exported to JSON) | REAL | Camera and uniform sequencing with a visual editor. The animator's tool |
| Scroll | **Lenis** + **GSAP ScrollTrigger / SplitText** | REAL | Damped scroll proxy; DOM and type choreography |
| Character animation | **Rive** (`@rive-app/react-canvas`) | REAL | State machines driven by real events (§E8.1). Chosen over Lottie: interactive inputs, ~0% idle CPU. ADR-0041 |
| State | **Zustand** | REAL | WS event bus; transient subscriptions bypass React render |
| UI motion | **Motion** | REAL | Panel and component transitions only |
| Map (2D / fallback / heavy layers) | **MapLibre GL + deck.gl** | REAL | Keyless and self-hostable like Leaflet, but vector and GPU-instanced. Corrects §E2 #5 |
| Styling | Tailwind v4 + shadcn/ui, re-tokenised | REAL | |
| Data | TanStack Query + `openapi-typescript` generated client | REAL | Drift fails CI — a Phase 18 gate |
| Type | Self-hosted woff2, subset with `fonttools` | REAL | Devanagari subset separately; metric-matched fallbacks |
| Audio | Web Audio + opus sprites | REAL | Lazily loaded on first unmute |
| Share cards | `satori` + `resvg` | REAL | Server-rendered OG images per complaint / ward |
| Assets | Blender → `gltf-transform` (Draco / Meshopt) → KTX2 / Basis | REAL | Modular clay city kit; AO baked to vertex colours |
| Test | Playwright (+ SwiftShader headless WebGL), Storybook, `axe`, Vitest, `r3f-perf` | REAL | |

**Explicitly not used:** Leaflet (superseded), hand-written GLSL (TSL supersedes), Lottie (Rive
supersedes), any CDN-hosted asset (§6 Principle #6), any paid API (§6 Principle #6).

---

# Part III — Surfaces

## E16. The landing — "The Walk"

A scroll-driven film in nine acts.

> **The film never cuts to the product. The film becomes the product.** By Act 6 the pins are
> live WebSocket data. By Act 8 the camera has pulled back into what is literally the public
> dashboard, with a working search field in it. There is no "Get started" transition to design,
> because there is no transition.

**Spine.** One normalised `t ∈ [0,1]` driven by a damped scroll proxy (Lenis, `lerp ≈ 0.075`) so
the camera has weight and never jitters. `t` drives a Theatre.js camera and uniform sequence.
GSAP ScrollTrigger drives DOM and type. Scroll-snap per act, so every beat lands.

**Scroll controls distance travelled, not playback.** Stop scrolling and the character stops
walking. This hands the reader physical authorship of the story, and it is the difference between
a film you are watching and a walk you are taking.

| Act | `t` | Beat |
|---|---|---|
| **0 · Cold open** | 0.00–0.05 | Black, grain, distant city. `NEMESIS` sets in Gambarino glyph-by-glyph (24 ms stagger). Courier Prime micro-caps expand the acronym. Then: *Prove, don't log.* A hairline rule draws down as the scroll cue |
| **1 · The walk** | 0.05–0.22 | Locked side-tracking shot. The clay world builds: cardboard buildings, a hoarding, a tea stall steaming, a parked auto, sagging wires. Ink figure on twos, camera smooth. Copy at road level, a beat apart: *A pothole gets reported · The app says "In Progress" · Weeks pass* |
| **2 · The stop** | 0.22–0.32 | Camera pushes to ankle height. The pothole fills the lower third, water in it, tar broken at the lip. Rack focus. **The figure's shoulders drop — one movement, held a full second.** That is the entire disappointment beat |
| **3 · The silence** | 0.32–0.43 | The camera lifts and pans down the road. Ghost flags fade in along the same stretch, each handwritten in Kalam: `reported 14 Mar — no closure`, `02 Apr`, `19 Jun`, `07 Aug`. Nine of them. All dim at once. Then: *"That silence isn't apathy. It's the sound of a system that broke its promise once too often."* |
| **4 · The report** | 0.43–0.55 | The figure raises a phone; the camera **pushes through the screen**. The viewfinder is the real `<ReportCapture>` in DOM over a blurred backdrop. Shutter. The 3D pothole freezes to a photograph, and the photograph **peels off the world into a paper card** — clay becomes paper, the transition the direction rests on |
| **5 · The pipeline** | 0.55–0.70 | The card travels through physical gates, each stamping it. See table below |
| **6 · The merge** | 0.70–0.83 | **THE SHOT.** Pull back to the model at dusk; tilt-shift snaps it to miniature. Three flags on one road lean, converge, merge; the survivor grows; the second ink overprints; **a thumbprint presses in**; registration rings remain. `1 INCIDENT · 3 REPORTS · SEVERITY 0.78` stamps. Then: *"This isn't an animation. It's a live event from the pipeline"* with a mono timestamp ticking |
| **7 · The city awake** | 0.83–0.93 | A survey frame rule-draws in — margins, scale bar, north arrow, legend. The film has become a survey document, and the survey document is the public dashboard. Three entrances: **Report something**, **Look up your ward** (a field, pre-guessed and confirmable), **Department sign-in** (quiet, top-right) |
| **8 · The table** | 0.93–1.00 | One last pull. The model is revealed on a **workbench** — cutting mat, scalpel, glue, a stack of paper, a riso proof drying on the corner. The film's last frame is the console's establishing shot |
| **9 · The receipts** | below fold | Deliberately boring. A real closure with before/after, SSIM score, citizen confirmation, contractor, cost variance; a contractor ledger; a ward's allocated-vs-spent bar; the public API with a live `curl`; and **§44's REAL/SIMULATED/ROADMAP table, published** |

### E16.1 Act 5 — the gates

| Gate | Stamp | Backing event | Status |
|---|---|---|---|
| Safety | `NO HAZARD TRIGGER` / `SAFETY OVERRIDE · BYPASSING QUEUE` | `safety_trigger_fired` | REAL |
| Trust | `EXIF INTACT · DEVICE NOT ON WATCHLIST` | `exif_check_completed` | REAL |
| Perception | `POTHOLE 0.91` **and the runner-up class** | `classification_scored` | REAL |
| Perception — degraded | `CLASSIFIER UNAVAILABLE · PARKED FOR HUMAN REVIEW`. The gate does not stall and does not guess; it stamps a **third outcome** and the card continues. Complaint status `pending_classification` (§24.2, §E26.1) | `pipeline_stage_degraded` | REAL |
| Redaction | a face visibly blurs *on the photograph itself* | `media_redacted` | REAL |
| Dedup | `3 NEARBY REPORTS · MATCH 0.87` | `cluster_match_found` | REAL |

Showing the runner-up and a confidence figure is more persuasive than showing certainty (§E3.3).
Showing a citizen's face disappear from their own evidence is a trust moment no competitor
stages, and it is a real pipeline stage, not an illustration of one.

### E16.2 Publishing the honesty table is the differentiator

Act 9 renders §44 on the marketing surface. Every competitor overclaims; §6 Principle #8 says
this is a competitive advantage rather than a limitation, and Act 9 is where that belief is
actually tested in public.

---

## E17. The citizen product

### E17.1 Report — three screens, one thumb · REAL

The app opens **in the viewfinder**, not on a form.

1. **Capture.** Full-bleed camera, one shutter, a microphone for voice — many users would sooner speak than type, more so in Marathi and Hindi, and the transcription pipeline exists (`media_transcribed`). Then the photo, a four-second undo, and one optional field: *"What's wrong here?"* **The app must work if it is left empty**, per §26.1 where description text is optional.
2. **Place.** Auto-located and presented as a *card*, not a picker: **"Paud Road, near Karve Statue · Kothrud · Ward 14"**, with `adjust` opening a map and a 60 px draggable pin. Never ask someone standing in traffic beside a pothole to pinch-zoom a map. Reverse-geocode, state the guess, allow correction.
3. **Send.** Optimistic. Queued locally and confirmed instantly, before the round-trip. Offline is not an error state (§E21).

### E17.2 The wait is the demo · REAL

§26.1 promises `estimated_processing_time_seconds: 8`. **Do not show a spinner.** Show the Act 5
gates, live, in clay-and-paper miniature, on the citizen's phone. Then the payoff:

> **You're the 4th person to report this.** First reported 6 days ago. Severity raised to *High.*

One sentence that converts a solitary act into a collective one — and it is generated by the real
dedup engine, not written by a copywriter.

### E17.3 The receipt · REAL

Not a toast. A **document**: saveable, shareable, printed on stock with a deckled edge, carrying
the complaint id and chain hash in JetBrains Mono and the line *"This record cannot be edited.
Corrections are added, never overwritten."*

Nobody reads the hash. Everybody feels that this system keeps records. This is §9.1 made tangible
in one card.

### E17.4 Track — a ledger, not a status badge · REAL

"In Progress" is the enemy; it is the exact failure the README opens with. Every state change is
an event with an actor, a timestamp, and evidence, rendered as a vertical paper ledger.

The severity row carries `why? →`, which opens the **actual rubric weights, the actual
contributing factors, and the rubric version** — §13.1's transparent rubric, rendered. A citizen
who can see why their pothole scored below someone's collapsed drain stops believing the system
is rigged, which is worth more than any amount of polish.

*Severity breakdown display is REAL as a component and SIMULATED as data until Phase 12 lands.*

### E17.5 Close the loop · ROADMAP (Phase 15)

Before/after slider, full-bleed, draggable. **"Is it actually fixed?"**

- **Yes** → the stamp; Modak celebrates; `citizen_confirmed`; milestone funds release.
- **No** → the camera opens; `citizen_disputed` with fresh evidence; the work order reopens and the dispute enters the contractor's public record.

**The citizen holds the last gate.** That is the product, and §15.6 already requires it.

### E17.6 Retention — four mechanics, all honest

| Mechanic | What | Status |
|---|---|---|
| **The Ledger** | A citizen's personal record of everything they reported and what became of it, designed as a **passport with stamps** — physical, collectible, every stamp a real event. Printable | REAL |
| **"Your Ward's Month"** | A **20-second stop-motion film auto-generated from the event log** via the Phase 21 replay engine, rendered offscreen through the press, shareable as video or a deep link. Complaints appear, cluster, route, resolve; budget accumulates; breaches ignite | ROADMAP (Phase 21) |
| **Scale rewards** | Pin flags carry the *actual complaint text*, handwritten in Kalam via troika text. Illegible marks at city zoom, like a real model; real reports up close | REAL |
| **Easter eggs** | A stray dog that wanders the model. The tea stall that steams. A gully cricket match that only appears after 18:00 local. A gesture that pulls the camera back to show the animator's hand placing a pin | REAL |

**"Your Ward's Month" is the strongest single item in this document.** No competitor can build it
without rebuilding their foundation, because it is a rendering of an append-only log — and on top
of Phase 21 it is nearly free. It is §21.3's retention mechanic, finally specified.

---

## E18. The public transparency surface

Server-rendered, indexable, deep-linkable. Implements §16.2 and §16.3 against the endpoints Phase
4 shipped.

**Two response fields are UI contracts, not courtesies.** The API returns `rating_disclaimer` and
`SYSTEM_FLAGGED_NOTICE` as *required* fields; they render as first-class UI, never as tooltips or
footnotes. `<FlaggedNotice>` takes the disclaimer as a required prop and cannot render without it
(§E26).

**Suppression needs an honest empty state.** `suppressed` and `suppression_threshold` render as
*"Fewer than N reports — withheld to protect reporters,"* never as a blank cell that reads as
zero. A k-anonymity hole that looks like good news is worse than no data (ADR-0021).

**Contractor profiles are ledgers, never ratings** (§16.1): four independent metrics anyone can
argue with — within-SLA rate, cost variance, confirmed versus disputed, repeat-defect rate — each
tracing to records, with work history and before/after evidence. Flagged rows carry the
fluorescent hatch, the disclaimer, **and the contractor's response and appeal status in the same
frame**. §16.4 ships as a design element, not as a later phase.

---

## E19. The municipal console — "The Light Table"

Ground `mitti-950`, prints backlit on glass (§E9.3). Switzer at 15 px, JetBrains Mono for all
data, **three density modes** (comfortable / compact / dense) persisted per user, command palette
on `⌘K`. Dark-first, keyboard-first, dense, and **printable — print is a first-class target with
its own stylesheet**, because officers print.

### E19.0 Roles change the shell, not just the permissions · ROADMAP (Phase 13)

| Role | Sees | Can do |
|---|---|---|
| **Commissioner** | City-wide, all departments | Read, policy approval, blacklist co-sign, exports |
| **Department head** | Own department, all zones | Assign, budget, close, select contractors |
| **Zone / ward officer** | Own zone, all departments | Triage, escalate, verify closure |
| **Field staff** | Only assigned work orders | Mobile: capture evidence, update status |
| **Auditor** | Everything, read-only | Export, build case files — **cannot mutate** |
| **Contractor** | Own records only | Upload evidence, respond to flags, appeal (§E20) |
| **Platform support** | Cross-tenant; **impersonation audited and visible to the tenant** | Diagnose |

Field staff never see a kanban. They see three jobs and a camera (§E21).

### E19.1 Command · partially REAL

Split 60/40, resizable, remembered. **Left:** the model city top-down, ward boundaries extruded as
low clay walls so jurisdiction reads spatially, sun matching real local time. **Right:** the
queue — dense 36 px rows, default sort **SLA remaining ascending, then severity**, countdowns
climbing the glaze ramp as they burn.

Above it, one strip, and it is *not* vanity metrics:

> **7 breach in the next 24 hours.** 2 unassigned. 1 has no contractor certified for this category.

A dashboard should tell you what to do before it tells you how you are doing.

*The review queue behind this is REAL today. SLA countdowns are ROADMAP (Phase 12).*

### E19.2 Area view — the ward room · ROADMAP (Phase 12, 23)

Category mix over time, open versus closed, SLA performance, budget allocated and spent,
contractor share of work, and the **repeat-defect map** — where corruption first becomes
geographically visible.

Plus the panel nobody else builds, implementing §23.2:

> **⚠ Underreporting signal.** Ward 22 files 0.19 reports per road-km per month against a city
> median of 0.94, with similar road-condition indicators. **This is likely under-reporting, not
> good roads.** *Suggested: assisted-reporting drive · compare against 311 phone volume.*

This converts §23.1's bias risk from a documented concern into an operational recommendation, and
it is the answer to the first question any thoughtful commissioner asks.

### E19.3 Work order — assignment made fair · ROADMAP (Phase 14)

Evidence trail left, action rail right. **Contractor selection is a transparency feature, not a
dropdown** (§15.3): each candidate shows within-SLA rate, cost variance, and **current workload**,
and beneath the list sits the line that does the work —

> ⚠ Shirke has received 61% of road work in Ward 14 this quarter.

It blocks nothing. It makes the pattern impossible to not-know at the moment of the decision, and
it is logged that the assigner saw it.

**Budget entry** against the rate card shows **variance live in the field**: type 30% over
Schedule of Rates and the field says so before submission and requests a justification note that
becomes part of the record (§15.4). **Milestones** (30/40/30, §15.5) render as a physical gate
strip. You cannot drag a milestone open. Only evidence opens it.

### E19.4 Closure — evidence or nothing · ROADMAP (Phase 15)

**The backend enforces this; the UI renders it.** `WorkOrder.ssim_score` carries the comment
*"Phase 15's state machine — not the UI — refuses to reach `resolved` without it."* That division
is deliberate and this document must not blur it: a client-side check is a convenience, and if it
is ever mistaken for the control, someone will eventually ship a path around it.

The UI's job is to make the rule **legible before it is hit**. `Resolved` renders visibly
disabled **with the unmet conditions attached**:

- a before/after pair exists, with the "after" EXIF location within tolerance,
- SSIM verification has run — **and its score is printed honestly, including when ambiguous**,
- citizen confirmation has been requested.

Making an integrity rule visible in its disabled state is what teaches an organisation the rule.
A validation that fires on submit teaches nothing, and a validation the UI *owns* teaches
something false.

### E19.5 Money · ROADMAP (Phase 14, 23)

Allocated versus spent, sliceable by ward, department, contractor, and scheme (AMRUT, Smart
Cities Mission, ward fund), with variance. A **"what citizens see"** toggle renders the same
figures with suppression applied. Knowing that your internal number and your public number are
the same number is the entire point of §16.2.

### E19.6 Integrity — where corruption becomes a case · ROADMAP (Phase 17)

Designed as an **investigation tool**, not an accusation machine — otherwise it is simultaneously
a §22.2 liability and a political weapon.

**Signals**, each hatched in fluorescent pink and disclaimed: cost-variance outliers against the
rate card; repeat defects within 90 days of closure; award concentration against the city
distribution; closure photographs failing SSIM or EXIF-distant; end-of-fiscal-year closure
clustering; milestone releases where all three photographs uploaded inside four minutes; and
**entity resolution** — contractors sharing a phone, address, PAN, or director — as a
force-directed graph. That is the one place in this product where a network diagram earns its
place, because shell-company structure *is* a graph (§17.1).

**Every signal card shows the detector name, its threshold, and its confidence.** If you are going
to flag a named commercial entity, you show your method.

**The case file** is the real feature: a paper document where evidence items are attached *by
reference*, each with its own hash and source — you cannot paste a screenshot into a case, you
attach a record — where findings are written by a named officer and versioned, where the
contractor's response is mandatory to solicit and carries a clock, and where a second approver is
required for any adverse finding.

**Blacklisting is never a button.** It is the outcome of a completed case with: a minimum evidence
count, a solicited response or an expired response window, two named approvers, a stated
duration, and a published basis. The UI enforces this by **showing the unmet requirements** — the
action is visible and disabled, reading *"3 of 5 requirements met"*, which is far more honest than
hiding it. On the public profile, a blacklist **always** renders with its appeal status beside it
(§16.4).

### E19.7 Report builder · ROADMAP (Phase 23)

An officer's actual job is producing documents for someone above them. Scope and period select
into a Gambarino-covered, Courier Prime-bodied risograph document with charts in ground inks, an
evidence appendix, and a verification footer carrying the chain root and a verify URL.

**A report that carries its own proof is a category difference from a report that carries a
logo.** Export to PDF; the PDF is the same design as the screen.

### E19.8 Policy studio & simulation · REAL

The console's sharpest screen, and it is fully backed today.

Rules as editable documents with revision history and diff. **The activate control is disabled
without a backtest**: *"Run against a labelled set to see what this would have changed."* Then the
result — *"this threshold would have merged 47 additional reports across 400 historical
complaints, 6 of which reviewers later reverted."*

Retuning becomes an evidence-based act. This implements §13.3's "rubric improves as data
accumulates" as an actual interface, and per PHASES defect #3 it is the reason nobody discovers
the damage from a threshold change on real citizen reports.

### E19.9 Temporal replay · ROADMAP (Phase 21, backend *and* UI)

**Correcting an earlier draft of this document**, which listed this as backed by
shipped backend. It is not. The event log is complete and hash-chained, which is
what makes replay *possible*; the server-side windowed streaming with snapshot
seeking that makes it *fast* is Phase 21 and does not exist. There is no replay
endpoint. Nothing in this section can be built before that ships.

Scrub a ward's twelve months in clay, replayed through the same projectors as the live system.
Two audiences: an officer asking *what actually happened on this road last year*, and a journalist
handed a deep link at a timestamp and camera position. Also the render source for §E17.6's ward
film.

---

## E20. The contractor portal · ROADMAP (Phase 17)

A third product surface, not a tab. §16.4 states plainly that a one-sided accountability system
invites resistance and sabotage, and §6 Principle #5 requires fairness to both sides.

- Own records only. Work history, evidence uploads, milestone status, payment state.
- **Respond to a flag** — every system-flagged anomaly has a response affordance with a clock, and the response renders on the public profile alongside the flag.
- **Appeal** a disputed closure or an adverse finding, with additional evidence.
- **Cross-ward performance, internal only** (§16.4) — a contractor sees their own breakdown by ward, which is the data that distinguishes systemic underperformance from localised assignment problems. Not public, to avoid unverified public accusation.
- Contractors get the **light table** too, at lower density and with an onboarding path, because most will not be daily users.

---

## E21. Field & offline · ROADMAP (Phase 22)

Field staff work in basements, back lanes, and dead zones. The people expected to upload closure
evidence have the worst connectivity in the system.

- Three jobs on a list, a large camera button, offline queue with visible per-item state.
- **Outdoor mode** goes near-monochrome with heavier weights and larger type. Sunlight on a phone at noon defeats every subtle palette; design for gloves and glare, not for a design review.
- Camera-first capture with client-side compression and EXIF preservation.
- Background upload with per-item state, so nothing fails silently.
- Conflict-free sync on reconnect, made safe by server-side idempotency (§E14.2).

---

# Part IV — Standards

## E22. Accessibility & internationalisation

**WCAG 2.2 AA is a floor, audited rather than only scanned** (a Phase 18 gate).

- Every severity pair tested at 4.5:1 on both grounds — **and re-tested after the press**, because halftone changes effective contrast. Text is exempt by construction, since it prints solid (§E6.2).
- Full keyboard path **including the map**: arrow-key pin traversal, `/` search, `j`/`k` queue, `e` evidence, `⌘K` palette.
- **The 3D map always has a synchronised accessible list view in the DOM — a peer, not a fallback, always present.** A canvas is opaque to assistive technology; a list of the same entities is not.
- Reduced motion is a complete alternate edit (§E13 Tier C), not a disabled animation.
- Sound is off by default and every bus respects `prefers-reduced-motion` as a sensory-sensitivity proxy.

**Internationalisation** per §E10.1: per-script type scale, +0.15 line-height on Devanagari,
sentences as translation units, RTL-ready layout primitives, and every string from the Phase 5
locale registry so **a locale added in the control plane appears with no code change** — which is
the Phase 18 gate, verified in §E25.

---

## E23. Performance budgets

Local deployment on one laptop. The GPU is shared with Ollama (ADR-0002), which is the binding
constraint — not developer hours (§E2 defect #7).

| Budget | Value | Enforced by |
|---|---|---|
| Scene VRAM | **≤ 512 MB** | CI assertion. The Investigation Agent must never be starved by the map |
| Frame rate | 60 fps sustained with 5 000 instanced pins + extruded footprints | Measured on the actual laptop, **with Ollama running** |
| Draw calls | Under budget; instanced pins are one call | `renderer.info`, captured in CI |
| Console TTI | < 1.5 s | Lighthouse |
| Landing LCP | < 2.0 s | Hero deferred behind the Act 0 still |
| Lighthouse | ≥ 90 performance and accessibility on citizen and department routes | Phase 18 gate |

**Adaptive quality degrades effects before it degrades frame rate**, and the first thing it turns
is the press's quality dial (§E6.4) — which is the one degradation in this product that improves
the picture.

---

## E24. Design ops

Corrects §E2 defect #13 and delivers §19.3's promise that severity colour is "defined once."

- **Tokens are the source of truth.** Authored once in JSON, generated into CSS custom properties **and** TSL uniform constants. The severity ink in a badge and the glaze in a shader are literally the same number, generated, never re-typed. A hand-written colour literal in application source fails CI, mirroring the backend's "no magic values" standard.
- **Storybook** for every component across three densities × two themes × two scripts.
- **Playwright + SwiftShader headless WebGL harness** with golden-image visual regression per act and per scene at fixed seed and camera.
- **`axe` gating and Lighthouse budgets** in CI.
- **Zero `any` in application source** (Phase 18 gate). Generated client drift fails CI.
- **Fixtures are generated, never invented.** Where a phase has not landed, its screens are built against the **real generated types** — the tables and schema fields listed in §E1 already exist — with fixture *values*, never a fixture *shape*. A hand-written interface describing a backend contract is a review failure; if the type does not exist yet, the screen is not ready to build.
- **The "not wired" chip.** Screens whose contract returns nulls today carry a permanent dev-only badge and **cannot be routed to a public URL** until the backing phase populates them. Track E races ahead of the backend without ever lying about it — §6 Principle #8 enforced by the build, not by discipline.
- **Design QA ritual.** Every visual PR posts its Storybook diff and a five-second scene capture.
- **Usability testing with real field staff and department users**, with task-success measured and findings tracked (a Phase 18 gate — the plan calls this a design *practice*, not a component library, and that distinction is the gate).

---

## E25. Phase gates — 18 through 22, restated

These extend `docs/PHASES.md` Track E rather than replacing it. Each is objective and
machine-checkable except where an audit is explicitly required.

### Phase 18 — Design system, application shell & i18n
- Lighthouse ≥ 90 performance and accessibility on citizen and department routes
- WCAG 2.2 AA verified by audit, not only by automated scan
- Zero `any` in application source; generated-client drift fails CI
- **A locale added in the control plane appears in the UI with no code change**
- Measured task-success rate from a usability session, with findings tracked
- **New:** token generation produces CSS and TSL from one source; a hand-written colour literal in application source fails CI
- **New:** the press renders identically in 2D and 3D at fixed seed, and **text layers are byte-identical with the press on and off**

### Phase 19 — Geospatial 3D engine
- 60 fps sustained with 5 000 instanced pins plus extruded buildings on this laptop, measured, **with Ollama running**
- Draw calls under budget; **VRAM ≤ 512 MB asserted in CI**
- Forced context loss recovers to a correct scene without a page reload
- `prefers-reduced-motion` and a no-WebGL device both render a correct, usable map
- **New:** the WebGL2 backend fallback renders the same scene as WebGPU, verified by golden image
- **New:** the accessible list view is present and synchronised in every tier

### Phase 20 — Signature scenes & shader layer
- **Every scene is triggered by a genuine backend event in an E2E test — a scene that can only be fired by a button fails the gate**
- Every fallback tier is exercised in CI by forcing its trigger
- Golden-image regression passes per scene at fixed seed and camera
- Frame budget held with all effects enabled
- **New:** Tier C storyboard prints are reviewed as a design deliverable, not generated

### Phase 21 — Temporal replay
- Replayed state at timestamp *T* is byte-identical to the projection computed independently at *T*
- Scrubbing 12 months of seeded history holds frame budget
- Replay is provably read-only — it cannot emit events or mutate projections
- **New:** "Your Ward's Month" renders offscreen deterministically at a fixed seed

### Phase 22 — Field & offline experience
- A complaint and a closure photo captured fully offline sync correctly on reconnect
- A killed app mid-upload resumes without duplicating or losing the submission
- The flow is usable end to end on a throttled 2G profile
- **New:** outdoor mode passes contrast at 7:1 for primary text

---

# Appendices

## E26. Appendix A — component contracts

Components appearing on three or more surfaces must render identically everywhere. Where a rule
below says *required prop*, it is enforced by the type system, not by review.

| Component | Contract |
|---|---|
| `<Press>` | The print pipeline. 2D CSS/SVG layer and 3D TSL pass from one token source. Text composites unprocessed |
| `<SeverityBadge>` | Ink + shape + label. Never colour alone |
| `<EvidenceTrail>` | The event ledger. Citizen, officer, and public views differ **only by row filtering** — never by different code |
| `<BeforeAfter>` | Slider + SSIM score + capture metadata. Identical on all three surfaces, because a contractor's evidence must look the same to everyone who sees it |
| `<FlaggedNotice>` | Fluorescent hatch + **required** `disclaimer` prop + **required** `responseHref`. Cannot render without them (§16.4, §22.2) |
| `<SuppressionNotice>` | *"Fewer than N reports — withheld to protect reporters."* Never a blank cell |
| `<Receipt>` | Deckled paper, mono id, chain hash, the append-only sentence |
| `<ContractorLedger>` | Four metrics. **Cannot be collapsed to one score** — no single-value variant exists in the API |
| `<DegradedBanner>` | Named degradation with an honest cause. Calm register, secondary ink, never an error colour |
| `<Stamp>` | The one confirmation primitive |
| `<ClayScene>` | 3D host: renderer, adaptive quality, context-loss recovery, the accessible peer list |

### E26.1 The status vocabulary

Corrects §E2 defects #15 and #16. These enums are defined in
`backend/nemesis/domain/lifecycle.py` and are the **complete** set. A status chip
rendering a value not on these lists, or a view omitting one, is a defect —
including the states that only occur when something has gone wrong, which are the
ones a UI is most likely to forget and most needs to show.

**`ComplaintStatus` — thirteen members**

```
submitted · verifying · classified · pending_classification · clustered · scored
routed · in_progress · pending_verification · resolved · closed · disputed · flagged
```

Two need explicit design, because both mean *the system did not do the normal thing*:

- **`pending_classification`** — the classifier was unavailable, so the pipeline parked the report for human review rather than guessing a category that would be indistinguishable downstream from a confident one (§24.2). The tracking ledger and the pipeline theatre (§E17.2) must render this as a **third outcome** of the perception gate, not as a stalled second one. It is also the better trust moment: *"we didn't guess"* reads as competence, where a fabricated 0.6 confidence reads as noise.
- **`flagged`** — routed out of the normal path by trust and safety. Renders in the fluorescent hatch per ADR-0039, never in a severity colour.

**`WorkOrderStatus` — six members, and the board shows all six**

```
created · assigned · in_progress · pending_verification · closed · disputed
```

`created` is the unassigned backlog and `disputed` is a citizen rejecting a
closure. §15.7 omits both; §E19 shows both. `created` is the column §E19.1's
breach strip counts, and `disputed` is the one the contractor ledger and the
appeal path (§E20) both hang off.

**`MilestoneStage`** — `start · mid · complete`, matching §15.5's 30/40/30 gate
strip. **`AssigneeType`** — `staff · contractor`; a work order has one or the
other, never both, so the assignment UI is a single control with two modes rather
than two independent pickers.

---

## E27. Appendix B — event-to-surface traceability

§6 Principle #9 requires every visual element to map to a real pipeline event. This table is the
audit. A visual element not on this list, and not classifiable as chrome, is a defect.

| Event | Surface | Visual |
|---|---|---|
| `complaint_submitted` | Report, map, console | Pin pushed into the clay; the Settle motion |
| `exif_check_completed` | Pipeline theatre | Trust gate stamp |
| `safety_trigger_fired` | Pipeline theatre, map | Safety stamp; **selective bloom — the only glow in the product**; the struck metal note |
| `classification_scored` | Pipeline theatre, tracking | Category + confidence + runner-up |
| `media_transcribed` | Report, evidence trail | Transcript on paper |
| `media_redacted` | Pipeline theatre | The face blurring on the photograph itself |
| `perceptual_duplicate_detected` | Map | Registration ring appearing |
| `cluster_match_found` | **Map — the hero** | **The Merge**: converge, overprint, thumbprint, count stamp |
| `cluster_created` / `complaint_clustered` | Map, tracking | Flag count increments |
| `cluster_merge_reverted` | Console, map | The merge running backward; rings re-separate |
| `severity_scored` | Everywhere | Glaze colour; pin height; the `why? →` breakdown |
| `review_queued` / `review_decided` | Console review queue | Queue row; decision stamp |
| `abuse_pattern_flagged` | Console | Fluorescent hatch + disclaimer |
| `pipeline_stage_degraded` / `system_degradation` | Every surface | `<DegradedBanner>` |
| `policy_drafted` / `policy_certified` / `policy_transitioned` | Policy studio | Revision list; activation stamp |
| `evaluation_set_published` | Simulation | Backtest availability |
| `work_order_created` / `work_order_assigned` | Console kanban, tracking | Assignment row *(Phase 14)* |
| `ssim_verification_completed` | Closure | The printed SSIM score *(Phase 15)* |
| `citizen_confirmation_requested` | Tracking | Pending gate |
| `citizen_confirmed` | Tracking, map, character | Stamp; Modak; closure dissolve; Rive `relief` |
| `citizen_disputed` | Tracking, contractor profile | Dispute row on the public record |
| `taxonomy_published` / `organisation_changed` / `tenant_provisioned` | Console control plane | Tree updates |
| `admin_action` | Audit view | Ledger row |

---

## E28. Appendix C — REAL / SIMULATED / ROADMAP, frontend rows

Reconciles into §44. Status is against **M7** plus ADR-0043/0044/0045/0046.

**What M7 changed here, and what it deliberately did not.** Thirteen console
rows moved. Four moved to **REAL / REAL** and are finished — the review queue,
the policy studio and simulation, control-plane admin, and the developer portal
— because the backend behind all four already shipped and F4–F6 built the
surfaces onto it. Eight moved to **component REAL, data ROADMAP**: they are
screens an officer can open, built against generated types with fixture
*values* behind the §E24 chip, and the two-column split is the whole reason
that is sayable without lying. One did not move: **Command view SLA
countdowns**, and its row says why.

**The M5 rows in this table were wrong for the length of one milestone**, and that is
recorded rather than quietly corrected. M5 shipped `/report`, the six-gate theatre, the
third outcome and the dedup payoff, and every one of those rows still read **ROADMAP**
here — because the progress table in `docs/FRONTEND-EXECUTION-PLAN.md` was updated and
this one was not. It was found by M6 *generating* the published honesty page from this
table and somebody reading the result. That is the argument for generating it: a
hand-kept honesty table drifts in exactly the direction that flatters, and the drift is
invisible until the table is put somewhere people look.

**This table was wrong, and the way it was wrong is worth recording rather than
quietly fixing.** The original had one status column, and it answered two
different questions at once: *does real backend stand behind this?* and *has
Track E built it?* For most rows the two answers differ, and collapsing them
produced a table where `Report capture → submit → receipt` read **REAL** while
`/report` was a three-line placeholder, and `Tracking ledger from the event log`
read **REAL** while no endpoint served a complaint's history at all. §6 Principle
#8 and §E3.3 both say the same thing about this: an honesty table that is wrong
is worse than no table, because it is the artefact a reader trusts *instead of*
checking.

So there are two columns now. **Component** is what Track E has shipped and can
be opened in a browser. **Data** is what stands behind it. The milestone column
says which step of `docs/FRONTEND-EXECUTION-PLAN.md` closes the component; the
phase column says which backend phase closes the data. A row is only finished
when both read REAL.

| Frontend capability | Component | Data | Closes at |
|---|---|---|---|
| Design system, tokens, press, type stack | **REAL** — M1, M2 | — | done |
| §E26 component contracts (badge, trail, before/after, flagged, suppression, receipt, ledger, banner, stamp, clay scene) | **REAL** — M4, and `<ClayScene>` at M8/F8: renderer, adaptive quality, context-loss recovery and the accessible peer list, with the list asserted present and synchronised in every tier | — | done |
| Generated client, BFF seam, WebSocket store, reconciliation rule | **REAL** — M3 | **REAL** | done |
| Report capture → submit → receipt | **REAL** — M5; `/report` opens in the viewfinder and the receipt carries the chain head | **REAL** — `POST /complaints` ships, and the 202 carries the chain head (ADR-0044) | M5 |
| Pipeline theatre — **six** gates, not five | **REAL** — M5; every gate reads the log, and a gate the log will never reach says *held* rather than *waiting* | **REAL** — all six are drivable from a genuine event since ADR-0045 shaped `exif_check_completed` and `media_redacted` | M5 |
| The third outcome (`pending_classification`, §24.2) | **REAL** — M5 | **REAL** — `pipeline_stage_degraded` is shaped, and the status is on the published enum | M5 |
| Tracking ledger from the event log | **REAL** — `<EvidenceTrail>`, built against the published envelope | **REAL** — `GET /complaints/{id}/events` (ADR-0043) | screen at M5 |
| Dedup payoff — *"you're the 4th person"* | **REAL** — M5, from the real engine | **REAL** — `cluster_match_found` carries `report_count` | M5 |
| Severity breakdown panel | **REAL** — M5, `why? →` opens the rubric | **SIMULATED** — `severity_score`, `severity_breakdown`, `severity_policy_version` are on the published v1 schema and return null | M5/M7 · Phase 12 |
| Cluster-merge hero, live | **REAL** — M9/F14; Act 6 renders the merge from `cluster_match_found` and renders **nothing** until one arrives — no stamp, no rings, and the empty state is what the gate asserts. Watching a real one land is **unexercised on this checkout**: the committed test photograph scores below the classifier's own floor, so the report parks before deduplication and no merge is ever published. Recorded, not waived: [`docs/reports/story-merge-gate.md`](docs/reports/story-merge-gate.md) | **REAL** | done |
| Live map, instanced pins | **REAL** — M8; one instanced draw call for the city and one for its pins, driven off the M3 bus, with a synchronised DOM list beside it in every tier. The **frame rate** clause of the Phase 19 gate is a recorded deviation, measured: [`docs/reports/clay-frame-rate.md`](docs/reports/clay-frame-rate.md) | **REAL** | done |
| Temporal replay — endpoint and UI both | **ROADMAP** | **ROADMAP** — *no replay endpoint exists; an earlier draft of this document wrongly listed the backend as shipped* | Phase 21 |
| "Your Ward's Month" film | **ROADMAP** | **ROADMAP** | Phase 21 |
| Review queue | **REAL** — M7/F4; the queue, the item and the decision, with `<EvidenceTrail>` under officer filtering — the same component the citizen reads, differing by which rows are filtered and by nothing else | **REAL** — `/api/v1/review` ships, media included | done |
| Policy studio + simulation | **REAL** — M7/F5; rules as documents with revision history and a structural diff, the backtest stated in reverted-decision terms, shadow mode and rollback. Activation is refused by the server and the disabled control says why (§E19.4) | **REAL** — backtest, shadow mode, activation guardrail all ship | done |
| Control-plane admin (taxonomy, zones, departments, calendars, locales) | **REAL** — M7/F6; a tenant is provisioned, given an invented taxonomy and published through the UI, including ADR-0046's publication control with its justification as a first-class input | **REAL** | done |
| Developer portal (keys, webhooks, usage, versions) | **REAL** — M7/F6; keys, webhooks with their delivery log and secret rotation, usage, and the version registry with its deprecation clock | **REAL** | done |
| Public place pages (zone / ward) | **REAL** — M6 | **ROADMAP** — the aggregate is correct and can never match: `complaints.ward` is nullable and nothing writes it, so every figure is structurally zero | Phase 12 |
| Public contractor and budget pages | **REAL** — M6 | **REAL** — k-anonymous aggregates ship; *thin until Phases 14–17* | done |
| Suppression rendered rather than blanked | **REAL** — M6; a figure is a union the compiler will not let a surface print as a number | **REAL** | done |
| Share cards (`satori` + `resvg`) | **REAL** — M6, on four self-hosted faces built from the shipped woff2 | **REAL** | done |
| Role-based console shells | **REAL** — M7/F7; built against generated types with fixture *values*, behind the §E24 chip | **ROADMAP** | Phase 13 |
| Command view SLA countdowns | **ROADMAP** — and deliberately so after M7/F7: the command view carries real queue figures, and a fixture countdown beside them would put measurements and decoration in one strip with nothing on screen saying which is which. The breach line renders its Phase 12 chip and the sentence it will say, instead of a number it cannot know | **ROADMAP** | Phase 12 |
| Area view + underreporting signal | **REAL** — M7/F7, fixture values behind the chip | **ROADMAP** | Phase 12 · Phase 23 |
| Work order, assignment, contractor picker, budget entry | **REAL** — M7/F7, fixture values behind the chip; the concentration warning and the live rate-card variance render | **ROADMAP** | Phase 14 |
| Milestone gate strip | **REAL** — M7/F7, fixture values behind the chip | **SIMULATED** — fund release is a ledger event, never a disbursement (§15.5) | Phase 14 |
| Closure gates + SSIM display | **REAL** — M7/F7; `Resolved` renders disabled with its unmet conditions attached and the ambiguous SSIM printed as the number it is | **ROADMAP** | Phase 15 |
| Money view | **REAL** — M7/F7, fixture values behind the chip, including the *what citizens see* toggle | **ROADMAP** | Phase 14 · Phase 23 |
| Integrity room, case file, blacklist flow | **REAL** — M7/F7; the blacklist action renders *n of m requirements met* rather than hiding itself (§E19.6) | **ROADMAP** | Phase 17 |
| Contractor portal + appeals | **ROADMAP** | **ROADMAP** | Phase 17 |
| Report builder + verifiable PDF | **REAL** — M7/F7, fixture values behind the chip, with its verification footer | **ROADMAP** | Phase 23 |
| Honesty table published as data (§44 + §E28) | **REAL** — M6; generated from these two documents and drift-checked in CI, so a blueprint edit that is not republished is a build failure | **REAL** | done |
| RTI draft generation | **ROADMAP** | **SIMULATED** — template auto-fill only, no filing integration (§16.1) | Phase 23 |
| Accident-prone & traffic overlays | **ROADMAP** | **ROADMAP** | Phase 23 |
| PWA, offline queue, outdoor mode | **ROADMAP** | **REAL** — server-side idempotency is what makes the queue safe, and it ships | M11 · Phase 22 |
| Sound design | **ROADMAP** — the library is unauthored | — | M10 |
| Tiers S / A / B / C / D fallback ladder | **ROADMAP** — the press's quality dial is REAL; the tier ladder above it is not | — | M8–M10 |
| Golden images, Storybook diffs, Lighthouse, WCAG audit, usability session | **ROADMAP** — see the outstanding register, group A | — | M5–M12 |

**Two rows are deliberately left as they were.** *Temporal replay* and *"Your
Ward's Month"* were already honest, including the note recording that an earlier
draft got one of them wrong. Rewriting a correction erases the record of the
mistake, which is the thing this repository has consistently refused to do.

---

*This document is the Track E counterpart to `NEMESIS-Blueprint-v2.md`. Where the two disagree on
frontend matters, this one governs, and §E2 records why. Where they disagree on anything else, the
main blueprint governs.*
