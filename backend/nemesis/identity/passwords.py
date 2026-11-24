"""Password hashing with ``hashlib.scrypt``, and no new dependency.

**Why not argon2 or bcrypt.** Both are better-known answers and either would be
defensible. Both are also compiled wheels, and this repository has a standing
argument about what goes in which image: ``pyproject.toml`` records that
``imagehash`` and ``exifread`` were *removed* because they would have landed in
the ML image and made the two §11.1 checks untestable in the image the suite
runs in. The same reasoning applies here with more force — authentication runs
in the API image, in the worker, and in the CLI that bootstraps the first
operator, and a compiled dependency in the authentication path is a dependency
in every one of them.

``hashlib.scrypt`` is in the standard library, is backed by OpenSSL, and is a
memory-hard KDF designed for exactly this. This is **not** hand-rolled
cryptography: no primitive is implemented here. What is implemented here is
parameter selection, a salt, an encoding, and a constant-time comparison.

**Parameters, and why they are stored per credential.** ``n=2**15, r=8, p=1``
costs about 32 MiB and ~50 ms on the reference machine. That is the standard
interactive-login figure and it is deliberately not the maximum the machine can
bear: sign-in is also the path a brute-force attempt hits, so the cost is paid
by us on every attempt too, and ``maxmem`` has to admit it — a limit left at the
default rejects ``n=2**15`` outright with a confusing ``ValueError``, which is
the first thing anyone hits when raising these numbers.

Storing the parameters on the row (``user_credentials.params``) is what makes
raising them possible later: verification reads the parameters the digest was
made with, so an old credential keeps working and is rehashed at its next
successful sign-in. A module constant would mean the day we raise the cost is
the day every existing password stops verifying.

**What this module does not do.** No composition rules beyond a length floor,
no forced rotation (``credential_policies.max_age_days`` defaults to 0 for that
reason), and no complexity classes. Current guidance is that length and a
breach-corpus check do the work and that complexity rules push people towards
``Password1!``; the policy table carries the two levers worth having.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import unicodedata
from dataclasses import dataclass
from typing import Any, Final

from nemesis.identity.errors import ValidationError

ALGORITHM: Final = "scrypt"

#: CPU/memory cost. 2**15 blocks at r=8 is ~32 MiB per hash.
DEFAULT_N: Final = 2**15
DEFAULT_R: Final = 8
DEFAULT_P: Final = 1
#: Bytes of output. 32 is the hash length; longer buys nothing against a
#: 256-bit-strength KDF output.
DEFAULT_DKLEN: Final = 32
#: Bytes of salt. 16 is the conventional floor and the only requirement is
#: uniqueness, which ``secrets.token_bytes`` gives.
SALT_BYTES: Final = 16


#: ``hashlib.scrypt`` refuses any (n, r, p) whose working set exceeds
#: ``maxmem``, and the default is 32 MiB *minus* overhead — just under what
#: these parameters need. Computed from the parameters rather than written as a
#: number so raising ``n`` cannot silently start raising ``ValueError``.
def _maxmem_for(n: int, r: int, p: int) -> int:
    """``128 * n * r * p`` is scrypt's working set, with headroom."""
    return 128 * n * r * p * 2


#: The shortest password the platform will accept regardless of tenant policy.
#: A tenant may raise its own floor (``credential_policies.min_length``); it may
#: not lower it below this.
PLATFORM_MIN_LENGTH: Final = 10


@dataclass(frozen=True, slots=True)
class Hashed:
    """A hashed password and everything needed to verify it later."""

    algorithm: str
    salt: str
    digest: str
    params: dict[str, Any]


def normalise(password: str) -> bytes:
    """NFKC, then UTF-8.

    Unicode normalisation is not cosmetic here: a password typed with a composed
    "é" on one keyboard and a combining accent on another is the same password to
    the person typing it and two different byte strings to scrypt. Without this,
    a user who sets their password on Android and signs in on macOS can be
    locked out by their own keyboard, and the failure is unreproducible by
    whoever takes the support call.
    """
    return unicodedata.normalize("NFKC", password).encode("utf-8")


def hash_password(
    password: str,
    *,
    n: int = DEFAULT_N,
    r: int = DEFAULT_R,
    p: int = DEFAULT_P,
    dklen: int = DEFAULT_DKLEN,
) -> Hashed:
    """Hash a password with a fresh salt."""
    salt = secrets.token_bytes(SALT_BYTES)
    derived = hashlib.scrypt(
        normalise(password),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=dklen,
        maxmem=_maxmem_for(n, r, p),
    )
    return Hashed(
        algorithm=ALGORITHM,
        salt=salt.hex(),
        digest=derived.hex(),
        params={"n": n, "r": r, "p": p, "dklen": dklen},
    )


def verify(
    *,
    password: str,
    salt: str,
    digest: str,
    algorithm: str = ALGORITHM,
    params: dict[str, Any] | None = None,
) -> bool:
    """Constant-time verification against a stored digest.

    An unknown ``algorithm`` returns ``False`` rather than raising. The caller is
    the sign-in path, and the only honest rendering of "this credential was
    written by a build that knew something we do not" is a failed sign-in with a
    recorded reason — raising would surface as a 500 on an endpoint whose error
    behaviour is otherwise deliberately uninformative.
    """
    if algorithm != ALGORITHM:
        return False

    settings = params or {}
    n = int(settings.get("n", DEFAULT_N))
    r = int(settings.get("r", DEFAULT_R))
    p = int(settings.get("p", DEFAULT_P))
    dklen = int(settings.get("dklen", DEFAULT_DKLEN))

    try:
        derived = hashlib.scrypt(
            normalise(password),
            salt=bytes.fromhex(salt),
            n=n,
            r=r,
            p=p,
            dklen=dklen,
            maxmem=_maxmem_for(n, r, p),
        )
    except (ValueError, TypeError):
        # Malformed stored parameters — a hand-edited row, a truncated restore.
        # Same reasoning as the unknown algorithm above.
        return False

    return hmac.compare_digest(derived.hex(), digest)


def needs_rehash(*, algorithm: str, params: dict[str, Any] | None) -> bool:
    """Whether this credential was made with weaker parameters than we now use.

    Called after a *successful* verification, which is the only moment the
    plaintext is available to rehash with. Comparing ``n`` alone would miss a
    change to ``r``; comparing the whole dict would rehash every credential the
    day a key is added to it, so the comparison is over the parameters that
    affect cost.
    """
    if algorithm != ALGORITHM:
        return True
    settings = params or {}
    return (
        int(settings.get("n", 0)) < DEFAULT_N
        or int(settings.get("r", 0)) < DEFAULT_R
        or int(settings.get("p", 0)) < DEFAULT_P
    )


def assert_acceptable(password: str, *, min_length: int) -> None:
    """Enforce the length floor, and nothing else. See the module docstring.

    The floor is ``max(tenant, platform)``: a tenant may be stricter than the
    platform and may not be laxer, because the platform's floor is what the
    deployment's own security posture rests on and a tenant administrator is not
    the right person to lower it.
    """
    floor = max(min_length, PLATFORM_MIN_LENGTH)
    # Count *characters* after normalisation, not bytes. A twelve-character
    # Devanagari passphrase is longer than twelve bytes and a byte floor would
    # accept a shorter Latin one while rejecting nothing — the opposite of the
    # intent, and invisible in an English-only test suite.
    length = len(unicodedata.normalize("NFKC", password))
    if length < floor:
        raise ValidationError(f"password must be at least {floor} characters; this one is {length}")
    if password.strip() == "":
        raise ValidationError("password is entirely whitespace")


__all__ = [
    "ALGORITHM",
    "DEFAULT_DKLEN",
    "DEFAULT_N",
    "DEFAULT_P",
    "DEFAULT_R",
    "PLATFORM_MIN_LENGTH",
    "SALT_BYTES",
    "Hashed",
    "assert_acceptable",
    "hash_password",
    "needs_rehash",
    "normalise",
    "verify",
]
