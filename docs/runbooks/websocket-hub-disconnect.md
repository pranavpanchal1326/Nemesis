# WebSocket hub disconnects

- **Severity:** warning
- **Owner:** PLT
- **Alerts:** `NemesisWebsocketHubDegraded`

**Dependency:** websocket_hub

**Blueprint scenario:** WebSocket hub disconnects mid-demo

> §27.3 names this as a *demo* scenario, and that framing is the useful part:
> the system remains correct, it stops being *live*. Nothing is lost, nothing is
> mis-scored. The map goes stale and the cluster-merge scene stops animating.
> Under demo conditions that is a presentation problem; in production it is a
> perceived-freshness problem. Neither is a data problem.

## Symptoms

- `nemesis_system_degradation_total{dependency="websocket_hub"}` rising.
- Clients reporting the map stops updating while a page refresh shows correct,
  current state — the signature of a transport failure rather than a pipeline one.
- Connection count dropping sharply without a corresponding drop in HTTP traffic.

## How to confirm

```bash
docker compose exec api python -c "import urllib.request;print(urllib.request.urlopen('http://localhost:8000/ready',timeout=5).status)"
docker compose logs --tail=200 api | grep -i -E 'websocket|ws_|backpressure'
```

In the browser, the Network tab's WS entry shows the close code. Distinguish:

- **1001 / 1006** — the connection dropped. Network, proxy idle timeout, or laptop sleep.
- **1013 / server-initiated close** — the hub shed the client deliberately under
  backpressure. That is the designed behaviour (§26.3) and means a client stopped
  reading, not that the hub failed.

## Immediate mitigation

1. Confirm the fallback engaged. §27.3 specifies 5-second polling against
   `GET /api/v1/complaints/{id}`; the cluster-merge visualisation degrades to a
   manual refresh trigger rather than crashing.
2. If the hub is flapping — repeatedly accepting and dropping — force the clean
   fallback rather than letting clients thrash reconnect loops:

   ```bash
   nem flag kill realtime_websocket_hub --actor "$USER" --reason "hub flapping, incident <id>"
   ```

   Every client moves to polling within one reload interval. Polling is heavier
   on the API but it is *predictable*, and predictable degradation is the point.
3. **Before a demo**, exercise this deliberately rather than hoping. §27.3's own
   note — that this fallback should be tested at least once before demo day, not
   assumed — is a direct instruction, and the kill switch is how you follow it.
4. Restore when stable:

   ```bash
   nem flag clear realtime_websocket_hub --actor "$USER" --reason "hub stable"
   ```

## Root cause investigation

- **A slow consumer.** Per-connection backpressure (§26.3) sheds a client that
  stops reading so it cannot stall the hub. Shedding working is *success*, and it
  will look like this alert.
- **Laptop sleep / network change.** Overwhelmingly the most common cause in
  demo conditions.
- **An intermediary idle timeout.** No proxy exists locally; this becomes the
  first thing to check once Phase 1b puts a load balancer in front of the hub.
- **A missed heartbeat.** If heartbeats stopped, look at event-loop blocking in
  the API — a synchronous call on the async path starves the hub before it
  starves anything else, which makes this a leading indicator worth reading.

## Prevention

- Phase 3's gate requires that a client which stops reading is shed *without
  stalling the hub*, and that resumable cursors let a reconnecting client catch
  up without gaps.
- Phase 20 requires every 3D scene to ship a documented fallback exercised in
  CI by forcing the flag — so the degraded path is tested on every commit rather
  than discovered on stage.
- §27.4 pre-flight: confirm the WebSocket connection is live in dev tools before
  walking on stage.
