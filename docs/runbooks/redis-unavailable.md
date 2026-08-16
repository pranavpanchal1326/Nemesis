# Redis unavailable

- **Severity:** critical
- **Owner:** PLT · SRE
- **Alerts:** `NemesisDependencyDown{dependency="redis"}`, `NemesisUnclassifiedDegradation`

**Dependency:** redis

> Redis carries three unrelated responsibilities: the Celery broker, the Celery
> result backend, and the feature flag override store. They fail together and
> they matter differently. Work out which one is hurting before acting, because
> the mitigation for a stalled queue and the mitigation for stale flags have
> nothing in common.

## Symptoms

- Tasks stop being consumed; queue depth flat rather than draining.
- `system_degradation` with `dependency="redis"` and `reason="flag_store_unavailable"`.
- Log event `flag_store_refresh_failed` (a snapshot exists — flags still work,
  frozen at their last known values) or `flag_store_unreachable_no_snapshot`
  (**no snapshot has ever loaded — kill switches are not in effect**).
- `redis-cli ping` failing or timing out.

## How to confirm

```bash
docker compose exec redis redis-cli ping
docker compose exec redis redis-cli info memory | grep -E 'used_memory_human|maxmemory_human'
docker compose exec redis redis-cli llen io
```

`maxmemory` is 192 MB with `noeviction`. That policy is deliberate: silently
evicting queue state is unacceptable, so Redis returns write errors instead of
losing tasks. **A write error under `noeviction` is the design working**, not a
misconfiguration to be relaxed.

## Immediate mitigation

1. **If memory is full**, find what is holding it. Task *results* are the usual
   answer (`result_expires=3600`), not the queues themselves:

   ```bash
   docker compose exec redis redis-cli --bigkeys
   docker compose exec redis redis-cli info keyspace
   ```

   Expired result keys can be dropped safely. Queue keys (`io`, `ml`, `safety`)
   must not be touched — deleting one loses accepted work, which is the exact
   outcome `noeviction` exists to prevent.

2. **If the flag store is the concern**, read the failure mode carefully:
   - `flag_store_refresh_failed` — the last snapshot is retained. Flags are
     frozen but correct as of the last successful load. No action needed.
   - `flag_store_unreachable_no_snapshot` — nothing ever loaded, so every flag
     is at its declared *default* and any kill switch pulled before this process
     started is **not being honoured**. If a kill switch was load-bearing,
     restoring Redis is now urgent, not merely important.

3. **If Redis is down**, restart it. The AOF/RDB `save 60 1` policy means at
   most 60 seconds of accepted-but-unpersisted queue state is at risk:

   ```bash
   docker compose restart redis
   docker compose restart worker-io worker-ml beat
   ```

   Workers are restarted deliberately: `broker_connection_retry_on_startup` gets
   them reconnected, but a worker that lost its broker mid-task can hold a stale
   connection. Redelivery is safe — every task is idempotent by contract.

## Root cause investigation

- **Result backend growth.** `result_expires=3600` with high throughput can
  outrun expiry. Phase 3 should consider `task_ignore_result` for tasks whose
  results nobody reads.
- **The 256 MB container limit** versus the 192 MB `maxmemory` setting — the
  gap is deliberate headroom for Redis's own overhead. Raising `maxmemory`
  toward the container limit trades a clean write error for an OOM kill.
- **A large flag override payload.** Not currently plausible (four flags), but
  worth ruling out if tenant targeting lists grow.

## Prevention

- Phase 25 exercises broker loss as an automated fault-injection test.
- The flag store's retain-last-snapshot behaviour is covered by a unit test; it
  is the single most important safety property of the flag system, because the
  alternative — clearing the snapshot — would silently revert every kill switch
  at the exact moment the system is already unhealthy.
