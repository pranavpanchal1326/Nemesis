# An activation was refused: the candidate has no passing certificate

- **Severity:** info — nothing is broken. A control did its job.
- **Owner:** DATA
- **Alerts:** none. This surfaces as a 409 to the operator who pressed Activate,
  which is the right place for it: the person who needs to know is already
  looking at the screen.

> **Read this before doing anything.** A refusal here does **not** mean the
> candidate is wrong. It means nobody has checked it. The tenant published a
> labelled evaluation set for this policy kind, which is what turns the guardrail
> on, and the candidate has no passing certificate over its exact bytes. The fix
> is to run an evaluation — one API call — and read what it says. It is not to
> find a way around the check.
>
> Nothing is degraded while this is happening. The previously active document is
> still deciding, still stamped on every decision, and still correct in the sense
> that it is what production has been using all along.

## Symptoms

- `POST /api/v1/control-plane/policies/{kind}/{revision}/activate` returns **409**
  with `type: https://nemesis.dev/problems/not-certified`.
- The detail names an evaluation set code and says to run an evaluation.
- An operator reports "I approved it and it won't go live."

Not this page:

- **403** — the control-plane token is missing or wrong.
- **409 with `type: .../conflict`** — the document is not in `approved`, or is
  already active. That is a lifecycle problem; see `policy-rollback.md`.
- **422** — the body is invalid for its kind.

## How to confirm

Find which set is gating the kind:

```bash
curl -s "http://localhost:8000/api/v1/control-plane/simulations/evaluation-sets?kind=<KIND>" -H "X-Tenant-Id: <TENANT>"
```

Exactly one entry will have `status: published`. That is the exam. Note its
`label_count` and `pass_ratio`.

Read what it actually asks:

```bash
curl -s "http://localhost:8000/api/v1/control-plane/simulations/evaluation-sets/<CODE>/labels" -H "X-Tenant-Id: <TENANT>"
```

Each label names a complaint, a written `rationale`, and the expectations a
candidate has to satisfy. If a label looks wrong, that is a finding about the
set — see "The set itself is wrong" below — not a reason to bypass it.

Check whether this candidate has been evaluated at all:

```bash
docker compose exec -T postgres psql -U nemesis -d nemesis -tAc "SELECT created_at, verdict, labels_passed, labels_evaluated, labels_unresolvable FROM policy_certificates WHERE tenant_id = '<TENANT>' AND kind = '<KIND>' ORDER BY created_at DESC LIMIT 5"
```

Three possibilities, and they call for different actions:

| What you see | What it means |
|---|---|
| No rows | Nobody has evaluated this candidate. Run one. |
| `verdict = fail` | It was evaluated and it disagreed with the labels. Read the findings. |
| `verdict = pass` but activation still refused | The certificate is for **different bytes**, or the set has been republished since. See below. |

## Immediate mitigation

### Run the evaluation

This backtests the candidate over history *and* marks it against the published
set, in one call:

```bash
curl -s -X POST "http://localhost:8000/api/v1/control-plane/simulations/runs" -H "X-Tenant-Id: <TENANT>" -H "X-Control-Plane-Token: <TOKEN>" -H "Content-Type: application/json" -d '{"kind": "<KIND>", "revision": <REVISION>, "certify": true}'
```

The response carries `certificate.verdict`. If it is `pass`, the activation will
now succeed. If it is `fail`, `certificate.findings.labels` lists every label the
candidate got wrong, with the expected and actual value for each — that is the
list to take to whoever wrote the candidate.

### If the run is refused with "too few complaints"

The window holds fewer than 30 complaints, and a comparison over a handful of
reports says "no regressions" with the same confidence whether or not that is
true. Widen it:

```bash
curl -s -X POST "http://localhost:8000/api/v1/control-plane/simulations/runs" -H "X-Tenant-Id: <TENANT>" -H "X-Control-Plane-Token: <TOKEN>" -H "Content-Type: application/json" -d '{"kind": "<KIND>", "revision": <REVISION>, "certify": true, "window_start": "2025-01-01T00:00:00Z"}'
```

A brand-new tenant genuinely has no history to evaluate against. That tenant
should not have a published evaluation set yet — retiring it is the honest
action, not lowering the floor.

### If this is an incident and the *current* policy is the problem

Do not try to force the candidate through. **Roll back**, which restores content
that was previously live and is therefore exempt from certification by design:

```bash
curl -s -X POST "http://localhost:8000/api/v1/control-plane/policies/<KIND>/rollback" -H "X-Tenant-Id: <TENANT>" -H "X-Control-Plane-Token: <TOKEN>" -H "Content-Type: application/json" -d '{"to_revision": <PREVIOUS>, "reason": "incident <ID>: <what is happening>"}'
```

This appends `policy_certification_waived` to the tenant chain, so the bypass is
on the record. See `policy-rollback.md` for the full procedure.

## Root cause investigation

### A passing certificate that still does not unlock the activation

Two causes, both deliberate:

**The bytes changed.** A certificate is keyed by the document's content hash, not
its revision number. Compare them:

```bash
docker compose exec -T postgres psql -U nemesis -d nemesis -tAc "SELECT v.revision, v.content_hash, c.verdict FROM policy_versions v LEFT JOIN policy_certificates c ON c.candidate_content_hash = v.content_hash AND c.tenant_id = v.tenant_id WHERE v.tenant_id = '<TENANT>' AND v.kind = '<KIND>' ORDER BY v.revision DESC LIMIT 5"
```

**The exam changed.** Publishing a new set retires the old one and moves the
`labels_hash`; certificates issued against the previous set no longer apply.
That is the intended behaviour — a candidate marked against different questions
has not been marked against these — and the remedy is to re-run the evaluation.

### The candidate genuinely regresses

Read `certificate.findings.labels`. Each entry names the complaint, what was
expected, and what the candidate produced. Take it to the author with the
backtest report beside it:

```bash
curl -s "http://localhost:8000/api/v1/control-plane/simulations/runs?kind=<KIND>" -H "X-Tenant-Id: <TENANT>"
```

The report's `severity.tier_transitions`, `routing.department_deltas` and
`sla.tightened` say what the change would do at scale, which is usually the
context that explains the failed labels.

### The set itself is wrong

It happens: a label was written against a rubric that has since legitimately
changed, and the set is now testing for behaviour nobody wants. **Labels are
frozen at publication and cannot be edited** — a guardrail whose questions can be
edited is one that can be made to pass by editing the exam. The correct path is
to create a new set, label it, and publish it; publishing retires the incumbent
in the same transaction.

Retiring without a replacement turns the guardrail off for that kind and writes
`evaluation_set_retired` to the chain:

```bash
curl -s -X POST "http://localhost:8000/api/v1/control-plane/simulations/evaluation-sets/<CODE>/retire" -H "X-Tenant-Id: <TENANT>" -H "X-Control-Plane-Token: <TOKEN>"
```

Do this deliberately and say why in the incident record. It is a real reduction
in safety, which is exactly why it is an event rather than a quiet update.

### A report that could not cover the change

If the run's report carries `coverage_gaps`, one of the candidate's routing rules
reads a fact the corpus cannot supply — `zone_code` or `tags`, neither of which
any event carries yet. Such a report is **not certifiable**, and the reason
matters: an absent fact compares `False`, so the rule would backtest as
affecting nobody, which is identical output to a change that genuinely does
nothing. Rewrite the rule against available facts, or wait for the phase that
supplies the fact (named in the gap's `reason`).

## Prevention

- **Evaluate before you approve, not after.** The run endpoint works on a draft.
  An author who runs it while writing sees the failed labels while the document
  is still editable, instead of an approver seeing a 409 an hour later.
- **Keep the set small and meaningful.** A set assembled from complaints somebody
  actually argued about is worth more than a hundred routine ones, and every
  label carries a written rationale precisely so the next person can tell which
  is which.
- **Set `pass_ratio` honestly.** 1.0 for a set of clear-cut cases; lower for one
  containing genuinely disputed complaints. Below 0.5 the service refuses it —
  a threshold that low does not fail a change, it endorses one.
- **Retire a set that no longer represents your intent** rather than leaving it
  to block work until somebody bypasses it. A control people route around is
  worse than no control, because it looks like one on the screen.

## Related

- `policy-rollback.md` — when the *live* document is the problem
- `taxonomy-misconfiguration.md` — when the categories the labels name have moved
- ADR-0028 — why the guardrail is a row rather than a call
- ADR-0029 — why the backtest replays the log
- ADR-0030 — why shadow mode cannot write
