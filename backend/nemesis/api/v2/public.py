"""§26.4, v2 — the same data, deliberately reshaped.

The query layer is shared with v1 (``public.aggregates``) and only the
*presentation* differs. That split is the point: two versions of a contract
should not mean two implementations of the arithmetic, or they will disagree
about a resolution rate and the disagreement will be discovered by a reader
comparing two URLs.

Every field here is still declared in ``public.policy.PUBLIC_FIELDS`` — a new
version is a new shape, never a new disclosure. ``totals`` and ``zone_code``
were added there when this module was written, which is the mechanism working:
the allow-list had to be edited deliberately for a field to become publishable.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, ConfigDict

from nemesis.api.deps import ConfigDep, SessionDep
from nemesis.api.errors import HTTP_404_NOT_FOUND, PROBLEM_BASE, ProblemDetailError
from nemesis.api.public_deps import (
    PublicLocaleDep,
    PublicRateLimit,
    PublicTenant,
    PublicTenantDep,
)
from nemesis.api.versioning import get_version, version_headers
from nemesis.observability import metrics
from nemesis.public import aggregates, notices
from nemesis.public.localisation import PublicStrings, public_strings

router = APIRouter(prefix="/public", tags=["public"], dependencies=[PublicRateLimit])

API_VERSION = "v2"


class PublicModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Centroid(PublicModel):
    lat: float | None
    lng: float | None


class CategoryCountResponse(PublicModel):
    category: str
    category_name: str
    count: int


class LocalisedEnvelope(PublicModel):
    """ADR-0052's four fields, identical in shape to v1's.

    A new version is a new shape, never a new disclosure — and it is also not a
    new *policy*. C7 and C8 are answered the same way in both versions, because
    a reader who follows a v1 link and a v2 link to the same ward and gets a
    Marathi disclaimer in one and an English one in the other has found a defect
    rather than a version difference.
    """

    tenant_name: str
    locale: str
    #: See v1's envelope: the same list, for the same reason. A version is a
    #: shape, never a policy.
    locales: list[str]
    notice_locale: str
    notice_review: str


class ZoneTotals(PublicModel):
    """The v2 grouping. This object is the breaking change.

    A v1 consumer reads ``body["total_reports"]``; here that is
    ``body["totals"]["total_reports"]``, and there is no shim making the old path
    work. A compatibility shim would be the friendlier choice and the wrong one:
    it would mean v1 and v2 are the same contract with two spellings, so the
    version number would stop meaning anything and the next breaking change
    would have nowhere to go.
    """

    total_reports: int
    open_reports: int
    resolved_reports: int
    auto_confirmed_resolutions: int
    resolution_rate: float | None
    median_resolution_hours: float | None


class ZoneSummaryV2(LocalisedEnvelope):
    api_version: str
    generated_at: str
    tenant: str
    notice: str

    zone_code: str
    zone_name: str
    zone_kind: str
    centroid: Centroid | None

    totals: ZoneTotals
    by_category: list[CategoryCountResponse]

    suppressed: bool
    suppression_threshold: int
    count_suppressed_buckets: int

    @classmethod
    def of(
        cls,
        summary: aggregates.ZoneSummary,
        *,
        tenant: PublicTenant,
        strings: PublicStrings,
    ) -> ZoneSummaryV2:
        notice_locale, catalogue = notices.resolve(strings.locale)
        return cls(
            api_version=API_VERSION,
            generated_at=aggregates.utc_now().isoformat(),
            tenant=tenant.slug,
            tenant_name=tenant.name,
            locales=list(tenant.locales),
            locale=strings.locale,
            notice=catalogue.system_flagged.text,
            notice_locale=notice_locale,
            notice_review=catalogue.system_flagged.review,
            zone_code=summary.zone_code,
            zone_name=strings.zone(summary.zone_code, summary.zone_name),
            zone_kind=summary.zone_kind,
            centroid=(
                None
                if summary.centroid is None
                else Centroid(lat=summary.centroid[0], lng=summary.centroid[1])
            ),
            totals=ZoneTotals(
                total_reports=summary.total_reports,
                open_reports=summary.open_reports,
                resolved_reports=summary.resolved_reports,
                auto_confirmed_resolutions=summary.auto_confirmed_resolutions,
                resolution_rate=aggregates.rate(summary.resolved_reports, summary.total_reports),
                median_resolution_hours=summary.median_resolution_hours,
            ),
            by_category=[
                CategoryCountResponse(
                    category=item.category,
                    category_name=strings.category(item.category),
                    count=item.count,
                )
                for item in summary.by_category
            ],
            suppressed=summary.suppressed,
            suppression_threshold=summary.suppression.threshold,
            count_suppressed_buckets=summary.suppression.suppressed_buckets,
        )


class ZoneIndexV2(LocalisedEnvelope):
    api_version: str
    generated_at: str
    tenant: str
    notice: str
    zones: list[ZoneSummaryV2]
    count: int


@router.get(
    "/{tenant_slug}/zones",
    summary="List published places (v2 shape)",
    response_model=ZoneIndexV2,
)
async def list_zones(
    tenant: PublicTenantDep,
    locale: PublicLocaleDep,
    session: SessionDep,
    settings: ConfigDep,
    response: Response,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ZoneIndexV2:
    summaries, total = await aggregates.zone_index(
        session,
        tenant_id=tenant.id,
        threshold=tenant.suppression_threshold,
        limit=limit,
        offset=offset,
    )
    strings = await public_strings(session, tenant_id=tenant.id, locale=locale)
    notice_locale, catalogue = notices.resolve(locale)
    _finish(
        response,
        endpoint="/public/{tenant_slug}/zones",
        cache_seconds=settings.public_api.cache_seconds,
        notice_locale=notice_locale,
    )
    return ZoneIndexV2(
        api_version=API_VERSION,
        generated_at=aggregates.utc_now().isoformat(),
        tenant=tenant.slug,
        tenant_name=tenant.name,
        locales=list(tenant.locales),
        locale=locale,
        notice=catalogue.system_flagged.text,
        notice_locale=notice_locale,
        notice_review=catalogue.system_flagged.review,
        zones=[ZoneSummaryV2.of(item, tenant=tenant, strings=strings) for item in summaries],
        count=total,
    )


@router.get(
    "/{tenant_slug}/zone/{zone_code}/summary",
    summary="Figures for one place (v2 path and shape)",
    response_model=ZoneSummaryV2,
)
async def zone_summary(
    zone_code: str,
    tenant: PublicTenantDep,
    locale: PublicLocaleDep,
    session: SessionDep,
    settings: ConfigDep,
    response: Response,
) -> ZoneSummaryV2:
    """``/zone/`` rather than v1's ``/ward/``. See the package docstring."""
    summary = await aggregates.zone_summary(
        session,
        tenant_id=tenant.id,
        zone_code=zone_code,
        threshold=tenant.suppression_threshold,
    )
    if summary is None:
        raise ProblemDetailError(
            status_code=HTTP_404_NOT_FOUND,
            title="Not found",
            detail="No published place matches that identifier.",
            problem_type=f"{PROBLEM_BASE}/not-found",
        )
    metrics.public_api_suppressed_buckets_total.inc(summary.suppression.suppressed_buckets)
    strings = await public_strings(session, tenant_id=tenant.id, locale=locale)
    _finish(
        response,
        endpoint="/public/{tenant_slug}/zone/{zone_code}/summary",
        cache_seconds=settings.public_api.cache_seconds,
        notice_locale=notices.resolve(locale)[0],
    )
    return ZoneSummaryV2.of(summary, tenant=tenant, strings=strings)


def _finish(response: Response, *, endpoint: str, cache_seconds: int, notice_locale: str) -> None:
    response.headers.update(version_headers(get_version(API_VERSION)))
    response.headers["Cache-Control"] = f"public, max-age={cache_seconds}"
    response.headers["Content-Language"] = notice_locale
    metrics.public_api_requests_total.labels(endpoint=endpoint, outcome="ok").inc()
