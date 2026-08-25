"""Whether a tenant publishes to §26.4's open surface — ADR-0046.

``api/public_deps.py`` states the rule this module exists to serve:

    The risk is publishing a customer's data because the code *can*, which is a
    disclosure decision no engineer is entitled to make on their behalf.

``tenants.public_api_enabled`` defaults to false and 404s while it is. Until
ADR-0046 there was no way to make it true for a real tenant — provisioning does
not accept it and no route set it — so the only available mechanism was an
``UPDATE`` typed into ``psql``, which is the one route that leaves no record of
who decided, when, or on what basis.

This module is that record. One function, called from one route, appending one
``admin_action`` with a justification that is not optional.

**Not part of provisioning, deliberately.** A tenant is born unpublished, and
publishing is a second act taken after somebody has looked at what is in it. The
argument is in the ADR; the mechanism is that ``TenantSpec`` has no field here
and this function takes a slug rather than a spec.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.control_plane.errors import NotFoundError, ValidationError
from nemesis.db.models.tenant import Tenant
from nemesis.domain.lifecycle import EntityType
from nemesis.events.store import EventStore
from nemesis.tenancy.context import tenant_scope

#: The action string on the appended ``admin_action``. One constant, because it
#: is read by whoever greps the chain for a disclosure decision and a typo'd
#: variant would be invisible to exactly that search.
ACTION: str = "public_api_publication_changed"


@dataclass(frozen=True, slots=True)
class PublicationState:
    """What the tenant publishes, after the call.

    ``changed`` is the field the caller actually needs: a PUT is idempotent, and
    a deployment script re-running should be able to tell "I turned this on"
    from "this was already on" without diffing anything.
    """

    tenant_id: uuid.UUID
    slug: str
    enabled: bool
    min_aggregate: int
    changed: bool


async def set_publication(
    session: AsyncSession,
    *,
    slug: str,
    enabled: bool,
    justification: str,
    min_aggregate: int | None,
    floor: int,
    correlation_id: str | None = None,
) -> PublicationState:
    """Turn §26.4's surface on or off for one tenant, and say so in the log.

    ``floor`` is ``public_api.min_aggregate_floor`` — the deployment's own
    minimum, passed in rather than read here so this function stays callable
    from a test without a settings object.

    A requested ``min_aggregate`` below the floor is **refused**, not clamped.
    ``public.policy.clamp_suppression_threshold`` still clamps at read time and
    that is not redundant: clamping protects rows that already exist, where
    failing would take a live page down over a historical mistake. This is a
    person asking for something wrong, right now, and telling them so is the
    entire value of the exchange (ADR-0046).
    """
    if min_aggregate is not None and min_aggregate < floor:
        raise ValidationError(
            f"min_aggregate {min_aggregate} is below this deployment's floor of "
            f"{floor}; a threshold beneath it turns an aggregate endpoint into a "
            f"per-complaint feed, which §26.4 forbids whoever asked for it"
        )

    # tenant-scope-exempt: this IS the tenant lookup, and it runs before there
    # is a scope to bind — the same exemption `api.public_deps` takes for the
    # same reason.
    tenant = (await session.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
    if tenant is None:
        # Not "no such tenant". A control-plane token is a shared secret, not an
        # identity, so the 404-not-403 discipline the rest of this package keeps
        # applies here too rather than confirming a customer list to whoever
        # holds the token.
        raise NotFoundError("no tenant matches that name")

    before_enabled = bool(tenant.public_api_enabled)
    before_threshold = int(tenant.public_api_min_aggregate)
    after_threshold = before_threshold if min_aggregate is None else min_aggregate

    changed = before_enabled != enabled or before_threshold != after_threshold
    if not changed:
        return PublicationState(
            tenant_id=tenant.id,
            slug=tenant.slug,
            enabled=before_enabled,
            min_aggregate=before_threshold,
            changed=False,
        )

    tenant.public_api_enabled = enabled
    tenant.public_api_min_aggregate = after_threshold

    # `admin_action` and not `organisation_changed`: the latter describes
    # structure, and somebody walking a city's chain to answer "when did this
    # start publishing" should not have to filter departments out of the way to
    # find it (ADR-0046).
    with tenant_scope(tenant.id):
        await EventStore(session).append(
            tenant_id=tenant.id,
            entity_type=EntityType.ADMIN_ACTION.value,
            entity_id=uuid.uuid4(),
            event_type="admin_action",
            payload={
                "action": ACTION,
                "target_entity_type": EntityType.TENANT.value,
                "target_entity_id": str(tenant.id),
                "justification": justification,
                "changes": {
                    "public_api_enabled": [before_enabled, enabled],
                    "public_api_min_aggregate": [before_threshold, after_threshold],
                },
            },
            correlation_id=correlation_id,
        )

    return PublicationState(
        tenant_id=tenant.id,
        slug=tenant.slug,
        enabled=enabled,
        min_aggregate=after_threshold,
        changed=True,
    )
