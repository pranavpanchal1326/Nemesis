"""ADR-0046 — publishing is an act somebody takes, and the log says who.

``api/public_deps.py`` calls the publication flag *"a disclosure decision no
engineer is entitled to make on their behalf"*. Until this route existed there
was no way for anyone else to make it either: the column had one writer,
``sandbox.py``, and every other path went through ``psql``.

These tests are about the properties that make the route a control rather than a
setter — the justification is not optional, the change lands on the chain, the
floor cannot be undercut, retraction goes through the same door, and a re-run
that changes nothing writes nothing.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncEngine

from nemesis.api.v1.control_plane import CONTROL_PLANE_TOKEN_HEADER
from nemesis.control_plane.publication import ACTION
from tests.conftest import postgres_required

pytestmark = [postgres_required, pytest.mark.asyncio, pytest.mark.integration]

DEV_TOKEN = "dev-only-insecure-control-plane-token-change-me"
REASON = "Council resolution 2026/114 — transparency portal go-live."


def admin() -> dict[str, str]:
    return {CONTROL_PLANE_TOKEN_HEADER: DEV_TOKEN}


async def publish(
    api_client: AsyncClient, slug: str = "pilot-city", **body: object
) -> tuple[int, dict[str, object]]:
    response = await api_client.put(
        f"/api/v1/control-plane/tenants/{slug}/publication",
        json={"enabled": True, "justification": REASON} | body,
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


async def flags(engine: AsyncEngine, tenant_id: uuid.UUID) -> tuple[bool, int]:
    async with engine.begin() as conn:
        row = await conn.execute(
            sql_text(
                "SELECT public_api_enabled, public_api_min_aggregate "
                "FROM tenants WHERE id = :tenant"
            ).bindparams(tenant=tenant_id)
        )
        enabled, threshold = row.one()
        return bool(enabled), int(threshold)


async def test_publishing_makes_the_public_surface_answer(
    api_client: AsyncClient, migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The whole point, end to end: 404 before, 200 after, over HTTP only.

    Asserted through the public endpoint rather than by reading the column,
    because the column is an implementation detail and the 404 is the contract.
    """
    before = await api_client.get("/api/v1/public/pilot-city/zones")
    assert before.status_code == 404

    status, body = await publish(api_client)
    assert status == 200
    assert body["enabled"] is True
    assert body["changed"] is True

    after = await api_client.get("/api/v1/public/pilot-city/zones")
    assert after.status_code == 200
    assert str(tenant_id) == body["tenant_id"]


async def test_the_decision_lands_on_the_chain_with_its_reason(
    api_client: AsyncClient, migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """``admin_action``, with the before and after, and the justification.

    The event is the reason this route exists rather than a column setter. A
    disclosure that happened is recoverable from a database dump; a disclosure
    *decision* is only recoverable if somebody wrote down why.
    """
    await publish(api_client)

    events = await admin_actions(migrated_engine, tenant_id)
    assert len(events) == 1
    payload = events[0]
    assert payload["action"] == ACTION
    assert payload["justification"] == REASON
    assert payload["target_entity_type"] == "tenant"
    assert payload["target_entity_id"] == str(tenant_id)
    changes: dict[str, object] = payload["changes"]  # type: ignore[assignment]
    assert changes["public_api_enabled"] == [False, True]


async def test_a_call_that_changes_nothing_writes_nothing(
    api_client: AsyncClient, migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Idempotent, and idempotent without polluting the audit trail.

    A deployment script re-running must not fail, and it must not leave the
    chain full of re-runs among which the actual decisions are hard to find. The
    response says ``changed: false`` so nothing is hidden by the omission.
    """
    await publish(api_client)
    status, body = await publish(api_client)

    assert status == 200
    assert body["enabled"] is True
    assert body["changed"] is False
    assert len(await admin_actions(migrated_engine, tenant_id)) == 1


async def test_retraction_goes_through_the_same_door(
    api_client: AsyncClient, migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A one-way door on a disclosure is a control that has been used once."""
    await publish(api_client)
    status, body = await publish(
        api_client, enabled=False, justification="Pilot concluded; data withdrawn."
    )

    assert status == 200
    assert body["enabled"] is False
    assert body["changed"] is True
    assert (await api_client.get("/api/v1/public/pilot-city/zones")).status_code == 404
    assert len(await admin_actions(migrated_engine, tenant_id)) == 2


async def test_retraction_states_the_cache_window(api_client: AsyncClient) -> None:
    """Retraction is not erasure, and the response does not imply it is.

    Anything already served under ``Cache-Control: public, max-age=300`` is still
    out there. A caller switching publication off should learn the window rather
    than assume there isn't one.
    """
    _, body = await publish(api_client, enabled=False, justification="Withdrawn pending review.")
    assert isinstance(body["cache_seconds"], int)
    assert body["cache_seconds"] > 0


async def test_a_floor_below_the_deployments_is_refused_not_clamped(
    api_client: AsyncClient, migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """422, naming the floor — and the tenant is left exactly as it was.

    Read-time clamping protects rows that already exist, where failing would
    take a live page down over a historical mistake. This is a person asking for
    something wrong right now, and the clamp would answer them by silently doing
    something else (ADR-0046).
    """
    response = await api_client.put(
        "/api/v1/control-plane/tenants/pilot-city/publication",
        json={"enabled": True, "justification": REASON, "min_aggregate": 2},
        headers=admin(),
    )
    assert response.status_code == 422
    assert "floor" in response.text.lower()
    assert await flags(migrated_engine, tenant_id) == (False, 5)


async def test_the_floor_can_be_raised(
    api_client: AsyncClient, migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    status, body = await publish(api_client, min_aggregate=25)
    assert status == 200
    assert body["min_aggregate"] == 25
    assert await flags(migrated_engine, tenant_id) == (True, 25)


async def test_omitting_the_floor_leaves_it_alone(
    api_client: AsyncClient, migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Silently resetting a threshold somebody chose is a regression, not a default."""
    await publish(api_client, min_aggregate=25)
    await publish(api_client, enabled=False, justification="Paused for the monsoon audit.")
    assert await flags(migrated_engine, tenant_id) == (False, 25)


async def test_a_justification_is_required(api_client: AsyncClient) -> None:
    """The field ``AdminActionV1`` already refuses to be without, on the surface too."""
    response = await api_client.put(
        "/api/v1/control-plane/tenants/pilot-city/publication",
        json={"enabled": True},
        headers=admin(),
    )
    assert response.status_code == 422


async def test_the_token_is_required(api_client: AsyncClient) -> None:
    response = await api_client.put(
        "/api/v1/control-plane/tenants/pilot-city/publication",
        json={"enabled": True, "justification": REASON},
    )
    assert response.status_code == 403


async def test_an_unknown_tenant_is_not_found_not_forbidden(api_client: AsyncClient) -> None:
    """A control-plane token is a shared secret, not an identity.

    So holding one does not entitle its holder to enumerate the deployment's
    customer list one slug at a time — the same discipline ``api.deps`` applies
    to tenant resolution.
    """
    response = await api_client.put(
        "/api/v1/control-plane/tenants/no-such-city/publication",
        json={"enabled": True, "justification": REASON},
        headers=admin(),
    )
    assert response.status_code == 404
    assert "forbidden" not in response.text.lower()


async def test_publication_does_not_reach_across_tenants(
    api_client: AsyncClient, migrated_engine: AsyncEngine, other_tenant_id: uuid.UUID
) -> None:
    """Publishing one city says nothing about the one next to it."""
    await publish(api_client)
    assert await flags(migrated_engine, other_tenant_id) == (False, 5)
    assert (await api_client.get("/api/v1/public/campus/zones")).status_code == 404
