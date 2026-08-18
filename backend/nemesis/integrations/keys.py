"""Minting, verifying, and accounting for API keys.

**The key format is ``nem_<prefix>_<secret>``.** Three parts, each earning its
place:

``nem_``
    A recognisable marker. Secret scanners — including this repository's own
    pre-commit ``gitleaks`` hook — match on prefixes, and a key that looks like
    an arbitrary base32 blob is one that gets committed to a customer's public
    repository and never flagged. Making our own credentials greppable is a
    control that costs four characters.
``<prefix>``
    Twelve characters, stored in the clear. It is what a log line, a support
    ticket, and the usage rollup name the key by, so a consumer can be discussed
    without anybody pasting the secret into a chat window in order to identify
    which one they mean.
``<secret>``
    256 bits from ``secrets.token_bytes``, shown exactly once.

**Verification is a single indexed lookup on the digest, not a scan.** The
alternative — fetch every key for a tenant and compare — is O(keys) per request
on the hottest path and, worse, needs the tenant *before* the key is resolved,
which is backwards: the key is what names the tenant. Hashing the presented
secret and looking up the digest is one index probe and reveals nothing about
which keys exist.

**Timing.** The digest lookup either finds a row or does not, and a missing row
returns before any comparison happens — so the "wrong key" path is measurably
faster than the "right key" path. That leaks nothing useful: an attacker learns
whether their guess was a real key, which is the answer the 401 gives them
anyway. Where constant time genuinely matters is comparing a *known* expected
value, which is what ``control_plane`` does with ``hmac.compare_digest`` for the
shared token, and this module does not have that shape.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Final

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.db.models.integration import KEY_PREFIX_LENGTH, ApiKey, ApiKeyUsage
from nemesis.integrations.errors import NotFoundError, ValidationError
from nemesis.tenancy.context import tenant_scope
from nemesis.tenancy.guard import TENANT_SCOPE_EXEMPT

#: Marker prefix. See the module docstring — this is a secret-scanner affordance.
KEY_MARKER: Final = "nem"

#: Bytes of entropy in the secret half. 32 bytes is 256 bits; there is no
#: meaningful attack against it, which is the entire justification for the
#: unsalted SHA-256 at rest.
SECRET_BYTES: Final = 32

#: What a key may do. Declared in code and checked at mint time, so a typo
#: becomes a rejected request rather than a key that silently authorises
#: nothing — which would present to the consumer as "the API is broken".
#:
#: Not a database enum, for the reason ``pipeline_dead_letters.stage`` gives: the
#: vocabulary is a property of the routes, and a database type would be a second
#: thing to migrate whenever a phase adds a capability.
SCOPES: Final[dict[str, str]] = {
    "public:read": "Read the §26.4 public aggregates at a higher quota than anonymous callers",
    "export:read": "Download bulk CSV/NDJSON extracts",
    "webhooks:manage": "Create, rotate, and delete this tenant's webhook subscriptions",
}


@dataclass(frozen=True, slots=True)
class MintedKey:
    """A newly issued key. ``secret`` exists on this object and nowhere else.

    Deliberately not a field on the ORM model or on any response the system can
    reconstruct: the plaintext is returned once, from this dataclass, straight
    into the HTTP response, and then it is gone. "We can look it up for you" is
    the property that makes a leaked credential unrecoverable-from rather than
    merely embarrassing.
    """

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    prefix: str
    secret: str
    scopes: tuple[str, ...]
    quota_per_hour: int
    expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class ResolvedKey:
    """A verified key, as the request layer needs it."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    prefix: str
    scopes: frozenset[str]
    quota_per_hour: int

    def permits(self, scope: str) -> bool:
        return scope in self.scopes


def digest(secret: str) -> str:
    """SHA-256 hex of a presented key. See the module docstring on why not bcrypt."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _generate() -> tuple[str, str, str]:
    """Return ``(full_key, prefix, digest)``."""
    prefix = secrets.token_hex(KEY_PREFIX_LENGTH // 2)
    body = secrets.token_urlsafe(SECRET_BYTES)
    full = f"{KEY_MARKER}_{prefix}_{body}"
    return full, prefix, digest(full)


async def mint(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    name: str,
    scopes: list[str],
    quota_per_hour: int = 3600,
    expires_at: datetime | None = None,
) -> MintedKey:
    """Issue a key. The plaintext is returned and never stored."""
    if not scopes:
        raise ValidationError(
            "a key with no scopes can call nothing; issuing one produces a credential "
            "that presents to its holder as a broken API"
        )
    unknown = sorted(set(scopes) - set(SCOPES))
    if unknown:
        raise ValidationError(f"unknown scope(s) {unknown}; declared scopes are {sorted(SCOPES)}")
    if expires_at is not None and expires_at <= datetime.now(tz=UTC):
        raise ValidationError("expires_at is already in the past")

    full, prefix, key_digest = _generate()
    row = ApiKey(
        tenant_id=tenant_id,
        name=name,
        key_prefix=prefix,
        key_digest=key_digest,
        scopes=sorted(set(scopes)),
        quota_per_hour=quota_per_hour,
        expires_at=expires_at,
    )
    session.add(row)
    await session.flush()

    return MintedKey(
        id=row.id,
        tenant_id=tenant_id,
        name=name,
        prefix=prefix,
        secret=full,
        scopes=tuple(row.scopes),
        quota_per_hour=quota_per_hour,
        expires_at=expires_at,
    )


async def resolve(session: AsyncSession, *, presented: str) -> ResolvedKey | None:
    """Verify a presented key and return what it authorises, or ``None``.

    ``None`` covers unknown, revoked, and expired alike. The caller turns all
    three into one 401 with one message: telling a caller their key is *expired*
    rather than *unknown* confirms the key was real, which is a fact worth
    knowing to somebody who found it in a log file.
    """
    # tenant-scope-exempt: this is the lookup that *determines* the tenant. There
    # is no tenant in context yet by construction — the key is what names one —
    # and the digest is globally unique, so the row this returns is the only row
    # that could match.
    row = (
        await session.execute(
            # tenant-scope-exempt: resolves which tenant is calling; see above.
            select(ApiKey)
            .where(ApiKey.key_digest == digest(presented))
            .execution_options(**{TENANT_SCOPE_EXEMPT: True})
        )
    ).scalar_one_or_none()

    if row is None or row.revoked_at is not None:
        return None
    if row.expires_at is not None and row.expires_at <= datetime.now(tz=UTC):
        return None

    return ResolvedKey(
        id=row.id,
        tenant_id=row.tenant_id,
        name=row.name,
        prefix=row.key_prefix,
        scopes=frozenset(row.scopes),
        quota_per_hour=row.quota_per_hour,
    )


async def revoke(
    session: AsyncSession, *, tenant_id: uuid.UUID, key_id: uuid.UUID, reason: str
) -> None:
    """Revoke a key. The row stays forever — see the model's column comment.

    Idempotent on the *second* revocation: revoking an already-revoked key is a
    no-op rather than a 409, because the caller's intent ("this key must not
    work") is already satisfied and a retrying incident-response script should
    not have to special-case success.
    """
    with tenant_scope(tenant_id):
        result = await session.execute(
            update(ApiKey)
            .where(
                ApiKey.tenant_id == tenant_id,
                ApiKey.id == key_id,
                ApiKey.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(tz=UTC), revoked_reason=reason)
        )
        if int(getattr(result, "rowcount", 0) or 0) == 0:
            exists = (
                await session.execute(
                    select(func.count())
                    .select_from(ApiKey)
                    .where(ApiKey.tenant_id == tenant_id, ApiKey.id == key_id)
                )
            ).scalar_one()
            if not exists:
                raise NotFoundError(f"no API key {key_id} for this tenant")


async def list_keys(session: AsyncSession, *, tenant_id: uuid.UUID) -> list[ApiKey]:
    with tenant_scope(tenant_id):
        return list(
            (
                await session.execute(
                    select(ApiKey)
                    .where(ApiKey.tenant_id == tenant_id)
                    .order_by(ApiKey.created_at.desc())
                )
            )
            .scalars()
            .all()
        )


async def record_usage(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
    endpoint: str,
    outcome: str,
    on: date | None = None,
) -> None:
    """Increment the (key, day, endpoint) bucket.

    One statement, an ``ON CONFLICT DO UPDATE``, because the alternative —
    select, branch, insert-or-update — races two concurrent requests into
    either a duplicate-key error or a lost increment, and both happen only under
    the traffic that makes the number worth having.

    ``last_used_at`` on the key is refreshed from the same call rather than on
    every request. It is accurate to the day, which is what the column comment
    promises and what "is this key still in use before I revoke it" needs.
    """
    day = on or datetime.now(tz=UTC).date()
    counters = {
        "request_count": 1,
        "error_count": 1 if outcome == "error" else 0,
        "throttled_count": 1 if outcome == "throttled" else 0,
    }
    statement = insert(ApiKeyUsage).values(
        tenant_id=tenant_id,
        api_key_id=key_id,
        usage_date=day,
        endpoint=endpoint,
        **counters,
    )
    with tenant_scope(tenant_id):
        await session.execute(
            statement.on_conflict_do_update(
                constraint="uq_api_key_usage_bucket",
                set_={
                    "request_count": ApiKeyUsage.request_count + counters["request_count"],
                    "error_count": ApiKeyUsage.error_count + counters["error_count"],
                    "throttled_count": (ApiKeyUsage.throttled_count + counters["throttled_count"]),
                },
            )
        )
        await session.execute(
            update(ApiKey)
            .where(ApiKey.tenant_id == tenant_id, ApiKey.id == key_id)
            .values(last_used_at=datetime.now(tz=UTC))
        )


async def usage_report(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    since: date,
    until: date,
) -> list[tuple[uuid.UUID, str, date, str, int, int, int]]:
    """Per-key, per-day, per-endpoint usage for a window.

    Returned as tuples rather than ORM rows because the only consumer renders
    them, and returning entities would attach them to the session for no reason
    on a report that can be thousands of rows.
    """
    with tenant_scope(tenant_id):
        return [
            (row[0], row[1], row[2], row[3], int(row[4]), int(row[5]), int(row[6]))
            for row in (
                await session.execute(
                    select(
                        ApiKeyUsage.api_key_id,
                        ApiKey.key_prefix,
                        ApiKeyUsage.usage_date,
                        ApiKeyUsage.endpoint,
                        ApiKeyUsage.request_count,
                        ApiKeyUsage.error_count,
                        ApiKeyUsage.throttled_count,
                    )
                    .join(
                        ApiKey,
                        (ApiKey.id == ApiKeyUsage.api_key_id)
                        & (ApiKey.tenant_id == ApiKeyUsage.tenant_id),
                    )
                    .where(
                        ApiKeyUsage.tenant_id == tenant_id,
                        ApiKey.tenant_id == tenant_id,
                        ApiKeyUsage.usage_date >= since,
                        ApiKeyUsage.usage_date <= until,
                    )
                    .order_by(ApiKeyUsage.usage_date.desc(), ApiKeyUsage.endpoint)
                )
            ).tuples()
        ]
