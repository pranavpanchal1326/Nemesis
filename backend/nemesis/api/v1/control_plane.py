"""The control-plane HTTP surface — Phase 5's "without a deploy" made literal.

**Reads are tenant-scoped and token-free; writes need the control-plane token.**
Reading your own taxonomy is the same class of operation as reading your own
complaint, and it goes through the same ``X-Tenant-ID`` resolution with the same
honest caveat about what that header is (see ``api.deps``). Writes create
tenants and redefine what a complaint *means*, so they carry the shared secret
described in ``Settings.control_plane_token`` until Phase 13 replaces it with a
real operator identity.

**One error translation, here.** The services raise ``ControlPlaneError``
subclasses precisely so they can be called from a CLI and a test without
dragging HTTP status codes into them. The mapping lives in ``_translate`` so a
new error kind surfaces consistently instead of as whatever the nearest handler
improvised — and so the 404-vs-403 discipline ``api.deps`` applies to tenant
lookup is applied here too: a taxonomy key belonging to another tenant is
"not found", never "forbidden".

**Every write commits through the request's session.** ``SessionDep`` yields a
``session_scope()`` that commits on success, so a provisioning request that
raises partway rolls back everything — including the events — which is the
property ``control_plane.provisioning`` is built around.
"""

from __future__ import annotations

import hmac
import uuid
from datetime import datetime, timedelta
from typing import Annotated, Any, Final

from fastapi import APIRouter, Header, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.api.deps import ConfigDep, SessionDep, TenantDep
from nemesis.api.errors import (
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_422_UNPROCESSABLE,
    PROBLEM_BASE,
    ProblemDetailError,
)
from nemesis.config import Settings
from nemesis.control_plane import calendars, organisation, provisioning, taxonomy, templates
from nemesis.control_plane import translations as i18n
from nemesis.control_plane.errors import (
    ConflictError,
    ControlPlaneError,
    NotFoundError,
)
from nemesis.control_plane.schemas import (
    CalendarSpec,
    CertificationSpec,
    ContractorSpec,
    DepartmentSpec,
    PromptSetSpec,
    ProvisioningRequest,
    ProvisioningResult,
    TaxonomyNodeSpec,
    TaxonomyNodeUpdate,
    TranslationBundle,
    ZoneSpec,
)
from nemesis.db.models.taxonomy import TaxonomyNode
from nemesis.db.models.tenant import Tenant
from nemesis.observability.logging import get_correlation_id
from nemesis.tenancy.context import tenant_scope

router = APIRouter(prefix="/control-plane", tags=["control-plane"])

CONTROL_PLANE_TOKEN_HEADER: Final = "X-Control-Plane-Token"


# ---------------------------------------------------------------------------
# Response models — declared, never assembled as bare dicts
# ---------------------------------------------------------------------------


class ApiModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class TaxonomyNodeResponse(ApiModel):
    key: str
    parent_key: str | None
    path: str
    depth: int
    display_name: str
    description: str | None
    icon: str | None
    sort_order: int
    is_active: bool
    is_selectable: bool
    severity_semantics: dict[str, Any]
    routing_hints: dict[str, Any]
    attributes: dict[str, Any]
    version: int

    @classmethod
    def of(cls, node: TaxonomyNode, *, parent_key: str | None) -> TaxonomyNodeResponse:
        return cls(
            key=node.key,
            parent_key=parent_key,
            path=node.path,
            depth=node.depth,
            display_name=node.display_name,
            description=node.description,
            icon=node.icon,
            sort_order=node.sort_order,
            is_active=node.is_active,
            is_selectable=node.is_selectable,
            severity_semantics=dict(node.severity_semantics),
            routing_hints=dict(node.routing_hints),
            attributes=dict(node.attributes),
            version=node.version,
        )


class TaxonomyListResponse(ApiModel):
    revision: int
    content_hash: str
    nodes: list[TaxonomyNodeResponse]


class OrgUnitResponse(ApiModel):
    code: str
    name: str
    kind: str
    parent_code: str | None
    path: str
    depth: int
    is_active: bool


class DepartmentResponse(OrgUnitResponse):
    is_assignable: bool
    ward: str | None


class CalendarResponse(ApiModel):
    code: str
    name: str
    timezone: str
    is_continuous: bool
    is_default: bool
    working_hours: dict[str, Any]


class DeadlineResponse(ApiModel):
    """A previewed SLA deadline, with the reasoning that produced it.

    Exposed as an endpoint because a calendar is the configuration most likely
    to be subtly wrong and least likely to be noticed — a monsoon window off by
    a month produces deadlines that look plausible for eleven months of the
    year. Being able to ask "what would a 72-hour SLA starting now come out as"
    turns that from a discovery into a check.
    """

    due_at: str
    elapsed_hours: float
    working_hours_consumed: float
    adjustments: dict[str, str]


class CoverageResponse(ApiModel):
    locale: str
    translatable: int
    translated: int
    ratio: float
    missing_keys: list[str]


class TemplateResponse(ApiModel):
    name: str
    version: str
    description: str
    locales: list[str]
    taxonomy_nodes: int
    departments: int
    zones: int


# ---------------------------------------------------------------------------
# Write authorisation
# ---------------------------------------------------------------------------


def _require_token(settings: Settings, supplied: str | None) -> None:
    """Constant-time comparison against the configured control-plane token.

    ``hmac.compare_digest`` rather than ``==``: the comparison is against a
    caller-supplied string, and a short-circuiting comparison leaks the length
    of the shared prefix. That is a small leak against a token an attacker can
    guess at over the network, and it costs one function call to close.
    """
    expected = settings.control_plane_token.get_secret_value()
    if supplied is None or not hmac.compare_digest(supplied, expected):
        raise ProblemDetailError(
            status_code=HTTP_403_FORBIDDEN,
            title="Control-plane token required",
            detail=(
                f"Supply the {CONTROL_PLANE_TOKEN_HEADER} header. Control-plane "
                f"writes redefine what a complaint means and are not open."
            ),
            problem_type=f"{PROBLEM_BASE}/control-plane-forbidden",
        )


TokenDep = Annotated[str | None, Header(alias=CONTROL_PLANE_TOKEN_HEADER)]


def _translate(error: ControlPlaneError) -> ProblemDetailError:
    """One mapping from service errors to the RFC 9457 contract.

    ``NotFoundError`` becomes 404 even when the entity exists for a different
    tenant — the services never distinguish the two, and this layer must not
    reintroduce the distinction the isolation guarantee depends on.
    """
    if isinstance(error, NotFoundError):
        return ProblemDetailError(
            status_code=HTTP_404_NOT_FOUND,
            title="Not found",
            detail=str(error),
            problem_type=f"{PROBLEM_BASE}/not-found",
        )
    if isinstance(error, ConflictError):
        return ProblemDetailError(
            status_code=HTTP_409_CONFLICT,
            title="Conflict",
            detail=str(error),
            problem_type=f"{PROBLEM_BASE}/conflict",
        )
    return ProblemDetailError(
        status_code=HTTP_422_UNPROCESSABLE,
        title="Control-plane request rejected",
        detail=str(error),
        problem_type=f"{PROBLEM_BASE}/validation-error",
    )


# ---------------------------------------------------------------------------
# Templates and provisioning
# ---------------------------------------------------------------------------


@router.get("/templates", summary="List the seeded tenant templates")
async def list_templates() -> list[TemplateResponse]:
    """Read-only and tenant-free: the library is a property of the build.

    Counts rather than full contents. A template is a few hundred lines of
    JSON, and a picker needs to know which one to choose, not what every prompt
    says — the full document is in the repository for whoever wants it.
    """
    return [
        TemplateResponse(
            name=template.name,
            version=template.version,
            description=template.description,
            locales=list(template.locales),
            taxonomy_nodes=len(template.taxonomy),
            departments=len(template.departments),
            zones=len(template.zones),
        )
        for template in templates.all_templates()
    ]


@router.post(
    "/tenants",
    status_code=status.HTTP_201_CREATED,
    summary="Provision a tenant",
    responses={
        403: {"description": "Control-plane token missing or wrong"},
        409: {"description": "Slug already taken"},
        422: {"description": "The plan is internally inconsistent"},
    },
)
async def provision_tenant(
    session: SessionDep,
    settings: ConfigDep,
    request: ProvisioningRequest,
    token: TokenDep = None,
) -> ProvisioningResult:
    """Create a tenant and everything it needs, in one transaction.

    201 rather than 202: unlike a complaint, this *is* complete when it
    returns. There is no pipeline behind it, and the returned counts describe
    rows that already exist.

    No ``tenant_scope`` wrapper, deliberately. The tenant does not exist until
    partway through this handler, so there is nothing to bind on entry;
    ``provisioning`` passes the id explicitly to every write, which is what the
    event store's ``tenant_id`` parameter is for.
    """
    _require_token(settings, token)
    try:
        return await provisioning.provision(
            session, request=request, correlation_id=get_correlation_id()
        )
    except ControlPlaneError as exc:
        raise _translate(exc) from exc


# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------


@router.get("/taxonomy", summary="The tenant's defect taxonomy")
async def get_taxonomy(
    tenant: TenantDep,
    session: SessionDep,
    include_inactive: Annotated[bool, Query()] = False,
) -> TaxonomyListResponse:
    """Every node, ordered by path, with the revision that produced them.

    The revision and content hash travel with the list so a caller caching this
    — the classifier, a UI, Phase 7's backtester — can tell whether what it
    holds is still current without diffing the nodes.
    """
    with tenant_scope(tenant.id):
        nodes = await taxonomy.list_nodes(
            session, tenant_id=tenant.id, include_inactive=include_inactive
        )
        digest = await taxonomy.digest(session, tenant_id=tenant.id)
        revision = await _taxonomy_revision(session, tenant_id=tenant.id)

    by_id = {node.id: node.key for node in nodes}
    return TaxonomyListResponse(
        revision=revision,
        content_hash=digest.content_hash,
        nodes=[
            TaxonomyNodeResponse.of(
                node, parent_key=None if node.parent_id is None else by_id.get(node.parent_id)
            )
            for node in nodes
        ],
    )


@router.post(
    "/taxonomy",
    status_code=status.HTTP_201_CREATED,
    summary="Define a defect category",
    responses={403: {"description": "Control-plane token missing or wrong"}},
)
async def create_taxonomy_node(
    tenant: TenantDep,
    session: SessionDep,
    settings: ConfigDep,
    spec: TaxonomyNodeSpec,
    token: TokenDep = None,
) -> TaxonomyNodeResponse:
    _require_token(settings, token)
    with tenant_scope(tenant.id):
        try:
            node = await taxonomy.create_node(session, tenant_id=tenant.id, spec=spec)
            await taxonomy.publish(
                session,
                tenant_id=tenant.id,
                change_kind=taxonomy.CHANGE_CREATED,
                changed_keys=[spec.key],
                correlation_id=get_correlation_id(),
            )
        except ControlPlaneError as exc:
            raise _translate(exc) from exc
        return TaxonomyNodeResponse.of(node, parent_key=spec.parent_key)


@router.patch(
    "/taxonomy/{key}",
    summary="Amend a defect category",
    responses={403: {"description": "Control-plane token missing or wrong"}},
)
async def patch_taxonomy_node(
    tenant: TenantDep,
    session: SessionDep,
    settings: ConfigDep,
    key: str,
    changes: TaxonomyNodeUpdate,
    token: TokenDep = None,
) -> TaxonomyNodeResponse:
    """Partial update, including reparenting the whole subtree.

    There is no DELETE. A category a complaint was classified into cannot be
    removed without making that complaint's history unreadable, so deactivation
    (``is_active=false``) is the operation — and it is reversible, which
    deletion is not.
    """
    _require_token(settings, token)
    with tenant_scope(tenant.id):
        try:
            node = await taxonomy.update_node(
                session, tenant_id=tenant.id, key=key, changes=changes
            )
            await taxonomy.publish(
                session,
                tenant_id=tenant.id,
                change_kind=taxonomy.CHANGE_UPDATED,
                changed_keys=[key],
                correlation_id=get_correlation_id(),
            )
        except ControlPlaneError as exc:
            raise _translate(exc) from exc
        parent_key = (
            None
            if node.parent_id is None
            else (await _key_of(session, tenant_id=tenant.id, node_id=node.parent_id))
        )
        return TaxonomyNodeResponse.of(node, parent_key=parent_key)


@router.get("/taxonomy/{key}/subtree", summary="A category and everything under it")
async def get_subtree(
    tenant: TenantDep,
    session: SessionDep,
    key: str,
    include_inactive: Annotated[bool, Query()] = False,
) -> list[TaxonomyNodeResponse]:
    with tenant_scope(tenant.id):
        try:
            nodes = await taxonomy.subtree(
                session, tenant_id=tenant.id, key=key, include_inactive=include_inactive
            )
        except ControlPlaneError as exc:
            raise _translate(exc) from exc
    by_id = {node.id: node.key for node in nodes}
    return [
        TaxonomyNodeResponse.of(
            node, parent_key=None if node.parent_id is None else by_id.get(node.parent_id)
        )
        for node in nodes
    ]


@router.put(
    "/taxonomy/prompt-sets",
    summary="Attach or replace a category's classifier prompts",
    responses={403: {"description": "Control-plane token missing or wrong"}},
)
async def put_prompt_set(
    tenant: TenantDep,
    session: SessionDep,
    settings: ConfigDep,
    spec: PromptSetSpec,
    token: TokenDep = None,
) -> dict[str, str]:
    """PUT, not POST: the resource is ``(node, locale, encoder)`` and it is
    replaced wholesale. Phase 9's loop is measure → edit prompts → measure, and
    an append-only endpoint would accumulate every rejected experiment."""
    _require_token(settings, token)
    with tenant_scope(tenant.id):
        try:
            prompt_set = await taxonomy.upsert_prompt_set(session, tenant_id=tenant.id, spec=spec)
            # **Read the row's values here, before `publish` commits.**
            #
            # A commit expires every attribute on the instance, so touching one
            # afterwards triggers a lazy refresh — which is blocking IO inside an
            # async handler, and asyncpg answers with `MissingGreenlet` rather
            # than with a row. The response below used to be built after the
            # commit and 500'd on every call, which nothing noticed because the
            # endpoint had no caller until a seeding script grew one.
            #
            # Captured from the ORM object rather than from `spec` on purpose:
            # `upsert_prompt_set` normalises what it stores, and echoing the
            # request back would report what was asked for instead of what was
            # written.
            written = {
                "node_key": spec.node_key,
                "locale": prompt_set.locale,
                "encoder": prompt_set.encoder,
                "prompt_set_version": prompt_set.prompt_set_version,
            }
            await taxonomy.publish(
                session,
                tenant_id=tenant.id,
                change_kind=taxonomy.CHANGE_PROMPTS,
                changed_keys=[spec.node_key],
                correlation_id=get_correlation_id(),
            )
        except ControlPlaneError as exc:
            raise _translate(exc) from exc
        return written


# ---------------------------------------------------------------------------
# Organisation
# ---------------------------------------------------------------------------


@router.get("/departments", summary="The responsibility tree")
async def get_departments(
    tenant: TenantDep,
    session: SessionDep,
    include_inactive: Annotated[bool, Query()] = False,
) -> list[DepartmentResponse]:
    with tenant_scope(tenant.id):
        rows = await organisation.list_departments(
            session, tenant_id=tenant.id, include_inactive=include_inactive
        )
    by_id = {row.id: row.code for row in rows}
    return [
        DepartmentResponse(
            code=row.code,
            name=row.name,
            kind=row.kind,
            parent_code=None if row.parent_id is None else by_id.get(row.parent_id),
            path=row.path,
            depth=row.depth,
            is_active=row.is_active,
            is_assignable=row.is_assignable,
            ward=row.ward,
        )
        for row in rows
    ]


@router.post(
    "/departments",
    status_code=status.HTTP_201_CREATED,
    summary="Add a unit of responsibility",
    responses={403: {"description": "Control-plane token missing or wrong"}},
)
async def create_department(
    tenant: TenantDep,
    session: SessionDep,
    settings: ConfigDep,
    spec: DepartmentSpec,
    token: TokenDep = None,
) -> DepartmentResponse:
    _require_token(settings, token)
    with tenant_scope(tenant.id):
        try:
            department = await organisation.create_department(
                session, tenant_id=tenant.id, spec=spec
            )
            await organisation.record_change(
                session,
                tenant_id=tenant.id,
                subject=organisation.SUBJECT_DEPARTMENT,
                subject_key=spec.code,
                subject_id=department.id,
                change_kind=organisation.CHANGE_CREATED,
                correlation_id=get_correlation_id(),
            )
        except ControlPlaneError as exc:
            raise _translate(exc) from exc
        return DepartmentResponse(
            code=department.code,
            name=department.name,
            kind=department.kind,
            parent_code=spec.parent_code,
            path=department.path,
            depth=department.depth,
            is_active=department.is_active,
            is_assignable=department.is_assignable,
            ward=department.ward,
        )


@router.get("/zones", summary="The place tree")
async def get_zones(
    tenant: TenantDep,
    session: SessionDep,
    include_inactive: Annotated[bool, Query()] = False,
) -> list[OrgUnitResponse]:
    with tenant_scope(tenant.id):
        rows = await organisation.list_zones(
            session, tenant_id=tenant.id, include_inactive=include_inactive
        )
    by_id = {row.id: row.code for row in rows}
    return [
        OrgUnitResponse(
            code=row.code,
            name=row.name,
            kind=row.kind,
            parent_code=None if row.parent_id is None else by_id.get(row.parent_id),
            path=row.path,
            depth=row.depth,
            is_active=row.is_active,
        )
        for row in rows
    ]


@router.post(
    "/zones",
    status_code=status.HTTP_201_CREATED,
    summary="Add a unit of place",
    responses={403: {"description": "Control-plane token missing or wrong"}},
)
async def create_zone(
    tenant: TenantDep,
    session: SessionDep,
    settings: ConfigDep,
    spec: ZoneSpec,
    token: TokenDep = None,
) -> OrgUnitResponse:
    _require_token(settings, token)
    with tenant_scope(tenant.id):
        try:
            zone = await organisation.create_zone(session, tenant_id=tenant.id, spec=spec)
            await organisation.record_change(
                session,
                tenant_id=tenant.id,
                subject=organisation.SUBJECT_ZONE,
                subject_key=spec.code,
                subject_id=zone.id,
                change_kind=organisation.CHANGE_CREATED,
                changed_fields=["boundary"] if spec.boundary is not None else [],
                correlation_id=get_correlation_id(),
            )
        except ControlPlaneError as exc:
            raise _translate(exc) from exc
        return OrgUnitResponse(
            code=zone.code,
            name=zone.name,
            kind=zone.kind,
            parent_code=spec.parent_code,
            path=zone.path,
            depth=zone.depth,
            is_active=zone.is_active,
        )


@router.post(
    "/contractors",
    status_code=status.HTTP_201_CREATED,
    summary="Register a contractor",
    responses={403: {"description": "Control-plane token missing or wrong"}},
)
async def register_contractor(
    tenant: TenantDep,
    session: SessionDep,
    settings: ConfigDep,
    spec: ContractorSpec,
    token: TokenDep = None,
) -> dict[str, str]:
    _require_token(settings, token)
    with tenant_scope(tenant.id):
        try:
            contractor = await organisation.register_contractor(
                session, tenant_id=tenant.id, spec=spec
            )
        except ControlPlaneError as exc:
            raise _translate(exc) from exc
        return {"registration_id": contractor.registration_id, "name": contractor.name}


@router.post(
    "/certifications",
    status_code=status.HTTP_201_CREATED,
    summary="Certify a contractor for a defect category",
    responses={403: {"description": "Control-plane token missing or wrong"}},
)
async def certify_contractor(
    tenant: TenantDep,
    session: SessionDep,
    settings: ConfigDep,
    spec: CertificationSpec,
    token: TokenDep = None,
) -> dict[str, str]:
    _require_token(settings, token)
    with tenant_scope(tenant.id):
        try:
            certification = await organisation.certify(session, tenant_id=tenant.id, spec=spec)
            await organisation.record_change(
                session,
                tenant_id=tenant.id,
                subject=organisation.SUBJECT_CERTIFICATION,
                subject_key=f"{spec.contractor_registration_id}:{spec.taxonomy_key}",
                subject_id=certification.id,
                change_kind=organisation.CHANGE_CREATED,
                correlation_id=get_correlation_id(),
            )
        except ControlPlaneError as exc:
            raise _translate(exc) from exc
        return {
            "contractor_registration_id": spec.contractor_registration_id,
            "taxonomy_key": spec.taxonomy_key,
        }


# ---------------------------------------------------------------------------
# Calendars
# ---------------------------------------------------------------------------


@router.get("/calendars", summary="Business calendars")
async def get_calendars(tenant: TenantDep, session: SessionDep) -> list[CalendarResponse]:
    with tenant_scope(tenant.id):
        rows = await calendars.list_calendars(session, tenant_id=tenant.id)
    return [
        CalendarResponse(
            code=row.code,
            name=row.name,
            timezone=row.timezone,
            is_continuous=row.is_continuous,
            is_default=row.is_default,
            working_hours=dict(row.working_hours),
        )
        for row in rows
    ]


@router.post(
    "/calendars",
    status_code=status.HTTP_201_CREATED,
    summary="Define a working week",
    responses={403: {"description": "Control-plane token missing or wrong"}},
)
async def create_calendar(
    tenant: TenantDep,
    session: SessionDep,
    settings: ConfigDep,
    spec: CalendarSpec,
    token: TokenDep = None,
) -> CalendarResponse:
    _require_token(settings, token)
    with tenant_scope(tenant.id):
        try:
            calendar = await calendars.create_calendar(session, tenant_id=tenant.id, spec=spec)
            await organisation.record_change(
                session,
                tenant_id=tenant.id,
                subject=organisation.SUBJECT_CALENDAR,
                subject_key=spec.code,
                subject_id=calendar.id,
                change_kind=organisation.CHANGE_CREATED,
                correlation_id=get_correlation_id(),
            )
        except ControlPlaneError as exc:
            raise _translate(exc) from exc
        return CalendarResponse(
            code=calendar.code,
            name=calendar.name,
            timezone=calendar.timezone,
            is_continuous=calendar.is_continuous,
            is_default=calendar.is_default,
            working_hours=dict(calendar.working_hours),
        )


class DeadlinePreviewRequest(BaseModel):
    """Ask a calendar what a budget of working time comes out as."""

    model_config = ConfigDict(extra="forbid")

    #: ISO 8601 with an offset. Required, not defaulted to "now": the whole
    #: point of the preview is checking a specific date against a specific
    #: seasonal window, and defaulting it would make the monsoon case — the one
    #: worth checking — the hard one to ask about.
    start: str
    budget_hours: float = Field(gt=0, le=24 * 365)
    calendar_code: str | None = None


@router.post("/calendars/preview-deadline", summary="Preview an SLA deadline")
async def preview_deadline(
    tenant: TenantDep, session: SessionDep, request: DeadlinePreviewRequest
) -> DeadlineResponse:
    """Read-only, and therefore token-free: it writes nothing and reveals only
    the tenant's own configuration back to it."""
    try:
        start = datetime.fromisoformat(request.start)
    except ValueError as exc:
        raise ProblemDetailError(
            status_code=HTTP_422_UNPROCESSABLE,
            title="Control-plane request rejected",
            detail="start must be an ISO 8601 timestamp with an offset",
            problem_type=f"{PROBLEM_BASE}/validation-error",
        ) from exc

    with tenant_scope(tenant.id):
        try:
            calendar_id: uuid.UUID | None = None
            if request.calendar_code is not None:
                calendar = await calendars.get_calendar(
                    session, tenant_id=tenant.id, code=request.calendar_code
                )
                if calendar is None:
                    raise NotFoundError(f"no calendar {request.calendar_code!r} for this tenant")
                calendar_id = calendar.id
            week = await calendars.load_working_week(
                session, tenant_id=tenant.id, calendar_id=calendar_id
            )
            deadline = calendars.resolve_deadline(
                week=week, start=start, budget=timedelta(hours=request.budget_hours)
            )
        except ControlPlaneError as exc:
            raise _translate(exc) from exc

    return DeadlineResponse(
        due_at=deadline.due_at.isoformat(),
        elapsed_hours=round(deadline.elapsed_hours, 4),
        working_hours_consumed=round(deadline.working_hours_consumed, 4),
        adjustments={label: str(multiplier) for label, multiplier in deadline.adjustments},
    )


# ---------------------------------------------------------------------------
# Translations
# ---------------------------------------------------------------------------


@router.put(
    "/translations",
    summary="Import a translation bundle",
    responses={403: {"description": "Control-plane token missing or wrong"}},
)
async def put_translations(
    tenant: TenantDep,
    session: SessionDep,
    settings: ConfigDep,
    bundle: TranslationBundle,
    token: TokenDep = None,
) -> dict[str, int | str]:
    """The Phase 5 requirement in one endpoint: a new language is an import."""
    _require_token(settings, token)
    with tenant_scope(tenant.id):
        try:
            written = await i18n.import_bundle(session, tenant_id=tenant.id, bundle=bundle)
        except ControlPlaneError as exc:
            raise _translate(exc) from exc
    return {"namespace": bundle.namespace, "locale": bundle.locale, "written": written}


@router.get("/translations/{namespace}/{locale}", summary="A namespace's strings")
async def get_translations(
    tenant: TenantDep, session: SessionDep, namespace: str, locale: str
) -> dict[str, str]:
    with tenant_scope(tenant.id):
        return await i18n.resolve_bundle(
            session, tenant_id=tenant.id, namespace=namespace, locale=locale
        )


@router.get("/translations/coverage", summary="Taxonomy translation coverage per locale")
async def get_coverage(tenant: TenantDep, session: SessionDep) -> list[CoverageResponse]:
    """What is *not* translated, per declared locale.

    The endpoint exists because the alternative way to discover a half-localised
    taxonomy is a citizen reading English on a Marathi interface.
    """
    with tenant_scope(tenant.id):
        try:
            reports = await i18n.coverage_across_locales(session, tenant_id=tenant.id)
        except ControlPlaneError as exc:
            raise _translate(exc) from exc
    return [
        CoverageResponse(
            locale=report.locale,
            translatable=report.translatable,
            translated=report.translated,
            ratio=round(report.ratio, 4),
            missing_keys=list(report.missing_keys),
        )
        for report in reports
    ]


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _taxonomy_revision(session: AsyncSession, *, tenant_id: uuid.UUID) -> int:
    # tenant-scope-exempt: `tenants` is the tenant registry; its primary key IS
    # the tenant. See tenancy.registry.
    revision = (
        await session.execute(select(Tenant.taxonomy_revision).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    return 0 if revision is None else int(revision)


async def _key_of(session: AsyncSession, *, tenant_id: uuid.UUID, node_id: uuid.UUID) -> str | None:
    row = await session.execute(
        select(TaxonomyNode.key).where(
            TaxonomyNode.tenant_id == tenant_id, TaxonomyNode.id == node_id
        )
    )
    return row.scalar_one_or_none()
