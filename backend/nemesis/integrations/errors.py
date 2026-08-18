"""Integration-layer errors, mirroring ``control_plane.errors``.

Separate from the control plane's family rather than shared, because the two
surfaces have different callers and the shared base would be a class with one
purpose: making an ``except`` clause in the HTTP layer shorter. The translation
table in ``api.v1.integrations`` maps both, which is where the similarity is
supposed to live.
"""

from __future__ import annotations


class IntegrationError(RuntimeError):
    """Base for every failure this package raises deliberately."""


class NotFoundError(IntegrationError):
    """The named key, endpoint, or delivery does not exist for this tenant.

    Raised — never a "forbidden" — for another tenant's identifier, applying the
    same 404-not-403 discipline ``api.deps`` uses: a distinguishable rejection
    turns an identifier into an existence oracle.
    """


class ConflictError(IntegrationError):
    """The change collides with something already there."""


class ValidationError(IntegrationError):
    """The request is well-formed and unacceptable."""


class UnsafeTargetError(ValidationError):
    """A webhook URL that would make this deployment an SSRF proxy.

    Its own type rather than a plain ``ValidationError`` because the HTTP layer
    returns a *different* message for it — one that names the control rather
    than saying "invalid url", so an operator configuring a legitimate internal
    endpoint learns immediately why it was refused instead of assuming a typo.
    """
