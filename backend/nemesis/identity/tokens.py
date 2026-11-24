"""Access tokens: a strict, single-algorithm JWT, implemented here on purpose.

**Why first-party rather than PyJWT.** Two reasons, and the second is the real
one.

The first is the same dependency argument ``passwords`` makes: authentication
runs in three images and the CLI, and every dependency in that path is a
dependency in all of them.

The second is that a general JWT library's job is to support *every* algorithm,
and every serious JWT vulnerability of the last decade has come from that
flexibility — ``alg: none``, HMAC-vs-RSA confusion, ``kid`` pointing at an
attacker-chosen key. The mitigation in a library is a long list of options you
must pass correctly on every call. Here it is the absence of a code path: this
module verifies HMAC-SHA256 and nothing else, reads the algorithm from *our*
configuration rather than from the token, and treats a token whose header is not
exactly ``{"alg": "HS256", "typ": "JWT"}`` as forged. There is no second
algorithm to confuse it with.

That claim comes with an obligation, which ADR-0063 records: **this module may
never be extended to verify an asymmetric signature.** The moment a provider's
RS256 ID token needs verifying, that is a different problem with a different
answer — see ``identity.oidc``, which obtains the ID token over a back-channel
TLS connection to the issuer's own token endpoint and therefore never has to
verify its signature at all.

**What the token is for, and what it is not trusted for.** The access token
carries the session id. Whether that session is still *live* is a database read
on every request (``identity.sessions.resolve``), so the signature proves the
token was minted by us and the row proves it has not been revoked. A valid
signature alone authorises nothing, which is the property that makes revocation
immediate — ADR-0066.

Every value is validated: ``exp`` and ``nbf`` with a small leeway for clock
skew, ``iss`` and ``aud`` against configuration, and the claim *types* before
they are used. A ``sub`` that is not a UUID is a forged or corrupt token, and
``uuid.UUID(...)`` raising inside a dependency would be a 500 where a 401
belongs.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from nemesis.identity.errors import AuthenticationError

#: The only algorithm this module implements. Not a parameter: see the module
#: docstring. ``Settings.jwt_algorithm`` exists from Phase 0 and is validated
#: against this value at startup rather than used to select an implementation.
ALGORITHM: Final = "HS256"

#: Fixed header. Serialised with sorted keys and no spaces so the encoding is
#: deterministic — two tokens for the same claims are byte-identical, which
#: makes the test suite able to assert on a token rather than on its decoding.
_HEADER: Final[dict[str, str]] = {"alg": ALGORITHM, "typ": "JWT"}

#: Claim names. Short, because they are sent on every request, and standard
#: where a standard name exists.
CLAIM_ISSUER: Final = "iss"
CLAIM_AUDIENCE: Final = "aud"
CLAIM_SUBJECT: Final = "sub"
CLAIM_TENANT: Final = "tid"
CLAIM_SESSION: Final = "sid"
CLAIM_ISSUED_AT: Final = "iat"
CLAIM_NOT_BEFORE: Final = "nbf"
CLAIM_EXPIRES: Final = "exp"
CLAIM_TOKEN_ID: Final = "jti"
CLAIM_IMPERSONATION: Final = "imp"
CLAIM_OPERATOR: Final = "ops"

#: Tolerance for clock skew between the signer and the verifier. Thirty seconds:
#: large enough for an unsynchronised container, small enough that an expired
#: token is expired. This is the only place in the system where "approximately
#: now" is acceptable, and it is bounded here rather than per call site.
CLOCK_SKEW_LEEWAY: Final = timedelta(seconds=30)

#: Default audience and issuer. Both are configurable; both have defaults
#: because a deployment that has not thought about them should still get a token
#: that cannot be replayed against a different NEMESIS deployment with the same
#: secret — which is the failure a missing ``aud`` check permits.
DEFAULT_ISSUER: Final = "nemesis"
DEFAULT_AUDIENCE: Final = "nemesis-api"


@dataclass(frozen=True, slots=True)
class AccessClaims:
    """The validated contents of an access token."""

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    session_id: uuid.UUID
    subject: str
    expires_at: datetime
    token_id: str
    impersonation_id: uuid.UUID | None = None
    operator_subject: str | None = None


def _b64encode(raw: bytes) -> str:
    """Base64url, no padding, as JWS requires."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    """Base64url with the padding JWS omits, restored.

    Rejects anything that is not valid base64url rather than letting
    ``binascii.Error`` escape: a malformed segment is a malformed token, which
    is a 401, and the caller should not have to know this module's internals to
    produce one.
    """
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except (binascii.Error, ValueError) as exc:
        raise AuthenticationError("malformed token", reason="malformed") from exc


def _sign(message: bytes, secret: str) -> str:
    return _b64encode(hmac.new(secret.encode("utf-8"), message, hashlib.sha256).digest())


def encode(claims: dict[str, Any], *, secret: str) -> str:
    """Serialise and sign a claim set.

    ``separators`` and ``sort_keys`` are not tidiness: without them the encoding
    depends on dict insertion order and on Python's default spacing, which makes
    a signature reproducible only by accident.
    """
    header = _b64encode(json.dumps(_HEADER, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    payload = _b64encode(
        json.dumps(claims, separators=(",", ":"), sort_keys=True, default=str).encode("utf-8")
    )
    signing_input = f"{header}.{payload}".encode("ascii")
    return f"{header}.{payload}.{_sign(signing_input, secret)}"


def mint_access_token(
    *,
    secret: str,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    subject: str,
    ttl_seconds: int,
    issuer: str = DEFAULT_ISSUER,
    audience: str = DEFAULT_AUDIENCE,
    impersonation_id: uuid.UUID | None = None,
    operator_subject: str | None = None,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    """Mint one access token. Returns ``(token, expires_at)``.

    The expiry is returned rather than left for the caller to recompute, because
    the API hands it to the client (so a console can refresh before a request
    fails rather than after) and two independent computations of the same
    instant is how they end up disagreeing by a second.
    """
    issued = now or datetime.now(tz=UTC)
    expires = issued + timedelta(seconds=ttl_seconds)
    claims: dict[str, Any] = {
        CLAIM_ISSUER: issuer,
        CLAIM_AUDIENCE: audience,
        CLAIM_SUBJECT: str(user_id),
        CLAIM_TENANT: str(tenant_id),
        CLAIM_SESSION: str(session_id),
        CLAIM_ISSUED_AT: int(issued.timestamp()),
        CLAIM_NOT_BEFORE: int(issued.timestamp()),
        CLAIM_EXPIRES: int(expires.timestamp()),
        CLAIM_TOKEN_ID: secrets.token_urlsafe(12),
        "sub_name": subject,
    }
    if impersonation_id is not None:
        claims[CLAIM_IMPERSONATION] = str(impersonation_id)
    if operator_subject is not None:
        claims[CLAIM_OPERATOR] = operator_subject
    return encode(claims, secret=secret), expires


def decode(
    token: str,
    *,
    secret: str,
    issuer: str = DEFAULT_ISSUER,
    audience: str = DEFAULT_AUDIENCE,
    now: datetime | None = None,
) -> AccessClaims:
    """Verify a token and return its claims, or raise ``AuthenticationError``.

    Order matters and is deliberate: structure, then *signature*, then claims.
    Validating ``exp`` before the signature would mean an attacker's forged
    token with a sensible expiry takes a different code path from one with a
    stale expiry, and the difference is observable in timing and in logs. Every
    rejection below the signature check is a rejection of a token we minted.
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise AuthenticationError("malformed token", reason="malformed")

    header_segment, payload_segment, signature_segment = parts

    try:
        header = json.loads(_b64decode(header_segment))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        # ``UnicodeDecodeError`` and not only ``JSONDecodeError``: a segment of
        # arbitrary bytes decodes from base64 fine and then fails *before* the
        # JSON parser sees it. Catching one and not the other turns a malformed
        # token into a 500, which is both a worse answer and a louder signal to
        # whoever is sending it.
        raise AuthenticationError("malformed token header", reason="malformed") from exc
    if header != _HEADER:
        # Covers `alg: none`, an algorithm substitution, a `kid` we did not
        # write, and a `typ` that is not JWT — one comparison, no branches an
        # attacker can steer. See the module docstring.
        raise AuthenticationError("unexpected token header", reason="header_mismatch")

    expected = _sign(f"{header_segment}.{payload_segment}".encode("ascii"), secret)
    if not hmac.compare_digest(expected, signature_segment):
        raise AuthenticationError("bad token signature", reason="bad_signature")

    try:
        claims = json.loads(_b64decode(payload_segment))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise AuthenticationError("malformed token payload", reason="malformed") from exc
    if not isinstance(claims, dict):
        raise AuthenticationError("malformed token payload", reason="malformed")

    if claims.get(CLAIM_ISSUER) != issuer:
        raise AuthenticationError("token issued elsewhere", reason="issuer_mismatch")
    if claims.get(CLAIM_AUDIENCE) != audience:
        raise AuthenticationError("token not for this audience", reason="audience_mismatch")

    moment = now or datetime.now(tz=UTC)
    expires_at = _instant(claims, CLAIM_EXPIRES)
    if expires_at + CLOCK_SKEW_LEEWAY <= moment:
        raise AuthenticationError("token expired", reason="expired")
    not_before = _instant(claims, CLAIM_NOT_BEFORE)
    if not_before - CLOCK_SKEW_LEEWAY > moment:
        raise AuthenticationError("token not yet valid", reason="not_yet_valid")

    return AccessClaims(
        user_id=_claim_uuid(claims, CLAIM_SUBJECT),
        tenant_id=_claim_uuid(claims, CLAIM_TENANT),
        session_id=_claim_uuid(claims, CLAIM_SESSION),
        subject=str(claims.get("sub_name") or ""),
        expires_at=expires_at,
        token_id=str(claims.get(CLAIM_TOKEN_ID) or ""),
        impersonation_id=_optional_uuid(claims, CLAIM_IMPERSONATION),
        operator_subject=(
            str(claims[CLAIM_OPERATOR]) if claims.get(CLAIM_OPERATOR) is not None else None
        ),
    )


def _instant(claims: dict[str, Any], name: str) -> datetime:
    value = claims.get(name)
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise AuthenticationError(f"token {name} is not a timestamp", reason="malformed")
    try:
        return datetime.fromtimestamp(float(value), tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise AuthenticationError(f"token {name} is out of range", reason="malformed") from exc


def _claim_uuid(claims: dict[str, Any], name: str) -> uuid.UUID:
    value = claims.get(name)
    if not isinstance(value, str):
        raise AuthenticationError(f"token {name} is missing", reason="malformed")
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise AuthenticationError(f"token {name} is not an identifier", reason="malformed") from exc


def _optional_uuid(claims: dict[str, Any], name: str) -> uuid.UUID | None:
    if claims.get(name) is None:
        return None
    return _claim_uuid(claims, name)


def mint_refresh_token() -> tuple[str, str]:
    """A new opaque refresh token. Returns ``(plaintext, digest)``.

    Opaque, not a JWT. A refresh token's whole job is to be exchanged at one
    endpoint that looks it up, so there is nothing for a self-describing token to
    save — and a JWT refresh token is a credential that stays valid after
    revocation unless the endpoint looks it up anyway.

    ``nem_rt_`` for the reason ``integrations.keys`` prefixes API keys: the
    repository's own ``gitleaks`` hook and every commercial secret scanner match
    on prefixes, and a credential that looks like a random blob is one that gets
    committed and never flagged.
    """
    plaintext = f"nem_rt_{secrets.token_urlsafe(32)}"
    return plaintext, digest_refresh_token(plaintext)


def digest_refresh_token(plaintext: str) -> str:
    """SHA-256 hex of a refresh token.

    Unsalted, for the reason ``integrations.keys.digest`` gives: the input is 256
    bits from ``secrets.token_bytes``, so there is no dictionary to attack and a
    per-row salt would only prevent the indexed lookup the exchange needs.
    """
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


__all__ = [
    "ALGORITHM",
    "CLAIM_AUDIENCE",
    "CLAIM_EXPIRES",
    "CLAIM_IMPERSONATION",
    "CLAIM_ISSUER",
    "CLAIM_OPERATOR",
    "CLAIM_SESSION",
    "CLAIM_SUBJECT",
    "CLAIM_TENANT",
    "CLOCK_SKEW_LEEWAY",
    "DEFAULT_AUDIENCE",
    "DEFAULT_ISSUER",
    "AccessClaims",
    "decode",
    "digest_refresh_token",
    "encode",
    "mint_access_token",
    "mint_refresh_token",
]
