"""Request-scoped dependencies: which tenant, which session, which limits.

**Tenant resolution is a header, and this is not authentication.** Phase 13 owns
identity, and until it lands there is no token to read a tenant claim from. The
options were to invent a placeholder auth scheme now — which Phase 13 would
delete, having taught everyone a shape that was never real — or to resolve the
tenant from an explicit header, validate it against the registry, and say
plainly what it is.

This is the second. ``X-Tenant-ID`` names a tenant; it does not prove anything
about who is asking. Every consequence of that is confined to this module, so
Phase 13's change is one function body: resolve the tenant from verified token
claims instead of from a header, and every route downstream is unaffected
because they all already depend on ``TenantContext`` rather than on the request.

The validation that *is* real: the id must parse, must exist, and must be an
active tenant. An unknown tenant gets 404 and not 403, deliberately — a
distinguishable "exists but forbidden" would let an unauthenticated caller
enumerate the customer list one request at a time, which is the disclosure
§25 treats error responses as a surface for.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated, Final

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.api.errors import (
    HTTP_400_BAD_REQUEST,
    HTTP_404_NOT_FOUND,
    PROBLEM_BASE,
    ProblemDetailError,
)
from nemesis.config import Settings, get_settings
from nemesis.db.models.tenant import Tenant
from nemesis.db.session import session_scope
from nemesis.domain.constants import SYSTEM_TENANT_ID

TENANT_HEADER: Final = "X-Tenant-ID"


@dataclass(frozen=True, slots=True)
class TenantContext:
    """The resolved tenant for one request."""

    id: uuid.UUID
    slug: str
    plan: str
    primary_locale: str


async def get_session() -> AsyncIterator[AsyncSession]:
    """A transactional session for the request. Commits on success."""
    async with session_scope() as session:
        yield session


def get_config(request: Request) -> Settings:
    """Settings from application state, falling back to the process singleton.

    Read from ``app.state`` first so a test that builds an app with overridden
    settings gets those settings in its handlers, rather than the cached
    process-wide ones that were parsed from whatever ``.env`` happened to exist.
    """
    settings = getattr(request.app.state, "settings", None)
    return settings if isinstance(settings, Settings) else get_settings()


async def require_tenant(
    x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
) -> TenantContext:
    """Resolve and validate the tenant named by the header.

    Opens its own short session rather than sharing the request's. The tenant
    has to be known *before* the route's transaction begins — a submission that
    turns out to belong to an unknown tenant should never have opened one — and
    the lookup is a primary-key read against a table with as many rows as the
    deployment has customers.
    """
    if not x_tenant_id:
        raise ProblemDetailError(
            status_code=HTTP_400_BAD_REQUEST,
            title="Tenant not specified",
            detail=f"Supply the {TENANT_HEADER} header.",
            problem_type=f"{PROBLEM_BASE}/tenant-required",
        )
    try:
        tenant_id = uuid.UUID(x_tenant_id)
    except ValueError:
        raise ProblemDetailError(
            status_code=HTTP_400_BAD_REQUEST,
            title="Tenant not specified",
            detail=f"{TENANT_HEADER} must be a UUID.",
            problem_type=f"{PROBLEM_BASE}/tenant-required",
        ) from None

    if tenant_id == SYSTEM_TENANT_ID:
        # The reserved deployment tenant owns integrity findings and
        # degradations. It is not a customer, it has no complaints, and letting
        # a request address it would mean citizen data could be written into the
        # operational audit trail.
        raise _unknown_tenant()

    # tenant-scope-exempt: this IS the tenant lookup. `tenants` is the one table
    # whose primary key is the tenant, so there is nothing to scope it by.
    async with session_scope() as session:
        row = (
            await session.execute(
                select(Tenant.id, Tenant.slug, Tenant.plan, Tenant.primary_locale).where(
                    Tenant.id == tenant_id, Tenant.is_active.is_(True)
                )
            )
        ).one_or_none()

    if row is None:
        raise _unknown_tenant()

    return TenantContext(id=row[0], slug=row[1], plan=row[2], primary_locale=row[3])


def _unknown_tenant() -> ProblemDetailError:
    """404 for both "no such tenant" and "suspended tenant", on purpose.

    Distinguishing them tells an unauthenticated caller which tenant ids are
    real, which turns the header into an enumeration oracle for the customer
    list.
    """
    return ProblemDetailError(
        status_code=HTTP_404_NOT_FOUND,
        title="Tenant not found",
        detail="No active tenant matches the supplied identifier.",
        problem_type=f"{PROBLEM_BASE}/tenant-not-found",
    )


TenantDep = Annotated[TenantContext, Depends(require_tenant)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ConfigDep = Annotated[Settings, Depends(get_config)]
