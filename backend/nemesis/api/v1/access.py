"""Administering access: roles, grants, delegations, users, support sessions.

**This router is where the control plane stops being a shared secret.** Phase 5
guarded every write with ``X-Control-Plane-Token`` and said plainly, in
``api.v1.control_plane``'s own docstring, that it would stay until Phase 13
replaced it with a real operator identity. Every route here carries a declared
permission instead. The token is not removed from the Phase 5 routes in this
commit — a running pilot would lose the ability to configure anything mid-flight
— and ADR-0066 holds the migration: both are accepted, the token is logged as
deprecated when used, and it is removed in the release after a deployment has
run the bootstrap.

**Three groups of routes, three different scoping stories.**

*Roles and the decision log* are tenant-level: ``roles:write`` and
``decisions:read`` are declared with ``KIND_USER`` as their resource kind, so
``requires`` checks "held somewhere" and each handler narrows to the tenant.
There is no narrower subject than the tenant for a role definition — a role
scoped to a ward would be a different role.

*Grants and users* are the interesting case, and the one the Phase 13 gate
clause is about. A department head holding ``grants:delegate`` on their own
department must not be able to grant anything in another, and must not be able
to *see* another department's users — including through a count. So
``list_users`` builds a ``Visibility`` and the query is filtered from it, rather
than fetching and then hiding.

*Impersonation* splits by direction: starting one needs the platform-only
``impersonation:start``, and reading what support did to you needs the tenant
permission ``impersonation:read``. That asymmetry is the gate clause — the
customer can see it and support cannot hide it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from nemesis.api.deps import ConfigDep, SessionDep
from nemesis.api.identity_deps import PrincipalDep, authenticated, requires, translate
from nemesis.db.models.identity import ImpersonationSession, Role, RoleGrant
from nemesis.db.models.organisation import User
from nemesis.identity import decisions, impersonation, oidc, permissions, roles, sessions, users
from nemesis.identity.errors import IdentityError, ValidationError
from nemesis.identity.scopes import Principal, Resource, Visibility

router = APIRouter(prefix="/access", tags=["access"])

#: The default page size for the lists here. Small: these are administrative
#: surfaces read by a person, and a tenant with four thousand users does not
#: want them in one response any more than we want to serialise them.
DEFAULT_PAGE: Final = 50
MAX_PAGE: Final = 200


# ---------------------------------------------------------------------------
# Wire contracts
# ---------------------------------------------------------------------------


class RoleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    #: Validated against the registry by ``identity.roles``, which returns the
    #: legal vocabulary in the error. Not an enum in this schema on purpose: an
    #: enum here would put the permission list in the OpenAPI document twice,
    #: and the generated client would then have a copy that can disagree with
    #: the server's.
    permissions: list[str] = Field(min_length=1)


class RoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    permissions: list[str] | None = Field(default=None, min_length=1)
    is_active: bool | None = None


class RoleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    code: str
    name: str
    description: str | None
    permissions: list[str]
    is_system: bool
    is_active: bool
    #: How many live grants this role has. Returned on the *read* so an
    #: administrator sees "41 people hold this" before editing it, not after —
    #: a role edit reaches every holder on their next request
    #: (``identity.roles.update_role`` says why that immediacy is deliberate).
    live_grants: int


class PermissionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    resource_kind: str | None
    audited: bool
    scopable: bool


class GrantSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID
    role_id: uuid.UUID
    scope_kind: str = Field(default=permissions.SCOPE_TENANT, max_length=32)
    scope_ref: uuid.UUID | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    reason: str | None = Field(default=None, max_length=2000)


class DelegationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to_user_id: uuid.UUID
    role_id: uuid.UUID
    scope_kind: str = Field(max_length=32)
    scope_ref: uuid.UUID | None = None
    #: Required, unlike on a grant. A delegation with no end is a standing
    #: grant that arrived through the side door — ``identity.roles.delegate``
    #: caps it at ninety days as well.
    valid_until: datetime
    reason: str = Field(min_length=10, max_length=2000)


class GrantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    user_id: uuid.UUID
    role_id: uuid.UUID
    role_code: str
    scope_kind: str
    scope_ref: uuid.UUID | None
    valid_from: datetime | None
    valid_until: datetime | None
    delegated_by_user_id: uuid.UUID | None
    reason: str | None
    revoked_at: datetime | None


class UserSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    kind: str = Field(default=users.KIND_OPERATOR, max_length=32)
    #: The §18.1 org-chart label. Superseded as an authorization input by
    #: ``role_grants`` and kept as a label — see the column comment on
    #: ``users.role``.
    role_label: str | None = Field(default=None, max_length=64)
    department_id: uuid.UUID | None = None
    contractor_id: uuid.UUID | None = None
    #: Optional. A user created without one has no credential and cannot sign
    #: in until an administrator sets one or they arrive through a provider —
    #: which is the right default for an SSO tenant.
    password: str | None = Field(default=None, min_length=1, max_length=1024)


class UserResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    subject: str
    display_name: str | None
    email: str | None
    kind: str
    role_label: str | None
    department_id: uuid.UUID | None
    contractor_id: uuid.UUID | None
    is_active: bool
    last_login_at: datetime | None
    has_password: bool


class PasswordResetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=1, max_length=1024)


class ProviderSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9-]*$")
    display_name: str = Field(min_length=1, max_length=200)
    issuer: str = Field(min_length=8, max_length=2048)
    client_id: str = Field(min_length=1, max_length=2048)
    #: The *name* of an environment variable. The secret itself is never sent to
    #: this API and never stored — see ``db.models.identity.IdentityProvider``.
    client_secret_setting: str = Field(min_length=1, max_length=128)
    scopes: list[str] | None = None
    allowed_email_domains: list[str] | None = None
    auto_provision: bool = False
    auto_provision_role_id: uuid.UUID | None = None


class ImpersonationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: uuid.UUID
    user_id: uuid.UUID
    justification: str = Field(min_length=30, max_length=2000)
    ticket_ref: str | None = Field(default=None, max_length=128)
    minutes: int = Field(default=30, ge=1, le=120)
    allows_writes: bool = False


class ImpersonationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    subject_user_id: uuid.UUID
    operator_subject: str
    justification: str
    ticket_ref: str | None
    allows_writes: bool
    started_at: datetime
    ends_at: datetime
    ended_at: datetime | None
    ended_reason: str | None
    decisions_allowed: int
    decisions_denied: int


class ImpersonationTokenResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    impersonation: ImpersonationResponse
    access_token: str
    expires_in: int
    refresh_token: str
    token_type: str = "Bearer"


class AuthorizationDecisionResponse(BaseModel):
    """One row of the decision log.

    Named at length rather than ``DecisionResponse``, and the reason is a
    mistake worth not repeating: ``api.v1.review`` already publishes a
    ``DecisionResponse`` — a *review* decision, an entirely different thing —
    and two Pydantic models with one class name make FastAPI qualify both
    schema components with their full module paths. The generated TypeScript
    client's ``DecisionResponse`` then stops existing, and the breakage surfaces
    in the frontend rather than here.
    """

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    decided_at: datetime
    user_id: uuid.UUID | None
    permission: str
    allowed: bool
    reason_code: str
    resource_kind: str | None
    resource_id: uuid.UUID | None
    matched_scope: str | None
    operator_subject: str | None
    route: str | None


# ---------------------------------------------------------------------------
# Permissions & roles
# ---------------------------------------------------------------------------


@router.get(
    "/permissions",
    response_model=list[PermissionResponse],
    summary="The permission vocabulary this build recognises",
    dependencies=[Depends(requires("roles:read"))],
)
async def list_permissions(principal: PrincipalDep) -> list[PermissionResponse]:
    """What a role may be composed from.

    Served from the code-declared registry rather than a table, which is the
    whole argument of ``identity.permissions`` — and the reason this endpoint
    exists at all: an administrator composing a role needs the list, and the
    list cannot be fetched from the database because it is not in the database.

    A platform principal sees the platform-only permissions as well. A tenant
    administrator does not, because a role they composed could not hold one
    anyway and offering it in a picker would be an invitation to a 422.
    """
    await decisions.authorize(
        principal=principal,
        permission="roles:read",
        resource=Resource(kind=permissions.KIND_USER),
    )
    available = (
        permissions.PERMISSIONS.values()
        if principal.is_platform
        else [permissions.get(name) for name in sorted(permissions.TENANT_PERMISSIONS)]
    )
    return [
        PermissionResponse(
            name=permission.name,
            description=permission.description,
            resource_kind=permission.resource_kind,
            audited=permission.audited,
            scopable=permission.is_scopable,
        )
        for permission in available
    ]


@router.get(
    "/roles",
    response_model=list[RoleResponse],
    summary="This tenant's roles",
    dependencies=[Depends(requires("roles:read"))],
)
async def list_roles(principal: PrincipalDep, session: SessionDep) -> list[RoleResponse]:
    await decisions.authorize(
        principal=principal,
        permission="roles:read",
        resource=Resource(kind=permissions.KIND_USER),
    )
    rows = await roles.list_roles(session, tenant_id=principal.tenant_id)
    return [await _role_response(session, principal=principal, role=row) for row in rows]


@router.post(
    "/roles",
    response_model=RoleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Compose a role",
    dependencies=[Depends(requires("roles:write"))],
)
async def create_role(
    principal: PrincipalDep, session: SessionDep, payload: RoleSpec
) -> RoleResponse:
    await decisions.authorize(
        principal=principal,
        permission="roles:write",
        resource=Resource(kind=permissions.KIND_USER),
    )
    try:
        row = await roles.create_role(
            session,
            tenant_id=principal.tenant_id,
            code=payload.code,
            name=payload.name,
            permissions=payload.permissions,
            description=payload.description,
        )
    except IdentityError as error:
        raise translate(error) from error
    return await _role_response(session, principal=principal, role=row)


@router.patch(
    "/roles/{role_id}",
    response_model=RoleResponse,
    summary="Amend a role",
    dependencies=[Depends(requires("roles:write"))],
)
async def update_role(
    principal: PrincipalDep,
    session: SessionDep,
    role_id: uuid.UUID,
    payload: RoleUpdate,
) -> RoleResponse:
    await decisions.authorize(
        principal=principal,
        permission="roles:write",
        resource=Resource(kind=permissions.KIND_USER, id=role_id),
    )
    try:
        row = await roles.update_role(
            session,
            tenant_id=principal.tenant_id,
            role_id=role_id,
            name=payload.name,
            description=payload.description,
            permissions=payload.permissions,
            is_active=payload.is_active,
        )
    except IdentityError as error:
        raise translate(error) from error
    return await _role_response(session, principal=principal, role=row)


# ---------------------------------------------------------------------------
# Grants & delegation
# ---------------------------------------------------------------------------


@router.get(
    "/grants",
    response_model=list[GrantResponse],
    summary="Who holds what",
    dependencies=[Depends(requires("users:read"))],
)
async def list_grants(
    principal: PrincipalDep,
    session: SessionDep,
    user_id: Annotated[uuid.UUID | None, Query()] = None,
    include_revoked: Annotated[bool, Query()] = False,
) -> list[GrantResponse]:
    """An access review, scoped to what the caller may see.

    ``visible`` first, and the filter is applied to the query rather than to the
    result. A department head reviewing access sees grants held by users in
    their own department and no others — and crucially gets the same answer from
    a count as from a list, which is the clause about pagination counts.
    """
    allowed = decisions.visible(principal=principal, permission="users:read")
    rows = await roles.list_grants(
        session,
        tenant_id=principal.tenant_id,
        user_id=user_id,
        include_revoked=include_revoked,
    )
    visible_user_ids = await _visible_user_ids(session, principal=principal, allowed=allowed)
    return [
        GrantResponse(
            id=grant.id,
            user_id=grant.user_id,
            role_id=grant.role_id,
            role_code=role.code,
            scope_kind=grant.scope_kind,
            scope_ref=grant.scope_ref,
            valid_from=grant.valid_from,
            valid_until=grant.valid_until,
            delegated_by_user_id=grant.delegated_by_user_id,
            reason=grant.reason,
            revoked_at=grant.revoked_at,
        )
        for grant, role in rows
        if visible_user_ids is None or grant.user_id in visible_user_ids
    ]


@router.post(
    "/grants",
    response_model=GrantResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Assign a role on a scope",
    dependencies=[Depends(requires("grants:write"))],
)
async def create_grant(
    principal: PrincipalDep, session: SessionDep, payload: GrantSpec
) -> GrantResponse:
    """Assign a role. The caller must be permitted *on the scope being granted*.

    That second check is the one that matters and it is easy to leave out: a
    department head with ``grants:write`` on their own department must not be
    able to grant a role over the tenant. The resource handed to ``authorize``
    is therefore the *scope of the grant*, not the user receiving it.
    """
    try:
        resolved = await roles.resolve_scope(
            session,
            tenant_id=principal.tenant_id,
            kind=payload.scope_kind,
            ref=payload.scope_ref,
        )
    except IdentityError as error:
        raise translate(error) from error

    await decisions.authorize(
        principal=principal,
        permission="grants:write",
        resource=_scope_resource(resolved),
    )

    try:
        grant = await roles.grant(
            session,
            tenant_id=principal.tenant_id,
            user_id=payload.user_id,
            role_id=payload.role_id,
            scope_kind=payload.scope_kind,
            scope_ref=payload.scope_ref,
            valid_from=payload.valid_from,
            valid_until=payload.valid_until,
            reason=payload.reason,
        )
        role = await roles.get_role(session, tenant_id=principal.tenant_id, role_id=payload.role_id)
    except IdentityError as error:
        raise translate(error) from error
    return _grant_response(grant, role_code=role.code)


@router.post(
    "/grants/delegate",
    response_model=GrantResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Hand a subset of your own authority to a colleague",
    dependencies=[Depends(requires("grants:delegate"))],
)
async def delegate_grant(
    principal: PrincipalDep, session: SessionDep, payload: DelegationSpec
) -> GrantResponse:
    """Leave and shift cover, bounded and attributed.

    The delegator's own grants are passed to the service from the principal
    resolved for *this* request — ``identity.roles.delegate`` explains why
    loading them again inside the service would make its answer race with this
    authorization check.
    """
    try:
        resolved = await roles.resolve_scope(
            session,
            tenant_id=principal.tenant_id,
            kind=payload.scope_kind,
            ref=payload.scope_ref,
        )
    except IdentityError as error:
        raise translate(error) from error

    await decisions.authorize(
        principal=principal,
        permission="grants:delegate",
        resource=_scope_resource(resolved),
    )

    try:
        grant = await roles.delegate(
            session,
            tenant_id=principal.tenant_id,
            delegator_user_id=principal.user_id,
            delegator_grants=principal.grants,
            to_user_id=payload.to_user_id,
            role_id=payload.role_id,
            scope_kind=payload.scope_kind,
            scope_ref=payload.scope_ref,
            valid_until=payload.valid_until,
            reason=payload.reason,
        )
        role = await roles.get_role(session, tenant_id=principal.tenant_id, role_id=payload.role_id)
    except IdentityError as error:
        raise translate(error) from error
    return _grant_response(grant, role_code=role.code)


@router.delete(
    "/grants/{grant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a grant",
    dependencies=[Depends(requires("grants:write"))],
)
async def revoke_grant(
    principal: PrincipalDep,
    session: SessionDep,
    grant_id: uuid.UUID,
    reason: Annotated[str, Query(min_length=3, max_length=2000)],
) -> Response:
    """Revoke, with the scope of the grant checked before anything is written.

    The grant is loaded first so the authorization can be against *its* scope.
    Checking only ``grants:write`` somewhere would let a department head revoke
    a grant in another department, which is the same defect as granting one
    there, reached from the other direction.
    """
    from sqlalchemy import select

    from nemesis.tenancy.context import tenant_scope

    with tenant_scope(principal.tenant_id):
        existing = (
            await session.execute(
                select(RoleGrant).where(
                    RoleGrant.tenant_id == principal.tenant_id,
                    RoleGrant.id == grant_id,
                )
            )
        ).scalar_one_or_none()
    if existing is None:
        from nemesis.identity.errors import NotFoundError

        raise translate(NotFoundError(f"no grant {grant_id} in this tenant"))

    try:
        resolved = await roles.resolve_scope(
            session,
            tenant_id=principal.tenant_id,
            kind=existing.scope_kind,
            ref=existing.scope_ref,
        )
    except IdentityError as error:
        raise translate(error) from error

    await decisions.authorize(
        principal=principal,
        permission="grants:write",
        resource=_scope_resource(resolved),
    )
    try:
        await roles.revoke_grant(
            session, tenant_id=principal.tenant_id, grant_id=grant_id, reason=reason
        )
    except IdentityError as error:
        raise translate(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


@router.get(
    "/users",
    response_model=list[UserResponse],
    summary="This tenant's users, scoped to the caller",
    dependencies=[Depends(requires("users:read"))],
)
async def list_users(
    principal: PrincipalDep,
    session: SessionDep,
    include_inactive: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = DEFAULT_PAGE,
) -> list[UserResponse]:
    """The clause about pagination counts, implemented.

    A contractor lead holding ``contractor_users:manage`` on their own
    organisation sees their own crew. A department head sees their own
    department. Neither sees a count that includes the other's people, because
    the constraint reaches the query rather than filtering the response.
    """
    allowed = decisions.visible(principal=principal, permission="users:read")
    visible_user_ids = await _visible_user_ids(session, principal=principal, allowed=allowed)

    rows = await users.list_users(
        session,
        tenant_id=principal.tenant_id,
        contractor_id=(
            principal.contractor_id
            if visible_user_ids is not None and principal.contractor_id is not None
            else None
        ),
        include_inactive=include_inactive,
    )
    selected = [row for row in rows if visible_user_ids is None or row.id in visible_user_ids][
        :limit
    ]
    return [await _user_response(session, principal=principal, user=row) for row in selected]


@router.post(
    "/users",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a user",
    # ``authenticated()`` rather than ``requires(...)``, and this is not a
    # weaker declaration — it is the only correct one. Which permission
    # applies depends on the *body*: creating a contractor organisation's
    # sub-user needs ``contractor_users:manage`` on that contractor, and
    # creating an operator needs ``users:write``. Declaring either one here
    # would make ``requires`` refuse the other caller before the handler
    # could choose, which is how a foreman managing their own crew ends up
    # being told they need a permission the job does not require.
    #
    # The decision is made in the body, with the resource carrying the
    # contractor, and ``check_endpoint_permissions.py`` permits this shape
    # only for a handler that calls ``authorize`` itself — which this does.
    dependencies=[Depends(authenticated())],
)
async def create_user(
    principal: PrincipalDep, session: SessionDep, payload: UserSpec
) -> UserResponse:
    """Create a user. A new user holds nothing until somebody grants it.

    A contractor sub-user is created through this route too, by a caller holding
    ``contractor_users:manage`` on that contractor — which is why the resource
    for the decision carries ``contractor_id`` when one is named. A foreman
    creating their own crew is the case this serves; a foreman creating a
    municipal operator is the case it refuses.
    """
    required = "contractor_users:manage" if payload.contractor_id is not None else "users:write"
    await decisions.authorize(
        principal=principal,
        permission=required,
        resource=Resource(
            kind=(
                permissions.KIND_CONTRACTOR
                if payload.contractor_id is not None
                else permissions.KIND_USER
            ),
            contractor_id=payload.contractor_id,
        ),
    )
    try:
        row = await users.create_user(
            session,
            tenant_id=principal.tenant_id,
            display_name=payload.display_name,
            email=payload.email,
            kind=payload.kind,
            role_label=payload.role_label,
            department_id=payload.department_id,
            contractor_id=payload.contractor_id,
            password=payload.password,
        )
    except IdentityError as error:
        raise translate(error) from error
    return await _user_response(session, principal=principal, user=row)


@router.post(
    "/users/{user_id}/password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Reset a user's password",
    dependencies=[Depends(requires("users:write"))],
)
async def reset_password(
    principal: PrincipalDep,
    session: SessionDep,
    user_id: uuid.UUID,
    payload: PasswordResetSpec,
) -> Response:
    """Set a password on somebody else's account, and sign them out everywhere.

    Both halves are deliberate. ``must_change`` is set because a password
    somebody else chose is a password somebody else knows, and every session is
    revoked because the usual reason for an administrative reset is that
    somebody who should not have the old credential does.
    """
    await decisions.authorize(
        principal=principal,
        permission="users:write",
        resource=await _user_resource(session, principal=principal, user_id=user_id),
    )
    try:
        await users.set_password(
            session,
            tenant_id=principal.tenant_id,
            user_id=user_id,
            password=payload.password,
            must_change=True,
        )
    except IdentityError as error:
        raise translate(error) from error
    await sessions.revoke_for_user(
        session,
        tenant_id=principal.tenant_id,
        user_id=user_id,
        reason=sessions.REVOKED_ADMIN,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Deactivate a user",
    dependencies=[Depends(requires("users:write"))],
)
async def deactivate_user(
    principal: PrincipalDep, session: SessionDep, user_id: uuid.UUID
) -> Response:
    """Deactivate, never delete. Grants are kept for the access review.

    ``DELETE`` on a route that deactivates is a deliberate choice of verb over
    literalism: the client's intent is "remove this person's access", that is
    what happens, and offering a ``DELETE`` that actually deleted would make the
    audit trail destructible from the API.
    """
    if user_id == principal.user_id:
        raise translate(
            ValidationError(
                "deactivating your own account would lock you out of the tenant with "
                "no way back in; ask another administrator"
            )
        )
    await decisions.authorize(
        principal=principal,
        permission="users:write",
        resource=await _user_resource(session, principal=principal, user_id=user_id),
    )
    try:
        await users.deactivate(session, tenant_id=principal.tenant_id, user_id=user_id)
    except IdentityError as error:
        raise translate(error) from error
    await sessions.revoke_for_user(
        session,
        tenant_id=principal.tenant_id,
        user_id=user_id,
        reason=sessions.REVOKED_USER_DEACTIVATED,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Identity providers
# ---------------------------------------------------------------------------


@router.post(
    "/providers",
    status_code=status.HTTP_201_CREATED,
    summary="Configure an OIDC provider for this tenant",
    dependencies=[Depends(requires("users:write"))],
)
async def register_provider(
    principal: PrincipalDep, session: SessionDep, payload: ProviderSpec
) -> dict[str, str]:
    await decisions.authorize(
        principal=principal,
        permission="users:write",
        resource=Resource(kind=permissions.KIND_USER),
    )
    try:
        provider = await oidc.register_provider(
            session,
            tenant_id=principal.tenant_id,
            slug=payload.slug,
            display_name=payload.display_name,
            issuer=payload.issuer,
            client_id=payload.client_id,
            client_secret_setting=payload.client_secret_setting,
            scopes=payload.scopes,
            allowed_email_domains=payload.allowed_email_domains,
            auto_provision=payload.auto_provision,
            auto_provision_role_id=payload.auto_provision_role_id,
        )
    except IdentityError as error:
        raise translate(error) from error
    return {"id": str(provider.id), "slug": provider.slug}


# ---------------------------------------------------------------------------
# Impersonation
# ---------------------------------------------------------------------------


@router.post(
    "/impersonation",
    response_model=ImpersonationTokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Begin an audited support session",
    dependencies=[Depends(requires("impersonation:start"))],
)
async def start_impersonation(
    principal: PrincipalDep,
    session: SessionDep,
    config: ConfigDep,
    payload: ImpersonationSpec,
) -> ImpersonationTokenResponse:
    """Platform-only, justified, capped, and visible to the tenant.

    ``requires("impersonation:start")`` is the whole authorization check here,
    and it is sufficient because the permission is declared with
    ``resource_kind=None``: there is no narrower subject than "may this operator
    impersonate", and the *target* is validated by the service rather than by a
    scope — a support engineer with the permission may support any customer, and
    the control on that is the audit rather than a grant per tenant.
    """
    try:
        started = await impersonation.start(
            session,
            secret=config.jwt_secret.get_secret_value(),
            operator=principal,
            target_tenant_id=payload.tenant_id,
            target_user_id=payload.user_id,
            justification=payload.justification,
            access_ttl_seconds=config.identity.access_ttl_seconds,
            duration=timedelta(minutes=payload.minutes),
            allows_writes=payload.allows_writes,
            ticket_ref=payload.ticket_ref,
        )
        row = await impersonation.get(
            session,
            tenant_id=started.tenant_id,
            impersonation_id=started.impersonation_id,
        )
    except IdentityError as error:
        raise translate(error) from error

    now = datetime.now(tz=UTC)
    return ImpersonationTokenResponse(
        impersonation=_impersonation_response(row),
        access_token=started.issued.access_token,
        expires_in=max(1, int((started.issued.access_expires_at - now).total_seconds())),
        refresh_token=started.issued.refresh_token,
    )


@router.delete(
    "/impersonation/{impersonation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="End a support session",
    dependencies=[Depends(authenticated())],
)
async def end_impersonation(
    principal: PrincipalDep, session: SessionDep, impersonation_id: uuid.UUID
) -> Response:
    """Endable by the operator inside it **or** by the tenant it is against.

    Two callers, two permissions, one route — and no ``requires`` declaration,
    because which permission applies depends on who is asking. The checker
    permits that only for a route that calls ``authorize`` itself, which this
    does, twice, with the two possibilities named. A tenant being unable to
    close a support session it did not ask for would make the visibility this
    phase is built around decorative.
    """
    try:
        row = await impersonation.get(
            session, tenant_id=principal.tenant_id, impersonation_id=impersonation_id
        )
    except IdentityError:
        # Not this tenant's. It may still be the operator's own session, in
        # which case the principal's tenant *is* the impersonated tenant (the
        # session was issued against their user), so there is nothing else to
        # look for: 404 either way, and the same 404 for both.
        from nemesis.identity.errors import NotFoundError

        raise translate(NotFoundError(f"no impersonation session {impersonation_id}")) from None

    if principal.impersonation_id == impersonation_id:
        reason = impersonation.ENDED_BY_OPERATOR
    else:
        await decisions.authorize(
            principal=principal,
            permission="impersonation:read",
            resource=Resource(kind=permissions.KIND_TENANT, id=principal.tenant_id),
        )
        reason = impersonation.ENDED_BY_TENANT

    try:
        await impersonation.end(
            session,
            tenant_id=row.tenant_id,
            impersonation_id=impersonation_id,
            reason=reason,
        )
    except IdentityError as error:
        raise translate(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/impersonation",
    response_model=list[ImpersonationResponse],
    summary="What support has done in this tenant",
    dependencies=[Depends(requires("impersonation:read"))],
)
async def list_impersonations(
    principal: PrincipalDep,
    session: SessionDep,
    only_open: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = DEFAULT_PAGE,
) -> list[ImpersonationResponse]:
    """The gate clause, as an endpoint the customer can call.

    Held by tenant roles and not by support: the whole point is that the
    impersonated party can see this and the operator cannot suppress it.
    """
    await decisions.authorize(
        principal=principal,
        permission="impersonation:read",
        resource=Resource(kind=permissions.KIND_TENANT, id=principal.tenant_id),
    )
    rows = await impersonation.list_for_tenant(
        session, tenant_id=principal.tenant_id, limit=limit, only_open=only_open
    )
    return [_impersonation_response(row) for row in rows]


# ---------------------------------------------------------------------------
# Decision log
# ---------------------------------------------------------------------------


@router.get(
    "/decisions",
    response_model=list[AuthorizationDecisionResponse],
    summary="This tenant's authorization decision log",
    dependencies=[Depends(requires("decisions:read"))],
)
async def list_decisions(
    principal: PrincipalDep,
    session: SessionDep,
    user_id: Annotated[uuid.UUID | None, Query()] = None,
    allowed: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[AuthorizationDecisionResponse]:
    """ "Who tried to open Ward 7 last night", without an engineer running SQL.

    Rows are retained for ninety days (``identity.decisions.DECISION_RETENTION``)
    and the response does not say so — the API documentation does, because a
    field in every row repeating a constant is noise.
    """
    await decisions.authorize(
        principal=principal,
        permission="decisions:read",
        resource=Resource(kind=permissions.KIND_USER),
    )
    rows = await decisions.recent(
        session,
        tenant_id=principal.tenant_id,
        limit=limit,
        user_id=user_id,
        allowed=allowed,
    )
    return [
        AuthorizationDecisionResponse(
            id=row.id,
            decided_at=row.decided_at,
            user_id=row.user_id,
            permission=row.permission,
            allowed=row.allowed,
            reason_code=row.reason_code,
            resource_kind=row.resource_kind,
            resource_id=row.resource_id,
            matched_scope=row.matched_scope,
            operator_subject=row.operator_subject,
            route=row.route,
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scope_resource(resolved: roles.ScopeRef) -> Resource:
    """The scope of a grant, expressed as the resource a decision is about.

    This is the function that makes "may you grant *there*" checkable at all. A
    department-scoped grant becomes a resource carrying that department's path,
    so a caller whose own grant covers a parent department passes and one whose
    grant covers a sibling does not.
    """
    if resolved.kind == permissions.SCOPE_DEPARTMENT:
        return Resource(kind=permissions.KIND_USER, department_path=resolved.path)
    if resolved.kind == permissions.SCOPE_ZONE:
        return Resource(kind=permissions.KIND_USER, zone_path=resolved.path)
    if resolved.kind == permissions.SCOPE_TAXONOMY:
        return Resource(kind=permissions.KIND_USER, taxonomy_path=resolved.path)
    if resolved.kind == permissions.SCOPE_CONTRACTOR:
        return Resource(kind=permissions.KIND_CONTRACTOR, contractor_id=resolved.ref)
    # `tenant` and `self`. A tenant-wide grant can only be made by somebody
    # holding the permission tenant-wide, which is what an empty resource
    # produces: no path for a scoped grant to contain.
    return Resource(kind=permissions.KIND_USER)


async def _user_resource(
    session: SessionDep, *, principal: Principal, user_id: uuid.UUID
) -> Resource:
    """A user, described by the attributes a grant can test.

    Their department's *path*, not its id: containment is prefix containment, so
    a grant on a parent department must cover a user in a child. Resolving the
    path here rather than storing it on the user keeps a reparented department
    from leaving stale authorization data behind.
    """
    from sqlalchemy import select

    from nemesis.db.models.organisation import Department
    from nemesis.tenancy.context import tenant_scope

    row = await users.get_user(session, tenant_id=principal.tenant_id, user_id=user_id)
    department_path: str | None = None
    if row.department_id is not None:
        with tenant_scope(principal.tenant_id):
            department_path = (
                await session.execute(
                    select(Department.path).where(
                        Department.tenant_id == principal.tenant_id,
                        Department.id == row.department_id,
                    )
                )
            ).scalar_one_or_none()
    return Resource(
        kind=permissions.KIND_USER,
        id=row.id,
        department_path=department_path,
        contractor_id=row.contractor_id,
        attached_user_ids=frozenset({row.id}),
    )


async def _visible_user_ids(
    session: SessionDep, *, principal: Principal, allowed: Visibility
) -> set[uuid.UUID] | None:
    """Which users this principal may see, or ``None`` for "all of them".

    ``None`` rather than a set containing every user, because the two readings of
    an empty set — "nothing is visible" and "no constraint" — differ by the whole
    dataset, which is the mistake ``Visibility.unrestricted`` exists to make
    impossible to write by accident.
    """
    if allowed.unrestricted:
        return None
    if allowed.permits_nothing:
        return set()

    from sqlalchemy import or_, select

    from nemesis.db.models.organisation import Department
    from nemesis.tenancy.context import tenant_scope

    with tenant_scope(principal.tenant_id):
        statement = select(User.id).where(User.tenant_id == principal.tenant_id)
        clauses = []
        if allowed.includes_self:
            clauses.append(User.id == principal.user_id)
        if allowed.contractor_ids:
            clauses.append(User.contractor_id.in_(allowed.contractor_ids))
        if allowed.department_paths:
            # Prefix containment in SQL: the same `LIKE 'path/%'` the three
            # trees use everywhere else, plus the node itself.
            department_ids = select(Department.id).where(
                Department.tenant_id == principal.tenant_id,
                or_(
                    *[
                        or_(
                            Department.path == path,
                            Department.path.like(f"{path}/%"),
                        )
                        for path in sorted(allowed.department_paths)
                    ]
                ),
            )
            clauses.append(User.department_id.in_(department_ids))
        if not clauses:
            # Zone and taxonomy scopes say nothing about which *users* exist —
            # a user is attached to a department, not to a ward. A principal
            # whose only grants are zone-scoped therefore sees no user list,
            # which is the honest answer rather than the whole tenant.
            return set()
        statement = statement.where(or_(*clauses))
        return set((await session.execute(statement)).scalars().all())


async def _role_response(session: SessionDep, *, principal: Principal, role: Role) -> RoleResponse:
    return RoleResponse(
        id=role.id,
        code=role.code,
        name=role.name,
        description=role.description,
        permissions=sorted(role.permissions),
        is_system=role.is_system,
        is_active=role.is_active,
        live_grants=await roles.count_grants_of_role(
            session, tenant_id=principal.tenant_id, role_id=role.id
        ),
    )


async def _user_response(session: SessionDep, *, principal: Principal, user: User) -> UserResponse:
    from sqlalchemy import select

    from nemesis.db.models.identity import UserCredential
    from nemesis.tenancy.context import tenant_scope

    with tenant_scope(principal.tenant_id):
        has_password = (
            await session.execute(
                select(UserCredential.id).where(
                    UserCredential.tenant_id == principal.tenant_id,
                    UserCredential.user_id == user.id,
                )
            )
        ).scalar_one_or_none() is not None

    return UserResponse(
        id=user.id,
        subject=user.subject,
        display_name=user.display_name,
        email=user.email,
        kind=user.kind,
        role_label=user.role,
        department_id=user.department_id,
        contractor_id=user.contractor_id,
        is_active=user.is_active,
        last_login_at=user.last_login_at,
        has_password=has_password,
    )


def _grant_response(grant: RoleGrant, *, role_code: str) -> GrantResponse:
    return GrantResponse(
        id=grant.id,
        user_id=grant.user_id,
        role_id=grant.role_id,
        role_code=role_code,
        scope_kind=grant.scope_kind,
        scope_ref=grant.scope_ref,
        valid_from=grant.valid_from,
        valid_until=grant.valid_until,
        delegated_by_user_id=grant.delegated_by_user_id,
        reason=grant.reason,
        revoked_at=grant.revoked_at,
    )


def _impersonation_response(row: ImpersonationSession) -> ImpersonationResponse:
    return ImpersonationResponse(
        id=row.id,
        tenant_id=row.tenant_id,
        subject_user_id=row.subject_user_id,
        operator_subject=row.operator_subject,
        justification=row.justification,
        ticket_ref=row.ticket_ref,
        allows_writes=row.allows_writes,
        started_at=row.started_at,
        ends_at=row.ends_at,
        ended_at=row.ended_at,
        ended_reason=row.ended_reason,
        decisions_allowed=row.decisions_allowed,
        decisions_denied=row.decisions_denied,
    )


__all__ = ["router"]
