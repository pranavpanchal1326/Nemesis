"""FastAPI application factory.

Phase 0 ships the application skeleton, operational probes, the observability
stack, and the error contract. Domain routers land in Phase 3.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, Response, status
from sqlalchemy import text

from nemesis.api.errors import register_exception_handlers
from nemesis.api.middleware import register_middleware
from nemesis.config import Settings, get_settings
from nemesis.db.session import dispose_engine, get_engine
from nemesis.flags import close_flags, get_flags
from nemesis.flags.registry import REGISTRY
from nemesis.observability import metrics
from nemesis.observability.logging import configure_logging, get_logger
from nemesis.observability.tracing import configure_tracing

log = get_logger(__name__)

# Extensions the dedup engine (§14) is built directly on. Their absence is a
# provisioning failure, not a runtime condition to tolerate.
REQUIRED_EXTENSIONS = ("postgis", "vector")


async def _check_database() -> dict[str, str]:
    """Probe the datastore and the extensions the pipeline depends on.

    Uses a bare connection rather than the ORM session scope: a readiness probe
    must not open a transaction it might have to roll back, and must not be
    affected by session state.
    """
    checks: dict[str, str] = {}
    try:
        async with get_engine().connect() as conn:
            rows = await conn.execute(
                text("SELECT extname FROM pg_extension WHERE extname = ANY(:names)"),
                {"names": list(REQUIRED_EXTENSIONS)},
            )
            found = {row[0] for row in rows}
        checks["database"] = "ok"
        for ext in REQUIRED_EXTENSIONS:
            checks[ext] = "ok" if ext in found else "missing"
    except Exception as exc:
        log.warning("readiness_check_failed", error_type=type(exc).__name__)
        checks["database"] = "unavailable"
        for ext in REQUIRED_EXTENSIONS:
            checks[ext] = "unknown"
    return checks


def _lifespan_factory(settings: Settings) -> Any:
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        log.info(
            "startup",
            app_env=settings.app_env,
            service=settings.service_name,
            version=settings.service_version,
        )
        try:
            yield
        finally:
            await dispose_engine()
            await close_flags()
            log.info("shutdown")

    return lifespan


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or get_settings()
    configure_logging(level=cfg.log_level, service_name=cfg.service_name)

    app = FastAPI(
        title="NEMESIS",
        version=cfg.service_version,
        description=(
            "Networked Enforcement & Municipal Evidence System for "
            "Infrastructure & Service accountability"
        ),
        lifespan=_lifespan_factory(cfg),
    )
    app.state.settings = cfg

    register_middleware(app, cfg)
    register_exception_handlers(app)
    configure_tracing(cfg, app)

    @app.get("/health", tags=["ops"], summary="Liveness probe")
    async def health() -> dict[str, str]:
        """Liveness only — deliberately touches no dependency.

        A slow database must not cause the orchestrator to kill an otherwise
        healthy process; that turns a degraded dependency into an outage.
        """
        return {"status": "ok"}

    @app.get("/ready", tags=["ops"], summary="Readiness probe")
    async def ready(response: Response) -> dict[str, Any]:
        """Readiness — verifies dependencies and **fails the HTTP status** when
        they are not satisfied.

        Returning 200 alongside a `"degraded"` body would be silently useless:
        every orchestrator and load balancer routes on the status code, so a
        broken instance would keep receiving traffic.
        """
        checks = await _check_database()
        healthy = all(v == "ok" for v in checks.values())

        for dependency, state in checks.items():
            metrics.dependency_up.labels(dependency=dependency).set(1.0 if state == "ok" else 0.0)

        if not healthy:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

        return {"status": "ok" if healthy else "degraded", "checks": checks}

    @app.get("/ops/flags", tags=["ops"], summary="Declared feature flags and resolved state")
    async def ops_flags() -> dict[str, Any]:
        """Read-only. Mutation is CLI-only until Phase 13 ships authorization.

        Exposing the *listing* without auth is a deliberate, bounded decision: a
        flag's name and on/off state are not secrets, and being able to see
        which kill switches are pulled without shelling into a container is
        worth having during an incident. Exposing a *write* path without auth
        would mean an unauthenticated request could disable the safety
        fail-safe. See ADR-0009.
        """
        flags = get_flags()
        decisions = await flags.snapshot()
        return {
            "flags": [
                {
                    "name": name,
                    "enabled": decision.value,
                    "source": decision.source,
                    "kill_switch": REGISTRY[name].kill_switch,
                    "owner": REGISTRY[name].owner,
                    "remove_by": REGISTRY[name].remove_by.isoformat(),
                    "description": REGISTRY[name].description,
                }
                for name, decision in sorted(decisions.items())
            ],
            "reload_interval_seconds": cfg.flags.reload_interval_seconds,
        }

    @app.get("/metrics", tags=["ops"], include_in_schema=False)
    async def prometheus_metrics() -> Response:
        payload, content_type = metrics.render()
        return Response(content=payload, media_type=content_type)

    return app


app = create_app()
