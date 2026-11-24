"""Users, credentials, and the password sign-in path.

**A contractor sub-user is a user with ``kind='contractor'`` and a
``contractor_id``, not a second table.** §16 requires a contractor's actions to
sit beside a department's in the same audit trail and the same decision log, and
two tables would mean every query that answers "who did this" is a union — which
is the query most likely to be written against one table by somebody who did not
know about the other.

**Lockout counts failures on the credential row, not in the cache.** A lockout
that evaporates on restart is a lockout an attacker clears with a deploy, and the
write cost falls on the failing path, which is the one we are least interested
in making fast. ``user_credentials`` documents this; the enforcement is
``authenticate``.

**The timing of a failed sign-in is deliberately uninformative about *why* it
failed, and deliberately informative about nothing else.** An unknown email, a
user with no credential, a deactivated user and a wrong password all take the
same shape of answer: one ``AuthenticationError`` with a distinct ``reason`` for
the log. They do not all take the same *time*, and pretending otherwise would
require a dummy KDF run on the unknown-user path. That is a real mitigation and
it is implemented — ``_waste_time`` — because without it the 50 ms scrypt cost
makes "this address has an account" measurable from across the internet.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.db.models.identity import CredentialPolicy, UserCredential
from nemesis.db.models.organisation import Contractor, Department, User
from nemesis.identity import passwords
from nemesis.identity.errors import (
    AuthenticationError,
    ConflictError,
    CredentialLockedError,
    NotFoundError,
    ValidationError,
)
from nemesis.observability import metrics
from nemesis.observability.logging import get_logger
from nemesis.tenancy.context import tenant_scope

log = get_logger(__name__)

#: What a user *is*. Three kinds, one table — see the module docstring.
KIND_OPERATOR: Final = "operator"
#: A contractor organisation's own sub-user. ``contractor_user`` rather than
#: ``contractor`` for the reason ``permissions.SCOPE_CONTRACTOR`` gives: the bare
#: word is a tenant role name, and this is a kind of *account*.
KIND_CONTRACTOR: Final = "contractor_user"
KIND_SERVICE: Final = "service"
USER_KINDS: Final[tuple[str, ...]] = (KIND_OPERATOR, KIND_CONTRACTOR, KIND_SERVICE)

#: Platform defaults, used when a tenant has written no ``credential_policies``
#: row. Mirrors the column server-defaults; declared here as well because the
#: service must be able to answer "what policy applies" without a row existing,
#: and reading a default out of the schema at runtime is not something
#: SQLAlchemy offers cleanly.
DEFAULT_MIN_LENGTH: Final = 12
DEFAULT_MAX_FAILED_ATTEMPTS: Final = 10
DEFAULT_LOCKOUT_SECONDS: Final = 900

#: Subject prefix for a locally authenticated user. ``users.subject`` holds an
#: identity-provider subject claim; a password user has no provider, so one is
#: minted that cannot collide with any issuer's namespace.
LOCAL_SUBJECT_PREFIX: Final = "local:"


@dataclass(frozen=True, slots=True)
class EffectivePolicy:
    """The password rules in force for one tenant."""

    min_length: int
    max_failed_attempts: int
    lockout_seconds: int
    max_age_days: int


async def effective_policy(session: AsyncSession, *, tenant_id: uuid.UUID) -> EffectivePolicy:
    """The tenant's policy, or the platform defaults where it has none."""
    with tenant_scope(tenant_id):
        row = (
            await session.execute(
                select(CredentialPolicy).where(CredentialPolicy.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()
    if row is None:
        return EffectivePolicy(
            min_length=DEFAULT_MIN_LENGTH,
            max_failed_attempts=DEFAULT_MAX_FAILED_ATTEMPTS,
            lockout_seconds=DEFAULT_LOCKOUT_SECONDS,
            max_age_days=0,
        )
    return EffectivePolicy(
        min_length=row.min_length,
        max_failed_attempts=row.max_failed_attempts,
        lockout_seconds=row.lockout_seconds,
        max_age_days=row.max_age_days,
    )


async def create_user(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    display_name: str,
    email: str | None = None,
    subject: str | None = None,
    kind: str = KIND_OPERATOR,
    role_label: str | None = None,
    department_id: uuid.UUID | None = None,
    contractor_id: uuid.UUID | None = None,
    identity_provider_id: uuid.UUID | None = None,
    password: str | None = None,
) -> User:
    """Create a user, optionally with a local password.

    **A new user has no grants.** That is default-deny made visible: an account
    that could do nothing on creation is an account whose access was granted
    deliberately, by somebody holding ``grants:write``, in a row with their name
    on it. Bundling a role into creation would make the common path "create with
    the usual permissions", and "the usual permissions" is how everybody ends up
    an administrator.
    """
    if kind not in USER_KINDS:
        raise ValidationError(f"unknown user kind {kind!r}; legal kinds are {list(USER_KINDS)}")
    if kind == KIND_CONTRACTOR and contractor_id is None:
        raise ValidationError(
            "a contractor user must name its contractor organisation; without it the "
            "contractor-scoped grants that make the account useful cannot be written"
        )
    if kind != KIND_CONTRACTOR and contractor_id is not None:
        raise ValidationError(
            f"a {kind!r} user may not belong to a contractor organisation; set "
            f"kind='{KIND_CONTRACTOR}' if that is what this account is"
        )
    normalised_email = _normalise_email(email)

    with tenant_scope(tenant_id):
        if department_id is not None:
            found = (
                await session.execute(
                    select(Department.id).where(
                        Department.tenant_id == tenant_id, Department.id == department_id
                    )
                )
            ).scalar_one_or_none()
            if found is None:
                raise NotFoundError(f"no department {department_id} in this tenant")
        if contractor_id is not None:
            found = (
                await session.execute(
                    select(Contractor.id).where(
                        Contractor.tenant_id == tenant_id, Contractor.id == contractor_id
                    )
                )
            ).scalar_one_or_none()
            if found is None:
                raise NotFoundError(f"no contractor {contractor_id} in this tenant")

        if normalised_email is not None:
            clash = (
                await session.execute(
                    select(User.id).where(
                        User.tenant_id == tenant_id, User.email == normalised_email
                    )
                )
            ).scalar_one_or_none()
            if clash is not None:
                raise ConflictError("a user with that email already exists in this tenant")

        row = User(
            tenant_id=tenant_id,
            subject=subject or f"{LOCAL_SUBJECT_PREFIX}{uuid.uuid4()}",
            display_name=display_name,
            email=normalised_email,
            kind=kind,
            role=role_label,
            department_id=department_id,
            contractor_id=contractor_id,
            identity_provider_id=identity_provider_id,
            is_active=True,
        )
        session.add(row)
        await session.flush()

    if password is not None:
        await set_password(
            session, tenant_id=tenant_id, user_id=row.id, password=password, must_change=True
        )
    return row


async def set_password(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    password: str,
    must_change: bool = False,
    now: datetime | None = None,
) -> None:
    """Set or replace a local password. Revoking sessions is the caller's job.

    Deliberately *not* done here, although it is what should usually happen. A
    password change by the user themself should keep their current session alive
    — signing somebody out of the browser they just used to change their password
    is user-hostile — while a reset by an administrator must kill every session,
    because the reason for the reset is usually that somebody else has the old
    one. One function cannot know which, so the two callers in
    ``api.v1.auth`` and ``api.v1.access`` each say.
    """
    moment = now or datetime.now(tz=UTC)
    policy = await effective_policy(session, tenant_id=tenant_id)
    passwords.assert_acceptable(password, min_length=policy.min_length)
    hashed = passwords.hash_password(password)

    with tenant_scope(tenant_id):
        existing = (
            await session.execute(
                select(UserCredential).where(
                    UserCredential.tenant_id == tenant_id,
                    UserCredential.user_id == user_id,
                )
            )
        ).scalar_one_or_none()

        if existing is None:
            await _assert_user(session, tenant_id=tenant_id, user_id=user_id)
            session.add(
                UserCredential(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    algorithm=hashed.algorithm,
                    params=hashed.params,
                    salt=hashed.salt,
                    digest=hashed.digest,
                    must_change=must_change,
                    rotated_at=moment,
                )
            )
            await session.flush()
        else:
            # A successful password change clears a lockout: the person proved
            # they hold the account, and continuing to lock them out would
            # punish the victim of the guessing attempt rather than the
            # attacker. An explicit ``update()`` for the tenancy-guard reason
            # the success path in ``authenticate`` explains.
            await session.execute(
                update(UserCredential)
                .where(
                    UserCredential.tenant_id == tenant_id,
                    UserCredential.id == existing.id,
                )
                .values(
                    algorithm=hashed.algorithm,
                    params=hashed.params,
                    salt=hashed.salt,
                    digest=hashed.digest,
                    must_change=must_change,
                    rotated_at=moment,
                    failed_attempts=0,
                    locked_until=None,
                )
            )


@dataclass(frozen=True, slots=True)
class Authenticated:
    """A verified password sign-in."""

    user: User
    must_change_password: bool


async def authenticate(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    identifier: str,
    password: str,
    now: datetime | None = None,
) -> Authenticated:
    """Verify a password. Every failure is one error type with a reason.

    ``identifier`` is an email or a subject, and which one it is is decided by
    the presence of an ``@`` rather than by a parameter. A sign-in form has one
    field; making the caller declare what the user typed into it would mean the
    console guessing, and guessing wrong on an address like
    ``ops+pune@example.org`` is the kind of bug that only affects some people.
    """
    moment = now or datetime.now(tz=UTC)
    policy = await effective_policy(session, tenant_id=tenant_id)

    # **Two scoped reads rather than one outer join, and the tenancy guard is
    # why.** The join reads naturally -- user left-joined to credential -- but
    # the credential side then carries no literal ``tenant_id`` predicate, only
    # a column-to-column comparison in the ON clause, which scopes the *join*
    # and not the table. ``tenancy.guard`` refuses it, and it is right to: the
    # same statement with the ON clause edited would read every tenant's
    # credentials. ``api.v1.complaints`` hit this exact distinction and resolved
    # it the same way.
    with tenant_scope(tenant_id):
        user_statement = select(User).where(User.tenant_id == tenant_id)
        if "@" in identifier:
            user_statement = user_statement.where(User.email == _normalise_email(identifier))
        else:
            user_statement = user_statement.where(User.subject == identifier)
        user = (await session.execute(user_statement)).scalar_one_or_none()

        credential = None
        if user is not None:
            credential = (
                await session.execute(
                    select(UserCredential).where(
                        UserCredential.tenant_id == tenant_id,
                        UserCredential.user_id == user.id,
                    )
                )
            ).scalar_one_or_none()

    if user is None:
        _waste_time(password)
        metrics.authentication_attempts_total.labels(method="password", outcome="rejected").inc()
        raise AuthenticationError("no such user", reason="unknown_user")

    if credential is None:
        # An SSO-only user being asked for a password. Same answer, different
        # reason: the remedy is "use the sign-in button", and support needs to
        # be able to see that from the log without asking the user to retry.
        _waste_time(password)
        metrics.authentication_attempts_total.labels(method="password", outcome="rejected").inc()
        raise AuthenticationError("no local credential", reason="no_credential")

    if credential.locked_until is not None and credential.locked_until > moment:
        metrics.authentication_attempts_total.labels(method="password", outcome="locked").inc()
        raise CredentialLockedError(
            retry_after_seconds=int((credential.locked_until - moment).total_seconds())
        )

    if not passwords.verify(
        password=password,
        salt=credential.salt,
        digest=credential.digest,
        algorithm=credential.algorithm,
        params=credential.params,
    ):
        await _record_failure(
            session,
            tenant_id=tenant_id,
            credential=credential,
            policy=policy,
            now=moment,
        )
        metrics.authentication_attempts_total.labels(method="password", outcome="rejected").inc()
        raise AuthenticationError("wrong password", reason="bad_password")

    if not user.is_active:
        # Checked *after* the password, on purpose. Checking first would make a
        # deactivated account answer faster than an active one with a wrong
        # password, which turns "is this person still employed here" into
        # something measurable by anybody with their old address.
        metrics.authentication_attempts_total.labels(method="password", outcome="rejected").inc()
        raise AuthenticationError("user deactivated", reason="user_inactive")

    # Explicit ``update()`` statements rather than mutating the loaded row.
    # A flush of an ORM mutation emits ``UPDATE ... WHERE id = ?`` with no
    # tenant predicate, which ``tenancy.guard`` rejects -- correctly, because a
    # primary-key-only write is one nothing can prove was scoped.
    rehash = passwords.needs_rehash(algorithm=credential.algorithm, params=credential.params)
    cleared: dict[str, object] = {
        "failed_attempts": 0,
        "locked_until": None,
        "last_verified_at": moment,
    }
    if rehash:
        # The only moment the plaintext exists alongside a verified
        # credential, and therefore the only moment the cost can be raised
        # without asking the user to type it again.
        upgraded = passwords.hash_password(password)
        cleared |= {
            "algorithm": upgraded.algorithm,
            "params": upgraded.params,
            "salt": upgraded.salt,
            "digest": upgraded.digest,
        }

    with tenant_scope(tenant_id):
        await session.execute(
            update(UserCredential)
            .where(
                UserCredential.tenant_id == tenant_id,
                UserCredential.id == credential.id,
            )
            .values(**cleared)
        )
        await session.execute(
            update(User)
            .where(User.tenant_id == tenant_id, User.id == user.id)
            .values(last_login_at=moment)
        )
    if rehash:
        log.info("credential.rehashed", user_id=str(user.id))

    expired = (
        policy.max_age_days > 0
        and credential.rotated_at is not None
        and credential.rotated_at + timedelta(days=policy.max_age_days) <= moment
    )
    metrics.authentication_attempts_total.labels(method="password", outcome="issued").inc()
    return Authenticated(user=user, must_change_password=credential.must_change or expired)


def _waste_time(password: str) -> None:
    """Burn one KDF's worth of time on a path with no credential to check.

    Without this, an unknown address answers in a millisecond and a known one in
    fifty, which makes "does this person have an account here" a question
    anybody can ask from anywhere. The salt is random and the result is
    discarded; the only thing being produced is the delay.
    """
    passwords.hash_password(password or secrets.token_urlsafe(16))


async def _record_failure(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    credential: UserCredential,
    policy: EffectivePolicy,
    now: datetime,
) -> None:
    """Increment the counter and lock the credential at the threshold.

    The counter resets *with* the lock, so the next lockout needs a fresh run
    of failures rather than one more attempt. A counter that kept climbing
    would mean a single wrong guess after a lockout expires locks the account
    again, which is a denial of service against the legitimate user.

    An explicit ``update()`` for the reason the success path gives: an ORM
    mutation flushes a primary-key-only write that the tenancy guard refuses.
    """
    attempts = credential.failed_attempts + 1
    locking = attempts >= policy.max_failed_attempts
    values: dict[str, object] = (
        {
            "failed_attempts": 0,
            "locked_until": now + timedelta(seconds=policy.lockout_seconds),
        }
        if locking
        else {"failed_attempts": attempts}
    )
    with tenant_scope(tenant_id):
        await session.execute(
            update(UserCredential)
            .where(
                UserCredential.tenant_id == tenant_id,
                UserCredential.id == credential.id,
            )
            .values(**values)
        )
    if locking:
        log.warning(
            "credential.locked",
            user_id=str(credential.user_id),
            seconds=policy.lockout_seconds,
        )


async def deactivate(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    delete_credential: bool = True,
) -> None:
    """Deactivate a user: no sign-in, no credential, grants kept.

    Grants are kept on purpose. An access review asking "what could this person
    do when they left" needs them, and a reactivated account getting its old
    access back silently is prevented by the deactivation itself rather than by
    destroying the record. Sessions are revoked by the caller — see
    ``set_password`` for why that is not done here.
    """
    await _assert_user(session, tenant_id=tenant_id, user_id=user_id)
    with tenant_scope(tenant_id):
        await session.execute(
            update(User)
            .where(User.tenant_id == tenant_id, User.id == user_id)
            .values(is_active=False)
        )
        if delete_credential:
            await session.execute(
                delete(UserCredential).where(
                    UserCredential.tenant_id == tenant_id,
                    UserCredential.user_id == user_id,
                )
            )


async def reactivate(session: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """Reactivate a user. They will have no credential until one is set."""
    await _assert_user(session, tenant_id=tenant_id, user_id=user_id)
    with tenant_scope(tenant_id):
        await session.execute(
            update(User)
            .where(User.tenant_id == tenant_id, User.id == user_id)
            .values(is_active=True)
        )


async def get_user(session: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID) -> User:
    with tenant_scope(tenant_id):
        row = (
            await session.execute(
                select(User).where(User.tenant_id == tenant_id, User.id == user_id)
            )
        ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"no user {user_id} in this tenant")
    return row


async def find_by_subject(
    session: AsyncSession, *, tenant_id: uuid.UUID, subject: str
) -> User | None:
    with tenant_scope(tenant_id):
        return (
            await session.execute(
                select(User).where(User.tenant_id == tenant_id, User.subject == subject)
            )
        ).scalar_one_or_none()


async def list_users(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    contractor_id: uuid.UUID | None = None,
    include_inactive: bool = False,
) -> list[User]:
    """Users, newest first. ``contractor_id`` narrows to one organisation.

    The narrowing exists because ``contractor_users:manage`` is scoped to a
    contractor: a foreman managing their own crew must be able to list it, and
    the list they get must not contain the municipality's staff. The *filter* is
    applied by the caller from its ``Visibility``; this parameter is how it
    reaches the query.
    """
    with tenant_scope(tenant_id):
        statement = select(User).where(User.tenant_id == tenant_id).order_by(User.created_at.desc())
        if contractor_id is not None:
            statement = statement.where(User.contractor_id == contractor_id)
        if not include_inactive:
            statement = statement.where(User.is_active.is_(True))
        return list((await session.execute(statement)).scalars().all())


async def count_users(
    session: AsyncSession, *, tenant_id: uuid.UUID, include_inactive: bool = False
) -> int:
    with tenant_scope(tenant_id):
        statement = select(func.count()).select_from(User).where(User.tenant_id == tenant_id)
        if not include_inactive:
            statement = statement.where(User.is_active.is_(True))
        return int((await session.execute(statement)).scalar_one())


def _normalise_email(email: str | None) -> str | None:
    """Lowercase and strip. No further normalisation, deliberately.

    Stripping dots or ``+tag`` suffixes — "canonicalising" a Gmail address — is
    a guess about somebody else's mail server that is wrong for most of them,
    and two accounts that the provider considers distinct being merged by us is
    worse than two accounts existing.
    """
    if email is None:
        return None
    cleaned = email.strip().lower()
    if not cleaned:
        return None
    if "@" not in cleaned or cleaned.startswith("@") or cleaned.endswith("@"):
        raise ValidationError(f"{email!r} is not an email address")
    return cleaned


async def _assert_user(session: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID) -> None:
    with tenant_scope(tenant_id):
        found = (
            await session.execute(
                select(User.id).where(User.tenant_id == tenant_id, User.id == user_id)
            )
        ).scalar_one_or_none()
    if found is None:
        raise NotFoundError(f"no user {user_id} in this tenant")


__all__ = [
    "DEFAULT_LOCKOUT_SECONDS",
    "DEFAULT_MAX_FAILED_ATTEMPTS",
    "DEFAULT_MIN_LENGTH",
    "KIND_CONTRACTOR",
    "KIND_OPERATOR",
    "KIND_SERVICE",
    "LOCAL_SUBJECT_PREFIX",
    "USER_KINDS",
    "Authenticated",
    "EffectivePolicy",
    "authenticate",
    "count_users",
    "create_user",
    "deactivate",
    "effective_policy",
    "find_by_subject",
    "get_user",
    "list_users",
    "reactivate",
    "set_password",
]
