"""Metric cardinality guarantees.

Unbounded label cardinality is the classic way a Prometheus endpoint takes down
the thing it was meant to observe. Labelling by resolved path would give every
complaint UUID its own time series; at even modest volume the registry grows
without bound and `/metrics` becomes too slow to scrape.

This is exactly the kind of defect that never shows up in development — where
three requests look fine — and appears in production as a memory leak.
"""

from __future__ import annotations

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from nemesis.api.middleware import register_middleware
from nemesis.config import Settings
from nemesis.observability import metrics


def _recorded_endpoints() -> set[str]:
    return {
        sample.labels["endpoint"]
        for family in metrics.REGISTRY.collect()
        if family.name == "nemesis_http_requests"
        for sample in family.samples
    }


async def _app_client() -> AsyncClient:
    app = FastAPI()
    register_middleware(app, Settings())

    @app.get("/api/v1/complaints/{complaint_id}")
    async def get_complaint(complaint_id: str) -> dict[str, str]:
        return {"id": complaint_id}

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


class TestLabelCardinality:
    async def test_path_parameters_collapse_to_one_series(self) -> None:
        client = await _app_client()
        async with client:
            for i in range(25):
                await client.get(f"/api/v1/complaints/complaint-uuid-{i}")

        endpoints = _recorded_endpoints()
        # 25 distinct identifiers, one label value.
        assert "/api/v1/complaints/{complaint_id}" in endpoints
        assert not any("complaint-uuid-" in e for e in endpoints)

    async def test_unmatched_routes_share_a_single_bucket(self) -> None:
        # A 404 scan across thousands of random URLs must not be able to inflate
        # the registry — that would make the metrics endpoint a DoS vector.
        client = await _app_client()
        async with client:
            for i in range(20):
                await client.get(f"/nonexistent/path/{i}")

        endpoints = _recorded_endpoints()
        assert not any(e.startswith("/nonexistent") for e in endpoints)
        assert "unmatched" in endpoints
