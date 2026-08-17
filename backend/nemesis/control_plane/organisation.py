"""Departments, zones, shifts, contractors, and certification scopes.

Two trees with identical mechanics and different meanings — see
``db.models.organisation`` for why they are not one table. The shared mechanics
live in ``control_plane.hierarchy``; what is here is the part that differs: a
department can carry a calendar and hold shifts, a zone can carry a boundary.

**Every write emits ``organisation_changed`` on the tenant chain.** Not for
tidiness: §17 audits assignments for favouritism, and "the contractor was
certified for this category at the time" is only answerable if the certification
grant is in the log rather than only in a row that has since been edited.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any, Final

from geoalchemy2 import WKTElement
from sqlalchemy import Select, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.control_plane import hierarchy
from nemesis.control_plane.errors import ConflictError, NotFoundError, ValidationError
from nemesis.control_plane.schemas import (
    CertificationSpec,
    ContractorSpec,
    DepartmentSpec,
    ShiftSpec,
    ZoneSpec,
)
from nemesis.control_plane.taxonomy import require_node
from nemesis.db.models.calendar import BusinessCalendar
from nemesis.db.models.i18n import NAMESPACE_ORGANISATION, NAMESPACE_ZONE, Translation
from nemesis.db.models.organisation import (
    MAX_ORG_DEPTH,
    Contractor,
    ContractorCertification,
    Department,
    Shift,
    Zone,
)
from nemesis.domain.lifecycle import EntityType
from nemesis.events.store import EventStore

TENANT_ENTITY: Final = EntityType.TENANT.value

SUBJECT_DEPARTMENT: Final = "department"
SUBJECT_ZONE: Final = "zone"
SUBJECT_SHIFT: Final = "shift"
SUBJECT_CALENDAR: Final = "calendar"
SUBJECT_CERTIFICATION: Final = "certification"

CHANGE_CREATED: Final = "created"
CHANGE_UPDATED: Final = "updated"


async def record_change(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    subject: str,
    subject_key: str,
    subject_id: uuid.UUID,
    change_kind: str,
    changed_fields: Sequence[str] = (),
    actor_id: uuid.UUID | None = None,
    correlation_id: str | None = None,
) -> None:
    """Append one ``organisation_changed`` event in the caller's transaction.

    Exposed rather than private because provisioning batches many changes and
    decides for itself which of them are worth an event — a template that
    creates eleven departments is one operator action, and eleven events would
    make the tenant chain unreadable at exactly the moment it is most useful.
    """
    await EventStore(session).append(
        entity_id=tenant_id,
        event_type="organisation_changed",
        payload={
            "subject": subject,
            "subject_key": subject_key,
            "subject_id": str(subject_id),
            "change_kind": change_kind,
            "changed_fields": sorted(set(changed_fields)),
        },
        tenant_id=tenant_id,
        actor_id=actor_id,
        correlation_id=correlation_id,
        occurred_at=datetime.now(tz=UTC),
    )


# ---------------------------------------------------------------------------
# Departments — the responsibility tree
# ---------------------------------------------------------------------------


def _department_query(tenant_id: uuid.UUID, *, include_inactive: bool) -> Select[Any]:
    statement = select(Department).where(Department.tenant_id == tenant_id)
    if not include_inactive:
        statement = statement.where(Department.is_active.is_(True))
    return statement.order_by(Department.path)


async def get_department(
    session: AsyncSession, *, tenant_id: uuid.UUID, code: str
) -> Department | None:
    row = await session.execute(
        select(Department).where(Department.tenant_id == tenant_id, Department.code == code)
    )
    return row.scalar_one_or_none()


async def require_department(
    session: AsyncSession, *, tenant_id: uuid.UUID, code: str
) -> Department:
    department = await get_department(session, tenant_id=tenant_id, code=code)
    if department is None:
        raise NotFoundError(f"no department {code!r} for this tenant")
    return department


async def list_departments(
    session: AsyncSession, *, tenant_id: uuid.UUID, include_inactive: bool = False
) -> list[Department]:
    rows = await session.execute(_department_query(tenant_id, include_inactive=include_inactive))
    return list(rows.scalars().all())


async def create_department(
    session: AsyncSession, *, tenant_id: uuid.UUID, spec: DepartmentSpec
) -> Department:
    if await get_department(session, tenant_id=tenant_id, code=spec.code) is not None:
        raise ConflictError(f"department code {spec.code!r} already exists for this tenant")

    parent: Department | None = None
    if spec.parent_code is not None:
        parent = await require_department(session, tenant_id=tenant_id, code=spec.parent_code)

    path = (
        spec.code
        if parent is None
        else hierarchy.build_path(hierarchy.path_keys(parent.path), spec.code)
    )
    hierarchy.assert_within_depth(path, maximum=MAX_ORG_DEPTH, label="department")

    calendar_id: uuid.UUID | None = None
    if spec.calendar_code is not None:
        calendar_id = await _calendar_id(session, tenant_id=tenant_id, code=spec.calendar_code)

    department = Department(
        tenant_id=tenant_id,
        name=spec.name,
        code=spec.code,
        kind=spec.kind,
        parent_id=None if parent is None else parent.id,
        path=path,
        depth=hierarchy.depth_of(path),
        is_assignable=spec.is_assignable,
        is_active=spec.is_active,
        calendar_id=calendar_id,
        ward=spec.ward,
        attributes=spec.attributes,
    )
    session.add(department)
    try:
        await session.flush()
    except IntegrityError as exc:  # pragma: no cover - the pre-check above wins the race
        raise ConflictError(f"department code {spec.code!r} already exists") from exc

    await _write_translations(
        session,
        tenant_id=tenant_id,
        namespace=NAMESPACE_ORGANISATION,
        message_key=spec.code,
        translations=spec.translations,
    )
    return department


async def import_departments(
    session: AsyncSession, *, tenant_id: uuid.UUID, specs: Sequence[DepartmentSpec]
) -> list[Department]:
    """Create a whole org chart, parents before children."""
    ordered = _ordered_by_parent(
        specs,
        key_of=lambda spec: spec.code,
        parent_of=lambda spec: spec.parent_code,
        label="department",
    )
    return [await create_department(session, tenant_id=tenant_id, spec=spec) for spec in ordered]


# ---------------------------------------------------------------------------
# Zones — the place tree
# ---------------------------------------------------------------------------


async def get_zone(session: AsyncSession, *, tenant_id: uuid.UUID, code: str) -> Zone | None:
    row = await session.execute(select(Zone).where(Zone.tenant_id == tenant_id, Zone.code == code))
    return row.scalar_one_or_none()


async def require_zone(session: AsyncSession, *, tenant_id: uuid.UUID, code: str) -> Zone:
    zone = await get_zone(session, tenant_id=tenant_id, code=code)
    if zone is None:
        raise NotFoundError(f"no zone {code!r} for this tenant")
    return zone


async def list_zones(
    session: AsyncSession, *, tenant_id: uuid.UUID, include_inactive: bool = False
) -> list[Zone]:
    statement = select(Zone).where(Zone.tenant_id == tenant_id)
    if not include_inactive:
        statement = statement.where(Zone.is_active.is_(True))
    rows = await session.execute(statement.order_by(Zone.path))
    return list(rows.scalars().all())


async def create_zone(session: AsyncSession, *, tenant_id: uuid.UUID, spec: ZoneSpec) -> Zone:
    if await get_zone(session, tenant_id=tenant_id, code=spec.code) is not None:
        raise ConflictError(f"zone code {spec.code!r} already exists for this tenant")

    parent: Zone | None = None
    if spec.parent_code is not None:
        parent = await require_zone(session, tenant_id=tenant_id, code=spec.parent_code)

    path = (
        spec.code
        if parent is None
        else hierarchy.build_path(hierarchy.path_keys(parent.path), spec.code)
    )
    hierarchy.assert_within_depth(path, maximum=MAX_ORG_DEPTH, label="zone")

    zone = Zone(
        tenant_id=tenant_id,
        name=spec.name,
        code=spec.code,
        kind=spec.kind,
        parent_id=None if parent is None else parent.id,
        path=path,
        depth=hierarchy.depth_of(path),
        boundary=_boundary_of(spec),
        is_active=spec.is_active,
        attributes=spec.attributes,
    )
    session.add(zone)
    try:
        await session.flush()
    except IntegrityError as exc:  # pragma: no cover - the pre-check above wins the race
        raise ConflictError(f"zone code {spec.code!r} already exists") from exc

    if spec.boundary is not None:
        # Computed by PostGIS from the stored boundary, in a second statement, so
        # the centroid can never disagree with the polygon it summarises. Doing
        # the area-weighted arithmetic in Python would mean reimplementing a
        # solved problem and getting a different answer from every containment
        # query, which uses PostGIS.
        await session.execute(
            update(Zone)
            .where(Zone.tenant_id == tenant_id, Zone.id == zone.id)
            .values(centroid=func.ST_Centroid(Zone.boundary))
        )
        await session.flush()
        session.expire(zone)

    await _write_translations(
        session,
        tenant_id=tenant_id,
        namespace=NAMESPACE_ZONE,
        message_key=spec.code,
        translations=spec.translations,
    )
    return zone


async def import_zones(
    session: AsyncSession, *, tenant_id: uuid.UUID, specs: Sequence[ZoneSpec]
) -> list[Zone]:
    ordered = _ordered_by_parent(
        specs, key_of=lambda spec: spec.code, parent_of=lambda spec: spec.parent_code, label="zone"
    )
    return [await create_zone(session, tenant_id=tenant_id, spec=spec) for spec in ordered]


def _boundary_of(spec: ZoneSpec) -> WKTElement | None:
    """Validated GeoJSON coordinates as WKT PostGIS will accept.

    Built as text rather than through ``shapely``. GeoAlchemy2 works without
    shapely and the base image does not carry it; adding a compiled geometry
    library to the API and both worker images to format a string is a poor
    trade, and the string is bounded — the rings have already been validated as
    closed, in range, and long enough by ``ZoneSpec``.

    ``WKTElement`` rather than a bare string so the SRID travels with the value.
    A WKT string with no SRID is inserted into a ``geography(...,4326)`` column
    as 4326 *by assumption*, which is right until somebody changes the column.
    """
    if spec.boundary is None:
        return None
    polygons: list[str] = []
    for rings in spec.boundary:
        if not rings:
            continue
        rendered_rings = [
            "(" + ", ".join(f"{longitude} {latitude}" for longitude, latitude in ring) + ")"
            for ring in rings
        ]
        polygons.append("(" + ", ".join(rendered_rings) + ")")
    if not polygons:
        return None
    return WKTElement(f"MULTIPOLYGON({', '.join(polygons)})", srid=4326)


# ---------------------------------------------------------------------------
# Shifts
# ---------------------------------------------------------------------------


async def create_shift(session: AsyncSession, *, tenant_id: uuid.UUID, spec: ShiftSpec) -> Shift:
    department = await require_department(session, tenant_id=tenant_id, code=spec.department_code)
    existing = await session.execute(
        select(Shift.id).where(
            Shift.tenant_id == tenant_id,
            Shift.department_id == department.id,
            Shift.code == spec.code,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise ConflictError(
            f"shift {spec.code!r} already exists for department {spec.department_code!r}"
        )

    shift = Shift(
        tenant_id=tenant_id,
        department_id=department.id,
        code=spec.code,
        name=spec.name,
        weekdays=list(spec.weekdays),
        starts_at=spec.starts_at,
        ends_at=spec.ends_at,
        effective_from=spec.effective_from,
        effective_to=spec.effective_to,
        is_active=spec.is_active,
    )
    session.add(shift)
    await session.flush()
    return shift


async def list_shifts(
    session: AsyncSession, *, tenant_id: uuid.UUID, department_code: str | None = None
) -> list[Shift]:
    statement = select(Shift).where(Shift.tenant_id == tenant_id)
    if department_code is not None:
        department = await require_department(session, tenant_id=tenant_id, code=department_code)
        statement = statement.where(Shift.department_id == department.id)
    rows = await session.execute(statement.order_by(Shift.code))
    return list(rows.scalars().all())


# ---------------------------------------------------------------------------
# Contractors and certification scopes
# ---------------------------------------------------------------------------


async def register_contractor(
    session: AsyncSession, *, tenant_id: uuid.UUID, spec: ContractorSpec
) -> Contractor:
    existing = await session.execute(
        select(Contractor).where(
            Contractor.tenant_id == tenant_id,
            Contractor.registration_id == spec.registration_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise ConflictError(
            f"contractor {spec.registration_id!r} is already registered for this tenant"
        )

    contractor = Contractor(
        tenant_id=tenant_id,
        name=spec.name,
        registration_id=spec.registration_id,
        registered_address=spec.registered_address,
        phone=spec.phone,
        director_names=list(spec.director_names),
        active_since=spec.active_since,
    )
    session.add(contractor)
    await session.flush()
    return contractor


async def certify(
    session: AsyncSession, *, tenant_id: uuid.UUID, spec: CertificationSpec
) -> ContractorCertification:
    """Grant a contractor a scope over one taxonomy node.

    Both sides are resolved by their tenant-facing identifiers and both lookups
    are tenant-scoped, so a certification can never be granted across a tenant
    boundary — the case that would let one municipality's contractor appear in
    another's assignment ranking.
    """
    contractor = (
        await session.execute(
            select(Contractor).where(
                Contractor.tenant_id == tenant_id,
                Contractor.registration_id == spec.contractor_registration_id,
            )
        )
    ).scalar_one_or_none()
    if contractor is None:
        raise NotFoundError(f"no contractor {spec.contractor_registration_id!r} for this tenant")

    node = await require_node(session, tenant_id=tenant_id, key=spec.taxonomy_key)

    existing = (
        await session.execute(
            select(ContractorCertification).where(
                ContractorCertification.tenant_id == tenant_id,
                ContractorCertification.contractor_id == contractor.id,
                ContractorCertification.taxonomy_node_id == node.id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError(
            f"contractor {spec.contractor_registration_id!r} is already certified for "
            f"{spec.taxonomy_key!r}; update the validity window rather than granting it twice"
        )

    certification = ContractorCertification(
        tenant_id=tenant_id,
        contractor_id=contractor.id,
        taxonomy_node_id=node.id,
        certificate_ref=spec.certificate_ref,
        valid_from=spec.valid_from,
        valid_until=spec.valid_until,
    )
    session.add(certification)
    await session.flush()
    return certification


async def certified_contractors(
    session: AsyncSession, *, tenant_id: uuid.UUID, taxonomy_key: str, on: datetime | None = None
) -> list[Contractor]:
    """Contractors whose certification for a category is valid on a given date.

    ``on`` defaults to now. It is a parameter rather than an implicit ``now()``
    because §17's audit asks the question retrospectively — "was this contractor
    certified on the day it was assigned" — and a function that can only answer
    for today cannot answer that at all.
    """
    node = await require_node(session, tenant_id=tenant_id, key=taxonomy_key)
    when = (on or datetime.now(tz=UTC)).date()

    rows = await session.execute(
        select(Contractor)
        .join(
            ContractorCertification,
            ContractorCertification.contractor_id == Contractor.id,
        )
        .where(
            Contractor.tenant_id == tenant_id,
            ContractorCertification.tenant_id == tenant_id,
            ContractorCertification.taxonomy_node_id == node.id,
            (ContractorCertification.valid_from.is_(None))
            | (ContractorCertification.valid_from <= when),
            (ContractorCertification.valid_until.is_(None))
            | (ContractorCertification.valid_until >= when),
        )
        .order_by(Contractor.name)
    )
    return list(rows.scalars().all())


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _calendar_id(session: AsyncSession, *, tenant_id: uuid.UUID, code: str) -> uuid.UUID:
    row = await session.execute(
        select(BusinessCalendar.id).where(
            BusinessCalendar.tenant_id == tenant_id, BusinessCalendar.code == code
        )
    )
    calendar_id = row.scalar_one_or_none()
    if calendar_id is None:
        raise NotFoundError(
            f"no calendar {code!r} for this tenant; create the calendar before the "
            f"department that computes its deadlines against it"
        )
    return uuid.UUID(str(calendar_id))


async def _write_translations(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    namespace: str,
    message_key: str,
    translations: dict[str, str],
) -> None:
    if not translations:
        return
    for locale, value in translations.items():
        session.add(
            Translation(
                tenant_id=tenant_id,
                namespace=namespace,
                message_key=message_key,
                locale=locale,
                value=value,
            )
        )
    await session.flush()


def _ordered_by_parent[SpecT](
    specs: Sequence[SpecT],
    *,
    key_of: Callable[[SpecT], str],
    parent_of: Callable[[SpecT], str | None],
    label: str,
) -> list[SpecT]:
    """Kahn's algorithm over parent edges — the same shape as the taxonomy's.

    Duplicated rather than shared with ``taxonomy._topologically_ordered``
    because the two disagree about one thing that matters: a taxonomy spec's
    parent is a ``key`` and an org spec's is a ``code``, and unifying them means
    a generic that reads worse than the eleven lines it replaces. The tree
    *mechanics* are shared in ``control_plane.hierarchy``; the ordering is not
    tree mechanics, it is batch validation.
    """
    keys = [key_of(spec) for spec in specs]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise ValidationError(f"duplicate {label} codes in the batch: {duplicates}")

    known = set(keys)
    ordered: list[SpecT] = []
    placed: set[str] = set()
    remaining = list(specs)

    while remaining:
        ready = [
            spec
            for spec in remaining
            if parent_of(spec) is None or parent_of(spec) not in known or parent_of(spec) in placed
        ]
        if not ready:
            stuck = sorted(key_of(spec) for spec in remaining)
            raise ValidationError(
                f"{label} batch contains a parent cycle among {stuck}; none of them "
                f"can be created before the others"
            )
        for spec in ready:
            ordered.append(spec)
            placed.add(key_of(spec))
        remaining = [spec for spec in remaining if key_of(spec) not in placed]

    return ordered


async def count_departments(session: AsyncSession, *, tenant_id: uuid.UUID) -> int:
    total = await session.execute(
        select(func.count()).select_from(Department).where(Department.tenant_id == tenant_id)
    )
    return int(total.scalar_one())


async def update_department(
    session: AsyncSession, *, tenant_id: uuid.UUID, code: str, values: dict[str, Any]
) -> Department:
    """Apply a whitelisted set of scalar changes to one department.

    A whitelist rather than ``**values``: this is called from an HTTP handler,
    and a caller-controlled column name would let a request write ``path`` or
    ``tenant_id`` directly. ``path`` in particular is derived — writing it
    without rewriting the subtree silently detaches every descendant.
    """
    allowed = {"name", "kind", "is_assignable", "is_active", "ward", "attributes"}
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ValidationError(f"fields not updatable through this path: {unknown}")
    department = await require_department(session, tenant_id=tenant_id, code=code)
    if not values:
        return department

    await session.execute(
        update(Department)
        .where(Department.tenant_id == tenant_id, Department.id == department.id)
        .values(**values, version=Department.version + 1)
    )
    await session.flush()
    session.expire(department)
    return await require_department(session, tenant_id=tenant_id, code=code)
