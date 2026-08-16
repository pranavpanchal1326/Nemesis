# Readiness probe returning 503

- **Severity:** warning
- **Owner:** SRE
- **Alerts:** `NemesisReadinessFailing`

> This alert is **inhibited by `NemesisDependencyDown`**, because a failed
> dependency is *why* readiness is 503 and saying both adds nothing. If you are
> reading this page and `NemesisDependencyDown` is not firing, that combination
> is itself the interesting fact: readiness is failing without a dependency
> being reported down.

## Symptoms

- `/ready` returning 503 while every dependency gauge reads 1.
- The 503 rate is intermittent rather than sustained — a sustained failure would
  have brought the dependency gauge down with it.

## How to confirm

```bash
for i in $(seq 1 10); do
  curl -s -o /dev/null -w '%{http_code} ' localhost:8000/ready
done; echo
curl -s localhost:8000/ready | python -m json.tool
```

A mix of 200 and 503 across ten calls points at a probe that is *timing out*
rather than failing — the probe opens a bare connection and queries
`pg_extension`, so it is fast unless the pool is contended.

## Immediate mitigation

1. **Intermittent 503 with healthy dependencies** almost always means pool
   contention: the probe is competing for a connection with real traffic. Check
   pool saturation before anything else — see
   [database-pool-exhausted.md](database-pool-exhausted.md).
2. **If an orchestrator is cycling the instance**, note that liveness and
   readiness are deliberately split. `/health` touches nothing and should stay
   200 throughout; if `/health` is also failing, this is not a readiness problem
   and the process itself is in trouble.
3. Do not "fix" this by making `/ready` return 200 on a degraded check. That was
   a real defect, caught by the Phase 0 gate, and CI now asserts the 503.

## Root cause investigation

- **Pool contention against the probe.** The most common cause, and the reason
  the probe uses a bare connection rather than the ORM session scope.
- **A probe interval shorter than the probe's own latency**, so probes queue
  behind each other and each one makes the next slower.
- **An extension genuinely missing** on a recreated volume — the body says so
  explicitly; see [dependency-down.md](dependency-down.md).

## Prevention

- The readiness contract is CI-enforced: the stack job stops Postgres and
  asserts a 503 within seconds, then asserts recovery.
- Phase 28's load scenarios assert the probe stays responsive under load, which
  is the condition where this failure actually appears.
