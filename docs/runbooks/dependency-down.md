# Dependency down / readiness failing

- **Severity:** critical
- **Owner:** SRE
- **Alerts:** `NemesisDependencyDown`

> This alert is driven by the same gauge `/ready` sets, so the alert and the
> readiness probe can never disagree about what "up" means. If this fires, the
> instance is already returning 503 and taking itself out of rotation — which is
> the system working correctly, not an additional failure.

## Symptoms

- `nemesis_dependency_up{dependency="..."} == 0` for two minutes.
- `/ready` returning 503 while `/health` returns 200. That split is intentional:
  liveness deliberately touches no dependency, so a slow database cannot get an
  otherwise healthy process killed and turn a degraded dependency into an outage.

## How to confirm

```bash
curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/health   # expect 200
curl -s localhost:8000/ready | python -m json.tool               # names the failing check
```

The response body names the specific check. Beyond `database`, the probe also
verifies the `postgis` and `vector` extensions — their absence is a
*provisioning* failure, not a runtime condition, and calls for a different fix
than a connectivity failure.

## Immediate mitigation

By failing check:

**`database: unavailable`**
```bash
docker compose ps postgres
docker compose logs --tail=100 postgres
docker compose exec postgres pg_isready -U nemesis -d nemesis
```
If connections are refused rather than the container being down, go to
[database-pool-exhausted.md](database-pool-exhausted.md).

**`postgis: missing` or `vector: missing`**

The extensions are created by `infra/postgres/init/01-extensions.sql`, which
Postgres runs **only on an empty data directory**. Seeing this on an existing
volume means the volume was created before the init script existed, or by a
different image. Create them by hand:

```bash
docker compose exec postgres psql -U nemesis -d nemesis \
  -c "CREATE EXTENSION IF NOT EXISTS postgis; CREATE EXTENSION IF NOT EXISTS vector;"
```

**`redis`** — see [redis-unavailable.md](redis-unavailable.md).

## Root cause investigation

- **The container is unhealthy but running.** Healthchecks catch this; `docker
  compose ps` shows the state. A container that is `running` is not a container
  that is working.
- **Postgres OOM.** Capped at 1536 MB with `shared_buffers=384MB`. An HNSW index
  build is `maintenance_work_mem`-bound and is the operation most likely to push
  it over — relevant from Phase 2 onward.
- **The volume was recreated.** `nem nuke` destroys data by design and requires
  typing `destroy` to confirm; an accidental `docker compose down -v` does the
  same thing with no confirmation at all.

## Prevention

- The 503-on-failure behaviour is itself a Phase 0 fix for a real defect: `/ready`
  previously returned 200 while reporting `degraded`, so a broken instance kept
  taking traffic. CI asserts the 503 on every commit.
- Phase 25 exercises every dependency failure as an automated fault-injection
  test rather than a documented belief.
