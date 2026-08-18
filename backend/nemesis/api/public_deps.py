"""Resolving a tenant on an *unauthenticated* surface, and the opt-in gate.

The §26.4 endpoints take no auth by design — that is what makes the platform
infrastructure other tools can build on rather than a closed app. But this is a
multi-tenant deployment, so the request still has to name a tenant, and it
cannot do it the way every other endpoint does.

**Why the slug is in the path and not in a header.** ``X-Tenant-ID`` is a UUID
and an internal handle; asking a journalist to obtain one before they can call a
public API defeats the purpose. A slug is the tenant's own public name, it is
already unique, and it makes the URL something a person can read, share, and put
in a citation — which is what §16.2's "bookmark-able by journalists" means.

**Why the tenant must opt in.** Enumeration is not the risk here: the whole
point is that these figures are public. The risk is publishing a customer's data
because the code *can*, which is a disclosure decision no engineer is entitled to
make on their behalf. ``tenants.public_api_enabled`` defaults to false, and a
tenant that has not enabled it is 404 rather than 403 — a distinguishable
"exists but is not publishing" would still confirm the customer list to anyone
who wanted to compile one.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Path, Request
from sqlalchemy import select

from nemesis.api.errors import (
    HTTP_404_NOT_FOUND,
    PROBLEM_BASE,
    ProblemDetailError,
)
from nemesis.api.ratelimit import get_limiter
from nemesis.config import Settings, get_settings
from nemesis.db.models.tenant import Tenant
from nemesis.db.session import session_scope
from nemesis.observability import metrics
from nemesis.public.policy import clamp_suppression_threshold

#: Slugs are ``String(64)`` and tenant-chosen. Bounded in the path so a
#: multi-kilobyte path segment never reaches a query — the endpoint is
#: unauthenticated, so the cheapest possible rejection is the right one.
SLUG_MAX_LENGTH = 64


@dataclass(frozen=True, slots=True)
class PublicTenant:
    """A tenant that has agreed to publish, and the floor it publishes above."""

    id: uuid.UUID
    slug: str
    name: str
    #: Already clamped to the deployment floor. The route never sees the raw
    #: tenant column, so there is no code path where somebody uses the
    #: unclamped value by reaching for the wrong attribute.
    suppression_threshold: int


async def require_public_tenant(
    tenant_slug: Annotated[str, Path(max_length=SLUG_MAX_LENGTH, pattern=r"^[a-z0-9][a-z0-9-]*$")],
    request: Request,
) -> PublicTenant:
    """Resolve a publishing tenant from its slug, or 404.

    Opens its own short session for the reason ``require_tenant`` does: the
    tenant has to be known before the route's transaction begins, and this is a
    single indexed read against a table with as many rows as the deployment has
    customers.
    """
    settings = _settings_of(request)
    if not settings.public_api.enabled:
        # A deployment can turn the whole surface off — a kill switch that does
        # not depend on every tenant's individual setting being correct.
        raise _not_found()

    # tenant-scope-exempt: this IS the tenant lookup. `tenants` is the one table
    # whose primary key is the tenant, so there is nothing to scope it by.
    async with session_scope() as session:
        row = (
            await session.execute(
                select(
                    Tenant.id,
                    Tenant.slug,
                    Tenant.name,
                    Tenant.public_api_min_aggregate,
                ).where(
                    Tenant.slug == tenant_slug,
                    Tenant.is_active.is_(True),
                    Tenant.public_api_enabled.is_(True),
                )
            )
        ).one_or_none()

    if row is None:
        raise _not_found()

    return PublicTenant(
        id=row[0],
        slug=row[1],
        name=row[2],
        suppression_threshold=clamp_suppression_threshold(
            row[3], settings.public_api.min_aggregate_floor
        ),
    )


def _not_found() -> ProblemDetailError:
    """404 for "no such tenant", "suspended", and "not publishing" alike.

    Three different internal states, one external answer. Distinguishing them
    would let anyone compile the deployment's customer list and, worse, learn
    which customers have declined to publish — which is a statement about a
    public body that this system has no business making on its behalf.
    """
    return ProblemDetailError(
        status_code=HTTP_404_NOT_FOUND,
        title="No public data",
        detail=(
            "No organisation publishes transparency data under that name. "
            "See /developers for the organisations that do."
        ),
        problem_type=f"{PROBLEM_BASE}/not-found",
    )


def _settings_of(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    return settings if isinstance(settings, Settings) else get_settings()


async def enforce_public_rate_limit(request: Request) -> None:
    """§26.4's 60 requests per minute per IP, on the shared token bucket.

    **Keyed by client address, which is the weakest identity available and the
    only one there is.** An unauthenticated endpoint has nothing else — and that
    is stated rather than dressed up, because a limit keyed on an address behind
    a shared NAT throttles a whole office building together, and one behind a
    rotating proxy throttles nobody. The control this actually provides is
    against a naive scraper; §26.4's real answer for a serious consumer is to
    issue them a key with a real quota, which is why ``api_keys`` exists.

    Fails open, counted, for the reason ADR-0017 gives — and the reason is
    weaker here, so it is worth restating: on the ingest path a fail-closed
    limiter drops a citizen's hazard report. On this path it only makes a
    transparency page unavailable. It still fails open, because a Redis outage
    turning the public accountability surface dark is a worse look than a few
    minutes of unbounded scraping, and the outage is already alerting.
    """
    settings = _settings_of(request)
    if not settings.rate_limit.enabled:
        return

    limiter = get_limiter(settings.redis_url, settings.rate_limit)
    identity = request.client.host if request.client else "unknown"
    decision = await limiter.check_tier(
        tier_name="public_anonymous",
        tier=settings.public_api.anonymous_tier,
        # A fixed namespace rather than a tenant: the §26.4 budget is per IP
        # across the whole public surface, so spending it against one tenant and
        # then moving to another would make the published number a fiction.
        namespace="public",
        identity=identity,
    )
    if decision.allowed:
        return

    metrics.public_api_requests_total.labels(endpoint="*", outcome="rate_limited").inc()
    from nemesis.api.errors import HTTP_429_TOO_MANY_REQUESTS

    raise ProblemDetailError(
        status_code=HTTP_429_TOO_MANY_REQUESTS,
        title="Rate limit exceeded",
        detail=(
            "The public API allows a limited number of requests per minute per client. "
            "Retry in "
            f"{decision.retry_after_seconds} seconds, or request an API key for a higher "
            "quota — see /developers."
        ),
        problem_type=f"{PROBLEM_BASE}/rate-limited",
        extra={"retry_after_seconds": decision.retry_after_seconds},
    )


PublicTenantDep = Annotated[PublicTenant, Depends(require_public_tenant)]
PublicRateLimit = Depends(enforce_public_rate_limit)
