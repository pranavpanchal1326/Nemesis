"""The centralised decision point, and the log it writes.

**Default-deny is a property of this module's shape, not of a flag.** There is
one function that answers "may this principal do this" — ``authorize`` — and it
returns a ``Decision`` whose ``allowed`` is false unless a grant was found that
both carries the permission and contains the resource. No caller can ask the
question a different way, because ``api.identity_deps`` is the only thing the
routes can reach and it calls this.

**What is logged, and why not everything.** Every denial, and every allow of a
permission the registry marks ``audited``. ``identity.permissions`` argues the
trade in full; the short version is that ``complaints:read`` allows are one row
per list request per officer per refresh, which buys an unreadable table. The
*metric* counts all of them, so "this role is denied constantly" is visible
without the write amplification.

**The log write is not in the request's transaction, and that is deliberate.**
A denial must be recorded even when the request it denied is about to roll back
— and a request that is being denied frequently rolls back, because the handler
raises. So the decision row is written through its own short session, committed
on its own. The cost is that a decision row can exist for an action that never
happened, which is the correct asymmetry for an audit trail: "access was
attempted and refused" is the fact worth keeping, and whether the surrounding
transaction committed is not part of it.

**Impersonation counters are maintained here** rather than by the impersonation
module, because this is the only place that knows whether a decision was
allowed. A tenant reading "support read 14 things and changed nothing" is
reading these two integers.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.db.models.identity import AuthorizationDecision, ImpersonationSession
from nemesis.db.session import session_scope
from nemesis.identity import permissions as perms
from nemesis.identity import roles as role_service
from nemesis.identity.errors import AuthorizationError
from nemesis.identity.scopes import (
    REASON_PLATFORM_ONLY,
    Decision,
    Principal,
    Resource,
    Visibility,
    evaluate,
    is_write_action,
    visibility,
)
from nemesis.observability import metrics
from nemesis.observability.logging import get_correlation_id, get_logger
from nemesis.tenancy.context import tenant_scope

log = get_logger(__name__)

#: How long a decision row is kept before Phase 26's sweep may remove it. Ninety
#: days matches §22.4's EXIF window, and for the same underlying reason: it is
#: long enough to investigate an incident reported a quarter late, and short
#: enough that the table is not a permanent record of every officer's every
#: click. Written onto each row (``purge_after``) rather than applied at sweep
#: time, so changing this value changes what *new* rows promise and leaves the
#: promise made to existing rows intact.
DECISION_RETENTION: Final = timedelta(days=90)

OUTCOME_GRANTED: Final = "granted"
OUTCOME_DENIED: Final = "denied"

#: The route the current request matched, as a template.
#:
#: A ``ContextVar`` for the reason ``observability.logging`` gives about the
#: correlation id: the API serves concurrent requests on one event loop, so a
#: module global would let request B record request A's route. It is set once
#: per request by ``api.identity_deps`` and read by ``_record``.
#:
#: **Why it is ambient rather than a parameter.** It was a parameter first,
#: and the live decision log showed the consequence immediately: the
#: dependency-level checks passed it and the *handler-level* ones — the
#: per-resource decisions, which are the rows an operator traces an incident
#: through — all recorded ``route = NULL``. Threading a ``Request`` into every
#: handler's ``authorize`` call to fix that would put an HTTP object in the
#: argument list of the one function that is called from the CLI and the tests
#: as well. An explicit parameter still wins where one is given.
_current_route: ContextVar[str | None] = ContextVar("nemesis_decision_route", default=None)


def set_route(route: str | None) -> None:
    """Bind the matched route for the rest of this request."""
    _current_route.set(route)


def current_route() -> str | None:
    return _current_route.get()


async def load_principal_grants(
    session: AsyncSession,
    *,
    principal: Principal,
    now: datetime | None = None,
) -> Principal:
    """Attach the principal's live grants. Called once per request.

    Returns a new ``Principal`` rather than mutating: the dataclass is frozen
    because a principal that can be modified after the authorization check is a
    principal that can be widened between two awaits, and that bug is invisible
    in a single-threaded test.
    """
    grants = await role_service.load_live_grants(
        session, tenant_id=principal.tenant_id, user_id=principal.user_id, now=now
    )
    return Principal(
        user_id=principal.user_id,
        tenant_id=principal.tenant_id,
        subject=principal.subject,
        session_id=principal.session_id,
        grants=grants,
        display_name=principal.display_name,
        contractor_id=principal.contractor_id,
        department_id=principal.department_id,
        impersonator_subject=principal.impersonator_subject,
        impersonation_id=principal.impersonation_id,
        impersonation_allows_writes=principal.impersonation_allows_writes,
        is_platform=principal.is_platform,
    )


async def authorize(
    *,
    principal: Principal,
    permission: str,
    resource: Resource | None = None,
    route: str | None = None,
    raise_on_deny: bool = True,
) -> Decision:
    """The only authorization check in the system.

    ``resource=None`` means the permission is tenant-global, and the registry is
    what decides whether that is legitimate: asking for ``review:decide`` with no
    resource is a programming error, not a tenant-wide allowance, and it is
    refused here rather than silently evaluated against an empty resource that
    every path-scoped grant would fail to cover — which would look like a
    permissions bug to whoever hit it.

    ``raise_on_deny`` defaults true because every route wants the 403. The false
    case is for a handler deciding whether to *render* something, where a denial
    is an answer rather than an error.
    """
    declared = perms.get(permission)

    if declared.resource_kind is None:
        subject = resource or Resource(kind=perms.KIND_TENANT)
    elif resource is None:
        raise AuthorizationError(permission=permission, reason_code="resource_required")
    else:
        subject = resource

    if declared.platform_only and not principal.is_platform:
        decision = Decision(allowed=False, permission=permission, reason_code=REASON_PLATFORM_ONLY)
    else:
        decision = evaluate(
            principal=principal,
            permission=permission,
            resource=subject,
            requires_write=is_write_action(permission),
        )

    metrics.authorization_decisions_total.labels(
        permission=permission,
        outcome=OUTCOME_GRANTED if decision.allowed else OUTCOME_DENIED,
        reason=decision.reason_code,
    ).inc()

    if not decision.allowed or declared.audited:
        await _record(
            principal=principal,
            decision=decision,
            resource=subject,
            route=route or current_route(),
        )

    if not decision.allowed:
        # At info, not warning. A denial is the system working — default-deny
        # means the normal state of an officer clicking into a ward they do not
        # cover is a denial — and a warning-level line per denial makes the log
        # unreadable on the day something is genuinely wrong.
        log.info(
            "authorization.denied",
            permission=permission,
            reason=decision.reason_code,
            resource_kind=subject.kind,
            impersonated=principal.is_impersonated,
        )
        if raise_on_deny:
            raise AuthorizationError(permission=permission, reason_code=decision.reason_code)

    return decision


def visible(*, principal: Principal, permission: str) -> Visibility:
    """What a list query may return. Thin wrapper over ``scopes.visibility``.

    Here rather than imported directly by handlers so there is one import path
    for "ask the decision point something" — and so a future need (a metric on
    how often a visibility is unrestricted, a cache) has somewhere to land that
    is not every call site.
    """
    perms.get(permission)
    return visibility(principal=principal, permission=permission)


async def _record(
    *,
    principal: Principal,
    decision: Decision,
    resource: Resource,
    route: str | None,
) -> None:
    """Write one decision row, in its own transaction. See the module docstring.

    Failures here are swallowed after logging, and that is the one place in this
    package where that is the right answer. The alternative — an audit write
    failure turning an otherwise-successful denial into a 500 — would mean a
    full disk on the audit table makes the product unavailable rather than
    unaudited, and an unavailable product is the worse outcome for the citizen
    whose report is in the queue. The ``audit.write_failed`` line is what the
    §25 alert rule watches for.
    """
    now = datetime.now(tz=UTC)
    row = AuthorizationDecision(
        tenant_id=principal.tenant_id,
        decided_at=now,
        user_id=principal.user_id,
        session_id=principal.session_id,
        impersonation_id=principal.impersonation_id,
        operator_subject=principal.impersonator_subject,
        permission=decision.permission,
        allowed=decision.allowed,
        reason_code=decision.reason_code,
        resource_kind=resource.kind,
        resource_id=resource.id,
        matched_scope=decision.scope_description if decision.matched_grant else None,
        matched_grant_id=decision.matched_grant.id if decision.matched_grant else None,
        route=route,
        correlation_id=get_correlation_id(),
        purge_after=now + DECISION_RETENTION,
    )
    try:
        async with session_scope() as audit_session:
            with tenant_scope(principal.tenant_id):
                audit_session.add(row)
                await audit_session.flush()

                if principal.impersonation_id is not None:
                    column = (
                        ImpersonationSession.decisions_allowed
                        if decision.allowed
                        else ImpersonationSession.decisions_denied
                    )
                    await audit_session.execute(
                        update(ImpersonationSession)
                        .where(
                            ImpersonationSession.tenant_id == principal.tenant_id,
                            ImpersonationSession.id == principal.impersonation_id,
                        )
                        .values({column: column + 1})
                    )
    except Exception as exc:
        log.error(
            "audit.write_failed",
            table="authorization_decisions",
            permission=decision.permission,
            allowed=decision.allowed,
            error=str(exc),
        )


async def recent(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    limit: int = 100,
    user_id: uuid.UUID | None = None,
    allowed: bool | None = None,
) -> list[AuthorizationDecision]:
    """The tenant's own decision log, newest first.

    Exposed through ``decisions:read`` so an administrator can answer "who tried
    to open Ward 7 last night" without an engineer running SQL — which is the
    §27 complaint the critique log makes about support tooling, answered for the
    one surface Phase 13 owns.
    """
    from sqlalchemy import select

    with tenant_scope(tenant_id):
        statement = (
            select(AuthorizationDecision)
            .where(AuthorizationDecision.tenant_id == tenant_id)
            .order_by(AuthorizationDecision.decided_at.desc())
            .limit(min(limit, 500))
        )
        if user_id is not None:
            statement = statement.where(AuthorizationDecision.user_id == user_id)
        if allowed is not None:
            statement = statement.where(AuthorizationDecision.allowed.is_(allowed))
        return list((await session.execute(statement)).scalars().all())


__all__ = [
    "DECISION_RETENTION",
    "OUTCOME_DENIED",
    "OUTCOME_GRANTED",
    "authorize",
    "current_route",
    "load_principal_grants",
    "recent",
    "set_route",
    "visible",
]
