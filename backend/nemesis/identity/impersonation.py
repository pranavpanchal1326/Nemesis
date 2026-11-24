"""Audited support access: acting as a tenant user, visibly and with an end.

**The gate clause is "every impersonation session is visible to the impersonated
tenant", and three mechanisms carry it** — deliberately three, because any one
of them alone fails in a way the others cover:

1. **A row scoped to the impersonated tenant** (``impersonation_sessions``),
   readable through ``impersonation:read``, which is a *tenant* permission and
   not a platform one. Support cannot hold it; the customer can.
2. **An event in the impersonated tenant's own chain**
   (``impersonation_started``). The row could be deleted by somebody with
   database access; the chain is hash-linked, so a removal is detectable by the
   integrity sweep that already runs.
3. **Every decision the session makes, in the decision log**, carrying
   ``operator_subject``. The row says access was granted; this says what was
   done with it.

**Read-only by default.** ``allows_writes`` is false unless the request said
otherwise, and ``identity.scopes.evaluate`` refuses a write on a read-only
impersonation even where the impersonated user could perform it. Most support
work is looking at what the customer is looking at. A session that can also
*act* is a different request with a different justification, and the asymmetry
is what makes "support changed our data" a question with a definite answer
rather than a trust exercise.

**The session is capped at start and cannot be extended.** ``ends_at`` is
written once; a longer session is a new session with a new justification. An
extendable window is an unbounded window with extra steps, and the extension is
exactly the step that gets skipped at 03:00.

**What this module cannot do.** It cannot impersonate a user in the platform
tenant (support engineers may not become each other), it cannot impersonate a
deactivated user, and it cannot grant the impersonated user's *platform*
permissions — a tenant user has none, so there is nothing to inherit, and the
check is written anyway because the day somebody adds a platform-permission
grant to a customer tenant is the day that reasoning stops holding on its own.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.db.models.identity import ImpersonationSession
from nemesis.db.models.organisation import User
from nemesis.domain.constants import SYSTEM_TENANT_ID
from nemesis.events.store import EventStore
from nemesis.identity import sessions as session_service
from nemesis.identity.errors import NotFoundError, ValidationError
from nemesis.identity.scopes import Principal
from nemesis.observability import metrics
from nemesis.observability.logging import get_logger
from nemesis.tenancy.context import tenant_scope

log = get_logger(__name__)

#: The longest a support session may run. Two hours: long enough to work
#: through a ticket with the customer on the phone, short enough that a forgotten
#: session closes itself before the next shift.
MAX_SESSION: Final = timedelta(hours=2)
DEFAULT_SESSION: Final = timedelta(minutes=30)

#: Why a session ended. A closed vocabulary because the tenant's own view of
#: "what happened" groups by it.
ENDED_BY_OPERATOR: Final = "ended_by_operator"
ENDED_BY_TENANT: Final = "ended_by_tenant"
ENDED_EXPIRED: Final = "expired"


@dataclass(frozen=True, slots=True)
class StartedImpersonation:
    """An open support session and the tokens that act through it."""

    impersonation_id: uuid.UUID
    tenant_id: uuid.UUID
    subject_user_id: uuid.UUID
    ends_at: datetime
    allows_writes: bool
    issued: session_service.IssuedSession


async def start(
    session: AsyncSession,
    *,
    secret: str,
    operator: Principal,
    target_tenant_id: uuid.UUID,
    target_user_id: uuid.UUID,
    justification: str,
    access_ttl_seconds: int,
    duration: timedelta | None = None,
    allows_writes: bool = False,
    ticket_ref: str | None = None,
    now: datetime | None = None,
) -> StartedImpersonation:
    """Open an audited session acting as ``target_user_id``.

    The caller has already been authorized for ``impersonation:start`` — this
    function does not re-check, for the reason ``roles.delegate`` gives about
    passing the delegator's grants in: a second check here would be a second
    answer to the same question, and two answers that can disagree is worse than
    one.

    The refresh token's lifetime is clamped to the session's remaining window.
    Without that, a thirty-minute support session hands out a seven-day refresh
    token, and the cap becomes a suggestion the client may ignore.
    """
    moment = now or datetime.now(tz=UTC)

    if not justification or len(justification.strip()) < 30:
        raise ValidationError(
            "an impersonation needs a justification of at least 30 characters — a "
            "ticket and a symptom. 'debugging' is not one, and the tenant reading "
            "this row six months from now is the person it is written for"
        )
    if target_tenant_id == SYSTEM_TENANT_ID:
        raise ValidationError(
            "the reserved platform tenant cannot be impersonated; support engineers "
            "may not become each other"
        )
    if operator.tenant_id != SYSTEM_TENANT_ID:
        raise ValidationError(
            "impersonation is a platform capability and the operator is not in the platform tenant"
        )

    window = duration or DEFAULT_SESSION
    if window > MAX_SESSION:
        raise ValidationError(
            f"a support session may run at most {int(MAX_SESSION.total_seconds() // 60)} "
            f"minutes. Start another one, with a justification that says why the first "
            f"was not enough"
        )
    if window <= timedelta(0):
        raise ValidationError("a support session must have a positive duration")

    with tenant_scope(target_tenant_id):
        target = (
            await session.execute(
                select(User).where(User.tenant_id == target_tenant_id, User.id == target_user_id)
            )
        ).scalar_one_or_none()
    if target is None:
        raise NotFoundError(f"no user {target_user_id} in that tenant")
    if not target.is_active:
        raise ValidationError(
            "that user is deactivated. Impersonating a deactivated account would "
            "produce access the account itself does not have"
        )

    ends_at = moment + window
    row = ImpersonationSession(
        tenant_id=target_tenant_id,
        subject_user_id=target.id,
        operator_subject=operator.subject,
        operator_user_id=operator.user_id,
        operator_tenant_id=operator.tenant_id,
        justification=justification.strip(),
        ticket_ref=ticket_ref,
        allows_writes=allows_writes,
        started_at=moment,
        ends_at=ends_at,
    )
    with tenant_scope(target_tenant_id):
        session.add(row)
        await session.flush()

        # Into the *impersonated* tenant's chain. See the module docstring.
        await EventStore(session).append(
            entity_id=target_tenant_id,
            event_type="impersonation_started",
            tenant_id=target_tenant_id,
            actor_id=operator.user_id,
            actor_role="platform_support",
            payload={
                "operator_subject": operator.subject,
                "operator_tenant_id": operator.tenant_id,
                "subject_user_id": target.id,
                "justification": row.justification,
                "ticket_ref": ticket_ref,
                "allows_writes": allows_writes,
                "ends_at": ends_at,
            },
        )

    issued = await session_service.issue(
        session,
        secret=secret,
        user=target,
        access_ttl_seconds=min(access_ttl_seconds, int(window.total_seconds())),
        refresh_ttl_seconds=int(window.total_seconds()),
        method=session_service.METHOD_IMPERSONATION,
        impersonation_id=row.id,
        operator_subject=operator.subject,
        now=moment,
    )

    metrics.impersonation_sessions_active.inc()
    log.warning(
        # Warning, not info, and it is the only authorization event in this
        # package logged above info. An impersonation is rare, is always worth a
        # human knowing about, and §25's alerting routes on level.
        "impersonation.started",
        tenant_id=str(target_tenant_id),
        operator=operator.subject,
        subject_user_id=str(target.id),
        allows_writes=allows_writes,
        ends_at=ends_at.isoformat(),
    )
    return StartedImpersonation(
        impersonation_id=row.id,
        tenant_id=target_tenant_id,
        subject_user_id=target.id,
        ends_at=ends_at,
        allows_writes=allows_writes,
        issued=issued,
    )


async def end(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    impersonation_id: uuid.UUID,
    reason: str,
    now: datetime | None = None,
) -> None:
    """Close a session, revoke its tokens, and record what it touched.

    Idempotent: ending an ended session is a no-op. Three callers reach this —
    the operator finishing, the tenant cutting it off, and the sweep expiring it
    — and two of them can arrive at the same moment.
    """
    moment = now or datetime.now(tz=UTC)
    with tenant_scope(tenant_id):
        row = (
            await session.execute(
                select(ImpersonationSession).where(
                    ImpersonationSession.tenant_id == tenant_id,
                    ImpersonationSession.id == impersonation_id,
                )
            )
        ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"no impersonation session {impersonation_id} for this tenant")
    if row.ended_at is not None:
        return

    with tenant_scope(tenant_id):
        await session.execute(
            update(ImpersonationSession)
            .where(
                ImpersonationSession.tenant_id == tenant_id,
                ImpersonationSession.id == impersonation_id,
            )
            .values(ended_at=moment, ended_reason=reason)
        )
        await EventStore(session).append(
            entity_id=tenant_id,
            event_type="impersonation_ended",
            tenant_id=tenant_id,
            actor_id=row.operator_user_id,
            actor_role="platform_support",
            payload={
                "operator_subject": row.operator_subject,
                "subject_user_id": row.subject_user_id,
                "reason": reason,
                "decisions_allowed": row.decisions_allowed,
                "decisions_denied": row.decisions_denied,
            },
        )

    # Every session minted under this impersonation, not just the one the
    # operator is holding. A refresh carries the impersonation forward
    # (`sessions.rotate` says why), so ending the impersonation has to reach the
    # whole lineage or the operator keeps working past the cap.
    await _revoke_sessions(
        session, tenant_id=tenant_id, impersonation_id=impersonation_id, now=moment
    )

    metrics.impersonation_sessions_active.dec()
    log.warning(
        "impersonation.ended",
        tenant_id=str(tenant_id),
        operator=row.operator_subject,
        reason=reason,
        decisions_allowed=row.decisions_allowed,
        decisions_denied=row.decisions_denied,
    )


async def _revoke_sessions(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    impersonation_id: uuid.UUID,
    now: datetime,
) -> None:
    from nemesis.db.models.identity import UserSession

    with tenant_scope(tenant_id):
        await session.execute(
            update(UserSession)
            .where(
                UserSession.tenant_id == tenant_id,
                UserSession.impersonation_id == impersonation_id,
                UserSession.revoked_at.is_(None),
            )
            .values(
                revoked_at=now,
                revoked_reason=session_service.REVOKED_IMPERSONATION_ENDED,
            )
        )


async def get(
    session: AsyncSession, *, tenant_id: uuid.UUID, impersonation_id: uuid.UUID
) -> ImpersonationSession:
    with tenant_scope(tenant_id):
        row = (
            await session.execute(
                select(ImpersonationSession).where(
                    ImpersonationSession.tenant_id == tenant_id,
                    ImpersonationSession.id == impersonation_id,
                )
            )
        ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"no impersonation session {impersonation_id} for this tenant")
    return row


async def is_live(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    impersonation_id: uuid.UUID,
    now: datetime | None = None,
) -> ImpersonationSession | None:
    """The session if it is open and unexpired, else ``None``.

    Read on every request made through an impersonated session — the token's
    ``imp`` claim is checked against this. That is a second indexed read on top
    of the session read, and it is worth it for the same reason the first one is:
    without it, ``ends_at`` is a number in a row that nothing enforces, and the
    cap this module is built around would hold only until the first access token
    outlived it.
    """
    moment = now or datetime.now(tz=UTC)
    with tenant_scope(tenant_id):
        row = (
            await session.execute(
                select(ImpersonationSession).where(
                    ImpersonationSession.tenant_id == tenant_id,
                    ImpersonationSession.id == impersonation_id,
                    ImpersonationSession.ended_at.is_(None),
                )
            )
        ).scalar_one_or_none()
    if row is None or row.ends_at <= moment:
        return None
    return row


async def list_for_tenant(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    limit: int = 50,
    only_open: bool = False,
) -> list[ImpersonationSession]:
    """What support has done in this tenant — the customer's own view."""
    with tenant_scope(tenant_id):
        statement = (
            select(ImpersonationSession)
            .where(ImpersonationSession.tenant_id == tenant_id)
            .order_by(ImpersonationSession.started_at.desc())
            .limit(min(limit, 200))
        )
        if only_open:
            statement = statement.where(ImpersonationSession.ended_at.is_(None))
        return list((await session.execute(statement)).scalars().all())


async def expire_overdue(session: AsyncSession, *, now: datetime | None = None) -> int:
    """End every session past its cap. Returns how many.

    Cross-tenant by construction — it sweeps the deployment — so it reads
    without a tenant scope and then ends each session inside one. The
    alternative, a sweep per tenant, makes the job's cost proportional to the
    customer count rather than to the number of open sessions, and there are
    almost never any.
    """
    from nemesis.tenancy.guard import TENANT_SCOPE_EXEMPT

    moment = now or datetime.now(tz=UTC)
    # tenant-scope-exempt: a deployment-wide sweep over open support sessions.
    # There is no single tenant to scope it to, and missing one tenant's overdue
    # session is the failure this exists to prevent.
    overdue = list(
        (
            await session.execute(
                # tenant-scope-exempt: see above.
                select(ImpersonationSession.tenant_id, ImpersonationSession.id)
                .where(
                    ImpersonationSession.ended_at.is_(None),
                    ImpersonationSession.ends_at <= moment,
                )
                .execution_options(**{TENANT_SCOPE_EXEMPT: True})
            )
        ).tuples()
    )
    for tenant_id, impersonation_id in overdue:
        await end(
            session,
            tenant_id=tenant_id,
            impersonation_id=impersonation_id,
            reason=ENDED_EXPIRED,
            now=moment,
        )
    return len(overdue)


__all__ = [
    "DEFAULT_SESSION",
    "ENDED_BY_OPERATOR",
    "ENDED_BY_TENANT",
    "ENDED_EXPIRED",
    "MAX_SESSION",
    "StartedImpersonation",
    "end",
    "expire_overdue",
    "get",
    "is_live",
    "list_for_tenant",
    "start",
]
