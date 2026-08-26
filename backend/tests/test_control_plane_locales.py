"""A2 — a locale can be added to a tenant that already exists, and the log says who.

Phase 18's gate is *"a locale added in the control plane appears in the UI with
no code change."* Its first clause had no mechanism: ``ProvisioningRequest``
accepts ``locales`` once, at birth, and nothing could change them afterwards, so
adding a language to a running city meant an ``UPDATE`` typed into ``psql`` —
the state ADR-0046 described for publication, in the same words.

The end-to-end claim needs a browser and a running stack and belongs to
``scripts/gate_phase18_locale.py``. What is asserted here is what a gate script
cannot see: that the list is normalised rather than trusted, that the change
lands on the tenant's chain, that a primary locale outside the list is refused,
and that the public surface publishes the result — which is what the reader's
language switch is built from.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncEngine

from nemesis.api.v1.control_plane import CONTROL_PLANE_TOKEN_HEADER
from nemesis.control_plane.locales import ACTION
from tests.conftest import postgres_required

pytestmark = [postgres_required, pytest.mark.asyncio, pytest.mark.integration]

DEV_TOKEN = "dev-only-insecure-control-plane-token-change-me"
REASON = "Council resolution 2026/118 — Konkani added to the portal."


def admin() -> dict[str, str]:
    return {CONTROL_PLANE_TOKEN_HEADER: DEV_TOKEN}


async def set_locales(
    api_client: AsyncClient, slug: str = "pilot-city", **body: object
) -> tuple[int, dict[str, object]]:
    response = await api_client.put(
        f"/api/v1/control-plane/tenants/{slug}/locales",
        json={"locales": ["en", "kok"], "justification": REASON} | body,
        headers=admin(),
    )
    payload: dict[str, object] = response.json()
    return response.status_code, payload


async def admin_actions(engine: AsyncEngine, tenant_id: uuid.UUID) -> list[dict[str, object]]:
    async with engine.begin() as conn:
        rows = await conn.execute(
            sql_text(
                "SELECT payload FROM events WHERE tenant_id = :tenant "
                "AND event_type = 'admin_action' ORDER BY id"
            ).bindparams(tenant=tenant_id)
        )
        return [row[0] for row in rows.all()]


async def test_a_locale_is_added_over_http(
    api_client: AsyncClient, migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The clause Phase 18 asks for, at the layer it is written."""
    status, body = await set_locales(api_client)
    assert status == 200, body
    assert body["locales"] == ["en", "kok"]
    assert body["changed"] is True

    async with migrated_engine.begin() as conn:
        row = await conn.execute(
            sql_text("SELECT locales FROM tenants WHERE id = :tenant").bindparams(tenant=tenant_id)
        )
        assert list(row.scalar_one()) == ["en", "kok"]


async def test_the_change_is_on_the_chain(
    api_client: AsyncClient, migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A published change with no record of who made it is the thing ADR-0046 refuses.

    The tenant's locale list is what the public surface offers readers, so
    adding to it changes what a stranger sees. That is a decision, and a
    decision with no justification is an audit trail that answers "what" and
    refuses to answer "why".
    """
    await set_locales(api_client)

    actions = await admin_actions(migrated_engine, tenant_id)
    mine = [entry for entry in actions if entry.get("action") == ACTION]
    assert len(mine) == 1
    assert mine[0]["justification"] == REASON
    changes = mine[0]["changes"]
    assert isinstance(changes, dict)
    # `[before, after]`, so the second element is what the tenant now declares.
    assert changes["locales"][1] == ["en", "kok"]


async def test_a_re_run_that_changes_nothing_writes_nothing(
    api_client: AsyncClient, migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A PUT states a desired state, so a deployment script may run twice.

    ``changed`` is what distinguishes "I added Konkani" from "Konkani was
    already there" without the caller diffing anything — and a second identical
    call must not put a second entry on the chain, or a city's history fills
    with the record of a script being idempotent.
    """
    await set_locales(api_client)
    status, body = await set_locales(api_client)

    assert status == 200
    assert body["changed"] is False
    mine = [
        entry
        for entry in await admin_actions(migrated_engine, tenant_id)
        if entry.get("action") == ACTION
    ]
    assert len(mine) == 1


async def test_duplicates_are_dropped_and_order_is_kept(api_client: AsyncClient) -> None:
    """This list is rendered as a language switch somebody reads top to bottom.

    A repeated entry renders a repeated link and fails nothing — the kind of
    defect that survives for months.
    """
    status, body = await set_locales(api_client, locales=["en", "mr", "en", "kok", "mr"])
    assert status == 200
    assert body["locales"] == ["en", "mr", "kok"]


async def test_a_primary_locale_outside_the_list_is_refused(api_client: AsyncClient) -> None:
    """A tenant whose own working language is not one it offers is a misconfiguration."""
    status, _ = await set_locales(api_client, locales=["en", "mr"], primary_locale="kok")
    assert status == 422


async def test_an_empty_list_is_refused(api_client: AsyncClient) -> None:
    """Not "no localisation" — a surface with no language at all."""
    status, _ = await set_locales(api_client, locales=[])
    assert status == 422


async def test_the_justification_is_not_optional(api_client: AsyncClient) -> None:
    status, _ = await set_locales(api_client, justification="")
    assert status == 422


async def test_the_token_is_required(api_client: AsyncClient) -> None:
    response = await api_client.put(
        "/api/v1/control-plane/tenants/pilot-city/locales",
        json={"locales": ["en", "kok"], "justification": REASON},
    )
    assert response.status_code == 403


async def test_an_unknown_tenant_is_a_404_not_a_403(api_client: AsyncClient) -> None:
    """A control-plane token is a shared secret, not an identity.

    Answering 403 here would confirm a customer list to whoever holds the token,
    which is the discipline the rest of this package keeps.
    """
    status, _ = await set_locales(api_client, slug="no-such-city")
    assert status == 404


async def test_the_public_surface_publishes_the_result(
    api_client: AsyncClient, migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A2's other half: the reader's language switch is built from this list.

    Published on every public body, primary first. Without it the frontend keeps
    its own list — a second source of truth for a fact the platform holds — and
    a locale added here appears in no switch, which is what made Phase 18's
    clause unmeetable rather than unmet.
    """
    await api_client.put(
        "/api/v1/control-plane/tenants/pilot-city/publication",
        json={"enabled": True, "justification": REASON},
        headers=admin(),
    )
    await set_locales(api_client, locales=["en", "mr", "kok"], primary_locale="mr")

    response = await api_client.get("/api/v1/public/pilot-city/zones")
    assert response.status_code == 200
    body = response.json()
    # Primary first, because a switch is a list somebody reads top down.
    assert body["locales"][0] == "mr"
    assert set(body["locales"]) == {"en", "mr", "kok"}
