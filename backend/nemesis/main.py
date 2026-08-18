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
from nemesis.api.ratelimit import close_limiter
from nemesis.api.v1 import api_v1, portal_router, realtime_router
from nemesis.api.v2 import api_v2
from nemesis.api.versioning import VersionStatus, all_versions
from nemesis.config import Settings, get_settings
from nemesis.db.session import dispose_engine, get_engine
from nemesis.flags import close_flags, get_flags
from nemesis.flags.registry import REGISTRY
from nemesis.observability import metrics
from nemesis.observability.logging import configure_logging, get_logger
from nemesis.observability.tracing import configure_tracing
from nemesis.realtime.service import close_realtime

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
            # Realtime first: closing sockets needs the loop, the flag store,
            # and Redis to still be alive. Disposing the engine before telling
            # connected clients the server is going away would leave them
            # waiting for a close frame that never arrives.
            await close_realtime()
            await close_limiter()
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

    # Domain routers (Phase 3). Registered after the ops probes so a routing
    # mistake in a domain router can never shadow /health or /ready — the two
    # endpoints an orchestrator uses to decide whether this process lives.
    app.include_router(api_v1)
    # Phase 4. v2 is a real surface, not a placeholder — the gate's first clause
    # is that a v1 consumer keeps working after v2 ships, and that cannot be
    # proven against a version which does not exist. See `nemesis.api.v2`.
    app.include_router(api_v2)
    app.include_router(portal_router)
    app.include_router(realtime_router)

    _register_sunset_versions(app)

    return app


def _register_sunset_versions(app: FastAPI) -> None:
    """Answer 410 for any version past its published sunset date.

    **Registered as routes rather than checked inside each handler**, so a
    version cannot be removed from the registry and left serving because one
    router forgot the check. And computed from the *date* rather than from the
    status field, so a deployment nobody has updated still stops serving on
    schedule: the promise made to consumers was a date, not a promise that
    somebody would remember it.

    410 and not 404, for the reason ``api.errors`` records next to the constant:
    "gone" sends an integrator to the changelog, "not found" sends them to
    re-read their own URL construction.
    """
    from datetime import UTC, datetime

    from nemesis.api.errors import HTTP_410_GONE, PROBLEM_BASE, ProblemDetailError

    today = datetime.now(tz=UTC).date()
    expired = [v for v in all_versions() if v.is_expired(today) or v.status is VersionStatus.SUNSET]
    for version in expired:

        async def gone(
            version_name: str = version.name, successor: str | None = version.successor
        ) -> None:
            raise ProblemDetailError(
                status_code=HTTP_410_GONE,
                title=f"API {version_name} has been withdrawn",
                detail=(
                    f"API {version_name} reached its published sunset date. "
                    + (
                        f"Move to {successor}; see /developers#versions."
                        if successor
                        else "See /developers#versions."
                    )
                ),
                problem_type=f"{PROBLEM_BASE}/api-version-sunset",
            )

        app.add_api_route(
            f"/api/{version.name}/{{path:path}}",
            gone,
            methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            include_in_schema=False,
        )


app = create_app()
