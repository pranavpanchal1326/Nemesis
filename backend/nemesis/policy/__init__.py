"""Phase 6 — the policy and rules engine.

Every behavioural knob in NEMESIS becomes governed data here: severity rubrics,
dedup thresholds, safety rulesets, SLA matrices, routing rules, and rate cards.
Each is a versioned, effective-dated, tenant-scoped document that moves through
draft → review → approve → activate, with every transition on the tenant's hash
chain and every decision stamped with the exact version that produced it.

The package splits along the line that matters operationally:

``documents``
    What a policy *is*. Six Pydantic bodies, validated identically wherever they
    are written from — the API, the provisioner, a test.
``expressions``
    The sandboxed condition language routing rules are written in. Never calls
    ``eval``; a compiled condition provably cannot raise, do I/O, or vary.
``service``
    The lifecycle. One transition table, one mutation path, one place that
    writes ``policy_transitioned``.
``resolver``
    Reading policy at decision time — hot reload on a TTL, taxonomy ancestor
    walks, and the arithmetic that turns a document into a score, a band, or a
    route.
``baselines``
    What a tenant starts with, and the single source both provisioning and the
    resolver's fallback read.

Nothing in this package commits, and nothing in it raises an HTTP error. The
services are called from HTTP handlers, from the Celery pipeline, and from the
Phase 7 backtester, and a package that knew about any one of those could not be
called from the other two.
"""

from __future__ import annotations

from nemesis.policy.documents import PolicyKind, PolicyStatus
from nemesis.policy.errors import (
    ExpressionError,
    PolicyConflictError,
    PolicyError,
    PolicyNotFoundError,
    PolicyTransitionError,
    PolicyValidationError,
)

__all__ = [
    "ExpressionError",
    "PolicyConflictError",
    "PolicyError",
    "PolicyKind",
    "PolicyNotFoundError",
    "PolicyStatus",
    "PolicyTransitionError",
    "PolicyValidationError",
]
