"""Identity failures, as a closed set the API layer translates in one place.

Not ``ProblemDetailError``, for the reason ``control_plane.errors`` gives: these
services are called from HTTP handlers, from the CLI that bootstraps the first
operator, and from tests, and a service that raised an HTTP error would drag
FastAPI status codes into a migration script.

**The split between ``AuthenticationError`` and ``AuthorizationError`` is the
401/403 split, and it is the one place in this package where a distinction is
*deliberately* visible to a caller.** Everywhere else — a user id belonging to
another tenant, a role that does not exist here — the answer is ``NotFoundError``
and the caller cannot tell "absent" from "not yours". But "your credential is
not valid" and "your credential is valid and does not permit this" must be
distinguishable, because they have different remedies: sign in again, or ask for
a grant. Collapsing them would mean an officer whose session expired mid-shift
is told they lack permission, and they would go and ask an administrator for an
access change they already have.
"""

from __future__ import annotations


class IdentityError(Exception):
    """Base for every rejection this package makes."""


class NotFoundError(IdentityError):
    """A referenced user, role, grant or session does not exist *for this tenant*.

    The tenant qualifier is the whole point, and it is load-bearing for the
    Phase 13 gate: "department head A provably cannot read department B's rows
    through any endpoint, including error messages". An error that said
    "forbidden" where another tenant's row exists, and "not found" where nothing
    does, is a cross-tenant oracle built out of status codes.
    """


class ConflictError(IdentityError):
    """The change collides with something that already exists."""


class ValidationError(IdentityError):
    """The request is internally inconsistent or violates a stated invariant."""


class AuthenticationError(IdentityError):
    """The caller did not prove who they are.

    One type for every cause — no credential, unknown subject, wrong password,
    expired refresh token, revoked session, replayed refresh token. The API
    layer renders all of them as one 401 with one message, for the reason
    ``integrations.keys`` gives about API keys: telling a caller their session is
    *expired* rather than *unknown* confirms the session was real, which is the
    fact worth having to somebody working through a stolen token.

    ``reason`` carries the real cause for the log and the metric. It never
    reaches the response body.
    """

    def __init__(self, message: str, *, reason: str) -> None:
        self.reason = reason
        super().__init__(message)


class AuthorizationError(IdentityError):
    """The caller is known and the permission was not granted on this scope.

    Carries the machine-readable facts the decision log already recorded, so the
    403 body can name the permission the caller lacks without the handler
    reconstructing it. Naming the permission is a deliberate disclosure: it is
    the difference between a support ticket that says "I need review:decide on
    Ward 4" and one that says "the console is broken".

    What it never carries is anything about the *resource* — not its id, not its
    department, not whether it exists. A 403 that varies with the target is the
    enumeration oracle the gate clause forbids.
    """

    def __init__(self, *, permission: str, reason_code: str) -> None:
        self.permission = permission
        self.reason_code = reason_code
        super().__init__(f"permission {permission!r} was not granted ({reason_code})")


class CredentialLockedError(AuthenticationError):
    """Too many failed attempts against one credential.

    A subclass rather than a reason string on ``AuthenticationError`` because
    this one *is* told to the caller, and that is a considered exception to the
    paragraph above. A lockout the user cannot see is a user who keeps trying,
    extends their own lockout, and raises a support ticket that costs more than
    the information disclosed — which is only that this account exists and
    somebody has been guessing at it.
    """

    def __init__(self, *, retry_after_seconds: int) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            f"too many failed attempts; retry in {retry_after_seconds} seconds",
            reason="locked",
        )


class ProviderError(IdentityError):
    """An external identity provider failed, or answered something unusable.

    Its own type because the remedy is neither the caller's nor ours: a tenant's
    OIDC provider being down is an outage somewhere else, and ADR-0060's rule
    applies — an unreachable API is an answer, not an exception to leak. The API
    layer renders it as 502 with the provider named, and the attempt is recorded
    as a degradation so a provider that fails every morning at 09:00 is visible.
    """


__all__ = [
    "AuthenticationError",
    "AuthorizationError",
    "ConflictError",
    "CredentialLockedError",
    "IdentityError",
    "NotFoundError",
    "ProviderError",
    "ValidationError",
]
