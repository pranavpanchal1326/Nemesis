# Runbook index

A runbook is written for someone who is **tired, under pressure, and did not
build this component**. That reader shapes everything about the format: the
mitigation comes before the explanation, commands are copy-pasteable rather than
described, and every page says plainly whether the situation is urgent — because
"is this actually bad?" is the first question and the hardest one to answer from
a dashboard.

Several pages here open by telling you *not* to act quickly. That is deliberate.
§24.2 makes degrading a first-class behaviour, so a system taking a documented
fallback is working as designed, and the reflex to restart something is usually
wrong.

## How these are kept honest

`scripts/check_runbooks.py` runs in CI and fails the build when:

1. A `**Scenario:**` heading in Blueprint §27.3 has no page claiming it. The
   scenarios are parsed **from the blueprint**, not from a copied list, so a
   scenario added later fails immediately rather than being noticed never.
2. A declared `Dependency` the system can degrade against has no page.
3. A page is not linked from this index. An unlinked runbook will not be found
   under pressure, which is the same as not existing.
4. A page is missing a required section.

`scripts/check_observability.py` additionally fails when an alert's
`runbook_url` points at a file that does not exist. An alert whose runbook 404s
at 2am is worse than no alert.

This is the Phase 1a gate — *every §27.3 scenario has a runbook page* —
implemented rather than asserted, per the plan's own rule that every gate is a
test and not an opinion.

## Required sections

Every page carries these, in this order:

```markdown
## Symptoms
## How to confirm
## Immediate mitigation
## Root cause investigation
## Prevention
```

`## Prevention` is not filler. It is where a runbook records the phase and gate
that will make it obsolete — most pages here are bridges to an automated control
that lands later, and saying which one keeps them from ossifying into permanent
manual process.

## Index

### Blueprint §27.3 scenarios

The three the blueprint names explicitly. All three are exercised as automated
fault-injection tests in Phase 25.

| Page | Scenario | Severity |
|---|---|---|
| [ollama-unreachable.md](ollama-unreachable.md) | Ollama/LLM service unreachable | warning |
| [database-pool-exhausted.md](database-pool-exhausted.md) | Database connection pool exhausted | critical |
| [websocket-hub-disconnect.md](websocket-hub-disconnect.md) | WebSocket hub disconnects mid-demo | warning |

### Dependencies

| Page | Dependency | Severity |
|---|---|---|
| [dependency-down.md](dependency-down.md) | any — readiness failing | critical |
| [redis-unavailable.md](redis-unavailable.md) | `redis` — broker, results, flag store | critical |

### Service level

| Page | Covers | Severity |
|---|---|---|
| [slo-latency-budget-breach.md](slo-latency-budget-breach.md) | §27.1 processing budgets | warning |
| [safety-path-degraded.md](safety-path-degraded.md) | §11.2 fail-safe latency | **critical** |
| [pipeline-stage-failures.md](pipeline-stage-failures.md) | A stage failing rather than degrading | warning |
| [http-error-rate.md](http-error-rate.md) | 5xx ratio | critical |
| [readiness-failing.md](readiness-failing.md) | 503 with healthy dependencies | warning |
| [system-degradation.md](system-degradation.md) | Catch-all for unclassified fallbacks | warning |

### Product health

| Page | Covers | Severity |
|---|---|---|
| [kpi-drift.md](kpi-drift.md) | §41 KPIs outside range | info |

### Event store (Phase 2)

The append-only log is the system of record, so both of these are about the
integrity of evidence rather than about availability.

| Page | Covers | Severity |
|---|---|---|
| [event-chain-integrity.md](event-chain-integrity.md) | A chain no longer recomputes, or the sweep that detects that has stopped | **critical** |
| [event-partition-maintenance.md](event-partition-maintenance.md) | Rows stranded in the DEFAULT partition; retention and archival | warning |

### Procedures and the observability stack itself

| Page | Covers | Severity |
|---|---|---|
| [feature-flag-kill-switch.md](feature-flag-kill-switch.md) | Pulling and restoring an emergency handle | procedure |
| [credential-leak.md](credential-leak.md) | A secret reached somewhere it should not | critical |
| [alert-pipeline-heartbeat.md](alert-pipeline-heartbeat.md) | The alerting path itself stopped working | info |
| [scrape-target-down.md](scrape-target-down.md) | Prometheus cannot reach a target | critical |

## Writing a new page

1. Copy the section skeleton above.
2. If it covers a `Dependency` enum member, add `**Dependency:** <value>` on its
   own line. If it covers a §27.3 scenario, add `**Blueprint scenario:** <exact
   heading text>`. Both are parsed by the checker.
3. Link it from this index.
4. Point at least one alert's `runbook_url` at it — a runbook with no alert is a
   page nobody will ever be routed to.

## Related

- [Incident process](../incidents/README.md) — severity, roles, and the
  blameless post-mortem.
- [Secrets and rotation](../SECRETS.md)
- [Release policy](../RELEASE.md) — how to find out what changed, which is
  usually the second question in any incident.
