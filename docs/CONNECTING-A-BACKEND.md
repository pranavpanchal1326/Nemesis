# Connecting a backend — the seam, the variables, and one open blocker

- **Written:** 2026-08-27
- **For:** Adi, or whoever wires the main backend to this frontend
- **Companion to:** [HANDOVER.md](HANDOVER.md), which is the entry point for backend work and stays that. This file is the Track E side of the same join: what the frontend expects, where it attaches, and what is currently blocking the public surface.

Read [HANDOVER.md](HANDOVER.md) first for ownership, the nine rules and phase
state. This one is narrower and practical: how to run it, how to fill it with
data, and — the part worth reading before writing any code — **the one seam that
decides whether a backend swap is an afternoon or a fortnight.**

---

## 1 · Run it

```bash
nem doctor
```

```bash
nem up
```

`nem up` builds and starts everything and blocks until every service is
healthy: `api`, `postgres`, `redis`, `relay`, `webhooks`, `beat`, `worker-io`,
`worker-ml`. Then the frontend, on the host:

```bash
nem web
```

`nem down` stops the stack and keeps the volumes. `nem nuke` deletes them.

---

## 2 · Seed a city

An empty deployment renders honestly — every ward reads *"not yet scored"* and
*"none filed"*, which is §E3.3 working, not a bug. To get a city with a week
behind it:

```bash
nem seed-demo --reports 140
```

**`--reports` defaults to 0**, which is why a fresh checkout looks empty. That
default is deliberate — the flag costs a minute or two of pipeline time — but it
is also the single most common reason somebody thinks the product is broken.

If the tenant already exists the script cannot resolve its id over HTTP (no
control-plane endpoint maps a slug to an id, by design — ADR-0021). Get it from
the database and pass it:

```bash
docker compose exec postgres psql -U nemesis -d nemesis -t -c "select id from tenants where slug = 'pune-demo'"
```

```bash
nem seed-demo --tenant-id <id> --reports 140
```

Everything the seeder creates goes **over HTTP through the real handlers** — no
fixture loader, no `INSERT`. The reports are classified, clustered, redacted and
scored by the same pipeline a real report goes through, which is why some of
them come out flagged and some park at `pending_classification`. That is the
product, not the seed.

---

## 3 · The four variables

All server-side. **None is `NEXT_PUBLIC_`, and that is the point** — a browser
that named its own tenant would ship a trust boundary that is not one
(ADR-0040). Copy `frontend/.env.example` to `frontend/.env.local`.

| Variable | What it decides |
|---|---|
| `NEMESIS_API_URL` | Where FastAPI is. Defaults to `http://127.0.0.1:8000` |
| `NEMESIS_TENANT_ID` | The tenant this deployment serves. Goes away after Phase 13, when verified session claims replace it |
| `NEMESIS_CONTROL_PLANE_TOKEN` | The shared secret every console **write** carries. Without it the console reads fine and the first decision fails with a backend error — a failure mode that is easy to misread |
| `NEMESIS_STORY_TENANT` | The published slug the landing film is a film *of*. Unset is supported: the landing prints the storyboard and claims nothing about a place |

Two optional ones: `NEMESIS_REALTIME_URL` (only when the browser reaches the
socket at a different host from the server) and `NEMESIS_PUBLIC_API_URL` (the
public host for Act 9's live `curl`; unset prints the path without a host,
because a landing page must not print `127.0.0.1` as though it were public).

---

## 4 · Where a backend attaches

**One seam, and it is already isolated.** Every read goes through
`frontend/src/server/upstream.ts`, which owns the typed client, the tenant
header, and the `onError` middleware that turns a transport failure into a
designed `503` instead of a framework crash page (ADR-0060). Nothing in
`src/app/` talks to the network directly.

The TypeScript client is **generated from the live OpenAPI document**, not
hand-written:

```bash
nem web-openapi
```

```bash
nem web-types
```

So the honest test of "can this frontend talk to that backend" is: point
`NEMESIS_API_URL` at it, regenerate, and see what fails to compile. A contract
difference becomes a type error at build time rather than a blank panel at
runtime. `nem api-lock` re-locks the published contract after a deliberate
change.

**What this means for a swap.** If the replacement backend serves the same
OpenAPI shapes, the change is the env var and a regenerate. If it does not, the
compile errors are the exact worklist, and they are in one directory.

---

## 5 · Open — the public surface is structurally empty, and it is not the seed

**Read this before you spend an afternoon on it.** After seeding 140 reports,
the console, the review queue, the clusters and the citizen tracking all fill
in. **The public transparency surface — ward counts, the map's figures, the
landing's peer list — stays at zero**, and no amount of seeding changes it.

The cause is one column:

- `backend/nemesis/public/aggregates.py:182` joins complaints to zones on
  `Complaint.ward == zone_code`
- `backend/nemesis/projections/writer.py:89` writes `"ward": state.get("ward")`
- **Nothing ever puts a ward in that state.** `ward` appears in no domain event
  and no entry in `events/catalog.py`

The module says so itself, and the note is worth quoting because it is the
decision rather than an oversight:

> Phase 5 shipped `zones` as the supersession of the `ward` label and **Phase 12
> is what makes routing write the zone reference**. Doing the label match now
> and saying so is better than inventing a foreign key the pipeline does not
> populate.

Verified on this checkout: 671 complaints, `ward` NULL on every one, all
fourteen zones reporting `total_reports: 0`.

**The resolver already exists and works.** `GET /api/v1/places/resolve` maps a
coordinate to the zone chain with `ST_Covers` over a GiST index
(`backend/nemesis/api/v1/places.py`), and the seeded wards have real boundaries.
The missing piece is only that no pipeline stage calls it and records the
answer.

**It was deliberately not fixed here.** Stamping the ward means adding a field
to an event schema, which in this repository means a schema fingerprint, a
registry version, a migration and gate updates — Phase 12's work, and precisely
the kind of change that collides with an incoming backend. If the main backend
already routes complaints to zones, this resolves itself the moment it is
connected: write the zone code into `complaints.ward` and every public figure
lights up with no frontend change at all.

---

## 6 · What is real today

The product publishes its own answer to this and it is generated, not written
by hand:

- `/{tenant}/honesty` — every claim with a status label against it
- Act 9 of the landing — the counts, from the same source
- The `NOT WIRED` chips on `/staff` — each names the phase that populates it

Trust those over any prose, including this file. They are drift-checked
(`nem web-check`); this paragraph is not.

---

## 7 · Gates before you push

```bash
nem check
```

```bash
nem web-check
```

Backend and frontend, the same sets CI runs. The frontend one covers tokens,
the ten design guards, typecheck, lint, format, unit tests and E2E.

**Known failing, deliberately:** the nine act golden images. They were left
failing while the film printed flat, because a regenerated baseline would have
baked the defect in as the expected result. [ADR-0061](adr/0061-a-run-is-printed-at-an-exposure-and-the-story-run-needs-one.md)
fixed the flat print, so they should now be regenerated — on Linux, the way CI
sees them:

```bash
nem web-golden
```

That is a reviewable step on its own, which is why it has not been done for you.
