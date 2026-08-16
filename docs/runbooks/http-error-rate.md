# Elevated HTTP error rate

- **Severity:** critical
- **Owner:** PLT
- **Alerts:** `NemesisHighErrorRate`

## Symptoms

- `nemesis:http_error_ratio > 0.05` for five minutes.
- 5xx responses concentrated on one route template, or spread across all of
  them — those two shapes have entirely different causes and the distinction is
  the first thing to establish.

## How to confirm

```bash
curl -s 'localhost:9090/api/v1/query?query=sum by (endpoint,status) (rate(nemesis_http_requests_total{status=~"5.."}[5m]))' \
  | python -m json.tool
```

Then read an actual response body. The RFC 9457 error contract carries a `type`
that groups the failure mode without disclosing internals, which is usually
enough to classify the problem without opening logs:

```bash
docker compose logs --tail=200 api | grep -E '"level":\s*"error"' | head -20
```

Every log line carries a `correlation_id`, and that id is also the trace
baggage — so one failing request can be followed from the log line into Tempo
and across the queue boundary.

## Immediate mitigation

1. **Concentrated on one endpoint** — that endpoint's handler is the problem.
   Check whether it is behind a feature flag; if so, that is the fastest
   containment available:

   ```bash
   curl -s localhost:8000/ops/flags | python -m json.tool
   ```

2. **Spread across all endpoints** — this is not an HTTP problem. It is a
   dependency problem presenting as one. Go to
   [dependency-down.md](dependency-down.md); a 503 storm from a failed readiness
   check will show up here as well.

3. **Errors only on write paths** while reads succeed — pool exhaustion. See
   [database-pool-exhausted.md](database-pool-exhausted.md).

4. **500s inside the error handler itself.** Rare and nasty, and it has happened
   here before: Starlette's deprecation of `HTTP_422_UNPROCESSABLE_ENTITY` raised
   *inside* the validation handler under `filterwarnings = ["error"]`, turning
   every 422 into a 500. If validation errors are appearing as 500s, suspect the
   handler before the handler's callers.

## Root cause investigation

- **A dependency failure surfacing as 5xx.** Most common; rule it out first
  rather than last.
- **An unhandled exception in a new code path.** Correlate the onset against the
  deploy timeline — the changelog and version endpoint exist for exactly this.
- **Something raising inside an exception handler.** See above; the symptom is a
  status code that does not match the error the client actually made.
- **Upstream input change.** A client sending a payload shape the boundary
  validation rejects should produce 4xx, not 5xx. 5xx here means validation is
  not doing its job.

## Prevention

- Pydantic v2 validation on every request, response, and task payload is a
  standing engineering standard, not a per-endpoint decision.
- The error contract is CI-tested: no internal disclosure, and `instance` must
  not echo user-controlled input — both were real defects caught by the Phase 0
  gate.
- Phase 4's contract tests make a breaking change to a published API version fail
  the provider build rather than a consumer's production.
