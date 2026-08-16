# INC-YYYY-MM-DD — <one line, in plain language>

- **Severity:** SEV-1 | SEV-2 | SEV-3
- **Status:** Investigating | Mitigated | Resolved | Post-mortem complete
- **Detected:** YYYY-MM-DDTHH:MM:SSZ — by <alert name | customer report | someone noticed>
- **Mitigated:** YYYY-MM-DDTHH:MM:SSZ
- **Resolved:** YYYY-MM-DDTHH:MM:SSZ
- **Lead:** · **Operator:** · **Scribe:**
- **Runbooks used:** [page](../runbooks/page.md)

## Impact

Who was affected, how many, and for how long — in citizen terms first, system
terms second. "412 submissions queued for 18 minutes; none lost" is useful.
"Elevated p99" is not, because nobody can tell from it whether anything actually
happened to anyone.

State explicitly whether any complaint was **lost**, **mis-scored**, or
**incorrectly merged**. Those three are the failures this product exists to
prevent, and their absence is worth asserting rather than implying.

## Timeline (UTC)

Written *during* the incident by the scribe. Include what was believed at each
point, not only what turned out to be true — the gap between the two is usually
where the finding is.

| Time | Event |
|---|---|
| 00:00 | `NemesisX` fired |
| 00:02 | Acknowledged; opened runbook |
| 00:0? | *Believed* cause was X, based on Y |
| 00:0? | X ruled out because Z |
| 00:0? | Mitigated by … |
| 00:0? | Alert cleared; verified via `<metric>` |

## Detection

- What fired, or who noticed.
- **Time to detect** from first impact.
- Would an existing alert have caught this sooner? If a human noticed before the
  monitoring did, that is a finding in its own right and belongs in the actions.

## Contributing factors

Plural. A single-cause incident is rare enough that finding exactly one should
raise the suspicion that the investigation ended early rather than finished.

For each: what made the wrong thing easy, or the right thing hard?

## What went well

Not a morale exercise. Controls that worked are controls worth protecting from
a future refactor, and they are invisible unless written down — the fallback
that held, the alert that fired correctly, the kill switch that was already
declared.

## What was luck

The most valuable section and the one most often skipped. What would have made
this materially worse, and why did it not happen this time? Luck is not a
control, and anything that depended on it is a finding.

## Actions

Every row goes into [action-register.md](action-register.md). An action with no
owner and no date is a wish.

| # | Action | Type | Owner | Due |
|---|---|---|---|---|
| 1 | | detect / mitigate / prevent | | |

Rejected as action items, always:

- "Be more careful."
- "Add documentation", unless the documentation is a runbook page that an alert
  actually links to.
- "Human error" as a root cause — that is a description of where the
  investigation stopped, not of what happened.

## Blueprint / phase impact

Does this change a gate, an ADR, or a phase's scope? An incident that reveals a
gate was insufficient is the strongest possible argument for changing it, and
that argument is only available while the incident is fresh.
