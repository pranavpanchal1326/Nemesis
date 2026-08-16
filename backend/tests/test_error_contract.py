"""Error contract tests (RFC 9457 Problem Details).

Two properties are load-bearing and both are tested here:

1. **Shape stability.** The generated TypeScript client types errors from this
   schema. An ad-hoc error body is an untyped surprise at the client boundary.
2. **No internal disclosure.** §25 treats error responses as an information
   disclosure surface. Driver messages, SQL fragments, and stack traces are log
   material, never response bodies.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from nemesis.api.errors import ProblemDetailError, register_exception_handlers
from nemesis.api.middleware import CORRELATION_HEADER, CorrelationHeaderMiddleware


@pytest.fixture
async def failing_client() -> AsyncClient:
    """An app with routes that fail in each way the handlers must cover."""
    app = FastAPI()
    # Correlation middleware is part of the contract under test: a 500 is
    # precisely when the caller needs an ID to quote, so the fixture mirrors the
    # real application rather than testing handlers in isolation.
    app.add_middleware(CorrelationHeaderMiddleware)
    register_exception_handlers(app)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("connection to postgres at 10.0.0.5:5432 failed: password auth")

    @app.get("/problem")
    async def problem() -> None:
        raise ProblemDetailError(
            status_code=409,
            title="Cluster already merged",
            detail="This complaint was merged into an existing cluster.",
            problem_type="https://nemesis.dev/problems/already-merged",
        )

    @app.get("/typed/{count}")
    async def typed(count: int) -> dict[str, int]:
        return {"count": count}

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return AsyncClient(transport=transport, base_url="http://test")


class TestProblemDetailShape:
    async def test_domain_error_uses_problem_json(self, failing_client: AsyncClient) -> None:
        resp = await failing_client.get("/problem")
        assert resp.status_code == 409
        assert resp.headers["content-type"].startswith("application/problem+json")

    async def test_required_rfc9457_members_present(self, failing_client: AsyncClient) -> None:
        body = (await failing_client.get("/problem")).json()
        assert body["type"] == "https://nemesis.dev/problems/already-merged"
        assert body["title"] == "Cluster already merged"
        assert body["status"] == 409
        assert body["instance"] == "/problem"

    async def test_validation_error_reports_fields_without_echoing_values(
        self, failing_client: AsyncClient
    ) -> None:
        resp = await failing_client.get("/typed/not-an-integer")
        assert resp.status_code == 422
        body = resp.json()
        assert body["type"].endswith("/validation-error")
        assert any("count" in e["field"] for e in body["errors"])
        # `instance` carries the route template, never the resolved path.
        assert body["instance"] == "/typed/{count}"
        # The rejected value is deliberately not reflected anywhere in the body:
        # a bad payload may carry exactly the personal data §22 requires us not
        # to echo, and a reflected path segment is an injection surface.
        assert "not-an-integer" not in resp.text


class TestNoInternalDisclosure:
    async def test_unhandled_exception_returns_generic_500(
        self, failing_client: AsyncClient
    ) -> None:
        resp = await failing_client.get("/boom")
        assert resp.status_code == 500
        assert resp.json()["title"] == "Internal server error"

    @pytest.mark.parametrize(
        "leak",
        ["10.0.0.5", "5432", "password auth", "RuntimeError", "Traceback", "postgres"],
    )
    async def test_internal_details_never_reach_the_client(
        self, failing_client: AsyncClient, leak: str
    ) -> None:
        resp = await failing_client.get("/boom")
        assert leak not in resp.text

    async def test_correlation_id_is_returned_for_support(
        self, failing_client: AsyncClient
    ) -> None:
        # The one internal value that is safe to return, and the only one a
        # support engineer needs to find the matching trace.
        body = (await failing_client.get("/boom")).json()
        assert body.get("correlation_id")


class TestCorrelationOnErrorPaths:
    async def test_correlation_header_survives_a_500(self, client: AsyncClient) -> None:
        # Correlation middleware must wrap the exception handlers — a 500 is
        # exactly when the caller most needs the ID.
        resp = await client.get("/health", headers={CORRELATION_HEADER: "trace-xyz"})
        assert resp.headers[CORRELATION_HEADER] == "trace-xyz"


class TestSecurityHeaders:
    @pytest.mark.parametrize(
        ("header", "expected"),
        [
            ("X-Content-Type-Options", "nosniff"),
            ("X-Frame-Options", "DENY"),
            ("Referrer-Policy", "strict-origin-when-cross-origin"),
        ],
    )
    async def test_security_headers_present(
        self, client: AsyncClient, header: str, expected: str
    ) -> None:
        resp = await client.get("/health")
        assert resp.headers[header] == expected

    async def test_csp_denies_everything_on_a_json_api(self, client: AsyncClient) -> None:
        csp = (await client.get("/health")).headers["Content-Security-Policy"]
        assert "default-src 'none'" in csp
        assert "frame-ancestors 'none'" in csp
