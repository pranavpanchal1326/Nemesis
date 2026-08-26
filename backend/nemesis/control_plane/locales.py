"""Which languages a tenant speaks — A2, Phase 18's gate.

Phase 18 states the gate in one sentence:

    A locale added in the control plane appears in the UI with no code change.

Until this module existed the first half of that sentence had no mechanism.
``ProvisioningRequest`` accepts ``locales`` **once**, at birth; nothing could
change them afterwards. So adding Konkani to a city that had been running for a
year meant an ``UPDATE`` typed into ``psql`` — which is the state ADR-0046
described for publication, in the same words, for the same reason: *"the one
route that leaves no record of who decided, when, or on what basis."*

**Why a route rather than re-provisioning.** Re-running ``POST /tenants``
answers 409, and rightly: provisioning is the creation of a taxonomy, a
department tree and a zone set, and a city that wants one more language has not
asked for any of that. A locale change is a small, reversible, frequently-made
decision, and it deserves a door of its own.

**Why it is not simply a column write.** A tenant's locale list is the list its
public surface offers readers (``api/public_deps.PublicTenant.locales``), so
adding one is a *published* change: the next reader sees a language link that
was not there before. That is a decision somebody made, and this module records
it on the tenant's chain as an ``admin_action`` with a justification that is not
optional — the same discipline, and the same argument, as publication.

**Removing a locale is allowed, and is the interesting case.** A locale that is
withdrawn while readers hold bookmarks to ``?locale=kok`` degrades to the source
language rather than 404ing, because ``negotiateLocale`` falls back key by key
and ``notices.resolve`` falls back to the canonical English. Nothing is lost;
the translations stay in the table. What changes is what the tenant *offers*.
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

#: The action string on the appended ``admin_action``. One constant, for the
#: reason `publication.ACTION` is one: it is what somebody greps the chain for.
ACTION: str = "tenant_locales_changed"


@dataclass(frozen=True, slots=True)
class LocaleState:
    """What the tenant declares, after the call."""

    tenant_id: uuid.UUID
    slug: str
    primary_locale: str
    locales: tuple[str, ...]
    #: A PUT is idempotent, and a deployment script re-running should be able to
    #: tell "I added Konkani" from "Konkani was already there".
    changed: bool


async def set_locales(
    session: AsyncSession,
    *,
    slug: str,
    locales: list[str],
    primary_locale: str | None,
    justification: str,
    correlation_id: str | None = None,
) -> LocaleState:
    """Declare the languages this tenant offers, and say so in the log.

    ``primary_locale`` is optional: a city adding a fourth language is usually
    not changing which one its own staff work in, and requiring it to be
    restated is how it eventually gets restated wrongly.
    """
    # Order is preserved and duplicates are dropped, because this list is
    # rendered as a language switch somebody reads top to bottom.
    declared: list[str] = []
    for locale in locales:
        if locale not in declared:
            declared.append(locale)
    if not declared:
        raise ValidationError(
            "a tenant must declare at least one locale; an empty list is not "
            "'no localisation', it is a surface with no language at all"
        )

    # tenant-scope-exempt: this IS the tenant lookup, and it runs before there
    # is a scope to bind — the same exemption `publication` takes.
    tenant = (await session.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
    if tenant is None:
        # 404 and not 403: a control-plane token is a shared secret rather than
        # an identity, so this route does not confirm a customer list to
        # whoever holds it.
        raise NotFoundError("no tenant matches that name")

    after_primary = primary_locale if primary_locale is not None else str(tenant.primary_locale)
    if after_primary not in declared:
        raise ValidationError(
            f"the primary locale {after_primary!r} is not in the declared list; a tenant "
            f"whose own working language is not one it offers is a misconfiguration, not "
            f"a preference"
        )

    before_primary = str(tenant.primary_locale)
    before_locales = tuple(tenant.locales or ())
    changed = before_locales != tuple(declared) or before_primary != after_primary

    if not changed:
        return LocaleState(
            tenant_id=tenant.id,
            slug=tenant.slug,
            primary_locale=before_primary,
            locales=before_locales,
            changed=False,
        )

    tenant.locales = declared
    tenant.primary_locale = after_primary

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
                    "locales": [list(before_locales), declared],
                    "primary_locale": [before_primary, after_primary],
                },
            },
            correlation_id=correlation_id,
        )

    return LocaleState(
        tenant_id=tenant.id,
        slug=tenant.slug,
        primary_locale=after_primary,
        locales=tuple(declared),
        changed=True,
    )
