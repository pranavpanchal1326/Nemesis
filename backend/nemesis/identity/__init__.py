"""Identity, authorization and org modelling — Phase 13.

**The dependency direction inside this package is one-way, and it is the thing
to keep true.** ``permissions`` and ``scopes`` are pure leaves: a vocabulary and
a decision function, no session, no clock, no settings. Everything else depends
on them and nothing depends back. That is what makes the Phase 13 gate clause
"authorization decisions are property-tested against a generated role/scope
matrix" achievable at all — Hypothesis hammers ``scopes.evaluate`` with
generated matrices in milliseconds, which is impossible against a function that
needs a database.

Reading order, which is also the layering:

``permissions``
    Every permission this system recognises, declared in code. Three mechanisms
    read it: the CI checker that fails an endpoint with no declared permission,
    the decision point, and the role service.
``scopes``
    ``Principal``, ``Resource``, ``Grant``, and the pure containment test.
    Also ``Visibility`` — the same model expressed as a query filter, which is
    what makes "including pagination counts" in the gate clause meetable.
``errors``
    The closed set the API layer translates. The 401/403 split is the one
    distinction this package deliberately makes visible to a caller.
``tokens`` / ``passwords``
    Credentials. Both are first-party and both say why in their docstrings; the
    short version is that a compiled dependency in the authentication path is a
    dependency in three images and the CLI.
``sessions``
    Sign-in, refresh rotation, reuse detection, revocation.
``roles``
    Composing roles, granting them on scopes, and the delegation invariant.
``users``
    Users, credentials, lockout, the password sign-in path.
``decisions``
    The centralised decision point and the decision log. **The only
    authorization check in the system** — ``api.identity_deps`` is the only
    thing routes can reach, and it calls this.
``oidc``
    The authorization-code flow, with no asymmetric cryptography in process and
    an ADR explaining why that is sound.
``impersonation``
    Audited support access.
``bootstrap``
    Installing the shipped role library and creating the first operator.
"""

from __future__ import annotations

__all__: list[str] = []
