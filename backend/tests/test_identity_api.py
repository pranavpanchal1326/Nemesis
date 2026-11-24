"""The gate clause, over HTTP: zone officer A cannot reach zone B's rows.

**This file is the Phase 13 exit gate.** The clause reads: *"a negative
authorisation test per role pair: department head A provably cannot read
department B's rows through any endpoint, including error messages and
pagination counts"*.

Three deliberate choices in how that is tested:

**The scope is a zone, not a department.** ``complaints.department_id`` is
written by routing, which is Phase 12 — nothing writes it in this build, so a
department-scoped grant can prove nothing and correctly denies everything
(``identity.resources`` states this consequence). ``complaints.ward`` *is*
written, so a zone-scoped grant is the one that can be tested positively **and**
negatively today. The matrix is the same matrix; the attribute it turns on is
the one that exists.

**Both halves are asserted for every pair.** A test that only checks the denial
passes just as happily against a build that denies everybody, which is not
authorization — it is an outage. So each pair asserts that A sees A's row and
does not see B's.

**The count is asserted, not just the page.** ``total`` is computed in SQL
before any row is serialised, so a filter applied to the response would leave it
describing another officer's work. That is the specific leak the clause names,
and it is why ``trust.review.list_items`` takes a condition rather than the
router filtering afterwards.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from nemesis.config import Settings
from nemesis.control_plane import organisation
from nemesis.control_plane.schemas import ZoneSpec
from nemesis.db.models.complaint import Complaint
from nemesis.db.models.identity import AuthorizationDecision, Role
from nemesis.domain.constants import SYSTEM_TENANT_ID
from nemesis.identity import bootstrap, roles, users
from nemesis.identity import permissions as perms
from nemesis.tenancy.context import tenant_scope
from nemesis.trust.review import ReviewReason, queue
from tests.conftest import postgres_required
from tests.test_trust_review import make_complaint

pytestmark = [postgres_required, pytest.mark.integration]

AUTH = "/api/v1/auth"
ACCESS = "/api/v1/access"
REVIEW = "/api/v1/review"
PASSWORD = "a-perfectly-adequate-passphrase"

#: Two wards, and the complaint that sits in each. The whole matrix turns on
#: these being different places with different officers.
NORTH = "ward-north"
SOUTH = "ward-south"


class Fixture:
    """What a scenario built, so a test can refer to it by name."""

    def __init__(self) -> None:
        self.items: dict[str, uuid.UUID] = {}
        self.complaints: dict[str, uuid.UUID] = {}
        self.tokens: dict[str, str] = {}
        self.user_ids: dict[str, uuid.UUID] = {}
        self.role_ids: dict[str, uuid.UUID] = {}
        self.zone_ids: dict[str, uuid.UUID] = {}


@pytest.fixture
async def engine_for_api(app_settings: Settings) -> AsyncIterator[AsyncEngine]:
    """An engine on the app's database, with the tenancy guard installed."""
    from nemesis.tenancy.guard import install_tenant_guard

    engine = create_async_engine(app_settings.database_url, pool_pre_ping=True)
    install_tenant_guard(engine.sync_engine)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _session(engine: AsyncEngine) -> AsyncSession:
    return async_sessionmaker(engine, expire_on_commit=False)()


async def build_queue_scenario(engine: AsyncEngine, *, tenant_id: uuid.UUID) -> Fixture:
    """Two wards, one flagged complaint in each, two zone officers.

    Also a tenant-wide reviewer, because a matrix with only narrow grants cannot
    tell "scoping works" from "nothing works".
    """
    built = Fixture()
    session = await _session(engine)
    try:
        with tenant_scope(tenant_id):
            await organisation.import_zones(
                session,
                tenant_id=tenant_id,
                specs=[
                    ZoneSpec.model_validate({"code": NORTH, "name": "North Ward"}),
                    ZoneSpec.model_validate({"code": SOUTH, "name": "South Ward"}),
                ],
            )
            zones = await organisation.list_zones(session, tenant_id=tenant_id)
            built.zone_ids = {zone.code: zone.id for zone in zones}

            for ward in (NORTH, SOUTH):
                complaint_id = await make_complaint(session, tenant_id=tenant_id)
                await session.execute(
                    update(Complaint)
                    .where(Complaint.tenant_id == tenant_id, Complaint.id == complaint_id)
                    .values(ward=ward)
                )
                queued = await queue(
                    session,
                    tenant_id=tenant_id,
                    complaint_id=complaint_id,
                    reason=ReviewReason.LOW_TRUST,
                    evidence={"ward": ward},
                    trust_score=0.2,
                )
                built.complaints[ward] = complaint_id
                built.items[ward] = queued.review_item_id

            await bootstrap.install_roles(session, tenant_id=tenant_id, section="tenant")
            all_roles = {
                role.code: role for role in await roles.list_roles(session, tenant_id=tenant_id)
            }
            built.role_ids = {code: role.id for code, role in all_roles.items()}

            for ward, label in ((NORTH, "north_officer"), (SOUTH, "south_officer")):
                user = await users.create_user(
                    session,
                    tenant_id=tenant_id,
                    display_name=label,
                    email=f"{label}@example.org",
                    password=PASSWORD,
                )
                await roles.grant(
                    session,
                    tenant_id=tenant_id,
                    user_id=user.id,
                    role_id=all_roles["reviewer"].id,
                    scope_kind=perms.SCOPE_ZONE,
                    scope_ref=built.zone_ids[ward],
                )
                built.user_ids[label] = user.id

            admin = await users.create_user(
                session,
                tenant_id=tenant_id,
                display_name="admin",
                email="admin@example.org",
                password=PASSWORD,
            )
            await roles.grant(
                session,
                tenant_id=tenant_id,
                user_id=admin.id,
                role_id=all_roles["tenant_admin"].id,
            )
            await roles.grant(
                session,
                tenant_id=tenant_id,
                user_id=admin.id,
                role_id=all_roles["reviewer"].id,
            )
            built.user_ids["admin"] = admin.id

            nobody = await users.create_user(
                session,
                tenant_id=tenant_id,
                display_name="nobody",
                email="nobody@example.org",
                password=PASSWORD,
            )
            built.user_ids["nobody"] = nobody.id
        await session.commit()
    finally:
        await session.close()
    return built


async def sign_in(client: AsyncClient, *, tenant_id: uuid.UUID, email: str) -> dict[str, str]:
    """Sign in and return the Authorization header for that user."""
    response = await client.post(
        f"{AUTH}/token",
        headers={"X-Tenant-ID": str(tenant_id)},
        json={"identifier": email, "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


# ---------------------------------------------------------------------------
# The sign-in flow
# ---------------------------------------------------------------------------


async def test_sign_in_returns_a_session_that_describes_itself(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    built = await build_queue_scenario(engine_for_api, tenant_id=tenant_id)
    headers = await sign_in(api_client, tenant_id=tenant_id, email="admin@example.org")

    response = await api_client.get(f"{AUTH}/session", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["user_id"] == str(built.user_ids["admin"])
    assert body["tenant_id"] == str(tenant_id)
    assert "review:decide" in body["permissions"]
    assert body["impersonated_by"] is None
    # Grants are returned so a console can explain *why* a tab is visible,
    # rather than only that it is.
    assert {grant["role_code"] for grant in body["grants"]} == {
        "tenant_admin",
        "reviewer",
    }


async def test_no_token_is_401_and_a_bad_token_is_401(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    await build_queue_scenario(engine_for_api, tenant_id=tenant_id)

    missing = await api_client.get(f"{AUTH}/session")
    forged = await api_client.get(
        f"{AUTH}/session", headers={"Authorization": "Bearer not.a.token"}
    )
    assert missing.status_code == 401
    assert forged.status_code == 401
    # RFC 9110: a 401 without this header leaves a client unable to tell a
    # missing credential from a rejected one.
    assert missing.headers.get("www-authenticate") == "Bearer"


async def test_a_wrong_password_is_401_and_says_nothing_useful(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    await build_queue_scenario(engine_for_api, tenant_id=tenant_id)
    response = await api_client.post(
        f"{AUTH}/token",
        headers={"X-Tenant-ID": str(tenant_id)},
        json={"identifier": "admin@example.org", "password": "wrong"},
    )
    assert response.status_code == 401
    body = response.json()
    # No "user exists", no "password incorrect", no reason code.
    assert "password" not in body["detail"].lower()


async def test_refresh_rotates_and_the_old_token_is_dead(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    await build_queue_scenario(engine_for_api, tenant_id=tenant_id)
    first = await api_client.post(
        f"{AUTH}/token",
        headers={"X-Tenant-ID": str(tenant_id)},
        json={"identifier": "admin@example.org", "password": PASSWORD},
    )
    refresh = first.json()["refresh_token"]

    second = await api_client.post(f"{AUTH}/refresh", json={"refresh_token": refresh})
    assert second.status_code == 200, second.text
    assert second.json()["refresh_token"] != refresh

    replay = await api_client.post(f"{AUTH}/refresh", json={"refresh_token": refresh})
    assert replay.status_code == 401


async def test_logout_revokes_the_session_immediately(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Revocation does not wait for the access token to expire — ADR-0066."""
    await build_queue_scenario(engine_for_api, tenant_id=tenant_id)
    headers = await sign_in(api_client, tenant_id=tenant_id, email="admin@example.org")

    assert (await api_client.get(f"{AUTH}/session", headers=headers)).status_code == 200
    assert (await api_client.post(f"{AUTH}/logout", headers=headers)).status_code == 204
    assert (await api_client.get(f"{AUTH}/session", headers=headers)).status_code == 401


# ---------------------------------------------------------------------------
# The negative-authorisation matrix — the gate clause
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("officer", "own_ward", "other_ward"),
    [
        ("north_officer@example.org", NORTH, SOUTH),
        ("south_officer@example.org", SOUTH, NORTH),
    ],
)
async def test_a_zone_officer_sees_their_own_queue_and_not_the_others(
    api_client: AsyncClient,
    engine_for_api: AsyncEngine,
    tenant_id: uuid.UUID,
    officer: str,
    own_ward: str,
    other_ward: str,
) -> None:
    """Both halves, for both officers. See the module docstring."""
    built = await build_queue_scenario(engine_for_api, tenant_id=tenant_id)
    headers = await sign_in(api_client, tenant_id=tenant_id, email=officer)

    page = await api_client.get(f"{REVIEW}/queue", headers=headers)
    assert page.status_code == 200, page.text
    body = page.json()

    returned = {item["id"] for item in body["items"]}
    assert returned == {str(built.items[own_ward])}
    # The clause about pagination counts: the total must agree with the page.
    assert body["total"] == 1


@pytest.mark.parametrize(
    ("officer", "other_ward"),
    [
        ("north_officer@example.org", SOUTH),
        ("south_officer@example.org", NORTH),
    ],
)
async def test_a_zone_officer_cannot_read_another_wards_item_by_id(
    api_client: AsyncClient,
    engine_for_api: AsyncEngine,
    tenant_id: uuid.UUID,
    officer: str,
    other_ward: str,
) -> None:
    built = await build_queue_scenario(engine_for_api, tenant_id=tenant_id)
    headers = await sign_in(api_client, tenant_id=tenant_id, email=officer)

    response = await api_client.get(f"{REVIEW}/queue/{built.items[other_ward]}", headers=headers)
    assert response.status_code == 403
    body = response.json()
    # The permission is named; the resource never is. A 403 that varied with the
    # target would be the enumeration oracle the clause forbids.
    assert body["required_permission"] == "review:read"
    assert str(built.items[other_ward]) not in response.text
    assert other_ward not in response.text


@pytest.mark.parametrize(
    ("officer", "other_ward"),
    [
        ("north_officer@example.org", SOUTH),
        ("south_officer@example.org", NORTH),
    ],
)
async def test_a_zone_officer_cannot_decide_another_wards_item(
    api_client: AsyncClient,
    engine_for_api: AsyncEngine,
    tenant_id: uuid.UUID,
    officer: str,
    other_ward: str,
) -> None:
    built = await build_queue_scenario(engine_for_api, tenant_id=tenant_id)
    headers = await sign_in(api_client, tenant_id=tenant_id, email=officer)

    response = await api_client.post(
        f"{REVIEW}/queue/{built.items[other_ward]}/decision",
        headers=headers,
        json={"decision": "approve", "rationale": "looks fine to me"},
    )
    assert response.status_code == 403


async def test_a_user_with_no_grants_is_refused_the_queue_entirely(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Default-deny, end to end. A new account can do nothing until granted."""
    await build_queue_scenario(engine_for_api, tenant_id=tenant_id)
    headers = await sign_in(api_client, tenant_id=tenant_id, email="nobody@example.org")

    response = await api_client.get(f"{REVIEW}/queue", headers=headers)
    assert response.status_code == 403
    assert response.json()["required_permission"] == "review:read"


async def test_a_tenant_wide_reviewer_sees_both_wards(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The positive control. Without it, "scoping works" is indistinguishable
    from "the queue is broken for everybody"."""
    built = await build_queue_scenario(engine_for_api, tenant_id=tenant_id)
    headers = await sign_in(api_client, tenant_id=tenant_id, email="admin@example.org")

    page = await api_client.get(f"{REVIEW}/queue", headers=headers)
    assert page.status_code == 200, page.text
    body = page.json()
    assert {item["id"] for item in body["items"]} == {
        str(built.items[NORTH]),
        str(built.items[SOUTH]),
    }
    assert body["total"] == 2


async def test_a_decision_is_attributed_to_the_authenticated_operator(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Phase 8 recorded a label the caller supplied; Phase 13 records a person."""
    built = await build_queue_scenario(engine_for_api, tenant_id=tenant_id)
    headers = await sign_in(api_client, tenant_id=tenant_id, email="north_officer@example.org")

    response = await api_client.post(
        f"{REVIEW}/queue/{built.items[NORTH]}/decision",
        headers=headers,
        json={
            "decision": "approve",
            "rationale": "photograph matches the reported location",
            "decided_by_label": "covering the north desk",
        },
    )
    assert response.status_code == 201, response.text
    label = response.json()["decided_by_label"]
    # The derived identity first, the caller's note appended — never replacing.
    assert label.startswith("local:")
    assert "covering the north desk" in label


async def test_a_session_from_another_tenant_cannot_read_this_one(
    api_client: AsyncClient,
    engine_for_api: AsyncEngine,
    tenant_id: uuid.UUID,
    other_tenant_id: uuid.UUID,
) -> None:
    """The tenant boundary, now that a token carries a tenant claim.

    The header is not read at all for an authenticated request, so passing
    somebody else's tenant id alongside a valid token changes nothing — which is
    the property ``api.deps.require_tenant`` promised Phase 13 would bring.
    """
    built = await build_queue_scenario(engine_for_api, tenant_id=tenant_id)

    session = await _session(engine_for_api)
    try:
        with tenant_scope(other_tenant_id):
            await bootstrap.install_roles(session, tenant_id=other_tenant_id, section="tenant")
            their_roles = {
                role.code: role
                for role in await roles.list_roles(session, tenant_id=other_tenant_id)
            }
            outsider = await users.create_user(
                session,
                tenant_id=other_tenant_id,
                display_name="outsider",
                email="outsider@example.org",
                password=PASSWORD,
            )
            await roles.grant(
                session,
                tenant_id=other_tenant_id,
                user_id=outsider.id,
                role_id=their_roles["tenant_admin"].id,
            )
            await roles.grant(
                session,
                tenant_id=other_tenant_id,
                user_id=outsider.id,
                role_id=their_roles["reviewer"].id,
            )
        await session.commit()
    finally:
        await session.close()

    headers = await sign_in(api_client, tenant_id=other_tenant_id, email="outsider@example.org")
    headers["X-Tenant-ID"] = str(tenant_id)

    page = await api_client.get(f"{REVIEW}/queue", headers=headers)
    assert page.status_code == 200, page.text
    # Their own (empty) queue, not ours — the header was ignored.
    assert page.json()["total"] == 0

    direct = await api_client.get(f"{REVIEW}/queue/{built.items[NORTH]}", headers=headers)
    # 404 and not 403: for a row in another tenant the honest answer is that it
    # does not exist, because distinguishing the two is the oracle.
    assert direct.status_code == 404


# ---------------------------------------------------------------------------
# Administering access over HTTP
# ---------------------------------------------------------------------------


async def test_an_administrator_composes_a_role_and_grants_it(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    built = await build_queue_scenario(engine_for_api, tenant_id=tenant_id)
    headers = await sign_in(api_client, tenant_id=tenant_id, email="admin@example.org")

    created = await api_client.post(
        f"{ACCESS}/roles",
        headers=headers,
        json={
            "code": "night_desk",
            "name": "Night desk",
            "permissions": ["review:read"],
        },
    )
    assert created.status_code == 201, created.text
    role_id = created.json()["id"]

    granted = await api_client.post(
        f"{ACCESS}/grants",
        headers=headers,
        json={
            "user_id": str(built.user_ids["nobody"]),
            "role_id": role_id,
            "scope_kind": perms.SCOPE_ZONE,
            "scope_ref": str(built.zone_ids[NORTH]),
        },
    )
    assert granted.status_code == 201, granted.text

    # And the grant takes effect on the next request the holder makes.
    theirs = await sign_in(api_client, tenant_id=tenant_id, email="nobody@example.org")
    page = await api_client.get(f"{REVIEW}/queue", headers=theirs)
    assert page.status_code == 200
    assert page.json()["total"] == 1


async def test_a_role_cannot_be_composed_from_an_undeclared_permission(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    await build_queue_scenario(engine_for_api, tenant_id=tenant_id)
    headers = await sign_in(api_client, tenant_id=tenant_id, email="admin@example.org")

    response = await api_client.post(
        f"{ACCESS}/roles",
        headers=headers,
        json={"code": "typo", "name": "Typo", "permissions": ["review:decidr"]},
    )
    assert response.status_code == 422
    assert "review:decidr" in response.json()["detail"]


async def test_a_tenant_role_cannot_be_composed_from_a_platform_permission(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A tenant administrator granting themselves the support path, refused."""
    await build_queue_scenario(engine_for_api, tenant_id=tenant_id)
    headers = await sign_in(api_client, tenant_id=tenant_id, email="admin@example.org")

    response = await api_client.post(
        f"{ACCESS}/roles",
        headers=headers,
        json={
            "code": "sneaky",
            "name": "Sneaky",
            "permissions": ["review:read", "impersonation:start"],
        },
    )
    assert response.status_code == 422


async def test_the_permission_catalogue_hides_platform_permissions_from_a_tenant(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    await build_queue_scenario(engine_for_api, tenant_id=tenant_id)
    headers = await sign_in(api_client, tenant_id=tenant_id, email="admin@example.org")

    response = await api_client.get(f"{ACCESS}/permissions", headers=headers)
    assert response.status_code == 200
    names = {entry["name"] for entry in response.json()}
    assert "review:decide" in names
    assert not (names & perms.PLATFORM_PERMISSIONS)


async def test_an_officer_cannot_administer_roles(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    await build_queue_scenario(engine_for_api, tenant_id=tenant_id)
    headers = await sign_in(api_client, tenant_id=tenant_id, email="north_officer@example.org")
    response = await api_client.post(
        f"{ACCESS}/roles",
        headers=headers,
        json={"code": "self_promotion", "name": "More", "permissions": ["review:read"]},
    )
    assert response.status_code == 403


async def test_an_administrator_cannot_deactivate_themselves(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The lockout nobody can undo, refused before it happens."""
    built = await build_queue_scenario(engine_for_api, tenant_id=tenant_id)
    headers = await sign_in(api_client, tenant_id=tenant_id, email="admin@example.org")

    response = await api_client.delete(f"{ACCESS}/users/{built.user_ids['admin']}", headers=headers)
    assert response.status_code == 422


async def test_deactivating_a_user_ends_their_sessions(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    built = await build_queue_scenario(engine_for_api, tenant_id=tenant_id)
    admin = await sign_in(api_client, tenant_id=tenant_id, email="admin@example.org")
    officer = await sign_in(api_client, tenant_id=tenant_id, email="north_officer@example.org")
    assert (await api_client.get(f"{AUTH}/session", headers=officer)).status_code == 200

    removed = await api_client.delete(
        f"{ACCESS}/users/{built.user_ids['north_officer']}", headers=admin
    )
    assert removed.status_code == 204
    assert (await api_client.get(f"{AUTH}/session", headers=officer)).status_code == 401


# ---------------------------------------------------------------------------
# The decision log
# ---------------------------------------------------------------------------


async def test_a_denial_is_recorded_in_the_tenants_decision_log(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Default-deny with a log is auditable; without one it is only quiet."""
    built = await build_queue_scenario(engine_for_api, tenant_id=tenant_id)
    officer = await sign_in(api_client, tenant_id=tenant_id, email="north_officer@example.org")

    refused = await api_client.get(f"{REVIEW}/queue/{built.items[SOUTH]}", headers=officer)
    assert refused.status_code == 403

    admin = await sign_in(api_client, tenant_id=tenant_id, email="admin@example.org")
    log = await api_client.get(f"{ACCESS}/decisions", headers=admin, params={"allowed": False})
    assert log.status_code == 200, log.text
    denials = log.json()
    assert any(
        entry["permission"] == "review:read"
        and entry["reason_code"] == "out_of_scope"
        and entry["user_id"] == str(built.user_ids["north_officer"])
        for entry in denials
    )


async def test_an_unaudited_allow_is_not_written_to_the_log(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """``review:read`` allows are the hot path; ``review:decide`` allows are not.

    The registry's ``audited`` flag is what separates them, and the cost it is
    avoiding is one row per list request per officer per refresh.
    """
    built = await build_queue_scenario(engine_for_api, tenant_id=tenant_id)
    officer = await sign_in(api_client, tenant_id=tenant_id, email="north_officer@example.org")

    assert (
        await api_client.get(f"{REVIEW}/queue/{built.items[NORTH]}", headers=officer)
    ).status_code == 200
    assert (
        await api_client.post(
            f"{REVIEW}/queue/{built.items[NORTH]}/decision",
            headers=officer,
            json={"decision": "approve", "rationale": "the photograph matches"},
        )
    ).status_code == 201

    session = await _session(engine_for_api)
    try:
        with tenant_scope(tenant_id):
            rows = list(
                (
                    await session.execute(
                        select(
                            AuthorizationDecision.permission,
                            AuthorizationDecision.allowed,
                        ).where(AuthorizationDecision.tenant_id == tenant_id)
                    )
                ).tuples()
            )
    finally:
        await session.close()

    assert ("review:decide", True) in rows
    assert ("review:read", True) not in rows


# ---------------------------------------------------------------------------
# Impersonation, over HTTP
# ---------------------------------------------------------------------------


async def test_support_impersonates_and_the_tenant_can_see_it(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The gate clause: every impersonation session is visible to the tenant.

    Asserted from the *customer's* side — an administrator of the impersonated
    tenant lists it through their own endpoint, with the justification attached.
    """
    built = await build_queue_scenario(engine_for_api, tenant_id=tenant_id)

    session = await _session(engine_for_api)
    try:
        with tenant_scope(SYSTEM_TENANT_ID):
            await bootstrap.install_roles(session, tenant_id=SYSTEM_TENANT_ID, section="platform")
            operator = await users.create_user(
                session,
                tenant_id=SYSTEM_TENANT_ID,
                display_name="Support",
                email="support@nemesis.example",
                password=PASSWORD,
            )
            support_role = (
                await session.execute(
                    select(Role).where(
                        Role.tenant_id == SYSTEM_TENANT_ID,
                        Role.code == "platform_support",
                    )
                )
            ).scalar_one()
            await roles.grant(
                session,
                tenant_id=SYSTEM_TENANT_ID,
                user_id=operator.id,
                role_id=support_role.id,
            )
        await session.commit()
    finally:
        await session.close()

    support = await sign_in(api_client, tenant_id=SYSTEM_TENANT_ID, email="support@nemesis.example")
    started = await api_client.post(
        f"{ACCESS}/impersonation",
        headers=support,
        json={
            "tenant_id": str(tenant_id),
            "user_id": str(built.user_ids["north_officer"]),
            "justification": "ticket NEM-5001: officer reports the north queue renders empty",
            "minutes": 15,
        },
    )
    assert started.status_code == 201, started.text
    body = started.json()
    acting = {"Authorization": f"Bearer {body['access_token']}"}

    # Acting as the officer: their scope, not the operator's.
    who = await api_client.get(f"{AUTH}/session", headers=acting)
    assert who.status_code == 200, who.text
    assert who.json()["impersonated_by"] == operator.subject
    assert who.json()["user_id"] == str(built.user_ids["north_officer"])

    page = await api_client.get(f"{REVIEW}/queue", headers=acting)
    assert page.status_code == 200
    assert page.json()["total"] == 1

    # A read-only support session cannot decide, even though the officer could.
    refused = await api_client.post(
        f"{REVIEW}/queue/{built.items[NORTH]}/decision",
        headers=acting,
        json={"decision": "approve", "rationale": "support should not be able to do this"},
    )
    assert refused.status_code == 403
    assert refused.json()["required_permission"] == "review:decide"

    # And the customer sees the session, with the justification.
    admin = await sign_in(api_client, tenant_id=tenant_id, email="admin@example.org")
    visible = await api_client.get(f"{ACCESS}/impersonation", headers=admin)
    assert visible.status_code == 200, visible.text
    sessions_seen = visible.json()
    assert len(sessions_seen) == 1
    assert sessions_seen[0]["operator_subject"] == operator.subject
    assert "NEM-5001" in sessions_seen[0]["justification"]
    assert sessions_seen[0]["allows_writes"] is False

    # The customer can also end it.
    ended = await api_client.delete(
        f"{ACCESS}/impersonation/{sessions_seen[0]['id']}", headers=admin
    )
    assert ended.status_code == 204
    assert (await api_client.get(f"{AUTH}/session", headers=acting)).status_code == 401


async def test_a_tenant_administrator_cannot_start_an_impersonation(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """``impersonation:start`` is platform-only, so no tenant role can hold it."""
    built = await build_queue_scenario(engine_for_api, tenant_id=tenant_id)
    admin = await sign_in(api_client, tenant_id=tenant_id, email="admin@example.org")

    response = await api_client.post(
        f"{ACCESS}/impersonation",
        headers=admin,
        json={
            "tenant_id": str(tenant_id),
            "user_id": str(built.user_ids["north_officer"]),
            "justification": "ticket LOCAL-1: I would like to see what my officer sees",
            "minutes": 15,
        },
    )
    assert response.status_code == 403
    assert response.json()["required_permission"] == "impersonation:start"


# ---------------------------------------------------------------------------
# The citizen surfaces are untouched
# ---------------------------------------------------------------------------


async def test_a_citizen_can_still_read_their_receipt_without_a_session(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Phase 13 does not put a role in front of a citizen reading their own report.

    The complaint id is the capability (ADR-0016, ADR-0043): a UUIDv4 handed to
    the submitter. Requiring a grant here would mean a citizen needs an account
    with the council to see whether their pothole was fixed, which is the
    opposite of §3.1's argument for why the product exists.
    """
    built = await build_queue_scenario(engine_for_api, tenant_id=tenant_id)
    response = await api_client.get(
        f"/api/v1/complaints/{built.complaints[NORTH]}",
        headers={"X-Tenant-ID": str(tenant_id)},
    )
    assert response.status_code == 200, response.text
