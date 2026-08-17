"""Operational probe contract tests.

These exist because of a real Phase 0 defect: `/ready` returned HTTP 200 with a
`"degraded"` body. Every orchestrator, load balancer, and compose healthcheck
routes on the **status code**, so a broken instance would have kept receiving
traffic while truthfully reporting that it could not serve it.

The lesson generalises: a probe's contract is its status code, and a probe that
cannot fail is not a probe.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from nemesis.config import Settings


class TestLiveness:
    async def test_health_returns_ok(self, client: AsyncClient) -> None:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    async def test_health_does_not_touch_the_database(self, client: AsyncClient) -> None:
        # Liveness must stay dependency-free: a slow database should never get a
        # healthy process killed, which converts a degraded dependency into a
        # full outage.
        with patch("nemesis.main._check_database") as probe:
            resp = await client.get("/health")
        assert resp.status_code == 200
        probe.assert_not_called()


class TestReadiness:
    async def test_ready_returns_200_when_all_dependencies_ok(self, client: AsyncClient) -> None:
        with patch(
            "nemesis.main._check_database",
            return_value={"database": "ok", "postgis": "ok", "vector": "ok"},
        ):
            resp = await client.get("/ready")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    @pytest.mark.parametrize(
        "checks",
        [
            {"database": "unavailable", "postgis": "unknown", "vector": "unknown"},
            {"database": "ok", "postgis": "missing", "vector": "ok"},
            {"database": "ok", "postgis": "ok", "vector": "missing"},
        ],
        ids=["database-down", "postgis-missing", "pgvector-missing"],
    )
    async def test_ready_returns_503_when_any_dependency_fails(
        self, client: AsyncClient, checks: dict[str, str]
    ) -> None:
        # The regression this file exists for. A degraded body with a 200 status
        # is invisible to every consumer that matters.
        with patch("nemesis.main._check_database", return_value=checks):
            resp = await client.get("/ready")
        assert resp.status_code == 503
        assert resp.json()["status"] == "degraded"

    async def test_ready_reports_which_dependency_failed(self, client: AsyncClient) -> None:
        with patch(
            "nemesis.main._check_database",
            return_value={"database": "ok", "postgis": "ok", "vector": "missing"},
        ):
            body: dict[str, Any] = (await client.get("/ready")).json()
        assert body["checks"]["vector"] == "missing"

    async def test_ready_never_raises_on_datastore_failure(self, client: AsyncClient) -> None:
        # A probe that 500s tells the orchestrator nothing useful about *why*.
        with patch(
            "nemesis.db.session.get_engine",
            side_effect=RuntimeError("connection pool exhausted"),
        ):
            resp = await client.get("/ready")
        assert resp.status_code in (200, 503)


class TestMetricsEndpoint:
    async def test_metrics_exposes_prometheus_format(self, client: AsyncClient) -> None:
        resp = await client.get("/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers["content-type"]

    async def test_domain_metrics_are_registered(self, client: AsyncClient) -> None:
        # §41 KPIs are domain metrics; generic HTTP instrumentation cannot
        # produce them, so their presence is asserted explicitly.
        body = (await client.get("/metrics")).text
        for metric in (
            "nemesis_http_requests_total",
            "nemesis_pipeline_stage_duration_seconds",
            "nemesis_system_degradation_total",
            "nemesis_dependency_up",
        ):
            assert metric in body

    async def test_metrics_endpoint_is_not_in_public_schema(self, client: AsyncClient) -> None:
        schema = (await client.get("/openapi.json")).json()
        assert "/metrics" not in schema["paths"]


def _samples_for(payload: str, endpoint: str) -> list[str]:
    return [ln for ln in payload.splitlines() if f'endpoint="{endpoint}"' in ln]


class TestProbeCardinality:
    """Probe traffic: counted in metrics, silent in logs.

    CHANGED IN PHASE 1a, deliberately. Phase 0 excluded /health, /ready, and
    /metrics from *both* the access log and the metrics, on one justification —
    that healthchecks every 15s would swamp real traffic.

    That reasoning holds for logs, which are per-event, and does not transfer to
    metrics, which are aggregates. The exclusion was caught when the Phase 1a
    alert rules were written: `NemesisReadinessFailing` counts 503s on /ready,
    and the middleware refused to create that series, so the alert could never
    fire. An alert that cannot fire is worse than no alert, because it reads as
    coverage.

    The cardinality concern is genuine but is not about these paths: it is about
    *resolved* paths becoming labels, which `_route_template` prevents and
    `test_metrics_cardinality.py` asserts. /health and /ready are route
    templates and contribute a bounded handful of series between them.
    """

    async def test_readiness_probes_are_counted_so_alerts_can_fire(
        self, client: AsyncClient
    ) -> None:
        for _ in range(3):
            await client.get("/ready")
        body = (await client.get("/metrics")).text

        samples = _samples_for(body, "/ready")
        assert samples, (
            "/ready produced no metric samples, so NemesisReadinessFailing can "
            "never fire — the exact defect this test was rewritten to prevent"
        )
        assert any("nemesis_http_requests_total" in line for line in samples)

    async def test_liveness_probes_are_counted(self, client: AsyncClient) -> None:
        for _ in range(5):
            await client.get("/health")
        body = (await client.get("/metrics")).text
        assert _samples_for(body, "/health")

    async def test_the_metrics_endpoint_does_not_count_itself(self, client: AsyncClient) -> None:
        """Scraping the scrape endpoint is self-referential noise: it inflates
        every request-rate panel with the act of observing it."""
        for _ in range(3):
            await client.get("/metrics")
        body = (await client.get("/metrics")).text
        assert _samples_for(body, "/metrics") == []

    async def test_probe_paths_stay_out_of_the_access_log(self) -> None:
        """The half of the Phase 0 decision that was correct and is retained."""
        from nemesis.api.middleware import _QUIET_LOG_PATHS, _UNMEASURED_PATHS

        assert {"/health", "/ready", "/metrics"} <= _QUIET_LOG_PATHS
        assert set(_UNMEASURED_PATHS) == {"/metrics"}


class TestProductionSafetyGuards:
    def test_pilot_env_rejects_development_secret(self) -> None:
        # The two mistakes that are trivial to make and expensive to discover.
        with pytest.raises(ValueError, match="development JWT secret"):
            Settings(app_env="pilot")

    def test_pilot_env_rejects_wildcard_cors(self) -> None:
        with pytest.raises(ValueError, match="wildcard CORS"):
            Settings(
                app_env="pilot",
                jwt_secret="a-real-generated-secret",  # type: ignore[arg-type]
                control_plane_token="a-real-generated-token",  # type: ignore[arg-type]
                cors_allow_origins=("*",),
            )

    def test_local_env_permits_development_defaults(self) -> None:
        assert Settings(app_env="local").app_env == "local"
