# Outbox relay stalled — the map has gone quiet

- **Severity:** warning (page if the backlog is still growing after 15 minutes)
- **Owner:** PLT
- **Alerts:** `NemesisOutboxBacklogGrowing`, `NemesisOutboxDispatchLagHigh`

> **Nothing is lost while this is happening.** The outbox is a durable queue in
> Postgres and every undispatched row is still there; the events themselves are
> in the append-only log and were never at risk. What is broken is *delivery*, so
> the citizen-facing symptom is a map that does not update and a §19.2 merge
> animation that never fires — not a complaint that vanished. Read that sentence
> before deciding how hard to push, because a stalled relay looks alarming and is
> the mildest realtime failure the system has.

## Symptoms

- `nemesis_outbox_pending_messages` climbing and not coming back down.
- `nemesis_outbox_dispatch_lag_seconds` p95 in the tens of seconds or worse.
- Clients connected, receiving heartbeats, and receiving no events.
- `relay` container unhealthy, restarting, or logging `relay_standby` on a loop.

## How to confirm

```bash
docker compose ps relay
```

```bash
docker compose logs relay --tail 50
```

```bash
docker compose exec -T postgres psql -U nemesis -d nemesis -tAc "SELECT count(*), min(created_at) FROM outbox_messages WHERE dispatched_at IS NULL"
```

A backlog with an old `min(created_at)` is a stalled relay. A backlog with a
*recent* one is a burst the relay is working through, which is the system
behaving correctly under load.

## Immediate mitigation

1. **Check which failure this is, from the logs:**

   - `relay_standby` repeatedly — another process holds the advisory lock. That
     is by design with more than one replica, but if there is only one relay it
     means a *previous* relay's database session is still open. Find it:

     ```bash
     docker compose exec -T postgres psql -U nemesis -d nemesis -tAc "SELECT pid, state, backend_start FROM pg_stat_activity WHERE pid IN (SELECT pid FROM pg_locks WHERE locktype = 'advisory')"
     ```

     Terminating that backend releases the lock. Do not restart Postgres for this.

   - `outbox_publish_failed` — Redis is refusing publishes. Go to
     [redis-unavailable.md](redis-unavailable.md); this page is a symptom of that
     one.

   - `relay_pass_failed` — the database is refusing the relay's connections. Go
     to [database-pool-exhausted.md](database-pool-exhausted.md).

   - Nothing at all, and the container is up — the loop is alive and finding no
     work, which means the backlog is not what you think it is. Re-run the
     confirmation query.

2. **Restart the relay** if the logs show no ongoing cause. It is stateless: the
   queue is the database, and a restart resumes from the same rows.

   ```bash
   docker compose restart relay
   ```

3. **Tell clients to fall back.** §27.3's documented fallback is 5-second polling
   against `GET /api/v1/complaints/{id}`, and the handle that forces it is the
   kill switch — which refuses the WebSocket *handshake* rather than accepting
   and closing, so clients take the fallback instead of reconnecting in a loop.

   ```bash
   docker compose exec -T api python -m nemesis.flags kill realtime_websocket_hub --actor you --reason "relay stalled"
   ```

4. **Do not delete outbox rows.** They are the queue. Deleting undispatched rows
   is the one action on this page that turns a delivery incident into permanent
   data loss for the realtime layer — the events survive, but no client will ever
   be told about them.

## Root cause investigation

- **A publish that keeps failing on the same row.** The relay stops the batch at
  the first failure rather than skipping ahead, deliberately: publishing later
  rows past a failed one delivers an entity's events out of order, and arrival
  order is the consumer's only ordering signal. The consequence is that one
  poison row blocks everything behind it. Identify it:

  ```bash
  docker compose exec -T postgres psql -U nemesis -d nemesis -tAc "SELECT id, event_type, attempts, last_error FROM outbox_messages WHERE dispatched_at IS NULL ORDER BY id LIMIT 5"
  ```

- **The purge task not running**, so the table has grown large enough that even
  the partial index is slow. Check that `nemesis.integrity.purge_outbox` is
  firing — the beat schedule is hourly.

- **A relay that is alive, healthy, and hours behind.** This is the failure the
  lag histogram exists for: every liveness signal is green and the product is
  broken. Trust `nemesis_outbox_dispatch_lag_seconds` over the healthcheck.

## Prevention

- The relay's liveness is a process check, not an HTTP probe — it serves no port,
  and if the process dies the map goes quiet while every other service stays
  green.
- `nem gate-phase3` exercises the whole path — submission, outbox, relay,
  WebSocket — against the running stack, including a worker kill.
- Phase 25's fault injection is where a deliberately unreachable Redis becomes an
  automated test of this page rather than a procedure nobody has rehearsed.
