"""RFC 9457 Problem Details error handling.

Two properties matter here beyond tidiness:

1. **One error shape.** The generated TypeScript client (Phase 18) types errors
   from this schema; ad-hoc error bodies would make client error handling
   guesswork.
2. **No internal detail leaks.** An unhandled exception returns a correlation ID
   and nothing else. Stack traces, driver messages, and SQL fragments are log
   material, not response bodies — §25 treats error responses as an information
   disclosure surface, and pagination counts or "not found vs forbidden"
   distinctions are exactly how tenant isolation leaks.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from nemesis.observability.logging import get_correlation_id, get_logger

log = get_logger(__name__)

PROBLEM_CONTENT_TYPE = "application/problem+json"

# Spelled out rather than taken from `starlette.status`, which renamed these
# constants and deprecated the old names. Under `filterwarnings = ["error"]` a
# deprecation raises *inside* the handler, turning the error response into a
# 500 — a failure mode that only appears on the path that is already an error,
# which is the path least likely to be exercised before production.
#
# Phase 0 hit this with 422 and fixed that one constant. Phase 3 hit it again
# with 413 the first time an upload exceeded its cap, because the fix had been
# applied to the symptom rather than to the class. Every status code this API
# returns from an error path is now named here: the numbers are stable, and the
# library's names for them demonstrably are not.
HTTP_400_BAD_REQUEST = 400
HTTP_401_UNAUTHORIZED = 401
HTTP_403_FORBIDDEN = 403
HTTP_404_NOT_FOUND = 404
HTTP_409_CONFLICT = 409
HTTP_413_CONTENT_TOO_LARGE = 413
HTTP_415_UNSUPPORTED_MEDIA_TYPE = 415
HTTP_422_UNPROCESSABLE = 422
HTTP_429_TOO_MANY_REQUESTS = 429
# Phase 4. A sunset API version answers 410, not 404: "gone" tells a consumer
# their integration targeted something real that has been withdrawn on the
# published schedule, which is a different instruction from "you have the URL
# wrong" and sends them to the changelog rather than to their own code.
HTTP_410_GONE = 410
HTTP_500_INTERNAL_SERVER_ERROR = 500

# Stable, documentable type URIs. Clients branch on these, never on prose.
PROBLEM_BASE = "https://nemesis.dev/problems"


class ProblemDetailError(Exception):
    """A domain error that already knows how it should surface to a client."""

    def __init__(
        self,
        *,
        status_code: int,
        title: str,
        detail: str | None = None,
        problem_type: str = "about:blank",
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.title = title
        self.detail = detail
        self.problem_type = problem_type
        self.extra = extra or {}
        super().__init__(detail or title)


def _instance_for(request: Request) -> str:
    """Route template, not the resolved path.

    RFC 9457's `instance` identifies the occurrence, and the obvious
    implementation is `request.url.path`. That reflects user-controlled input
    straight back into the response body — a path segment can carry an injection
    payload or the personal data §22 requires us not to echo. The correlation ID
    already identifies the specific occurrence, so `instance` carries the
    template and reflects nothing the caller supplied.
    """
    route = request.scope.get("route")
    path_format = getattr(route, "path_format", None)
    return path_format if isinstance(path_format, str) else "unmatched"


def _problem_response(
    status_code: int,
    title: str,
    detail: str | None = None,
    problem_type: str = "about:blank",
    instance: str | None = None,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": problem_type,
        "title": title,
        "status": status_code,
    }
    if detail is not None:
        body["detail"] = detail
    if instance is not None:
        body["instance"] = instance
    cid = get_correlation_id()
    if cid is not None:
        # The one piece of internal state that is safe to return, and the only
        # one a support engineer needs to find the matching trace.
        body["correlation_id"] = cid
    if extra:
        body.update(extra)

    return JSONResponse(status_code=status_code, content=body, media_type=PROBLEM_CONTENT_TYPE)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProblemDetailError)
    async def _handle_problem(request: Request, exc: ProblemDetailError) -> JSONResponse:
        return _problem_response(
            status_code=exc.status_code,
            title=exc.title,
            detail=exc.detail,
            problem_type=exc.problem_type,
            instance=_instance_for(request),
            extra=exc.extra,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _problem_response(
            status_code=exc.status_code,
            title=str(exc.detail),
            problem_type=f"{PROBLEM_BASE}/http-error",
            instance=_instance_for(request),
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Field-level errors are returned because the client needs them to fix
        # the request. Input values are not echoed back — a rejected payload may
        # contain exactly the personal data §22 requires us not to reflect.
        errors = [
            {"field": ".".join(str(p) for p in e["loc"]), "reason": e["msg"]} for e in exc.errors()
        ]
        return _problem_response(
            status_code=HTTP_422_UNPROCESSABLE,
            title="Request validation failed",
            problem_type=f"{PROBLEM_BASE}/validation-error",
            instance=_instance_for(request),
            extra={"errors": errors},
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.exception(
            "unhandled_exception",
            path=str(request.url.path),
            method=request.method,
            error_type=type(exc).__name__,
        )
        return _problem_response(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            title="Internal server error",
            detail="An unexpected error occurred. Quote the correlation ID when reporting this.",
            problem_type=f"{PROBLEM_BASE}/internal-error",
            instance=_instance_for(request),
        )
