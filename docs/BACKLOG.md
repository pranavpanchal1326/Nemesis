# NEMESIS — Backend Backlog

**Companion to [HANDOVER.md](HANDOVER.md).** That document tells you where the
system is and how to work in it. This one tells you what is left, broken into
items you can start on a Monday morning.

**Scope:** backend only. Track E (Phases 18–22 — app shell, design system, i18n,
3D engine, shader scenes, temporal replay, PWA) belongs to the project owner and
is deliberately absent. Where a backend item exists *to serve* a Track E phase,
it is listed here and marked **[serves FE]**.

**Effort scale.** S = under a day. M = 2–4 days. L = 1–2 weeks. XL = 2 weeks+.
Estimates assume one engineer already familiar with the codebase; add
significantly for the first item you touch.

---

## Priority 0 — do these first

### B-10.1 · Build the photograph corpus · **XL** · unblocks G1, Phase 10, Phase 11 · **[serves FE]**

The single highest-value item in this backlog. Two phases now depend on it, a
third is about to, and **Track E's Phase 20 gate now does too.**

The frontend's landing film asserts that its merge scene renders nothing until a
real `cluster_match_found` arrives — and the case that would watch one arrive
cannot run, because the only committed test photograph is procedurally drawn and
scores 0.142 against the classifier's 0.150 floor, so the report parks before
deduplication. Two *photographed* images of one defect would take that gate.
See [`reports/story-merge-gate.md`](reports/story-merge-gate.md); the frontend
needs a handful of images from this corpus, not the whole of it, so a partial
delivery is genuinely useful here.

The image modality has never been measured. CLIP prompt sets ship unvalidated,
dedup's `image_weight` has never been exercised, and Phase 10's gate fails
specifically because text alone cannot separate two potholes thirty metres apart.

**Do**
- Source a licence-clean set of photographed civic defects. Options, in order of preference: (a) an open municipal dataset with a redistributable licence; (b) a commissioned shoot in one city with model releases; (c) a partner municipality's archive under a data agreement. **Record the licence in the corpus file.**
- Do not render synthetic street scenes. That measures the renderer, and the repository has already declined this once for exactly that reason.
- Label two ways, because two phases need different things: **category** (extends `perception/corpora/municipality-v1.json`, for Phase 9's F1) and **incident identity** (extends `dedup/corpora/municipality-dedup-v1.json`, for Phase 10's precision/recall).
- **Add a calibration / held-out split to the dedup corpus.** Its absence is why no remedy could be adopted in Phase 10 — see `reports/dedup-precision-recall.md`. Follow the pattern `perception/corpus.py` already implements.
- Re-run `nem f1` and `nem dedup-eval`; publish both.

**Done when** the image modality has a published per-category F1, dedup precision/recall is measured with image embeddings live, and both reports state their corpus licence.

---

### B-10.2 · Re-tune the point-defect dedup radius · **S** · blocked by B-10.1

The baseline `DedupBand` gives every category a 50 m radius. A pothole is a metre
across; fifty metres of road holds many of them, and within-incident jitter in
the current corpus never exceeds 12 m.

**Do**
- Fit a per-category radius on the *calibration* split only, then re-measure on held-out.
- Update `policy/baselines.py`. It is an exempt module in `check_domain_literals.py` precisely because it holds baseline documents a tenant may override.
- Re-run `nem gate-phase10`.

**Do not** start this before B-10.1 delivers a split. Tuning against the only
measurement available publishes a number about the tuning.

---

### B-G2 · Close the §22.1 distant-face gap · **M** · privacy obligation

Measured recall is **0.00 for faces below 80 px** and 1.00 at and above it.
Bystanders in the background of a street photograph are not being blurred.

**Do**
- Evaluate a tiled detection pass (slice the image, detect per tile, merge boxes) against `blaze_face_short_range` — cheapest option, no new model.
- If tiling is insufficient, add a second detector (`blaze_face_full_range` or an equivalent) behind the existing `detector_scope` seam.
- Re-measure on the same harness curve. The curve already exists; only the detector changes.
- Watch the §27.1 budget: a tiled pass multiplies inference cost per image.

**Done when** full recall extends to a face width defensible for street photography, and the number is republished.

---

## Priority 1 — Track C · Intelligence

### Phase 12 · Severity, routing & SLA engine · **XL** · no image dependency

**Start here if B-10.1 is blocked on sourcing.** Phases 6 and 7 govern a severity
rubric that *nothing evaluates* — a tenant can approve a policy that changes no
behaviour at all. This is the largest gap between claim and reality in the system.

| Item | Effort | Notes |
|---|---|---|
| **B-12.1** Rubric evaluation stage | M | `policy.resolver.score_severity` already exists and is tested. Write the `severity_scoring` stage provider, emit `severity_scored` (already registered), write `severity_breakdown` + `severity_policy_version`. |
| **B-12.2** Score reproducibility property test | S | Gate clause: any scored complaint reproduces its score from its logged breakdown alone. `hypothesis` over random inputs. |
| **B-12.3** Policy-version immutability test | S | Gate clause: a policy version change never mutates previously scored complaints. |
| **B-12.4** OSM road-class + POI enrichment | L | Locally cached with a cold-start fallback. **New external dependency ⇒ needs a `Dependency` enum member, a runbook, and an alert** (`metrics.py` already notes OSM is not a member yet). |
| **B-12.5** Cluster-density re-scoring (§12.5) | M | Re-score when report count crosses a threshold. **Implement as an explicit rule and label it as a rule** — the plan is emphatic that this must never be blurred into the agent claim. |
| **B-12.6** Routing rule evaluation | M | Against the Phase 5 organisation model. Emit `work_order_created` with `routing_rule_id` so misrouting is diagnosable. |
| **B-12.7** Calendar/season-aware SLA (§13.4) | L | `control_plane/calendars.py` exists. Compute deadlines against tenant calendars. |
| **B-12.8** Beat-driven SLA sweeper + auto-escalation | M | Gate clause: escalation fires within one sweep interval of the deadline, clock-controlled. Use a controllable clock, not `sleep`. |
| **B-12.9** Gate script `gate_phase12.py` + `nem gate-phase12` | S | Follow `gate_phase10.py`. |

**Gate:** score reproducible from its own breakdown; policy change never rewrites
history; escalation fires within one sweep; holiday and monsoon windows
measurably shift deadlines.

---

### Phase 11 · ML platform: labelling, drift & feedback · **XL** · after 12–15

Deliberately sequenced late. Its premise is that human decisions become training
labels, and the richest sources — merge overrides, review outcomes, citizen
disputes, closure confirmations — only accumulate once Phases 12–15 exist.
Building it now means building against empty tables.

| Item | Effort | Notes |
|---|---|---|
| **B-11.1** Labelling pipeline | L | Fed automatically from `review_decisions`, dedup reversals (`nemesis_dedup_merge_reversions_total` is already instrumented), citizen disputes, closure confirmations. |
| **B-11.2** Versioned datasets with lineage | M | Any published metric reproducible from an exact snapshot. Extend the corpus pattern in `perception/corpus.py` and `dedup/corpus.py`. |
| **B-11.3** Drift detection | L | On input *and* confidence distributions. Gate clause: injected drift alerts within one detection window. |
| **B-11.4** Champion/challenger with shadow evaluation | L | `simulation/shadow.py` is read-only by construction (ADR-0030) — reuse it. Promotion gated on measured improvement against a frozen regression set. |
| **B-11.5** Active learning queue ordering | M | Rank review candidates by margin. `classification_scored@2` already records `margin` and `raw_similarities` for exactly this. |
| **B-11.6** Growing regression set | M | Never allowed to shrink; enforce with a test. |
| **B-11.7** Per-tenant model performance reporting | M | Accuracy is not uniform across geographies. |

**Gate:** a month of review decisions demonstrably improves a held-out metric;
injected drift alerts within one window; a challenger cannot be promoted without
beating the champion; every published number reproduces from a versioned snapshot.

---

## Priority 2 — Track D · Accountability

### Phase 13 · Identity, authorization & org modelling · **XL** · **longest-lead blocker**

Everything in Track D and the entire frontend sit behind this. Today's auth is
API keys (Phase 4) plus a shared control-plane token (ADR-0020). There is no user
identity anywhere in the system.

| Item | Effort | Notes |
|---|---|---|
| **B-13.1** OIDC-capable identity, JWT, refresh rotation, revocation | L | Optional SSO for institutional tenants. |
| **B-13.2** ABAC/ReBAC authorization | XL | **Not a role enum.** Custom roles composed from permissions, scoped by department, zone, site, or taxonomy node. Critique-log defect #7. |
| **B-13.3** Delegation & temporary grants with expiry | M | Leave and shift coverage. |
| **B-13.4** Contractor organisations with sub-users | M | |
| **B-13.5** Audited impersonation | M | Requires justification; produces an event **visible to the tenant**. |
| **B-13.6** Centralised decision point + decision log | M | Default-deny. |
| **B-13.7** CI check: an endpoint without a declared permission fails the build | S | Same shape as `check_tenant_scoping.py`. |
| **B-13.8** Negative-authorisation test per role pair | L | Gate clause: department head A provably cannot read department B's rows **through any endpoint, including error messages and pagination counts**. |

**Gate:** the negative test above; undeclared-permission endpoints fail CI;
decisions property-tested against a generated role/scope matrix; every
impersonation session visible to the impersonated tenant.

---

### Phase 14 · Department workflow & work orders · **L** · **[serves FE]**

Backend scope: state machine, assignment, budget, evidence. The kanban is the
project owner's.

| Item | Effort | Notes |
|---|---|---|
| **B-14.1** Triage queue, role-scoped, SLA+severity ordered | M | |
| **B-14.2** Assignment with workload/rating-ranked contractor selection | M | Distribution must be auditable so silent favouritism is detectable. |
| **B-14.3** Rate-card budget entry | M | Against the effective-dated card from Phase 6. |
| **B-14.4** Milestone evidence workflow | M | **Fund release is SIMULATED — label it in the data model, the API response, and the UI contract.** |
| **B-14.5** Bulk operations and saved views | M | |
| **B-14.6** Cluster-to-cluster merge (closes **G6**) | M | The action a reviewer resolving an ambiguous dedup needs. Writes `ComplaintCluster.superseded_by_id`, which Phase 10 filters on and nothing writes. |

**Gate:** evidence trail correctly scoped to every role that can see it;
contractor distribution auditable; **every transition is an event, no status
directly editable**.

---

### Phase 15 · Closure loop & evidence verification · **L**

The fix for the trust-collapse mechanism (§3.1) — the reason the product exists.

| Item | Effort | Notes |
|---|---|---|
| **B-15.1** Before/after SSIM structural verification | M | Gates `pending_verification`. `scikit-image` is already a dependency. |
| **B-15.2** Perceptual-hash guard on resubmission | S | A "before" photo must not be resubmittable as the "after". `trust/phash.py` already exists — reuse it. |
| **B-15.3** Citizen confirm / dispute / auto-confirm-**unconfirmed** | M | Auto-confirmed must be visibly distinct from citizen-confirmed in the API, the UI contract, **and the contractor's computed rating**. |
| **B-15.4** Notification fallback chain on a decoupled worker | M | A notification failure must never block a work order. |
| **B-15.5** Dispute reopening and escalation | M | |
| **B-15.6** Resolution-streak retention mechanic (§21.3) | M | Promoted from ROADMAP because it targets the exact decay the product exists to prevent. |

**Gate:** closure cannot reach `resolved` without a passing SSIM delta — enforced
in the state machine, not the UI; a near-identical after-photo is rejected;
auto-confirmed is distinguishable everywhere.

---

### Phase 16 · Investigation Agent & agent platform · **XL**

The single genuinely agentic component (§12.4). Built to survive "walk me through
what it decided."

| Item | Effort | Notes |
|---|---|---|
| **B-16.1** LangGraph state machine over local `llama3.1:8b` | L | Ollama is already a declared `Dependency` with a runbook. |
| **B-16.2** Tool registry | M | Tools declared with typed schemas, permissions, timeouts and cost budgets — **not wired inline**. |
| **B-16.3** Tools: additional photo, OSM utility proximity, location history | M | Sequencing decided from prior results — that is the actual difference between an agent and a function with an LLM call in it. |
| **B-16.4** Structured conclusion + logged natural-language justification | M | Exactly the §12.4 payload shape. |
| **B-16.5** Full trace persisted as events, checkpointed and replayable | L | Gate clause: any invocation replayable step-by-step from the log alone. |
| **B-16.6** Agent evaluation harness | L | Fixed scenario suite scoring decision quality, so a prompt or model change is measured rather than vibed. Model it on `perception/harness.py`. |
| **B-16.7** Prompt-injection hardening on citizen text | L | Closes the gap §25.1 honestly lists as unmitigated. Needs an explicit adversarial suite. |
| **B-16.8** Wire the ambiguous dedup band to the agent | S | Phase 10 currently routes it to a human under `ReviewReason.AMBIGUOUS_DEDUP`. This is the substitution being repaid. |

**Gate:** replayable from the log; Ollama-down and tool-timeout both degrade to
human review with a `system_degradation` event, never a hang; adversarial text
cannot redirect tool calls; a prompt change is accepted only on measured
improvement; end to end within §27.1.

---

### Phase 17 · Contractor transparency, fraud & equity · **L**

| Item | Effort | Notes |
|---|---|---|
| **B-17.1** Public contractor profiles | M | Computed reputation metrics — **never a collapsed star rating** (§16.1). |
| **B-17.2** Rate-card deviation anomaly detection | M | REAL. Quantity-vs-photo is SIMULATED and must be labelled. |
| **B-17.3** Entity resolution over shared address/phone/director | L | `pg_trgm` is installed. **Kept internal** per the defamation reasoning in §17.1. |
| **B-17.4** Fund-source and scheme tagging, ward budget transparency | M | §17.6. |
| **B-17.5** Underreporting-zone equity flag | M | OSM infrastructure proxies vs complaint density (§23.2). |
| **B-17.6** Contractor dispute/appeal workflow | M | **Ships in this phase, not later.** §6.5 requires the appeal path to arrive with the accountability feature. |
| **B-17.7** Repeat-defect dedup mode | M | The Phase 10 engine filtered by contractor (§17.5). **This is where `hnsw.iterative_scan` finally gets set** — it is a genuine global ANN query, unlike dedup Stage 2 (see ADR-0036). |
| **B-17.8** Serializer-level "no unreviewed flag reaches a public body" test | M | The §22.2 defamation control, enforced in code rather than a template. |

**Gate:** the serializer test; every public metric traceable to source events;
GPS coarsening and PII scrubbing verified on every public endpoint; a contested
flag provably disappears from public view pending review.

---

## Priority 3 — Tracks F, G, H

### Phase 23 · Analytics platform & metrics layer · **XL**
CDC from the event log into an analytical store; a **governed metrics layer**
where every §41 KPI has one definition; privacy-respecting product telemetry;
operational and tenant-facing dashboards; data-quality monitoring.
**Gate:** two surfaces reporting the same metric provably agree; a data-quality
regression alerts before it reaches a customer; analytics carries no
citizen-identifying data, verified by test.

### Phase 24 · Experimentation · **L**
Assignment, exposure logging, guardrail metrics. Applied first to the questions
that matter: does the resolution-streak mechanic reduce reporting decay, and does
positive-framed metric ordering affect department adoption. Closes the loop into
Phase 7 so a winning configuration becomes a proposed policy change with evidence.
**Gate:** one experiment end to end with a pre-registered hypothesis; guardrails
can halt automatically; assignment provably stable and leak-free.

### Phase 25 · Security hardening & threat verification · **L**
**Postgres Row-Level Security on the highest-risk tables** — closes the §18.3 gap
rather than documenting it. Every §25.1 threat row mapped to a test. `toxiproxy`
fault injection (closes part of **G7**). SSRF protection on outbound enrichment,
upload sandboxing, content sniffing. Third-party pen test.
**Gate:** every §27.3 runbook scenario passes as an automated test; no
high-severity vulnerability in shipped images; **RLS proven to block a direct
database query the application layer would have refused**.

### Phase 26 · Privacy & DPDP compliance · **XL**
Compliance as running systems, not prose. Consent registry as events;
data-subject access/correction/erasure reconciled with an append-only log via
**cryptographic erasure and tombstoning**; automated retention on the §22.4
schedule; DPIA and data-flow map as living artefacts; breach runbook, rehearsed;
data residency per tenant; minors' data per §22.3.
**Gate:** an erasure request completes within the statutory window **without
breaking chain integrity**; retention deletion automated and proven; a breach
simulation completes notification within its window.

### Phase 27 · Tenant operations, metering & support console · **L**
Self-serve and assisted provisioning with a trial path; usage metering per §28
with per-tenant COGS attribution; subscription and invoicing; a support console
with audited impersonation (needs Phase 13), tenant health, queue depth and
config inspection; customer-facing SLA reporting; onboarding/migration tooling.
**Gate:** a tenant provisioned, configured and live **without engineering
involvement**; metered usage reconciles exactly with the event log; a support
engineer resolves a seeded issue using only the console, every action audited.

### Phase 28 · Performance, resilience & DR · **L** · closes most of **G7**
`k6` scenarios asserting **every §27.1 budget as a CI threshold** — currently
unproven after ten phases. Capacity model and documented scaling limits. Backup,
PITR, and a **timed restore drill against stated RTO/RPO**. Multi-AZ posture.
Error budgets that actually halt feature work. A game day.
**Gate:** every SLO met under load; a full restore within the stated RTO, drilled
and timed; a game day run with on-call following documented runbooks.

---

## Priority 4 — Release

### Phase 29 · Seed, E2E & release certification · **XL** · needs the frontend
Deterministic seed of 300–500 complaints with realistic ratios, planted anomalies,
seeded abuse accounts, and 12 months of backdated history. **Three structurally
different tenants** — municipality, campus, industrial park — proving the control
plane rather than asserting it. Full E2E: submit → classify → cluster → score →
route → assign → execute → SSIM verify → citizen confirm → public record.
Kill-and-restart resilience across every service. **§44 REAL/SIMULATED/ROADMAP
reconciled line by line** (closes **G8**).
**Gate:** one command, air-gapped, clean checkout to working system; full E2E on
the demo laptop, on battery, WiFi disabled; the same E2E against three
structurally different tenants; every `README.md` and §44 claim traces to a
passing test.

---

## Standing items — not a phase, do them continuously

| # | Item | Effort | Why |
|---|---|---|---|
| **B-S1** | Fix `CHANGELOG.md` generation (**G9**) | S | Phases 3–10 are missing. The file itself says a wrong changelog is worse than none. Either the conventional-commit parsing or the commit messages have drifted — find out which. |
| **B-S2** | Add `mutmut` to scoring, dedup, authz and chain modules (**G7**) | M | A declared standard that has never run. Start with `dedup/decide.py` and `policy/resolver.py`. |
| **B-S3** | Consumer-driven contract tests (**G7**) | M | `api_contract_lock.json` is a provider-side snapshot, not a consumer contract. |
| **B-S4** | Add a dedup runbook | S | Phase 10 shipped metrics (`nemesis_dedup_*`) with no runbook page. `check_runbooks.py` does not require one yet because dedup declares no external `Dependency`, but the standard says every shipped phase enters the on-call runbook. |
| **B-S5** | Reconcile §44 as you go (**G8**) | S each | Cheaper per phase than once at Phase 29. |
| **B-S6** | Guard against silent test skips | S | `pytest` without `NEMESIS_TEST_ADMIN_DSN` skips ~400 tests and exits 0. Add a marker that fails the run if the integration suite was skipped wholesale outside an explicitly-unit-only invocation. |

---

## Dependency map

```
B-10.1 (photo corpus) ──┬─→ B-10.2 (radius) ─→ Phase 10 gate closed
                        └─→ Phase 11 (evaluation sets)

Phase 12 ─┬─→ Phase 14 ─→ Phase 15 ─→ Phase 17
          └─→ Phase 23
Phase 13 ─┴─→ Phase 14, 27, and all of Track E

Phase 10 + 12 ─→ Phase 16 ─→ (repays the Phase 10 ambiguous-band substitution)
Phase 23 ─→ Phase 24
Phase 13 + 17 ─→ Phase 25 ─→ Phase 28
Phase 13 + 23 ─→ Phase 26
everything ─→ Phase 29
```

**Critical path to a demonstrable product:** 13 → 12 → 14 → 15, with B-10.1 run
in parallel because it is a sourcing problem before it is an engineering one.
