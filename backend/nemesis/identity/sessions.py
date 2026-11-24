"""Sign-in, refresh rotation, reuse detection, and revocation.

**The access token is checked against a row on every request, and ADR-0066 is
the argument for it.** The short version: a signature-only check cannot be
revoked before expiry, and "revocation" is a Phase 13 ship line. One indexed
primary-key read on a request that is already opening a connection buys
immediate revocation, which is what "a compromised account is locked out now"
means. The read is ``resolve``.

**Refresh rotation keeps the old row.** Every exchange mints a new token, writes
a new row, and marks the old one revoked with ``rotated`` — it does not update
the old row's digest. That is what makes a *replay* detectable: a presented
token whose digest matches an already-revoked row is either a retry after a lost
response or a stolen token being used behind the legitimate holder, and because
those are indistinguishable from here, both get the same answer — revoke the
whole family and make everyone sign in again. The alternative (accept the
replay, assume a dropped response) is the design where a stolen refresh token
works forever.

That answer has a cost worth naming: a client whose network drops the response
to a refresh loses its session and must sign in again. For a console on a
municipal desktop that is an annoyance; for the field app it lands in a dead
zone, which is exactly where §E21 says the product has to work. The mitigation
is in the client, not here — the field app's queue (ADR-0056) is a durable
outbox keyed by idempotency key, so a submission survives the re-authentication
rather than depending on it.

**Everything here takes a session and a clock, and neither is ambient.** The
clock is a parameter with a default so the reuse-detection tests can place two
exchanges a microsecond apart without sleeping, and so an expiry test does not
depend on wall time.
"""

from __future__ import annotations

import ipaddress
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.db.models.identity import UserSession
from nemesis.db.models.organisation import User
from nemesis.identity import tokens
from nemesis.identity.errors import AuthenticationError
from nemesis.tenancy.context import tenant_scope
from nemesis.tenancy.guard import TENANT_SCOPE_EXEMPT

#: How a session was authenticated. Free text in the column, named here so the
#: three writers agree.
METHOD_PASSWORD: Final = "password"
METHOD_OIDC: Final = "oidc"
METHOD_IMPERSONATION: Final = "impersonation"

#: Revocation reasons. A closed vocabulary because an operator reads this column
#: during an incident and "why did everybody get signed out" must have the same
#: answer every time it happens.
REVOKED_ROTATED: Final = "rotated"
REVOKED_SIGNED_OUT: Final = "signed_out"
REVOKED_REUSE: Final = "reuse_detected"
REVOKED_USER_DEACTIVATED: Final = "user_deactivated"
REVOKED_ADMIN: Final = "revoked_by_admin"
REVOKED_IMPERSONATION_ENDED: Final = "impersonation_ended"
REVOKED_CREDENTIAL_CHANGED: Final = "credential_changed"


@dataclass(frozen=True, slots=True)
class IssuedSession:
    """A new session and the two tokens that address it.

    ``refresh_token`` exists on this object and nowhere else — the same
    discipline ``integrations.keys.MintedKey`` applies to an API key secret. The
    database holds a digest, so a leaked refresh token cannot be recovered from
    a backup, and "we can look it up for you" is not a sentence anybody can say.
    """

    session_id: uuid.UUID
    family_id: uuid.UUID
    access_token: str
    access_expires_at: datetime
    refresh_token: str
    refresh_expires_at: datetime


@dataclass(frozen=True, slots=True)
class ResolvedSession:
    """A live session, as the request layer needs it."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    subject: str
    display_name: str | None
    contractor_id: uuid.UUID | None
    department_id: uuid.UUID | None
    user_kind: str
    impersonation_id: uuid.UUID | None
    method: str


def truncate_network(address: str | None) -> str | None:
    """An IPv4 /24 or an IPv6 /48, or ``None``.

    §22 treats an officer's location as personal data, and a session table is
    not an access log. A /24 answers "did this session move cities" and does not
    answer "which house". Anything unparseable returns ``None`` rather than the
    raw string: a value we could not interpret is a value we should not store,
    and a forwarded-header spoof is the most likely source of one.
    """
    if not address:
        return None
    try:
        parsed = ipaddress.ip_address(address.strip())
    except ValueError:
        return None
    if parsed.version == 4:
        network = ipaddress.ip_network(f"{parsed}/24", strict=False)
    else:
        network = ipaddress.ip_network(f"{parsed}/48", strict=False)
    return str(network)


async def issue(
    session: AsyncSession,
    *,
    secret: str,
    user: User,
    access_ttl_seconds: int,
    refresh_ttl_seconds: int,
    method: str,
    issuer: str = tokens.DEFAULT_ISSUER,
    audience: str = tokens.DEFAULT_AUDIENCE,
    impersonation_id: uuid.UUID | None = None,
    operator_subject: str | None = None,
    client_network: str | None = None,
    client_kind: str | None = None,
    family_id: uuid.UUID | None = None,
    parent_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> IssuedSession:
    """Create a session row and mint both tokens against it.

    The row is written *before* the access token is minted, because the token
    carries the row's id. Doing it the other way round would mean generating an
    id client-side and hoping the insert succeeds, and a token naming a session
    that does not exist is a token that fails every request with a reason
    nobody can diagnose.
    """
    moment = now or datetime.now(tz=UTC)
    refresh_plaintext, refresh_digest = tokens.mint_refresh_token()
    row = UserSession(
        tenant_id=user.tenant_id,
        user_id=user.id,
        refresh_digest=refresh_digest,
        family_id=family_id or uuid.uuid4(),
        parent_id=parent_id,
        issued_at=moment,
        expires_at=moment + timedelta(seconds=refresh_ttl_seconds),
        method=method,
        impersonation_id=impersonation_id,
        client_network=truncate_network(client_network),
        client_kind=client_kind,
    )
    with tenant_scope(user.tenant_id):
        session.add(row)
        await session.flush()

    access_token, access_expires = tokens.mint_access_token(
        secret=secret,
        user_id=user.id,
        tenant_id=user.tenant_id,
        session_id=row.id,
        subject=user.subject,
        ttl_seconds=access_ttl_seconds,
        issuer=issuer,
        audience=audience,
        impersonation_id=impersonation_id,
        operator_subject=operator_subject,
        now=moment,
    )
    return IssuedSession(
        session_id=row.id,
        family_id=row.family_id,
        access_token=access_token,
        access_expires_at=access_expires,
        refresh_token=refresh_plaintext,
        refresh_expires_at=row.expires_at,
    )


async def resolve(
    session: AsyncSession,
    *,
    claims: tokens.AccessClaims,
    now: datetime | None = None,
) -> ResolvedSession:
    """Confirm the session named by a verified token is still live.

    Joined to ``users`` in one statement rather than two reads. The request needs
    both — the session to confirm it is live, the user to build a principal — and
    two round trips on the hottest path in the system is a cost paid per request
    for the rest of the deployment's life.

    Every failure is one ``AuthenticationError`` with a distinct ``reason``. The
    reason reaches the log and the metric; the response says only "not
    authenticated", for the reason ``errors`` states.
    """
    moment = now or datetime.now(tz=UTC)
    with tenant_scope(claims.tenant_id):
        row = (
            await session.execute(
                select(UserSession, User)
                .join(User, User.id == UserSession.user_id)
                .where(
                    UserSession.tenant_id == claims.tenant_id,
                    UserSession.id == claims.session_id,
                    User.tenant_id == claims.tenant_id,
                )
            )
        ).one_or_none()

    if row is None:
        raise AuthenticationError("no such session", reason="session_unknown")

    user_session, user = row

    # The token's tenant claim and the row's tenant must agree. They cannot
    # disagree through any code path this package has — the predicate above
    # already filtered on it — which is exactly why the check is cheap and worth
    # keeping: it is the assertion that would catch a future `resolve` written
    # without the tenant predicate.
    if user_session.tenant_id != claims.tenant_id or user.tenant_id != claims.tenant_id:
        raise AuthenticationError("session is not this tenant's", reason="tenant_mismatch")
    if user_session.user_id != claims.user_id:
        raise AuthenticationError("session is not this user's", reason="user_mismatch")
    if user_session.revoked_at is not None:
        raise AuthenticationError("session revoked", reason="session_revoked")
    if user_session.expires_at <= moment:
        raise AuthenticationError("session expired", reason="session_expired")
    if not user.is_active:
        raise AuthenticationError("user deactivated", reason="user_inactive")

    return ResolvedSession(
        id=user_session.id,
        tenant_id=user_session.tenant_id,
        user_id=user.id,
        subject=user.subject,
        display_name=user.display_name,
        contractor_id=user.contractor_id,
        department_id=user.department_id,
        user_kind=user.kind,
        impersonation_id=user_session.impersonation_id,
        method=user_session.method,
    )


async def touch(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    now: datetime | None = None,
) -> None:
    """Record that this session was used, to the minute.

    Not on every request — the caller decides, and the API layer only calls it
    when ``last_seen_at`` is more than a minute stale. "Who is currently signed
    in" is worth answering; a write on every read to answer it to the
    millisecond is not, and the row is on the hot path of every authenticated
    request in the system.
    """
    moment = now or datetime.now(tz=UTC)
    with tenant_scope(tenant_id):
        await session.execute(
            update(UserSession)
            .where(UserSession.tenant_id == tenant_id, UserSession.id == session_id)
            .values(last_seen_at=moment)
        )


async def rotate(
    session: AsyncSession,
    *,
    secret: str,
    presented_refresh_token: str,
    access_ttl_seconds: int,
    refresh_ttl_seconds: int,
    issuer: str = tokens.DEFAULT_ISSUER,
    audience: str = tokens.DEFAULT_AUDIENCE,
    client_network: str | None = None,
    client_kind: str | None = None,
    now: datetime | None = None,
) -> IssuedSession:
    """Exchange a refresh token for a new pair, detecting replay.

    The lookup is tenant-exempt and it has to be: a refresh token is presented
    with no access token and no tenant header, so this read is what *determines*
    the tenant — the same situation ``integrations.keys.resolve`` is in, and
    documented the same way.
    """
    moment = now or datetime.now(tz=UTC)
    digest = tokens.digest_refresh_token(presented_refresh_token)

    # tenant-scope-exempt: this is the lookup that determines the tenant. The
    # digest is globally unique (`uq_user_sessions_refresh_digest`), so the row
    # it returns is the only row that could match.
    row = (
        await session.execute(
            # tenant-scope-exempt: resolves which tenant is refreshing; see above.
            select(UserSession)
            .where(UserSession.refresh_digest == digest)
            .execution_options(**{TENANT_SCOPE_EXEMPT: True})
        )
    ).scalar_one_or_none()

    if row is None:
        raise AuthenticationError("unknown refresh token", reason="refresh_unknown")

    if row.revoked_at is not None:
        # A token that was already exchanged. Either a replay or a theft, and
        # from here they are the same observation — so the response is the
        # strong one. See the module docstring on the cost of this choice.
        await revoke_family(
            session,
            tenant_id=row.tenant_id,
            family_id=row.family_id,
            reason=REVOKED_REUSE,
            now=moment,
        )
        with tenant_scope(row.tenant_id):
            await session.execute(
                update(UserSession)
                .where(UserSession.tenant_id == row.tenant_id, UserSession.id == row.id)
                .values(reuse_detected_at=moment)
            )
        raise AuthenticationError("refresh token already used", reason="refresh_reuse_detected")

    if row.expires_at <= moment:
        raise AuthenticationError("refresh token expired", reason="refresh_expired")

    with tenant_scope(row.tenant_id):
        user = (
            await session.execute(
                select(User).where(User.tenant_id == row.tenant_id, User.id == row.user_id)
            )
        ).scalar_one_or_none()
    if user is None or not user.is_active:
        raise AuthenticationError("user deactivated", reason="user_inactive")

    with tenant_scope(row.tenant_id):
        await session.execute(
            update(UserSession)
            .where(UserSession.tenant_id == row.tenant_id, UserSession.id == row.id)
            .values(revoked_at=moment, revoked_reason=REVOKED_ROTATED)
        )

    # The rotated session inherits the family, the method and the impersonation
    # it was created under. Inheriting the impersonation matters: a support
    # session that could shed its `impersonation_id` by refreshing would become
    # an ordinary session acting as the customer, with nothing in the decision
    # log saying so.
    return await issue(
        session,
        secret=secret,
        user=user,
        access_ttl_seconds=access_ttl_seconds,
        refresh_ttl_seconds=refresh_ttl_seconds,
        method=row.method,
        issuer=issuer,
        audience=audience,
        impersonation_id=row.impersonation_id,
        client_network=client_network or row.client_network,
        client_kind=client_kind or row.client_kind,
        family_id=row.family_id,
        parent_id=row.id,
        now=moment,
    )


async def revoke(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    reason: str,
    now: datetime | None = None,
) -> None:
    """Revoke one session. Idempotent: revoking a revoked session is a no-op.

    Idempotent on purpose, like ``integrations.keys.revoke``: the caller's intent
    ("this session must not work") is already satisfied, and a sign-out button
    clicked twice should not produce an error the user has to read.
    """
    moment = now or datetime.now(tz=UTC)
    with tenant_scope(tenant_id):
        await session.execute(
            update(UserSession)
            .where(
                UserSession.tenant_id == tenant_id,
                UserSession.id == session_id,
                UserSession.revoked_at.is_(None),
            )
            .values(revoked_at=moment, revoked_reason=reason)
        )


async def revoke_family(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    family_id: uuid.UUID,
    reason: str,
    now: datetime | None = None,
) -> int:
    """Revoke every live session descended from one sign-in. Returns the count."""
    moment = now or datetime.now(tz=UTC)
    with tenant_scope(tenant_id):
        result = await session.execute(
            update(UserSession)
            .where(
                UserSession.tenant_id == tenant_id,
                UserSession.family_id == family_id,
                UserSession.revoked_at.is_(None),
            )
            .values(revoked_at=moment, revoked_reason=reason)
        )
    return int(getattr(result, "rowcount", 0) or 0)


async def revoke_for_user(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    reason: str,
    except_session_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> int:
    """Revoke every live session for one user. Returns the count.

    Called on deactivation, on a password change, and by an administrator
    answering "sign them out everywhere". One statement rather than a loop over
    families: the administrator pressed one button and the result must be
    atomic, or a session minted between two iterations survives it.

    ``except_session_id`` is for the password change a user makes themselves.
    Every other session must die -- a password change is the response to
    suspecting somebody else holds it -- while signing the person out of the
    browser they just used is user-hostile and teaches them not to change
    passwords. Expressed as an exclusion inside the one statement rather than
    as a revoke-then-restore, which would leave a window in which the
    caller's own session was revoked.
    """
    moment = now or datetime.now(tz=UTC)
    with tenant_scope(tenant_id):
        statement = update(UserSession).where(
            UserSession.tenant_id == tenant_id,
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
        )
        if except_session_id is not None:
            statement = statement.where(UserSession.id != except_session_id)
        result = await session.execute(statement.values(revoked_at=moment, revoked_reason=reason))
    return int(getattr(result, "rowcount", 0) or 0)


async def list_for_user(
    session: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> list[UserSession]:
    """Every session, live or not, newest first — "where am I signed in"."""
    with tenant_scope(tenant_id):
        return list(
            (
                await session.execute(
                    select(UserSession)
                    .where(UserSession.tenant_id == tenant_id, UserSession.user_id == user_id)
                    .order_by(UserSession.issued_at.desc())
                )
            )
            .scalars()
            .all()
        )


__all__ = [
    "METHOD_IMPERSONATION",
    "METHOD_OIDC",
    "METHOD_PASSWORD",
    "REVOKED_ADMIN",
    "REVOKED_CREDENTIAL_CHANGED",
    "REVOKED_IMPERSONATION_ENDED",
    "REVOKED_REUSE",
    "REVOKED_ROTATED",
    "REVOKED_SIGNED_OUT",
    "REVOKED_USER_DEACTIVATED",
    "IssuedSession",
    "ResolvedSession",
    "issue",
    "list_for_user",
    "resolve",
    "revoke",
    "revoke_family",
    "revoke_for_user",
    "rotate",
    "touch",
    "truncate_network",
]
