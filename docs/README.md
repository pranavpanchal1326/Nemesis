# NEMESIS — Documentation Index

**New to this repository and picking up backend work? Start with
[HANDOVER.md](HANDOVER.md).** Read it end to end before writing code — twenty
minutes there saves a week here.

---

## Start here

| Document | What it answers |
|---|---|
| **[HANDOVER.md](HANDOVER.md)** | Where the system is, the nine rules that govern it, how to run everything, what is owed, and what to do next. **The entry point.** |
| **[BACKLOG.md](BACKLOG.md)** | What remains, phase by phase, broken into startable items with effort and dependencies. |
| **[UPGRADES.md](UPGRADES.md)** | Model training, architectural upgrades and technical debt — proposals with costs, not requirements. |
| **[GETTING-STARTED.md](GETTING-STARTED.md)** | Getting the stack running on a fresh machine. |
| **[CONNECTING-A-BACKEND.md](CONNECTING-A-BACKEND.md)** | The Track E join: the four env variables, the one seam every read goes through, how to seed a city, and the open reason the public transparency surface reads zero. |

## The plan of record

| Document | What it answers |
|---|---|
| **[PHASES.md](PHASES.md)** | The thirty-phase program plan, every exit gate, and — most usefully — the defect log recording what each gate *caught*. |
| `../NEMESIS-Blueprint-v2.md` | The product specification. The `§` numbers scattered through the code point here. |
| `../NEMESIS-Frontend-Blueprint.md` | The Track E specification — art direction, design system, frontend architecture, surface by surface. The `§E` numbers point here. Supersedes §8.1, §19, §20. |
| **[FRONTEND-EXECUTION-PLAN.md](FRONTEND-EXECUTION-PLAN.md)** | The Track E build sequence: milestones M0–M12, what blocks what, which §E25 gate each one closes, and the outstanding register. **All thirteen milestones are done.** |
| **[FRONTEND-PHASE-PLAN.md](FRONTEND-PHASE-PLAN.md)** | The same work as eighteen phases, F1–F18, each finishable and gated. Owns *order and gate*; the execution plan owns *what and why*. `scripts/check_phase_coverage.py` asserts every ship line and every open register row is claimed by exactly one phase. |

## Decisions and operations

| Directory | Contents |
|---|---|
| **[adr/](adr/)** | 58 architecture decision records. Read the relevant one before changing a design. Check the highest number before claiming a new one. |
| **[runbooks/](runbooks/)** | 27 pages, one per failure scenario and per external dependency. What to do at 2am. |
| **[incidents/](incidents/)** | Severity definitions, the post-mortem template, and the tracked action register. |
| **[rfc/](rfc/)** | The RFC process, for anything that crosses tracks. |

## Measurements

| Report | What it establishes |
|---|---|
| **[reports/perception-f1.md](reports/perception-f1.md)** | Per-category precision/recall/F1 for classification, per-locale breakdown, inference latency, and §22.1 distant-face recall. Phase 9's published number. |
| **[reports/dedup-precision-recall.md](reports/dedup-precision-recall.md)** | Dedup precision/recall and the false-merge count. Phase 10's published number — **and its failing gate, with the diagnosis.** |
| **[reports/hnsw-recall.md](reports/hnsw-recall.md)** | The measured recall curve behind the chosen HNSW parameters. |
| **[reports/story-press-flat.md](reports/story-press-flat.md)** | Why §E16's film renders as a flat field of ink — the fixed stage that was not fixed, Phase 19's missing tone map, and **the story run's three chromatic inks saturating on the one plate that carries the clay.** Two fixes shipped; the third is an art-direction decision, named and not guessed. |
| **[reports/clay-frame-rate.md](reports/clay-frame-rate.md)** | The clay engine's measured frame rate against §E23's budget — **the gate clause that failed**, with the scaling curve, the optimisations tried, and the token deliberately left unrelaxed. |
| **[reports/m12-reconciliation.md](reports/m12-reconciliation.md)** | Track E's close. §E28 and §44 read line by line against what is actually running — **five wrong claims, none of them found by reading**, including a REAL row whose only rendering was a fixture. |
| **[reports/e27-audit.md](reports/e27-audit.md)** | §E27's event-to-surface table executed in both directions against the event catalog and the frontend source. Two registered audit events reach no surface at all. |

Every report above is reproducible by one command, named at the top of the file.

## Published gaps

A gate clause that cannot be taken is published rather than skipped quietly, and
rather than passed by manufacturing its input. Each of these names one clause,
what was refused, what is asserted instead, and what would close it.

| Report | The clause |
|---|---|
| **[reports/story-merge-gate.md](reports/story-merge-gate.md)** | §E16 Act 6's live merge. The committed test photograph scores below the classifier's own floor and parks before deduplication, so no real `cluster_match_found` exists to render. |
| **[reports/character-relief-gate.md](reports/character-relief-gate.md)** | §E8.1's *"`citizen_confirmed` fires `relief`"*. The event is registered, projected and shaped for the wire, and **nothing in this system appends it** — §E17.5's door is Phase 15. |
| **[reports/positional-foley-gap.md](reports/positional-foley-gap.md)** | §E12's *"an operator can hear where the problems are"*. The mechanism is built and asserted; the only positioned data the frontend receives is wards, and a loop chosen from a ward's name would assert a fault nobody detected. |
| **[reports/wcag-audit-gap.md](reports/wcag-audit-gap.md)** | Phase 18's *"WCAG 2.2 AA verified by audit, not only by automated scan"* (A15). The scan is clean and is about a third of the standard; **no person has audited this product.** All 56 AA criteria dispositioned, three expected failures flagged in advance. |
| **[reports/usability-session-gap.md](reports/usability-session-gap.md)** | Phase 18's *"measured task-success rate from a usability session"* (A16). **Not run — no participant has ever touched this product.** Ten tasks with binary criteria, and deliberately no pass mark. |

## Operations reference

| Document | What it answers |
|---|---|
| [MODELS.md](MODELS.md) | Which models, how they are cached, and how the air-gap guarantee is verified. |
| [HARDWARE.md](HARDWARE.md) | The machine this is built and budgeted against. |
| [RELEASE.md](RELEASE.md) | Release process, versioning, and deprecation clocks. |
| [SECRETS.md](SECRETS.md) | Secret handling and rotation procedures. |

---

## Two things worth knowing immediately

**Run `nem check` before you believe anything.** It is the same set CI runs —
lint, format, `mypy --strict`, the full test suite against real Postgres/PostGIS/
pgvector, the coverage floor, and seven CI checkers.

**A suspiciously fast green test run is not a green test run.** `pytest` inside
the container without `NEMESIS_TEST_ADMIN_DSN` skips roughly 400 database tests
and exits 0. Always go through `nem test`, which injects it.
