# 0005 — Explicit container environment, never YAML merge inheritance

- **Status:** Accepted
- **Date:** 2026-08-16
- **Owner:** SRE
- **Blueprint:** §24.2, §27.3

## Context

Found during the Phase 0 exit gate, in a stack that was otherwise passing.

`docker-compose.yml` used a YAML anchor to share common service configuration:

```yaml
x-backend-base: &backend-base
  env_file: [.env]
  environment:
    NEMESIS_DATABASE_URL: postgresql+asyncpg://…@postgres:5432/nemesis
    NEMESIS_REDIS_URL: redis://redis:6379/0
```

`worker-ml` inherited it and added its own thread-limit variables:

```yaml
  worker-ml:
    <<: *backend-base
    environment:            # ← replaces the anchor's map wholesale
      OMP_NUM_THREADS: "4"
```

**YAML merge keys do not deep-merge mappings.** A service-level `environment`
block replaces the anchor's entirely. `worker-ml` therefore lost both service
URLs and fell back to the root `.env`, which correctly carries *host-oriented*
values (`localhost`) for host-side tooling like pytest and Alembic.

The container started successfully, reported no configuration error, and simply
could not reach the broker. `api` and `worker-io` were unaffected only because
they happened not to override `environment`.

This is the failure class worth an ADR: **silent, environment-shaped, and
invisible to every check except an end-to-end one.** A unit test would not catch
it. A config-file review would not catch it. Only starting the service and
watching it fail to connect catches it.

## Decision

1. Container-internal service addresses live in a **dedicated anchor merged
   *inside* each service's `environment` map**, not inherited through the
   service-level anchor:

   ```yaml
   x-backend-env: &backend-env
     NEMESIS_DATABASE_URL: …
     NEMESIS_REDIS_URL: …

   services:
     worker-ml:
       environment:
         <<: *backend-env
         OMP_NUM_THREADS: "4"
   ```

2. Every backend service declares `environment` explicitly, even when it adds
   nothing, so the merge is uniform and a future addition cannot silently drop
   it.

3. `.env` is understood as **host tooling configuration**, never as the source
   of truth for container networking. The reasoning is recorded in the compose
   file itself, at the point of use.

## Alternatives considered

**Remove `env_file` and put everything in `environment`.** Rejected: secrets
would then live in a committed file, and host tooling would lose its config.

**Rely on precedence rules.** Rejected: `environment` does override `env_file` —
that rule was never the problem. The bug was the anchor's map being discarded
before precedence applied. Relying on a subtlety that already fooled us once is
not a control.

**A validation script comparing service environments.** Rejected as the primary
fix — it detects the symptom rather than removing the cause. The readiness probe
(§ADR-0005 consequence below) covers detection more cheaply.

## Consequences

- Compose is slightly more verbose, and correct by construction.
- The `/ready` probe now returns **503** rather than 200-with-`degraded`, so a
  misconfigured service fails its healthcheck instead of quietly accepting
  traffic. This class of bug now surfaces at startup.
- Any future service added to the stack must opt into `x-backend-env`
  explicitly, which is the intended friction.

## Revisit when

The stack moves to Kubernetes, where ConfigMaps and Secrets replace compose
anchors entirely and this specific hazard disappears.
