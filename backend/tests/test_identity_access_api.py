"""Administering access over HTTP: users, grants, delegation, the decision log.

``test_identity_api.py`` owns the gate — the negative matrix and the
impersonation clause. This file covers the rest of ``/api/v1/access``, and the
cases worth having are the ones where an administrator's *own* scope has to bind
them:

- a department head with ``grants:write`` **on their department** must not be
  able to grant anything tenant-wide, which is the privilege-escalation path
  that a naive implementation leaves wide open (check the permission, ignore the
  scope being granted);
- a contractor lead managing their own crew must not see, or create, a
  municipal operator;
- a delegation must be refused when it exceeds what the delegator holds, over
  HTTP and not only in the service.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from nemesis.config import Settings
from nemesis.control_plane import organisation
from nemesis.control_plane.schemas import ContractorSpec, DepartmentSpec
from nemesis.identity import bootstrap, roles, users
from nemesis.identity import permissions as perms
from nemesis.tenancy.context import tenant_scope
from tests.conftest import postgres_required

pytestmark = [postgres_required, pytest.mark.integration]

AUTH = "/api/v1/auth"
ACCESS = "/api/v1/access"
PASSWORD = "a-perfectly-adequate-passphrase"


@pytest.fixture
async def engine_for_api(app_settings: Settings) -> AsyncIterator[AsyncEngine]:
    from nemesis.tenancy.guard import install_tenant_guard

    engine = create_async_engine(app_settings.database_url, pool_pre_ping=True)
    install_tenant_guard(engine.sync_engine)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _session(engine: AsyncEngine) -> AsyncSession:
    return async_sessionmaker(engine, expire_on_commit=False)()


class Org:
    """The tenant an access-administration test works in."""

    def __init__(self) -> None:
        self.roles: dict[str, uuid.UUID] = {}
        self.users: dict[str, uuid.UUID] = {}
        self.departments: dict[str, uuid.UUID] = {}
        self.contractors: dict[str, uuid.UUID] = {}


async def build_org(engine: AsyncEngine, *, tenant_id: uuid.UUID) -> Org:
    """Two departments, one contractor, and the people who administer them."""
    org = Org()
    session = await _session(engine)
    try:
        with tenant_scope(tenant_id):
            await organisation.import_departments(
                session,
                tenant_id=tenant_id,
                specs=[
                    DepartmentSpec.model_validate({"code": "pw", "name": "Public Works"}),
                    DepartmentSpec.model_validate(
                        {"code": "south", "name": "South Division", "parent_code": "pw"}
                    ),
                    DepartmentSpec.model_validate({"code": "water", "name": "Water"}),
                ],
            )
            org.departments = {
                row.code: row.id
                for row in await organisation.list_departments(session, tenant_id=tenant_id)
            }
            contractor = await organisation.register_contractor(
                session,
                tenant_id=tenant_id,
                spec=ContractorSpec.model_validate(
                    {"name": "Deshmukh Infra", "registration_id": "REG-1"}
                ),
            )
            org.contractors["deshmukh"] = contractor.id

            installed = await bootstrap.install_roles(
                session, tenant_id=tenant_id, section="tenant"
            )
            org.roles = {role.code: role.id for role in installed}

            for label, role_code, scope_kind, scope_ref in (
                ("admin", "tenant_admin", perms.SCOPE_TENANT, None),
                (
                    "head",
                    "department_head",
                    perms.SCOPE_DEPARTMENT,
                    org.departments["pw"],
                ),
                (
                    "lead",
                    "contractor_lead",
                    perms.SCOPE_CONTRACTOR,
                    org.contractors["deshmukh"],
                ),
            ):
                user = await users.create_user(
                    session,
                    tenant_id=tenant_id,
                    display_name=label,
                    email=f"{label}@example.org",
                    password=PASSWORD,
                    kind=(users.KIND_CONTRACTOR if label == "lead" else users.KIND_OPERATOR),
                    contractor_id=(org.contractors["deshmukh"] if label == "lead" else None),
                )
                await roles.grant(
                    session,
                    tenant_id=tenant_id,
                    user_id=user.id,
                    role_id=org.roles[role_code],
                    scope_kind=scope_kind,
                    scope_ref=scope_ref,
                )
                org.users[label] = user.id

            # Somebody to be granted things, and somebody in another department.
            for label, department in (("recruit", None), ("water_officer", "water")):
                user = await users.create_user(
                    session,
                    tenant_id=tenant_id,
                    display_name=label,
                    email=f"{label}@example.org",
                    password=PASSWORD,
                    department_id=org.departments[department] if department else None,
                )
                org.users[label] = user.id
        await session.commit()
    finally:
        await session.close()
    return org


async def sign_in(client: AsyncClient, *, tenant_id: uuid.UUID, label: str) -> dict[str, str]:
    response = await client.post(
        f"{AUTH}/token",
        headers={"X-Tenant-ID": str(tenant_id)},
        json={"identifier": f"{label}@example.org", "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------


async def test_a_shipped_role_reports_how_many_people_hold_it(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """So an administrator reads "this changes access for N people" first."""
    await build_org(engine_for_api, tenant_id=tenant_id)
    admin = await sign_in(api_client, tenant_id=tenant_id, label="admin")

    listing = await api_client.get(f"{ACCESS}/roles", headers=admin)
    assert listing.status_code == 200, listing.text
    by_code = {role["code"]: role for role in listing.json()}
    assert by_code["tenant_admin"]["live_grants"] == 1
    assert by_code["department_head"]["live_grants"] == 1
    assert by_code["analyst"]["live_grants"] == 0
    assert by_code["tenant_admin"]["is_system"] is True


async def test_a_shipped_role_cannot_be_edited_over_http(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    org = await build_org(engine_for_api, tenant_id=tenant_id)
    admin = await sign_in(api_client, tenant_id=tenant_id, label="admin")

    response = await api_client.patch(
        f"{ACCESS}/roles/{org.roles['reviewer']}",
        headers=admin,
        json={"permissions": ["review:read"]},
    )
    assert response.status_code == 422
    assert "copy" in response.json()["detail"].lower()


async def test_a_composed_role_can_be_amended_and_deactivated(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    await build_org(engine_for_api, tenant_id=tenant_id)
    admin = await sign_in(api_client, tenant_id=tenant_id, label="admin")

    created = await api_client.post(
        f"{ACCESS}/roles",
        headers=admin,
        json={"code": "night", "name": "Night desk", "permissions": ["review:read"]},
    )
    assert created.status_code == 201, created.text
    role_id = created.json()["id"]

    amended = await api_client.patch(
        f"{ACCESS}/roles/{role_id}",
        headers=admin,
        json={
            "name": "Night desk (revised)",
            "permissions": ["review:read", "review:decide"],
            "description": "covers 22:00-06:00",
        },
    )
    assert amended.status_code == 200, amended.text
    assert amended.json()["permissions"] == ["review:decide", "review:read"]

    deactivated = await api_client.patch(
        f"{ACCESS}/roles/{role_id}", headers=admin, json={"is_active": False}
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False


async def test_an_unknown_role_is_a_404(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    await build_org(engine_for_api, tenant_id=tenant_id)
    admin = await sign_in(api_client, tenant_id=tenant_id, label="admin")
    response = await api_client.patch(
        f"{ACCESS}/roles/{uuid.uuid4()}", headers=admin, json={"name": "Ghost"}
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Grants, and the scope that binds the granter
# ---------------------------------------------------------------------------


async def test_a_department_head_cannot_grant_outside_their_department(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The escalation path a naive implementation leaves open.

    The head holds ``grants:delegate`` on Public Works. The resource a grant is
    authorized against is the **scope being granted**, not the user receiving
    it — so a grant over the whole tenant, or over another department, has to
    fail even though the caller legitimately holds the permission somewhere.
    """
    org = await build_org(engine_for_api, tenant_id=tenant_id)
    head = await sign_in(api_client, tenant_id=tenant_id, label="head")
    tomorrow = (datetime.now(tz=UTC) + timedelta(days=1)).isoformat()

    tenant_wide = await api_client.post(
        f"{ACCESS}/grants/delegate",
        headers=head,
        json={
            "to_user_id": str(org.users["recruit"]),
            "role_id": str(org.roles["reviewer"]),
            "scope_kind": perms.SCOPE_TENANT,
            "valid_until": tomorrow,
            "reason": "covering the whole city, apparently",
        },
    )
    assert tenant_wide.status_code == 403

    other_department = await api_client.post(
        f"{ACCESS}/grants/delegate",
        headers=head,
        json={
            "to_user_id": str(org.users["recruit"]),
            "role_id": str(org.roles["reviewer"]),
            "scope_kind": perms.SCOPE_DEPARTMENT,
            "scope_ref": str(org.departments["water"]),
            "valid_until": tomorrow,
            "reason": "covering a department I do not run",
        },
    )
    assert other_department.status_code == 403


async def test_a_department_head_may_delegate_inside_their_own_subtree(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The positive control for the test above: a *child* department is fine."""
    org = await build_org(engine_for_api, tenant_id=tenant_id)
    head = await sign_in(api_client, tenant_id=tenant_id, label="head")

    response = await api_client.post(
        f"{ACCESS}/grants/delegate",
        headers=head,
        json={
            "to_user_id": str(org.users["recruit"]),
            "role_id": str(org.roles["zone_officer"]),
            "scope_kind": perms.SCOPE_DEPARTMENT,
            "scope_ref": str(org.departments["south"]),
            "valid_until": (datetime.now(tz=UTC) + timedelta(days=7)).isoformat(),
            "reason": "covering South Division while Priya is on leave",
        },
    )
    # The head holds `department_head` on `pw`, which carries complaints:read,
    # review:read and more — a superset of `zone_officer` minus nothing it needs.
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["delegated_by_user_id"] == str(org.users["head"])
    assert body["valid_until"] is not None


async def test_a_delegation_of_a_permission_the_delegator_lacks_is_refused(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The delegation invariant, over HTTP: a subset, never more.

    The role used here is composed for the test rather than taken from the
    shipped library, and the reason is worth recording: every library role that
    exceeds a department head also carries a *tenant-global* permission, so the
    refusal would come from the scope check ("publication:write cannot be scoped
    to a department") rather than from the invariant under test. A role holding
    only ``users:write`` — scopable, and not held by a department head anywhere —
    isolates the clause this test is about.
    """
    org = await build_org(engine_for_api, tenant_id=tenant_id)
    admin = await sign_in(api_client, tenant_id=tenant_id, label="admin")
    head = await sign_in(api_client, tenant_id=tenant_id, label="head")

    composed = await api_client.post(
        f"{ACCESS}/roles",
        headers=admin,
        json={
            "code": "people_admin",
            "name": "People administrator",
            "permissions": ["users:read", "users:write"],
        },
    )
    assert composed.status_code == 201, composed.text

    response = await api_client.post(
        f"{ACCESS}/grants/delegate",
        headers=head,
        json={
            "to_user_id": str(org.users["recruit"]),
            "role_id": composed.json()["id"],
            "scope_kind": perms.SCOPE_DEPARTMENT,
            "scope_ref": str(org.departments["south"]),
            "valid_until": (datetime.now(tz=UTC) + timedelta(days=2)).isoformat(),
            "reason": "handing over rather more than I have",
        },
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "users:write" in detail
    assert "does not hold them there" in detail


async def test_a_delegation_without_a_reason_is_refused(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """An unexplained temporary grant is indistinguishable from a mistake."""
    org = await build_org(engine_for_api, tenant_id=tenant_id)
    head = await sign_in(api_client, tenant_id=tenant_id, label="head")

    response = await api_client.post(
        f"{ACCESS}/grants/delegate",
        headers=head,
        json={
            "to_user_id": str(org.users["recruit"]),
            "role_id": str(org.roles["zone_officer"]),
            "scope_kind": perms.SCOPE_DEPARTMENT,
            "scope_ref": str(org.departments["south"]),
            "valid_until": (datetime.now(tz=UTC) + timedelta(days=2)).isoformat(),
            "reason": "x",
        },
    )
    assert response.status_code == 422


async def test_a_grant_is_listed_and_can_be_revoked(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    org = await build_org(engine_for_api, tenant_id=tenant_id)
    admin = await sign_in(api_client, tenant_id=tenant_id, label="admin")

    granted = await api_client.post(
        f"{ACCESS}/grants",
        headers=admin,
        json={
            "user_id": str(org.users["recruit"]),
            "role_id": str(org.roles["reviewer"]),
            "scope_kind": perms.SCOPE_TENANT,
            "reason": "joined the trust & safety desk",
        },
    )
    assert granted.status_code == 201, granted.text
    grant_id = granted.json()["id"]

    # Duplicate: a 409 rather than a silent second row, because the likely
    # intent was to extend the first one.
    again = await api_client.post(
        f"{ACCESS}/grants",
        headers=admin,
        json={
            "user_id": str(org.users["recruit"]),
            "role_id": str(org.roles["reviewer"]),
            "scope_kind": perms.SCOPE_TENANT,
        },
    )
    assert again.status_code == 409

    listing = await api_client.get(
        f"{ACCESS}/grants", headers=admin, params={"user_id": str(org.users["recruit"])}
    )
    assert listing.status_code == 200
    assert [entry["id"] for entry in listing.json()] == [grant_id]

    revoked = await api_client.delete(
        f"{ACCESS}/grants/{grant_id}", headers=admin, params={"reason": "moved desks"}
    )
    assert revoked.status_code == 204

    after = await api_client.get(
        f"{ACCESS}/grants", headers=admin, params={"user_id": str(org.users["recruit"])}
    )
    assert after.json() == []

    history = await api_client.get(
        f"{ACCESS}/grants",
        headers=admin,
        params={"user_id": str(org.users["recruit"]), "include_revoked": True},
    )
    assert len(history.json()) == 1


async def test_revoking_an_unknown_grant_is_a_404(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    await build_org(engine_for_api, tenant_id=tenant_id)
    admin = await sign_in(api_client, tenant_id=tenant_id, label="admin")
    response = await api_client.delete(
        f"{ACCESS}/grants/{uuid.uuid4()}", headers=admin, params={"reason": "nothing"}
    )
    assert response.status_code == 404


async def test_a_grant_naming_a_scope_that_does_not_exist_is_a_404(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The referential integrity ``scope_ref`` gives up by not being an FK."""
    org = await build_org(engine_for_api, tenant_id=tenant_id)
    admin = await sign_in(api_client, tenant_id=tenant_id, label="admin")

    response = await api_client.post(
        f"{ACCESS}/grants",
        headers=admin,
        json={
            "user_id": str(org.users["recruit"]),
            "role_id": str(org.roles["reviewer"]),
            "scope_kind": perms.SCOPE_DEPARTMENT,
            "scope_ref": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 404


async def test_a_tenant_global_permission_cannot_be_granted_on_a_department(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A meaningless narrowing is refused rather than silently widened."""
    org = await build_org(engine_for_api, tenant_id=tenant_id)
    admin = await sign_in(api_client, tenant_id=tenant_id, label="admin")

    created = await api_client.post(
        f"{ACCESS}/roles",
        headers=admin,
        json={
            "code": "publisher",
            "name": "Publisher",
            # publication:write is declared with `resource_kind=None`: the
            # subject of it *is* the tenant, so there is nothing narrower.
            "permissions": ["publication:write"],
        },
    )
    assert created.status_code == 201, created.text

    response = await api_client.post(
        f"{ACCESS}/grants",
        headers=admin,
        json={
            "user_id": str(org.users["recruit"]),
            "role_id": created.json()["id"],
            "scope_kind": perms.SCOPE_DEPARTMENT,
            "scope_ref": str(org.departments["pw"]),
        },
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Users, and the contractor boundary
# ---------------------------------------------------------------------------


async def test_an_administrator_lists_every_user_and_a_contractor_lead_lists_their_crew(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Two callers, two answers, one endpoint — from ``Visibility``."""
    org = await build_org(engine_for_api, tenant_id=tenant_id)
    admin = await sign_in(api_client, tenant_id=tenant_id, label="admin")
    lead = await sign_in(api_client, tenant_id=tenant_id, label="lead")

    everybody = await api_client.get(f"{ACCESS}/users", headers=admin)
    assert everybody.status_code == 200, everybody.text
    assert len(everybody.json()) == len(org.users)

    # The contractor lead holds users:read nowhere, so the listing is refused
    # outright rather than returning an empty page — "you cannot ask" and "the
    # answer is none" are different facts.
    crew = await api_client.get(f"{ACCESS}/users", headers=lead)
    assert crew.status_code == 403
    assert crew.json()["required_permission"] == "users:read"


async def test_a_contractor_lead_creates_a_sub_user_and_not_an_operator(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """``contractor_users:manage`` is scoped to one organisation."""
    org = await build_org(engine_for_api, tenant_id=tenant_id)
    lead = await sign_in(api_client, tenant_id=tenant_id, label="lead")

    crew_member = await api_client.post(
        f"{ACCESS}/users",
        headers=lead,
        json={
            "display_name": "Crew member",
            "email": "crew@deshmukh.example",
            "kind": users.KIND_CONTRACTOR,
            "contractor_id": str(org.contractors["deshmukh"]),
        },
    )
    assert crew_member.status_code == 201, crew_member.text
    assert crew_member.json()["kind"] == users.KIND_CONTRACTOR

    # An operator for the municipality is a different act entirely.
    operator = await api_client.post(
        f"{ACCESS}/users",
        headers=lead,
        json={"display_name": "Sneaky operator", "email": "sneaky@example.org"},
    )
    assert operator.status_code == 403
    assert operator.json()["required_permission"] == "users:write"


async def test_a_user_created_with_a_password_must_change_it(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    await build_org(engine_for_api, tenant_id=tenant_id)
    admin = await sign_in(api_client, tenant_id=tenant_id, label="admin")

    created = await api_client.post(
        f"{ACCESS}/users",
        headers=admin,
        json={
            "display_name": "New starter",
            "email": "starter@example.org",
            "password": PASSWORD,
            "role_label": "junior engineer",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["has_password"] is True
    assert created.json()["role_label"] == "junior engineer"

    issued = await api_client.post(
        f"{AUTH}/token",
        headers={"X-Tenant-ID": str(tenant_id)},
        json={"identifier": "starter@example.org", "password": PASSWORD},
    )
    assert issued.status_code == 200
    assert issued.json()["must_change_password"] is True


async def test_a_duplicate_email_is_a_conflict(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    await build_org(engine_for_api, tenant_id=tenant_id)
    admin = await sign_in(api_client, tenant_id=tenant_id, label="admin")
    response = await api_client.post(
        f"{ACCESS}/users",
        headers=admin,
        json={"display_name": "Clash", "email": "admin@example.org"},
    )
    assert response.status_code == 409


async def test_an_administrative_reset_signs_the_user_out_everywhere(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The usual reason for a reset is that somebody else has the old one."""
    org = await build_org(engine_for_api, tenant_id=tenant_id)
    admin = await sign_in(api_client, tenant_id=tenant_id, label="admin")
    theirs = await sign_in(api_client, tenant_id=tenant_id, label="recruit")
    assert (await api_client.get(f"{AUTH}/session", headers=theirs)).status_code == 200

    reset = await api_client.post(
        f"{ACCESS}/users/{org.users['recruit']}/password",
        headers=admin,
        json={"password": PASSWORD + "-reset"},
    )
    assert reset.status_code == 204
    assert (await api_client.get(f"{AUTH}/session", headers=theirs)).status_code == 401

    reissued = await api_client.post(
        f"{AUTH}/token",
        headers={"X-Tenant-ID": str(tenant_id)},
        json={"identifier": "recruit@example.org", "password": PASSWORD + "-reset"},
    )
    assert reissued.status_code == 200
    assert reissued.json()["must_change_password"] is True


async def test_a_short_password_is_refused_by_the_tenants_policy(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    org = await build_org(engine_for_api, tenant_id=tenant_id)
    admin = await sign_in(api_client, tenant_id=tenant_id, label="admin")
    response = await api_client.post(
        f"{ACCESS}/users/{org.users['recruit']}/password",
        headers=admin,
        json={"password": "short"},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# The caller's own session
# ---------------------------------------------------------------------------


async def test_a_user_changes_their_own_password_and_keeps_this_session(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Signing somebody out of the browser they just used is user-hostile; every
    *other* session dies, because the change is the response to a suspicion."""
    await build_org(engine_for_api, tenant_id=tenant_id)
    first = await sign_in(api_client, tenant_id=tenant_id, label="recruit")
    second = await sign_in(api_client, tenant_id=tenant_id, label="recruit")

    changed = await api_client.post(
        f"{AUTH}/password",
        headers=second,
        json={"current_password": PASSWORD, "new_password": PASSWORD + "-mine"},
    )
    assert changed.status_code == 204, changed.text

    assert (await api_client.get(f"{AUTH}/session", headers=second)).status_code == 200
    assert (await api_client.get(f"{AUTH}/session", headers=first)).status_code == 401


async def test_changing_a_password_requires_the_current_one(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A stolen token must not be usable to lock the owner out of their account."""
    await build_org(engine_for_api, tenant_id=tenant_id)
    headers = await sign_in(api_client, tenant_id=tenant_id, label="recruit")
    response = await api_client.post(
        f"{AUTH}/password",
        headers=headers,
        json={"current_password": "not-it", "new_password": PASSWORD + "-mine"},
    )
    assert response.status_code == 401


async def test_a_user_lists_their_own_sessions_and_signs_out_everywhere(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    await build_org(engine_for_api, tenant_id=tenant_id)
    first = await sign_in(api_client, tenant_id=tenant_id, label="recruit")
    second = await sign_in(api_client, tenant_id=tenant_id, label="recruit")

    listing = await api_client.get(f"{AUTH}/sessions", headers=second)
    assert listing.status_code == 200, listing.text
    entries = listing.json()
    assert len(entries) == 2
    assert sum(1 for entry in entries if entry["is_current"]) == 1
    assert all(entry["method"] == "password" for entry in entries)

    everywhere = await api_client.post(f"{AUTH}/logout-all", headers=second)
    assert everywhere.status_code == 204
    assert (await api_client.get(f"{AUTH}/session", headers=first)).status_code == 401
    assert (await api_client.get(f"{AUTH}/session", headers=second)).status_code == 401


async def test_the_sign_in_providers_listing_is_public_and_says_little(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A sign-in page renders from it before anybody is signed in, and it must
    not become an enumeration surface for a tenant's SSO configuration."""
    await build_org(engine_for_api, tenant_id=tenant_id)
    admin = await sign_in(api_client, tenant_id=tenant_id, label="admin")

    registered = await api_client.post(
        f"{ACCESS}/providers",
        headers=admin,
        json={
            "slug": "sso",
            "display_name": "Council SSO",
            "issuer": "https://sso.example.org",
            "client_id": "nemesis-pilot",
            "client_secret_setting": "NEMESIS_TEST_OIDC_SECRET",
            "allowed_email_domains": ["example.org"],
        },
    )
    assert registered.status_code == 201, registered.text

    listing = await api_client.get(f"{AUTH}/providers", headers={"X-Tenant-ID": str(tenant_id)})
    assert listing.status_code == 200
    assert listing.json() == [{"slug": "sso", "display_name": "Council SSO"}]


async def test_a_plaintext_issuer_is_refused_over_http(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    await build_org(engine_for_api, tenant_id=tenant_id)
    admin = await sign_in(api_client, tenant_id=tenant_id, label="admin")
    response = await api_client.post(
        f"{ACCESS}/providers",
        headers=admin,
        json={
            "slug": "insecure",
            "display_name": "Insecure",
            "issuer": "http://sso.example.org",
            "client_id": "nemesis-pilot",
            "client_secret_setting": "NEMESIS_TEST_OIDC_SECRET",
        },
    )
    assert response.status_code == 422


async def test_starting_a_provider_flow_needs_a_configured_redirect(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The deployment's allow-list is empty by default, which is default-deny
    rather than a localhost entry that follows the stack into production."""
    await build_org(engine_for_api, tenant_id=tenant_id)
    admin = await sign_in(api_client, tenant_id=tenant_id, label="admin")
    await api_client.post(
        f"{ACCESS}/providers",
        headers=admin,
        json={
            "slug": "sso",
            "display_name": "Council SSO",
            "issuer": "https://sso.example.org",
            "client_id": "nemesis-pilot",
            "client_secret_setting": "NEMESIS_TEST_OIDC_SECRET",
        },
    )

    response = await api_client.post(
        f"{AUTH}/oidc/start",
        headers={"X-Tenant-ID": str(tenant_id)},
        json={
            "provider": "sso",
            "redirect_uri": "https://console.example.org/auth/callback",
        },
    )
    assert response.status_code == 422


async def test_an_oidc_callback_with_an_unknown_state_is_401(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    await build_org(engine_for_api, tenant_id=tenant_id)
    response = await api_client.post(
        f"{AUTH}/oidc/callback",
        headers={"X-Tenant-ID": str(tenant_id)},
        json={"state": "never-issued", "code": "whatever"},
    )
    assert response.status_code == 401


async def test_the_sign_in_route_requires_a_tenant(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """400 and not 401: the request is unanswerable, not unauthorised.

    One email address can exist in two tenants, and searching every tenant for
    it would be a cross-tenant read on an unauthenticated endpoint.
    """
    await build_org(engine_for_api, tenant_id=tenant_id)
    missing = await api_client.post(
        f"{AUTH}/token", json={"identifier": "admin@example.org", "password": PASSWORD}
    )
    malformed = await api_client.post(
        f"{AUTH}/token",
        headers={"X-Tenant-ID": "not-a-uuid"},
        json={"identifier": "admin@example.org", "password": PASSWORD},
    )
    assert missing.status_code == 400
    assert malformed.status_code == 400


# ---------------------------------------------------------------------------
# The decision log
# ---------------------------------------------------------------------------


async def test_the_decision_log_is_filterable_and_scoped_to_the_tenant(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    org = await build_org(engine_for_api, tenant_id=tenant_id)
    admin = await sign_in(api_client, tenant_id=tenant_id, label="admin")
    head = await sign_in(api_client, tenant_id=tenant_id, label="head")

    # One denial, from a caller who holds roles:write nowhere.
    refused = await api_client.post(
        f"{ACCESS}/roles",
        headers=head,
        json={"code": "nope", "name": "Nope", "permissions": ["review:read"]},
    )
    assert refused.status_code == 403

    everything = await api_client.get(f"{ACCESS}/decisions", headers=admin)
    assert everything.status_code == 200, everything.text

    just_theirs = await api_client.get(
        f"{ACCESS}/decisions",
        headers=admin,
        params={"user_id": str(org.users["head"]), "allowed": False, "limit": 10},
    )
    assert just_theirs.status_code == 200
    entries = just_theirs.json()
    assert entries
    assert all(entry["user_id"] == str(org.users["head"]) for entry in entries)
    assert all(entry["allowed"] is False for entry in entries)
    assert any(entry["permission"] == "roles:write" for entry in entries)
    # Every row carries the route, including the ones a handler body recorded
    # rather than a dependency — the gap that showed up in the live gate's log
    # and is why `decisions.set_route` exists. As a *template*, never with an id
    # substituted in: a resolved path makes this column unaggregatable and
    # duplicates what `resource_id` already holds.
    assert entries
    assert all(entry["route"] for entry in entries)
    assert all("{" in entry["route"] or entry["route"].count("/") <= 4 for entry in entries)


async def test_an_officer_cannot_read_the_decision_log(
    api_client: AsyncClient, engine_for_api: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    await build_org(engine_for_api, tenant_id=tenant_id)
    head = await sign_in(api_client, tenant_id=tenant_id, label="head")
    response = await api_client.get(f"{ACCESS}/decisions", headers=head)
    assert response.status_code == 403
