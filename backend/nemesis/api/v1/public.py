"""§26.4 — the public transparency API, v1.

The blueprint marks this ROADMAP with a stated schema. Phase 4 builds it, with
four deliberate departures from the sketch, each recorded here because a
departure nobody wrote down is indistinguishable from a mistake:

1. **``/public/{tenant_slug}/...`` rather than ``/public/...``.** This is a
   multi-tenant deployment; §26.4 was written for a single one. See
   ``api.public_deps`` on why the slug and not a header.
2. **``ward`` became ``zones``, and ``ward_id`` became ``zone_code``** — in v2.
   v1 keeps the blueprint's noun so the first published contract matches what
   was announced, and the rename is exactly the kind of breaking change the
   version registry exists to carry. ADR-0018 is why the accurate word is zone.
3. **Aggregates are suppressed below a k-anonymity floor**, which §26.4 does not
   mention and which "privacy-scrubbed" cannot mean without. A ward summary over
   two complaints is not an aggregate.
4. **A discovery endpoint exists.** Three endpoints keyed by identifiers a
   caller cannot learn is a public API that only works for people holding an
   internal document.

**Every response body goes through a declared shape.** No handler returns a
model built from an ORM row by attribute copying — ``public.policy`` holds the
allow-list and ``tests/test_public_privacy.py`` walks these models against it,
which is how the gate's third clause is proven over the schema rather than over
one sampled body.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, ConfigDict

from nemesis.api.deps import ConfigDep, SessionDep
from nemesis.api.errors import HTTP_404_NOT_FOUND, PROBLEM_BASE, ProblemDetailError
from nemesis.api.public_deps import PublicRateLimit, PublicTenant, PublicTenantDep
from nemesis.api.versioning import get_version, version_headers
from nemesis.config import Settings
from nemesis.observability import metrics
from nemesis.public import aggregates

router = APIRouter(prefix="/public", tags=["public"], dependencies=[PublicRateLimit])

#: The version this module implements. Declared once so the headers, the
#: envelope field, and the contract lock all name the same thing.
API_VERSION = "v1"


class PublicModel(BaseModel):
    """Frozen, and extra fields forbidden on the way *in* and *out*.

    ``extra="forbid"`` matters more here than anywhere else in the codebase: a
    field that reaches a public response without being declared is a disclosure,
    and pydantic refusing to construct the model is a louder failure than a test
    that only runs on the paths somebody remembered to cover.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


class Centroid(PublicModel):
    lat: float | None
    lng: float | None


class CategoryCountResponse(PublicModel):
    category: str
    count: int


class ZoneSummaryResponse(PublicModel):
    api_version: str
    generated_at: str
    tenant: str
    notice: str

    zone_code: str
    zone_name: str
    zone_kind: str
    centroid: Centroid | None

    total_reports: int
    open_reports: int
    resolved_reports: int
    auto_confirmed_resolutions: int
    resolution_rate: float | None
    median_resolution_hours: float | None
    by_category: list[CategoryCountResponse]

    suppressed: bool
    suppression_threshold: int
    count_suppressed_buckets: int

    @classmethod
    def of(cls, summary: aggregates.ZoneSummary, *, tenant: PublicTenant) -> ZoneSummaryResponse:
        return cls(
            api_version=API_VERSION,
            generated_at=aggregates.utc_now().isoformat(),
            tenant=tenant.slug,
            notice=aggregates.SYSTEM_FLAGGED_NOTICE,
            zone_code=summary.zone_code,
            zone_name=summary.zone_name,
            zone_kind=summary.zone_kind,
            centroid=(
                None
                if summary.centroid is None
                else Centroid(lat=summary.centroid[0], lng=summary.centroid[1])
            ),
            total_reports=summary.total_reports,
            open_reports=summary.open_reports,
            resolved_reports=summary.resolved_reports,
            auto_confirmed_resolutions=summary.auto_confirmed_resolutions,
            resolution_rate=aggregates.rate(summary.resolved_reports, summary.total_reports),
            median_resolution_hours=summary.median_resolution_hours,
            by_category=[
                CategoryCountResponse(category=item.category, count=item.count)
                for item in summary.by_category
            ],
            suppressed=summary.suppressed,
            suppression_threshold=summary.suppression.threshold,
            count_suppressed_buckets=summary.suppression.suppressed_buckets,
        )


class ZoneIndexResponse(PublicModel):
    api_version: str
    generated_at: str
    tenant: str
    notice: str
    zones: list[ZoneSummaryResponse]
    count: int


class ContractorProfileResponse(PublicModel):
    api_version: str
    generated_at: str
    tenant: str
    notice: str
    rating_disclaimer: str

    contractor_id: str
    contractor_name: str
    registration_id: str
    active_since: str | None

    work_orders_completed: int
    work_orders_open: int
    on_time_rate: float | None
    disputed_count: int
    certified_categories: list[str]
    suppressed: bool
    suppression_threshold: int


class BudgetLineResponse(PublicModel):
    funding_source: str
    allocated_amount: str
    spent_amount: str
    utilisation_rate: float | None


class BudgetSummaryResponse(PublicModel):
    api_version: str
    generated_at: str
    tenant: str
    notice: str
    zone_code: str
    fiscal_year: str
    currency: str
    allocations: list[BudgetLineResponse]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/{tenant_slug}/zones",
    summary="List published places and their headline figures",
    response_model=ZoneIndexResponse,
)
async def list_zones(
    tenant: PublicTenantDep,
    session: SessionDep,
    settings: ConfigDep,
    response: Response,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ZoneIndexResponse:
    """Discovery. Without it the other endpoints need identifiers nobody has."""
    summaries, total = await aggregates.zone_index(
        session,
        tenant_id=tenant.id,
        threshold=tenant.suppression_threshold,
        limit=limit,
        offset=offset,
    )
    _finish(response, endpoint="/public/{tenant_slug}/zones", settings=settings)
    for summary in summaries:
        metrics.public_api_suppressed_buckets_total.inc(summary.suppression.suppressed_buckets)
    return ZoneIndexResponse(
        api_version=API_VERSION,
        generated_at=aggregates.utc_now().isoformat(),
        tenant=tenant.slug,
        notice=aggregates.SYSTEM_FLAGGED_NOTICE,
        zones=[ZoneSummaryResponse.of(item, tenant=tenant) for item in summaries],
        count=total,
    )


@router.get(
    "/{tenant_slug}/ward/{zone_code}/summary",
    summary="Complaint and resolution figures for one place",
    response_model=ZoneSummaryResponse,
)
async def ward_summary(
    zone_code: str,
    tenant: PublicTenantDep,
    session: SessionDep,
    settings: ConfigDep,
    response: Response,
) -> ZoneSummaryResponse:
    """§26.4's ``/public/ward/{ward_id}/summary``.

    The path keeps ``ward`` in v1 because that is the noun the blueprint
    published; v2 renames it to ``zone``, which ADR-0018 argues is the accurate
    word, and the rename is carried by the version registry rather than applied
    to a live contract.
    """
    summary = await aggregates.zone_summary(
        session,
        tenant_id=tenant.id,
        zone_code=zone_code,
        threshold=tenant.suppression_threshold,
    )
    if summary is None:
        raise _no_such()
    metrics.public_api_suppressed_buckets_total.inc(summary.suppression.suppressed_buckets)
    _finish(
        response,
        endpoint="/public/{tenant_slug}/ward/{zone_code}/summary",
        settings=settings,
    )
    return ZoneSummaryResponse.of(summary, tenant=tenant)


@router.get(
    "/{tenant_slug}/contractor/{contractor_id}/profile",
    summary="A contractor's public track record",
    response_model=ContractorProfileResponse,
)
async def contractor_profile(
    contractor_id: uuid.UUID,
    tenant: PublicTenantDep,
    session: SessionDep,
    settings: ConfigDep,
    response: Response,
) -> ContractorProfileResponse:
    """§16.1's track record, never a star rating.

    ``rating_disclaimer`` is a required field, not an optional courtesy. §22.2's
    legal reasoning is that a system-derived figure about a named commercial
    entity, published without its provenance stated, is an assertion — and §16.4
    requires the appeal path to ship in the same phase as the accountability
    feature, which starts with the figure declaring what it is.
    """
    profile = await aggregates.contractor_profile(
        session,
        tenant_id=tenant.id,
        contractor_id=contractor_id,
        threshold=tenant.suppression_threshold,
    )
    if profile is None:
        raise _no_such()
    _finish(
        response,
        endpoint="/public/{tenant_slug}/contractor/{contractor_id}/profile",
        settings=settings,
    )
    return ContractorProfileResponse(
        api_version=API_VERSION,
        generated_at=aggregates.utc_now().isoformat(),
        tenant=tenant.slug,
        notice=aggregates.SYSTEM_FLAGGED_NOTICE,
        rating_disclaimer=aggregates.RATING_DISCLAIMER,
        contractor_id=str(profile.contractor_id),
        contractor_name=profile.contractor_name,
        registration_id=profile.registration_id,
        active_since=None if profile.active_since is None else profile.active_since.isoformat(),
        work_orders_completed=profile.work_orders_completed,
        work_orders_open=profile.work_orders_open,
        on_time_rate=profile.on_time_rate,
        disputed_count=profile.disputed_count,
        certified_categories=list(profile.certified_categories),
        suppressed=profile.suppressed,
        suppression_threshold=tenant.suppression_threshold,
    )


@router.get(
    "/{tenant_slug}/budget/{zone_code}",
    summary="Budget allocation and spend for one place and fiscal year",
    response_model=BudgetSummaryResponse,
)
async def budget(
    zone_code: str,
    tenant: PublicTenantDep,
    session: SessionDep,
    settings: ConfigDep,
    response: Response,
    fiscal_year: Annotated[str, Query(min_length=4, max_length=16)],
) -> BudgetSummaryResponse:
    """§26.4's budget endpoint. Not suppressed — see ``aggregates.budget_summary``."""
    summary = await aggregates.budget_summary(
        session, tenant_id=tenant.id, zone_code=zone_code, fiscal_year=fiscal_year
    )
    _finish(
        response,
        endpoint="/public/{tenant_slug}/budget/{zone_code}",
        settings=settings,
    )
    return BudgetSummaryResponse(
        api_version=API_VERSION,
        generated_at=aggregates.utc_now().isoformat(),
        tenant=tenant.slug,
        notice=aggregates.SYSTEM_FLAGGED_NOTICE,
        zone_code=summary.zone_code,
        fiscal_year=summary.fiscal_year,
        currency=summary.currency,
        allocations=[
            BudgetLineResponse(
                funding_source=line.funding_source,
                allocated_amount=line.allocated_amount,
                spent_amount=line.spent_amount,
                utilisation_rate=line.utilisation_rate,
            )
            for line in summary.allocations
        ],
    )


# ---------------------------------------------------------------------------


def _no_such() -> ProblemDetailError:
    """One 404 message for every resource kind on this surface.

    The first version named the kind — "no published contractor", "no published
    place". ``check_domain_literals`` flagged 'contractor' as a role literal in a
    domain module, and looking at *why* the check was unhappy is what showed the
    distinction was worthless: the resource kind is already in the path the
    caller sent, so naming it back tells them nothing, and every extra branch in
    a 404 message is one more place for the 404-not-403 discipline to be eroded
    by somebody being helpful.
    """
    return ProblemDetailError(
        status_code=HTTP_404_NOT_FOUND,
        title="Not found",
        detail="No published record matches that identifier.",
        problem_type=f"{PROBLEM_BASE}/not-found",
    )


def _finish(response: Response, *, endpoint: str, settings: Settings) -> None:
    """Version headers, cache directive, and the request counter.

    ``public`` rather than ``private`` in the cache directive, and that is a
    claim being made deliberately: an intermediary may store and share these
    bodies, which is only safe because the scrub guarantees they carry nothing
    caller-specific. The two facts are stated next to each other so changing one
    without the other requires ignoring this comment.
    """
    seconds = settings.public_api.cache_seconds
    response.headers.update(version_headers(get_version(API_VERSION)))
    response.headers["Cache-Control"] = f"public, max-age={seconds}"
    metrics.public_api_requests_total.labels(endpoint=endpoint, outcome="ok").inc()
