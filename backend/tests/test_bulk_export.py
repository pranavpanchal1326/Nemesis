"""Bulk export for RTI applicants and researchers, and the sandbox tenant.

The export is the most attractive way to exfiltrate this dataset, so the tests
that matter are about what is *not* in a row: no complaint id, no assignee, and
a date rather than a timestamp. The last one is the least obvious and the most
important — a second-resolution timestamp beside a coarse location is a
re-identifier even though neither field is one alone.
"""

from __future__ import annotations

import csv
import io
import json
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncEngine

from nemesis.public import export
from nemesis.public.policy import find_disclosures
from tests.conftest import postgres_required

pytestmark = [postgres_required, pytest.mark.asyncio]

TOKEN = {"X-Control-Plane-Token": "dev-only-insecure-control-plane-token-change-me"}


async def test_csv_and_ndjson_are_available() -> None:
    assert export.resolve_format("csv").media_type.startswith("text/csv")
    assert export.resolve_format("NDJSON").name == "ndjson"


async def test_parquet_is_refused_with_its_reason() -> None:
    """Not offered, and the refusal says why rather than reading as a gap.

    ADR-0024: pyarrow is a 40 MB dependency in the image that serves citizen
    submissions, for a columnar analytics format whose value is a query engine.
    Phase 23 owns the warehouse and is where it belongs.
    """
    with pytest.raises(export.UnknownFormatError, match="ADR-0024"):
        export.resolve_format("parquet")


async def test_a_csv_export_carries_no_identifiers(
    api_client: AsyncClient, migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """No complaint id, and that is the decision.

    A stable handle would let two extracts taken a month apart be joined into a
    per-reporter history — the reconstruction the aggregates exist to prevent.
    """
    secret = await _key(api_client, tenant_id, scopes=["export:read"])
    await _seed(migrated_engine, tenant_id, complaints=6)

    response = await api_client.get(
        "/api/v1/export/complaints?format=csv", headers={"X-API-Key": secret}
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=" in response.headers["content-disposition"]

    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert len(rows) == 6
    assert set(rows[0]) == set(export.COMPLAINT_COLUMNS)
    assert "complaint_id" not in rows[0]
    assert "description_text" not in rows[0]
    assert "submitter_device_fingerprint" not in response.text
    assert "the citizen's own words" not in response.text


async def test_an_export_row_carries_a_date_not_a_timestamp(
    api_client: AsyncClient, migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The subtlest re-identifier, closed.

    Two people do not photograph the same street corner in the same second, so a
    second-resolution timestamp narrows a coarse location to one person even
    though neither field does alone.
    """
    secret = await _key(api_client, tenant_id, scopes=["export:read"])
    await _seed(migrated_engine, tenant_id, complaints=3)

    response = await api_client.get(
        "/api/v1/export/complaints?format=ndjson", headers={"X-API-Key": secret}
    )
    rows = [json.loads(line) for line in response.text.strip().splitlines()]
    assert len(rows) == 3
    for row in rows:
        assert len(row["reported_date"]) == 10  # YYYY-MM-DD
        assert "T" not in row["reported_date"]
        assert ":" not in row["reported_date"]


async def test_exported_coordinates_are_coarsened(
    api_client: AsyncClient, migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    secret = await _key(api_client, tenant_id, scopes=["export:read"])
    await _seed(migrated_engine, tenant_id, complaints=3)

    response = await api_client.get(
        "/api/v1/export/complaints?format=ndjson", headers={"X-API-Key": secret}
    )
    for line in response.text.strip().splitlines():
        row = json.loads(line)
        assert row["lat"] == round(row["lat"], 3)
        assert row["lng"] == round(row["lng"], 3)
        assert find_disclosures(row) == []
    assert "18.520431" not in response.text


async def test_a_work_order_export_names_no_contractor(
    api_client: AsyncClient, migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """§16.1 publishes a contractor's aggregate record through its own endpoint.

    Where it arrives with the §22.2 disclaimer and the §16.4 appeal path
    attached. A per-job extract naming the contractor is the same accusation
    without either, in a format designed for automated republication.
    """
    secret = await _key(api_client, tenant_id, scopes=["export:read"])
    await _seed(migrated_engine, tenant_id, complaints=3, with_work_orders=True)

    response = await api_client.get(
        "/api/v1/export/work-orders?format=csv", headers={"X-API-Key": secret}
    )
    assert response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert set(rows[0]) == set(export.WORK_ORDER_COLUMNS)
    assert "assigned_to_id" not in rows[0]
    assert "contractor" not in response.text.lower()


async def test_the_row_cap_is_announced(
    api_client: AsyncClient, migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A truncated extract always says it was truncated.

    Otherwise it is a dataset a researcher will publish conclusions from.
    """
    secret = await _key(api_client, tenant_id, scopes=["export:read"])
    await _seed(migrated_engine, tenant_id, complaints=2)
    response = await api_client.get("/api/v1/export/complaints", headers={"X-API-Key": secret})
    assert int(response.headers["X-Export-Row-Limit"]) > 0
    assert response.headers["Cache-Control"] == "no-store"


async def test_an_export_is_scoped_to_the_keys_tenant(
    api_client: AsyncClient,
    migrated_engine: AsyncEngine,
    tenant_id: uuid.UUID,
    other_tenant_id: uuid.UUID,
) -> None:
    """The key determines the tenant; a header cannot widen it."""
    secret = await _key(api_client, tenant_id, scopes=["export:read"])
    await _seed(migrated_engine, tenant_id, complaints=4)
    await _seed(migrated_engine, other_tenant_id, complaints=7, zone="CAMPUS-A")

    response = await api_client.get(
        "/api/v1/export/complaints?format=csv",
        headers={"X-API-Key": secret, "X-Tenant-ID": str(other_tenant_id)},
    )
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert len(rows) == 4
    assert {row["zone_code"] for row in rows} == {"W-01"}


async def test_an_unknown_dataset_is_a_422(api_client: AsyncClient, tenant_id: uuid.UUID) -> None:
    secret = await _key(api_client, tenant_id, scopes=["export:read"])
    response = await api_client.get("/api/v1/export/citizens", headers={"X-API-Key": secret})
    assert response.status_code == 422


async def test_an_unknown_format_is_a_422(api_client: AsyncClient, tenant_id: uuid.UUID) -> None:
    secret = await _key(api_client, tenant_id, scopes=["export:read"])
    response = await api_client.get(
        "/api/v1/export/complaints?format=xlsx", headers={"X-API-Key": secret}
    )
    assert response.status_code == 422


async def test_an_empty_export_still_carries_its_header_row(
    api_client: AsyncClient, migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A CSV with no header is one a spreadsheet opens with the wrong columns."""
    secret = await _key(api_client, tenant_id, scopes=["export:read"])
    await _seed(migrated_engine, tenant_id, complaints=0)
    response = await api_client.get(
        "/api/v1/export/complaints?format=csv", headers={"X-API-Key": secret}
    )
    assert response.text.strip() == ",".join(export.COMPLAINT_COLUMNS)


# ---------------------------------------------------------------------------
# Sandbox
# ---------------------------------------------------------------------------


async def test_the_sandbox_is_a_real_tenant_that_publishes(
    api_client: AsyncClient, migrated_engine: AsyncEngine
) -> None:
    """Not a mocked response path.

    A mock proves the shape and nothing about whether the query works, and the
    integration that passes against one fails on first contact with suppression
    and empty buckets.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from nemesis import sandbox

    slug = f"sandbox-{uuid.uuid4().hex[:8]}"
    async with async_sessionmaker(migrated_engine, expire_on_commit=False)() as session:
        summary = await sandbox.provision_sandbox(session, slug=slug, complaints=90)
        await session.commit()

    assert summary.zones == len(sandbox.SANDBOX_ZONES)
    assert summary.work_orders > 0

    listing = await api_client.get(f"/api/v1/public/{slug}/zones")
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert body["count"] == len(sandbox.SANDBOX_ZONES)

    # The whole point: an integrator meets a suppressed bucket during
    # development rather than when a real quiet ward hits it.
    assert any(zone["suppressed"] for zone in body["zones"]), (
        "the sandbox produced no suppressed zone, so an integrator never sees "
        "that branch — SBX-W is seeded thin deliberately"
    )
    assert any(not zone["suppressed"] for zone in body["zones"])


async def test_the_sandbox_is_deterministic(migrated_engine: AsyncEngine) -> None:
    """``seed`` is what lets an integrator assert against a specific figure."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from nemesis import sandbox

    counts: list[int] = []
    for _ in range(2):
        slug = f"sandbox-{uuid.uuid4().hex[:8]}"
        async with async_sessionmaker(migrated_engine, expire_on_commit=False)() as session:
            summary = await sandbox.provision_sandbox(session, slug=slug, complaints=60, seed=7)
            await session.commit()
        counts.append(summary.work_orders)
    assert counts[0] == counts[1]


async def test_the_sandbox_carries_no_citizen_prose(migrated_engine: AsyncEngine) -> None:
    """Copying production data into a sandbox is the obvious shortcut and a disclosure.

    The sandbox exists to be handed to strangers.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from nemesis import sandbox

    slug = f"sandbox-{uuid.uuid4().hex[:8]}"
    async with async_sessionmaker(migrated_engine, expire_on_commit=False)() as session:
        summary = await sandbox.provision_sandbox(session, slug=slug, complaints=30)
        await session.commit()

    async with migrated_engine.begin() as conn:
        rows = (
            await conn.execute(
                sql_text(
                    "SELECT description_text, photo_url, audio_url, "
                    "submitter_device_fingerprint FROM complaints WHERE tenant_id = :t"
                ).bindparams(t=summary.tenant_id)
            )
        ).all()

    assert rows
    for row in rows:
        assert row.description_text is None
        assert row.photo_url is None
        assert row.audio_url is None
        assert row.submitter_device_fingerprint is None


# ---------------------------------------------------------------------------


async def _key(client: AsyncClient, tenant_id: uuid.UUID, *, scopes: list[str]) -> str:
    response = await client.post(
        "/api/v1/integrations/keys",
        headers=TOKEN | {"X-Tenant-ID": str(tenant_id)},
        json={"name": "Researcher", "scopes": scopes},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["secret"])


async def _seed(
    engine: AsyncEngine,
    tenant_id: uuid.UUID,
    *,
    complaints: int,
    zone: str = "W-01",
    with_work_orders: bool = False,
) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            sql_text(
                "INSERT INTO zones (tenant_id, code, name, kind, path, depth) "
                "VALUES (:tenant, :code, 'Zone', 'ward', :code, 0) ON CONFLICT DO NOTHING"
            ).bindparams(tenant=tenant_id, code=zone)
        )
        for _index in range(complaints):
            cluster_id = uuid.uuid4()
            await conn.execute(
                sql_text(
                    "INSERT INTO complaint_clusters (id, tenant_id, centroid, first_reported, "
                    "last_reported) VALUES (:id, :tenant, "
                    "ST_GeogFromText('SRID=4326;POINT(73.856745 18.520431)'), now(), now())"
                ).bindparams(id=cluster_id, tenant=tenant_id)
            )
            await conn.execute(
                sql_text(
                    "INSERT INTO complaints (tenant_id, status, category, ward, location, "
                    "reported_at, cluster_id, description_text, "
                    "submitter_device_fingerprint, severity_score) "
                    "VALUES (:tenant, 'resolved', 'pothole_or_road_damage', :zone, "
                    "ST_GeogFromText('SRID=4326;POINT(73.856745 18.520431)'), now(), :cluster, "
                    "'the citizen''s own words', 'fp-must-not-leak', 4.2)"
                ).bindparams(tenant=tenant_id, zone=zone, cluster=cluster_id)
            )
            if with_work_orders:
                await conn.execute(
                    sql_text(
                        "INSERT INTO work_orders (tenant_id, complaint_cluster_id, status, "
                        "assigned_to_type, assigned_to_id, sla_deadline) "
                        "VALUES (:tenant, :cluster, 'closed', 'contractor', :assignee, "
                        "now() + interval '3 days')"
                    ).bindparams(tenant=tenant_id, cluster=cluster_id, assignee=uuid.uuid4())
                )
