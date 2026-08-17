"""The control-plane API — the Phase 5 gate over HTTP.

The gate's first clause is "onboarded end to end **without a code change or a
deploy**", and the only honest way to demonstrate that is through the surface a
solutions engineer would actually use. The service-level tests prove the logic;
these prove the surface — token enforcement, error translation, and the fact
that a tenant created by one request is usable by the next.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from nemesis.api.v1.control_plane import CONTROL_PLANE_TOKEN_HEADER
from tests.conftest import postgres_required

pytestmark = [postgres_required, pytest.mark.integration]

#: The development default from ``Settings``. A pilot deployment refuses to boot
#: with it, which is asserted in the configuration tests rather than here.
DEV_TOKEN = "dev-only-insecure-control-plane-token-change-me"


def admin() -> dict[str, str]:
    return {CONTROL_PLANE_TOKEN_HEADER: DEV_TOKEN}


def tenant_headers(tenant_id: uuid.UUID, *, token: bool = False) -> dict[str, str]:
    headers = {"X-Tenant-ID": str(tenant_id)}
    if token:
        headers |= admin()
    return headers


async def provision(api_client: AsyncClient, slug: str, **body: object) -> dict[str, object]:
    response = await api_client.post(
        "/api/v1/control-plane/tenants",
        headers=admin(),
        json={"tenant": {"slug": slug, "name": slug.title()}} | body,
    )
    assert response.status_code == 201, response.text
    result: dict[str, object] = response.json()
    return result


# ---------------------------------------------------------------------------
# Authorisation
# ---------------------------------------------------------------------------


async def test_provisioning_without_the_token_is_refused(api_client: AsyncClient) -> None:
    """An open tenant-creation endpoint would let anyone mint a customer.

    403 rather than 401: there is no authentication scheme to challenge with
    until Phase 13, and a ``WWW-Authenticate`` header naming one that does not
    exist would be a lie a client could act on.
    """
    response = await api_client.post(
        "/api/v1/control-plane/tenants",
        json={"tenant": {"slug": "sneaky", "name": "Sneaky"}},
    )
    assert response.status_code == 403
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_a_wrong_token_is_refused(api_client: AsyncClient) -> None:
    response = await api_client.post(
        "/api/v1/control-plane/tenants",
        headers={CONTROL_PLANE_TOKEN_HEADER: "not-the-token"},
        json={"tenant": {"slug": "sneaky", "name": "Sneaky"}},
    )
    assert response.status_code == 403


async def test_a_taxonomy_write_without_the_token_is_refused(
    api_client: AsyncClient, tenant_id: uuid.UUID
) -> None:
    """Redefining what a complaint means is a write, not a read."""
    response = await api_client.post(
        "/api/v1/control-plane/taxonomy",
        headers=tenant_headers(tenant_id),
        json={"key": "smuggled", "display_name": "Smuggled"},
    )
    assert response.status_code == 403


async def test_reads_do_not_need_the_token(api_client: AsyncClient, tenant_id: uuid.UUID) -> None:
    """Reading your own taxonomy is the same class of operation as reading your
    own complaint, and goes through the same tenant resolution."""
    response = await api_client.get(
        "/api/v1/control-plane/taxonomy", headers=tenant_headers(tenant_id)
    )
    assert response.status_code == 200
    assert response.json()["nodes"] == []


# ---------------------------------------------------------------------------
# The gate, over HTTP
# ---------------------------------------------------------------------------


async def test_the_template_library_is_listed_without_a_tenant(
    api_client: AsyncClient,
) -> None:
    response = await api_client.get("/api/v1/control-plane/templates")
    assert response.status_code == 200
    names = {entry["name"] for entry in response.json()}
    assert names == {"campus", "industrial_park", "municipality"}


async def test_a_campus_is_onboarded_and_immediately_usable(api_client: AsyncClient) -> None:
    """Provision, then read back through a *separate request*.

    The second request is the point. Provisioning returning 201 proves a
    transaction committed; resolving the new tenant from a header and serving
    its taxonomy proves the tenant is real to the rest of the system — which is
    what "onboarded end to end" means.
    """
    result = await provision(api_client, "campus-http", template="campus")
    tenant = uuid.UUID(str(result["tenant_id"]))

    listing = await api_client.get("/api/v1/control-plane/taxonomy", headers=tenant_headers(tenant))
    assert listing.status_code == 200
    body = listing.json()
    keys = {node["key"] for node in body["nodes"]}

    assert "elevator_fault" in keys
    assert "pothole" not in keys
    assert body["revision"] == 1
    assert len(body["content_hash"]) == 64

    departments = await api_client.get(
        "/api/v1/control-plane/departments", headers=tenant_headers(tenant)
    )
    assert {d["code"] for d in departments.json()} >= {"EST", "EST-MECH"}

    zones = await api_client.get("/api/v1/control-plane/zones", headers=tenant_headers(tenant))
    assert {z["code"] for z in zones.json()} >= {"CAMPUS", "BLD-CHEM"}


async def test_a_wholly_novel_taxonomy_is_accepted_with_no_template(
    api_client: AsyncClient,
) -> None:
    """The sharpest form of the gate: a vocabulary nothing in the repo knows.

    No template, no seeded category, and every key invented by the caller. If
    anything in the pipeline still assumed a fixed category set, this is where
    it would surface.
    """
    result = await provision(
        api_client,
        "orbital-station",
        departments=[
            {"code": "LIFE", "name": "Life Support", "kind": "section"},
            {"code": "HULL", "name": "Hull Integrity", "kind": "section"},
        ],
        taxonomy=[
            {
                "key": "micrometeorite_strike",
                "display_name": "Micrometeorite strike",
                "severity_semantics": {"floor": 9.0, "bypasses_scoring": True},
                "routing_hints": {"department_code": "HULL"},
            },
            {
                "key": "co2_scrubber_fault",  # gitleaks:allow
                "display_name": "CO2 scrubber fault",
                "routing_hints": {"department_code": "LIFE"},
            },
        ],
    )
    tenant = uuid.UUID(str(result["tenant_id"]))

    listing = await api_client.get("/api/v1/control-plane/taxonomy", headers=tenant_headers(tenant))
    nodes = {node["key"]: node for node in listing.json()["nodes"]}

    assert set(nodes) == {"micrometeorite_strike", "co2_scrubber_fault"}
    assert nodes["micrometeorite_strike"]["severity_semantics"]["bypasses_scoring"] is True
    assert nodes["micrometeorite_strike"]["routing_hints"]["department_code"] == "HULL"


async def test_two_tenants_with_conflicting_taxonomies_are_served_separately(
    api_client: AsyncClient,
) -> None:
    """Gate clause 3, over HTTP, in one process, against one database.

    The same key under two different parents, requested back to back. A leak
    here is one customer's vocabulary served to another.
    """
    first = await provision(
        api_client,
        "city-api",
        template="municipality",
        tenant={
            "slug": "city-api",
            "name": "City",
            "locales": ["en", "hi", "mr"],
            "primary_locale": "en",
        },
    )
    second = await provision(api_client, "campus-api", template="campus")

    city = await api_client.get(
        "/api/v1/control-plane/taxonomy",
        headers=tenant_headers(uuid.UUID(str(first["tenant_id"]))),
    )
    campus = await api_client.get(
        "/api/v1/control-plane/taxonomy",
        headers=tenant_headers(uuid.UUID(str(second["tenant_id"]))),
    )

    city_keys = {node["key"] for node in city.json()["nodes"]}
    campus_keys = {node["key"] for node in campus.json()["nodes"]}
    assert city_keys & campus_keys == set()
    assert "pothole" in city_keys
    assert "elevator_fault" in campus_keys


async def test_a_category_added_after_onboarding_bumps_the_revision(
    api_client: AsyncClient,
) -> None:
    """Adding a category is a request, not a deploy — and it is recorded."""
    result = await provision(api_client, "growing", template="campus")
    tenant = uuid.UUID(str(result["tenant_id"]))

    created = await api_client.post(
        "/api/v1/control-plane/taxonomy",
        headers=tenant_headers(tenant, token=True),
        json={
            "key": "bike_theft",
            "display_name": "Bicycle theft",
            "routing_hints": {"department_code": "SEC"},
        },
    )
    assert created.status_code == 201, created.text

    listing = await api_client.get("/api/v1/control-plane/taxonomy", headers=tenant_headers(tenant))
    body = listing.json()
    assert "bike_theft" in {node["key"] for node in body["nodes"]}
    assert body["revision"] == 2


async def test_a_taxonomy_key_belonging_to_another_tenant_is_not_found(
    api_client: AsyncClient, tenant_id: uuid.UUID
) -> None:
    """404, never 403.

    A distinguishable "exists but forbidden" turns the endpoint into an oracle
    for enumerating another customer's taxonomy one key at a time — the same
    reasoning ``api.deps`` applies to unknown tenants.
    """
    owner = await provision(api_client, "owner-api", template="campus")
    assert owner["template"] == "campus"

    response = await api_client.get(
        "/api/v1/control-plane/taxonomy/elevator_fault/subtree",
        headers=tenant_headers(tenant_id),
    )
    assert response.status_code == 404
    assert "forbidden" not in response.text.lower()


async def test_a_duplicate_slug_surfaces_as_409(api_client: AsyncClient) -> None:
    await provision(api_client, "taken-api")
    response = await api_client.post(
        "/api/v1/control-plane/tenants",
        headers=admin(),
        json={"tenant": {"slug": "taken-api", "name": "Again"}},
    )
    assert response.status_code == 409


async def test_a_dangling_routing_hint_surfaces_as_422(api_client: AsyncClient) -> None:
    response = await api_client.post(
        "/api/v1/control-plane/tenants",
        headers=admin(),
        json={
            "tenant": {"slug": "dangling-api", "name": "Dangling"},
            "taxonomy": [
                {
                    "key": "thing",
                    "display_name": "Thing",
                    "routing_hints": {"department_code": "NOPE"},
                }
            ],
        },
    )
    assert response.status_code == 422
    assert "NOPE" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Calendars and translations, over HTTP
# ---------------------------------------------------------------------------


async def test_the_deadline_preview_reports_the_monsoon_adjustment(
    api_client: AsyncClient,
) -> None:
    """The endpoint exists so a misconfigured season is a check, not a discovery.

    The municipality template declares a 1.6x monsoon window; a 24-hour budget
    starting inside it must come back stretched and must say which span
    stretched it.
    """
    result = await provision(
        api_client,
        "monsoon-api",
        template="municipality",
        tenant={
            "slug": "monsoon-api",
            "name": "Monsoon",
            "locales": ["en", "hi", "mr"],
            "primary_locale": "en",
        },
    )
    tenant = uuid.UUID(str(result["tenant_id"]))

    response = await api_client.post(
        "/api/v1/control-plane/calendars/preview-deadline",
        headers=tenant_headers(tenant),
        json={
            "start": "2026-07-01T10:00:00+05:30",
            "budget_hours": 24,
            "calendar_code": "civic-standard",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["adjustments"] == {"monsoon": "1.600"}
    assert body["working_hours_consumed"] == pytest.approx(38.4)


async def test_a_new_language_is_an_import_not_a_release(api_client: AsyncClient) -> None:
    """The Phase 5 requirement, executed: PUT a bundle, read it back, see coverage."""
    result = await provision(
        api_client,
        "polyglot",
        tenant={
            "slug": "polyglot",
            "name": "Polyglot",
            "locales": ["en", "ta"],
            "primary_locale": "en",
        },
        taxonomy=[{"key": "leaky_tap", "display_name": "Leaky tap"}],
    )
    tenant = uuid.UUID(str(result["tenant_id"]))

    imported = await api_client.put(
        "/api/v1/control-plane/translations",
        headers=tenant_headers(tenant, token=True),
        json={"namespace": "taxonomy", "locale": "ta", "entries": {"leaky_tap": "கசியும் குழாய்"}},
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["written"] == 1

    bundle = await api_client.get(
        "/api/v1/control-plane/translations/taxonomy/ta", headers=tenant_headers(tenant)
    )
    assert bundle.json() == {"leaky_tap": "கசியும் குழாய்"}

    coverage = await api_client.get(
        "/api/v1/control-plane/translations/coverage", headers=tenant_headers(tenant)
    )
    by_locale = {entry["locale"]: entry for entry in coverage.json()}
    assert by_locale["ta"]["ratio"] == pytest.approx(1.0)
    assert by_locale["en"]["missing_keys"] == ["leaky_tap"]


async def test_prompts_are_attached_without_a_deploy(api_client: AsyncClient) -> None:
    """Phase 9's gate depends on this: a new category is classifiable by prompts."""
    result = await provision(
        api_client,
        "prompted",
        taxonomy=[{"key": "leaky_tap", "display_name": "Leaky tap"}],
    )
    tenant = uuid.UUID(str(result["tenant_id"]))

    response = await api_client.put(
        "/api/v1/control-plane/taxonomy/prompt-sets",
        headers=tenant_headers(tenant, token=True),
        json={
            "node_key": "leaky_tap",
            "locale": "en",
            "encoder": "clip",
            "prompts": ["a photo of a dripping tap"],
            "prompt_set_version": "leaky-1",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["prompt_set_version"] == "leaky-1"
