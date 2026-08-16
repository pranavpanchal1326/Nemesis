# Alerting pipeline heartbeat

- **Severity:** info — and it is **always firing**
- **Owner:** SRE
- **Alerts:** `NemesisAlertPipelineHeartbeat`

> This is the one page you read when an alert is **not** firing. The heartbeat
> evaluates `vector(1)`, so it fires unconditionally, forever. Its value is
> entirely in its absence: no conditionally-firing alert can ever prove the
> alerting path works, because "quiet" and "broken" look identical from outside.

## Symptoms

The heartbeat is missing from Alertmanager, or has gone `resolved`. Concretely:
the system has been silent for a while and you want to know whether that silence
means health.

## How to confirm

```bash
curl -s localhost:9093/api/v2/alerts | grep -c NemesisAlertPipelineHeartbeat
```

`1` means the whole chain works: Prometheus is evaluating rules, Alertmanager is
reachable, and routing is intact. `0` means every alert in this stack has
silently stopped working.

Then walk the chain in order — each step rules out everything before it:

```bash
curl -s localhost:9090/-/healthy                          # Prometheus alive
curl -s 'localhost:9090/api/v1/rules' | grep -c Heartbeat  # rules loaded
curl -s 'localhost:9090/api/v1/alerts' | grep -c Heartbeat # rule firing
curl -s localhost:9093/-/healthy                          # Alertmanager alive
```

The first of those to fail names the broken link.

## Immediate mitigation

1. **Rules not loaded** — a syntax error in a rule file means Prometheus keeps
   serving the *last good* configuration and logs the failure. The stack looks
   healthy and is not. Validate before reloading:

   ```bash
   docker compose exec prometheus promtool check rules /etc/prometheus/rules/*.yml
   curl -s -X POST localhost:9090/-/reload
   ```

2. **Rule firing but not in Alertmanager** — check reachability from inside the
   Prometheus container, not from the host:

   ```bash
   docker compose logs --tail=100 prometheus | grep -i alertmanager
   ```

3. **Alertmanager up but no alerts** — a stray silence or an over-broad inhibit
   rule. Inhibitions are the usual culprit, since they are written to suppress
   noise and occasionally suppress everything:

   ```bash
   curl -s localhost:9093/api/v2/silences
   ```

## Root cause investigation

- **A rule file edit that did not parse.** The most common cause by a wide
  margin, and the most dangerous, because Prometheus continues on the previous
  config rather than failing loudly. `promtool check rules` in CI is the fix;
  running it by hand is the mitigation.
- **The `obs` profile is not running.** Entirely expected — the observability
  stack is opt-in (ADR-0007). `nem obs` starts it.
- **An inhibit rule matching more than intended.** `NemesisTargetDown` inhibits
  all warning and info alerts by design; if a target is legitimately down, the
  heartbeat's own suppression is *correct* behaviour, not a bug.

## Prevention

- `nem obs-verify` walks this exact chain and is the Phase 1a gate: a metric
  emitted by the application reaches a Grafana dashboard and fires a configured
  alert, verified end to end rather than asserted.
- CI runs `promtool check rules`, so a rule file that would not load cannot merge.
- Phase 1b points this heartbeat at an external dead-man's-switch monitor, which
  is the only way its absence gets noticed without someone thinking to look.
