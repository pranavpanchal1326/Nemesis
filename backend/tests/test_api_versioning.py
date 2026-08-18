"""Gate clause 1: a v1 consumer keeps working after v2 ships.

**Proven against a v2 that actually exists and actually breaks.** A test that
asserts v1 still works while nothing has changed asserts nothing at all — so v2
ships a genuinely breaking reshape (counts moved under ``totals``, ``/ward/``
renamed to ``/zone/``), and these tests show a recorded v1 consumer surviving it.

The pinned contract test is ``test_api_contract.py``; this file covers the
registry, the published clock, and the runtime behaviour of both versions
side by side.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncEngine

from nemesis.api.versioning import (
    PUBLIC_API_NOTICE_DAYS,
    ApiVersion,
    VersionRegistryError,
    VersionStatus,
    _validate,
    all_versions,
    current_version,
    get_version,
    next_sunset_date,
    sunset_notice_remaining,
    version_headers,
)
from tests.conftest import postgres_required

# ---------------------------------------------------------------------------
# The registry and the published clock
# ---------------------------------------------------------------------------


def test_current_version_is_never_a_preview() -> None:
    """New integrations must not be pointed at a version allowed to change."""
    assert current_version().status is VersionStatus.ACTIVE


def test_v1_is_active_and_carries_no_sunset() -> None:
    v1 = get_version("v1")
    assert v1.status is VersionStatus.ACTIVE
    assert v1.sunset_on is None


def test_a_deprecation_shorter_than_the_published_notice_is_refused() -> None:
    """The mistake this prevents is the realistic one.

    Somebody deprecates v1 with a three-month sunset because v2 is ready and
    twelve months feels theoretical. It is not theoretical to the newsroom that
    integrated last year, and ``docs/RELEASE.md`` published the number.
    """
    announced = date(2026, 9, 1)
    with pytest.raises(VersionRegistryError, match="days of notice"):
        _validate(
            ApiVersion(
                name="v1",
                status=VersionStatus.DEPRECATED,
                released_on=date(2026, 1, 1),
                deprecated_on=announced,
                sunset_on=announced + timedelta(days=90),
                successor="v2",
            )
        )


def test_a_deprecation_with_no_successor_is_refused() -> None:
    announced = date(2026, 9, 1)
    with pytest.raises(VersionRegistryError, match="eviction"):
        _validate(
            ApiVersion(
                name="v1",
                status=VersionStatus.DEPRECATED,
                released_on=date(2026, 1, 1),
                deprecated_on=announced,
                sunset_on=next_sunset_date(announced),
                successor=None,
            )
        )


def test_a_sunset_date_on_an_active_version_is_refused() -> None:
    """A version with a removal date is deprecated by definition.

    Leaving the status active means the headers never announce it, so the clock
    runs in the registry and nowhere a consumer can see.
    """
    with pytest.raises(VersionRegistryError, match="carries a sunset date"):
        _validate(
            ApiVersion(
                name="v9",
                status=VersionStatus.ACTIVE,
                released_on=date(2026, 1, 1),
                sunset_on=date(2027, 1, 1),
            )
        )


def test_a_legal_deprecation_validates() -> None:
    announced = date(2026, 9, 1)
    _validate(
        ApiVersion(
            name="v1",
            status=VersionStatus.DEPRECATED,
            released_on=date(2026, 1, 1),
            deprecated_on=announced,
            sunset_on=next_sunset_date(announced),
            successor="v2",
        )
    )
    assert (next_sunset_date(announced) - announced).days == PUBLIC_API_NOTICE_DAYS


def test_deprecation_headers_are_the_standard_ones() -> None:
    """RFC 9745 / RFC 8594, not an invented ``X-`` pair.

    The point of announcing in a header is that generic tooling notices without
    being taught our vocabulary.
    """
    announced = date(2026, 9, 1)
    headers = version_headers(
        ApiVersion(
            name="v1",
            status=VersionStatus.DEPRECATED,
            released_on=date(2026, 1, 1),
            deprecated_on=announced,
            sunset_on=next_sunset_date(announced),
            successor="v2",
        )
    )
    assert headers["Deprecation"] == "Tue, 01 Sep 2026 00:00:00 GMT"
    assert headers["Sunset"] == "Wed, 01 Sep 2027 00:00:00 GMT"
    assert 'rel="deprecation"' in headers["Link"]
    assert 'rel="successor-version"' in headers["Link"]


def test_an_active_version_announces_no_clock() -> None:
    headers = version_headers(get_version("v1"))
    assert "Deprecation" not in headers
    assert "Sunset" not in headers
    assert headers["X-API-Version"] == "v1"


def test_a_preview_version_says_so() -> None:
    assert version_headers(get_version("v2"))["X-API-Stability"] == "preview"


def test_expiry_is_computed_from_the_date_not_the_status() -> None:
    """A deployment nobody updated still stops serving on schedule.

    The promise made to consumers was a date, not a promise that somebody would
    remember to redeploy on it.
    """
    version = ApiVersion(
        name="v0",
        status=VersionStatus.DEPRECATED,
        released_on=date(2025, 1, 1),
        deprecated_on=date(2025, 1, 1),
        sunset_on=date(2026, 1, 1),
        successor="v1",
    )
    assert version.is_expired(date(2026, 1, 1)) is True
    assert version.is_expired(date(2025, 12, 31)) is False
    assert sunset_notice_remaining(version, date(2025, 12, 1)) == 31
    # Negative once past — surfaced rather than clamped, because an expired
    # version still answering requests is a broken promise in the other
    # direction and consumers who did migrate deserve to know.
    assert sunset_notice_remaining(version, date(2026, 2, 1)) == -31


# ---------------------------------------------------------------------------
# Both versions, live, side by side
# ---------------------------------------------------------------------------


@postgres_required
@pytest.mark.asyncio
async def test_v1_survives_v2(
    api_client: AsyncClient, migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The gate clause, as one assertion pair.

    A recorded v1 consumer reads ``body["total_reports"]`` at the top level and
    calls ``/ward/``. v2 moved the first under ``totals`` and renamed the second
    to ``/zone/``. Both of those would have broken this consumer if they had been
    applied to v1 in place — and neither does, because they were shipped as a
    version.
    """
    await _seed(migrated_engine, tenant_id, complaints=9)

    v1 = await api_client.get("/api/v1/public/pilot-city/ward/W-01/summary")
    assert v1.status_code == 200, v1.text
    v1_body = v1.json()

    # The v1 consumer's exact access pattern, unchanged.
    assert v1_body["total_reports"] == 9
    assert v1_body["api_version"] == "v1"
    assert "totals" not in v1_body

    v2 = await api_client.get("/api/v2/public/pilot-city/zone/W-01/summary")
    assert v2.status_code == 200, v2.text
    v2_body = v2.json()

    # v2 is genuinely different, which is what makes the assertion above mean
    # something.
    assert v2_body["totals"]["total_reports"] == 9
    assert "total_reports" not in v2_body
    assert v2_body["api_version"] == "v2"

    # And the v1 path does not exist on v2 — a rename is breaking, by definition.
    assert (await api_client.get("/api/v2/public/pilot-city/ward/W-01/summary")).status_code == 404


@postgres_required
@pytest.mark.asyncio
async def test_both_versions_report_the_same_figures(
    api_client: AsyncClient, migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Two shapes, one arithmetic.

    v1 and v2 share ``public.aggregates``; if they did not, they would eventually
    disagree about a resolution rate and a reader comparing two URLs would find
    it before we did.
    """
    await _seed(migrated_engine, tenant_id, complaints=12)

    v1 = (await api_client.get("/api/v1/public/pilot-city/ward/W-01/summary")).json()
    v2 = (await api_client.get("/api/v2/public/pilot-city/zone/W-01/summary")).json()

    assert v1["total_reports"] == v2["totals"]["total_reports"]
    assert v1["resolved_reports"] == v2["totals"]["resolved_reports"]
    assert v1["resolution_rate"] == v2["totals"]["resolution_rate"]
    assert v1["by_category"] == v2["by_category"]


@postgres_required
@pytest.mark.asyncio
async def test_responses_carry_their_version_header(
    api_client: AsyncClient, migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    await _seed(migrated_engine, tenant_id, complaints=9)
    v1 = await api_client.get("/api/v1/public/pilot-city/ward/W-01/summary")
    v2 = await api_client.get("/api/v2/public/pilot-city/zone/W-01/summary")
    assert v1.headers["X-API-Version"] == "v1"
    assert v2.headers["X-API-Version"] == "v2"
    assert v2.headers["X-API-Stability"] == "preview"


@postgres_required
@pytest.mark.asyncio
async def test_version_discovery_publishes_the_clock(api_client: AsyncClient) -> None:
    """A consumer that polls this warns its own operators without reading a changelog."""
    response = await api_client.get("/api/v1/versions")
    assert response.status_code == 200
    body = response.json()

    assert body["current"] == "v1"
    assert body["notice_period_days"] == PUBLIC_API_NOTICE_DAYS
    assert {v["name"] for v in body["versions"]} == {v.name for v in all_versions()}
    assert next(v for v in body["versions"] if v["name"] == "v2")["status"] == "preview"


@postgres_required
@pytest.mark.asyncio
async def test_the_portal_renders_without_external_assets(api_client: AsyncClient) -> None:
    """A public accountability page that fetches a font leaks every reader's address."""
    response = await api_client.get("/developers")
    assert response.status_code == 200
    body = response.text

    assert "NEMESIS developer reference" in body
    assert "hmac.compare_digest" in body  # the worked verification example

    # The property is "loads nothing from off-origin", not "contains no URL" —
    # the page's own example requests are absolute by design so a reader can
    # paste them. So this looks for asset-loading constructs, which is what
    # would actually make a third party observe the reader.
    for construct in ("<script", "<link", "<img", "@import", "url(", "srcset", "<iframe"):
        assert construct not in body.lower(), construct


# ---------------------------------------------------------------------------


async def _seed(engine: AsyncEngine, tenant_id: uuid.UUID, *, complaints: int) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            sql_text(
                "UPDATE tenants SET public_api_enabled = true, public_api_min_aggregate = 5 "
                "WHERE id = :tenant"
            ).bindparams(tenant=tenant_id)
        )
        await conn.execute(
            sql_text(
                "INSERT INTO zones (tenant_id, code, name, kind, path, depth) "
                "VALUES (:tenant, 'W-01', 'Ward 1', 'ward', 'W-01', 0)"
            ).bindparams(tenant=tenant_id)
        )
        for index in range(complaints):
            await conn.execute(
                sql_text(
                    "INSERT INTO complaints (tenant_id, status, category, ward, location, "
                    "reported_at) VALUES (:tenant, :status, 'pothole_or_road_damage', 'W-01', "
                    "ST_GeogFromText('SRID=4326;POINT(73.8567 18.5204)'), now())"
                ).bindparams(
                    tenant=tenant_id,
                    status="resolved" if index % 3 else "in_progress",
                )
            )
