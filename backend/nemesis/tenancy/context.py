"""The ambient tenant for the current request, task, or replay.

A ``ContextVar`` rather than a module global, for the same reason the
correlation id in ``observability.logging`` is one: the API serves concurrent
requests on a single event loop, and a global would let request B read request
A's tenant between two awaits. That failure is silent, intermittent, and a
cross-tenant data leak.

Nothing here *enforces* anything. Enforcement is ``tenancy.guard``, which reads
this value to check that the query about to run is scoped to it. Keeping the two
apart means a code path that legitimately spans tenants — the integrity sweep,
the partition maintainer — declares that intent explicitly instead of
accidentally inheriting whichever tenant happened to be set.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_current_tenant: ContextVar[uuid.UUID | None] = ContextVar("nemesis_tenant_id", default=None)


class TenantContextError(RuntimeError):
    """A tenant-scoped operation ran with no tenant in context."""


def current_tenant_id() -> uuid.UUID | None:
    """The tenant in context, or ``None`` outside any tenant scope."""
    return _current_tenant.get()


def require_tenant_id() -> uuid.UUID:
    """The tenant in context, raising rather than defaulting.

    There is no sensible default tenant. Falling back to "the first one" or "the
    system one" is how a support query written against one customer's data gets
    run against another's.
    """
    tenant_id = _current_tenant.get()
    if tenant_id is None:
        raise TenantContextError(
            "no tenant in context; wrap the operation in tenant_scope(tenant_id)"
        )
    return tenant_id


@contextmanager
def tenant_scope(tenant_id: uuid.UUID) -> Iterator[uuid.UUID]:
    """Bind ``tenant_id`` for the duration of the block.

    Restores the previous value on exit via the token rather than by assigning
    ``None``, so nesting — a support tool impersonating a tenant inside a
    request that already has one — unwinds correctly instead of clearing the
    outer scope.
    """
    token = _current_tenant.set(tenant_id)
    try:
        yield tenant_id
    finally:
        _current_tenant.reset(token)
