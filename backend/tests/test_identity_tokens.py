"""Access-token invariants — the ones a JWT library's options exist to enforce.

``identity.tokens`` implements HMAC-SHA256 and nothing else, specifically so
that the historic JWT vulnerability classes have no code path to reach. That
claim is only worth making if it is tested, so every one of those classes is
attempted here: ``alg: none``, an algorithm substitution, a tampered payload, a
token from another issuer, a token for another audience, an expired token, and a
claim that is the wrong *type*.

A failure in this file is not a style regression. It means a forged token can be
accepted somewhere.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from nemesis.identity import tokens
from nemesis.identity.errors import AuthenticationError

SECRET = "test-secret-not-a-real-one"


def _mint(**overrides: object) -> tuple[str, dict[str, object]]:
    arguments: dict[str, object] = {
        "secret": SECRET,
        "user_id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "session_id": uuid.uuid4(),
        "subject": "ops@example.org",
        "ttl_seconds": 900,
    }
    arguments.update(overrides)
    token, _ = tokens.mint_access_token(**arguments)  # type: ignore[arg-type]
    return token, arguments


def _segments(token: str) -> tuple[str, str, str]:
    header, payload, signature = token.split(".")
    return header, payload, signature


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _resign(header: str, payload: str, secret: str) -> str:
    signature = hmac.new(
        secret.encode("utf-8"), f"{header}.{payload}".encode("ascii"), hashlib.sha256
    ).digest()
    return f"{header}.{payload}.{_b64(signature)}"


def test_round_trip_preserves_every_claim() -> None:
    token, arguments = _mint()
    claims = tokens.decode(token, secret=SECRET)

    assert claims.user_id == arguments["user_id"]
    assert claims.tenant_id == arguments["tenant_id"]
    assert claims.session_id == arguments["session_id"]
    assert claims.subject == arguments["subject"]
    assert claims.impersonation_id is None


def test_impersonation_claims_survive_the_round_trip() -> None:
    """A support session's token must say so, or the decision log cannot."""
    impersonation_id = uuid.uuid4()
    token, _ = _mint(impersonation_id=impersonation_id, operator_subject="support@nemesis")
    claims = tokens.decode(token, secret=SECRET)

    assert claims.impersonation_id == impersonation_id
    assert claims.operator_subject == "support@nemesis"


def test_encoding_is_deterministic() -> None:
    """Same claims, same bytes — the property that makes a token assertable.

    ``json.dumps`` with sorted keys and no spaces is what buys this. Without it
    the signature depends on dict ordering, and a test that compares two tokens
    passes or fails by accident.
    """
    claims = {"a": 1, "b": 2}
    assert tokens.encode(claims, secret=SECRET) == tokens.encode({"b": 2, "a": 1}, secret=SECRET)


def test_alg_none_is_refused() -> None:
    """The original JWT vulnerability: a token that asks not to be verified."""
    _, payload, _ = _segments(_mint()[0])
    forged_header = _b64(json.dumps({"alg": "none", "typ": "JWT"}, separators=(",", ":")).encode())
    forged = f"{forged_header}.{payload}."

    with pytest.raises(AuthenticationError) as caught:
        tokens.decode(forged, secret=SECRET)
    assert caught.value.reason == "header_mismatch"


def test_algorithm_substitution_is_refused() -> None:
    """A header naming a different algorithm never reaches a verifier.

    The header is compared as a whole, so there is no branch for an attacker to
    steer into an HMAC verification of an RSA token — the confusion attack has
    no code path here rather than being defended against.
    """
    _, payload, _ = _segments(_mint()[0])
    forged_header = _b64(json.dumps({"alg": "HS512", "typ": "JWT"}, separators=(",", ":")).encode())
    forged = _resign(forged_header, payload, SECRET)

    with pytest.raises(AuthenticationError) as caught:
        tokens.decode(forged, secret=SECRET)
    assert caught.value.reason == "header_mismatch"


def test_extra_header_fields_are_refused() -> None:
    """A ``kid`` we did not write is a key we would be told to use."""
    _, payload, _ = _segments(_mint()[0])
    forged_header = _b64(
        json.dumps(
            {"alg": "HS256", "typ": "JWT", "kid": "attacker"}, separators=(",", ":")
        ).encode()
    )
    with pytest.raises(AuthenticationError):
        tokens.decode(_resign(forged_header, payload, SECRET), secret=SECRET)


def test_tampered_payload_fails_the_signature() -> None:
    token, _ = _mint()
    header, payload, _ = _segments(token)
    claims = json.loads(_unb64(payload))
    claims[tokens.CLAIM_TENANT] = str(uuid.uuid4())
    tampered = f"{header}.{_b64(json.dumps(claims).encode())}.{_segments(token)[2]}"

    with pytest.raises(AuthenticationError) as caught:
        tokens.decode(tampered, secret=SECRET)
    assert caught.value.reason == "bad_signature"


def test_a_token_signed_with_another_secret_is_refused() -> None:
    header, payload, _ = _segments(_mint()[0])
    with pytest.raises(AuthenticationError) as caught:
        tokens.decode(_resign(header, payload, "another-deployments-secret"), secret=SECRET)
    assert caught.value.reason == "bad_signature"


def test_audience_is_checked() -> None:
    """Two deployments sharing a secret must not share sessions.

    This is the failure a staging environment restored from production creates:
    the secret is the same, so without an audience check a staging token works
    against production.
    """
    token, _ = _mint()
    with pytest.raises(AuthenticationError) as caught:
        tokens.decode(token, secret=SECRET, audience="some-other-api")
    assert caught.value.reason == "audience_mismatch"


def test_issuer_is_checked() -> None:
    token, _ = _mint()
    with pytest.raises(AuthenticationError) as caught:
        tokens.decode(token, secret=SECRET, issuer="not-nemesis")
    assert caught.value.reason == "issuer_mismatch"


def test_expiry_is_enforced_with_bounded_leeway() -> None:
    issued = datetime.now(tz=UTC) - timedelta(hours=1)
    token, _ = _mint(ttl_seconds=60, now=issued)

    with pytest.raises(AuthenticationError) as caught:
        tokens.decode(token, secret=SECRET)
    assert caught.value.reason == "expired"

    # Inside the leeway it is still accepted: a container with a thirty-second
    # clock skew must not log everybody out.
    just_expired = issued + timedelta(seconds=60)
    tokens.decode(token, secret=SECRET, now=just_expired + timedelta(seconds=10))


def test_not_before_is_enforced() -> None:
    future = datetime.now(tz=UTC) + timedelta(hours=1)
    token, _ = _mint(now=future)

    with pytest.raises(AuthenticationError) as caught:
        tokens.decode(token, secret=SECRET)
    assert caught.value.reason == "not_yet_valid"


@pytest.mark.parametrize(
    "claim",
    [tokens.CLAIM_SUBJECT, tokens.CLAIM_TENANT, tokens.CLAIM_SESSION],
)
def test_an_identifier_claim_that_is_not_a_uuid_is_rejected(claim: str) -> None:
    """A 401, never a 500. ``uuid.UUID`` raising inside a dependency would be
    an internal error where an authentication failure belongs."""
    token, _ = _mint()
    header, payload, _ = _segments(token)
    claims = json.loads(_unb64(payload))
    claims[claim] = "not-a-uuid"
    forged = _resign(header, _b64(json.dumps(claims).encode()), SECRET)

    with pytest.raises(AuthenticationError) as caught:
        tokens.decode(forged, secret=SECRET)
    assert caught.value.reason == "malformed"


def test_a_boolean_expiry_is_not_a_timestamp() -> None:
    """``True`` is an ``int`` in Python, and ``exp: true`` would be 1970.

    Which would make the token expired rather than valid, so this is not a
    vulnerability — it is the kind of type confusion that turns into one when
    the comparison is later inverted, and it costs one ``isinstance`` to close.
    """
    token, _ = _mint()
    header, payload, _ = _segments(token)
    claims = json.loads(_unb64(payload))
    claims[tokens.CLAIM_EXPIRES] = True
    forged = _resign(header, _b64(json.dumps(claims).encode()), SECRET)

    with pytest.raises(AuthenticationError) as caught:
        tokens.decode(forged, secret=SECRET)
    assert caught.value.reason == "malformed"


@pytest.mark.parametrize("malformed", ["", "a", "a.b", "a.b.c.d", "....", "a.b.c"])
def test_structurally_broken_tokens_are_rejected(malformed: str) -> None:
    with pytest.raises(AuthenticationError):
        tokens.decode(malformed, secret=SECRET)


def test_refresh_tokens_are_prefixed_and_hashed() -> None:
    """The prefix is a secret-scanner affordance, not decoration."""
    plaintext, digest = tokens.mint_refresh_token()

    assert plaintext.startswith("nem_rt_")
    assert digest == tokens.digest_refresh_token(plaintext)
    assert len(digest) == 64
    assert plaintext not in digest


def test_two_refresh_tokens_are_never_equal() -> None:
    minted = {tokens.mint_refresh_token()[0] for _ in range(50)}
    assert len(minted) == 50
