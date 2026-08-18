# Public API flood — the limiter is refusing requests

**Owning function:** PLT · **Phase:** 4 · **Blueprint:** §16.3, §26.4

**Alerts that land here:** `NemesisPublicApiFloodSustained`

**A flood alert on this surface is usually the system working.** §26.4 promises
a read-only, unauthenticated, rate-limited API, and the limiter refusing
requests is that promise being kept. The alert is `info` severity for exactly
that reason: it exists so somebody *knows*, not so somebody *acts*.

The wrong reflexes, in the order people reach for them:

1. **Tightening the limit.** §26.4 publishes 60 req/min/IP. Lowering it breaks
   legitimate consumers to inconvenience an abuser who is already being refused.
2. **Blocking the address.** Behind a shared NAT this blocks an institution;
   behind a rotating proxy it blocks nobody.
3. **Turning the surface off.** §16.3's point is that this is infrastructure
   other tools build on. Taking it down delivers the outcome the abuse was going
   to cause anyway.

The right answer for a serious consumer is a key with a real quota. That is why
`api_keys` exists, and it is a five-minute conversation.

## Symptoms

- `nemesis_public_api_requests_total{outcome="rate_limited"}` sustained above a
  few per second for fifteen minutes
- Reports that a public transparency page is intermittently slow
- A single endpoint dominating the request mix

## How to confirm

```bash
docker compose exec -T api sh -c "curl -s localhost:8000/metrics | grep nemesis_public_api_requests_total"
```

| Pattern | Reading |
|---|---|
| `rate_limited` high, `ok` also high | One noisy client, everyone else served. Working as designed. |
| Both near zero, alert firing | Redis is down and the limiter **failed open** — see mitigation 3. |
| `ok` collapsed to zero | Not a flood. Check `/ready` and the database. |

The endpoint label tells you what they are pulling. A crawler walking `/zones`
and then every `/ward/{code}/summary` is building a dataset the hard way — they
want the bulk export and do not know it exists.

## Immediate mitigation

### 1. Usually: nothing

Confirm legitimate traffic is still served and close the alert:

```bash
curl -s -o /dev/null -w "%{http_code}\n" localhost:8000/api/v1/public/SLUG/zones
```

### 2. Convert the consumer

There is no per-client label on the metrics, deliberately — a client address is
personal data under DPDP and a Prometheus label is a place it would be retained
indefinitely. Use the access logs, which carry a correlation id and normal
retention:

```bash
docker compose logs --since=30m api | grep -c "public"
```

If the consumer is identifiable and legitimate, issue a key:

```
POST /api/v1/integrations/keys
X-Control-Plane-Token: <token>
X-Tenant-ID: <the publishing tenant>

{"name": "<who they are>", "scopes": ["public:read", "export:read"], "quota_per_hour": 10000}
```

`export:read` is usually the fix rather than a larger `public:read` quota — a
crawler making 40 000 aggregate calls wanted one CSV.

### 3. Redis is down and the limiter failed open

ADR-0017: the limiter fails open and counts every time it does. On the ingest
path that protects a citizen's hazard report; here the reasoning is weaker and
still holds — a Redis outage turning the public accountability surface dark is
worse than a few minutes of unbounded scraping, and the outage already alerts.

```bash
docker compose exec -T api sh -c "curl -s localhost:8000/metrics | grep 'rate_limit_decisions_total.*failed_open'"
```

A climbing counter means this is `redis-unavailable.md`, not an abuse incident.

### 4. If it is genuinely malicious

In order of preference:

1. **Per-tenant opt-out** — reversible, auditable, no deploy:

   ```bash
   docker compose exec -T postgres psql -U nemesis -d nemesis -c "UPDATE tenants SET public_api_enabled = false WHERE slug = 'SLUG'"
   ```

   Tell the tenant. Their page is now 404 and they will otherwise hear it from a
   citizen before they hear it from us.

2. **Deployment-wide kill** — `NEMESIS_PUBLIC_API__ENABLED=false` and restart.
   Last resort, and it is a public accountability surface going dark: record it
   as an incident with the §16.3 commitment stated in the write-up.

3. **Edge blocking** belongs at the edge. Phase 1b owns the deployment target
   and is where a WAF rule lives.

**Do not** raise `min_aggregate_floor` to make scraping less useful — it is a
privacy control, not a rate control, and changing it silently changes what every
tenant publishes. **Do not** add a client-address label to the metrics. **Do
not** cache more aggressively without re-reading `nemesis/public/policy.py`: the
`Cache-Control: public` directive is only safe because no public response
carries caller-specific data.

## Root cause investigation

The counter is monotonic — watch the *rate* flatten, not the value fall:

```bash
docker compose exec -T api sh -c "curl -s localhost:8000/metrics | grep 'nemesis_public_api_requests_total.*rate_limited'"
```

Then answer two questions in the write-up:

- **Was this a consumer we should have had a relationship with?** A repeat
  flood from the same institution is a sales conversation that did not happen.
- **Did the limiter's identity key do anything useful?** It is the client
  address, which is the weakest identity available and the only one an
  unauthenticated endpoint has. If the traffic came from a rotating source, the
  limiter did nothing and the incident should say so rather than crediting a
  control that did not act.

## Prevention

- **Issuing keys is the actual prevention.** Every consumer with a key is one
  whose traffic is attributable, quota'd, and countable in `api_key_usage`
  rather than anonymous in an aggregate counter.
- **Phase 1b** brings a deployment target and with it edge rate limiting, which
  is where volumetric abuse should be absorbed — an application-level limiter
  still costs a request handler and a Redis round trip per refused request.
- **Phase 27 (metering)** turns quotas into a commercial conversation rather
  than an operational one, at which point "this consumer is over budget" stops
  being an alert at all.
