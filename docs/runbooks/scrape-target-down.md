# Scrape target down

- **Severity:** critical
- **Owner:** SRE
- **Alerts:** `NemesisTargetDown`

> This alert inhibits every warning and info alert for the same environment, and
> that is deliberate: if a target cannot be scraped, every ratio and quantile
> derived from it is stale, and alerting on stale data sends the responder
> somewhere the problem is not.

## Symptoms

- `up{job="..."} == 0` for more than two minutes.
- Dashboard panels flat-lining or showing "No data" while the application is
  demonstrably serving requests.
- A sudden, suspiciously clean drop to zero across several unrelated panels at
  the same instant — a hallmark of a scrape failure rather than a real one.

## How to confirm

```bash
curl -s 'localhost:9090/api/v1/targets?state=any' \
  | python -c "import json,sys;[print(t['labels']['job'], t['health'], t.get('lastError','')) for t in json.load(sys.stdin)['data']['activeTargets']]"
```

Then try the scrape yourself from inside the Prometheus container, because a
target reachable from the host but not from Prometheus is a different problem
with a different fix:

```bash
docker compose exec prometheus wget -q -O - http://api:8000/metrics | head -5
```

## Immediate mitigation

1. **`nemesis-api` down** — this is not an observability incident, it is an
   application incident. Go to [dependency-down.md](dependency-down.md) and
   treat the missing metrics as a symptom.
2. **`otel-collector` down** — traces are being lost, metrics are unaffected.
   The collector is memory-capped at 128 MB with a `memory_limiter` that sheds
   spans under pressure; an OOM kill means the limit is set above what the
   container can actually use.

   ```bash
   docker compose logs --tail=100 otel-collector
   docker compose restart otel-collector
   ```

3. **`alertmanager` down** — alerts are being evaluated and going nowhere. This
   is the quiet failure: Prometheus keeps working and nothing reaches a human.

   ```bash
   docker compose restart alertmanager
   ```

4. **Everything down** — the `obs` profile is not running. Expected state on a
   default `nem up` (ADR-0007). Start it with `nem obs`.

## Root cause investigation

- **Container OOM.** Check `docker inspect --format '{{.State.OOMKilled}}'` on
  the service. The observability stack runs on 992 MB total inside an already
  tight 8 GB WSL2 budget, so this is a real possibility rather than a formality.
- **A compose edit that changed a service name.** Scrape targets are addressed
  by service name; renaming one breaks the scrape while everything else works.
- **The API is up but `/metrics` is failing.** Rare, but distinguishable: the
  scrape error will be an HTTP status rather than a connection refusal.

## Prevention

- Every observability service that has a shell has a healthcheck. The collector
  is distroless and has none by design — Prometheus scraping its self-telemetry
  is the stronger signal anyway, because it also proves the scrape path itself.
- Phase 28's capacity model gives these limits a stated basis rather than the
  current one, which is "what fit alongside everything else on this laptop".
