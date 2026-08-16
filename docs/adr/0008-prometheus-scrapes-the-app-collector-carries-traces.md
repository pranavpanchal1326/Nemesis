# 0008 — Prometheus scrapes the application; the collector carries traces only

- **Status:** Accepted
- **Date:** 2026-08-16
- **Owner:** SRE
- **Blueprint:** §24.3, §27.1, §41

## Context

With an OpenTelemetry collector in the stack, there are two plausible paths for
metrics:

1. Prometheus scrapes the application's `/metrics` endpoint directly.
2. The collector's `prometheus` receiver scrapes the application, converts to
   OTLP, and re-exports — either back to a Prometheus exporter for scraping, or
   forward via remote write.

Option 2 is the conventional "single pipeline for all signals" architecture and
is genuinely better at scale, where you want one place to enrich, filter, and
route telemetry from many services.

The relevant fact is that this is not that situation. There is one metrics
producer, on one node, and the application already exposes a hand-built
Prometheus registry (`nemesis/observability/metrics.py`) whose histogram bucket
edges were chosen deliberately around the §27.1 budgets — 2 s submission ack,
8 s classification, 30 s end-to-end — rather than left at library defaults.

## Decision

**Prometheus scrapes `api:8000/metrics` directly. The collector's only pipeline
is traces**, exported to Tempo.

The collector's own self-telemetry is scraped by Prometheus on :8888, so the
collector is itself observable through the same path as everything else.

## Alternatives considered

**Route metrics through the collector.** Rejected for a specific reason rather
than a stylistic one: the scrape → OTLP → re-export path has subtly different
staleness semantics and `_total`-suffix handling than a direct scrape. Those
differences are small, well-documented, and exactly the kind of thing that
produces a number nobody can reconcile at 2am. Adding a translation layer
between us and the numbers the §27.1 budgets are judged on buys nothing today.

**Remote-write from the collector to Prometheus.** Same objection, plus it
inverts the pull model that makes `up` a meaningful signal — with push, a dead
producer and a healthy producer sending nothing look identical.

**Drop the collector and export traces straight to Tempo.** Tempo does accept
OTLP directly. Rejected because the collector is where per-environment
processing belongs (the `memory_limiter` that sheds spans under pressure rather
than being OOM-killed, the `resource` processor stamping the environment), and
because Phase 1b will need exactly that indirection when there is more than one
environment. This is the one piece of Phase 1a built slightly ahead of need, and
it is cheap.

## Consequences

**Easier:** metric semantics are exactly what `prometheus_client` produces, with
no conversion. Bucket edges, label cardinality guards, and the `/metrics`
contract stay the single source of truth.

**Harder:** two collection paths to understand instead of one. If a future
service exposes metrics only via OTLP, it will need the collector path adding —
at which point the two paths must be reconciled rather than left to diverge.

**A known gap this creates:** Celery workers serve no HTTP port, so they are not
scraped. Correct worker-side export requires `prometheus_client` multiprocess
mode — a shared `PROMETHEUS_MULTIPROC_DIR` and a `multiprocess_mode` on every
Gauge. Today `nemesis/pipeline/` is empty and there are exactly zero worker-side
metrics to miss, so this is deferred rather than solved. **It must land in Phase
3 alongside the first worker task**, and is recorded in `docs/PHASES.md` so it
cannot be forgotten at the moment it starts to matter. Deferring it here is a
decision, not an oversight.

## Revisit when

A second metrics-producing service exists, worker metrics land in Phase 3, or a
deployed environment needs cross-service enrichment that only a collector
pipeline can do.
