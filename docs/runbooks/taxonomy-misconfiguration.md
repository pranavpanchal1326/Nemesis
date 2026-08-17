# Taxonomy or calendar misconfiguration — a tenant's complaints stopped making sense

- **Severity:** warning (critical if a safety category was deactivated)
- **Owner:** PLT
- **Alerts:** none — this is reported by a customer, not by a monitor. See
  "Why there is no alert" below.

> **Nothing is lost while this is happening, and nothing is unrecoverable.**
> Control-plane changes are additive rows plus an event on the tenant's hash
> chain; no complaint is deleted and no history is rewritten. What breaks is
> *interpretation* — new complaints are classified, routed, or scheduled against
> a definition somebody changed. Read that before deciding how hard to push: the
> damage is bounded to complaints submitted after the change, and the change is
> in the log with a timestamp.

## Symptoms

Reported, usually, as one of these:

- "Reports are going to the wrong department since Tuesday."
- "A category disappeared from the citizen app."
- "Every SLA deadline moved by a day."
- "The category names are in English again."

## How to confirm — read the tenant chain first

This is the step that turns a guess into a diagnosis. Every control-plane
mutation appended an event; nothing else did.

```bash
docker compose exec -T postgres psql -U nemesis -d nemesis -tAc "SELECT occurred_at, event_type, payload FROM events WHERE tenant_id = '<TENANT>' AND entity_type = 'tenant' ORDER BY sequence DESC LIMIT 20"
```

- `taxonomy_published` carries `revision`, `change_kind`, `changed_keys`, and the
  `content_hash` of the whole taxonomy at that moment. Two adjacent rows with the
  same hash mean nothing semantic changed.
- `organisation_changed` carries the `subject` (department / zone / shift /
  calendar / certification), the `subject_key`, and the field names that moved.
  **Values are deliberately not recorded** — §22 applies to the audit trail too —
  so the event tells you *what* changed and the current row tells you *to what*.

Then read the current state:

```bash
docker compose exec -T api python -m nemesis.control_plane show --tenant <TENANT>
```

Inactive nodes are printed with a leading `-`. A category that "disappeared" is
almost always here, deactivated, rather than gone.

## Immediate mitigation

**1. A category was deactivated by mistake.** Reactivate it. There is no
`DELETE` in the control plane precisely so this is reversible:

```bash
curl -X PATCH http://localhost:8000/api/v1/control-plane/taxonomy/<KEY> \
  -H "X-Tenant-ID: <TENANT>" -H "X-Control-Plane-Token: $NEMESIS_CONTROL_PLANE_TOKEN" \
  -H 'Content-Type: application/json' -d '{"is_active": true}'
```

Complaints submitted while it was inactive are **not** retroactively fixed. They
were classified under the taxonomy as it stood, which is the correct and honest
outcome — the alternative is rewriting a classification nobody re-examined.

**2. Work is going to the wrong department.** A routing hint changed. Compare
the node's `routing_hints.department_code` against the department tree:

```bash
curl -s http://localhost:8000/api/v1/control-plane/departments -H "X-Tenant-ID: <TENANT>"
```

Restore the hint with the same `PATCH` as above. Work orders already created keep
their assignment; reassignment is a Phase 14 workflow decision, not a control-plane
one, and doing it by hand in SQL would produce a work order the log does not explain.

**3. Every deadline moved.** A calendar changed — most often an exception span
with a wrong end date, or a new default calendar demoting the old one. Ask the
calendar what it now believes, rather than reading the JSONB:

```bash
curl -X POST http://localhost:8000/api/v1/control-plane/calendars/preview-deadline \
  -H "X-Tenant-ID: <TENANT>" -H 'Content-Type: application/json' \
  -d '{"start":"2026-07-01T10:00:00+05:30","budget_hours":72,"calendar_code":"<CODE>"}'
```

The response carries `adjustments`, naming every seasonal span that stretched the
budget and by how much. A deadline nobody can explain is the failure this
endpoint exists to prevent; if `adjustments` names a span the customer did not
expect, that is the answer.

**4. Labels reverted to English.** Translation coverage dropped — usually a
re-import that omitted keys rather than one that overwrote them:

```bash
curl -s http://localhost:8000/api/v1/control-plane/translations/coverage -H "X-Tenant-ID: <TENANT>"
```

`missing_keys` is the exact list to send back to whoever prepared the bundle.
Re-importing is an upsert, so a corrected bundle can be sent repeatedly.

## Root cause investigation

Four questions, in this order, because the first two are usually enough.

**1. What changed, and when?** The `taxonomy_published` events carry a
`content_hash` of the entire taxonomy. Walk backwards until the hash last matched
what the customer expected; the `changed_keys` on the event *after* that point is
the change to explain.

**2. Was it a template drift rather than an edit?** A tenant records the template
and version it was provisioned from:

```bash
docker compose exec -T postgres psql -U nemesis -d nemesis -tAc "SELECT slug, provisioned_from_template, template_version, taxonomy_revision FROM tenants WHERE id = '<TENANT>'"
```

A tenant onboarded from `campus` v1.0.0 and one onboarded from a later version
have different defaults, and the difference is invisible without this. If the
customer's complaint is "our other site behaves differently", compare the two
tenants' `template_version` before looking at anything else.

**3. Did a *dependent* change do it?** These are the couplings that surprise
people, because the thing they edited is not the thing that broke:

- Creating a calendar with `is_default: true` **demotes the previous default**,
  so every department that had not named a calendar explicitly silently moved to
  the new one. `organisation_changed` records the calendar creation; it does not
  record the demotion, because the demotion is a consequence rather than an
  instruction.
- Deactivating a parent node does not deactivate its children. The children stay
  selectable and stay classifiable, which is deliberate — but a UI that hides an
  inactive parent's subtree makes them look gone.
- A department made `is_assignable: false` still exists and still appears in the
  tree; only routing stops choosing it.

**4. Is the projection consistent with the log?** If the events look right and
the rows do not, this is no longer a configuration problem — go to
[event-chain-integrity](event-chain-integrity.md).

## Prevention

- **Phase 7 is the real fix**, and it is named as such rather than implied:
  simulation and backtesting show which complaints would change severity, which
  merges would flip, and which SLAs would breach — *before* anyone approves the
  change. Everything on this page is the interim procedure.
- **Phase 6 adds the lifecycle** — draft → review → approve → activate, with safe
  rollback to any prior version. Phase 5 deliberately ships neither; a revision
  counter and a content hash are what it can honestly offer, and pretending
  otherwise would fix a shape before the phase that owns it exists.
- **Preview a deadline before changing a calendar**, not after. The
  `preview-deadline` endpoint costs one request and turns a monsoon window with a
  wrong end date from a discovery into a check.
- **Read `coverage` after every translation import.** A bundle that omitted keys
  and a bundle that was never sent look identical on screen.
- **Deactivate rather than replace.** There is no `DELETE` in the control plane
  (ADR-0019), so the reversible path is always available — but only if nobody
  works around its absence with SQL.

## If the change was not authorised

A control-plane write requires the shared token, and the token is not an
identity (ADR-0020). If nobody recognises the change:

1. Rotate `NEMESIS_CONTROL_PLANE_TOKEN` — see [SECRETS.md](../SECRETS.md).
   Rotation invalidates nothing but the token, so there is no reason to wait.
2. The tenant chain is the audit. Every change the old token made is in it,
   in order, with timestamps.
3. Open an incident under [the incident process](../incidents/README.md). A
   third party able to redefine a tenant's taxonomy could route hazard reports
   away from the crew that handles them, which is a safety issue and not only a
   data one.

## Why there is no alert

There is no runtime signal that distinguishes an intended taxonomy change from a
mistaken one. A monitor on "the taxonomy changed" would fire on every legitimate
onboarding and every ordinary edit, and an alert that fires on normal operation
is one people mute — Phase 1a's dead-man's-switch reasoning, applied in the
other direction.

What exists instead is the evidence to answer the question quickly once a human
raises it: a revision counter, a content hash, and a chain. Phase 7's simulation
and backtesting is the real fix — it shows the affected population *before*
anyone approves a change — and this page is the interim procedure until it lands.

## Related

- [ADR-0019](../adr/0019-taxonomy-keys-are-immutable-contracts.md) — why a key
  cannot be renamed and why deactivation is the only removal.
- [ADR-0020](../adr/0020-control-plane-writes-carry-a-shared-token.md) — why the
  events matter more than the token.
- [Event chain integrity](event-chain-integrity.md) — if the chain itself is
  what looks wrong.
