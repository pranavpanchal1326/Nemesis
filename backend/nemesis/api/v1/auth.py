"""Signing in, refreshing, signing out, and asking who you are.

**Four of these six routes are deliberately public**, and each says so with
``Depends(public())`` rather than by omitting a declaration — the distinction
``api.identity_deps.public`` exists for. A sign-in endpoint that required a
session would be a joke; an endpoint that is open *by accident* is the defect
``check_endpoint_permissions.py`` was written to catch.

**``POST /auth/token`` takes the tenant from the ``X-Tenant-ID`` header**, and
this is the one remaining place where that header does real work for an
authenticated flow. It has to: the caller has no token yet, and an email address
alone does not name a tenant — the same address can legitimately exist in two,
and resolving "which tenant does this person mean" by searching every tenant for
the address would be a cross-tenant read on an unauthenticated endpoint, which
is precisely what this system does not do.

**Rate limiting is on the sign-in route and is not optional.** Lockout
(``user_credentials.failed_attempts``) bounds attempts against *one* account;
it does nothing against one attempt each at ten thousand accounts, which is how
credential stuffing actually works. The limiter bounds by network and the
lockout bounds by account, and neither covers the other's case.

**``GET /auth/session`` is what the console calls on every load**, and it
returns the principal plus the *effective permission set*. That set is for
rendering — greying out a button — and the response says so in a field comment
the generated client carries. Phase 18's §E26 contracts take it as data; nothing
in this system authorises anything from it, because a permission held somewhere
is not a permission held here (``identity.decisions`` makes the argument).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Header, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from nemesis.api.deps import ConfigDep, SessionDep
from nemesis.api.errors import (
    HTTP_400_BAD_REQUEST,
    HTTP_429_TOO_MANY_REQUESTS,
    PROBLEM_BASE,
    ProblemDetailError,
)
from nemesis.api.identity_deps import PrincipalDep, authenticated, public, translate
from nemesis.api.ratelimit import get_limiter
from nemesis.config import RateLimitTier, Settings
from nemesis.identity import oidc, sessions, users
from nemesis.identity.errors import IdentityError
from nemesis.identity.scopes import Grant
from nemesis.observability import metrics

router = APIRouter(prefix="/auth", tags=["identity"])

TENANT_HEADER: Final = "X-Tenant-ID"

#: Sign-in attempts per network per hour, and the number is chosen for a
#: municipal office rather than for a home.
#:
#: **A whole council is usually one address.** Thirty officers behind one NAT
#: gateway all arrive from the same IP, several of them at 09:00, some of them
#: twice because the first attempt was a typo. A ten-per-hour cap would lock out
#: a shift change and present as "the product is down". Sixty with a burst of
#: ten absorbs that and still makes a stuffing run — which needs thousands of
#: attempts to be worth running — useless.
#:
#: The per-account control is the other half and the two do not substitute for
#: each other: ``user_credentials.failed_attempts`` bounds attempts against *one*
#: account, which does nothing against one attempt each at ten thousand
#: accounts; this bounds the total from one source, which does nothing against a
#: distributed attempt at one account.
SIGN_IN_TIER: Final = RateLimitTier(requests=60, window_seconds=3600, burst=10)


# ---------------------------------------------------------------------------
# Wire contracts
# ---------------------------------------------------------------------------


class PasswordGrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: An email address or a subject. One field because a sign-in form has one
    #: field — see ``identity.users.authenticate``.
    identifier: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class TokenResponse(BaseModel):
    """What a client stores. The refresh token is returned once per rotation."""

    model_config = ConfigDict(extra="forbid")

    access_token: str
    #: Seconds, not an instant, because a client's clock may be wrong and this
    #: value is used to schedule a refresh. An absolute expiry would make a
    #: machine with a skewed clock either refresh constantly or never.
    expires_in: int
    refresh_token: str
    refresh_expires_in: int
    token_type: str = "Bearer"
    #: True when the password must be changed before anything else is useful.
    #: The session is issued anyway, so the client can call the change-password
    #: endpoint with it — issuing no token would mean the only way to change a
    #: must-change password is a password reset, which is a loop.
    must_change_password: bool = False


class GrantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    role_code: str
    scope_kind: str
    scope_ref: uuid.UUID | None
    scope_path: str | None
    permissions: list[str]
    delegated: bool


class SessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    subject: str
    display_name: str | None
    kind: str
    email: str | None
    department_id: uuid.UUID | None
    contractor_id: uuid.UUID | None
    is_platform: bool

    #: Every permission held on *some* scope. **For rendering only** — a greyed
    #: out button, a hidden tab. Never an authorization decision: holding
    #: review:decide somewhere says nothing about this review item.
    permissions: list[str]
    grants: list[GrantResponse]

    #: Present only on an impersonated session, and present *to the operator*:
    #: the console renders a banner from it, so support cannot forget whose
    #: screen they are looking at.
    impersonated_by: str | None = None
    impersonation_ends_at: datetime | None = None
    impersonation_allows_writes: bool = False


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(min_length=1, max_length=512)


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)


class OidcStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=64)
    redirect_uri: str = Field(min_length=1, max_length=2048)
    return_to: str | None = Field(default=None, max_length=2048)


class OidcStartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authorization_url: str
    state: str
    expires_at: datetime


class OidcCallbackRequest(BaseModel):
    """Deliberately has no ``id_token`` field, and must never gain one.

    ``identity.oidc`` trusts an ID token because of the channel it arrived on —
    a back-channel TLS request to the issuer's own token endpoint (ADR-0064).
    A token handed to us through a browser has no such channel, and accepting
    one here would turn a documented design decision into a vulnerability.
    """

    model_config = ConfigDict(extra="forbid")

    state: str = Field(min_length=1, max_length=128)
    code: str = Field(min_length=1, max_length=4096)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/token",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Exchange a password for a session",
    dependencies=[Depends(public())],
)
async def issue_token(
    request: Request,
    payload: PasswordGrantRequest,
    session: SessionDep,
    config: ConfigDep,
    x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
) -> TokenResponse:
    tenant_id = _tenant_from_header(x_tenant_id)
    await _throttle_sign_in(request, config=config, tenant_id=tenant_id)

    try:
        authenticated = await users.authenticate(
            session,
            tenant_id=tenant_id,
            identifier=payload.identifier,
            password=payload.password,
        )
        issued = await sessions.issue(
            session,
            secret=config.jwt_secret.get_secret_value(),
            user=authenticated.user,
            access_ttl_seconds=config.identity.access_ttl_seconds,
            refresh_ttl_seconds=config.identity.refresh_ttl_seconds,
            method=sessions.METHOD_PASSWORD,
            issuer=config.identity.token_issuer,
            audience=config.identity.token_audience,
            client_network=_client_address(request),
            client_kind=_client_kind(request),
        )
    except IdentityError as error:
        raise translate(error) from error

    return _token_response(issued, must_change=authenticated.must_change_password)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Rotate a refresh token",
    dependencies=[Depends(public())],
)
async def refresh_token(
    request: Request,
    payload: RefreshRequest,
    session: SessionDep,
    config: ConfigDep,
) -> TokenResponse:
    """Public by necessity: the caller's access token has usually expired.

    The refresh token *is* the credential here, and it names the tenant by
    itself (``sessions.rotate`` resolves it with no tenant in context, exactly
    as ``integrations.keys`` resolves an API key).
    """
    try:
        issued = await sessions.rotate(
            session,
            secret=config.jwt_secret.get_secret_value(),
            presented_refresh_token=payload.refresh_token,
            access_ttl_seconds=config.identity.access_ttl_seconds,
            refresh_ttl_seconds=config.identity.refresh_ttl_seconds,
            issuer=config.identity.token_issuer,
            audience=config.identity.token_audience,
            client_network=_client_address(request),
            client_kind=_client_kind(request),
        )
    except IdentityError as error:
        outcome = (
            "reuse_detected"
            if getattr(error, "reason", "") == "refresh_reuse_detected"
            else "rejected"
        )
        metrics.authentication_attempts_total.labels(method="refresh", outcome=outcome).inc()
        raise translate(error) from error

    metrics.authentication_attempts_total.labels(method="refresh", outcome="rotated").inc()
    return _token_response(issued, must_change=False)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke the current session",
    dependencies=[Depends(authenticated())],
)
async def logout(
    principal: PrincipalDep,
    session: SessionDep,
) -> Response:
    """Revokes this session only, not the family.

    Signing out of one browser must not sign you out of the field app. Revoking
    everything is a separate, deliberate action (``POST /auth/logout-all``),
    because the two intents look identical in a UI and have very different
    consequences for somebody mid-shift.

    Carries no permission declaration beyond authentication: the thing being
    acted on is the caller's own session, and a permission that every session
    necessarily holds over itself would be a permission that means nothing.
    """
    await sessions.revoke(
        session,
        tenant_id=principal.tenant_id,
        session_id=principal.session_id,
        reason=sessions.REVOKED_SIGNED_OUT,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/logout-all",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke every session for the caller",
    dependencies=[Depends(authenticated())],
)
async def logout_everywhere(
    principal: PrincipalDep,
    session: SessionDep,
) -> Response:
    """ "Sign me out everywhere" — the response to a lost phone."""
    await sessions.revoke_for_user(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        reason=sessions.REVOKED_SIGNED_OUT,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/session",
    response_model=SessionResponse,
    summary="Who the caller is and what they hold",
    dependencies=[Depends(authenticated())],
)
async def current_session(
    principal: PrincipalDep,
    session: SessionDep,
) -> SessionResponse:
    """The console's first call. See the module docstring on ``permissions``."""
    from nemesis.identity import impersonation

    user = await users.get_user(session, tenant_id=principal.tenant_id, user_id=principal.user_id)

    ends_at: datetime | None = None
    if principal.impersonation_id is not None:
        live = await impersonation.is_live(
            session,
            tenant_id=principal.tenant_id,
            impersonation_id=principal.impersonation_id,
        )
        ends_at = live.ends_at if live is not None else None

    return SessionResponse(
        user_id=principal.user_id,
        tenant_id=principal.tenant_id,
        subject=principal.subject,
        display_name=principal.display_name,
        kind=user.kind,
        email=user.email,
        department_id=principal.department_id,
        contractor_id=principal.contractor_id,
        is_platform=principal.is_platform,
        permissions=sorted(principal.permissions),
        grants=[_grant_response(grant) for grant in principal.grants],
        impersonated_by=principal.impersonator_subject,
        impersonation_ends_at=ends_at,
        impersonation_allows_writes=principal.impersonation_allows_writes,
    )


@router.post(
    "/password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Change the caller's own password",
    dependencies=[Depends(authenticated())],
)
async def change_password(
    principal: PrincipalDep,
    payload: ChangePasswordRequest,
    session: SessionDep,
) -> Response:
    """Verify the current password, set the new one, keep this session.

    The current password is required even though the caller already holds a
    valid session. A session is a bearer credential that might have been stolen;
    requiring the password means a stolen token cannot be used to lock the owner
    out of their own account, which is the first thing an attacker does with one.

    Other sessions are revoked and this one is not — ``identity.users.set_password``
    explains why that choice belongs to the caller rather than the service.
    """
    try:
        await users.authenticate(
            session,
            tenant_id=principal.tenant_id,
            identifier=principal.subject,
            password=payload.current_password,
        )
        await users.set_password(
            session,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            password=payload.new_password,
            must_change=False,
        )
    except IdentityError as error:
        raise translate(error) from error

    await sessions.revoke_for_user(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        reason=sessions.REVOKED_CREDENTIAL_CHANGED,
        except_session_id=principal.session_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/providers",
    summary="Which identity providers this tenant offers",
    dependencies=[Depends(public())],
)
async def list_sign_in_providers(
    session: SessionDep,
    x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
) -> list[dict[str, str]]:
    """Public because a sign-in page renders from it before anybody is signed in.

    Returns the slug and the display name and nothing else — not the issuer, not
    the client id, not the allowed domains. A sign-in page needs a button label;
    everything else is configuration an unauthenticated caller has no business
    enumerating.
    """
    tenant_id = _tenant_from_header(x_tenant_id)
    providers = await oidc.list_providers(session, tenant_id=tenant_id)
    return [
        {"slug": provider.slug, "display_name": provider.display_name}
        for provider in providers
        if provider.is_active
    ]


@router.post(
    "/oidc/start",
    response_model=OidcStartResponse,
    summary="Begin a provider sign-in",
    dependencies=[Depends(public())],
)
async def start_oidc(
    payload: OidcStartRequest,
    session: SessionDep,
    config: ConfigDep,
    x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
) -> OidcStartResponse:
    tenant_id = _tenant_from_header(x_tenant_id)
    try:
        started = await oidc.begin(
            session,
            tenant_id=tenant_id,
            slug=payload.provider,
            redirect_uri=payload.redirect_uri,
            allowed_redirect_uris=config.identity.oidc_redirect_uris,
            return_to=payload.return_to,
        )
    except IdentityError as error:
        raise translate(error) from error
    return OidcStartResponse(
        authorization_url=started.authorization_url,
        state=started.state,
        expires_at=started.expires_at,
    )


@router.post(
    "/oidc/callback",
    response_model=TokenResponse,
    summary="Complete a provider sign-in",
    dependencies=[Depends(public())],
)
async def complete_oidc(
    request: Request,
    payload: OidcCallbackRequest,
    session: SessionDep,
    config: ConfigDep,
    x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
) -> TokenResponse:
    tenant_id = _tenant_from_header(x_tenant_id)
    try:
        completed = await oidc.complete(
            session, tenant_id=tenant_id, state=payload.state, code=payload.code
        )
        issued = await sessions.issue(
            session,
            secret=config.jwt_secret.get_secret_value(),
            user=completed.user,
            access_ttl_seconds=config.identity.access_ttl_seconds,
            refresh_ttl_seconds=config.identity.refresh_ttl_seconds,
            method=sessions.METHOD_OIDC,
            issuer=config.identity.token_issuer,
            audience=config.identity.token_audience,
            client_network=_client_address(request),
            client_kind=_client_kind(request),
        )
    except IdentityError as error:
        metrics.authentication_attempts_total.labels(method="oidc", outcome="rejected").inc()
        raise translate(error) from error

    metrics.authentication_attempts_total.labels(method="oidc", outcome="issued").inc()
    return _token_response(issued, must_change=False)


@router.get(
    "/sessions", summary="Where the caller is signed in", dependencies=[Depends(authenticated())]
)
async def my_sessions(
    principal: PrincipalDep,
    session: SessionDep,
) -> list[dict[str, object]]:
    """The caller's own sessions, and only ever the caller's own.

    Declares no permission beyond authentication, for the reason ``logout``
    gives: the subject is the caller themself, and a permission that every
    session necessarily holds over itself would mean nothing. There is no
    ``user_id`` parameter and there must not be one -- reading somebody
    else's session list is an administrative act that belongs on
    ``/access/users/{user_id}/sessions``, where it is scoped and audited.
    """
    rows = await sessions.list_for_user(
        session, tenant_id=principal.tenant_id, user_id=principal.user_id
    )
    return [
        {
            "id": str(row.id),
            "issued_at": row.issued_at.isoformat(),
            "expires_at": row.expires_at.isoformat(),
            "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
            "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
            "revoked_reason": row.revoked_reason,
            "method": row.method,
            "client_kind": row.client_kind,
            "client_network": row.client_network,
            "is_current": row.id == principal.session_id,
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _token_response(issued: sessions.IssuedSession, *, must_change: bool) -> TokenResponse:
    now = datetime.now(tz=issued.access_expires_at.tzinfo)
    return TokenResponse(
        access_token=issued.access_token,
        expires_in=max(1, int((issued.access_expires_at - now).total_seconds())),
        refresh_token=issued.refresh_token,
        refresh_expires_in=max(1, int((issued.refresh_expires_at - now).total_seconds())),
        must_change_password=must_change,
    )


def _grant_response(grant: Grant) -> GrantResponse:
    return GrantResponse(
        id=grant.id,
        role_code=grant.role_code,
        scope_kind=grant.scope_kind,
        scope_ref=grant.scope_id,
        scope_path=grant.scope_path,
        permissions=sorted(grant.permissions),
        delegated=grant.delegated_by_user_id is not None,
    )


def _tenant_from_header(raw: str | None) -> uuid.UUID:
    """Parse the tenant header. See the module docstring on why it is needed.

    A missing or malformed header is 400 rather than 401: the request is
    unanswerable, not unauthorised, and a 401 would send a client looking for a
    credential problem it does not have.
    """
    if not raw:
        raise ProblemDetailError(
            status_code=HTTP_400_BAD_REQUEST,
            title="Tenant not specified",
            detail=(
                f"Supply the {TENANT_HEADER} header. One email address can exist in two "
                f"tenants, and searching every tenant for it would be a cross-tenant read "
                f"on an unauthenticated endpoint."
            ),
            problem_type=f"{PROBLEM_BASE}/tenant-required",
        )
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise ProblemDetailError(
            status_code=HTTP_400_BAD_REQUEST,
            title="Tenant not specified",
            detail=f"{TENANT_HEADER} must be a UUID.",
            problem_type=f"{PROBLEM_BASE}/tenant-required",
        ) from None


async def _throttle_sign_in(request: Request, *, config: Settings, tenant_id: uuid.UUID) -> None:
    """Bound sign-in attempts per network. See the module docstring."""
    limiter = get_limiter(config.redis_url, config.rate_limit)
    decision = await limiter.check_tier(
        tier_name="sign_in",
        tier=SIGN_IN_TIER,
        namespace=str(tenant_id),
        identity=_client_address(request) or "unknown",
    )
    if decision.allowed:
        return
    metrics.authentication_attempts_total.labels(method="password", outcome="throttled").inc()
    raise ProblemDetailError(
        status_code=HTTP_429_TOO_MANY_REQUESTS,
        title="Too many sign-in attempts",
        detail=f"Retry in {decision.retry_after_seconds} seconds.",
        problem_type=f"{PROBLEM_BASE}/rate-limited",
        extra={"retry_after_seconds": decision.retry_after_seconds},
        headers={"Retry-After": str(decision.retry_after_seconds)},
    )


def _client_address(request: Request) -> str | None:
    """The peer address. Truncated before storage by ``sessions.issue``.

    Read from the socket, not from ``X-Forwarded-For``. A forwarded header is
    caller-supplied, so trusting it lets an attacker spend somebody else's
    rate-limit budget — and the deployment behind a proxy configures the proxy's
    own trusted-header handling rather than having this function guess.
    """
    client = request.client
    return client.host if client is not None else None


def _client_kind(request: Request) -> str | None:
    """The user agent's product token only — "did they sign in from the field app".

    The full string is a fingerprint, and §22 does not need one to answer the
    question anybody actually asks of this column.
    """
    agent = request.headers.get("user-agent")
    if not agent:
        return None
    return agent.split("/", 1)[0].split(" ", 1)[0][:64]


__all__ = ["router"]
