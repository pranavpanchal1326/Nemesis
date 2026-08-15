# NEMESIS — AI Civic Operations Agent
## The Complete Product & Engineering Blueprint

**Codename:** NEMESIS *(Networked Enforcement & Municipal Evidence System for Infrastructure & Service accountability)*
**Version:** 2.0 — Senior Review Edition
**Status:** Build-ready. Every claim in this document is labeled REAL, SIMULATED, or ROADMAP. Nothing is ambiguous.
**Prepared for:** Hackathon build, pilot deployment, and long-term product reference
**Supersedes:** v1.0 blueprint. This version closes every gap identified in the v1.0 review pass — see Section 34, Change Log.

---

> *"Every other civic app in this category answers 'did someone log the complaint.' NEMESIS is the only one that answers 'did the problem actually go away, who did it, what did it cost, and can you prove it.'"*

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Why NEMESIS — The Name and the Mandate](#2-why-nemesis--the-name-and-the-mandate)
3. [Problem Statement](#3-problem-statement)
4. [Why This, Why Now — Positioning](#4-why-this-why-now--positioning)
5. [Market & Buyer Personas](#5-market--buyer-personas)
6. [Product Philosophy & Design Principles](#6-product-philosophy--design-principles)
7. [System Architecture Overview](#7-system-architecture-overview)
8. [Complete Tech Stack](#8-complete-tech-stack)
9. [Data Model & Event Sourcing](#9-data-model--event-sourcing)
10. [Complaint Lifecycle — End to End](#10-complaint-lifecycle--end-to-end)
11. [Trust & Safety Layer](#11-trust--safety-layer)
12. [Multi-Agent Architecture — The Honest Version](#12-multi-agent-architecture--the-honest-version)
13. [Severity Scoring System](#13-severity-scoring-system)
14. [Deduplication & Clustering Engine — The Core Moat](#14-deduplication--clustering-engine--the-core-moat)
15. [Department-Side Workflow](#15-department-side-workflow)
16. [Contractor Transparency & Accountability Module](#16-contractor-transparency--accountability-module)
17. [Fraud & Corruption Detection Layer](#17-fraud--corruption-detection-layer)
18. [RBAC & Access Model — What's Real vs Described](#18-rbac--access-model--whats-real-vs-described)
19. [Frontend, Design Language & 3D/Shader Layer](#19-frontend-design-language--3dshader-layer)
20. [The Cluster-Merge Wow-Moment — Primary Path + Fallback](#20-the-cluster-merge-wow-moment--primary-path--fallback)
21. [Notification & Closure Loop](#21-notification--closure-loop)
22. [Privacy, Compliance & Legal Risk](#22-privacy-compliance--legal-risk)
23. [Equity & Bias Safeguards](#23-equity--bias-safeguards)
24. [Observability, Testing & Resilience](#24-observability-testing--resilience)
25. [Security Threat Model](#25-security-threat-model)
26. [API Contract Reference](#26-api-contract-reference)
27. [SLAs, Error Budgets & Operational Runbook](#27-slas-error-budgets--operational-runbook)
28. [Unit Economics & Monetization](#28-unit-economics--monetization)
29. [Competitive Landscape](#29-competitive-landscape)
30. [Hour-Budgeted Build Plan](#30-hour-budgeted-build-plan)
31. [MVP Cut Line — What Ships vs What's a Slide](#31-mvp-cut-line--what-ships-vs-whats-a-slide)
32. [CLIP Validation Protocol](#32-clip-validation-protocol)
33. [Demo Script & Judging Q&A Prep](#33-demo-script--judging-qa-prep)
34. [Risk Register](#34-risk-register)
35. [Change Log — v1.0 to v2.0](#35-change-log--v10-to-v20)
36. [Full Problem-to-Fix Traceability Index](#36-full-problem-to-fix-traceability-index)
37. [Team Execution Plan & RACI](#37-team-execution-plan--raci)
38. [Hackathon-Day Logistics](#38-hackathon-day-logistics)
39. [Pitch Deck Outline](#39-pitch-deck-outline)
40. [Post-Hackathon Roadmap — 90-Day Plan](#40-post-hackathon-roadmap--90-day-plan)
41. [KPI Dashboard Specification](#41-kpi-dashboard-specification)
42. [Appendix A — Glossary](#42-appendix-a--glossary)
43. [Appendix B — Reference Prompts & Rubric Config](#43-appendix-b--reference-prompts--rubric-config)
44. [Appendix C — Real vs Simulated vs Roadmap Master Table](#44-appendix-c--real-vs-simulated-vs-roadmap-master-table)

---

## 1. Executive Summary

**NEMESIS** turns messy, unstructured citizen reports (photo, voice, text) about infrastructure problems — potholes, garbage overflow, broken streetlights, water leakage, illegal dumping, damaged public infrastructure — into **verified, deduplicated, severity-scored, department-routed work orders**, and then **proves those work orders actually got resolved**, closing a loop that almost every prior civic-tech project leaves open.

It goes one step further than complaint routing: it adds a **contractor transparency and accountability layer** — public, auditable records of who did the work, how much it cost, whether it was actually fixed, and whether the same problem keeps recurring. This layer borrows entity-resolution and anomaly-detection techniques directly from fraud-graph work — the same problem class as mule-account detection in financial fraud, applied here to public-works vendor networks.

The system is built to run **entirely offline, on a single laptop, at zero external cost**, using only open-source models and self-hosted infrastructure — no paid APIs, no rate-limit risk during a live demo, and a credible, rehearsed answer to data-sovereignty and DPDP-compliance questions.

**The one-sentence pitch:**
> A citizen's photo becomes a verified, deduplicated, severity-scored, department-routed work order in seconds — and unlike every app before it, NEMESIS doesn't just log the complaint, it proves it got fixed, and it makes the money and the people who spent it fully visible.

**What makes v2.0 different from a typical hackathon writeup:** this document does not let a single claim exist without stating, explicitly, whether it is REAL (built and demoable), SIMULATED (a labeled placeholder standing in for a future real component), or ROADMAP (described, not built). A senior reviewer's first question is always "what did you actually build versus describe" — this blueprint answers that question before it's asked, on every section, not just in a single summary slide. See Section 44 for the master table.

---

## 2. Why NEMESIS — The Name and the Mandate

The name is not decorative. It encodes the product's actual thesis.

In Greek mythology, Nemesis is the goddess of retribution against those who evade consequence through hubris — specifically, against those who believe they are beyond being held accountable. That is precisely the failure mode this product targets: civic infrastructure spending that historically evades consequence because no system closes the loop between "money was spent" and "the problem was actually fixed." Contractors who under-deliver, departments that mark things closed without verification, and complaint pipelines that quietly die after the "routed" stage — all of these currently evade consequence because no proof mechanism exists. NEMESIS is the proof mechanism.

**Brand mandate for anyone building on this blueprint:**
- NEMESIS is never framed as a punitive system in citizen- or contractor-facing copy. The name carries the *mission* internally; the product experience externally is procedural, evidence-based, and fair (see Section 16.4, fairness mechanisms). A system that reads as vindictive in its UI language invites the exact political resistance described in Section 34.
- Internally, "the Nemesis standard" is shorthand for the core discipline running through this entire document: **no claim without an evidence trail.** Every team member should be able to answer "what's the Nemesis standard on this feature?" and mean "is there a logged, checkable proof behind this, or is it just a status flag?"

---

## 3. Problem Statement

Municipalities, campuses, industrial parks, and gated communities receive large volumes of complaints across a narrow set of recurring categories:

- Potholes / road damage
- Garbage / waste overflow
- Broken streetlights
- Water leakage
- Illegal dumping
- Damaged public infrastructure (railings, signage, drainage)
- Traffic-related hazards

The hard part was never *detecting* these issues. Citizens already report them constantly, through WhatsApp numbers, helplines, physical registers, or existing apps. The hard part is the **operational pipeline** that turns a raw report into an actioned, verified outcome:

```
citizen complaint → classification → verification → deduplication →
severity → department routing → work order → execution → closure verification → public accountability
```

Every existing civic-complaint tool stops somewhere in the middle of this chain — usually at "routed to department." Almost none of them close the loop, verify the fix, or make the spending and the vendor's track record visible. That gap is where citizen trust collapses, and it's the specific gap NEMESIS is built to close.

### 3.1 The trust-collapse mechanism, precisely

This deserves precision because it is the entire justification for the product's second half (contractor transparency), which is more expensive to build than complaint routing and therefore needs the strongest possible justification:

1. Citizen reports a problem.
2. App shows status change to "In Progress" or "Resolved" — a self-reported status by whoever is supposed to fix it, with no independent verification.
3. Citizen visits the location. Problem is unchanged, or was patched poorly and recurs within weeks.
4. Citizen has no mechanism to dispute the status, no visibility into who did the work or what it cost, and no reason to believe the next report will behave differently.
5. Citizen stops reporting. App's usage metrics decay. Department loses its primary low-cost signal channel. Everyone reverts to the status quo — ad hoc complaints via phone calls, social media pressure, or local political intermediaries.

This cycle has killed nearly every prior civic-complaint app at scale, regardless of how good the initial classification/routing technology was. NEMESIS is designed backward from this failure mode, not forward from "wouldn't it be nice to log complaints with AI."

---

## 4. Why This, Why Now — Positioning

### 4.1 What NOT to build
Do **not** build "an AI complaint chatbot." That category is saturated — civic/pothole/smart-city reporting apps are among the most common hackathon and Smart India Hackathon (SIH) submission categories, and judges have seen dozens of near-identical versions. Leading with "AI classifies your complaint" as the headline pitch is a guaranteed way to be grouped with everything else in the room.

### 4.2 What TO build
Build **"AI that turns messy citizen reports into executable municipal work orders — and proves they were completed, transparently."** The differentiation is not the AI. The differentiation is the loop closure and the accountability layer built on top of it.

### 4.3 The actual moat
A moat here is not a clever feature — it's a **data asset**. Every complaint processed builds a proprietary dataset of `(location, defect-type, severity, resolution-time, contractor, cost)` tuples specific to a tenant's geography. After sustained operation, this enables:
- **Predictive maintenance** — predicting where the next defect will form, before it's reported, using road-age proxies + traffic load + rainfall data
- **Vendor risk scoring** that gets more accurate over time and cannot be replicated by a competitor who launches on day one with no history
- **A defensible reason competitors can't just clone the UI over a weekend** — the UI is not the asset; the accumulated, verified, tenant-specific operational history is

### 4.4 The single differentiating sentence to say out loud in any pitch
> "Existing civic apps log complaints. NEMESIS is the only one that clusters duplicate reports into confidence-weighted incidents, applies a safety fail-safe that bypasses queues for danger-flagged reports, closes the loop with photo-verified resolution, and makes the contractor and the budget behind every fix fully auditable — which is the specific combination that causes citizen trust to collapse in every prior civic app."

### 4.5 Why now, specifically
- Open-source CV/NLP models (CLIP, YOLO, Whisper) have crossed the quality threshold where a self-hosted, zero-API-cost pipeline is credible for production use, not just a toy demo — this was not true three years ago.
- DPDP Act enforcement in India is creating real institutional pressure toward auditable, India-hosted, consent-aware civic data systems — a compliance posture that is now a sales asset, not just a legal checkbox.
- Public frustration with "complaint logged, nothing happened" civic apps has reached the point where a loop-closing alternative is a legible, sellable differentiator rather than a nice-to-have.

---

## 5. Market & Buyer Personas

| Persona | Org structure | Sales cycle | Adoption risk | Notes |
|---|---|---|---|---|
| **Campus (primary MVP target)** | Facilities/maintenance team, single decision-maker | Fast (2–4 weeks) | Low | Best first wedge — simple org structure, easy access, low political sensitivity |
| **Industrial park** | Facilities/security/ops | Fast–Medium (4–8 weeks) | Low | Similar to campus; "contractor" = maintenance vendor, not politically connected |
| **Gated community** | RWA / management committee | Fast (2–4 weeks) | Low | Small scale, good reference customer, high word-of-mouth value within RWA networks |
| **Municipality (long-term TAM)** | Multiple departments (PWD, Sanitation, Water, Electrical), ward officers, elected officials | Slow (6–18 months) | High | Real TAM but real procurement + political risk (see Section 34) |
| **Civil society / journalists / RTI applicants** | N/A — consumers of the public API | N/A | N/A | Secondary but valuable persona once public transparency data exists; drives adoption pressure on municipal buyers indirectly |

**GTM stance to state explicitly in any pitch:**
> "B2G is the long-term TAM, B2B campus/industrial is the go-to-market wedge. The complaint-routing side of the product sells fast everywhere; the contractor-transparency side sells fast to campuses and slowly to municipalities, because it threatens existing procurement relationships — that's expected, and it's still the right feature to build first."

### 5.1 Persona-specific value proposition summary

| Persona | Primary pain solved | Primary objection to pre-empt |
|---|---|---|
| Campus facilities head | No visibility into what maintenance actually got done vs. logged | "We already have a ticketing system" → NEMESIS is not a ticketing system, it's a proof-of-completion system; positioning must draw this line explicitly in the first two minutes of any sales conversation |
| Municipal ward officer | Constituent pressure with no defensible data to respond to | "This will expose underperformance publicly" → positive-framed default metrics (Section 23.3), dispute/appeal workflow |
| Journalist / RTI applicant | Fund-tracing across schemes (AMRUT, Smart Cities Mission) is currently manual and slow | N/A — this persona has no purchase authority, treat as an adoption-pressure lever not a sales target |

---

## 6. Product Philosophy & Design Principles

1. **Prove, don't log.** Every claim in the system (fixed, spent, verified) needs an evidence trail, not a status flag. This is the Nemesis standard (Section 2) and it is the single sentence that should resolve every subsequent design ambiguity in this document.
2. **Deterministic where it matters, probabilistic where it helps.** Safety fail-safes are hardcoded rules, not model scores. Severity and classification can be probabilistic, but every score is logged and explainable.
3. **Append-only, always.** Nothing is ever silently edited — corrections are new events, not overwritten fields. This applies to citizen data, department actions, and even super-admin actions.
4. **Minimum complexity that proves the thesis.** Every additional layer of architecture must map to a named problem it solves — no complexity for its own sake (see Section 36 traceability index).
5. **Fair to both sides.** A transparency system that only ever penalizes invites sabotage and political resistance. Contractors get a dispute/appeal channel. Officials get positive-framed metrics (response rate) as the default view, not just exposure metrics.
6. **Zero-cost, self-hosted, offline-capable.** No paid APIs. Must run fully on a single laptop via Docker Compose, with no dependency on venue WiFi during a demo.
7. **Equity-aware by design.** Reporting volume correlates with smartphone access and civic engagement, not with actual infrastructure damage — the system must actively surface underreported areas, not just react to what's reported.
8. **Honest about what's built.** No component in the demo or the pitch deck is described in language stronger than what was actually implemented. This is a competitive advantage, not a limitation — see Section 31 and Section 44. A system that survives hard technical questioning by simply telling the truth outperforms a system that oversells and gets caught.
9. **Every 3D/visual element must map to a real pipeline event.** Decoration for its own sake is explicitly against the product philosophy — see Section 19.1 and Section 20.

---

## 7. System Architecture Overview

### 7.1 High-level shape
NEMESIS is an **event-driven system**, not a CRUD app. A complaint is a sequence of immutable events flowing through a state machine, orchestrated partly by deterministic rules and partly by a single, genuinely agentic LLM component for ambiguous cases (see Section 12 for why this is now singular, not four agents).

### 7.2 Architecture diagram (textual)

```
                         ┌───────────────────────────┐
                         │   Citizen Web App          │
                         │   (browser client, laptop- │
                         │   first, mobile-browser OK)│
                         └─────────────┬─────────────┘
                                       │ HTTPS / WebSocket
                                       ▼
                         ┌───────────────────────────┐
                         │        FastAPI Gateway     │
                         │  (ingestion, auth, role-   │
                         │   scoped queries, WS hub)  │
                         └─────────────┬─────────────┘
                                       │
                     ┌─────────────────┼─────────────────┐
                     ▼                 ▼                 ▼
             ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
             │ Redis (queue,│  │ Celery workers│  │  Ollama (local│
             │ rate-limit,  │  │ (async tasks) │  │  LLM: Qwen2.5/│
             │ pub/sub)     │  │               │  │  Llama 3.1)   │
             └──────────────┘  └──────┬───────┘  └──────┬───────┘
                                       │                 │
                                       ▼                 ▼
                         ┌───────────────────────────────────────┐
                         │   NEMESIS Core Pipeline (deterministic) │
                         │  Intake → Fraud Check → Classification  │
                         │  → Dedup → Severity → Routing            │
                         │  (single Investigation Agent invoked     │
                         │   only on ambiguous cases — see Sec 12)  │
                         └─────────────────┬───────────────────────┘
                                           ▼
                         ┌───────────────────────────────────────┐
                         │   PostgreSQL (single instance)          │
                         │   + PostGIS   (geospatial clustering)   │
                         │   + pgvector  (embedding similarity)    │
                         │   + events table (hash-chained writes,  │
                         │     append-only — verification job is   │
                         │     ROADMAP, see Section 17.4)          │
                         └─────────────────┬───────────────────────┘
                                           ▼
                         ┌───────────────────────────────────────┐
                         │   Neo4j (contractor entity-resolution   │
                         │   graph — DESCRIBED + diagrammed for the │
                         │   demo, not queried live; see Sec 17.1) │
                         └───────────────────────────────────────┘
                                           │
                     ┌─────────────────────┼─────────────────────┐
                     ▼                     ▼                     ▼
           ┌────────────────┐   ┌────────────────┐   ┌────────────────┐
           │  Public Web UI  │   │ Department UI   │   │  Admin UI       │
           │  (map, contractor│  │ (app-level RBAC │   │  (audit log,    │
           │  profiles, budget│  │  kanban)        │   │  cross-ward view)│
           │  transparency)   │  │                 │   │                 │
           └────────────────┘   └────────────────┘   └────────────────┘
```

### 7.3 Core architectural decisions and why

| Decision | Reasoning | Status |
|---|---|---|
| Single Postgres instance for relational + geospatial + vector + event log | One backup strategy, one connection pool, one thing that can fail. `pgvector` with HNSW indexing matches or beats dedicated vector DBs at hackathon/early-product scale (well under 1M vectors) | REAL |
| Event sourcing from day one | Retrofitting later is painful; this is the one piece worth over-engineering upfront. Also directly answers "how do we know this data wasn't tampered with" | REAL (writes); verification job is ROADMAP |
| Single Investigation Agent, not a four-agent LangGraph | One genuinely agentic, multi-step, tool-using component beats four thin, mostly-deterministic "agents" that don't survive a "walk me through what it decided" question. See Section 12. | REAL |
| Local LLM (Ollama) over hosted API | Zero cost, zero live rate-limit risk during judging, works fully offline | REAL |
| CLIP zero-shot over fine-tuned YOLO | Fine-tuning on RDD2022 is the single largest time sink in the original plan for the smallest demo-perceivable payoff. Zero-shot, *validated with a real held-out accuracy number* (Section 32), is the correct engineering trade at this time budget. | REAL (with published accuracy number) |
| Neo4j contractor graph | Entity resolution (shared address/director/phone across "different" contractor entities) is a graph-native problem — but building and live-querying a graph DB for a 90-second demo has near-zero perceivable payoff versus a clear diagram + worked example. Described and diagrammed, not built live. | ROADMAP (design complete, not implemented) |
| App-level RBAC over Postgres RLS | Real RLS policies for a demo nobody stress-tests is hours better spent elsewhere. App-level role filtering achieves the same *demo-visible* guarantee; RLS migration path is documented (Section 18.3) so the honest answer under questioning is strong, not evasive. | REAL (app-level); RLS is ROADMAP with a stated migration path |
| No mobile app, no PWA offline queue | Explicit constraint: demo is laptop-only. Cut scope that solves a field-connectivity problem not needed for judging; kept as a stated roadmap item only | ROADMAP |

---

## 8. Complete Tech Stack

### 8.1 Frontend

| Component | Choice | Notes | Status |
|---|---|---|---|
| Framework | Next.js 15 (App Router) + TypeScript | SSR for public dashboard, client components for map/3D | REAL |
| Styling | Tailwind CSS + shadcn/ui | Fast, customizable tokens — avoid generic default look | REAL |
| Map (2D base layer) | Leaflet.js + OpenStreetMap tiles | Free, no API key, no rate-limit surprise mid-demo | REAL |
| 3D layer (primary path) | React Three Fiber + Drei | JSX-based Three.js, hooks-based animation, used for cluster-merge scene only | REAL, scoped to one scene |
| 3D layer (fallback path) | CSS transform transitions on DOM/Leaflet markers | Built in parallel with the shader scene from hour one, not as a panic fallback — see Section 20 | REAL, always shipped as a safety net |
| Shaders | Custom GLSL via `THREE.ShaderMaterial` | Merge-interpolation shader for the one cluster-merge scene | REAL if R3F path chosen |
| State bridge | Zustand | WebSocket events → Zustand store → shader uniforms or CSS state, bypassing unnecessary React re-renders | REAL |
| Client state (forms, filters) | Zustand | Lightweight, sufficient for this scope | REAL |
| UI-level animation | Motion (Framer Motion) | Component mount/unmount transitions, panel opens | REAL |

**Explicitly cut from v1.0 frontend scope, and why:** particle field hero scene, safety-pulse shader, closure-dissolve shader, GSAP choreography beyond the single cluster-merge sequence. All ROADMAP. One scene, built well, with a working fallback, beats four scenes with none guaranteed to survive a demo-laptop GPU. See Section 20 for the full reasoning.

### 8.2 Backend

| Component | Choice | Notes | Status |
|---|---|---|---|
| API framework | FastAPI (Python, async) | Matches ML/CV stack language, auto OpenAPI docs | REAL |
| Validation | Pydantic v2 | Schema validation on all complaint payloads | REAL |
| Task queue | Celery + Redis | Async classification/severity jobs, decoupled from ingestion | REAL |
| Rate limiting | Redis token bucket | Per-IP/device submission throttling | REAL |
| Real-time updates | WebSockets (FastAPI native) | Live map updates, cluster-merge events | REAL |
| Auth | FastAPI-Users + JWT with role claims | Role claims drive app-level RBAC filtering (Section 18) | REAL |

### 8.3 Database & storage

| Component | Choice | Notes | Status |
|---|---|---|---|
| Primary DB | PostgreSQL 16+ | Single instance, multiple extensions | REAL |
| Geospatial | PostGIS extension | `ST_DWithin` radius queries for clustering (Stage 1 of dedup) | REAL |
| Vector similarity | pgvector extension (HNSW index) | Embedding similarity for near-duplicate detection (Stage 2 of dedup) | REAL |
| Access control | App-level role filtering, RLS migration path documented | See Section 18.3 for the honest framing | REAL (app-level) / ROADMAP (DB-level) |
| Event log | `events` table, hash-chained on write | Append-only. Chain-integrity background verification job is a stated next step, not built for demo | REAL (writes) / ROADMAP (verification) |
| Graph DB | Neo4j | Contractor entity-resolution network — schema and worked example designed and diagrammed | ROADMAP |

### 8.4 AI / ML layer

| Component | Choice | Notes | Status |
|---|---|---|---|
| Classification (all categories) | CLIP zero-shot (`open_clip`, open source) | Pothole, garbage, streetlight, water leak, illegal dumping — one unified zero-shot approach, no fine-tuning dependency | REAL, with a published held-out accuracy number (Section 32) |
| Speech-to-text | `faster-whisper` | Hindi/Marathi voice complaint transcription, optimized for CPU | REAL for demo language set; multilingual expansion beyond Hindi/Marathi is ROADMAP |
| Text embeddings | `sentence-transformers` (`all-MiniLM-L6-v2`) | Near-duplicate detection on complaint text, Stage 2 of dedup | REAL |
| Local LLM | Ollama running Qwen2.5 or Llama 3.1 8B | Investigation Agent reasoning, zero API cost | REAL |
| Agent orchestration | LangGraph | Single-node graph for the Investigation Agent's evidence-gathering loop — not a four-agent persistent-state architecture | REAL, intentionally scoped down from v1.0 |
| Clustering | scikit-learn (DBSCAN) | Spatial-temporal clustering pass, secondary to the two-stage PostGIS/pgvector approach | REAL |
| Image comparison | scikit-image (SSIM) | Before/after closure photo verification | REAL |

**Explicit, deliberate cut:** YOLOv8n fine-tuning on RDD2022. This was the single largest time sink in v1.0 for the smallest demo-perceivable payoff — nobody in a 90-second demo can distinguish a fine-tuned detector from a well-validated zero-shot classifier by eye. Cut entirely, stated proactively on the real-vs-simulated slide, not discovered under questioning.

### 8.5 Trust & safety utilities

| Component | Choice | Notes | Status |
|---|---|---|---|
| EXIF/GPS extraction | `exifread` | Photo metadata cross-check against claimed location | REAL |
| Perceptual hashing | `imagehash` | Duplicate/reused image detection, survives compression | REAL |
| Face anonymization | MediaPipe Face Detection | Blur faces before storage (DPDP compliance) | REAL |
| Hash chaining (write path) | Python `hashlib` (SHA-256) | Tamper-evident event log, write-time only | REAL |
| Hash chain verification job | Background re-walk + integrity check | Described, not scheduled for demo build | ROADMAP |

### 8.6 Infra & deployment

| Component | Choice | Notes | Status |
|---|---|---|---|
| Containerization | Docker Compose | Single command spins up entire stack | REAL |
| Hosting (if deployed) | Oracle Cloud Free Tier (Mumbai region) or Railway/Render free tier | India-region hosting ties to DPDP data-localization stance | ROADMAP for pilot deployment |
| Demo environment | Fully local, air-gapped Docker Compose on presenting laptop | Zero dependency on venue WiFi, zero live API failure risk | REAL |
| Logging | Python `structlog`, stdout | Sufficient at this scale; the `events` table doubles as the primary audit/observability record | REAL |
| Testing | `pytest` (backend), held-out dataset script (CV accuracy reporting) | Real accuracy numbers before demo day, not claims | REAL |
| CI | GitHub Actions (basic, tests-on-push) | Free, minimal setup | REAL |

### 8.7 What was deliberately cut and why — master list

| Cut | Reason | Status |
|---|---|---|
| Native mobile app | Laptop-only demo constraint; browser client covers all needs | ROADMAP |
| PWA offline queue / service workers | Solves a field-connectivity problem out of scope for judging | ROADMAP |
| Separate vector database (Qdrant/Pinecone/Weaviate) | pgvector on existing Postgres performs comparably at this scale, avoids a second service to sync | Cut permanently unless scale demands it |
| Keycloak | Multi-hour integration project on its own; lighter JWT + app-level RBAC ships faster without losing the demo-visible guarantee | ROADMAP |
| SMS/IVR gateway (Twilio/Exotel) | Real cost and integration overhead | ROADMAP |
| Mapbox (paid tiers) | Leaflet + OSM tiles avoids API key/rate-limit risk entirely | Cut permanently |
| YOLOv8n fine-tuning | Largest time sink for smallest demo-perceivable payoff; CLIP zero-shot with published accuracy is the correct trade | Cut permanently for MVP, viable post-pilot if scale/accuracy demands it |
| Four-agent LangGraph (Intake/Classification/Investigation/Ops as separate persistent state machines) | One genuinely agentic component beats four thin ones under technical questioning | Cut to one agent (Investigation), see Section 12 |
| Live Neo4j graph queries in demo | Near-zero demo-perceivable payoff vs. a clear diagram and worked example | ROADMAP |
| Postgres RLS | Real policies for a demo nobody stress-tests is time better spent on the dedup engine and trust/safety spine | ROADMAP with documented migration path |
| Hash chain background verification job | The write-time hash chain is the cheap, high-value part; the verification job is the expensive, low-demo-payoff part | ROADMAP |
| Milestone-based fund release (working implementation) | Concept and data model shipped; actual disbursement logic simulated | SIMULATED |
| Additional 3D scenes (hero particle field, safety-pulse shader, closure-dissolve shader) | One scene built well beats four scenes with unclear demo-day GPU risk | ROADMAP |

---

## 9. Data Model & Event Sourcing

### 9.1 Core philosophy
The system of record is an **append-only events table**. Current state (`current_complaint_state`, `current_work_order_state`) is a materialized view derived from replaying events, not a directly-edited table. This gives:
- Full audit history for any complaint, work order, budget line, or admin action
- Tamper-evidence via hash chaining on write (verification job is roadmap, per Section 8.5)
- The ability to "time-travel" — reconstruct system state as of any past moment

### 9.2 Core tables (conceptual schema)

```sql
-- Append-only event log, the source of truth
CREATE TABLE events (
    id              BIGSERIAL PRIMARY KEY,
    event_type      TEXT NOT NULL,          -- e.g. 'complaint_submitted', 'severity_scored_v2'
    entity_type     TEXT NOT NULL,          -- 'complaint' | 'work_order' | 'contractor' | 'budget' | 'admin_action'
    entity_id       UUID NOT NULL,
    payload         JSONB NOT NULL,
    actor_id        UUID,                   -- who/what triggered this (citizen, staff, agent, system)
    actor_role      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    previous_hash   TEXT NOT NULL,          -- hash of the prior event row for this entity
    event_hash      TEXT NOT NULL           -- SHA256(previous_hash || event_type || entity_id || payload || created_at)
);

CREATE INDEX idx_events_entity ON events (entity_type, entity_id, created_at);
CREATE INDEX idx_events_type_time ON events (event_type, created_at);

-- Complaints (materialized current state, derived from events)
CREATE TABLE complaints (
    id                          UUID PRIMARY KEY,
    status                      TEXT NOT NULL,   -- submitted | verifying | classified | clustered | scored | routed | in_progress | pending_verification | closed | disputed | flagged
    category                    TEXT,            -- pothole | garbage | streetlight | water_leak | illegal_dumping | other
    description_text            TEXT,
    photo_url                   TEXT,
    audio_url                   TEXT,
    location                    GEOGRAPHY(POINT, 4326),   -- PostGIS
    exif_location                GEOGRAPHY(POINT, 4326),
    reported_at                  TIMESTAMPTZ,
    classification_confidence    FLOAT,
    text_embedding                VECTOR(384),   -- pgvector, matches MiniLM dimension
    image_embedding                VECTOR(512),   -- pgvector, matches CLIP dimension
    cluster_id                     UUID,
    severity_score                  FLOAT,
    severity_breakdown               JSONB,       -- component scores for explainability
    ward                              TEXT,
    department_id                      UUID REFERENCES departments(id),
    submitter_device_fingerprint        TEXT,
    is_safety_flagged                    BOOLEAN DEFAULT FALSE,
    is_fraud_flagged                      BOOLEAN DEFAULT FALSE,
    funding_source                         TEXT   -- e.g. 'AMRUT', 'ward_fund', 'smart_cities_mission'
);

-- Clusters (deduplicated incidents)
CREATE TABLE complaint_clusters (
    id               UUID PRIMARY KEY,
    centroid         GEOGRAPHY(POINT, 4326),
    report_count     INT DEFAULT 1,
    confidence       FLOAT,
    first_reported   TIMESTAMPTZ,
    last_reported    TIMESTAMPTZ,
    current_severity FLOAT
);

-- Work orders
CREATE TABLE work_orders (
    id                    UUID PRIMARY KEY,
    complaint_cluster_id  UUID REFERENCES complaint_clusters(id),
    department_id         UUID REFERENCES departments(id),
    assigned_to_type      TEXT,     -- 'staff' | 'contractor'
    assigned_to_id        UUID,
    status                TEXT,     -- assigned | in_progress | pending_verification | closed | disputed
    sla_deadline          TIMESTAMPTZ,
    budgeted_cost         NUMERIC,
    actual_cost           NUMERIC,
    before_photo_url      TEXT,
    after_photo_url       TEXT,
    ssim_score             FLOAT,
    milestone_stage         TEXT   -- 'start' | 'mid' | 'complete'
);

-- Contractors
CREATE TABLE contractors (
    id                    UUID PRIMARY KEY,
    name                  TEXT,
    registration_id       TEXT,
    registered_address    TEXT,
    phone                 TEXT,
    director_names        TEXT[],
    categories_certified  TEXT[],
    active_since          DATE,
    computed_rating        JSONB   -- derived, not manually entered
);

-- Departments
CREATE TABLE departments (
    id        UUID PRIMARY KEY,
    tenant_id UUID,      -- multi-tenant: municipality/campus/park
    name      TEXT,
    ward      TEXT
);

-- Budget / funding
CREATE TABLE budget_allocations (
    id               UUID PRIMARY KEY,
    ward             TEXT,
    funding_source   TEXT,
    fiscal_year      TEXT,
    allocated_amount NUMERIC,
    spent_amount     NUMERIC
);

-- Users (citizens, staff, contractors, admins) — role-gated via app-level filtering
CREATE TABLE users (
    id             UUID PRIMARY KEY,
    role           TEXT,   -- citizen | field_staff | department_head | ward_officer | super_admin | contractor
    department_id  UUID REFERENCES departments(id),
    contractor_id  UUID REFERENCES contractors(id)
);
```

### 9.3 Hash-chaining mechanism (write path only)

```python
import hashlib, json

def compute_event_hash(previous_hash: str, event_type: str, entity_id: str,
                        payload: dict, created_at: str) -> str:
    raw = f"{previous_hash}{event_type}{entity_id}{json.dumps(payload, sort_keys=True)}{created_at}"
    return hashlib.sha256(raw.encode()).hexdigest()

def insert_event(db, event_type, entity_type, entity_id, payload, actor_id, actor_role):
    prev = db.get_latest_event_hash(entity_type, entity_id) or GENESIS_HASH
    created_at = utcnow_iso()
    event_hash = compute_event_hash(prev, event_type, str(entity_id), payload, created_at)
    db.insert(
        event_type=event_type, entity_type=entity_type, entity_id=entity_id,
        payload=payload, actor_id=actor_id, actor_role=actor_role,
        created_at=created_at, previous_hash=prev, event_hash=event_hash
    )
```

Every new event's `previous_hash` must equal the `event_hash` of the immediately prior event for that entity (or a genesis hash if first event). This write-path chaining is cheap — roughly 30 minutes of implementation — and gives NEMESIS a true, specific, checkable answer to "how do we know this wasn't tampered with," even without the background re-verification job (which is explicitly ROADMAP; see Section 17.4 for the honest framing of that gap).

### 9.4 Event type catalog

| Event type | Fired when |
|---|---|
| `complaint_submitted` | Citizen submits a report |
| `exif_check_completed` | Fraud/EXIF verification runs |
| `safety_trigger_fired` | Danger keyword/visual trigger detected |
| `classification_scored` | CLIP zero-shot classification completes |
| `cluster_match_found` / `cluster_created` | Dedup engine result |
| `severity_rubric_v1_scored` | Severity scoring completes (versioned) |
| `investigation_agent_invoked` | Ambiguous case routed to the Investigation Agent |
| `investigation_agent_evidence_gathered` | Agent completes a tool call (photo request, OSM query, history check) |
| `investigation_agent_concluded` | Agent reaches a final classification/decision with logged justification |
| `work_order_created` | Routing completes |
| `work_order_assigned` | Department assigns staff/contractor |
| `budget_allocated` / `budget_spent` | Budget events |
| `milestone_evidence_uploaded` | Progress/completion photo uploaded |
| `ssim_verification_completed` | Before/after diff computed |
| `citizen_confirmation_requested` / `citizen_confirmed` / `citizen_disputed` | Closure loop |
| `anomaly_flagged` | Billing/quantity/rate-card anomaly detected |
| `admin_action` | Any super-admin action (logged, never a silent edit) |
| `dispute_raised` / `dispute_resolved` | Contractor appeal workflow |

---

## 10. Complaint Lifecycle — End to End

```
1. SUBMIT
   Citizen submits photo/voice/text + GPS via browser.
   ↓
2. PARALLEL VERIFICATION (async, Celery)
   ├── Fraud check: EXIF GPS cross-match + perceptual hash against submission history
   ├── Safety fail-safe scan: CLIP zero-shot danger-visual check + keyword scan
   └── Abuse/velocity check: Redis token bucket + device fingerprint clustering
   ↓
   IF safety trigger fires → SKIP QUEUE → immediate urgent alert, human-reviewed within minutes
   IF fraud/abuse flagged  → route to human review queue, do not silently block or pass
   ELSE → continue
   ↓
3. CLASSIFICATION
   CLIP zero-shot across all categories (pothole, garbage, streetlight, water leak, illegal dumping)
   faster-whisper transcription if voice note present
   → structured category + confidence, logged as versioned event
   ↓
4. DEDUPLICATION / CLUSTERING
   Stage 1: PostGIS ST_DWithin (50m radius, 72hr window) — cheap geo filter
   Stage 2: pgvector cosine similarity on image + text embeddings — semantic filter
   MATCH (>0.85 confidence) → merge into existing cluster, bump priority/confidence
   AMBIGUOUS → Investigation Agent (single LangGraph node) gathers more evidence before deciding
   NO MATCH → new cluster created
   ↓
5. SEVERITY SCORING
   severity = w1*(visual_damage) + w2*(road_class from OSM) + w3*(POI_proximity)
            + w4*(cluster_report_count)
   Every component logged for explainability. Versioned rubric (severity_rubric_v1, v2...).
   ↓
6. ROUTING
   Tenant-specific department config (real ward/department structure)
   → work order created, SLA timer starts based on severity tier
   ↓
7. ASSIGNMENT (department side)
   Department head assigns to staff or contractor
   Budget estimate logged against rate-card baseline
   ↓
8. EXECUTION
   Milestone-based evidence: start photo → mid-progress photo → completion photo
   Fund "release" (SIMULATED financial model) only when corresponding evidence event exists
   ↓
9. VERIFICATION
   SSIM comparison of before/after photos — meaningful visual change required
   before status can move to "pending_verification"
   ↓
10. CITIZEN CONFIRMATION
    Citizen notified, asked "was this actually fixed?"
    CONFIRMED → closed, feeds severity-model calibration + contractor rating
    DISPUTED → reopened, escalated, flagged
    NO RESPONSE (48hr) → auto-confirmed with "unconfirmed" note
   ↓
11. PUBLIC RECORD
    Contractor profile updates, budget transparency page updates,
    ward-level dashboard updates — all derived from the same event log
```

---

## 11. Trust & Safety Layer

### 11.1 Anti-fraud on submission
- **EXIF/GPS cross-check** (`exifread`): claimed location vs photo metadata GPS; mismatch beyond ~200m flags for review.
- **Edge case handling:** WhatsApp and many share flows strip EXIF by default. If EXIF is absent, reduce trust score rather than auto-reject, and require live-camera-capture-only mode (blocks gallery upload) as the real control for that case.
- **Perceptual hashing** (`imagehash`): catches re-uploaded/screenshotted images even after compression or resize — stronger than MD5/exact-match hashing.
- **Device/session velocity check:** Redis token bucket flags unusually high submission rates per device fingerprint.

### 11.2 Safety fail-safe (deterministic, not probabilistic)
A hardcoded trigger list — keywords ("live wire," "gas leak," "collapsed," "sparking," "flooding") plus one CLIP zero-shot visual trigger prompt set ("exposed electrical wiring," "structural collapse," "active flooding") — bypasses the entire scoring pipeline. Any match fires an immediate high-priority alert regardless of any other score.

**Why hardcoded and not ML-scored:** false negatives on danger signals are unacceptable. This is a deliberate design choice favoring deterministic behavior over probabilistic elegance, and it should be stated explicitly in any review — the willingness to be less "clever" here for the sake of reliability is itself a maturity signal, and it is the single strongest line to say out loud in a pitch (Section 33.2).

**Build cost:** 3–4 hours. Highest credibility-per-hour item in the entire system.

### 11.3 Coordinated abuse detection
Distinct from individual fraud — tracks submitter velocity (complaints per device-fingerprint/session/hour) and submitter geographic clustering (multiple "different" users submitting from suspiciously similar IP ranges or device fingerprints targeting one ward in a tight window). Flags, does not auto-block; routes to human review queue with the evidence bundle attached. Demo scope: 3 seeded fake accounts targeting one ward, sufficient to demonstrate the mechanism live without needing production-scale robustness.

### 11.4 Human review queue
Every "flag for review" in the system has a real destination: an admin-facing filtered table showing the flagged item, the evidence bundle (photo, EXIF data, similarity scores, reason flagged), and one-click approve/reject/escalate actions. No flag is ever a dead end — this is a Nemesis-standard requirement, not an optional polish item.

---

## 12. Multi-Agent Architecture — The Honest Version

### 12.1 Why this section changed the most from v1.0

The original design proposed four LangGraph agents — Intake, Classification, Investigation, and Ops — each described as persistent, stateful components. Under senior review, this doesn't hold up: three of the four (Intake, Classification, Ops) are, in an honest build, deterministic pipelines with clear if/then logic and tool calls, not agents making autonomous multi-step reasoning decisions. Calling them "agents" and then being asked "walk me through what the Ops Agent decided and why" is a real exposure risk — the honest answer would be "it checked a deadline against a timestamp," which is not agentic behavior, and admitting that live is worse than never having claimed it.

**The fix:** build one real agent, extremely well, and be explicit that the rest of the pipeline is deterministic — because deterministic is a *feature*, not an admission of weakness (see Design Principle #2, Section 6).

### 12.2 Why LangGraph, still
LangGraph remains the right orchestration choice for the one component that needs it — it treats agent behavior as a **state machine with tool calls**, not a chat loop, with built-in persistence, checkpointing, and replay/time-travel debugging. This directly supports the audit requirement ("walk me back through why this complaint got escalated") for the one place in the system where a genuine multi-step reasoning trace exists.

### 12.3 The deterministic pipeline stages (formerly "Intake Agent," "Classification Agent," "Ops Agent")

**Intake stage** (deterministic)
- Validates incoming complaint payload against Pydantic schema
- Checks EXIF/fraud/velocity signals against hardcoded thresholds
- Routes: fast-path (safety trigger → skip queue) or normal pipeline
- No LLM call. No claim of agentic behavior. This is correctly framed as a validation/routing pipeline stage.

**Classification stage** (deterministic + model inference, not agentic)
- Runs CLIP zero-shot + Whisper transcription + text classification
- Outputs structured category + confidence
- A model inference call is not, by itself, agentic behavior — it is a single forward pass. Framed honestly as ML inference, not agent reasoning.

**Ops stage** (deterministic)
- Owns SLA tracking: compares `sla_deadline` against current time on a scheduled check
- Triggers auto-escalation on SLA breach via a rule, not a reasoning step
- Requests citizen confirmation on department-marked closures via a triggered notification
- Correctly framed as a scheduled rules engine, which is exactly what production SLA tracking should be — deterministic reliability is the selling point here, not autonomy.

### 12.4 The Investigation Agent — the one genuinely agentic component

This is the actual, defensible basis for calling any part of NEMESIS "agentic," and it is built to survive hard questioning, not just to look good in a script.

**Trigger condition:** activates only on ambiguous/low-confidence or conflicting-signal cases — for example, citizen text says "gas leak" but CLIP's visual classification sees nothing conclusive, or dedup Stage 2 similarity sits in the 0.65–0.85 "maybe" band where neither auto-merge nor auto-reject is safe.

**What it actually does — genuine multi-step tool use, not a single classifier call:**
1. Requests an additional photo from the citizen via a push/in-app prompt (real tool call: notification dispatch)
2. Queries OpenStreetMap for gas-line/utility proximity data near the reported coordinates (real tool call: OSM Overpass API query, cached locally)
3. Checks recent complaint history at that GPS point — has this location generated similar reports before? (real tool call: Postgres query against complaint history)
4. Synthesizes the gathered evidence via the local LLM (Ollama, Qwen2.5/Llama 3.1) and produces a structured decision with a logged natural-language justification string

**Why this is genuinely agentic and the others aren't:** the sequence of tool calls is not fixed in advance — the agent decides, based on what the first tool call returns, whether a second is needed, and in what order. This is the actual, checkable difference between "agent" and "function with an LLM call in it," and it is the correct, narrow claim to make rather than the broader four-agent claim from v1.0.

**Logged output example:**
```json
{
  "event_type": "investigation_agent_concluded",
  "entity_id": "complaint-uuid-here",
  "payload": {
    "trigger_reason": "text_visual_conflict",
    "tool_calls_made": [
      "request_additional_photo",
      "query_osm_utility_proximity",
      "check_location_complaint_history"
    ],
    "evidence_summary": "No gas utility line within 15m of reported coordinates per OSM data; two prior unrelated complaints at this location (streetlight, 4 months ago). No additional photo received within timeout window.",
    "final_classification": "unconfirmed_hazard_report",
    "confidence": 0.42,
    "routing_decision": "escalate_to_human_review_not_safety_bypass",
    "justification": "Visual and OSM evidence do not corroborate the reported hazard type; routing to human review rather than dismissing, given the safety-relevant nature of the initial report."
  }
}
```

**Build cost:** 6–8 hours. This is your actual answer to "is this really an agent," and it should be rehearsed as a live demo moment (Section 33.1, step 5 equivalent), not just described.

### 12.5 Autonomous decision example — the cluster-density reasoning step
When cluster report count crosses a threshold (e.g., 5 independent reports within 6 hours), the system re-runs severity scoring with a cluster-density multiplier. If this reasoning step is built as a genuine LLM synthesis over aggregated evidence (multiple photo descriptions, spread of report times, textual urgency language) with a logged justification string, it is fair to describe as a second, smaller agentic behavior. **If time-constrained, this can legitimately be a hardcoded `if count > 5` rule** — but then it must be described as exactly that on the real-vs-simulated slide, not blurred into the Investigation Agent claim. Do not let two different claims collapse into one under pitch pressure.

---

## 13. Severity Scoring System

### 13.1 Transparent rubric, not a black box
```
severity = w1 * visual_damage_score      (from CLIP confidence + damage-type weight)
         + w2 * road_class_weight        (from OSM `highway` tag: primary > residential > footway)
         + w3 * poi_proximity_score      (from OSM `amenity=school/hospital` within 200m)
         + w4 * cluster_report_count     (more independent reports = higher confidence + priority)
```

Every component score is logged per complaint (`severity_breakdown` JSONB field), so any scored complaint can show exactly why it scored what it scored — this is the direct answer to "how do you know your severity score is accurate," and it's a stronger answer than a trained black-box model would give at this data scale. **Build cost: 2–3 hours.** This is the highest credibility-to-effort ratio item in the entire system — pure arithmetic against a JSONB column.

### 13.2 Explicit honesty about method
There is no labeled severity training dataset available. This is a **rules engine with CV as one input**, not a trained severity model — stated precisely, this is more credible under technical questioning than overclaiming "AI-estimated severity."

### 13.3 Versioning and calibration (MLOps discipline)
- Every rubric change is versioned (`severity_rubric_v1`, `v2`, ...) and logged against which complaints it scored
- Resolution outcomes feed back: did "urgent" complaints actually resolve faster than "low"? This becomes a real, reviewable feedback loop, not a one-time hardcoded formula
- Pitch framing: "we started at a stated baseline, and the rubric improves as resolution data accumulates" — a system that gets better, not a static model

### 13.4 Context normalization
SLA/severity performance is normalized against external context where fair — e.g., monsoon-season road repair is genuinely harder than dry-season repair (via free IMD rainfall data by date/region). This prevents contractors or departments from being unfairly penalized by conditions outside their control, and conversely rewards genuinely strong performance under hard conditions.

### 13.5 Default rubric weights (starting configuration)

| Component | Default weight | Range | Notes |
|---|---|---|---|
| `visual_damage_score` | 0.40 | 0–1 | From CLIP confidence × damage-type severity table (Appendix B) |
| `road_class_weight` | 0.25 | 0–1 | OSM `highway` tag lookup table |
| `poi_proximity_score` | 0.20 | 0–1 | Decays linearly from 1.0 at 0m to 0.0 at 200m from school/hospital |
| `cluster_report_count` | 0.15 | 0–1 | `min(report_count / 10, 1.0)` — capped to avoid runaway inflation |

These are stated defaults, not claimed-optimal values — calibration against real resolution-time outcomes is the explicit next step (Section 40).

---

## 14. Deduplication & Clustering Engine — The Core Moat

This is the single most technically defensible piece of NEMESIS — genuinely hard, not a groupby on lat/lng, and it is the one component that should **never** be cut, simplified, or demoted regardless of time pressure (Section 31).

### 14.1 Two-stage approach

**Stage 1 — cheap geo filter**
```sql
SELECT * FROM complaints
WHERE ST_DWithin(location, :new_complaint_location, 50)  -- 50 meters
AND reported_at > NOW() - INTERVAL '72 hours';
```
Eliminates ~90% of non-candidates cheaply before any embedding computation.

**Stage 2 — expensive semantic filter**
On surviving candidates, compute cosine similarity between:
- CLIP image embeddings (visual similarity)
- Sentence-transformer text embeddings (`all-MiniLM-L6-v2`) on complaint descriptions

```sql
SELECT id, 1 - (image_embedding <=> :new_image_embedding) AS visual_sim,
       1 - (text_embedding <=> :new_text_embedding) AS text_sim
FROM complaints
WHERE id = ANY(:stage1_candidate_ids)
ORDER BY GREATEST(visual_sim, text_sim) DESC;
```

If either exceeds ~0.85 threshold → merge into cluster with a confidence score.
If below threshold but geo-close → flag for human review rather than silently merging (avoids false-positive merges that would suppress genuinely distinct issues), OR route to the Investigation Agent if the case involves conflicting signals worth a deeper look (Section 12.4).

### 14.2 Why this matters
Two citizens photographing the same pothole from different angles, different lighting, different times is a real computer-vision + geospatial problem — not a trivial one. Getting this right, and being able to explain the two-stage mechanism precisely under questioning, is a genuine technical differentiator most competing civic-tech projects skip entirely. **Build cost: 8–10 hours.** This is the line item that justifies the entire product's technical credibility; everything else on the "never cut" list (Section 31) exists to support this one.

### 14.3 Threshold tuning notes
- 0.85 is a starting threshold, not a tuned constant — document it as such
- False-positive merges (distinct issues incorrectly combined) are worse than false-negative merges (duplicates left unmerged) in the early pilot phase, because incorrect merging actively suppresses a genuine report — bias the threshold conservative initially
- Track merge precision against a small human-labeled validation set before claiming a specific accuracy number in the pitch

---

## 15. Department-Side Workflow

### 15.1 Triage view
Queue sorted by SLA countdown + severity, filterable by ward/category. Department heads see everything in their department (app-level role filter); field staff see only what's assigned to them.

### 15.2 Assignment
Department head assigns a work order to in-house staff or an external contractor. Assigning externally creates a linked record between the work order and the contractor's persistent profile — this link is what feeds the entire transparency module (Section 16).

### 15.3 Contractor selection
When assigning externally, the system shows available contractors for that work category, ranked by existing rating + current workload — this itself is a transparency feature (prevents silently dumping all work on one vendor while others sit idle).

### 15.4 Budget entry
Estimated cost logged against a category-based rate card (ideally seeded with real PWD Schedule of Rates structure, or a reasonable stated estimate methodology if real data isn't available).

### 15.5 Milestone-based evidence & fund release (SIMULATED)
Work is staged: 30% on work-start photo + material delivery confirmation, 40% on mid-progress photo, 30% on citizen-confirmed closure. **In the demo, fund "release" is a simulated ledger event, not a real disbursement mechanism** — labeled explicitly on the real-vs-simulated slide. The data model and event structure are real and production-shaped; only the actual money-movement integration is simulated, since no real payment rail exists at pilot stage. This directly models real escrow/milestone-payment mechanisms and previews the "paid in full, work never finished" prevention story without overclaiming a built payment system.

### 15.6 Closure
SSIM-verified before/after photo pair required before "resolved" status is reachable. Then the same citizen-confirmation loop as the main complaint lifecycle, feeding both SLA metrics and the contractor's computed rating.

### 15.7 Department dashboard UI shape
- Left sidebar: role-filtered queue (automatic via app-level role check, no manual filter needed)
- Main panel: kanban columns — Assigned → In Progress → Pending Verification → Closed
- SLA countdown badges, color-coded by urgency, matching the same severity-color language used on the public map for visual consistency across citizen and department views
- Clicking a work order opens the full evidence trail — photos, cost log, event history — scoped to what that role is permitted to see

---

## 16. Contractor Transparency & Accountability Module

### 16.1 Contractor profile (public-facing)
- Name, registration/license ID, categories certified, active-since date
- **Computed reputation metrics** (never a single collapsed star rating):
  - % jobs closed within SLA
  - % jobs citizen-confirmed vs disputed
  - Average cost variance (budgeted vs actual)
  - Repeat-defect rate (same location re-reported within 90 days after "fix")
- Full work history: date, category, ward, budgeted vs actual cost, before/after photos, citizen confirmation outcome
- Any system-flagged anomaly shown with an explicit **"system-flagged, unverified, under human review"** disclaimer — never presented as a proven fact (see Section 22.2 for the legal reasoning)
- "Request RTI draft" button on any flagged item — auto-fills a template requesting official records for that contractor/ward/date-range, tying the platform directly to India's actual legal accountability mechanism (RTI Act). **Status: SIMULATED for demo (template auto-fill only, no actual filing integration); ROADMAP for real submission integration.**

### 16.2 Ward-level public transparency page
Total budget allocated this year, spent, by which contractor, with photographic evidence — bookmark-able by journalists, RTI applicants, or engaged citizens.

### 16.3 Public API
Read-only, rate-limited, no-auth-required API over aggregate, privacy-scrubbed data (ward-level spend, contractor track records, resolution rates). Makes the platform infrastructure other tools can build on, not just a closed app. **Status: ROADMAP** — described with a stated schema, not built for the initial demo, since it has no live-demo audience (no one else's tool is querying it during judging).

### 16.4 Fairness mechanisms (must-have, not optional)
- **Dispute/appeal workflow:** contractors get their own restricted login, can contest flagged anomalies or disputed closures, upload additional evidence, request re-review. A one-sided accountability system invites resistance and sabotage. **Status: ROADMAP for demo** (UI mockup only), core to pilot deployment.
- **Seasonal/context normalization:** SLA scoring adjusted for weather context (monsoon vs dry season) so ratings aren't unfairly skewed by external conditions. **Status: designed, ROADMAP for implementation.**
- **Cross-ward comparison (internal only):** same contractor's performance broken out by ward, revealing whether underperformance is systemic (skill/capacity) or localized (potentially favoritism in assignment/oversight) — kept internal to avoid unverified public accusation. **Status: ROADMAP.**

---

## 17. Fraud & Corruption Detection Layer

This section directly reuses fraud-graph / entity-resolution techniques — the same problem class as financial mule-account detection — applied to public-works vendor networks. **Every subsection here is explicitly labeled for demo status; this is the section most at risk of overclaiming if not handled carefully.**

### 17.1 Contractor entity-resolution graph (Neo4j) — DESCRIBED, NOT LIVE
- **Nodes:** contractors, registered addresses, phone numbers, director/owner names (from public company registration data where available)
- **Edges:** shared address, shared phone, shared director, shared bank details
- **Purpose:** surfaces bid-rigging-style patterns — e.g., "these 4 contractors, technically different registered entities, share a registered address and have collectively won 60% of contracts in this ward over 2 years." This is invisible to any single-contractor rating view.
- **Demo status:** presented as a schema diagram plus one worked hypothetical example, not a live Cypher query. This is a deliberate, disclosed simplification — nobody at a hackathon is realistically going to stress-test a live graph database query, and the diagram communicates the concept just as effectively in the available time.
- Kept strictly internal to municipal/admin roles in the real design — publishing raw entity-resolution graphs of real companies publicly carries materially higher defamation risk than a performance score, since it implies a *relationship*, not just a metric.

### 17.2 Billing anomaly detection
- **Quantity-vs-photo cross-check:** rough area/quantity estimate from before/after photo pairs (pixel-to-real-world scale calibration or segmentation) compared against billed quantity; large mismatches auto-flag for human review, never auto-reject. **Status: SIMULATED** — demo uses a seeded, planted anomaly with a simplified rule-based check rather than a real segmentation pipeline.
- **Rate-card deviation scoring:** billed line items compared against a seeded PWD Schedule of Rates baseline; systematic overbilling across many jobs (not a single outlier) is a statistical anomaly worth flagging — same family of technique as outlier detection in transaction-fraud scoring. **Status: REAL** — this is genuinely just a threshold comparison against a seeded rate table, cheap and honest to build.

### 17.3 Milestone-based fund release
See Section 15.5 — SIMULATED financial model, REAL data/event structure.

### 17.4 Tamper-evident ledger
Hash-chained event log makes retroactive edits provably detectable at the write-record level. **The background re-verification job that proves no historical record was altered post-hoc is ROADMAP** — this is the one honest gap in the tamper-evidence story, and it should be stated exactly this way if asked: "we hash-chain every write; the periodic full-chain integrity sweep is the documented next step, not yet scheduled as a running job." That sentence is stronger than either overclaiming full tamper-proofing or omitting the mechanism entirely.

### 17.5 Repeat-defect clustering (contractor-specific)
A second clustering pass specifically for repeat defects at the same location tied to the same contractor. If a contractor "fixed" the same GPS point three times in 8 months, that's either a genuine underlying infrastructure issue (e.g., drainage causing recurring damage) or minimal-effort patch jobs done for repeat billing. The system surfaces the pattern; it does not adjudicate guilt — that stays a human decision. **Status: REAL** — this reuses the exact same dedup engine from Section 14, filtered by contractor_id, so it is genuinely cheap to add once Section 14 exists.

### 17.6 Fund-source / scheme tracing
Every work order tagged to a `funding_source` (AMRUT, Smart Cities Mission, Swachh Bharat, ward-development funds, MLA/MP local area development funds), enabling "how much of the AMRUT allocation for this ward has actually been spent, on what, verified how" — directly useful to journalists and RTI applicants, a real secondary buyer persona. **Status: REAL** — this is a schema field and a query, not a new subsystem.

### 17.7 Who audits the system itself
Admin actions are events in the same hash-chained log as everything else — not privileged database writes that bypass it. Even a super-admin cannot silently edit history, only append correcting events. This is the concrete answer to "who watches the watchers," and it costs nothing extra since the write-path mechanism already exists for other entities.

---

## 18. RBAC & Access Model — What's Real vs Described

### 18.1 Role matrix

| Role | Sees | Can do |
|---|---|---|
| **Citizen (public, no login for browsing)** | Public map, contractor profiles, ward budget pages | Submit complaints, confirm/dispute closures |
| **Field staff** | Only work orders assigned to them | Update status, upload progress/completion photos |
| **Department head** | Full queue for their department only | Assign contractors/staff, approve budget, view department contractor performance, resolve disputes |
| **Ward officer** | All departments' work orders within their ward | Escalate, reassign priority, view ward-level budget dashboard |
| **Super-admin** | Everything — cross-ward comparison, entity-resolution graph, full audit log | Append correcting events (never silent edits), manage contractor onboarding |
| **Contractor (own login)** | Only their own assigned work orders + their own public-facing profile | Upload evidence, respond to disputes, contest flagged anomalies |

### 18.2 Enforcement mechanism — the honest version
Access control for the demo build is enforced via **application-layer role checks** on every query — the JWT's role claim gates which rows a query returns, checked in FastAPI middleware before data reaches the response. This is not a database-level guarantee; a direct, malformed database query outside the application could theoretically bypass it.

**This is a stated, deliberate scope decision, not an oversight.** Building and testing real Postgres Row-Level Security policies for a demo environment that no judge will attempt to penetration-test is hours better spent on Section 14 (dedup) and Section 11 (trust & safety). The correct move is to say so plainly.

### 18.3 RLS migration path — the prepared answer
If asked "why not RLS, and how would you get there," the honest and *strong* answer is:

> "For the demo, RBAC is enforced at the application layer via role claims in the JWT, checked in FastAPI middleware. Moving to Postgres Row-Level Security is a bounded, well-understood migration given our schema — it's a `SET LOCAL ROLE` or session-variable pattern applied per authenticated request, and every table already carries the `department_id`/`ward` columns RLS policies would filter on. We scoped it out of the hackathon build specifically because a demo environment doesn't stress-test database-layer security in a way that would be visible to judges in 90 seconds — but it's the first infrastructure hardening step before any real pilot deployment, not a redesign."

This answer is stronger than the original v1.0 line ("RLS is a materially stronger guarantee") *stated as if already built*, because it survives a follow-up request to see the policy. Never let a described-but-unbuilt component be phrased as already shipped.

### 18.4 Auth stack
FastAPI-Users + JWT with role claims. Role claims are checked against a middleware-level permission table on every request. Chosen over Keycloak for build-timeline reasons — Keycloak is a strong long-term option but is its own multi-hour integration project that would eat time better spent on the differentiating features (dedup, closure loop, trust & safety).

---

## 19. Frontend, Design Language & 3D/Shader Layer

### 19.1 Design principles
- 3D/shader work is used only where it carries **meaning**, never as decoration — Design Principle #9 (Section 6). Dashboards, forms, and department queues stay flat, clean, readable.
- **One** 3D scene, mapped to the single most important narrative beat (deduplication becoming visible), not several gratuitous effects. This is a deliberate reduction from v1.0's four-scene plan — see Section 20 for the full reasoning.

### 19.2 The one scene that ships: cluster-merge
When two or more citizen reports are detected as duplicates by the real dedup engine (Section 14), the frontend visualizes them merging into a single incident marker on the map, with the merged marker's size/color updating to reflect the new aggregated severity. This is the demo's core "wow" moment **because it is wired to a real, live WebSocket event from the actual pipeline — not a scripted animation triggered by a button.**

### 19.3 Design system notes
- Color language: severity-based (red = urgent, amber = medium, blue = low), used consistently across the public map, department kanban SLA badges, and the cluster-merge visualization — one visual vocabulary across every surface, not per-screen ad hoc choices
- Typography and general layout should stay clean and utilitarian outside the one hero visualization — a government-facing tool that looks over-designed everywhere reads as untrustworthy to the department-head persona specifically

### 19.4 Supporting animation libraries
- **Motion (Framer Motion):** component-level transitions, panel opens, status badge changes — used broadly, cheap, low-risk
- **GSAP:** not used in v2.0 scope; reserved only if the cluster-merge sequence specifically needs choreography beyond what a shared shader uniform can drive

---

## 20. The Cluster-Merge Wow-Moment — Primary Path + Fallback

This section exists because v1.0 treated the shader scene as equally certain to ship as the dedup engine. **It is not.** Dedup is deterministic backend engineering — if the team has the SQL/Python skill, it reliably ships. A custom-GLSL, InstancedMesh, WebSocket-driven shader scene is a different skill entirely, with real variance depending on whether the frontend owner has shipped React Three Fiber before.

### 20.1 Primary path — real shader scene
`InstancedMesh` with per-instance shader attributes (`InstancedBufferAttribute` for color, height, pulse phase) driven by a single shared vertex shader, not per-object JS. When duplicate reports are detected, a shared `uMergeProgress` uniform animates 0→1, and the shader interpolates their positions toward the merged centroid while growing the merged pin's height to reflect updated severity. GPU-driven — stays smooth even with hundreds of pins rendered simultaneously.

**Estimated build cost:** 10–14 hours, contingent on prior R3F/GLSL experience on the team.

### 20.2 Fallback path — CSS/DOM transition, built in parallel from hour one
A plain set of Leaflet DOM markers with a CSS `transition: transform 0.5s ease` rule. When the same real WebSocket `cluster_match_found` event fires, the two marker elements animate their `translate` toward the merged centroid coordinates, and the merged marker's size/color updates via a class change. Visually less impressive than a shader, but **it proves the identical underlying claim** — a live pipeline event driving a visible state change, not a canned animation.

**Estimated build cost:** 3–4 hours.

### 20.3 The rule
Both paths consume the exact same WebSocket event contract (Section 26.3), so switching between them is a frontend-only decision made as late as end of Day 2, with zero backend rework. **Never let the one wow-moment become a single point of failure.** If the shader path is unstable by the Day 2 checkpoint, ship the fallback without hesitation and without framing it as a downgrade in the pitch — "live pipeline-driven visualization" is the honest and sufficient claim either way.

### 20.4 Reduced-motion / crash safety
Regardless of which path ships, a static-map fallback (no animation at all, just updated markers) must exist and be one config flag away from activation. A crashed WebGL context in front of judges is a worse outcome than no 3D at all — test this on the actual demo laptop, not a development machine, at least once before demo day.

---

## 21. Notification & Closure Loop

### 21.1 Fallback chain
Push (if browser supports it) → SMS (ROADMAP, not built) → in-app log fallback (always available on next visit). Decoupled from the main pipeline via a Celery-based notification worker so a notification failure never blocks a work order from being created.

### 21.2 Closure verification mechanism
Department marks work "resolved" → triggers request for before/after photo pair → SSIM structural similarity check confirms meaningful visual change → citizen notified, asked to confirm within a 48-hour window → confirmed closes the loop and feeds contractor rating + severity-model calibration; disputed reopens and escalates; no response defaults to "auto-confirmed (unconfirmed)" with a visible note distinguishing it from an actual citizen confirmation.

### 21.3 Retention mechanic (counters engagement decay)
Citizens who receive photo-verified closures on their reports earn a visible "resolution streak" or trust-score boost unlocking priority handling on future reports — a small, cheap gamification loop that directly targets the reason prior civic apps died (Section 3.1): citizens stop reporting once they stop believing anything changes. **Status: ROADMAP**, cheap to add post-pilot, not core to demo.

---

## 22. Privacy, Compliance & Legal Risk

### 22.1 DPDP Act (India) compliance posture
- Faces blurred via MediaPipe before storage
- Raw photos auto-purged after a stated retention period (e.g., 30 days); only classification metadata and blurred thumbnail retained long-term
- GPS coarsened to ~100m precision on any public-facing dashboard; exact GPS retained internally for routing only
- Explicit consent/retention-period statement shown at submission
- Self-hosted on India-region infrastructure (Oracle Cloud Free Tier, Mumbai region, or equivalent) at pilot stage — ties directly to data-localization expectations

### 22.2 Defamation / reputational risk (contractor transparency module)
Publishing performance data about named real people/companies is a factual claim that can damage business reputation. Any anomaly-flagging that is wrong even once carries real legal exposure under both civil and criminal defamation provisions in India.

**Mitigation, non-negotiable:**
- Nothing reaches a public profile without either (a) a human review step confirming the flag, or (b) explicit "system-flagged, unverified, under review" framing with disclosed methodology — never presented as settled fact.
- This limitation should be stated proactively in any pitch or review — it demonstrates legal maturity almost no competing hackathon project will show.

### 22.3 Data minimization for minors
Any photo potentially containing minors is subject to the same face-blur pipeline; no additional identifying metadata about minors is retained.

### 22.4 Data retention schedule (concrete, statable)

| Data type | Retention | Justification |
|---|---|---|
| Raw uploaded photo | 30 days | Sufficient for dispute/verification window; minimizes exposure |
| Blurred thumbnail | Indefinite (tied to public record) | Needed for public transparency page evidence |
| Exact GPS | Indefinite, internal-only | Needed for routing/dedup accuracy over time |
| Coarsened GPS (~100m) | Indefinite, public-facing | Balances transparency with individual location privacy |
| EXIF metadata | 90 days | Needed for fraud-pattern review window, then purged |
| Device fingerprint | 90 days, rolling | Abuse-pattern detection needs a rolling window, not permanent tracking |

---

## 23. Equity & Bias Safeguards

### 23.1 The core bias risk
Complaint volume correlates with smartphone access and civic engagement, not with actual infrastructure damage. Wealthier, more digitally engaged wards will generate disproportionate report volume; poorer or less-connected wards with objectively worse infrastructure may show as "no complaints = no problem," which is exactly backwards — and left unaddressed, the system would quietly reinforce existing inequality while appearing purely data-driven.

### 23.2 Mitigation — underreporting-zone flag
Pull OSM `highway`/road-surface tags as a rough proxy for infrastructure age/quality per ward, overlay against complaint-density heatmap. Wards with high inferred infrastructure risk but low complaint density are flagged as "possible underreporting zone" — a dashboard toggle, cheap to build, and arguably the single most senior-level design decision in the whole system, since it preempts a bias critique before a judge or reviewer raises it. **Status: REAL**, a few hours of GIS work.

### 23.3 Political sensitivity framing
A dashboard showing "Ward 12 has 40 unresolved potholes for 60 days" is implicitly a performance report on whoever is accountable for that ward — this affects adoption willingness, especially in government contexts. Default public framing leads with positive, incentive-aligned metrics (response rate, average resolution time) rather than an unresolved-count "shame board," with the raw unresolved-age metric available as a secondary drill-down rather than the headline view.

---

## 24. Observability, Testing & Resilience

### 24.1 Testing strategy
- Hold out ~15–20% of the seeded demo dataset; report actual precision/recall for classification and a sanity check on severity-ranking agreement with human judgment before demo day (Section 32 details the CLIP-specific protocol)
- `pytest` coverage on backend logic (routing, severity computation, hash-chain write integrity)

### 24.2 Graceful degradation
If the classification service is down, the pipeline must not halt — complaint still gets created with status `pending_classification` and routes to manual review rather than being lost. This resilience thinking signals a system designed to run unattended for months, not just survive a five-minute demo.

### 24.3 Logging
Structured logging (`structlog`) to stdout is sufficient at this scale; the `events` table itself doubles as the primary audit/observability record, avoiding the need for a separate observability stack.

### 24.4 Model/rubric versioning discipline
Every severity rubric or classification prompt-set change is versioned and logged against which complaints it scored, enabling backtesting when retuning — this is the MLOps discipline that turns "we built a model" into "we built a system that measurably improves."

---

## 25. Security Threat Model

A senior review does not skip this section — it was absent from v1.0 entirely, and any technically literate judge or reviewer will ask about it.

### 25.1 Threat surface summary

| Threat | Vector | Mitigation in this design | Status |
|---|---|---|---|
| Fake/spam complaints flooding the pipeline | Automated submission scripts | Redis token bucket rate limiting per device fingerprint + IP | REAL |
| Coordinated brigading against a ward/contractor | Multiple accounts, similar fingerprints, targeting one location | Velocity + geographic clustering abuse detection (Section 11.3) | REAL (demo-scale) |
| Photo/location spoofing to fake a complaint's legitimacy | Uploading unrelated or reused images, faking GPS | EXIF cross-check + perceptual hashing + live-capture-only fallback | REAL |
| Unauthorized cross-department data access | Direct API calls bypassing UI filtering | App-level role middleware (Section 18.2); RLS is the hardening step for pilot | PARTIAL (app-layer only) |
| Retroactive tampering with historical records | Direct database edit bypassing the application | Write-path hash chaining detects a broken chain link | PARTIAL (no scheduled verification sweep yet) |
| Defamatory false-positive on a contractor's public profile | Anomaly detector misfires on a legitimate contractor | Human-review gate before any public flag; explicit "unverified" framing (Section 22.2) | REAL (process control, not a technical filter) |
| PII exposure (citizen photos containing bystanders/minors) | Uploaded images processed and stored | MediaPipe face-blur pipeline before storage | REAL |
| Denial of service on ingestion endpoint | High-volume submission flood | Redis rate limiting; Celery queue absorbs burst without blocking ingestion | REAL |
| Model prompt manipulation (adversarial text in complaint description trying to manipulate the Investigation Agent) | Crafted complaint text designed to alter agent tool-call behavior | Not currently mitigated beyond standard input validation | ROADMAP — flagged honestly as an open item, not glossed over |

### 25.2 What a senior reviewer will specifically probe
1. "What stops someone from submitting a complaint claiming a location they're not at?" → EXIF cross-check, live-capture fallback (Section 11.1)
2. "What stops a department head from seeing another department's budget data?" → app-level role filtering today, RLS as the pilot hardening step (Section 18.3)
3. "What stops someone from editing history to hide a bad decision?" → hash-chained write path; verification sweep is the disclosed gap (Section 17.4)
4. "What happens if your CLIP model is fooled by an adversarial image?" → honestly, this is not specifically hardened for v2.0 — flagged in Section 25.1 as roadmap, since claiming otherwise would not survive scrutiny

### 25.3 Security posture statement for the pitch
> "We've mapped our threat surface explicitly rather than assuming security by omission. Everything on our real-vs-simulated slide has a corresponding entry in our threat model, and where a mitigation isn't built yet, we say so — that's a stronger security posture for a hackathon-stage product than silence."

---

## 26. API Contract Reference

Also absent from v1.0. A senior technical reviewer expects at least a sketch of the actual API surface, not just an architecture diagram.

### 26.1 Complaint submission

```
POST /api/v1/complaints
Content-Type: multipart/form-data

Fields:
  photo: file (required if audio absent)
  audio: file (required if photo absent)
  description_text: string (optional, max 1000 chars)
  latitude: float (required)
  longitude: float (required)
  device_fingerprint: string (required, generated client-side)

Response 202 Accepted:
{
  "complaint_id": "uuid",
  "status": "submitted",
  "estimated_processing_time_seconds": 8
}
```

### 26.2 Complaint status polling / retrieval

```
GET /api/v1/complaints/{complaint_id}

Response 200:
{
  "complaint_id": "uuid",
  "status": "clustered",
  "category": "pothole",
  "classification_confidence": 0.91,
  "cluster_id": "uuid",
  "severity_score": 0.74,
  "severity_breakdown": {
    "visual_damage_score": 0.8,
    "road_class_weight": 0.6,
    "poi_proximity_score": 0.3,
    "cluster_report_count": 0.4
  },
  "work_order_id": null
}
```

### 26.3 WebSocket event contract (drives both the shader and fallback visualization, Section 20)

```
Connection: wss://host/ws/pipeline-events?tenant_id={tenant_id}

Event envelope (all events share this shape):
{
  "event_type": "cluster_match_found",
  "entity_id": "uuid",
  "timestamp": "2026-08-15T10:22:31Z",
  "payload": { ... event-specific fields ... }
}

Example — cluster_match_found (drives the cluster-merge visualization):
{
  "event_type": "cluster_match_found",
  "entity_id": "cluster-uuid",
  "timestamp": "2026-08-15T10:22:31Z",
  "payload": {
    "merged_complaint_ids": ["uuid-1", "uuid-2"],
    "cluster_centroid": { "lat": 18.5204, "lng": 73.8567 },
    "new_confidence": 0.91,
    "new_severity": 0.78
  }
}
```

### 26.4 Public transparency API (ROADMAP, schema described)

```
GET /api/v1/public/ward/{ward_id}/summary
GET /api/v1/public/contractor/{contractor_id}/profile
GET /api/v1/public/budget/{ward_id}?fiscal_year=2026-27

All endpoints: read-only, no auth required, rate-limited (60 req/min/IP),
privacy-scrubbed (no exact GPS, no citizen identifiers).
```

### 26.5 Internal permission middleware pseudocode

```python
async def require_role(request: Request, allowed_roles: list[str]):
    claims = decode_jwt(request.headers["Authorization"])
    if claims["role"] not in allowed_roles:
        raise HTTPException(403, "Insufficient role for this resource")
    if claims["role"] == "department_head":
        request.state.department_filter = claims["department_id"]
    if claims["role"] == "field_staff":
        request.state.assigned_only = claims["user_id"]
    return claims
```

---

## 27. SLAs, Error Budgets & Operational Runbook

Also new in v2.0 — a system that claims to "track SLA" needs its own SLA definitions to be credible.

### 27.1 Complaint-processing SLA tiers (internal system performance, not the citizen-facing work-order SLA)

| Stage | Target latency | Notes |
|---|---|---|
| Submission acknowledgment | < 2 seconds | Synchronous response, before async processing begins |
| Fraud/safety check completion | < 5 seconds | Celery async, safety-critical path prioritized in queue |
| Classification completion | < 8 seconds | CLIP inference, CPU-bound at demo scale |
| Dedup match decision | < 10 seconds | Two-stage query, cheap Stage 1 + selective Stage 2 |
| End-to-end submit → work-order-created (non-ambiguous case) | < 30 seconds | Full pipeline, no Investigation Agent invocation |
| End-to-end with Investigation Agent invoked | < 90 seconds | Includes LLM reasoning + tool-call round trips |

### 27.2 Work-order SLA tiers (citizen-facing, severity-driven)

| Severity tier | Score range | SLA deadline |
|---|---|---|
| Urgent (safety-flagged) | Bypasses scoring | Human review within 15 minutes, target resolution 24 hours |
| High | 0.75–1.0 | 72 hours |
| Medium | 0.40–0.74 | 7 days |
| Low | 0.0–0.39 | 21 days |

### 27.3 Operational runbook — common failure scenarios

**Scenario: Ollama/LLM service unreachable**
1. Investigation Agent invocations fail gracefully — ambiguous cases route directly to human review queue instead of erroring
2. Classification pipeline is unaffected (CLIP does not depend on Ollama)
3. Alert logged as `system_degradation` event, visible on admin dashboard

**Scenario: Database connection pool exhausted**
1. FastAPI returns 503 with retry-after header on new submissions
2. Existing Celery queue continues draining against the same pool once connections free up
3. No data loss — submissions queue client-side with a visible "retrying" state rather than silently failing

**Scenario: WebSocket hub disconnects mid-demo**
1. Frontend falls back to 5-second polling against `GET /api/v1/complaints/{id}` for any in-flight demo complaint
2. Cluster-merge visualization degrades to a manual refresh trigger rather than crashing
3. This fallback should be tested at least once before demo day, not assumed

### 27.4 Demo-day pre-flight checklist (operational, distinct from Section 38's logistics checklist)
1. `docker compose up` completes clean on the actual presenting laptop, timed — should be under 3 minutes
2. Seed script has run and produced the expected complaint/cluster/contractor counts — spot-check 5 records manually
3. WebSocket connection confirmed live in browser dev tools before walking on stage
4. One full run-through of the Section 33.1 demo flow, on battery power, with WiFi disabled — the actual demo conditions, not a rehearsal on a charger with venue WiFi

---

## 28. Unit Economics & Monetization

### 28.1 Cost structure
Self-hosted, open-source inference (CLIP, Whisper, Ollama-run LLMs) carries near-zero marginal cost per complaint — the real cost is server/GPU hosting, roughly $20–50/month for a small instance handling thousands of complaints.

### 28.2 Pricing model
B2G / B2B SaaS. Campus/industrial-park package: flat ₹5,000–15,000/month covering unlimited complaints up to a defined size tier — a believable, statable number for an Indian B2B campus deal, and a materially stronger answer under judge questioning than "we'll figure out pricing later."

### 28.3 Monetization sequencing
1. Campus/industrial-park direct sales (fast cycle, low political risk) — primary near-term revenue
2. Gated communities / RWAs — similar profile, smaller scale
3. Municipal B2G — long sales cycle, high long-term TAM, contractor-transparency module adopted more slowly here for political reasons (explicitly expected, not treated as a flaw)
4. Public API / civil-society tooling — not direct revenue, but strengthens public credibility and adoption pressure on municipal buyers over time

### 28.4 Cost breakdown detail (pilot-stage, monthly, single small municipality/campus tenant)

| Line item | Estimated monthly cost | Notes |
|---|---|---|
| Compute (VM, 4 vCPU / 16GB, GPU-optional) | $25–35 | Sufficient for CLIP/Whisper CPU inference at low-thousands complaint volume |
| Storage (photo/audio, ~30-day retention) | $3–8 | Scales with retention policy (Section 22.4), not total historical volume |
| Bandwidth | $2–5 | Low at this scale, mostly image upload/download |
| Domain + TLS | ~$1 | Negligible, annual cost amortized |
| **Total infra** | **~$30–50/month** | Matches the stated pricing headroom against a ₹5–15k/month package |

---

## 29. Competitive Landscape

| Existing tool/precedent | What it does | What it doesn't do (the gap NEMESIS fills) |
|---|---|---|
| OpenPotholeMap-style projects | Physical infrastructure + CV detection | No clustering/dedup, no severity context, no closure verification |
| Civic data + planning tools | Civic data aggregation + actionable planning | No contractor accountability, no budget transparency |
| Generic workflow automation platforms | Operational workflow automation | Not specialized for civic complaint verification or public trust |
| Municipal complaint apps (e.g. PMC Care-style) | Complaint logging, category selection, status tracking | No clustering, no severity context, no closure verification, no contractor transparency — logs the complaint, doesn't prove resolution |

**Standing differentiation statement:** "Existing civic apps log complaints. NEMESIS is the only one that clusters duplicate reports into confidence-weighted incidents, applies a safety fail-safe that bypasses queues for danger-flagged reports, closes the loop with photo-verified resolution, and makes the contractor and budget behind every fix fully auditable."

---

## 30. Hour-Budgeted Build Plan

This replaces the phase-only plan from v1.0 with concrete hour estimates and a fallback per item, so the team can track actual progress against budget rather than vague phase completion.

| Feature | Real spec | Est. hours | Fallback if behind |
|---|---|---|---|
| Citizen submit | Next.js form + Leaflet + GPS capture | 4–6 | Not cuttable — too core |
| Two-stage dedup engine | PostGIS `ST_DWithin` → pgvector cosine, 0.85 threshold | 8–10 | Never cut — the moat |
| Severity rubric | Weighted formula, JSONB breakdown column | 2–3 | Not cuttable — cheapest high-value item |
| Safety fail-safe | Keyword list + 1 CLIP zero-shot trigger, hardcoded bypass | 3–4 | Not cuttable — highest credibility-per-hour |
| Hash-chain writes only | SHA256 on insert, no verification job | 0.5 | Skip verification job (already scoped as roadmap), keep the write |
| Closure loop | Before/after photo + real SSIM (`scikit-image`) + confirm/dispute | 4–5 | Rule-based pixel-diff placeholder, labeled "simulated" |
| Cluster-merge visualization | Shader primary + CSS/DOM fallback built in parallel | 10–14 (shader) / 3–4 (fallback) | Ship whichever is stable by Day 2 EOD (Section 20) |
| CLIP classification | Zero-shot only, validated against held-out set | 3–4 + 2 for validation | Not cuttable |
| Investigation Agent | One real LangGraph node: evidence-gathering on ambiguous case | 6–8 | Scripted fallback path only if it genuinely breaks — never the first cut |
| App-level RBAC | Role filtering middleware | 2–3 | Not cuttable — needed for department-view demo credibility |
| Repeat-defect clustering | Reuse of dedup engine, filtered by contractor_id | 1–2 | Cut to roadmap if behind — genuinely low cost but non-critical |
| Rate-card deviation flag | Threshold comparison against seeded rate table | 1–2 | Cut to roadmap if behind |
| Underreporting-zone flag | OSM proxy overlay against complaint density | 2–3 | Not cuttable — highest strategic-credibility-per-hour item |

**Total core (non-cuttable) estimate: ~35–45 hours** across a 4-person team running in parallel — comfortably fits a standard hackathon window with buffer for integration and rehearsal.

### 30.1 Day-by-day sequencing (survives partial failure at any checkpoint)

1. **Day 1:** Dedup pipeline + severity rubric — these two alone already exceed every incumbent civic app's technical depth
2. **Day 2:** Safety fail-safe + closure loop + hash-chain writes — now the full trust-and-safety story exists
3. **Day 3:** Start shader AND CSS fallback simultaneously, not sequentially — pick the winner end of day
4. **Day 4:** Investigation Agent (single real evidence-gathering flow) + app-level RBAC
5. **Day 5:** Seed data, demo rehearsal, held-out CLIP validation (Section 32), "what's real vs simulated" slide, threat-model review

If time runs out past Day 2, the team still has a coherent, demoable, complete narrative arc: dedup + severity + safety fail-safe + closure loop, even with a static map and no agent.

### 30.2 If one extra day is available, add-back priority
1. Hash-chain background verification job — now the tamper-evidence claim is fully closed, not partially disclosed
2. A second seeded fraud anomaly, so the billing-anomaly Q&A answer has two concrete examples instead of one
3. Real Postgres RLS on the single highest-risk table (`work_orders` or `budget_allocations`) — "we actually implemented it where it mattered most" is a strong, narrow, honest flex

---

## 31. MVP Cut Line — What Ships vs What's a Slide

**Never cut (core technical claims, always real):**
- PostGIS + pgvector two-stage deduplication (Section 14)
- Severity rubric with logged component breakdown (Section 13)
- Safety fail-safe bypass (Section 11.2)
- Event-sourced log with write-path hash chaining (Section 9.3)
- CLIP zero-shot classification, with a published held-out accuracy number (Section 32)
- The single Investigation Agent's real evidence-gathering behavior (Section 12.4)

**Cut third (build real if time allows, mock clearly-labeled if not):**
- Before/after closure SSIM verification (mock with a rule-based placeholder, state clearly it's simulated)
- Coordinated-abuse detection (toy demo with 3 seeded accounts is sufficient)

**Cut second (roadmap-only if time-constrained):**
- SMS/IVR fallback
- Full Neo4j entity-resolution graph (described + diagrammed if not built — see Section 17.1)
- Dispute/appeal workflow UI
- Hash-chain background verification job

**Cut first (dashboard polish, always secondary to the technical spine):**
- Additional 3D scenes beyond the cluster-merge moment
- Extended cross-ward comparison views
- Public RTI-draft auto-generation (stated feature with a mockup, not a working generator)
- Public transparency API (schema described, not implemented)

**State explicitly in the pitch, regardless of what's cut:** a clear slide listing exactly what's real vs simulated vs roadmap. Judges respect this list far more than discovering it themselves under questioning. See Section 44 for the master reference table this slide is built from.

---

## 32. CLIP Validation Protocol

Absent from v1.0 as a concrete procedure — "we used CLIP zero-shot" is not itself a credibility claim; the measured accuracy number is. This section makes that number a required deliverable, not an afterthought.

### 32.1 Procedure
1. From the 300–500 seeded demo complaints (Section 30, Phase 6 equivalent), hold out a labeled validation subset of at least 60–80 examples, stratified across all five categories (pothole, garbage, streetlight, water leak, illegal dumping)
2. Run CLIP zero-shot classification against this held-out set using the finalized prompt set (Appendix B)
3. Compute precision, recall, and F1 per category — not just overall accuracy, since a single blended number can hide a category that's performing badly
4. Identify the weakest-performing category explicitly — expect this to be garbage-vs-illegal-dumping (visually similar) or streetlight-off-vs-broken (subtle visual distinction)
5. Document the number in the pitch deck (Section 39, slide 6) with the exact prompt set used, so the claim is fully reproducible if challenged

### 32.2 What to do if a category underperforms
- Do not silently drop the category from the demo seed set to inflate the headline number — this is the kind of thing that gets caught and costs more credibility than a mediocre honest number
- Instead, state it directly: "garbage vs. illegal dumping is our weakest category at zero-shot, at approximately [X]%, because the visual distinction is genuinely subtle — this is a named candidate for the fine-tuning investment we deliberately deferred past the hackathon (Section 40)"
- This turns a weakness into evidence of engineering maturity rather than a gap to hide

### 32.3 Minimum acceptable bar
There is no universal "good enough" number to state here without running the actual validation — but as a planning heuristic, anything below ~65% F1 on a category should trigger either a prompt-engineering pass (Appendix B.2) or an honest statement that the category needs a fine-tuned model before pilot deployment, not a claim of production-readiness.

---

## 33. Demo Script & Judging Q&A Prep

### 33.1 Suggested demo flow (backward from the wow-moment)
1. Open on the citizen submission view — submit a live complaint (photo + voice note in Hindi/Marathi) from the citizen view
2. Show it stream through the pipeline in real time via WebSocket-driven map updates
3. Submit 2–3 near-duplicate complaints seeded in advance — trigger the cluster-merge visualization live (shader or CSS fallback, whichever shipped)
4. Trigger a safety-flagged complaint — show the immediate escalation and explicitly narrate the deterministic (not ML-scored) design choice out loud
5. Trigger an ambiguous case that routes to the Investigation Agent — narrate its tool calls live as they happen (this is the single most important beat to rehearse, since it's the actual "is this an agent" proof)
6. Switch to department view — show the role-filtered kanban queue, assign a contractor, log a budget estimate
7. Show a pre-seeded closed work order with SSIM-verified before/after photos and citizen confirmation
8. Switch to the contractor's public profile — show computed reputation metrics and one clearly-labeled system-flagged anomaly with its "unverified, under review" disclaimer
9. Close on the ward-level budget transparency page

### 33.2 Anticipated questions and prepared answers

| Question | Answer |
|---|---|
| How do you dedupe complaints? | Two-stage: PostGIS radius filter, then pgvector cosine similarity on image + text embeddings, threshold-gated with human review for ambiguous cases |
| How do you know severity is accurate? | Transparent weighted rubric with logged component scores, versioned, calibrated against resolution-time outcomes over time — not a black-box model |
| What stops fake reports? | EXIF/GPS cross-check, perceptual image hashing, submission velocity monitoring, live-camera-only fallback when EXIF is stripped |
| Why not just use [existing municipal app]? | Existing apps log complaints; NEMESIS clusters, verifies closure, and makes contractor/budget accountability public — the standing differentiation statement (Section 4.4) |
| Is this really an "agent"? | Yes, specifically the Investigation Agent — it actively gathers additional evidence (requests photos, queries OSM, checks history) on ambiguous cases via genuine multi-step tool use, not a fixed rule path. The rest of the pipeline is honestly deterministic, by design (Section 12.1). |
| What about the four-agent architecture in your docs? | We deliberately scoped down from four thin agents to one genuinely agentic component after review — a system that can't survive "walk me through what it decided" isn't actually agentic, it's a function with an LLM call in it. One real agent beats four thin claims. |
| What about defamation risk on contractor profiles? | All flags are explicitly labeled "system-flagged, unverified, under review" until human-confirmed; nothing is published as settled fact |
| What about reporting bias / equity? | Underreporting-zone flag cross-references OSM infrastructure-age proxies against complaint density to surface likely-underreported areas |
| How do you know the data wasn't tampered with? | Every event is hash-chained at write time; the periodic full-chain integrity sweep is our documented next hardening step, not yet scheduled as a running job — we say this directly rather than overclaiming full tamper-proofing |
| Why app-level RBAC instead of database RLS? | Deliberate build-time trade-off; the migration path is well-defined given our schema (Section 18.3) and is the first hardening step before any real pilot |
| Did you fine-tune your CV model? | No — deliberately cut. Zero-shot CLIP, validated against a held-out set at [X]% F1 per category (Section 32), was the correct trade given the time budget; fine-tuning is a stated roadmap item if a category's accuracy demands it |
| What's your unit economics? | Near-zero marginal inference cost (self-hosted open-source models), ~$30–50/month hosting at small scale, ₹5–15k/month campus pricing tier |
| Would a real municipality adopt this? | Complaint-routing side: fast adoption everywhere. Contractor-transparency side: fast for campuses, slower for municipalities due to procurement-relationship sensitivity — a known, expected, three-stage GTM |

---

## 34. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Government sales cycle is slow | High | Medium | Don't make govt procurement the MVP dependency; lead GTM with campus/industrial persona |
| Defamation exposure from contractor flags | Medium | High | Human-review gate before public flagging; explicit "unverified" framing |
| Political resistance to contractor transparency | High (municipal context) | Medium | Dispute/appeal workflow for fairness; positive-framed default metrics; phased GTM avoiding municipal-first contractor-transparency sales |
| Reporting bias reinforcing inequality | Medium | Medium-High | Underreporting-zone dashboard flag; explicit statement of the limitation in any pitch |
| Coordinated data manipulation (fake reports, brigading) | Medium | Medium | Velocity/pattern-based abuse detection; human review queue |
| DPDP / data-localization non-compliance | Low if addressed | High if ignored | Face-blur, GPS coarsening, retention limits, India-region hosting |
| Over-scoped build vs available time | High | High | Explicit hour-budgeted cut line (Sections 30–31); "real vs simulated" slide stated proactively |
| Demo-day technical failure (live API, WiFi dependency) | Medium if not addressed | High | Fully offline, self-hosted, Docker Compose demo environment; no paid API dependency |
| GPU/WebGL failure on demo laptop | Low-Medium | Medium | Reduced-motion/static fallback for the 3D scene (Section 20.4); CSS fallback path built in parallel, not as a panic move |
| Existing incumbent apps raised as "already solved" | High (if judge is government-aware) | Medium | Pre-researched differentiation statement, screenshot of incumbent's actual limited scope ready |
| Overclaiming a described-but-unbuilt component under questioning | Medium | High (credibility) | Section 44 master table; every claim in the pitch script cross-checked against actual build status before demo day |
| Investigation Agent demo fails live (LLM timeout, unexpected tool-call failure) | Low-Medium | Medium | Rehearsed fallback: pre-recorded clip of a successful run, shown transparently as "here's a recorded run in case of live latency" rather than pretending a failure didn't happen |

---

## 35. Change Log — v1.0 to v2.0

| Area | v1.0 | v2.0 | Why |
|---|---|---|---|
| Product name | Unnamed ("AI Civic Operations Agent") | **NEMESIS** | Brand identity + internal engineering-discipline shorthand (Section 2) |
| Agent architecture | Four persistent LangGraph agents (Intake, Classification, Investigation, Ops) | One genuinely agentic component (Investigation Agent); the other three reframed honestly as deterministic pipeline stages | Four thin agent claims don't survive "walk me through what it decided"; one real agent does (Section 12) |
| CV model | YOLOv8n fine-tuned on RDD2022 + CLIP zero-shot for other categories | CLIP zero-shot only, across all categories, with a mandatory published validation number | Fine-tuning was the largest time sink for the smallest demo-perceivable payoff (Section 32) |
| RBAC | Postgres RLS stated as built | App-level role filtering, with RLS as a documented, honest migration path | Real RLS for an unstressed demo is misallocated time; the honest framing survives questioning better than an overclaim (Section 18.3) |
| Hash chaining | Full chain + verification job implied as built | Write-path hash chaining kept as REAL (cheap, high-value); background verification job explicitly marked ROADMAP | Decoupling the cheap part from the expensive part preserves a true, specific claim instead of an all-or-nothing one (Section 17.4) |
| 3D/shader scenes | Four scenes (hero particle field, cluster-merge, safety pulse, closure dissolve) | One scene (cluster-merge), with a CSS/DOM fallback built in parallel from hour one | Shader risk is not equivalent to backend risk; one scene done well plus a guaranteed fallback beats four scenes with unclear demo-day survival (Section 20) |
| Security | No explicit threat model | Full threat model (Section 25) mapping every major vector to a mitigation or an honestly disclosed gap | A senior reviewer expects this section to exist; its absence in v1.0 was itself a gap |
| API contracts | Described only via architecture diagram | Concrete endpoint/payload examples (Section 26) | Technical credibility requires a sketch of the actual surface, not just a box diagram |
| SLAs/runbook | Not present | Section 27 — explicit internal-processing SLAs, work-order SLAs, and a failure-scenario runbook | A system claiming to "track SLA" needs its own SLA definitions and failure handling documented |
| Build plan | Phase-based only, no hour estimates | Hour-budgeted per feature, with a stated fallback per item (Section 30) | Enables real progress tracking against budget, not vague phase completion |
| CLIP validation | Mentioned as a testing strategy line item | Full validation protocol as its own section, with a stated procedure for handling underperforming categories (Section 32) | "We used CLIP" is not a credibility claim without a measured number attached |
| Real/Simulated/Roadmap labeling | One summary slide (Section 26 in v1.0) | Every section header and every table row labeled individually; master table in Appendix C (Section 44) | The Nemesis standard applied to the document itself — no claim without a checkable status |

---

## 36. Full Problem-to-Fix Traceability Index

This index maps every gap identified through iterative review — both the original v1.0 review and the v2.0 senior pass — to its corresponding fix and the section where it's addressed.

| # | Problem identified | Fix | Section |
|---|---|---|---|
| 1 | Saturated category, no real moat | Proprietary operational dataset as long-term moat | 4.3 |
| 2 | Dedup/severity underspecified | Two-stage geo+semantic dedup; transparent weighted severity rubric | 13, 14 |
| 3 | No data source named for MVP | Public datasets + geo-jittered synthetic seed set | 30 (Day 5) |
| 4 | Generic department routing | Real tenant-specific ward/department config | 10, 15 |
| 5 | Resume-value framing in pitch | Removed from pitch entirely; replaced with unit economics | 28 |
| 6 | Thin demo data volume | 300–500 seeded complaints, realistic category ratios | 30 |
| 7 | No anti-fraud layer | EXIF check, perceptual hashing, live-capture fallback | 11.1 |
| 8 | No severity ground truth | Transparent rubric, explicit non-ML-model framing | 13.2 |
| 9 | No closure loop | Photo-verified, citizen-confirmed closure workflow | 21.2 |
| 10 | No single buyer persona | Campus/industrial park as primary MVP wedge | 5 |
| 11 | No language/voice support | faster-whisper Hindi/Marathi transcription | 8.4 |
| 12 | No competitive differentiation line | Standing differentiation statement | 4.4, 29 |
| 13 | "Agent" claim not substantiated | Investigation Agent's genuine multi-step evidence-gathering, reframed as the sole agentic claim | 12.4 |
| 14 | No safety fail-safe for danger signals | Hardcoded deterministic bypass trigger | 11.2 |
| 15 | Incumbent apps not addressed | Explicit gap statement + screenshot prep | 29, 33.2 |
| 16 | DPDP / data privacy exposure | Face-blur, retention limits, GPS coarsening, India hosting | 22.1, 22.4 |
| 17 | Connectivity assumptions unrealistic | PWA/offline queue scoped to roadmap | 8.7 |
| 18 | Political sensitivity of public dashboards | Positive-framed default metrics, drill-down for raw data | 23.3 |
| 19 | No unit economics | Self-hosted cost model + campus pricing tier | 28 |
| 20 | Engagement decay after launch | Resolution-streak retention mechanic tied to closure loop | 21.3 |
| 21 | Real vs mocked components unclear | Explicit per-section "what's real / simulated / roadmap" labeling throughout | 31, 44 |
| 22 | Reporting bias / equity gap | Underreporting-zone dashboard flag via OSM proxy | 23.1–23.2 |
| 23 | Closure trusted on honor system | SSIM before/after verification | 21.2 |
| 24 | No integration/replace-vs-augment stance | "Intelligence layer, not replacement" positioning | 4.4 |
| 25 | Coordinated abuse not modeled | Submitter-velocity + geographic-clustering abuse detection | 11.3 |
| 26 | No memorable demo visual moment | Cluster-merge visualization as core wow-beat, with a guaranteed fallback | 20 |
| 27 | Severity ground truth (duplicate of #8) | See #8 | 13.2 |
| 28 | Hackathon artifact vs real company ambiguity | Explicit scope decision documented in team plan | 37 |
| 29 | No team execution/cut-order plan | Hour-budgeted cut-line ordering | 30–31 |
| 30 | No liability plan for false negatives | Safety fail-safe as deterministic override | 11.2 |
| 31 | No stated incumbent-vs-new positioning | Standing differentiation statement | 29 |
| 32 | No plan for who audits admin actions | Hash-chained log covers admin actions too | 17.7 |
| 33 | Contractor network / shell-entity risk unaddressed | Neo4j entity-resolution graph (described + diagrammed) | 17.1 |
| 34 | Billing/ghost-work fraud unaddressed | Quantity-vs-photo + rate-card deviation anomaly detection | 17.2 |
| 35 | Lump-sum payment enables fraud | Milestone-based fund release (simulated financial model, real data structure) | 15.5, 17.3 |
| 36 | Data tamper risk on financial/audit records | SHA-256 hash-chained event log (write path real, verification roadmap) | 9.3, 17.4 |
| 37 | No fund-source/scheme-level tracing | `funding_source` tagging on work orders | 17.6 |
| 38 | No public accountability API | Schema described (roadmap for implementation) | 16.3, 26.4 |
| 39 | Repeat-defect pattern not tracked per contractor | Contractor-specific repeat-defect clustering, reusing dedup engine | 17.5 |
| 40 | Seasonal unfairness in performance scoring | Weather-context-normalized SLA scoring | 16.4 |
| 41 | One-sided accountability invites resistance | Contractor dispute/appeal workflow (roadmap for demo, core for pilot) | 16.4 |
| 42 | Cross-ward systemic patterns invisible | Internal cross-ward contractor comparison view (roadmap) | 16.4 |
| 43 | Defamation/legal exposure on public profiles | Human-review gate, explicit "unverified" framing | 22.2 |
| 44 | No RBAC/department-side design | Full role matrix, app-level enforcement, RLS migration path | 18 |
| 45 | Existing procurement/e-tender data landscape unacknowledged | Positioned as cross-referencing layer, not procurement replacement | 22 (contextual), 29 |
| 46 | Four-agent claim doesn't survive technical scrutiny | Scoped to one genuinely agentic component | 12 |
| 47 | No security threat model | Full threat model with per-vector mitigation/gap status | 25 |
| 48 | No API contract specification | Concrete endpoint/payload/WebSocket examples | 26 |
| 49 | No SLA definitions for the system itself | Internal processing SLAs + work-order SLAs + runbook | 27 |
| 50 | No hour-level build accountability | Hour-budgeted feature table with fallback per item | 30 |
| 51 | CLIP accuracy claimed without a validation procedure | Full validation protocol, mandatory published number | 32 |
| 52 | Shader scene treated as equal-certainty to backend engineering | Parallel-built fallback path, explicit risk framing | 20 |
| 53 | RLS claimed as a differentiator without being built | Honest app-level framing + documented migration path | 18.3 |

---

## 37. Team Execution Plan & RACI

### 37.1 Role assignment (for a 4-person team; scale proportionally)

| Owner | Owns | Never cut, always deep | First to mock/simplify if behind |
|---|---|---|---|
| Person A — CV/ML | CLIP zero-shot integration, prompt engineering (Appendix B), faster-whisper integration, validation protocol (Section 32) | Classification accuracy, held-out test reporting | Voice-note multilingual coverage beyond Hindi/Marathi |
| Person B — Backend/data | FastAPI, Celery, Postgres schema, event sourcing, hash-chain writes, dedup engine | Event log integrity, two-stage dedup pipeline | Hash-chain verification job (already roadmap-scoped) |
| Person C — Agents/fraud logic | LangGraph Investigation Agent, severity rubric, rate-card deviation flag, Neo4j graph design (diagram only) | Investigation Agent, severity breakdown logging | Neo4j graph implementation (present as design diagram) |
| Person D — Frontend/3D | Next.js app, Leaflet map, cluster-merge scene (shader + CSS fallback in parallel), department kanban UI | Cluster-merge visualization (whichever path ships), department queue UI | Additional 3D scenes (already cut to roadmap) |

### 37.2 RACI matrix for cross-cutting concerns

| Activity | Responsible | Accountable | Consulted | Informed |
|---|---|---|---|---|
| Real-vs-simulated status accuracy (Appendix C) | All 4 | Team lead | — | Judges (via pitch slide) |
| Demo rehearsal | Person D (staging) | Team lead | All 4 | — |
| Threat model review (Section 25) | Person B | Team lead | Person C | All 4 |
| Pitch script accuracy against actual build | Team lead | Team lead | All 4 | Judges |
| CLIP validation number (Section 32) | Person A | Person A | Team lead | All 4 |

### 37.3 Daily sync discipline
Even with unconstrained time, run short daily syncs against the hour-budgeted plan (Section 30) — the risk with "we have time" is silent scope drift, not literal lack of hours. Each sync should answer: what shipped real (not mocked) yesterday, what's blocking the critical path (dedup, event schema), and does today's work map to a budgeted line item or is it decorative.

### 37.4 Definition of done, per feature
A feature is not "done" until:
1. It runs against real (or realistically seeded) data, not a hardcoded demo path
2. It's covered by at least one `pytest` case or a held-out accuracy check
3. It's reflected in the event log correctly (state transitions fire the right events)
4. It survives a "kill the service and restart" test without losing state (Postgres is the source of truth, not in-memory)
5. Its status in Appendix C (Section 44) has been updated to match reality — this step is not optional and is the final gate before a feature is considered demo-ready

---

## 38. Hackathon-Day Logistics

### 38.1 Pre-event checklist
- Full stack runs via a single `docker compose up` with zero internet dependency (Ollama model weights, CLIP/Whisper weights all pre-downloaded and baked into images or local volumes ahead of time)
- Seed script (300–500 synthetic complaints with planted anomalies) runs once at container startup, deterministic and repeatable
- A recorded backup video of the full demo flow exists in case of live technical failure — never rely solely on a live run for a judged demo
- Laptop fully charged, backup power bank, and a tested HDMI/USB-C adapter for the venue's screen — this sounds trivial and is the single most common actual cause of demo failure

### 38.2 Presentation structure
Keep the live demo to 90 seconds of core flow (Section 33.1) inside a longer talk — the rest of the time slot is narrative, differentiation, and Q&A, not more clicking through screens.

### 38.3 What to physically bring
- Printed or slide-embedded screenshot of the incumbent app for the differentiation question
- A one-page printed architecture diagram as a leave-behind if judges want to review offline
- The "what's real vs simulated vs roadmap" slide, ready to show proactively rather than waiting to be asked
- A printed copy of Section 44's master table, for the team's own reference during Q&A — never guess a status live when it's already documented

---

## 39. Pitch Deck Outline

A suggested slide sequence, built backward from the differentiation spine (Section 4.4) rather than a generic problem-solution template:

1. **Title** — NEMESIS, one-sentence pitch (Section 1)
2. **The gap, not the problem** — don't spend time re-explaining that potholes exist; spend it on "every existing tool stops at 'routed to department' — here's what happens after that in the real world" (Section 3.1, the trust-collapse mechanism)
3. **The one sentence** — the standing differentiation statement (Section 4.4), on its own slide, nothing else
4. **Live demo** — 90 seconds, the flow from Section 33.1
5. **How dedup actually works** — the two-stage PostGIS + pgvector mechanism (Section 14), because judges will ask and a slide pre-empts a shaky live explanation
6. **How severity is scored** — the transparent rubric with a real logged example (Section 13), explicitly framed as rules+CV, not an oversold "AI model"
7. **Is this really an agent?** — the Investigation Agent's genuine tool-call sequence (Section 12.4), with the honest note that the rest of the pipeline is deliberately deterministic
8. **The closure loop** — before/after SSIM verification + citizen confirmation (Section 21.2), framed as "why this doesn't die like every prior civic app" (Section 3.1)
9. **Contractor transparency** — public profile mockup + the entity-resolution graph concept (Section 16–17), framed as the "beyond the hackathon" ambition slide
10. **What's real vs simulated vs roadmap** — the honest build-status slide (Section 31, backed by Section 44), shown proactively
11. **Architecture** — the one-diagram version of Section 7.2, simplified for a non-technical judge to follow in 10 seconds
12. **Market & GTM** — campus-first wedge, municipal long-term TAM (Section 5, 28.3)
13. **Risks we've thought about** — pick 2–3 from Section 34 (political sensitivity, defamation risk, equity/bias) and state the mitigation in one line each — this consistently reads as more credible than a risk-free pitch
14. **What's next** — the 90-day roadmap (Section 40)
15. **Ask / close** — whatever the specific hackathon's format requires (prize, mentorship, pilot conversation)

---

## 40. Post-Hackathon Roadmap — 90-Day Plan

New in v2.0 — a senior review expects a credible next-90-days plan, not just a hackathon artifact with no forward motion.

### 40.1 Days 1–30 — Harden what's real
- Schedule the hash-chain verification job (closes the Section 17.4 gap)
- Migrate the highest-risk tables to Postgres RLS (closes the Section 18.3 gap on the tables that matter most)
- Run CLIP validation against a larger, real-world (not synthetic) photo set from a pilot partner if one exists; fine-tune only the specific category that underperforms, if any (per Section 32.2)
- Build the public transparency API (Section 26.4) — no longer optional once a pilot partner exists to consume it

### 40.2 Days 31–60 — Pilot with one real tenant
- Deploy to a single campus or industrial-park pilot partner (per Section 5's GTM sequencing)
- Real milestone-based fund release integration — move from SIMULATED (Section 15.5) to REAL, scoped to the pilot partner's actual approval workflow, not a full payment-rail integration yet
- Contractor dispute/appeal workflow — move from ROADMAP to REAL, since a live pilot makes this a fairness requirement, not a nice-to-have
- Begin collecting real resolution-time data to calibrate the severity rubric (Section 13.3) against actual outcomes

### 40.3 Days 61–90 — Prepare for the second persona
- Neo4j entity-resolution graph — move from diagram-only to a real, queryable implementation, informed by whatever real contractor-network patterns emerged from the pilot
- SMS fallback integration (Twilio/Exotel) — evaluate cost against pilot-partner citizen digital-access data; only build if the pilot's own usage data justifies it
- Draft the municipal (B2G) sales narrative using real pilot data as the proof point — "here's what happened over 60 days at a real deployment" is a materially stronger municipal pitch than a hackathon demo alone

---

## 41. KPI Dashboard Specification

New in v2.0. The system's own success should be measurable against a defined KPI set from day one, not retrofitted later.

### 41.1 Product health KPIs

| KPI | Definition | Target (pilot stage) |
|---|---|---|
| Submission-to-classification latency | Time from `complaint_submitted` to `classification_scored` | < 8 seconds, p95 |
| Dedup precision | % of auto-merged clusters that are genuinely the same underlying issue (human-audited sample) | > 90% |
| Dedup recall | % of true duplicates that were successfully merged (not left as separate complaints) | > 80% |
| Safety fail-safe false-negative rate | % of genuinely dangerous reports that did NOT trigger the bypass | 0% — this is a hard requirement, not a target range |
| Closure confirmation rate | % of "resolved" work orders that citizens actively confirmed (vs. auto-confirmed-unconfirmed) | > 60% within 48hr window |
| Investigation Agent invocation rate | % of complaints requiring agent escalation vs. handled by the deterministic pipeline | Track as a baseline, no target — informs whether dedup thresholds need retuning |

### 41.2 Business/adoption KPIs (post-pilot)

| KPI | Definition | Notes |
|---|---|---|
| Citizen resubmission rate | % of citizens submitting a second report after their first closure | Proxy for trust — the Section 3.1 trust-collapse mechanism inverted as a success metric |
| Contractor dispute rate | % of flagged anomalies contested by contractors | Track alongside dispute *outcome* rate — high contestation with high overturn rate signals a miscalibrated detector, not contractor bad faith |
| Underreporting-zone coverage change | Change in complaint density in flagged underreporting zones over time | Direct measure of whether the equity mitigation (Section 23.2) is working |

---

## 42. Appendix A — Glossary

- **NEMESIS:** The product name — see Section 2 for the full naming rationale.
- **EXIF:** Metadata embedded in photo files, including GPS coordinates, camera settings, and timestamp — used here for location fraud-checking.
- **PostGIS:** A PostgreSQL extension adding geospatial query capability (distance, containment, radius queries).
- **pgvector:** A PostgreSQL extension enabling vector similarity search (embeddings) directly in the relational database.
- **RLS (Row-Level Security):** A database-layer access control mechanism restricting which rows a given database role can see or modify, enforced below the application layer. Roadmap for NEMESIS's demo build; see Section 18.3.
- **SSIM (Structural Similarity Index):** An image-comparison metric used to detect meaningful visual change between two photos — used here for before/after closure verification.
- **DBSCAN:** A density-based clustering algorithm used for spatial-temporal grouping of near-duplicate complaints.
- **LangGraph:** A graph-based orchestration framework for building stateful, multi-step, checkpointed AI agent workflows.
- **Hash chaining:** A tamper-evidence technique where each record includes a cryptographic hash of the prior record, making retroactive edits provably detectable at the write-record level.
- **Entity resolution:** The technique of identifying when multiple recorded entities (e.g., differently-named contractors) actually refer to the same underlying real-world actor, typically via shared attributes (address, phone, director).
- **DPDP Act:** India's Digital Personal Data Protection Act, governing consent, retention, and handling of personal data.
- **RTI Act:** India's Right to Information Act, the legal mechanism citizens use to request official government records.
- **Milestone-based fund release:** A payment structure where funds are disbursed in stages tied to verified evidence of progress, rather than as a single lump sum. Simulated in the NEMESIS demo build; see Section 15.5.
- **CLIP:** An open-source vision-language model capable of zero-shot image classification against arbitrary text prompts, without task-specific training.
- **Zero-shot classification:** Classifying inputs into categories the model was never explicitly fine-tuned on, by comparing against natural-language category descriptions.
- **Whisper / faster-whisper:** An open-source speech-to-text model (and an optimized inference implementation) supporting multilingual transcription, including Hindi and Marathi.
- **The Nemesis standard:** Internal shorthand (Section 2) for the product's core discipline — no claim without a logged, checkable evidence trail.
- **REAL / SIMULATED / ROADMAP:** The three-way status label used throughout this document (Section 44) to disambiguate what is actually built, what is a labeled stand-in for a future real component, and what is described but not implemented.

---

## 43. Appendix B — Reference Prompts & Rubric Config

### 43.1 CLIP zero-shot prompt set (starting configuration, subject to validation tuning per Section 32)

```yaml
categories:
  pothole:
    prompts:
      - "a photo of a pothole in a road"
      - "damaged asphalt road surface with a hole"
      - "a cracked and broken road surface"
  garbage:
    prompts:
      - "a photo of overflowing garbage or trash"
      - "an uncollected pile of waste on a street"
      - "an overflowing public garbage bin"
  streetlight:
    prompts:
      - "a photo of a broken or non-functional streetlight"
      - "a damaged street lamp pole"
      - "a streetlight that appears to be off or malfunctioning"
  water_leak:
    prompts:
      - "a photo of a water leak on a street or pipe"
      - "standing water from a burst pipe"
      - "a leaking water main or tap"
  illegal_dumping:
    prompts:
      - "a photo of illegally dumped waste or construction debris"
      - "trash dumped in a vacant lot or open area"
      - "an unauthorized waste dumping site"

safety_trigger_visual_prompts:
  - "exposed electrical wiring, dangerous"
  - "a structural collapse of a building or wall"
  - "active flooding of a street or area"

safety_trigger_keywords:
  - "live wire"
  - "gas leak"
  - "collapsed"
  - "sparking"
  - "flooding"
```

### 43.2 Prompt-engineering guidance if a category underperforms in validation (Section 32.2)
- Add 2–3 additional descriptive prompt variants per weak category before considering fine-tuning — zero-shot accuracy is often more sensitive to prompt phrasing than to the underlying model's capability ceiling
- Test negative prompts (e.g., "a normal, undamaged streetlight" as a contrasting class) to sharpen the decision boundary between visually similar categories
- Document every prompt-set version change as its own event, mirroring the severity rubric versioning discipline (Section 24.4)

### 43.3 Severity rubric default configuration (see Section 13.5 for weight table)

```yaml
severity_rubric_version: v1
weights:
  visual_damage_score: 0.40
  road_class_weight: 0.25
  poi_proximity_score: 0.20
  cluster_report_count: 0.15

road_class_lookup:
  primary: 1.0
  secondary: 0.8
  residential: 0.5
  footway: 0.3
  unclassified: 0.4

poi_proximity_decay:
  type: linear
  full_score_radius_m: 0
  zero_score_radius_m: 200
  relevant_amenities: ["school", "hospital", "clinic"]

cluster_report_count_cap: 10
```

---

## 44. Appendix C — Real vs Simulated vs Roadmap Master Table

This is the single source of truth for every status claim made throughout this document. Before any pitch, demo, or technical review, cross-check every stated claim against this table. No section elsewhere in this blueprint should contradict this table — if it does, this table wins, and the other section should be corrected.

| Component | Status | Section | One-line justification |
|---|---|---|---|
| Citizen submission (photo/text/GPS) | REAL | 8.1 | Core, standard web form + upload |
| Two-stage PostGIS + pgvector dedup | REAL | 14 | The core moat, never cut |
| Severity rubric with logged breakdown | REAL | 13 | Cheap arithmetic, high credibility |
| Safety fail-safe (keyword + CLIP trigger) | REAL | 11.2 | Deterministic bypass, few hours of work |
| Hash-chain writes | REAL | 9.3 | Cheap, write-path only |
| Hash-chain background verification job | ROADMAP | 17.4 | Expensive, low demo-perceivable payoff |
| CLIP zero-shot classification (all categories) | REAL | 8.4 | With mandatory published validation number |
| Fine-tuned YOLO detection | CUT PERMANENTLY (for MVP) | 8.4, 8.7 | Largest time sink, smallest demo payoff |
| faster-whisper (Hindi/Marathi) | REAL | 8.4 | Scoped to two languages for demo |
| Multilingual expansion beyond Hindi/Marathi | ROADMAP | 8.7 | Not needed for demo scope |
| Investigation Agent (single LangGraph node) | REAL | 12.4 | The one genuine agentic claim |
| Intake/Classification/Ops as "agents" | REFRAMED — deterministic pipeline stages | 12.3 | Honest naming correction from v1.0 |
| Cluster-merge shader scene | REAL (primary path, contingent on team skill) | 20.1 | Built in parallel with fallback |
| Cluster-merge CSS/DOM fallback | REAL | 20.2 | Guaranteed-to-ship safety net |
| Hero particle field / safety-pulse / closure-dissolve shaders | ROADMAP | 8.7, 19.1 | Cut to focus on one scene done well |
| Before/after SSIM closure verification | REAL | 21.2 | scikit-image, cheap and real |
| Citizen confirm/dispute closure loop | REAL | 21.2 | Core to the trust-collapse fix |
| App-level RBAC | REAL | 18.2 | Middleware role-check |
| Postgres RLS | ROADMAP (documented migration path) | 18.3 | Time better spent elsewhere for demo |
| Coordinated abuse detection | REAL (demo-scale, 3 seeded accounts) | 11.3 | Toy demo sufficient, not production-robust |
| Neo4j entity-resolution graph | ROADMAP (diagram + worked example only) | 17.1 | Near-zero live-demo payoff vs. cost |
| Rate-card deviation billing anomaly | REAL | 17.2 | Simple threshold comparison |
| Quantity-vs-photo billing anomaly | SIMULATED | 17.2 | Seeded/planted example, not real segmentation pipeline |
| Milestone-based fund release | SIMULATED (real data model, simulated disbursement) | 15.5 | No real payment rail at demo/pilot-zero stage |
| Repeat-defect clustering (contractor-specific) | REAL | 17.5 | Reuses the dedup engine, cheap to add |
| Fund-source/scheme tagging | REAL | 17.6 | Schema field + query, not a subsystem |
| Contractor public profile + reputation metrics | REAL | 16.1 | Computed from real event data |
| Contractor dispute/appeal workflow | ROADMAP (mockup for demo) | 16.4 | Core for pilot, not for hackathon demo |
| Seasonal/context SLA normalization | ROADMAP (designed, not implemented) | 16.4, 13.4 | Concept documented, implementation deferred |
| Cross-ward internal comparison view | ROADMAP | 16.4 | Deferred, not needed for demo narrative |
| RTI-draft auto-fill button | SIMULATED (template auto-fill only) | 16.1 | No real filing integration |
| Public read-only transparency API | ROADMAP (schema described) | 16.3, 26.4 | No live-demo audience for this endpoint |
| Underreporting-zone equity flag | REAL | 23.2 | High strategic value, cheap GIS overlay |
| Resolution-streak retention mechanic | ROADMAP | 21.3 | Cheap but non-critical, post-pilot |
| SMS/IVR notification fallback | ROADMAP | 21.1 | Real cost, deferred pending pilot usage data |
| Native mobile app / PWA offline queue | ROADMAP | 8.7 | Out of scope for laptop-only demo |
| Security threat model | REAL (documented) | 25 | Analysis complete; some listed mitigations are themselves roadmap items, clearly marked |
| API contract documentation | REAL (documented) | 26 | Sketch of actual implemented surface |
| SLA definitions + operational runbook | REAL (documented) | 27 | Defines targets; achievement of every target tracked post-pilot |

---

*End of blueprint. NEMESIS v2.0 is the single source of truth for the product design, rebuilt under senior review discipline: every architectural decision is traceable to a specific identified problem (Section 36), every build item carries an honest status label (Section 44), and every claim in the pitch script is required to trace back to something in this document before it is ever said out loud to a judge, investor, or pilot partner.*
