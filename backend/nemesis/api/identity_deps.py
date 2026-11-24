"""Authenticating a request, and declaring what a route requires.

**Every guarded route declares its permission in its decorator**, as a
dependency built by ``requires``. That declaration is not documentation:
``scripts/check_endpoint_permissions.py`` walks the AST of every router module
and fails the build on a route that declares neither a permission from the
registry nor an explicit public exemption. That is the Phase 13 gate clause
"adding an endpoint without a declared permission fails CI", and it is why the
declaration is a decorator argument an AST walk can read rather than a call
somewhere in a handler body.

**Authorization happens in two stages, and the split is the part worth
understanding.**

*Stage one*, here, in ``requires``: does this principal hold this permission on
**some** scope? A caller who holds ``review:decide`` nowhere is refused before
the handler opens a transaction or loads a row — which is both faster and, more
importantly, the refusal that must not leak whether the row exists.

*Stage two*, in the handler, through ``identity.decisions.authorize`` with the
loaded resource, or through ``decisions.visible`` for a list: does this
principal hold it **here**, on this department, this zone, this contractor?

Stage one alone is not authorization, and the obvious failure — a handler that
declares a permission and then reads any row the caller names — is precisely
what the Phase 13 gate forbids. So the CI checker enforces the second half too:
a route declaring a *scopable* permission must call ``authorize`` or ``visible``
in its body. A route declaring a tenant-global permission (one whose registry
entry has ``resource_kind=None``) needs no second stage, because there is no
narrower subject to check against, and ``requires`` performs the full decision
for those.

**Tenant resolution moves here from a header, exactly as ``api.deps`` promised.**
``api.deps.require_tenant`` now reads the bearer token's tenant claim when there
is one and falls back to ``X-Tenant-ID`` only for the routes that are
deliberately unauthenticated — citizen submission, the public surface. The
promise that module made was that Phase 13's change would be one function body;
it was.
"""

from __future__ import annotations

import hmac
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Final

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.api.errors import (
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
    HTTP_502_BAD_GATEWAY,
    PROBLEM_BASE,
    ProblemDetailError,
)
from nemesis.config import Settings, get_settings
from nemesis.db.session import session_scope
from nemesis.domain.constants import SYSTEM_TENANT_ID
from nemesis.identity import decisions, impersonation, permissions, sessions, tokens
from nemesis.identity.errors import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    CredentialLockedError,
    IdentityError,
    NotFoundError,
    ProviderError,
    ValidationError,
)
from nemesis.identity.scopes import Principal, Resource
from nemesis.observability.logging import get_logger
from nemesis.tenancy.context import tenant_scope

log = get_logger(__name__)

AUTHORIZATION_HEADER: Final = "Authorization"
#: Phase 5's shared secret. Named here as well as in ``api.v1.control_plane``
#: because this module is what deprecates it, and a second literal is cheaper
#: than an import that would point a dependency at a router.
CONTROL_PLANE_TOKEN_HEADER: Final = "X-Control-Plane-Token"
BEARER_PREFIX: Final = "Bearer "


def settings_of(request: Request) -> Settings:
    """Settings from application state, falling back to the singleton.

    The same two lines ``api.deps.get_config`` and ``api.key_deps`` each carry,
    and a third copy is one too many — but importing one of theirs would make
    this module depend on a router's dependency wiring. It lives here and the
    duplication is named rather than hidden.
    """
    candidate = getattr(request.app.state, "settings", None)
    return candidate if isinstance(candidate, Settings) else get_settings()


def bearer_token(request: Request) -> str | None:
    """The bearer token, or ``None``. Never raises on a malformed header.

    A malformed ``Authorization`` header is treated as no header at all, so a
    route that is happy to be anonymous stays reachable when a client sends
    something odd. A route that requires a principal produces the 401 itself,
    one layer up, with one message.
    """
    header = request.headers.get(AUTHORIZATION_HEADER)
    if not header or not header.startswith(BEARER_PREFIX):
        return None
    candidate = header[len(BEARER_PREFIX) :].strip()
    return candidate or None


async def resolve_principal(
    request: Request,
    *,
    now: datetime | None = None,
) -> Principal | None:
    """Verify the token, confirm the session, and load the grants.

    Returns ``None`` when there is no token at all — "anonymous" is a legitimate
    state for the citizen and public surfaces. A token that is *present and
    invalid* raises, because silently treating it as anonymous would turn an
    expired session into a confusing 404 on a route that happens to tolerate
    anonymity.

    Opens its own session rather than sharing the request's, for the reason
    ``api.deps.require_tenant`` gives about tenant resolution: the principal has
    to be known before the handler's transaction begins, and a handler that
    rolls back must not roll back the ``last_seen_at`` touch or the decision log.
    """
    # Bound before anything can be refused, so a decision recorded from a
    # handler body carries the route without every handler having to pass it.
    decisions.set_route(_route_label(request))

    presented = bearer_token(request)
    if presented is None:
        return None

    moment = now or datetime.now(tz=UTC)
    config = settings_of(request)

    try:
        claims = tokens.decode(
            presented,
            secret=config.jwt_secret.get_secret_value(),
            issuer=config.identity.token_issuer,
            audience=config.identity.token_audience,
            now=moment,
        )
    except AuthenticationError as error:
        raise _unauthenticated(error) from error

    async with session_scope() as session:
        try:
            resolved = await sessions.resolve(session, claims=claims, now=moment)
        except AuthenticationError as error:
            raise _unauthenticated(error) from error

        impersonator_subject: str | None = None
        allows_writes = False
        if resolved.impersonation_id is not None:
            # The token's `imp` claim is checked against the row, not trusted.
            # Without this the two-hour cap is a number in a table that nothing
            # enforces: an access token minted at the start of a session would
            # keep working after the session was ended or expired.
            live = await impersonation.is_live(
                session,
                tenant_id=resolved.tenant_id,
                impersonation_id=resolved.impersonation_id,
                now=moment,
            )
            if live is None:
                raise _unauthenticated(
                    AuthenticationError(
                        "impersonation session closed", reason="impersonation_closed"
                    )
                )
            impersonator_subject = live.operator_subject
            allows_writes = live.allows_writes
        elif claims.impersonation_id is not None:
            # A token claiming an impersonation the session row does not carry.
            # Unreachable through this code — `sessions.issue` writes both
            # together — and therefore exactly the forgery this check catches.
            raise _unauthenticated(
                AuthenticationError(
                    "token claims an impersonation this session does not have",
                    reason="impersonation_claim_mismatch",
                )
            )

        principal = Principal(
            user_id=resolved.user_id,
            tenant_id=resolved.tenant_id,
            subject=resolved.subject,
            session_id=resolved.id,
            display_name=resolved.display_name,
            contractor_id=resolved.contractor_id,
            department_id=resolved.department_id,
            impersonator_subject=impersonator_subject,
            impersonation_id=resolved.impersonation_id,
            impersonation_allows_writes=allows_writes,
            is_platform=resolved.tenant_id == SYSTEM_TENANT_ID,
        )
        principal = await decisions.load_principal_grants(session, principal=principal, now=moment)
        await _touch_if_stale(session, principal=principal, config=config, moment=moment)

    return principal


async def _touch_if_stale(
    session: AsyncSession,
    *,
    principal: Principal,
    config: Settings,
    moment: datetime,
) -> None:
    """Refresh ``last_seen_at``, at most once per configured interval.

    The staleness test is done with a conditional ``UPDATE`` rather than a read
    followed by a write: two requests arriving together would both read a stale
    value and both write, which is the write-per-request this interval exists to
    avoid.
    """
    from datetime import timedelta

    from sqlalchemy import update

    from nemesis.db.models.identity import UserSession

    cutoff = moment - timedelta(seconds=config.identity.session_touch_interval_seconds)
    with tenant_scope(principal.tenant_id):
        await session.execute(
            update(UserSession)
            .where(
                UserSession.tenant_id == principal.tenant_id,
                UserSession.id == principal.session_id,
                (UserSession.last_seen_at.is_(None)) | (UserSession.last_seen_at < cutoff),
            )
            .values(last_seen_at=moment)
        )


async def require_principal(request: Request) -> Principal:
    """A verified principal, or 401."""
    principal = await resolve_principal(request)
    if principal is None:
        raise ProblemDetailError(
            status_code=HTTP_401_UNAUTHORIZED,
            title="Not authenticated",
            detail="Supply a bearer token from POST /api/v1/auth/token.",
            problem_type=f"{PROBLEM_BASE}/unauthenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal


async def optional_principal(request: Request) -> Principal | None:
    """A verified principal, or ``None``. An invalid token still raises."""
    return await resolve_principal(request)


#: Declared here rather than at the end of the module: the dependency
#: factories below take the principal as a sub-dependency, which is what makes
#: FastAPI resolve it once per request instead of once per guard.
PrincipalDep = Annotated[Principal, Depends(require_principal)]
OptionalPrincipalDep = Annotated[Principal | None, Depends(optional_principal)]


def requires(permission: str) -> Callable[..., Awaitable[Principal]]:
    """Declare the permission a route requires. See the module docstring.

    The permission is resolved against the registry **at import time**, not per
    request: a typo in a route decorator is then an ``ImportError`` when the app
    is built — which the test suite and the container's own startup both hit —
    rather than a 403 nobody can explain in production.

    **The principal arrives as a sub-dependency rather than being resolved
    inside**, and that is a performance decision with a correctness edge.
    FastAPI caches a dependency's result per request, so a handler that also
    declares ``PrincipalDep`` shares this one resolution — one token verify,
    one session read, one grant load. Calling ``require_principal`` directly
    here would do all three twice, and the second load could legitimately
    return *different grants* if a revocation landed between them, which is a
    race nobody would ever reproduce.
    """
    declared = permissions.get(permission)

    async def dependency(request: Request, principal: PrincipalDep) -> Principal:
        route = _route_label(request)

        if declared.resource_kind is None:
            # Tenant-global: there is no narrower subject, so this *is* the
            # decision. `authorize` raises `AuthorizationError`, which the
            # handlers' translator turns into 403.
            await decisions.authorize(
                principal=principal,
                permission=permission,
                resource=Resource(kind=permissions.KIND_TENANT),
                route=route,
            )
            return principal

        # Scopable: "held somewhere" only. The handler narrows — and the CI
        # checker requires it to, by asserting the body calls `authorize` or
        # `visible`.
        if permission not in principal.permissions:
            await decisions.authorize(
                principal=principal,
                permission=permission,
                resource=Resource(kind=declared.resource_kind),
                route=route,
                raise_on_deny=True,
            )
        return principal

    return dependency


@dataclass(frozen=True, slots=True)
class Authorized:
    """The outcome of a dual-path guard: a principal, or the deprecated token.

    ``principal`` is ``None`` exactly when the caller presented the Phase 5
    shared control-plane token instead of a session. Handlers that need to
    record *who* did something must therefore handle both, and the two answers
    are genuinely different: a principal is a person, and the token is "somebody
    with the deployment secret". Returning a fabricated principal for the token
    path would put a name on an action nobody can be held to.
    """

    principal: Principal | None
    used_control_plane_token: bool

    @property
    def actor_id(self) -> uuid.UUID | None:
        return self.principal.user_id if self.principal is not None else None

    @property
    def actor_label(self) -> str:
        """What to write in an audit field. Honest in both cases."""
        if self.principal is None:
            return "control-plane token (deprecated)"
        if self.principal.impersonator_subject is not None:
            return f"{self.principal.subject} via {self.principal.impersonator_subject}"
        return self.principal.subject


def requires_or_control_plane_token(
    permission: str,
) -> Callable[..., Awaitable[Authorized]]:
    """Accept a session with ``permission`` **or** the Phase 5 shared token.

    **This is a migration seam with a removal date, not a design.** ADR-0066
    holds the argument in full; the short version is that Phase 5 guarded every
    control-plane write with ``X-Control-Plane-Token`` and said in its own
    docstring that Phase 13 would replace it. Replacing it in one commit would
    leave every running deployment -- and the console BFF, which holds that
    token server-side (ADR-0062) -- unable to configure anything until somebody
    ran the identity bootstrap. So both work, the token path logs a deprecation
    every time it is used, and the token is removed in the release after a
    deployment has an operator.

    The token path is deliberately *not* given a principal. A handler cannot
    pretend to know who acted, and the ``admin_action`` events these routes
    write carry ``actor_label`` rather than an invented actor id.
    """
    declared = permissions.get(permission)

    async def dependency(request: Request, principal: OptionalPrincipalDep) -> Authorized:
        config = settings_of(request)
        supplied = request.headers.get(CONTROL_PLANE_TOKEN_HEADER)

        # The session path is tried first, so a deployment that has migrated
        # never takes the deprecated branch even if a client still sends both.
        if principal is not None:
            await decisions.authorize(
                principal=principal,
                permission=permission,
                resource=Resource(kind=declared.resource_kind or permissions.KIND_TENANT),
                route=_route_label(request),
            )
            return Authorized(principal=principal, used_control_plane_token=False)

        expected = config.control_plane_token.get_secret_value()
        if supplied is not None and hmac.compare_digest(supplied, expected):
            log.warning(
                # Warning, and once per request, on purpose: this is how a
                # deployment finds out it has not migrated yet, and the §25
                # alerting routes on level.
                "control_plane_token.deprecated",
                route=_route_label(request),
                permission=permission,
            )
            return Authorized(principal=None, used_control_plane_token=True)

        raise ProblemDetailError(
            status_code=HTTP_403_FORBIDDEN,
            title="Not permitted",
            detail=(
                f"Supply a bearer token from POST /api/v1/auth/token carrying "
                f"'{permission}'. The {CONTROL_PLANE_TOKEN_HEADER} header is "
                f"accepted during the Phase 13 migration (ADR-0066) and is deprecated."
            ),
            problem_type=f"{PROBLEM_BASE}/forbidden",
            extra={"required_permission": permission},
        )

    return dependency


def authenticated() -> Callable[..., Awaitable[Principal]]:
    """Declare a route that needs a session and no permission.

    For the handful of routes whose subject is **the caller themself** —
    signing out, listing your own sessions, changing your own password. A
    permission for those would be one every session necessarily holds over
    itself, which is a permission that means nothing and a row in the registry
    that teaches a reader the wrong thing about what permissions are for.

    It is also the declaration for a route that decides *which* permission
    applies inside the handler, where two different callers may legitimately act
    for different reasons: ``access.end_impersonation`` is endable by the
    operator inside the session or by the tenant it was taken against, and a
    single declared permission would have to be wrong for one of them.

    Returning the principal rather than ``None`` so the handler can take it from
    this dependency instead of declaring a second one — two dependencies
    resolving the same principal would do the session lookup twice.
    """

    async def dependency(principal: PrincipalDep) -> Principal:
        return principal

    return dependency


def machine(scope: str) -> Callable[[], str]:
    """Declare a route authenticated by an API key rather than a session.

    The enforcement is ``api.key_deps`` — the route depends on ``KeyDep`` and
    calls ``require(scope)``. This marker does not duplicate that check; it
    makes the route's posture *declared*, so the CI checker can tell "machine
    identity, scope X" apart from "nobody has looked at this endpoint".

    An API key authenticates a consumer organisation, not a person
    (``api.key_deps`` makes the distinction), so these routes have no principal,
    no grants and no decision log entries. That is the honest shape of machine
    access and not a gap: a key's scope set is fixed at mint time and visible to
    the tenant that minted it.
    """

    def dependency() -> str:
        return scope

    return dependency


def public() -> Callable[[], None]:
    """Declare a route deliberately unauthenticated.

    A marker with no behaviour, and that is the whole value: it makes "this
    endpoint is open" a decision somebody wrote down in the decorator, visible
    in review and readable by the CI checker, rather than the absence of a
    declaration — which is indistinguishable from forgetting.
    """

    def dependency() -> None:
        return None

    return dependency


def _route_label(request: Request) -> str:
    """``GET /api/v1/complaints/{complaint_id}`` — the templated path.

    The template, never the resolved path. A resolved path puts a complaint id
    in a column that is grouped by in every query against the decision log, and
    duplicates what ``resource_id`` already holds — the same reasoning
    ``test_metrics_cardinality.py`` applies to the HTTP metric's labels.
    """
    route = request.scope.get("route")
    path = getattr(route, "path", None) or request.url.path
    return f"{request.method} {path}"[:160]


def _unauthenticated(error: AuthenticationError) -> ProblemDetailError:
    """One 401 with one message, whatever the cause.

    The cause reaches the log through ``reason``; it does not reach the body.
    ``integrations.keys`` makes the argument for API keys and it holds here: an
    "expired" that differs from "unknown" confirms the session was real to
    somebody working through a stolen token.

    ``CredentialLockedError`` is the deliberate exception — see
    ``identity.errors`` — and it carries ``Retry-After`` because a client that
    is locked out should wait rather than keep trying.
    """
    log.info("authentication.rejected", reason=error.reason)
    if isinstance(error, CredentialLockedError):
        return ProblemDetailError(
            status_code=HTTP_401_UNAUTHORIZED,
            title="Too many attempts",
            detail=str(error),
            problem_type=f"{PROBLEM_BASE}/credential-locked",
            extra={"retry_after_seconds": error.retry_after_seconds},
            headers={"Retry-After": str(error.retry_after_seconds)},
        )
    return ProblemDetailError(
        status_code=HTTP_401_UNAUTHORIZED,
        title="Not authenticated",
        detail="The supplied credentials are not valid.",
        problem_type=f"{PROBLEM_BASE}/unauthenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


def translate(error: IdentityError) -> ProblemDetailError:
    """One mapping from identity errors to the RFC 9457 contract.

    In one place, like ``control_plane._translate``, so a new error kind
    surfaces consistently rather than as whatever the nearest handler
    improvised. The ordering matters: ``CredentialLockedError`` is a subclass of
    ``AuthenticationError``, so it has to be tested first or it would render as
    a bare 401 and the client would never see the retry hint.
    """
    if isinstance(error, AuthorizationError):
        return ProblemDetailError(
            status_code=HTTP_403_FORBIDDEN,
            title="Not permitted",
            # The permission is named, the resource never is. Naming the
            # permission turns a support ticket from "the console is broken"
            # into "I need review:decide on Ward 4"; naming the resource would
            # vary the response with the target, which is the enumeration oracle
            # the gate clause forbids.
            detail=f"This session does not carry '{error.permission}' on that scope.",
            problem_type=f"{PROBLEM_BASE}/forbidden",
            extra={"required_permission": error.permission},
        )
    if isinstance(error, AuthenticationError):
        return _unauthenticated(error)
    if isinstance(error, NotFoundError):
        from nemesis.api.errors import HTTP_404_NOT_FOUND

        return ProblemDetailError(
            status_code=HTTP_404_NOT_FOUND,
            title="Not found",
            detail=str(error),
            problem_type=f"{PROBLEM_BASE}/not-found",
        )
    if isinstance(error, ConflictError):
        from nemesis.api.errors import HTTP_409_CONFLICT

        return ProblemDetailError(
            status_code=HTTP_409_CONFLICT,
            title="Conflict",
            detail=str(error),
            problem_type=f"{PROBLEM_BASE}/conflict",
        )
    if isinstance(error, ProviderError):
        return ProblemDetailError(
            status_code=HTTP_502_BAD_GATEWAY,
            title="Identity provider unavailable",
            detail=str(error),
            problem_type=f"{PROBLEM_BASE}/provider-unavailable",
        )
    if isinstance(error, ValidationError):
        from nemesis.api.errors import HTTP_422_UNPROCESSABLE

        return ProblemDetailError(
            status_code=HTTP_422_UNPROCESSABLE,
            title="Unprocessable",
            detail=str(error),
            problem_type=f"{PROBLEM_BASE}/unprocessable",
        )
    # No `else: 500`. Every subclass of `IdentityError` is listed above, and a
    # new one that reaches here is a mapping somebody forgot — which should be
    # a loud failure in the test that asserts this function is total, not a
    # silent 500 in production.
    raise error


def principal_tenant(principal: Principal) -> uuid.UUID:
    """The tenant a principal acts in. One line, named, because it is a seam.

    Impersonation makes "which tenant" a question with two plausible answers —
    the operator's or the customer's — and the answer is always the customer's:
    the session was issued against their user, in their tenant, and every write
    lands in their data. Spelling it out here means no handler has to decide.
    """
    return principal.tenant_id


#: For the few routes that still accept a tenant header *and* a token — the
#: citizen surfaces. See ``api.deps.require_tenant``.
TenantHeaderDep = Annotated[str | None, Header(alias="X-Tenant-ID")]


__all__ = [
    "AUTHORIZATION_HEADER",
    "BEARER_PREFIX",
    "CONTROL_PLANE_TOKEN_HEADER",
    "Authorized",
    "OptionalPrincipalDep",
    "PrincipalDep",
    "TenantHeaderDep",
    "authenticated",
    "bearer_token",
    "machine",
    "optional_principal",
    "principal_tenant",
    "public",
    "require_principal",
    "requires",
    "requires_or_control_plane_token",
    "resolve_principal",
    "settings_of",
    "translate",
]
