# Webhook delivery failing — a partner integration has gone quiet

**Owning function:** PLT · **Phase:** 4 · **Blueprint:** §16.3

**Alerts that land here:** `NemesisWebhookFanOutStalled`,
`NemesisWebhookBacklogGrowing`, `NemesisWebhookEndpointsDisabled`,
`NemesisWebhookDeliveryLagHigh`

**No data is lost by a webhook failure.** The events are in the append-only log
and the delivery rows are durable; a stalled dispatcher delays a partner
integration, it does not drop a citizen's report. Resist treating this as a
data-loss incident — the wrong response is a hurried manual `DELETE` on the
delivery table. What *is* at risk is a tenant's integration silently going
quiet, and a subscriber cannot distinguish "nothing happened" from "you stopped
sending".

## Symptoms

- Deliveries queued with nothing succeeding (`NemesisWebhookFanOutStalled`)
- A backlog growing while deliveries *are* landing (`NemesisWebhookBacklogGrowing`)
- Endpoints disabled after exhausting their failure budget
- p95 delivery lag in the minutes rather than the seconds
- A tenant reports their handler has received nothing since a given time

## How to confirm

Three failures wear similar symptoms. One query separates them:

```bash
docker compose exec -T postgres psql -U nemesis -d nemesis -c "SELECT status, count(*), min(next_attempt_at) AS soonest, max(attempts) AS worst FROM webhook_deliveries GROUP BY status"
```

| What you see | What it is | Mitigation section |
|---|---|---|
| `pending`, `soonest` in the past, nothing delivering | The dispatcher is not running | 1 |
| `pending`, `soonest` in the future, high `attempts` | Subscribers are refusing | 2 |
| Few `pending`, but `nemesis_webhook_deliveries_pending` high | The fan-out has not enqueued | 3 |

## Immediate mitigation

### 1. The dispatcher is not running

The dedicated process is the primary path; the Celery beat tasks are a bounded
safety net running every 30 seconds. If beat is alive, throughput is degraded
rather than zero — "some deliveries are landing, slowly" points here.

```bash
docker compose ps webhooks
```

```bash
docker compose up -d webhooks
```

Two dispatchers running at once is **safe**: rows are taken
`FOR UPDATE SKIP LOCKED`, so a second one is concurrent rather than duplicative.
Do not spend time hunting for a duplicate process.

### 2. Subscribers are refusing

```bash
docker compose exec -T postgres psql -U nemesis -d nemesis -c "SELECT e.url, d.last_status_code, d.last_error, count(*) FROM webhook_deliveries d JOIN webhook_endpoints e ON e.id = d.endpoint_id WHERE d.status <> 'delivered' GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 20"
```

Read the status code before acting.

| Code | Meaning | Action |
|---|---|---|
| No code, connection error | Their host is unreachable | Nothing. The schedule spans ~10 hours; it drains. |
| `429`, `408` | They are throttling us | Nothing. Both are explicitly retryable. |
| `5xx` | Their service is broken | Contact the tenant. Retrying is correct meanwhile. |
| `401`, `403` | Signature verification failing | See below — usually ours to explain |
| `410` | They removed the endpoint | Terminal by design. Ask them to delete the subscription. |

**Signature failures** are almost always one of three things:

1. **The receiver verifies the parsed body, not the raw bytes.** Any JSON
   round-trip reorders keys and the MAC no longer matches. The developer
   portal's worked example verifies raw bytes; point them at it.
2. **A secret was rotated and their deploy has not landed.** There is
   deliberately no overlap window. Compare fingerprints — the column exists so
   neither side has to transmit the secret:

   ```bash
   docker compose exec -T postgres psql -U nemesis -d nemesis -c "SELECT url, secret_version, secret_fingerprint FROM webhook_endpoints"
   ```

3. **Their clock is wrong.** The timestamp is inside the signed string and the
   recommended tolerance is 300 seconds. A receiver ten minutes out of sync
   rejects everything, and it looks identical to a bad secret.

**Never** disable signature verification, send an unsigned payload, or paste a
secret into a support channel to "check it".

### 3. The fan-out has not enqueued

```bash
docker compose exec -T postgres psql -U nemesis -d nemesis -c "SELECT c.last_outbox_id, (SELECT max(id) FROM outbox_messages) AS newest, (SELECT count(*) FROM outbox_messages WHERE id > c.last_outbox_id) AS behind FROM webhook_cursor c"
```

The outbox purge consults `sweep_outbox_safe_below()` and refuses to delete at
or above the cursor, so a stalled fan-out makes the outbox **grow** rather than
lose events. Expect `NemesisOutboxBacklogGrowing` alongside it — that is *this*
failure, and restarting the relay will not help.

Restart the dispatcher. The cursor advances in the same transaction as the rows
it produces, so a restart re-reads the batch and the unique constraint makes
that a no-op.

**Do not** manually advance the cursor to clear a backlog. Every event skipped
is one no subscriber will ever receive, with nothing left to show it — the
deliveries were never created, so there is no failed row to find later.

### 4. An endpoint has been disabled

```bash
docker compose exec -T postgres psql -U nemesis -d nemesis -c "SELECT url, disabled_at, disabled_reason, consecutive_failures FROM webhook_endpoints WHERE is_active = false AND disabled_at IS NOT NULL"
```

Re-enabling is the **tenant's** assertion that they have fixed it, made through
`POST /api/v1/integrations/webhooks/{id}/active` — it clears the failure
counter, which is why an operator doing it on their behalf tends to produce a
second disablement an hour later. Events that arrived while disabled are **not**
replayed; if the tenant needs the gap, the bulk export covers it.

## Root cause investigation

Confirm recovery first:

```bash
docker compose exec -T postgres psql -U nemesis -d nemesis -c "SELECT count(*) FILTER (WHERE status='pending') AS pending, count(*) FILTER (WHERE status='delivered' AND delivered_at > now() - interval '5 minutes') AS just_delivered FROM webhook_deliveries"
```

`just_delivered` rising and `pending` falling is recovery. `pending` falling
with `just_delivered` flat means rows are going *terminal* — check
`status='failed'` before declaring the incident over.

Then establish which of the three failures it was, and whether the alert fired
before or after a tenant noticed. A tenant noticing first means the lag alert
threshold is wrong for that integration's expectations.

Escalate rather than improvise when:

- **Signature errors across every tenant** — suspect
  `NEMESIS_WEBHOOK_SIGNING_KEY` changed. Every tenant's secret derives from it,
  so rotating it invalidates every subscriber at once (`docs/SECRETS.md`).
- **The cursor is ahead of `max(outbox_messages.id)`** — the outbox was
  truncated or restored out of step with the cursor. Stop the dispatcher and
  escalate; deliveries are being skipped.

## Prevention

- **The retention floor is the control that already landed.** Before Phase 4,
  the outbox purge would have deleted rows the fan-out had not read, and nothing
  would have reported the gap. `sweep_outbox_safe_below()` closes it and
  `test_the_outbox_purge_will_not_delete_past_the_fanout_cursor` holds it closed.
- **Phase 25 (fault injection)** is where the dispatcher meets `toxiproxy`
  rather than a mocked transport — the hour-long-outage drill is currently a
  simulated clock in the test suite, which proves the scheduling arithmetic and
  not the socket behaviour.
- **Phase 27 (support console)** removes most of this page: a tenant inspecting
  their own delivery log and re-enabling their own endpoint is self-service, and
  every `psql` command above is a query an operator should not be writing by
  hand.
