# Observability stack (Phase 1a)

Phase 0 instrumented the application — OpenTelemetry across FastAPI,
SQLAlchemy, asyncpg, Redis, and Celery, plus a Prometheus registry with the §41
domain metric families. It deliberately shipped no collector, so nothing had a
startup dependency on one. This directory is the backend those signals were
waiting for.

```bash
nem obs          # start it, and turn on trace export
nem obs-verify   # prove metric -> dashboard -> alert, end to end
nem obs-down     # stop it, and turn trace export back off
```

| | |
|---|---|
| Grafana | http://localhost:3001 (anonymous viewer; 3001 so it does not collide with the Next.js frontend on 3000) |
| Prometheus | http://localhost:9090 |
| Alertmanager | http://localhost:9093 |
| Tempo | http://localhost:3200 |

## Shape

```
  application  ──/metrics──────────────▶  Prometheus  ──▶ Alertmanager
       │                                      │
       └──OTLP/HTTP──▶ collector ──▶ Tempo    │
                                      │       │
                                      └───────┴──▶ Grafana
```

Metrics go straight from the application to Prometheus; only traces pass through
the collector (ADR-0008). The application already exposes a hand-built registry
with bucket edges chosen around the §27.1 budgets, and routing that through a
scrape-to-OTLP-to-re-export conversion would put a translation layer between us
and the numbers those budgets are judged on, for no benefit at this scale.

The whole stack is an **opt-in compose profile** (ADR-0007). The application
services already sum to 6336 MB inside an 8192 MB WSL2 cap; these five add
992 MB. It fits, but not by enough to make it the default on a machine that also
runs Ollama and a browser.

## Layout

```
otel-collector/config.yaml            traces only; memory_limiter sheds under pressure
tempo/tempo.yaml                      single-binary, local disk, 24h retention
prometheus/prometheus.yml             scrape config
prometheus/rules/recording-kpis.yml   §41 KPIs, defined exactly once
prometheus/rules/alerts.yml           17 alerts over §27.1 budgets and §27.3 scenarios
alertmanager/alertmanager.yml         routing, grouping, inhibition — no notifications yet
grafana/provisioning/                 datasources and the dashboard provider
grafana/dashboards/                   two dashboards, read-only
```

## Three conventions that CI enforces

These are checked by `scripts/check_observability.py` and
`scripts/check_runbooks.py`, because each failure mode is silent at runtime.

**1. One definition per KPI.** Every panel and every alert reads a
`nemesis:kpi_*` recording rule, never a raw histogram. A dashboard computing
`histogram_quantile` inline fails the check. This is a deliberate precursor to
Phase 23's governed metrics layer — the failure it prevents is the ordinary one,
where a panel, an alert, and a customer report each compute "classification
latency" slightly differently and then disagree in a meeting nobody can resolve.

**2. Label values are a contract with the code.** Every `stage="..."` and
`dependency="..."` selector must name a member of the corresponding `StrEnum` in
`nemesis/observability/metrics.py`. A panel querying `stage="classify"` while
the code emits `stage="classification"` renders an empty graph that looks
exactly like a healthy system with no traffic.

**3. Every alert has a runbook that exists.** `runbook_url` must resolve to a
file in `docs/runbooks/`. An alert whose runbook 404s at 2am is worse than no
alert.

`promtool check rules`, `promtool check config`, `amtool check-config`, and the
collector's own `validate` all run in CI as well — a rule file that does not
parse leaves Prometheus serving its **last good configuration** while logging
the failure, so the stack looks healthy and every alert has silently stopped
working.

## What is deliberately missing

**Notification integrations.** Alertmanager routes, groups, and inhibits;
receivers are named and terminal, with no Slack or PagerDuty attached. Paging
requires somebody to page — an on-call rotation is Phase 1b. A webhook wired to
a laptop stack would be a channel nobody watches, trained to be ignored before
the first real incident. Phase 1b adds integrations to receivers that already
exist rather than building a new routing tree.

**Worker metrics.** Celery workers serve no HTTP port and are not scraped.
Correct export needs `prometheus_client` multiprocess mode. Today
`nemesis/pipeline/` is empty and there are zero worker-side metrics to miss —
but the dashboards and alerts for pipeline stages already exist, and would show
no data. This must land with the first worker task in Phase 3; it is recorded in
`docs/PHASES.md` so it cannot be forgotten at the moment it starts to matter.

**Three §41 KPIs.** Dedup precision, dedup recall, and safety fail-safe
false-negative rate have no panel, and the dashboard says so in a panel of its
own. All three are defined against human-audited samples; a false negative is by
definition the case the system did not notice. A proxy metric that looks like the
KPI is worse than a visible gap, because the gap gets closed and the proxy gets
believed.

## Log correlation

Logs stay on stdout as structured JSON (§24.3) — there is no Loki here. Every
log line carries a `correlation_id` and a `trace_id`, so a line found with
`docker compose logs` can be pasted into Tempo. A log aggregator is a Phase 1b
decision, coupled to the deploy target like the rest of that phase.
