# §41 KPI drift

- **Severity:** info — **not** a page, and not something to fix under pressure
- **Owner:** DATA
- **Alerts:** `NemesisAgentInvocationRateElevated`, `NemesisClosureConfirmationRateLow`

> These alerts are conversation triggers, not incidents. Nothing is broken. A
> product-health KPI has moved outside the range §41 sets, which is a question
> for a working day, not a response at 2am.
>
> **The single most important instruction on this page: do not retune a threshold
> live.** Phase 7 exists so a configuration change can be backtested against
> historical events before anyone approves it. Changing a dedup band by hand
> because a KPI moved is how a system acquires settings nobody can justify.

## Symptoms

**`NemesisAgentInvocationRateElevated`** — more than 30 % of complaints escalate
to the Investigation Agent for an hour. §41.1 sets *no target* for this KPI and
says explicitly to track it as a baseline, because its value is as a signal
about the dedup bands (§14.3), not about the agent.

**`NemesisClosureConfirmationRateLow`** — under 60 % of closures are actively
confirmed by citizens rather than auto-confirmed-unconfirmed. This is the §3.1
trust-collapse mechanism read directly as a number.

## How to confirm

Open **NEMESIS — §41 Product Health KPIs** and check the volume panel first. A
ratio computed over a handful of complaints is noise wearing a percentage sign,
and on a local stack that is the overwhelmingly likely explanation.

```bash
curl -s 'localhost:9090/api/v1/query?query=nemesis:kpi_submission_rate_per_minute' | python -m json.tool
```

## Immediate mitigation

There is none, and that is correct. Record the observation and move on.

If the KPI has moved sharply rather than drifted, note what changed alongside
it — a policy activation, a model version, a new tenant — because the correlation
is the useful part and it is much harder to reconstruct later.

## Root cause investigation

**Agent invocation rate elevated:**
- The ambiguous dedup band (between `investigate_threshold` 0.65 and
  `merge_threshold` 0.85) is too wide for the data actually arriving.
- A new tenant taxonomy whose categories have higher visual variance than the
  civic set — a pothole and a garbage pile genuinely do not cluster alike.
- A classifier confidence shift pushing more decisions into the middle.

**Closure confirmation rate low:**
- The notification chain is failing silently. **Check this before concluding it
  is citizen apathy** — a notification that never arrived cannot be acted on, and
  attributing a delivery failure to disengagement is both wrong and unfair.
- The confirmation window is too short for the population.
- Genuine disengagement, which is the §3.1 mechanism the product exists to
  reverse. If this is the answer, it is a product finding, not an ops one.

## Prevention

- Phase 7 backtests a candidate policy against historical events and reports the
  delta *before* activation, with the affected population quantified.
- Phase 11's feedback loop turns human merge/split decisions into auto-tuning
  proposals, surfaced as drafts for approval — never applied automatically.
- Phase 24 makes the closure-confirmation question an experiment with a
  pre-registered hypothesis, rather than a number people argue about.
