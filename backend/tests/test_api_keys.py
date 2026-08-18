"""API keys: issue, verify, scope, quota, revoke, and account.

The property worth being deliberate about is that the *secret* exists in exactly
one place for exactly one moment — the mint response. Several tests here assert
absence, which is unusual and is the point: "the API does not return this" is the
whole security model, and a test that only checked the happy path would pass
against an implementation that helpfully echoed the key back on every list.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from nemesis.integrations import keys
from nemesis.integrations.errors import NotFoundError, ValidationError
from tests.conftest import postgres_required

pytestmark = [postgres_required, pytest.mark.asyncio]

TOKEN = {"X-Control-Plane-Token": "dev-only-insecure-control-plane-token-change-me"}


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------


async def test_a_minted_key_verifies(migrated_engine: AsyncEngine, tenant_id: uuid.UUID) -> None:
    async with _session(migrated_engine) as session:
        minted = await keys.mint(
            session, tenant_id=tenant_id, name="Data desk", scopes=["public:read"]
        )
        await session.commit()

        resolved = await keys.resolve(session, presented=minted.secret)

    assert resolved is not None
    assert resolved.tenant_id == tenant_id
    assert resolved.permits("public:read")
    assert not resolved.permits("webhooks:manage")


async def test_the_key_carries_a_scanner_visible_marker(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Making our own credentials greppable is a control that costs four characters.

    A key that looks like an arbitrary base32 blob is one that gets committed to
    a customer's public repository and never flagged by any secret scanner.
    """
    async with _session(migrated_engine) as session:
        minted = await keys.mint(session, tenant_id=tenant_id, name="Desk", scopes=["public:read"])
    assert minted.secret.startswith(f"{keys.KEY_MARKER}_")
    assert minted.prefix in minted.secret


async def test_the_secret_is_not_stored(migrated_engine: AsyncEngine, tenant_id: uuid.UUID) -> None:
    """The digest is stored; the plaintext is not recoverable from the database."""
    async with _session(migrated_engine) as session:
        minted = await keys.mint(session, tenant_id=tenant_id, name="Desk", scopes=["public:read"])
        await session.commit()

    async with migrated_engine.begin() as conn:
        stored = (
            await conn.execute(
                sql_text(
                    "SELECT key_digest, key_prefix FROM api_keys WHERE tenant_id = :t"
                ).bindparams(t=tenant_id)
            )
        ).one()

    assert stored.key_digest == keys.digest(minted.secret)
    assert minted.secret not in stored.key_digest
    assert stored.key_prefix == minted.prefix


async def test_a_wrong_key_resolves_to_nothing(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    async with _session(migrated_engine) as session:
        await keys.mint(session, tenant_id=tenant_id, name="Desk", scopes=["public:read"])
        await session.commit()
        assert await keys.resolve(session, presented="nem_deadbeef_not-a-real-key") is None


async def test_a_revoked_key_stops_working_but_the_row_remains(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Revocation is recorded, never implemented by deleting the row.

    A key that authenticated a request last March has to stay resolvable, or the
    usage rollup and the audit trail both point at nothing.
    """
    async with _session(migrated_engine) as session:
        minted = await keys.mint(session, tenant_id=tenant_id, name="Desk", scopes=["public:read"])
        await session.commit()

        await keys.revoke(
            session, tenant_id=tenant_id, key_id=minted.id, reason="found in a public gist"
        )
        await session.commit()

        assert await keys.resolve(session, presented=minted.secret) is None
        remaining = await keys.list_keys(session, tenant_id=tenant_id)

    assert len(remaining) == 1
    assert remaining[0].revoked_reason == "found in a public gist"


async def test_revoking_twice_is_a_no_op(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A retrying incident-response script should not have to special-case success."""
    async with _session(migrated_engine) as session:
        minted = await keys.mint(session, tenant_id=tenant_id, name="Desk", scopes=["public:read"])
        await session.commit()
        await keys.revoke(session, tenant_id=tenant_id, key_id=minted.id, reason="one")
        await session.commit()
        await keys.revoke(session, tenant_id=tenant_id, key_id=minted.id, reason="two")
        await session.commit()


async def test_revoking_an_unknown_key_is_not_found(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    async with _session(migrated_engine) as session:
        with pytest.raises(NotFoundError):
            await keys.revoke(session, tenant_id=tenant_id, key_id=uuid.uuid4(), reason="x")


async def test_an_expired_key_stops_working(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    async with _session(migrated_engine) as session:
        minted = await keys.mint(
            session,
            tenant_id=tenant_id,
            name="Desk",
            scopes=["public:read"],
            expires_at=datetime.now(tz=UTC) + timedelta(seconds=1),
        )
        await session.commit()
        await session.execute(
            sql_text(
                "UPDATE api_keys SET expires_at = now() - interval '1 hour' WHERE id = :k"
            ).bindparams(k=minted.id)
        )
        await session.commit()
        assert await keys.resolve(session, presented=minted.secret) is None


async def test_a_key_with_no_scopes_is_refused(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """It would present to its holder as a broken API, not as a restricted one."""
    async with _session(migrated_engine) as session:
        with pytest.raises(ValidationError, match="no scopes"):
            await keys.mint(session, tenant_id=tenant_id, name="Desk", scopes=[])


async def test_an_unknown_scope_is_refused(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    async with _session(migrated_engine) as session:
        with pytest.raises(ValidationError, match="unknown scope"):
            await keys.mint(
                session, tenant_id=tenant_id, name="Desk", scopes=["public:read", "admin:all"]
            )


async def test_usage_is_a_rollup_not_a_request_log(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Three requests to one endpoint on one day is one row, not three."""
    async with _session(migrated_engine) as session:
        minted = await keys.mint(session, tenant_id=tenant_id, name="Desk", scopes=["public:read"])
        await session.commit()

        for outcome in ("ok", "ok", "error", "throttled"):
            await keys.record_usage(
                session,
                tenant_id=tenant_id,
                key_id=minted.id,
                endpoint="/api/v1/export/{dataset}",
                outcome=outcome,
            )
        await session.commit()

        today = datetime.now(tz=UTC).date()
        report = await keys.usage_report(session, tenant_id=tenant_id, since=today, until=today)

    assert len(report) == 1
    _key_id, prefix, _day, endpoint, requests, errors, throttled = report[0]
    assert prefix == minted.prefix
    assert endpoint == "/api/v1/export/{dataset}"
    assert (requests, errors, throttled) == (4, 1, 1)


async def test_one_tenants_key_does_not_appear_in_anothers_listing(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID, other_tenant_id: uuid.UUID
) -> None:
    async with _session(migrated_engine) as session:
        await keys.mint(session, tenant_id=tenant_id, name="Mine", scopes=["public:read"])
        await keys.mint(session, tenant_id=other_tenant_id, name="Theirs", scopes=["public:read"])
        await session.commit()

        mine = await keys.list_keys(session, tenant_id=tenant_id)
        theirs = await keys.list_keys(session, tenant_id=other_tenant_id)

    assert [k.name for k in mine] == ["Mine"]
    assert [k.name for k in theirs] == ["Theirs"]


# ---------------------------------------------------------------------------
# Over HTTP
# ---------------------------------------------------------------------------


async def test_minting_requires_the_control_plane_token(
    api_client: AsyncClient, tenant_id: uuid.UUID
) -> None:
    """A key that could mint keys would be a privilege-escalation primitive.

    A leaked ``public:read`` credential could issue itself ``webhooks:manage``
    and subscribe to every event the tenant produces.
    """
    response = await api_client.post(
        "/api/v1/integrations/keys",
        headers={"X-Tenant-ID": str(tenant_id)},
        json={"name": "Desk", "scopes": ["public:read"]},
    )
    assert response.status_code == 403


async def test_the_secret_is_returned_once_and_never_listed(
    api_client: AsyncClient, tenant_id: uuid.UUID
) -> None:
    """The whole security model, as an assertion about absence."""
    headers = TOKEN | {"X-Tenant-ID": str(tenant_id)}
    created = await api_client.post(
        "/api/v1/integrations/keys",
        headers=headers,
        json={"name": "Times data desk", "scopes": ["public:read", "export:read"]},
    )
    assert created.status_code == 201, created.text
    secret = created.json()["secret"]
    assert secret.startswith("nem_")
    assert "cannot be retrieved" in created.json()["warning"]

    listing = await api_client.get(
        "/api/v1/integrations/keys", headers={"X-Tenant-ID": str(tenant_id)}
    )
    assert listing.status_code == 200
    assert secret not in listing.text
    assert listing.json()[0]["key_prefix"] == created.json()["key_prefix"]
    # The digest is not published either — it is the stored credential.
    assert "key_digest" not in listing.text


async def test_minting_writes_an_admin_action_naming_the_prefix_not_the_secret(
    api_client: AsyncClient, migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The compensating control for the token not being identity (ADR-0020).

    A compromise is investigated by reading the chain, because the token cannot
    say who used it — so the trail records *what* was issued, and deliberately
    not the credential itself.
    """
    created = await api_client.post(
        "/api/v1/integrations/keys",
        headers=TOKEN | {"X-Tenant-ID": str(tenant_id)},
        json={"name": "Desk", "scopes": ["public:read"]},
    )
    secret = created.json()["secret"]

    async with migrated_engine.begin() as conn:
        rows = (
            await conn.execute(
                sql_text(
                    "SELECT event_type, payload FROM events WHERE tenant_id = :t "
                    "AND entity_type = 'admin_action'"
                ).bindparams(t=tenant_id)
            )
        ).all()

    assert len(rows) == 1
    payload = rows[0].payload
    assert payload["action"] == "api_key_issued"
    assert payload["changes"]["key_prefix"] == created.json()["key_prefix"]
    assert secret not in str(payload)


async def test_a_scope_the_key_lacks_is_refused(
    api_client: AsyncClient, tenant_id: uuid.UUID
) -> None:
    created = await api_client.post(
        "/api/v1/integrations/keys",
        headers=TOKEN | {"X-Tenant-ID": str(tenant_id)},
        json={"name": "Read only", "scopes": ["public:read"]},
    )
    secret = created.json()["secret"]

    response = await api_client.get("/api/v1/export/complaints", headers={"X-API-Key": secret})
    assert response.status_code == 403
    assert response.json()["required_scope"] == "export:read"


async def test_an_unauthenticated_export_is_rejected(api_client: AsyncClient) -> None:
    """An unauthenticated full-table scan is a denial-of-service primitive."""
    assert (await api_client.get("/api/v1/export/complaints")).status_code == 401


async def test_an_invalid_key_says_only_that(api_client: AsyncClient) -> None:
    """Unknown, revoked, and expired get the same message.

    Telling a caller their key is *expired* rather than *unknown* confirms it
    was real, which is the fact worth having to somebody who found it in a log.
    """
    response = await api_client.get(
        "/api/v1/export/complaints", headers={"X-API-Key": "nem_dead_beef"}
    )
    assert response.status_code == 401
    body = response.json()["detail"].lower()
    assert "expired" not in body and "revoked" not in body


def _session(engine: AsyncEngine) -> object:
    return async_sessionmaker(engine, expire_on_commit=False)()
