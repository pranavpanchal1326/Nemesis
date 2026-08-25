"""Resolving a coordinate to the place tree — §E17.1's *Place* card.

Integration rather than unit, and it has to be: the whole endpoint is one
`ST_Covers` against a GiST index and one walk up a materialised path. There is
no pure function underneath worth testing in isolation — a fake would be
asserting that PostGIS does what PostGIS does.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient

from nemesis.api.deps import TENANT_HEADER
from tests.conftest import postgres_required

pytestmark = [postgres_required, pytest.mark.integration]

RESOLVE = "/api/v1/places/resolve"


def _box(latitude: float, longitude: float, half: float) -> str:
    """A closed ring as WKT, in `longitude latitude` order.

    The order is the thing that catches people out: every coordinate a person
    says is latitude-first, and every coordinate PostGIS takes is `x y`, where
    `x` is longitude.
    """
    west, east = longitude - half, longitude + half
    south, north = latitude - half, latitude + half
    ring = f"{west} {south}, {east} {south}, {east} {north}, {west} {north}, {west} {south}"
    return f"MULTIPOLYGON((({ring})))"


async def _zone(
    api_client: AsyncClient,
    tenant_id: uuid.UUID,
    *,
    code: str,
    name: str,
    kind: str,
    depth: int,
    path: str,
    parent: uuid.UUID | None = None,
    boundary: str | None = None,
) -> uuid.UUID:
    """Insert one zone directly.

    Through SQL rather than the control-plane route, on purpose: this file is
    about the *read*, and provisioning a whole tenant per assertion would make
    every failure here a failure about provisioning.
    """
    from sqlalchemy import text

    from nemesis.db.session import session_scope
    from nemesis.tenancy.context import tenant_scope

    zone_id = uuid.uuid4()
    with tenant_scope(tenant_id):
        async with session_scope() as session:
            await session.execute(
                text(
                    "INSERT INTO zones "
                    "(id, tenant_id, name, code, kind, parent_id, path, depth, boundary, "
                    " is_active, attributes, created_at, updated_at, version) "
                    "VALUES (:id, :tenant, :name, :code, :kind, :parent, :path, :depth, "
                    # Cast before the CASE, not inside it. asyncpg infers a
                    # parameter's type from how it is used, and a bare `:boundary`
                    # appearing in both branches gives it two answers and no way
                    # to pick — which surfaces as `AmbiguousParameterError`
                    # rather than as anything about geometry.
                    "        CASE WHEN CAST(:boundary AS text) IS NULL THEN NULL "
                    "             ELSE ST_GeogFromText(CAST(:boundary AS text)) END, "
                    "        true, '{}'::jsonb, now(), now(), 1)"
                ).bindparams(
                    id=zone_id,
                    tenant=tenant_id,
                    name=name,
                    code=code,
                    kind=kind,
                    parent=parent,
                    path=path,
                    depth=depth,
                    boundary=boundary,
                )
            )
    return zone_id


async def _seed_city(api_client: AsyncClient, tenant_id: uuid.UUID) -> None:
    """A city, a zone, a ward with geometry, and a locality inside the ward."""
    city = await _zone(
        api_client, tenant_id, code="CITY", name="Pune", kind="city", depth=0, path="CITY"
    )
    west = await _zone(
        api_client,
        tenant_id,
        code="Z-WEST",
        name="West Zone",
        kind="zone",
        depth=1,
        path="CITY/Z-WEST",
        parent=city,
    )
    ward = await _zone(
        api_client,
        tenant_id,
        code="W-KOTHRUD",
        name="Kothrud",
        kind="ward",
        depth=2,
        path="CITY/Z-WEST/W-KOTHRUD",
        parent=west,
        boundary=_box(18.5074, 73.8077, 0.012),
    )
    await _zone(
        api_client,
        tenant_id,
        code="L-PAUD",
        name="Paud Road",
        kind="locality",
        depth=3,
        path="CITY/Z-WEST/W-KOTHRUD/L-PAUD",
        parent=ward,
        boundary=_box(18.5074, 73.8077, 0.002),
    )


async def _resolve(
    api_client: AsyncClient, tenant_id: uuid.UUID, latitude: float, longitude: float
) -> Any:
    response = await api_client.get(
        RESOLVE,
        params={"latitude": latitude, "longitude": longitude},
        headers={TENANT_HEADER: str(tenant_id)},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_a_point_resolves_to_the_whole_chain_innermost_first(
    api_client: AsyncClient, tenant_id: uuid.UUID
) -> None:
    """§E17.1 asks for a *sentence*, not a row.

    *"Paud Road, near Karve Statue · Kothrud · Ward 14"* — the card reads
    inward-out, so the response is ordered that way and a surface joins it
    rather than sorting it. Only the leaves carry geometry, which is why the
    ancestors come from the materialised path rather than from a second spatial
    query that would match nothing.
    """
    await _seed_city(api_client, tenant_id)
    body = await _resolve(api_client, tenant_id, 18.5074, 73.8077)

    assert [unit["name"] for unit in body["units"]] == [
        "Paud Road",
        "Kothrud",
        "West Zone",
        "Pune",
    ]
    assert [unit["kind"] for unit in body["units"]] == ["locality", "ward", "zone", "city"]
    assert body["boundaries_configured"] is True


async def test_the_smallest_containing_unit_wins(
    api_client: AsyncClient, tenant_id: uuid.UUID
) -> None:
    """A locality inside a ward is a deliberate nesting, and both cover the point.

    Depth-descending picks the locality. Without it the card would name the ward
    and the citizen would be told something true and less useful than what the
    system knows.
    """
    await _seed_city(api_client, tenant_id)
    body = await _resolve(api_client, tenant_id, 18.5074, 73.8077)
    assert body["units"][0]["code"] == "L-PAUD"

    # A point inside the ward but outside the locality drops one level, rather
    # than falling out of the tree.
    outer = await _resolve(api_client, tenant_id, 18.5150, 73.8150)
    assert [unit["code"] for unit in outer["units"]] == ["W-KOTHRUD", "Z-WEST", "CITY"]


async def test_a_point_outside_every_boundary_is_not_an_error(
    api_client: AsyncClient, tenant_id: uuid.UUID
) -> None:
    """And it is distinguishable from a tenant that maps no places at all.

    The two look identical in `units` and mean completely different things to
    the person holding the phone: *"you appear to be outside the city"* is a
    correction they can act on; *"we do not map places here"* is not about them.
    """
    await _seed_city(api_client, tenant_id)
    body = await _resolve(api_client, tenant_id, 12.9716, 77.5946)  # Bengaluru
    assert body["units"] == []
    assert body["boundaries_configured"] is True


async def test_a_tenant_with_no_geometry_says_so(
    api_client: AsyncClient, tenant_id: uuid.UUID
) -> None:
    """The common case at onboarding: ward names, no shapefile.

    `nemesis/db/models/organisation.py` says it outright — a tenant knows its
    ward names long before anyone has geometry — so this branch is the default
    experience, not an edge case.
    """
    await _zone(
        api_client, tenant_id, code="W-01", name="Ward 1", kind="ward", depth=0, path="W-01"
    )
    body = await _resolve(api_client, tenant_id, 18.5074, 73.8077)
    assert body["units"] == []
    assert body["boundaries_configured"] is False


async def test_a_boundary_belongs_to_exactly_one_tenant(
    api_client: AsyncClient, tenant_id: uuid.UUID, other_tenant_id: uuid.UUID
) -> None:
    """Geometry is not shared. The most obvious multi-tenant leak in a spatial
    query is a `WHERE` that scopes the *point* and forgets the *polygon*, and
    it fails open — every tenant sees every other tenant's map."""
    await _seed_city(api_client, tenant_id)
    body = await _resolve(api_client, other_tenant_id, 18.5074, 73.8077)
    assert body["units"] == []
    assert body["boundaries_configured"] is False


async def test_a_coordinate_outside_the_earth_is_refused(
    api_client: AsyncClient, tenant_id: uuid.UUID
) -> None:
    """`Latitude`/`Longitude` are the catalog's own constrained types, so the
    refusal comes from the same validator the event payloads use — one
    definition of what a coordinate is, rather than a second one on the read
    path that could drift from it."""
    response = await api_client.get(
        RESOLVE,
        params={"latitude": 91.0, "longitude": 73.8},
        headers={TENANT_HEADER: str(tenant_id)},
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
