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

## The plan of record

| Document | What it answers |
|---|---|
| **[PHASES.md](PHASES.md)** | The thirty-phase program plan, every exit gate, and — most usefully — the defect log recording what each gate *caught*. |
| `../NEMESIS-Blueprint-v2.md` | The product specification. The `§` numbers scattered through the code point here. |
| `../NEMESIS-Frontend-Blueprint.md` | The Track E specification — art direction, design system, frontend architecture, surface by surface. The `§E` numbers point here. Supersedes §8.1, §19, §20. |
| **[FRONTEND-EXECUTION-PLAN.md](FRONTEND-EXECUTION-PLAN.md)** | The Track E build sequence: milestones M0–M12, what blocks what, and which §E25 gate each one closes. |

## Decisions and operations

| Directory | Contents |
|---|---|
| **[adr/](adr/)** | 42 architecture decision records. Read the relevant one before changing a design. Check the highest number before claiming a new one. |
| **[runbooks/](runbooks/)** | 27 pages, one per failure scenario and per external dependency. What to do at 2am. |
| **[incidents/](incidents/)** | Severity definitions, the post-mortem template, and the tracked action register. |
| **[rfc/](rfc/)** | The RFC process, for anything that crosses tracks. |

## Measurements

| Report | What it establishes |
|---|---|
| **[reports/perception-f1.md](reports/perception-f1.md)** | Per-category precision/recall/F1 for classification, per-locale breakdown, inference latency, and §22.1 distant-face recall. Phase 9's published number. |
| **[reports/dedup-precision-recall.md](reports/dedup-precision-recall.md)** | Dedup precision/recall and the false-merge count. Phase 10's published number — **and its failing gate, with the diagnosis.** |
| **[reports/hnsw-recall.md](reports/hnsw-recall.md)** | The measured recall curve behind the chosen HNSW parameters. |

Every report is reproducible by one command, named at the top of the file.

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
