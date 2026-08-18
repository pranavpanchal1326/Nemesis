"""Bring a tenant into existence — one transaction, or none of it.

The Phase 5 gate is that a brand-new tenant with a completely different taxonomy
is onboarded end to end **without a code change or a deploy**. This module is
that sentence made executable.

**Why it is one transaction.** A tenant with a taxonomy and no departments, or
with departments whose routing hints point at nothing, is worse than no tenant:
it accepts complaints and misroutes them. Everything below runs in the caller's
session, so a failure on the fortieth taxonomy node leaves no half-built customer
behind — and the caller can therefore retry with a corrected request rather than
first having to work out what got written.

**Why the template is applied before the request's own lists.** A customer
adopting the campus template and adding four categories of its own should be one
call, not two — the two-call version leaves a window in which the tenant exists
and is wrong. Overrides are additive: a request cannot delete something the
template created, because "apply this template except for the bits I dislike" is
a template of its own and belongs in the library.

**Events.** Provisioning appends exactly two: ``tenant_provisioned`` and one
``taxonomy_published``. Not one ``organisation_changed`` per department — a
template that creates eleven of them is a single operator action, and eleven
events would bury it. What the template created is recoverable from its name and
version, which is why both are recorded on the tenant *and* in the event.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.control_plane import calendars, organisation, taxonomy, templates, translations
from nemesis.control_plane.errors import ConflictError, ValidationError
from nemesis.control_plane.schemas import (
    CalendarSpec,
    DepartmentSpec,
    PromptSetSpec,
    ProvisioningRequest,
    ProvisioningResult,
    ShiftSpec,
    TaxonomyNodeSpec,
    TenantSpec,
    TranslationBundle,
    ZoneSpec,
)
from nemesis.db.models.tenant import Tenant
from nemesis.domain.constants import SYSTEM_TENANT_SLUG
from nemesis.events.store import EventStore
from nemesis.observability.logging import get_logger
from nemesis.policy import service as policy_service

log = get_logger(__name__)

#: Slugs a customer may not take. ``__system__`` is the reserved deployment
#: tenant that owns degradations and integrity findings; letting a customer
#: claim it would put citizen data into the operational audit trail.
RESERVED_SLUGS: Final[frozenset[str]] = frozenset({SYSTEM_TENANT_SLUG})


@dataclass(frozen=True, slots=True)
class _Plan:
    """The template and the request, merged, before anything is written."""

    template_name: str | None
    template_version: str | None
    calendars: list[CalendarSpec]
    departments: list[DepartmentSpec]
    zones: list[ZoneSpec]
    shifts: list[ShiftSpec]
    taxonomy: list[TaxonomyNodeSpec]
    prompt_sets: list[PromptSetSpec]
    translations: list[TranslationBundle]


async def provision(
    session: AsyncSession,
    *,
    request: ProvisioningRequest,
    actor_id: uuid.UUID | None = None,
    correlation_id: str | None = None,
) -> ProvisioningResult:
    """Create a tenant and everything it needs to accept its first complaint."""
    plan = _merge(request)
    _assert_timezone_resolvable(request.tenant.timezone)
    _assert_template_locales_declared(plan, tenant=request.tenant)
    _assert_routing_hints_resolve(plan)

    tenant_id = await _create_tenant(
        session, spec=request.tenant, template=plan.template_name, version=plan.template_version
    )

    await EventStore(session).append(
        entity_id=tenant_id,
        event_type="tenant_provisioned",
        payload={
            "slug": request.tenant.slug,
            "name": request.tenant.name,
            "plan": request.tenant.plan,
            "primary_locale": request.tenant.primary_locale,
            "locales": sorted(request.tenant.locales),
            "timezone": request.tenant.timezone,
            "data_residency": request.tenant.data_residency,
            "template": plan.template_name,
            "template_version": plan.template_version,
        },
        tenant_id=tenant_id,
        actor_id=actor_id,
        correlation_id=correlation_id,
        occurred_at=datetime.now(tz=UTC),
    )

    # Order is a dependency order, not a preference. Calendars first because a
    # department may name one; departments before the taxonomy because a routing
    # hint names one; the taxonomy before prompt sets and shifts before nothing.
    for calendar_spec in plan.calendars:
        await calendars.create_calendar(session, tenant_id=tenant_id, spec=calendar_spec)

    await organisation.import_departments(session, tenant_id=tenant_id, specs=plan.departments)
    await organisation.import_zones(session, tenant_id=tenant_id, specs=plan.zones)

    for shift_spec in plan.shifts:
        await organisation.create_shift(session, tenant_id=tenant_id, spec=shift_spec)

    await taxonomy.import_nodes(session, tenant_id=tenant_id, specs=plan.taxonomy)

    for prompt_spec in plan.prompt_sets:
        await taxonomy.upsert_prompt_set(session, tenant_id=tenant_id, spec=prompt_spec)

    for bundle in plan.translations:
        await translations.import_bundle(session, tenant_id=tenant_id, bundle=bundle)

    revision = await taxonomy.publish(
        session,
        tenant_id=tenant_id,
        change_kind=taxonomy.CHANGE_IMPORTED,
        changed_keys=[node.key for node in plan.taxonomy],
        actor_id=actor_id,
        correlation_id=correlation_id,
    )

    # Phase 6. After the taxonomy, because a severity override or a dedup band
    # names a category — the baselines name none, but a template that carries
    # its own policy documents will, and the ordering should not depend on which
    # kind of tenant is being provisioned. A tenant leaves this function
    # *governed*: the first complaint it accepts is scored by an approved rubric
    # with a version number, not by a fallback.
    seeded_policies = await policy_service.seed_baselines(
        session, tenant_id=tenant_id, actor_id=actor_id, correlation_id=correlation_id
    )

    counts = {
        "taxonomy_nodes": len(plan.taxonomy),
        "departments": len(plan.departments),
        "zones": len(plan.zones),
        "shifts": len(plan.shifts),
        "calendars": len(plan.calendars),
        "prompt_sets": len(plan.prompt_sets),
        "translation_bundles": len(plan.translations),
        "policies": len(seeded_policies),
    }
    log.info(
        "tenant_provisioned",
        tenant_id=str(tenant_id),
        slug=request.tenant.slug,
        template=plan.template_name,
        **counts,
    )

    return ProvisioningResult(
        tenant_id=tenant_id,
        slug=request.tenant.slug,
        template=plan.template_name,
        template_version=plan.template_version,
        taxonomy_revision=revision,
        counts=counts,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _merge(request: ProvisioningRequest) -> _Plan:
    """Template first, then the request's own additions.

    Collisions are refused rather than resolved. A request that re-declares a
    key the template already defines is either a misunderstanding of what the
    template contains or an attempt to override it, and both deserve an error
    naming the key rather than a silent last-writer-wins.
    """
    if request.template is None:
        return _Plan(
            template_name=None,
            template_version=None,
            calendars=list(request.calendars),
            departments=list(request.departments),
            zones=list(request.zones),
            shifts=list(request.shifts),
            taxonomy=list(request.taxonomy),
            prompt_sets=list(request.prompt_sets),
            translations=list(request.translations),
        )

    template = templates.load(request.template)

    _assert_no_collision(
        "taxonomy key",
        [node.key for node in template.taxonomy],
        [node.key for node in request.taxonomy],
    )
    _assert_no_collision(
        "department code",
        [department.code for department in template.departments],
        [department.code for department in request.departments],
    )
    _assert_no_collision(
        "zone code",
        [zone.code for zone in template.zones],
        [zone.code for zone in request.zones],
    )
    _assert_no_collision(
        "calendar code",
        [calendar.code for calendar in template.calendars],
        [calendar.code for calendar in request.calendars],
    )

    return _Plan(
        template_name=template.name,
        template_version=template.version,
        calendars=[*template.calendars, *request.calendars],
        departments=[*template.departments, *request.departments],
        zones=[*template.zones, *request.zones],
        shifts=[*template.shifts, *request.shifts],
        taxonomy=[*template.taxonomy, *request.taxonomy],
        prompt_sets=[*template.prompt_sets, *request.prompt_sets],
        translations=[*template.translations, *request.translations],
    )


def _assert_no_collision(label: str, from_template: list[str], from_request: list[str]) -> None:
    clashes = sorted(set(from_template) & set(from_request))
    if clashes:
        raise ValidationError(
            f"{label}(s) {clashes} are defined by both the template and the request. "
            f"Provisioning is additive: rename them, or provision without the template "
            f"and supply the whole set explicitly."
        )


def _assert_timezone_resolvable(timezone: str) -> None:
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValidationError(
            f"{timezone!r} is not an IANA timezone. Every SLA deadline, business "
            f"calendar, and 72-hour dedup window for this tenant is computed against "
            f"it, so an unresolvable zone is not a cosmetic error."
        ) from exc


def _assert_template_locales_declared(plan: _Plan, *, tenant: TenantSpec) -> None:
    """Every locale the plan writes strings in must be one the tenant declared.

    Checked before the first write rather than at each import, so the error names
    the whole set at once — "your template speaks Marathi and your tenant does
    not" is a single decision to make, not five identical rejections.
    """
    used = {bundle.locale for bundle in plan.translations}
    used |= {prompt.locale for prompt in plan.prompt_sets}
    for node in plan.taxonomy:
        used |= set(node.translations)
    for department in plan.departments:
        used |= set(department.translations)
    for zone in plan.zones:
        used |= set(zone.translations)

    undeclared = sorted(used - set(tenant.locales))
    if undeclared:
        raise ValidationError(
            f"the plan writes strings in {undeclared}, which this tenant has not "
            f"declared (declared: {sorted(tenant.locales)}). Rows in an undeclared "
            f"locale are never resolved by anything, so they read on screen exactly "
            f"like a missing translation."
        )


def _assert_routing_hints_resolve(plan: _Plan) -> None:
    """A routing hint naming a department that does not exist is a silent misroute.

    Validated across the *merged* plan rather than per source, because a
    perfectly reasonable request adds a category routed to a department the
    template created. Checking the two halves separately would reject it.
    """
    known = {department.code for department in plan.departments}
    dangling = sorted(
        {
            node.routing_hints.department_code
            for node in plan.taxonomy
            if node.routing_hints.department_code is not None
            and node.routing_hints.department_code not in known
        }
    )
    if dangling:
        raise ValidationError(
            f"routing hints reference department code(s) {dangling}, which this plan "
            f"does not create. A hint that points nowhere looks identical on screen to "
            f"one that works, and the complaint it should have routed goes nowhere."
        )


async def _create_tenant(
    session: AsyncSession, *, spec: TenantSpec, template: str | None, version: str | None
) -> uuid.UUID:
    if spec.slug in RESERVED_SLUGS:
        raise ConflictError(
            f"{spec.slug!r} is reserved for the deployment's own audit trail and "
            f"cannot be claimed by a customer"
        )

    # tenant-scope-exempt: `tenants` is the tenant registry; its primary key IS
    # the tenant, so there is nothing to scope this lookup by.
    existing = await session.execute(select(Tenant.id).where(Tenant.slug == spec.slug))
    if existing.scalar_one_or_none() is not None:
        raise ConflictError(f"tenant slug {spec.slug!r} is already taken")

    tenant = Tenant(
        id=uuid.uuid4(),
        slug=spec.slug,
        name=spec.name,
        plan=spec.plan,
        primary_locale=spec.primary_locale,
        locales=sorted(set(spec.locales)),
        timezone=spec.timezone,
        data_residency=spec.data_residency,
        branding=spec.branding,
        provisioned_from_template=template,
        template_version=version,
        is_active=True,
    )
    session.add(tenant)
    try:
        await session.flush()
    except IntegrityError as exc:  # pragma: no cover - the pre-check above wins the race
        raise ConflictError(f"tenant slug {spec.slug!r} is already taken") from exc
    return tenant.id
