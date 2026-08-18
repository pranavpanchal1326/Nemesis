"""Gate clause 3: every public field is provably free of exact GPS and citizen
identifiers.

**Proven over the schema, not over a sampled response.** A test that fetches one
body and greps it proves that *that row* leaked nothing — which is exactly the
assurance a fixture with no description text and no device fingerprint is
guaranteed to give while proving nothing at all. These tests walk the declared
response models of every public route and assert each field against
``public.policy.PUBLIC_FIELDS``, so a field added to a model without a
deliberate entry in the allow-list fails here rather than on a screen.

Three layers, because each catches what the others structurally cannot:

1. **Field names**, over the models — catches a forwarded column.
2. **Rendered values**, over a real response — catches a full-precision
   coordinate nested inside a field whose *name* is fine.
3. **The OpenAPI document**, over every published path — catches a route that
   was added to the public router without going through ``PublicModel`` at all.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from pydantic import BaseModel
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncEngine

from nemesis.api.v1 import public as public_v1
from nemesis.api.v2 import public as public_v2
from nemesis.public.policy import (
    FORBIDDEN_FIELDS,
    GPS_DECIMALS,
    PUBLIC_FIELDS,
    coarsen,
    find_disclosures,
)
from tests.conftest import postgres_required

#: Applied per test rather than module-wide: most of this file is a pure-function
#: walk over the response models, and a module-level asyncio mark on a synchronous
#: test is a pytest warning that `filterwarnings = ["error"]` turns into a failure.


def _public_models() -> list[type[BaseModel]]:
    """Every response model either public router can emit."""
    models: list[type[BaseModel]] = []
    for module in (public_v1, public_v2):
        for name in dir(module):
            candidate = getattr(module, name)
            if (
                isinstance(candidate, type)
                and issubclass(candidate, BaseModel)
                and candidate is not BaseModel
                and candidate.__module__ == module.__name__
                and name != "PublicModel"
            ):
                models.append(candidate)
    return models


def test_public_response_models_exist() -> None:
    """The walk below is only meaningful if it walks something.

    A discovery helper that silently finds zero models would make every
    assertion in this file vacuously true — which is the failure mode of every
    reflection-based test that nobody checked.
    """
    assert len(_public_models()) >= 6


def test_every_public_field_is_declared_safe() -> None:
    """No response model carries a field absent from ``PUBLIC_FIELDS``."""
    undeclared: list[str] = []
    for model in _public_models():
        for field in model.model_fields:
            if field not in PUBLIC_FIELDS:
                undeclared.append(f"{model.__name__}.{field}")

    assert not undeclared, (
        f"Public response fields with no entry in PUBLIC_FIELDS: {sorted(undeclared)}.\n"
        "A public field exists because a shape declared it, never because a column "
        "was forwarded. Add it to nemesis/public/policy.py with the reason it is "
        "safe to publish, or do not publish it."
    )


def test_no_public_model_carries_a_forbidden_field() -> None:
    """The named disclosures stay out, with the harm in the failure message."""
    leaks: list[str] = []
    for model in _public_models():
        for field in model.model_fields:
            if field in FORBIDDEN_FIELDS:
                leaks.append(f"{model.__name__}.{field} — {FORBIDDEN_FIELDS[field]}")
    assert not leaks, "Forbidden fields on a public surface:\n  " + "\n  ".join(leaks)


def test_allow_list_and_deny_list_do_not_overlap() -> None:
    """A field cannot be both publishable and forbidden.

    The two maps are edited by different instincts — one when adding a feature,
    one when reviewing a disclosure — and an overlap would mean the allow-list
    silently wins, because that is the check the model walk performs first.
    """
    overlap = sorted(set(PUBLIC_FIELDS) & set(FORBIDDEN_FIELDS))
    assert not overlap, f"declared both safe and forbidden: {overlap}"


def test_coarsen_holds_the_published_precision() -> None:
    assert coarsen(18.5204312) == pytest.approx(18.52, abs=1e-9)
    assert coarsen(-73.856745123) == pytest.approx(-73.857, abs=1e-9)
    assert coarsen(None) is None
    # The property that matters: the published value cannot resolve a house.
    # Three decimals is ~110 m at the equator.
    assert GPS_DECIMALS == 3


def test_disclosure_scanner_finds_a_precise_coordinate() -> None:
    findings = find_disclosures({"centroid": {"lat": 18.520431, "lng": 73.8}})
    assert any("decimal places" in f for f in findings), findings


def test_disclosure_scanner_finds_an_undeclared_field() -> None:
    findings = find_disclosures({"total_reports": 9, "submitter_email": "a@b.c"})
    assert any("submitter_email" in f for f in findings), findings


def test_disclosure_scanner_finds_an_embedded_identifier() -> None:
    findings = find_disclosures({"notice": f"complaint {uuid.uuid4()} was merged"})
    assert any("UUID" in f for f in findings), findings


def test_disclosure_scanner_does_not_flag_its_own_timestamps() -> None:
    """Regression: fractional seconds are not a coordinate.

    The first version of ``_PRECISE_COORD`` matched ``:45.123456`` inside every
    ISO-8601 timestamp the API emits, so ``generated_at`` was reported as an
    exact GPS leak on every clean response. A privacy check that fails on its
    own correct output is one people learn to ignore — which costs the real
    finding it exists for.
    """
    assert find_disclosures({"generated_at": "2026-08-17T14:23:45.123456+00:00"}) == []
    assert find_disclosures({"period_start": "2026-08-17 14:23:45.987654"}) == []
    # And it still catches the thing it is for, in a neighbouring field.
    assert find_disclosures({"zone_name": "near 18.520431, 73.856745"}) != []


def test_disclosure_scanner_allows_a_clean_payload() -> None:
    clean = {
        "api_version": "v1",
        "tenant": "pilot-city",
        "zone_code": "W-01",
        "centroid": {"lat": 18.52, "lng": 73.857},
        "total_reports": 42,
        "by_category": {"pothole_or_road_damage": 12, "co2_scrubber_fault": 3},
        "suppressed": False,
    }
    assert find_disclosures(clean) == []


def test_taxonomy_keys_are_not_treated_as_field_names() -> None:
    """``by_category`` is keyed by tenant vocabulary, which cannot be allow-listed.

    Phase 5 exists so a customer can invent ``micrometeorite_strike`` without a
    code change. A field-name allow-list that also policed those keys would fail
    on every tenant whose taxonomy was not written into this repository — which
    is every real tenant.
    """
    findings = find_disclosures({"by_category": {"micrometeorite_strike": 4}})
    assert findings == []


@postgres_required
@pytest.mark.asyncio
async def test_a_real_public_response_discloses_nothing(
    api_client: AsyncClient,
    migrated_engine: AsyncEngine,
    tenant_id: uuid.UUID,
) -> None:
    """Layer two: scan a rendered body, not a model.

    The row this seeds carries exactly the fields that must not escape — a
    description, a device fingerprint, and a full-precision location — so a
    handler that forwarded its row would be caught rather than flattered by a
    sparse fixture.
    """
    await _seed_publishing_tenant(migrated_engine, tenant_id, complaints=8)

    response = await api_client.get("/api/v1/public/pilot-city/ward/W-01/summary")
    assert response.status_code == 200, response.text

    findings = find_disclosures(response.json())
    assert findings == [], "\n".join(findings)

    # And the specific values, named, so a future reader sees what was seeded.
    body = response.text
    assert "18.520431" not in body
    assert "fingerprint-must-not-leak" not in body
    assert "the citizen's own words" not in body


@postgres_required
@pytest.mark.asyncio
async def test_every_published_path_is_covered_by_the_allow_list(
    api_client: AsyncClient,
) -> None:
    """Layer three: the OpenAPI document.

    Catches the route that never went through ``PublicModel`` — a handler
    returning a bare dict has no model for layer one to walk, and layer two only
    covers paths a test remembered to call.
    """
    spec = (await api_client.get("/openapi.json")).json()
    schemas = spec.get("components", {}).get("schemas", {})

    undeclared: list[str] = []
    for path, item in spec.get("paths", {}).items():
        if "/public/" not in path:
            continue
        for method, operation in item.items():
            if method not in {"get", "post"}:
                continue
            schema = (
                operation.get("responses", {})
                .get("200", {})
                .get("content", {})
                .get("application/json", {})
                .get("schema", {})
            )
            undeclared.extend(
                f"{path}: {field}"
                for field in _fields_of(schema, schemas)
                if field not in PUBLIC_FIELDS
            )

    assert not undeclared, (
        "Public paths expose fields with no PUBLIC_FIELDS entry:\n  "
        + "\n  ".join(sorted(undeclared))
    )


def _fields_of(
    schema: dict[str, Any], schemas: dict[str, Any], seen: frozenset[str] = frozenset()
) -> set[str]:
    ref = schema.get("$ref")
    if isinstance(ref, str):
        name = ref.rsplit("/", 1)[-1]
        if name in seen:
            return set()
        return _fields_of(schemas.get(name, {}), schemas, seen | {name})

    found: set[str] = set()
    for key in ("anyOf", "oneOf", "allOf"):
        for option in schema.get(key, []) or []:
            if isinstance(option, dict):
                found |= _fields_of(option, schemas, seen)
    items = schema.get("items")
    if isinstance(items, dict):
        found |= _fields_of(items, schemas, seen)
    for name, prop in (schema.get("properties") or {}).items():
        found.add(name)
        if isinstance(prop, dict):
            found |= _fields_of(prop, schemas, seen)
    return found


# ---------------------------------------------------------------------------


async def _seed_publishing_tenant(
    engine: AsyncEngine, tenant_id: uuid.UUID, *, complaints: int
) -> None:
    """A publishing tenant, one zone, and complaints carrying sensitive fields."""
    async with engine.begin() as conn:
        await conn.execute(
            sql_text(
                "UPDATE tenants SET public_api_enabled = true, public_api_min_aggregate = 5 "
                "WHERE id = :tenant"
            ).bindparams(tenant=tenant_id)
        )
        await conn.execute(
            sql_text(
                "INSERT INTO zones (tenant_id, code, name, kind, path, depth, centroid) "
                "VALUES (:tenant, 'W-01', 'Ward 1', 'ward', 'W-01', 0, "
                "ST_GeogFromText('SRID=4326;POINT(73.856745 18.520431)'))"
            ).bindparams(tenant=tenant_id)
        )
        for index in range(complaints):
            await conn.execute(
                sql_text(
                    "INSERT INTO complaints (tenant_id, status, category, ward, location, "
                    "reported_at, description_text, submitter_device_fingerprint, "
                    "severity_score) VALUES (:tenant, 'resolved', 'pothole_or_road_damage', "
                    "'W-01', ST_GeogFromText('SRID=4326;POINT(73.856745 18.520431)'), "
                    "now(), :description, 'fingerprint-must-not-leak', 5.5)"
                ).bindparams(
                    tenant=tenant_id,
                    description=f"the citizen's own words, report {index}",
                )
            )
