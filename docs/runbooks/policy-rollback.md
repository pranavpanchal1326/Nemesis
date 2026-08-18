# A policy change made things worse — rolling back a governed document

- **Severity:** warning (critical if the safety ruleset or a routing rule is involved)
- **Owner:** PLT
- **Alerts:** none — this is reported by a department or a customer, not by a
  monitor. See "Why there is no alert" below.

> **Nothing is lost while this is happening, and no history is rewritten.**
> A policy revision is a new row plus events on the tenant's hash chain; the
> previous version is still there, still readable, and every complaint decided
> under it is still stamped with it. What breaks is *future* decisions —
> complaints scored, routed, or scheduled after the activation. Read that before
> deciding how hard to push: the damage is bounded to work that arrived after a
> timestamp you can look up, and the fix is one API call.

## Symptoms

Reported, usually, as one of these:

- "Everything is coming through as urgent since this morning."
- "All the work is landing on one team."
- "The danger flag is firing on ordinary reports." (Or, worse, is not firing.)
- "Deadlines moved and nobody told us."

## How to confirm — read the policy history first

This is the step that turns a guess into a diagnosis. Every lifecycle transition
appended an event; nothing else changes what is deciding.

```bash
curl -s "http://localhost:8000/api/v1/control-plane/policies" -H "X-Tenant-ID: <TENANT>"
```

Each row carries `kind`, `revision`, `status`, `activated_at`, `change_reason`,
and who moved it. The one with `status: active` and an `effective_from` in the
past is what is deciding right now.

To see exactly what it says:

```bash
curl -s "http://localhost:8000/api/v1/control-plane/policies/<KIND>/active" -H "X-Tenant-ID: <TENANT>"
```

`is_baseline: true` means the tenant has **no** approved document of this kind
and is running on the platform default. That is a different problem — see
"A tenant is on baselines" below — and it is not fixed by a rollback.

For the chain view, including who did what and why:

```bash
docker compose exec -T postgres psql -U nemesis -d nemesis -tAc "SELECT occurred_at, event_type, payload FROM events WHERE tenant_id = '<TENANT>' AND event_type IN ('policy_drafted','policy_transitioned') ORDER BY sequence DESC LIMIT 20"
```

`policy_transitioned` carries `from_status`, `to_status`, the `reason` the
operator typed, and on an activation the `superseded_revision` it displaced.
That last field is the number you roll back to.

## Immediate mitigation

### Which complaints were affected

Bounded, and answerable exactly. Every decision records the version that made
it, so the blast radius is a query rather than an estimate:

```bash
docker compose exec -T postgres psql -U nemesis -d nemesis -tAc "SELECT count(*) FROM events WHERE tenant_id = '<TENANT>' AND event_type = 'severity_scored' AND payload->>'policy_version' = '<KIND>@<REVISION>'"
```

Nothing scored before the activation is affected, and nothing scored after the
rollback will be. Rescoring the affected complaints is a *separate* decision
with a separate cost — it changes deadlines that departments have already
planned around — and it is not part of this runbook.

### Roll back

One call. It restores the named revision's content as a **new** revision, which
is deliberate (ADR-0026): the version sequence only moves forward, so "what was
live on Tuesday" stays a query with one answer.

```bash
curl -s -X POST "http://localhost:8000/api/v1/control-plane/policies/<KIND>/rollback" -H "Content-Type: application/json" -H "X-Tenant-ID: <TENANT>" -H "X-Control-Plane-Token: $NEMESIS_CONTROL_PLANE_TOKEN" -d '{"to_revision": <REVISION>, "reason": "why you are doing this at this hour"}'
```

The response's `version.revision` is the **new** number — the one every
subsequent decision will be stamped with. It is not the number you asked for,
and that is not a bug.

**The reason is mandatory and it is read by a person.** Write the sentence you
would say out loud, not "rollback".

#### It takes up to 30 seconds, and that is the real number

The process that served your request is already using the restored version. Other
workers refresh on their own reload interval — 30 seconds by default (ADR-0027),
and the activation response repeats it back to you. **Wait it out.** Pressing the
button again does nothing except add revisions, and the next step after that is
somebody editing the database, which is how a recoverable incident becomes an
unrecoverable one.

Confirm with the same read you used to diagnose:

```bash
curl -s "http://localhost:8000/api/v1/control-plane/policies/<KIND>/active" -H "X-Tenant-ID: <TENANT>"
```

### If rollback is refused

- **409, "was never approved"** — you named a draft. Only versions that were
  actually live can be restored, because a rollback skips review and restoring
  an unreviewed draft would ship unreviewed content through the emergency door.
  Pick a revision with an `approved_at`.
- **409, "already active"** — the version you named is what is deciding. The
  problem is somewhere else; re-read the history and check whether a *different*
  kind changed at the same time.
- **403** — the control-plane token is missing or wrong. See
  [credential-leak.md](credential-leak.md) for rotation; the token is in
  `docs/SECRETS.md`.

## Root cause investigation

### The change itself

The `policy_transitioned` event that activated the bad version carries the
`reason` its author typed and the revision it displaced. Read it before writing
the postmortem: most of the time it says exactly what was intended, and the gap
between the intent and the effect is the finding.

The `based_on_revision` on the draft event tells you what it was edited *from*,
which is what makes a diff possible:

```bash
curl -s "http://localhost:8000/api/v1/control-plane/policies/<KIND>/<REVISION>" -H "X-Tenant-ID: <TENANT>"
```

Compare `content_hash` across revisions before comparing bodies — two revisions
with the same hash are byte-identical, which is what a rollback produces and
what rules out "the document changed" as the cause.

### A tenant is on baselines

`is_baseline: true` means the tenant predates Phase 6, or somebody archived
everything. It is *working* — the baselines are the same documents provisioning
seeds — but its decisions are stamped `baseline` rather than with a revision,
and nobody at that customer has approved anything.

Fix it forward rather than rolling anything back:

```bash
curl -s -X POST "http://localhost:8000/api/v1/control-plane/policies/seed-baselines" -H "X-Tenant-ID: <TENANT>" -H "X-Control-Plane-Token: $NEMESIS_CONTROL_PLANE_TOKEN"
```

Idempotent: a kind that already has any revision is skipped, so running it twice
is safe and it cannot clobber a tenant that has since tuned its own rubric.

To find every tenant in this state, search the logs rather than the database —
every baseline resolution logs it:

```bash
docker compose logs api worker-io worker-ml | grep policy_baseline_used
```

## Prevention

### Why there is no alert

The system cannot tell a bad policy from a policy that is working as approved.
A rubric that scores everything urgent is either a mistake or a municipality
that has decided everything is urgent, and the difference lives entirely in
somebody's intent. Alerting on "severity distribution shifted" would fire on
every legitimate retune and be muted within a week, which is worse than no alert.

What the platform provides instead is **attribution**: every decision names the
version that made it, every version names who approved it and why, and the whole
lifecycle is on a chain that can be verified. The report comes from a human, and
the diagnosis takes two queries.

Phase 7 is where this stops being reactive — a candidate policy is replayed
against historical events and the affected population is quantified *before*
anyone approves it. Until then, the control is the approval step, and the reason
field is what makes it a real one.

### Escalation

- A safety ruleset change that stopped the danger path firing is **critical** and
  is a page, not a ticket. See [safety-path-degraded.md](safety-path-degraded.md).
- If the chain does not verify — `policy_transitioned` events missing for a
  version that is live — that is not a policy incident, it is an integrity one.
  See [event-chain-integrity.md](event-chain-integrity.md).
