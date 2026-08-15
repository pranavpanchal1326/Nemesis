<div align="center">

<img src="nemesis-logo.png" alt="NEMESIS" width="380">

<br>

**Networked Enforcement & Municipal Evidence System for Infrastructure & Service accountability**

<sub>─────────────────  ○  ─────────────────</sub>

<br>

[![status](https://img.shields.io/badge/status-build--ready-141414?style=flat-square)](#31-mvp-cut-line)
[![architecture](https://img.shields.io/badge/architecture-event--sourced-141414?style=flat-square)](#architecture)
[![infra](https://img.shields.io/badge/infra-self--hosted%20%C2%B7%20zero--cost-141414?style=flat-square)](#tech-stack)
[![models](https://img.shields.io/badge/models-CLIP%20%C2%B7%20Whisper%20%C2%B7%20Ollama-141414?style=flat-square)](#tech-stack)
[![license](https://img.shields.io/badge/license-MIT-141414?style=flat-square)](#license)

</div>

<br>

> *Every other civic app in this category answers "did someone log the complaint." NEMESIS is the only one that answers "did the problem actually go away, who did it, what did it cost, and can you prove it."*

<br>

## The gap

A citizen photographs a pothole. The app says *"in progress."* Weeks later, nothing has changed — or it was patched badly and has already reappeared. There is no one to dispute it with, no visibility into who did the work or what it cost, and no reason to believe the next report will go any differently.

So the citizen stops reporting. Every civic-complaint app before this one has died to that exact mechanism, regardless of how good its intake pipeline was.

NEMESIS is built backward from that failure, not forward from *"wouldn't it be nice to classify complaints with AI."* It turns a photo, a voice note, or a line of text into a verified, deduplicated, severity-scored, department-routed work order — and then it **proves** the work order got resolved. Who fixed it. What it cost. Whether the same spot breaks again in ninety days. All of it, on the record.

```
citizen report → verification → deduplication → severity → routing →
execution → closure verification → public accountability
```

Every prior tool in this space stops somewhere in the middle of that chain. NEMESIS is the part that comes after.

<br>

<sub>─────────────────  ○  ─────────────────</sub>

## Contents

`01` [The Nemesis Standard](#the-nemesis-standard) · `02` [How it thinks](#how-it-thinks) · `03` [Architecture](#architecture) · `04` [Tech stack](#tech-stack) · `05` [The lifecycle of a complaint](#the-lifecycle-of-a-complaint) · `06` [Deduplication engine](#deduplication-engine) · `07` [Severity, transparently](#severity-transparently) · `08` [Contractor accountability](#contractor-accountability) · `09` [Trust & safety](#trust--safety) · `10` [What's real, right now](#whats-real-right-now) · `11` [Quickstart](#quickstart) · `12` [API surface](#api-surface) · `13` [Roadmap](#roadmap) · `14` [License](#license)

<br>

## The Nemesis Standard

In Greek myth, Nemesis is retribution against those who believe themselves beyond consequence. That is the exact failure this system targets — infrastructure spending that evades consequence because nothing closes the loop between *"money was spent"* and *"the problem was actually fixed."*

Internally, one sentence governs every design decision in this repository:

<div align="center">

**No claim without an evidence trail.**

</div>

Externally, the product never reads as punitive. It's procedural, evidence-based, and fair to every party it touches — contractors included, with a real dispute channel of their own. The name carries the mission; the interface carries none of the edge.

<br>

## How it thinks

Three commitments shape every line of this codebase more than any single technology choice does:

| Commitment | What it means in practice |
|---|---|
| **Deterministic where it matters** | Safety triggers are hardcoded rules, not model scores. A false negative on *"gas leak"* is not an acceptable trade for elegance. |
| **Append-only, always** | Nothing is silently edited. Corrections are new events layered on top of history — including corrections made by a super-admin. |
| **Honest about what's built** | Nothing in this document is described more strongly than what actually runs. See [§10](#whats-real-right-now) — it's the whole reason this README has that section at all. |

<br>

<sub>─────────────────  ○  ─────────────────</sub>

## Architecture

An event-driven system, not a CRUD app. A complaint is a sequence of immutable events moving through a state machine — mostly deterministic rules, with one genuinely agentic component reserved for the cases that actually need judgment.

```
                            ┌──────────────────────────┐
                            │      Citizen Web App       │
                            │   browser-first · offline  │
                            │        capable UI          │
                            └──────────────┬─────────────┘
                                           │  HTTPS / WebSocket
                                           ▼
                            ┌──────────────────────────┐
                            │       FastAPI Gateway       │
                            │  ingest · auth · role-scoped │
                            │       queries · WS hub       │
                            └──────────────┬─────────────┘
                                           │
                    ┌──────────────────────┼──────────────────────┐
                    ▼                      ▼                      ▼
            ┌───────────────┐    ┌────────────────┐    ┌────────────────┐
            │  Redis          │    │  Celery workers  │    │  Ollama          │
            │  queue · rate-  │    │  async pipeline  │    │  Qwen2.5 / Llama │
            │  limit · pub/sub│    │  stages          │    │  — local, offline │
            └───────────────┘    └────────┬────────┘    └────────┬────────┘
                                           │                      │
                                           ▼                      ▼
                            ┌──────────────────────────────────────────┐
                            │        NEMESIS Core Pipeline                │
                            │  Intake → Fraud check → Classify → Dedup →  │
                            │  Severity → Route                            │
                            │  (Investigation Agent invoked only on         │
                            │   ambiguous cases — see §2)                   │
                            └──────────────────┬───────────────────────────┘
                                               ▼
                            ┌──────────────────────────────────────────┐
                            │             PostgreSQL                      │
                            │  + PostGIS   — geospatial clustering         │
                            │  + pgvector  — embedding similarity          │
                            │  + events    — hash-chained, append-only     │
                            └──────────────────┬───────────────────────────┘
                                               ▼
                            ┌──────────────────────────────────────────┐
                            │      Neo4j — contractor entity graph        │
                            │      shared address / director / phone      │
                            │      across nominally distinct vendors      │
                            └──────────────────────────────────────────┘
                                               │
                    ┌──────────────────────────┼──────────────────────┐
                    ▼                          ▼                      ▼
           ┌────────────────┐        ┌────────────────┐     ┌────────────────┐
           │  Public UI       │        │  Department UI   │     │  Admin UI        │
           │  map · contractor│        │  role-scoped      │     │  full audit log, │
           │  profiles · spend│        │  kanban            │     │  cross-ward view │
           └────────────────┘        └────────────────┘     └────────────────┘
```

**Why one Postgres, not five services.** `pgvector` with HNSW indexing matches dedicated vector databases well under a million vectors — one backup strategy, one connection pool, one thing that can fail.

**Why one agent, not four.** Three of the four originally-planned "agents" — Intake, Classification, Ops — are, told honestly, deterministic pipelines: schema checks, a forward pass through CLIP, a scheduled deadline comparison. Calling those *agents* doesn't survive the question *"walk me through what it decided."* One component genuinely decides its own next step based on what the last tool call returned — the **Investigation Agent** — and that's the only place NEMESIS uses the word.

<br>

## Tech stack

<table>
<tr><td valign="top" width="50%">

**Frontend**
- Next.js 15 (App Router) + TypeScript
- Tailwind CSS + shadcn/ui
- Leaflet.js + OpenStreetMap — no API key, no rate-limit surprise
- React Three Fiber + Drei — one scene, the cluster-merge moment
- Zustand — WebSocket events → store → shader uniforms
- Motion (Framer Motion) — UI-level transitions

**Backend**
- FastAPI (async) + Pydantic v2
- Celery + Redis — async pipeline, rate limiting
- WebSockets — live pipeline events
- FastAPI-Users + JWT — role-claim auth

</td><td valign="top" width="50%">

**Data**
- PostgreSQL 16 · PostGIS · pgvector
- Append-only `events` table, hash-chained on write
- Neo4j — contractor entity-resolution graph

**AI / ML — all self-hosted, zero API cost**
- CLIP zero-shot — unified classification, one model, five categories
- `faster-whisper` — Hindi / Marathi transcription
- `sentence-transformers` (MiniLM-L6-v2) — text embeddings
- Ollama (Qwen2.5 / Llama 3.1) — Investigation Agent reasoning
- LangGraph — single-node agent orchestration
- scikit-image (SSIM) — before/after verification

</td></tr>
</table>

Runs entirely offline, on a single laptop, via `docker compose up`. No paid API, no rate-limit risk mid-demo, and a credible, rehearsed answer to every data-sovereignty question a reviewer asks.

<br>

<sub>─────────────────  ○  ─────────────────</sub>

## The lifecycle of a complaint

```
 1  SUBMIT               photo · voice · text · GPS, from the browser
       │
 2  VERIFY (parallel)    EXIF/GPS cross-match · perceptual hash · safety scan · velocity check
       │                       │
       │                       └── danger signal ──▶ skip every queue, human-reviewed in minutes
       ▼
 3  CLASSIFY              CLIP zero-shot across all categories, logged as a versioned event
       ▼
 4  DEDUPLICATE           PostGIS radius filter → pgvector cosine similarity
       │                       │
       │                       └── ambiguous band ──▶ Investigation Agent gathers more evidence
       ▼
 5  SCORE SEVERITY         transparent weighted rubric, every component logged
       ▼
 6  ROUTE                  tenant-specific department config, SLA timer starts
       ▼
 7  ASSIGN & EXECUTE        staff or contractor · milestone photo evidence
       ▼
 8  VERIFY CLOSURE           SSIM diff on before/after photos — real change required
       ▼
 9  CITIZEN CONFIRMS          "was this actually fixed?" — confirmed / disputed / auto-noted
       ▼
10  PUBLIC RECORD              contractor profile, ward budget page, dashboard — same event log
```

Nothing here is a status flag pretending to be proof. Every arrow above is a logged, replayable event.

<br>

## Deduplication engine

The single most technically defensible piece of this system, and the one component that never gets simplified under time pressure.

**Stage 1 — cheap geo filter.** Eliminate ~90% of non-candidates before touching an embedding:

```sql
SELECT * FROM complaints
WHERE ST_DWithin(location, :new_complaint_location, 50)   -- 50 metres
  AND reported_at > NOW() - INTERVAL '72 hours';
```

**Stage 2 — semantic filter**, on whatever survives Stage 1:

```sql
SELECT id,
       1 - (image_embedding <=> :new_image_embedding) AS visual_sim,
       1 - (text_embedding  <=> :new_text_embedding)  AS text_sim
FROM complaints
WHERE id = ANY(:stage1_candidate_ids)
ORDER BY GREATEST(visual_sim, text_sim) DESC;
```

Above ~0.85 similarity → merge into a confidence-weighted cluster. Below it but still geo-close → held for human review or handed to the Investigation Agent, never silently auto-merged. Two citizens photographing the same pothole from different angles, in different light, hours apart, is a real computer-vision problem — and it's the line item that justifies everything else in this repository being taken seriously.

<br>

## Severity, transparently

```
severity =  0.40 · visual_damage_score      (CLIP confidence × damage-type weight)
         +  0.25 · road_class_weight        (OSM highway tag: primary > residential > footway)
         +  0.20 · poi_proximity_score      (decays to 0 at 200m from a school/hospital)
         +  0.15 · cluster_report_count     (more independent reports = higher confidence)
```

Every component is logged per complaint. There is no labeled severity training set to pretend otherwise on — this is a rules engine with computer vision as one input, stated exactly that plainly, versioned (`severity_rubric_v1`, `v2`, …), and calibrated against real resolution-time outcomes as they accumulate.

<br>

<sub>─────────────────  ○  ─────────────────</sub>

## Contractor accountability

The layer that makes NEMESIS more than a routing tool — and reuses entity-resolution and anomaly-detection techniques from the same problem family as financial fraud-graph work, pointed at public-works vendor networks instead.

- **Public contractor profiles** — % jobs closed within SLA, % citizen-confirmed vs. disputed, cost variance, repeat-defect rate. Never a single collapsed star rating.
- **Entity-resolution graph** — surfaces when four nominally distinct contractors share an address, a director, or a phone number, and have collectively won most of a ward's contracts.
- **Milestone-based evidence & release** — 30% on work-start, 40% on mid-progress, 30% on citizen-confirmed closure, each gated on an actual photo event, not a self-reported checkbox.
- **Fairness, built in, not bolted on** — contractors get a real dispute channel, seasonal SLA normalization for monsoon vs. dry-season repair, and every anomaly is shown with an explicit *"system-flagged, unverified, under review"* disclaimer until a human confirms it. Nothing reaches a public profile as settled fact.

<br>

## Trust & safety

| Threat | Mitigation |
|---|---|
| Faked location on a photo | EXIF/GPS cross-check · live-capture-only fallback when metadata is stripped |
| Reused or screenshotted images | Perceptual hashing — survives compression and resize |
| Coordinated brigading | Submission velocity + device-fingerprint clustering, routed to human review, never auto-blocked |
| Genuine hazard (gas leak, live wire, structural collapse) | Hardcoded keyword + visual trigger, **bypasses every queue** — deterministic on purpose, because a false negative here is not an acceptable trade |
| Retroactive record tampering | Every write is hash-chained; even a super-admin can only append a correcting event, never edit history |
| Faces of bystanders and minors in uploaded photos | Blurred before storage, before anything is persisted |

<br>

## What's real, right now

This is the part most projects put in fine print. It's the point of this one.

| Component | Status |
|---|---|
| Two-stage PostGIS + pgvector deduplication | `REAL` |
| Severity rubric with logged component breakdown | `REAL` |
| Safety fail-safe (deterministic bypass) | `REAL` |
| Hash-chained event writes | `REAL` — background integrity sweep is `ROADMAP` |
| CLIP zero-shot classification, all categories | `REAL` — with a published held-out accuracy number |
| Investigation Agent (genuine multi-step tool use) | `REAL` |
| Cluster-merge visualization | `REAL` — shader primary path, CSS/DOM fallback shipped in parallel |
| Before/after SSIM closure verification | `REAL` |
| App-level RBAC | `REAL` — Postgres RLS is `ROADMAP`, migration path documented |
| Contractor entity-resolution graph (Neo4j) | `ROADMAP` — designed and diagrammed, not queried live |
| Milestone-based fund release | `SIMULATED` — real data model, simulated disbursement |
| Public read-only transparency API | `ROADMAP` — schema described |

No claim in a pitch, a demo, or this file outranks the truth of what's actually running. That discipline is the product, as much as any single feature is.

<br>

<sub>─────────────────  ○  ─────────────────</sub>

## Quickstart

```bash
git clone https://github.com/<your-org>/nemesis.git
cd nemesis

# pulls all model weights (CLIP, Whisper, Ollama) into local volumes —
# after the first run, this needs no internet connection at all
docker compose up --build

# seeds ~300–500 synthetic complaints with realistic category ratios
docker compose exec api python scripts/seed_demo_data.py
```

The stack is reachable at `localhost:3000` (citizen/public), `localhost:3000/department`, and `localhost:3000/admin`. No API keys required, anywhere.

<br>

## API surface

```http
POST /api/v1/complaints
Content-Type: multipart/form-data

photo, audio, description_text, latitude, longitude, device_fingerprint

→ 202 Accepted
{
  "complaint_id": "uuid",
  "status": "submitted",
  "estimated_processing_time_seconds": 8
}
```

```http
GET /api/v1/complaints/{complaint_id}

→ 200 OK
{
  "status": "clustered",
  "category": "pothole",
  "classification_confidence": 0.91,
  "severity_score": 0.74,
  "severity_breakdown": {
    "visual_damage_score": 0.8,
    "road_class_weight": 0.6,
    "poi_proximity_score": 0.3,
    "cluster_report_count": 0.4
  }
}
```

```text
wss://host/ws/pipeline-events?tenant_id={tenant_id}

{
  "event_type": "cluster_match_found",
  "payload": {
    "merged_complaint_ids": ["uuid-1", "uuid-2"],
    "cluster_centroid": { "lat": 18.5204, "lng": 73.8567 },
    "new_confidence": 0.91,
    "new_severity": 0.78
  }
}
```

Full contract, including the public transparency endpoints, lives in `/docs/api-reference.md`.

<br>

## Roadmap

```
Days  1–30   harden what's real     hash-chain verification sweep · RLS on highest-risk tables
Days 31–60   pilot, one real tenant  real milestone-based fund release · contractor dispute workflow live
Days 61–90   second persona          Neo4j graph goes live · municipal (B2G) narrative from real pilot data
```

The full 90-day plan, KPI definitions, and unit economics live in `/docs/roadmap.md`.

<br>

<sub>─────────────────  ○  ─────────────────</sub>

## License

MIT — see [`LICENSE`](./LICENSE).

<br>

<div align="center">

<sub>Every claim, checked before it's said out loud to a citizen, a contractor, or a judge.</sub>

<br><br>

**NEMESIS**
<br>
<sub>THE SYSTEM THAT REMEMBERS</sub>

</div>
