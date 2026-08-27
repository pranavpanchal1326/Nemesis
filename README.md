<div align="center">

<img src="assets/nemesis-mark-transparent.png" alt="" width="132">

<h1>NEMESIS</h1>

**Networked Enforcement & Municipal Evidence System**<br>
<sub>for Infrastructure & Service Accountability</sub>

<sub>─────────────────  ○  ─────────────────</sub>

### *The system that remembers*

<br>

![Stack](https://img.shields.io/badge/FastAPI_·_Postgres_·_Celery-16130F?style=flat-square&labelColor=16130F&color=925F52)
![Frontend](https://img.shields.io/badge/Next_16_·_React_19_·_Three.js-16130F?style=flat-square&labelColor=16130F&color=3D5588)
![Events](https://img.shields.io/badge/event--sourced-34_types-16130F?style=flat-square&labelColor=16130F&color=0FA9A0)
![ADRs](https://img.shields.io/badge/ADRs-61-16130F?style=flat-square&labelColor=16130F&color=8A7462)
![Honesty](https://img.shields.io/badge/every_claim-status--labelled-16130F?style=flat-square&labelColor=16130F&color=C97A1E)

</div>

<br>

> There's a particular kind of silence that settles over a neighbourhood after people stop believing anything will change. A pothole gets reported. The app says *"In Progress."* Weeks pass. The pothole is still there — or worse, patched so poorly it reopens by monsoon. No one knows who did the work, what it cost, or why it failed. So the next complaint never gets filed.
>
> **That silence isn't apathy. It's the sound of a system that broke its promise once too often.**

NEMESIS is a municipal accountability platform built on one premise: **a civic system earns trust by producing evidence, not status updates.** Every figure it publishes is computed from an append-only event log. Every claim it makes about *itself* carries a status label. Nothing is edited by hand.

<div align="center"><sub>─────────────────  ○  ─────────────────</sub></div>

<br>

## The product

Every image below is a photograph of the running application, taken by [`frontend/scripts/capture-product-shots.ts`](frontend/scripts/capture-product-shots.ts) against a live stack seeded with `nem seed-demo` — a demo tenant for Pune. Nothing here is a mockup, a rendering, or a retouched frame. The captions are the ones in [`assets/screens/shots.json`](assets/screens/shots.json), which the capture writes as it shoots.

<div align="center">

<img src="assets/screens/01-landing-cold-open.jpg" alt="The landing film's cold open — the NEMESIS wordmark over a clay model of Pune, with a list of every ward beside it" width="900">

<br>

<sub><b>The cold open</b> — the wordmark over a clay model of the city, lit by that city's real local time and its real weather. Every place the model draws is listed beside it in text, at full contrast, for anyone the canvas fails.</sub>

</div>

<br>

### The Walk — nine acts

<sub>A scroll-driven film. The camera is on a spine with one normalised position, so an act is a number somebody can write down rather than wherever the smooth-scroll happened to stop.</sub>

<div align="center">

| <img src="assets/screens/02-landing-walk.jpg" width="280" alt="Act 1, the walk"><br><sub><b>1 · The walk</b><br>A pothole gets reported. The app says “In Progress”. Weeks pass.</sub> | <img src="assets/screens/03-landing-stop.jpg" width="280" alt="Act 2, the stop"><br><sub><b>2 · The stop</b><br>The camera drops to ankle height and the problem fills the lower third.</sub> | <img src="assets/screens/04-landing-silence.jpg" width="280" alt="Act 3, the silence"><br><sub><b>3 · The silence</b><br>One ghost flag per report that was never closed. They dim together.</sub> |
|:---:|:---:|:---:|
| <img src="assets/screens/05-landing-report.jpg" width="280" alt="Act 4, the report"><br><sub><b>4 · The report</b><br>The camera pushes through the phone into the real citizen app, running in the page.</sub> | <img src="assets/screens/06-landing-pipeline.jpg" width="280" alt="Act 5, the pipeline"><br><sub><b>5 · The pipeline</b><br>Every gate stamps the card with what it found. A stamp is an event, never a status flag.</sub> | <img src="assets/screens/07-landing-merge.jpg" width="280" alt="Act 6, the merge"><br><sub><b>6 · The merge</b><br>Duplicates fuse into one cluster — and the scene waits for a real match rather than playing one.</sub> |
| <img src="assets/screens/08-landing-city-awake.jpg" width="280" alt="Act 7, the city awake"><br><sub><b>7 · The city, awake</b><br>Every ward this deployment publishes, from the live open-data API.</sub> | <img src="assets/screens/09-landing-table.jpg" width="280" alt="Act 8, the table"><br><sub><b>8 · The table</b><br>The model, photographed on the bench it was made on. The next frame is the console.</sub> | <img src="assets/screens/10-landing-receipts.jpg" width="280" alt="Act 9, the receipts"><br><sub><b>9 · The receipts</b><br>Deliberately boring. A live <code>curl</code>, the honesty counts, every published place.</sub> |

</div>

<br>

<div align="center"><sub>─────────────────  ○  ─────────────────</sub></div>

### For residents

<div align="center">

<img src="assets/screens/11-resident-home.jpg" alt="The residents' door — report a problem, follow a report, read what the city publishes" width="900">

<br>

<sub><b>Three doors</b> — report a problem, follow a report by its receipt id, or read what the city publishes about itself. <code>/citizen</code></sub>

</div>

<br>

<sub>The four frames below are one continuous session against the live stack: a real photograph uploaded, a real ward resolved from a real coordinate, a real complaint written to the log, and a receipt whose hash is the hash of the event the backend actually wrote.</sub>

<div align="center">

| <img src="assets/screens/12-resident-capture.jpg" width="205" alt="Step 1, the viewfinder"><br><sub><b>1 · Capture</b><br>Photograph it, say where, send. About thirty seconds.</sub> | <img src="assets/screens/13-resident-describe.jpg" width="205" alt="Step 1, describing the problem"><br><sub><b>2 · In your words</b><br>A four-second undo, and one optional line.</sub> | <img src="assets/screens/14-resident-place.jpg" width="205" alt="Step 2, the place card"><br><sub><b>3 · Where</b><br>A card, not a picker — resolved against the city's own zone tree.</sub> | <img src="assets/screens/15-resident-receipt.jpg" width="205" alt="Step 3, the receipt with its chain hash"><br><sub><b>4 · The receipt</b><br>A document. Its claim is a SHA-256 of the event that was written.</sub> |
|:---:|:---:|:---:|:---:|

</div>

<br>

<div align="center">

<img src="assets/screens/16-resident-track.jpg" alt="The evidence trail for a filed report — each gate it passed through, with hashes" width="900">

<br>

<sub><b>The evidence trail</b> — the receipt id is the only way in. Nobody can look the report up by name, and neither can we. Each gate it passed is an event with a hash, not a status. <code>/t/&lt;id&gt;</code></sub>

</div>

<br>

<div align="center"><sub>─────────────────  ○  ─────────────────</sub></div>

### What the city publishes

<sub>Open data, on public URLs, with the disclaimer as first-class UI rather than a footnote. A contractor's record is a ledger of four independent metrics — the API publishes no single score, so there is nothing to collapse into a rating.</sub>

<div align="center">

| <img src="assets/screens/17-public-city.jpg" width="420" alt="The city's open-data portal"><br><sub><b>The city</b> — every zone and ward, with what is counted and what is withheld. <code>/&lt;tenant&gt;</code></sub> | <img src="assets/screens/18-public-ward.jpg" width="420" alt="One ward's public page"><br><sub><b>One ward</b> — by category, with the suppression threshold stated rather than implied.</sub> |
|:---:|:---:|
| <img src="assets/screens/19-public-budget.jpg" width="420" alt="A zone's budget page"><br><sub><b>The money</b> — allocated against spent, in a fiscal year a resident can name.</sub> | <img src="assets/screens/20-public-contractor.jpg" width="420" alt="A contractor's public ledger"><br><sub><b>The contractor ledger</b> — four metrics side by side. No overall score, because an average would hide the one that matters.</sub> |

</div>

<br>

<div align="center">

<img src="assets/screens/21-public-honesty.jpg" alt="The honesty table — every claim the product makes about itself with a status label" width="900">

<br>

<sub><b>What is real</b> — every claim this product makes about <i>itself</i>, labelled <code>REAL</code>, <code>SIMULATED</code>, <code>ROADMAP</code>, <code>CUT</code> or <code>REFRAMED</code>. The counts are generated, not typed. <code>/&lt;tenant&gt;/honesty</code></sub>

</div>

<br>

<div align="center"><sub>─────────────────  ○  ─────────────────</sub></div>

### For staff

<div align="center">

<img src="assets/screens/23-console-command.jpg" alt="The console's command screen — what breaches first, the city, and the queue" width="900">

<br>

<sub><b>Command</b> — what breaches first, the city as a clay model, and the queue underneath it. Where the contract carries no deadline yet, the screen states the order it is actually in rather than deriving one that would look measured. <code>/console</code></sub>

</div>

<br>

<div align="center">

| <img src="assets/screens/22-staff-home.jpg" width="420" alt="The staff door — thirteen surfaces"><br><sub><b>Thirteen surfaces</b> — each tile says on its face whether it is wired. <code>/staff</code></sub> | <img src="assets/screens/24-console-review.jpg" width="420" alt="The review queue"><br><sub><b>The review queue</b> — the reports the pipeline would not decide alone.</sub> |
|:---:|:---:|
| <img src="assets/screens/25-console-review-item.jpg" width="420" alt="One review decision"><br><sub><b>One decision</b> — why the gate stopped, the redacted photograph, and the evidence behind it.</sub> | <img src="assets/screens/26-console-palette.jpg" width="420" alt="The command palette"><br><sub><b>The palette</b> — every screen one keystroke away. A keyboard surface first.</sub> |
| <img src="assets/screens/27-console-policy.jpg" width="420" alt="The policy studio"><br><sub><b>Policy studio</b> — rules as documents, with a backtest before they bite.</sub> | <img src="assets/screens/28-console-control.jpg" width="420" alt="The control plane"><br><sub><b>Control plane</b> — taxonomy, zones, departments, calendars, locales, tenants.</sub> |

</div>

<br>

<div align="center">

<img src="assets/screens/29-console-developers.jpg" alt="The console's developer portal — keys, webhooks, usage and the version clock" width="900">

<br>

<sub><b>Developer portal</b> — keys, webhooks, usage, and the version clock. <code>/console/developers</code></sub>

</div>

<br>

<div align="center"><sub>─────────────────  ○  ─────────────────</sub></div>

### Designed, not wired yet

> A console screen whose contract still returns nulls **is not routed to a public URL**. In a production build each of the seven below is a 404, and the rail does not render a link to it. They exist, they are designed against the published contract, and every field on them says which it is. The frames were taken from the development build for that reason — and this paragraph is the reason they are labelled rather than quietly shown.

<div align="center">

| <img src="assets/screens/30-console-area.jpg" width="280" alt="Area view"><br><sub><b>Area view</b> — one ward over time, including what it is not telling us.</sub> | <img src="assets/screens/31-console-work.jpg" width="280" alt="Work orders"><br><sub><b>Work orders</b> — assignment, the contractor picker, the rate card.</sub> | <img src="assets/screens/32-console-closure.jpg" width="280" alt="Closure"><br><sub><b>Closure</b> — evidence or nothing, with the conditions shown before they are hit.</sub> |
|:---:|:---:|:---:|
| <img src="assets/screens/33-console-money.jpg" width="280" alt="Money"><br><sub><b>Money</b> — allocated against spent, and what a citizen sees of it.</sub> | <img src="assets/screens/34-console-integrity.jpg" width="280" alt="Integrity"><br><sub><b>Integrity</b> — signals, case files, and what a blacklist has to meet.</sub> | <img src="assets/screens/35-console-reports.jpg" width="280" alt="Report builder"><br><sub><b>Report builder</b> — a document that carries its own proof.</sub> |
| <img src="assets/screens/36-console-roles.jpg" width="280" alt="Roles"><br><sub><b>Roles</b> — what each role sees, and what it may do.</sub> | | |

</div>

<br>

<div align="center"><sub>─────────────────  ○  ─────────────────</sub></div>

### Outdoors, and under the hood

<div align="center">

| <img src="assets/screens/37-field-app.jpg" width="230" alt="The field app on a phone"><br><sub><b>The crew's phone</b> — capture and close jobs outdoors and offline. Installs to a phone; built for sunlight and gloves. <code>/field</code></sub> | <img src="assets/screens/38-developers-portal.jpg" width="600" alt="The developer proof surfaces"><br><sub><b>The proof surfaces</b> — the dev-only routes each rendering pipeline is photographed through, and the contracts it holds. <code>/developers</code></sub> |
|:---:|:---:|

</div>

<br>

<sub><b>Reproducing these.</b> Start the stack and seed a city, build and serve the frontend, then run the capture — it writes both image sets and the manifest the deck reads.</sub>

```bash
nem up && nem seed-demo
cd frontend && npm run build && npx next start --port 3210 &
npm run dev -- --port 3211 &          # the roadmap screens are dev-only by design
node scripts/capture-product-shots.ts
cd .. && python scripts/build_screen_derivatives.py && python scripts/build_prototype_deck.py
```

<sub>The last line also rebuilds <a href="Nemesis_Prototype.pptx"><code>Nemesis_Prototype.pptx</code></a> — the same walkthrough as a 49-slide deck, generated from the same manifest.</sub>

<br>

<div align="center"><sub>─────────────────  ○  ─────────────────</sub></div>

## How a report moves

A citizen submits. The request is accepted synchronously and handed to an event-sourced pipeline — every stage **appends events** rather than mutating rows, so the log is the truth and every table is a projection of it.

```mermaid
flowchart LR
    A([Citizen<br/>submits]) --> B[ingest]
    B --> C[safety<br/>check]
    C --> D[trust<br/>verification]
    D --> E[classification]
    E --> F[dedup]
    F --> G[severity<br/>scoring]
    G --> H[routing<br/>· SLA]
    H --> I[investigation<br/>agent]
    I --> J([Public<br/>figures])

    classDef live fill:#EADCC7,stroke:#16130F,stroke-width:2px,color:#16130F
    classDef soon fill:#F4EDE1,stroke:#8A7462,stroke-width:1px,stroke-dasharray:4 3,color:#8A7462
    classDef edge fill:#925F52,stroke:#16130F,stroke-width:2px,color:#F4EDE1
    class B,C,D,E,F live
    class G,H,I soon
    class A,J edge
```

<sub>**Solid** = shipped and gated. **Dashed** = Phase 12 / 16, not built. The product says so on every screen that would otherwise show an empty number.</sub>

Three properties are load-bearing:

- **The safety fail-safe runs on its own queue**, served by a container that has never imported `torch`. A saturated ML queue cannot delay a gas-leak report. A trigger halts the pipeline outright.
- **Degradation is recorded, not swallowed.** Every external call has a timeout, a retry budget, a fallback, and a `pipeline_stage_degraded` event. `DEGRADED` is deliberately a different outcome from `FAILED` — correct degradation must not inflate the ratio that pages a human.
- **Deduplication is reversible.** A merge is undone by a compensating event, never by a delete.

<div align="center"><sub>─────────────────  ○  ─────────────────</sub></div>

## Architecture

```mermaid
flowchart TB
    subgraph SURF["&nbsp;Surfaces &nbsp;·&nbsp; Next.js 16 &nbsp;·&nbsp; React 19&nbsp;"]
        W["Landing film<br/>nine-act scroll"]
        R["Residents<br/>report · track"]
        S["Staff console<br/>13 screens"]
        F["Field app<br/>offline PWA"]
        P["Public portal<br/>open data"]
    end

    SEAM{{"server/upstream.ts<br/>the only network seam · holds the tenant"}}

    subgraph SVC["&nbsp;FastAPI&nbsp;"]
        PUB["public API<br/>k-anonymous"]
        CP["control plane<br/>tenants · taxonomy · policy"]
        ING["ingest<br/>+ transactional outbox"]
    end

    subgraph WK["&nbsp;Celery &nbsp;·&nbsp; split by memory profile&nbsp;"]
        IO["worker-io<br/>safety · trust · redaction"]
        ML["worker-ml<br/>CLIP · Whisper · embeddings"]
        RLY["relay<br/>outbox → WebSocket<br/>live to the console"]
    end

    subgraph DB["&nbsp;PostgreSQL 16 &nbsp;·&nbsp; one store, four jobs&nbsp;"]
        EV[("event log<br/>hash-chained · partitioned")]
        PJ[("projections")]
        GEO[("PostGIS<br/>+ pgvector")]
    end

    W --> SEAM
    R --> SEAM
    S --> SEAM
    F --> SEAM
    P --> SEAM

    SEAM --> PUB
    SEAM --> CP
    SEAM --> ING

    ING --> IO
    IO --> ML
    IO --> EV
    ML --> EV
    CP --> EV
    EV --> PJ
    EV --> RLY
    PUB --> PJ
    PUB --> GEO


    classDef surf fill:#EADCC7,stroke:#16130F,stroke-width:1.5px,color:#16130F
    classDef svc fill:#F4EDE1,stroke:#925F52,stroke-width:1.5px,color:#16130F
    classDef store fill:#925F52,stroke:#16130F,stroke-width:1.5px,color:#F4EDE1
    classDef seam fill:#0FA9A0,stroke:#16130F,stroke-width:2px,color:#16130F

    class W,R,S,F,P surf
    class PUB,CP,ING,IO,ML,RLY svc
    class EV,PJ,GEO store
    class SEAM seam

    style SURF fill:#F4EDE1,stroke:#8A7462,color:#8A7462
    style SVC fill:#F4EDE1,stroke:#8A7462,color:#8A7462
    style WK fill:#F4EDE1,stroke:#8A7462,color:#8A7462
    style DB fill:#F4EDE1,stroke:#8A7462,color:#8A7462
```

**One trust boundary, held on the server.** No browser ever names its own tenant — every read goes through a single seam that owns the typed client, the tenant header, and the middleware that turns a transport failure into a designed answer instead of a framework crash page.

**One Postgres.** Relational, geospatial (PostGIS), vector (pgvector), and the event log all live in one database, because a distributed transaction across four stores is a consistency bug waiting for a deadline.

<div align="center"><sub>─────────────────  ○  ─────────────────</sub></div>

## The stack

<table>
<tr><th align="left" width="16%">Layer</th><th align="left">Chosen</th><th align="left" width="42%">Why this one</th></tr>

<tr><td><b>API</b></td>
<td>FastAPI · Pydantic v2 · SQLAlchemy 2 (async) · asyncpg</td>
<td>Typed request/response all the way to the OpenAPI document the frontend generates against.</td></tr>

<tr><td><b>Data</b></td>
<td>PostgreSQL 16 · PostGIS · pgvector · Alembic</td>
<td>One store for four jobs. The event log is range-partitioned by month from the first migration.</td></tr>

<tr><td><b>Async</b></td>
<td>Celery · Redis · transactional outbox</td>
<td>Workers split <b>by memory profile</b>, not by domain — the ML container's footprint must never gate a safety check.</td></tr>

<tr><td><b>Perception</b></td>
<td>OpenCLIP · faster-whisper · sentence-transformers (multilingual-e5)</td>
<td>CPU-only inference against tenant-authored prompt sets. A new category is config, not a retrain.</td></tr>

<tr><td><b>Frontend</b></td>
<td>Next.js 16 · React 19 · TypeScript</td>
<td>Server components by default. The public doors work with JavaScript disabled.</td></tr>

<tr><td><b>3D</b></td>
<td>Three.js · React Three Fiber · drei · three-mesh-bvh · WebGPU</td>
<td>A generated clay city. Not a modelled asset — a kit assembled from real ground measurements in metres.</td></tr>

<tr><td><b>Maps</b></td>
<td>MapLibre GL · deck.gl</td>
<td>The 2D rung of a four-tier capability ladder that degrades to a printed storyboard.</td></tr>

<tr><td><b>Motion</b></td>
<td>Theatre.js · GSAP · Lenis</td>
<td>The film's camera is <b>authored as keys and generated</b> into a Theatre project, never hand-tweened.</td></tr>

<tr><td><b>State</b></td>
<td>Zustand · TanStack Query · openapi-fetch</td>
<td>The API client is generated from the live document. A contract drift is a compile error.</td></tr>

<tr><td><b>Observability</b></td>
<td>OpenTelemetry · Prometheus · Tempo · Grafana · Alertmanager</td>
<td>Opt-in compose profile. 28 runbooks — one per failure scenario, not one per service.</td></tr>

<tr><td><b>Gates</b></td>
<td>pytest · Vitest · Playwright · axe-core · Lighthouse CI · ruff · mypy --strict</td>
<td>Plus ten bespoke design guards and four Python standards checkers.</td></tr>
</table>

<div align="center"><sub>─────────────────  ○  ─────────────────</sub></div>

## The design system

This is the part most civic software skips. NEMESIS is built as **a printing press**, and that is a technical decision with teeth, not a mood board.

**Three materials, and nothing else exists.**

<table>
<tr>
<td align="center" width="33%"><b>CLAY</b><br><sub>the world</sub><br><br>The city is a generated model at real ground scale. A pin's height is severity — never decoration.</td>
<td align="center" width="33%"><b>INK</b><br><sub>people</sub><br><br>Four drawn figures, animated as a state machine on a 12 fps clock. No file format, no rigging.</td>
<td align="center" width="33%"><b>PAPER</b><br><sub>documents</sub><br><br>Everything a citizen can hold: receipts, ward pages, the honesty table.</td>
</tr>
</table>

<sub>A photograph would be a fourth material, so there are none. A stock image of a pothole that is not <i>this city's</i> pothole is a picture of evidence on the page where a resident is about to file some. Every illustration in this repository is drawn in the run's own inks.</sub>

**One press runs over all three.** Six stages — separation, halftone screen, misregistration, ink density, overprint, paper — implemented twice from one token source: a WebGPU shader for the 3D world, an SVG filter for the 2D surfaces.

**Two or three inks per run**, the real risograph constraint. Colour is never decorative: severity glazes are the only colours in the product that carry meaning.

<div align="center">

![paper-50](https://img.shields.io/badge/paper--50-F4EDE1?style=flat-square&labelColor=F4EDE1&color=F4EDE1)
![kraft-100](https://img.shields.io/badge/kraft--100-EADCC7?style=flat-square&labelColor=EADCC7&color=EADCC7)
![riso-black](https://img.shields.io/badge/riso--black-16130F?style=flat-square&labelColor=16130F&color=16130F)
![riso-brown](https://img.shields.io/badge/riso--brown-925F52?style=flat-square&labelColor=925F52&color=925F52)
![riso-sunflower](https://img.shields.io/badge/sunflower-FFB511?style=flat-square&labelColor=FFB511&color=FFB511)
![riso-aqua](https://img.shields.io/badge/aqua-0FA9A0?style=flat-square&labelColor=0FA9A0&color=0FA9A0)

<sub>severity — the only meaningful colour</sub><br>
![critical](https://img.shields.io/badge/critical-B03A2B?style=flat-square&labelColor=B03A2B&color=B03A2B)
![high](https://img.shields.io/badge/high-C97A1E?style=flat-square&labelColor=C97A1E&color=C97A1E)
![medium](https://img.shields.io/badge/medium-A98C1F?style=flat-square&labelColor=A98C1F&color=A98C1F)
![low](https://img.shields.io/badge/low-3E6E86?style=flat-square&labelColor=3E6E86&color=3E6E86)
![resolved](https://img.shields.io/badge/resolved-4E7D53?style=flat-square&labelColor=4E7D53&color=4E7D53)

</div>

**Every one of those values is generated.** `design/tokens.json` compiles to CSS custom properties and TypeScript, and a CI guard fails the build on a hand-written colour literal anywhere in application source. The severity ink in a badge and the glaze in a shader are the same number because both are written in one place.

<div align="center"><sub>─────────────────  ○  ─────────────────</sub></div>

## Quick start

```bash
nem doctor
```

```bash
nem up
```

Builds and starts the stack, blocking until every service is healthy. Then the frontend:

```bash
nem web
```

An empty deployment renders **honestly** — every ward reads *"not yet scored"* and *"none filed"*. To get a city with a week behind it:

```bash
nem seed-demo --reports 140
```

<sub><code>--reports</code> defaults to <code>0</code>, which is the commonest reason a fresh checkout looks broken. Everything the seeder creates goes over HTTP through the real handlers — no fixture loader, no <code>INSERT</code>. Reports get classified, clustered, redacted and scored by the same pipeline a real one goes through, which is why some come out flagged.</sub>

<details>
<summary><b>Every task <code>nem</code> knows</b></summary>

<br>

| | |
|---|---|
| `nem up` · `down` · `nuke` | Stack lifecycle (`nuke` destroys volumes) |
| `nem check` · `web-check` | Every quality gate CI runs, backend and frontend |
| `nem seed-demo` | Provision the demo city |
| `nem migrate` · `makemigration` · `rollback` | Alembic |
| `nem web-types` · `web-openapi` | Regenerate the typed client from the live document |
| `nem obs` · `obs-verify` | Observability stack, and prove metric → alert end to end |
| `nem gate-phase3…10` | Per-phase exit gates |
| `nem flag` · `control-plane` | Feature flags and tenant admin |
| `nem web-golden` · `web-lighthouse` | Visual regression and performance budgets |

</details>

<div align="center"><sub>─────────────────  ○  ─────────────────</sub></div>

## Repository

```
├── backend/            FastAPI · Celery · SQLAlchemy      179 modules · ~48k lines
│   └── nemesis/
│       ├── events/         append-only log, 34 registered types, hash-chained
│       ├── pipeline/       the stages, each one appending rather than mutating
│       ├── projections/    the ONLY module allowed to write current-state tables
│       ├── control_plane/  tenants · taxonomy · zones · calendars · locales
│       ├── policy/         rubrics and rules as versioned, effective-dated documents
│       ├── perception/     CLIP · Whisper · embeddings, with a model registry
│       └── public/         k-anonymous aggregates and open-data export
│
├── frontend/           Next.js 16 · React 19              215 modules · ~38k lines
│   └── src/
│       ├── design/         tokens.json → generated CSS + TS. The source of truth.
│       ├── press/          §E6 in two implementations, one token source
│       ├── clay/           the generated 3D city and its accessible peer list
│       ├── ink/            four drawn figures as a state machine
│       ├── story/          the nine-act landing film
│       ├── console/        thirteen operator screens
│       ├── citizen/        capture · track · the pipeline theatre
│       └── portal/         the two front doors
│
├── docs/
│   ├── adr/            61 architecture decision records
│   ├── runbooks/       28 — one per failure scenario
│   └── reports/        measured results, including the ones that failed
│
├── infra/              compose profiles, Grafana dashboards, alert rules
└── scripts/            standards checkers and phase gates
```

<div align="center"><sub>─────────────────  ○  ─────────────────</sub></div>

## The rules that are actually enforced

Not style preferences. Every one fails a build.

| | Rule |
|---|---|
| **1** | **State changes and events land in one transaction.** No row mutates without an event explaining it. One module writes projections; an AST walk enforces it. |
| **2** | **Event schemas are immutable once locked.** A fingerprint file covers every payload. Changing a released schema fails CI — register `v2` with an upcaster. |
| **3** | **Every query is tenant-scoped, visibly.** A checker reads the AST at each `select()`. A tenant predicate hidden behind a variable is one it cannot see, and it fails you. |
| **4** | **Configuration over code.** Anything a customer might want different is tenant data. A hardcoded category, role, ward or language tag fails the build. |
| **5** | **Deterministic ≠ hardcoded.** The safety fail-safe is a hot-reloadable governed ruleset that still executes as a hard rule: document order, first match wins, no regex. |
| **6** | **Every external call degrades on the record**, with `DEGRADED` kept distinct from `FAILED`. |
| **7** | **Prove, don't log.** Every phase has a machine-checkable exit gate. A skipped gate is technical debt with a false receipt. |
| **8** | **The honest number ships either way.** One phase published four categories below its own F1 floor. Another published a failing gate. |
| **9** | **Every non-obvious decision gets an ADR.** All 61 of them. |
| **10** | **No colour, duration, curve or type size is written by hand.** Ten design guards enforce it across the frontend. |

<div align="center"><sub>─────────────────  ○  ─────────────────</sub></div>

## Where this actually is

The product publishes its own answer to this question at `/{tenant}/honesty` and in Act 9 of the landing — generated from the same source, so it cannot drift into a flattering summary. **Trust those over this table.**

Thirty phases across nine tracks. Roughly a third are built and gated.

| Track | Phases | State |
|---|---|---|
| **0 · Operating system** | 0, 1a | ✅ gated · 1b deferred until a deploy target exists |
| **A · Platform** | 2, 3, 4 | ✅ event store, ingestion, public API |
| **B · Control plane** | 5, 6, 7 | ✅ tenants, policy engine, backtesting |
| **C · Intelligence** | 8, 9, 10 | ✅ safety, perception, dedup · 11–12 not started |
| **D · Accountability** | 13–17 | ❌ identity, work orders, closure, agent, contractor transparency |
| **E · Experience** | 18–22 | 🟡 design system, 3D engine, film, console and doors built ahead of schedule |
| **F–I · Data, trust, commercial, release** | 23–29 | ❌ not started |

**One open blocker worth naming.** Public ward figures are computed by matching complaints to zones. Routing — the stage that writes that label — is Phase 12 and does not exist, so the join falls back to the geometry the tenant already published, using the report's own coordinate. It is correct, indexed, and **retires itself** the day routing lands. Full detail in [`docs/CONNECTING-A-BACKEND.md`](docs/CONNECTING-A-BACKEND.md).

<div align="center"><sub>─────────────────  ○  ─────────────────</sub></div>

## Documentation

| | |
|---|---|
| [**HANDOVER.md**](docs/HANDOVER.md) | Where the system is, the rules that govern it, what is owed. **The entry point for backend work.** |
| [**CONNECTING-A-BACKEND.md**](docs/CONNECTING-A-BACKEND.md) | The four env variables, the one network seam, and the open blocker above. |
| [**PHASES.md**](docs/PHASES.md) | All thirty phases, their tracks, owners and dependencies. |
| [**BACKLOG.md**](docs/BACKLOG.md) | What remains, broken into startable items. |
| [**adr/**](docs/adr/) | 61 decision records. Read `0061` for how a run gets its exposure. |
| [**runbooks/**](docs/runbooks/) | 28 failure scenarios, each with a diagnosis path. |
| [**NEMESIS-Blueprint-v2.md**](NEMESIS-Blueprint-v2.md) | The product specification the phases implement. |
| [**NEMESIS-Frontend-Blueprint.md**](NEMESIS-Frontend-Blueprint.md) | §E — the design direction, materials and press. |

<br>

<div align="center">

<sub>─────────────────  ○  ─────────────────</sub>

<img src="assets/nemesis-mark-transparent.png" alt="" width="52">

**A civic system earns trust by producing evidence.**

<sub>Every figure on every page is computed from reports filed by residents.<br>Nothing is edited by hand.</sub>

</div>
