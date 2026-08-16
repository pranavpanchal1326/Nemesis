# Unclassified system degradation

- **Severity:** warning
- **Owner:** SRE
- **Alerts:** `NemesisUnclassifiedDegradation`

> This is the catch-all. It fires for a dependency with no dedicated alert rule,
> which means either a new degradation path was added without one, or an
> existing one is being taken for a reason nobody anticipated. Both are worth
> fifteen minutes of attention; neither is an emergency.
>
> The alert exists so that a new failure mode cannot be invisible **just because
> it is new** — the property that makes a catch-all worth its noise.

## Symptoms

- `nemesis_system_degradation_total` rising for 15 minutes on a dependency that
  is not `database`, `ollama`, or `websocket_hub`.
- The system is working. §24.2 makes degrading a first-class behaviour, so this
  fires while everything continues to function on a fallback path.

## How to confirm

```bash
curl -s 'localhost:9090/api/v1/query?query=nemesis:degradation_rate' | python -m json.tool
```

The `dependency` and `reason` labels together name the exact fallback being
taken. Cross-reference `reason` against the code path that emits it — every
`system_degradation` increment is written next to the fallback it describes.

## Immediate mitigation

1. **Decide whether the fallback is acceptable to run on.** In most cases it is:
   that is what a fallback is for, and §24.2 designed it deliberately. The
   question is whether it is acceptable *indefinitely*, and the answer is
   usually no.
2. **Check the accumulated cost.** A degradation that routes work to human review
   is free for the system and expensive for people. Queue depth is the real
   measure of impact here, not error rate.
3. **If this is a `redis` degradation with `reason="flag_store_unavailable"`**,
   go to [redis-unavailable.md](redis-unavailable.md) and read the distinction
   between a retained snapshot and no snapshot carefully — one is benign and one
   means kill switches are not in effect.

## Root cause investigation

Work outward from the label pair:

- **A new dependency added without an alert rule.** The correct fix is to add a
  specific rule and a runbook page, not to widen the exclusion list on this one.
  `scripts/check_runbooks.py` enforces that every declared `Dependency` has a
  page, so this should be caught before merge.
- **An existing dependency degrading for an unanticipated reason** — a new
  `reason` label value on a known dependency. Often the more interesting case,
  because it means a failure mode nobody modelled.
- **A retry budget exhausting under normal load**, so the fallback is being taken
  routinely rather than exceptionally. That is a tuning problem wearing an
  incident's clothes.

## Prevention

- Every external call must have a timeout, a retry budget, a fallback, and a
  `system_degradation` event — an engineering standard, enforced per phase.
- When this alert fires for a dependency twice, that is the signal to promote it
  to a dedicated rule with its own page. A catch-all that fires regularly has
  stopped being a catch-all and started being a backlog.
