"""HTTP middleware: correlation, metrics, access logging, security headers."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Final

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from nemesis.config import Settings
from nemesis.observability import metrics
from nemesis.observability.logging import get_logger, set_correlation_id
from nemesis.observability.tracing import current_trace_id

CORRELATION_HEADER: Final = "X-Correlation-ID"

log = get_logger(__name__)

# Probe and metrics endpoints are excluded from access LOGS. At a 15s healthcheck
# interval they would otherwise dominate the log volume and bury real traffic.
_QUIET_LOG_PATHS: Final = frozenset({"/health", "/ready", "/metrics"})

# Metrics exclusion is a *different, narrower* set, and the difference is
# load-bearing. These paths shared one set until the Phase 1a alert rules were
# written against them, at which point `NemesisReadinessFailing` — which counts
# 503s on /ready — turned out to be querying a series the middleware refused to
# create. An alert that can never fire is worse than no alert: it reads as
# coverage.
#
# The reasoning that justified the log exclusion does not transfer. Logs are
# per-event and probe traffic genuinely drowns them; metrics are aggregates, and
# four samples a minute cost nothing. A 503 on /ready is exactly the signal
# worth counting — it is the instance taking itself out of rotation.
#
# /metrics stays out: scraping the scrape endpoint is self-referential noise
# that inflates every request-rate panel with the act of observing it.
_UNMEASURED_PATHS: Final = frozenset({"/metrics"})

_SECURITY_HEADERS: Final = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    # This API serves JSON only; it never needs to load a script, a frame, or an
    # image. The tightest possible policy is also the correct one here.
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}


def _route_template(request: Request) -> str:
    """Route template, never the resolved path.

    Using `request.url.path` here would make every complaint UUID its own
    Prometheus time series and eventually take the metrics endpoint down.
    """
    route = request.scope.get("route")
    path_format = getattr(route, "path_format", None)
    if isinstance(path_format, str):
        return path_format
    return "unmatched"


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """Correlation ID, request metrics, and structured access logging."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        quiet_log = path in _QUIET_LOG_PATHS
        measured = path not in _UNMEASURED_PATHS
        start = time.perf_counter()

        if measured:
            metrics.http_requests_in_flight.inc()

        # Defaults to 500 so an exception propagating out of the handler is
        # recorded as a server error rather than vanishing from the metrics.
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            elapsed = time.perf_counter() - start
            endpoint = _route_template(request)

            if measured:
                metrics.http_requests_in_flight.dec()
                metrics.http_requests_total.labels(
                    method=request.method, endpoint=endpoint, status=str(status_code)
                ).inc()
                metrics.http_request_duration_seconds.labels(
                    method=request.method, endpoint=endpoint
                ).observe(elapsed)

            if not quiet_log:
                log.info(
                    "http_request",
                    method=request.method,
                    endpoint=endpoint,
                    status=status_code,
                    duration_ms=round(elapsed * 1000, 2),
                    trace_id=current_trace_id(),
                )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for header, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response


class CorrelationHeaderMiddleware(BaseHTTPMiddleware):
    """Echo the correlation ID on every response, including error responses.

    Kept separate from ObservabilityMiddleware so it wraps the exception
    handlers: a 500 is precisely when the caller most needs the ID to report.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        cid = set_correlation_id(request.headers.get(CORRELATION_HEADER))
        response = await call_next(request)
        response.headers[CORRELATION_HEADER] = cid
        return response


def register_middleware(app: FastAPI, settings: Settings) -> None:
    """Order matters. Starlette applies middleware bottom-up, so the last one
    added is the outermost. Correlation must be outermost to cover error
    responses produced by the exception handlers."""
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_allow_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", CORRELATION_HEADER],
        expose_headers=[CORRELATION_HEADER],
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(ObservabilityMiddleware)
    app.add_middleware(CorrelationHeaderMiddleware)
