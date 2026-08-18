"""§26.4 behaviour: opt-in, suppression, isolation, and the honest 404.

The privacy *properties* are proven in ``test_public_privacy.py``. This file is
about the endpoints behaving correctly for the people who use them — which
includes behaving correctly when there is almost no data, because that is the
state a real ward spends most of its time in and the state every mocked
integration gets wrong.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncEngine

from nemesis.public.policy import clamp_suppression_threshold
from tests.conftest import postgres_required

pytestmark = [postgres_required, pytest.mark.asyncio]


async def test_a_tenant_that_has_not_opted_in_is_not_published(
    api_client: AsyncClient, migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Default false, and the default is the decision.

    "The code is capable of publishing this" and "this customer agreed to
    publish it" are different statements, and a permissive default would make
    the first silently mean the second for every tenant already provisioned.
    """
    await _seed(migrated_engine, tenant_id, complaints=20, publish=False)
    response = await api_client.get("/api/v1/public/pilot-city/ward/W-01/summary")
    assert response.status_code == 404


async def test_opting_in_publishes(
    api_client: AsyncClient, migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    await _seed(migrated_engine, tenant_id, complaints=20)
    response = await api_client.get("/api/v1/public/pilot-city/ward/W-01/summary")
    assert response.status_code == 200
    assert response.json()["total_reports"] == 20


async def test_an_unknown_tenant_is_not_found_not_forbidden(api_client: AsyncClient) -> None:
    """Three internal states, one external answer.

    Distinguishing "no such tenant" from "not publishing" would let anyone
    compile the deployment's customer list *and* learn which customers declined
    to publish — a statement about a public body this system has no business
    making on its behalf.
    """
    response = await api_client.get("/api/v1/public/no-such-city/ward/W-01/summary")
    assert response.status_code == 404
    assert "forbidden" not in response.text.lower()


async def test_no_auth_header_is_required(
    api_client: AsyncClient, migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """§16.3: read-only, no auth required. Asserted rather than assumed."""
    await _seed(migrated_engine, tenant_id, complaints=20)
    response = await api_client.get(
        "/api/v1/public/pilot-city/zones", headers={"X-Tenant-ID": "", "X-API-Key": ""}
    )
    assert response.status_code == 200


async def test_a_thin_ward_is_suppressed_and_says_so(
    api_client: AsyncClient, migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Below the floor: the shape, the fact of suppression, and no measures.

    Returning zeros instead would be a lie a consumer cannot distinguish from a
    genuinely quiet ward.
    """
    await _seed(migrated_engine, tenant_id, complaints=3)
    body = (await api_client.get("/api/v1/public/pilot-city/ward/W-01/summary")).json()

    assert body["suppressed"] is True
    assert body["suppression_threshold"] == 5
    assert body["total_reports"] == 0
    assert body["by_category"] == []
    # The zone itself is still named — withholding *that* would make the whole
    # ward invisible rather than its figures unpublished.
    assert body["zone_code"] == "W-01"
    assert body["zone_name"] == "Ward 1"


async def test_a_thin_category_bucket_is_withheld_and_counted(
    api_client: AsyncClient, migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A gap must be countable.

    A ward with forty reports across nine categories, six suppressed, is a
    different picture from one with forty across three — and a consumer that
    cannot see the difference will read the second from the first.
    """
    await _seed(migrated_engine, tenant_id, complaints=0)
    async with migrated_engine.begin() as conn:
        for category, count in (("pothole_or_road_damage", 8), ("rare_defect", 2)):
            for _ in range(count):
                await conn.execute(
                    _complaint_sql(),
                    {"tenant": tenant_id, "category": category, "status": "resolved"},
                )

    body = (await api_client.get("/api/v1/public/pilot-city/ward/W-01/summary")).json()

    assert body["suppressed"] is False
    assert body["total_reports"] == 10
    assert [item["category"] for item in body["by_category"]] == ["pothole_or_road_damage"]
    assert body["count_suppressed_buckets"] == 1


async def test_the_suppression_floor_clamps_up_never_down() -> None:
    """A tenant that configures 1 has turned an aggregate into a per-complaint feed.

    Clamped rather than rejected: failing the request would take a public
    transparency page offline over a configuration mistake, which serves nobody.
    Degrading toward *more* privacy is the direction that is safe to do silently.
    """
    assert clamp_suppression_threshold(1, 5) == 5
    assert clamp_suppression_threshold(5, 5) == 5
    assert clamp_suppression_threshold(50, 5) == 50
    # Async only to satisfy the module-level asyncio mark; the function under
    # test is pure. Splitting it into its own module for the sake of the mark
    # would put a suppression assertion away from the suppression tests.


async def test_a_tenant_cannot_see_another_tenants_zone(
    api_client: AsyncClient,
    migrated_engine: AsyncEngine,
    tenant_id: uuid.UUID,
    other_tenant_id: uuid.UUID,
) -> None:
    """Isolation on the one surface where a missing predicate leaks to everybody."""
    await _seed(migrated_engine, tenant_id, complaints=9)
    async with migrated_engine.begin() as conn:
        await conn.execute(
            sql_text("UPDATE tenants SET public_api_enabled = true WHERE id = :tenant").bindparams(
                tenant=other_tenant_id
            )
        )
        await conn.execute(
            sql_text(
                "INSERT INTO zones (tenant_id, code, name, kind, path, depth) "
                "VALUES (:tenant, 'CAMPUS-A', 'Block A', 'block', 'CAMPUS-A', 0)"
            ).bindparams(tenant=other_tenant_id)
        )

    # pilot-city's ward is invisible to campus, and vice versa — 404, never 403.
    assert (await api_client.get("/api/v1/public/campus/ward/W-01/summary")).status_code == 404
    assert (
        await api_client.get("/api/v1/public/pilot-city/ward/CAMPUS-A/summary")
    ).status_code == 404


async def test_zone_discovery_lists_places(
    api_client: AsyncClient, migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Without discovery the other endpoints need identifiers nobody can learn."""
    await _seed(migrated_engine, tenant_id, complaints=9)
    body = (await api_client.get("/api/v1/public/pilot-city/zones")).json()
    assert body["count"] == 1
    assert body["zones"][0]["zone_code"] == "W-01"


async def test_budget_is_published_without_suppression(
    api_client: AsyncClient, migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A budget line is public finance, not an observation about a citizen.

    Withholding it because only one scheme funded a ward would hide exactly the
    thing an RTI applicant is looking for.
    """
    await _seed(migrated_engine, tenant_id, complaints=0)
    async with migrated_engine.begin() as conn:
        await conn.execute(
            sql_text(
                "INSERT INTO budget_allocations (tenant_id, ward, funding_source, fiscal_year, "
                "allocated_amount, spent_amount) "
                "VALUES (:tenant, 'W-01', 'municipal_capital', '2026-27', 1000000.00, 250000.00)"
            ).bindparams(tenant=tenant_id)
        )

    body = (
        await api_client.get("/api/v1/public/pilot-city/budget/W-01?fiscal_year=2026-27")
    ).json()

    assert len(body["allocations"]) == 1
    line = body["allocations"][0]
    # Strings, not floats. The column is NUMERIC for the §17.2 rate-card
    # reasoning, and JSON floats would reintroduce the sub-rupee ghosts in the
    # one place a reader compares against a printed document.
    assert line["allocated_amount"] == "1000000.00"
    assert isinstance(line["allocated_amount"], str)
    assert line["utilisation_rate"] == 0.25
    assert Decimal(line["spent_amount"]) == Decimal("250000.00")


async def test_a_contractor_with_too_little_history_is_suppressed(
    api_client: AsyncClient, migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Two jobs and one dispute is not a 33% dispute rate about a named company.

    §16.4 ships the appeal path in the same phase as the accountability feature,
    and the honest first line of that defence is not publishing a figure that
    cannot mean anything.
    """
    await _seed(migrated_engine, tenant_id, complaints=0)
    contractor_id = uuid.uuid4()
    async with migrated_engine.begin() as conn:
        await conn.execute(
            sql_text(
                "INSERT INTO contractors (id, tenant_id, name, registration_id) "
                "VALUES (:id, :tenant, 'Small Works Ltd', 'REG-1')"
            ).bindparams(id=contractor_id, tenant=tenant_id)
        )
        cluster_id = uuid.uuid4()
        await conn.execute(
            sql_text(
                "INSERT INTO complaint_clusters (id, tenant_id, centroid, first_reported, "
                "last_reported) VALUES (:id, :tenant, "
                "ST_GeogFromText('SRID=4326;POINT(73.8 18.5)'), now(), now())"
            ).bindparams(id=cluster_id, tenant=tenant_id)
        )
        for _ in range(2):
            await conn.execute(
                sql_text(
                    "INSERT INTO work_orders (tenant_id, complaint_cluster_id, status, "
                    "assigned_to_type, assigned_to_id) "
                    "VALUES (:tenant, :cluster, 'closed', 'contractor', :contractor)"
                ).bindparams(tenant=tenant_id, cluster=cluster_id, contractor=contractor_id)
            )

    body = (
        await api_client.get(f"/api/v1/public/pilot-city/contractor/{contractor_id}/profile")
    ).json()

    assert body["suppressed"] is True
    assert body["work_orders_completed"] == 0
    assert body["on_time_rate"] is None
    # The §16.1 / §22.2 disclaimer is a required field, not a courtesy.
    assert "not a score" in body["rating_disclaimer"]
    assert "under human review" in body["notice"]


async def test_public_responses_are_cacheable_by_intermediaries(
    api_client: AsyncClient, migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """``public``, not ``private`` — a claim that is only safe because of the scrub.

    Asserted so that changing the scrub without revisiting this reads as a test
    failure rather than as a silent widening of who may store the body.
    """
    await _seed(migrated_engine, tenant_id, complaints=9)
    response = await api_client.get("/api/v1/public/pilot-city/ward/W-01/summary")
    assert response.headers["Cache-Control"].startswith("public, max-age=")


async def test_an_empty_ward_is_not_suppressed(
    api_client: AsyncClient, migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Zero is publishable; between one and the floor is not.

    "No reports here" is a real, useful public fact. "Two reports here" is one
    citizen's report with a category and a location attached.
    """
    await _seed(migrated_engine, tenant_id, complaints=0)
    body = (await api_client.get("/api/v1/public/pilot-city/ward/W-01/summary")).json()
    assert body["suppressed"] is False
    assert body["total_reports"] == 0
    assert body["resolution_rate"] is None


# ---------------------------------------------------------------------------


def _complaint_sql() -> object:
    return sql_text(
        "INSERT INTO complaints (tenant_id, status, category, ward, location, reported_at) "
        "VALUES (:tenant, :status, :category, 'W-01', "
        "ST_GeogFromText('SRID=4326;POINT(73.8567 18.5204)'), now())"
    )


async def _seed(
    engine: AsyncEngine, tenant_id: uuid.UUID, *, complaints: int, publish: bool = True
) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            sql_text(
                "UPDATE tenants SET public_api_enabled = :publish, "
                "public_api_min_aggregate = 5 WHERE id = :tenant"
            ).bindparams(tenant=tenant_id, publish=publish)
        )
        await conn.execute(
            sql_text(
                "INSERT INTO zones (tenant_id, code, name, kind, path, depth) "
                "VALUES (:tenant, 'W-01', 'Ward 1', 'ward', 'W-01', 0)"
            ).bindparams(tenant=tenant_id)
        )
        for index in range(complaints):
            await conn.execute(
                _complaint_sql(),
                {
                    "tenant": tenant_id,
                    "status": "resolved" if index % 3 else "in_progress",
                    "category": "pothole_or_road_damage",
                },
            )
