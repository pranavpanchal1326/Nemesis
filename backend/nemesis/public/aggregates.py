"""The §26.4 aggregate queries, with suppression applied before serialisation.

**Suppression happens here, not in the response model.** A model that receives a
count of 2 and renders ``null`` has already had the count in memory, in a
traceback, and in whatever a debugger or an error reporter captured. Applying the
floor at the query boundary means the small number never travels.

**Every query is tenant-scoped, and the scoping is the point.** The public
endpoints are unauthenticated, which makes them the one surface where a missing
predicate leaks to the entire internet rather than to one logged-in customer.
The tenancy guard covers this by construction (ADR-0014), and the tests assert
it here anyway, because "the guard would have caught it" is a claim about a layer
rather than about this code.

**Why complaints join zones on a label, and what happens when nothing wrote
one.** ``complaints.ward`` is a string and ``zones.code`` is the tenant's place
vocabulary; the two are matched by value. That is the honest state of the schema
— Phase 5 shipped ``zones`` as the supersession of the ``ward`` label and Phase
12 is what makes routing write the zone reference.

The note that used to end here said the label match was better than inventing a
foreign key the pipeline does not populate, "which would produce a public page
that is permanently empty for reasons no reader could diagnose". **That is
exactly what it produced.** Nothing writes ``ward`` — it appears in no domain
event and no entry in ``events/catalog.py`` — so on a deployment with 671
processed complaints every zone published ``total_reports: 0``, and the reason
was undiagnosable from the outside in precisely the way the note feared.

So the join falls back to the geometry, and the fallback is not a second source
of truth: a report's location is *already* the thing routing would consult
(``api/v1/places.py`` resolves a coordinate to a zone chain with the same
``ST_Covers`` over the same GiST index). See ``_in_zone`` for the predicate and
for why it is gated on ``ward IS NULL`` rather than replacing the label.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from geoalchemy2 import Geometry
from sqlalchemy import and_, cast, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from nemesis.db.models.complaint import Complaint
from nemesis.db.models.organisation import Contractor, ContractorCertification, Zone
from nemesis.db.models.taxonomy import TaxonomyNode
from nemesis.db.models.work_order import BudgetAllocation, WorkOrder
from nemesis.domain.lifecycle import AssigneeType, ComplaintStatus, WorkOrderStatus
from nemesis.public.policy import coarsen
from nemesis.tenancy.context import tenant_scope

#: Statuses that mean the municipality considers the matter finished. Read from
#: the lifecycle enum rather than restated as strings, so a phase that adds a
#: terminal state does not silently drop it out of every published resolution
#: rate — the number would fall and nothing would say why.
RESOLVED_COMPLAINT_STATUSES: frozenset[str] = frozenset(
    {ComplaintStatus.RESOLVED, ComplaintStatus.CLOSED}
)

#: Statuses that mean it is still somebody's problem.
OPEN_COMPLAINT_STATUSES: frozenset[str] = frozenset(
    set(ComplaintStatus) - RESOLVED_COMPLAINT_STATUSES - {ComplaintStatus.DISPUTED}
)

#: §22.2, carried on every figure this system computed rather than observed.
#: The legal reasoning is the blueprint's: a system-derived rate presented as
#: fact is an assertion about a named party, and the disclaimer is what keeps it
#: an observation about data.
SYSTEM_FLAGGED_NOTICE = (
    "System-computed from reported data and under human review. Figures are not "
    "verified findings and must not be presented as proven fact."
)

RATING_DISCLAIMER = (
    "A track record, not a score. NEMESIS does not collapse a contractor to a "
    "single rating (§16.1), and a disputed or auto-confirmed closure is counted "
    "separately rather than folded into a headline number."
)


@dataclass(frozen=True, slots=True)
class Suppression:
    """The k-anonymity decision for one response.

    Carried alongside the data rather than expressed as a missing key, because
    "no complaints in this ward" and "too few complaints to publish" are
    different facts and a consumer building a map needs to distinguish them. An
    absent bucket that silently means the second is how a transparency API
    misleads while technically disclosing nothing.
    """

    threshold: int
    suppressed_buckets: int = 0

    def hide(self, count: int) -> bool:
        return 0 < count < self.threshold


@dataclass(frozen=True, slots=True)
class CategoryCount:
    category: str
    count: int


@dataclass(frozen=True, slots=True)
class ZoneSummary:
    zone_code: str
    zone_name: str
    zone_kind: str
    centroid: tuple[float, float] | None
    total_reports: int
    open_reports: int
    resolved_reports: int
    auto_confirmed_resolutions: int
    median_resolution_hours: float | None
    by_category: tuple[CategoryCount, ...] = field(default_factory=tuple)
    suppressed: bool = False
    suppression: Suppression = field(default_factory=lambda: Suppression(threshold=0))


@dataclass(frozen=True, slots=True)
class ContractorProfile:
    contractor_id: uuid.UUID
    contractor_name: str
    registration_id: str
    active_since: date | None
    work_orders_completed: int
    work_orders_open: int
    on_time_rate: float | None
    disputed_count: int
    certified_categories: tuple[str, ...]
    suppressed: bool = False


@dataclass(frozen=True, slots=True)
class BudgetLine:
    funding_source: str
    allocated_amount: str
    spent_amount: str
    utilisation_rate: float | None


@dataclass(frozen=True, slots=True)
class BudgetSummary:
    zone_code: str
    fiscal_year: str
    currency: str
    allocations: tuple[BudgetLine, ...]


#: ISO 4217. A constant rather than tenant configuration *for now*, and named
#: here so the day a tenant outside India onboards, the failure is a grep hit
#: rather than a silently wrong currency symbol on a public budget page.
#: Phase 27 owns commercial configuration and is where this becomes tenant data.
PUBLISHED_CURRENCY = "INR"


async def zone_summary(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    zone_code: str,
    threshold: int,
    since: datetime | None = None,
) -> ZoneSummary | None:
    """§26.4's ward summary, for one place, suppressed below ``threshold``.

    ``None`` when the zone does not exist for this tenant — which the route
    turns into 404, never 403, for the reason ``api.deps`` gives about tenant
    lookup: a distinguishable "exists but withheld" lets an unauthenticated
    caller enumerate a tenant's internal geography one request at a time.
    """
    with tenant_scope(tenant_id):
        zone = (
            await session.execute(
                select(Zone.code, Zone.name, Zone.kind, Zone.centroid).where(
                    Zone.tenant_id == tenant_id,
                    Zone.code == zone_code,
                    Zone.is_active.is_(True),
                )
            )
        ).one_or_none()
        if zone is None:
            return None

        centroid = await _zone_centroid(session, tenant_id=tenant_id, zone_code=zone_code)

        window: list[ColumnElement[bool]] = [
            Complaint.tenant_id == tenant_id,
            _in_zone(tenant_id, zone_code),
        ]
        if since is not None:
            window.append(Complaint.reported_at >= since)

        totals = (
            await session.execute(
                select(
                    func.count().label("total"),
                    func.count()
                    .filter(Complaint.status.in_(sorted(OPEN_COMPLAINT_STATUSES)))
                    .label("open"),
                    func.count()
                    .filter(Complaint.status.in_(sorted(RESOLVED_COMPLAINT_STATUSES)))
                    .label("resolved"),
                ).where(*window)
            )
        ).one()

        total = int(totals.total)
        suppression = Suppression(threshold=threshold)
        if suppression.hide(total):
            # Below the floor: publish the shape and the fact of suppression,
            # and no measure at all. Returning zeros instead would be a lie that
            # a consumer cannot distinguish from a genuinely quiet ward.
            return ZoneSummary(
                zone_code=zone.code,
                zone_name=zone.name,
                zone_kind=zone.kind,
                centroid=centroid,
                total_reports=0,
                open_reports=0,
                resolved_reports=0,
                auto_confirmed_resolutions=0,
                median_resolution_hours=None,
                by_category=(),
                suppressed=True,
                suppression=Suppression(threshold=threshold, suppressed_buckets=1),
            )

        categories, hidden = await _category_breakdown(session, window=window, threshold=threshold)
        auto_confirmed = await _auto_confirmed_count(
            session, tenant_id=tenant_id, zone_code=zone_code, since=since
        )
        median_hours = await _median_resolution_hours(
            session, tenant_id=tenant_id, zone_code=zone_code, since=since
        )

    return ZoneSummary(
        zone_code=zone.code,
        zone_name=zone.name,
        zone_kind=zone.kind,
        centroid=centroid,
        total_reports=total,
        open_reports=int(totals.open),
        resolved_reports=int(totals.resolved),
        auto_confirmed_resolutions=auto_confirmed,
        median_resolution_hours=median_hours,
        by_category=categories,
        suppressed=False,
        suppression=Suppression(threshold=threshold, suppressed_buckets=hidden),
    )


def _in_zone(tenant_id: uuid.UUID, zone_code: str) -> ColumnElement[bool]:
    """Whether a complaint belongs to this zone — by label, or failing that, by where it is.

    **Two branches, and the order matters.**

    ``ward == zone_code`` is the primary and stays primary. A tenant whose
    pipeline routes reports to zones has an authoritative answer already, and
    that answer must win: routing can legitimately place a report somewhere its
    raw coordinate does not fall — a report filed from across the road, a ward
    boundary corrected after the fact, an operator moving a case.

    The geometry branch fires **only when the label is NULL**, which is the
    "nobody has routed this yet" case. It is not a guess. ``Zone.boundary`` is
    the tenant's own published geometry and ``Complaint.location`` is where the
    report was made; ``ST_Covers`` between them is the same question
    ``places.py`` answers for §E17.1's Place card, against the same two GiST
    indexes (``ix_zones_boundary``, ``ix_complaints_location``). If those two
    disagreed, the Place card a citizen is shown would already be wrong.

    **It walks down, not just across.** Only leaves carry geometry in practice —
    a tenant draws its wards and lets the zone and the city be their union — so
    matching ``Zone.code`` alone would leave every parent at zero while its
    children filled in. The ``path`` prefixes gather this zone *and every
    descendant*, which is what makes a city's total the union of its wards
    rather than a separate empty row. ``EXISTS`` rather than a join, so a report
    covered by two nested boundaries is counted once for each zone that contains
    it and once only.

    **It retires itself.** The day routing writes ``ward``, the first branch
    matches and this one never evaluates. Nothing here has to be removed for
    Phase 12 to take over — which is the property that made this preferable to
    backfilling a column or registering an event for it.
    """
    # LIKE metacharacters in a tenant-authored code would silently widen the
    # match. Escaped rather than trusted: `code` is control-plane data, and the
    # rule elsewhere in this file is that a predicate says what it means.
    safe = zone_code.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    covering = (
        select(literal(1))
        .select_from(Zone)
        .where(
            Zone.tenant_id == tenant_id,
            Zone.is_active.is_(True),
            Zone.boundary.isnot(None),
            or_(
                Zone.code == zone_code,
                # A root: `CITY/...`. And a zone anywhere below one: `.../CITY/...`.
                Zone.path.like(f"{safe}/%", escape="\\"),
                Zone.path.like(f"%/{safe}/%", escape="\\"),
            ),
            func.ST_Covers(Zone.boundary, Complaint.location),
        )
        .exists()
    )

    return or_(
        Complaint.ward == zone_code,
        and_(Complaint.ward.is_(None), covering),
    )


async def _zone_centroid(
    session: AsyncSession, *, tenant_id: uuid.UUID, zone_code: str
) -> tuple[float, float] | None:
    """The zone's stored centroid, coarsened.

    Coarsened even though a ward centroid is not a citizen's location: the
    function that publishes a coordinate is the same one everywhere, so there is
    no code path where somebody has to remember which coordinates need rounding.
    A rule with an exception is a rule somebody applies the exception to by
    mistake.
    """
    row = (
        await session.execute(
            select(
                func.ST_Y(cast(Zone.centroid, Geometry)).label("lat"),
                func.ST_X(cast(Zone.centroid, Geometry)).label("lng"),
            ).where(
                Zone.tenant_id == tenant_id,
                Zone.code == zone_code,
                Zone.centroid.is_not(None),
            )
        )
    ).one_or_none()
    if row is None or row.lat is None or row.lng is None:
        return None
    lat, lng = coarsen(row.lat), coarsen(row.lng)
    return None if lat is None or lng is None else (lat, lng)


async def _category_breakdown(
    session: AsyncSession,
    *,
    window: Sequence[ColumnElement[bool]],
    threshold: int,
) -> tuple[tuple[CategoryCount, ...], int]:
    """Counts per taxonomy key, with thin buckets withheld and counted.

    The count of withheld buckets is returned rather than discarded. A ward with
    forty reports across nine categories, six of which are suppressed, is a
    different picture from one with forty across three — and a consumer that
    cannot see the difference will read the second from the first.
    """
    rows = (
        (
            await session.execute(
                select(Complaint.category, func.count().label("count"))
                .where(*window, Complaint.category.is_not(None))
                .group_by(Complaint.category)
                .order_by(func.count().desc(), Complaint.category)
            )
        )
        .tuples()
        .all()
    )
    kept: list[CategoryCount] = []
    hidden = 0
    for category, count in rows:
        if count < threshold:
            hidden += 1
            continue
        kept.append(CategoryCount(category=str(category), count=int(count)))
    return tuple(kept), hidden


async def _auto_confirmed_count(
    session: AsyncSession, *, tenant_id: uuid.UUID, zone_code: str, since: datetime | None
) -> int:
    """§44's distinguishability requirement, on a public surface.

    An auto-confirmed closure is one nobody looked at — the citizen simply did
    not respond inside the window. Folding it into a resolution rate makes a
    department that closes everything on a timer look identical to one that
    fixes things, which is the exact misreading a transparency API is supposed
    to prevent.
    """
    predicates = [
        WorkOrder.tenant_id == tenant_id,
        WorkOrder.status == WorkOrderStatus.CLOSED,
        WorkOrder.complaint_cluster_id.in_(
            select(Complaint.cluster_id).where(
                Complaint.tenant_id == tenant_id,
                _in_zone(tenant_id, zone_code),
                Complaint.cluster_id.is_not(None),
            )
        ),
    ]
    if since is not None:
        predicates.append(WorkOrder.created_at >= since)
    # The auto-confirmation flag lives on the `citizen_confirmed` event, not on
    # the work order row — Phase 15 owns the closure loop and the projector that
    # would materialise it. Until then this counts closures with no recorded
    # SSIM verification, which is the observable proxy: a closure that passed
    # structural verification had a human path through it, and one that did not
    # is exactly the case §44 wants kept visible. Narrower than the real
    # question, stated rather than presented as the real question.
    predicates.append(WorkOrder.ssim_score.is_(None))
    return int(
        (
            await session.execute(select(func.count()).select_from(WorkOrder).where(*predicates))
        ).scalar_one()
    )


async def _median_resolution_hours(
    session: AsyncSession, *, tenant_id: uuid.UUID, zone_code: str, since: datetime | None
) -> float | None:
    """Median, never mean.

    One complaint open for three years drags a mean past every real experience in
    the ward, and the resulting number is both technically correct and useless to
    the citizen deciding whether reporting is worth the effort. The median is the
    typical case, which is the question being asked.
    """
    predicates = [
        WorkOrder.tenant_id == tenant_id,
        WorkOrder.status == WorkOrderStatus.CLOSED,
        WorkOrder.complaint_cluster_id.in_(
            select(Complaint.cluster_id).where(
                Complaint.tenant_id == tenant_id,
                _in_zone(tenant_id, zone_code),
                Complaint.cluster_id.is_not(None),
            )
        ),
    ]
    if since is not None:
        predicates.append(WorkOrder.created_at >= since)

    value = (
        await session.execute(
            select(
                func.percentile_cont(0.5).within_group(
                    func.extract("epoch", WorkOrder.updated_at - WorkOrder.created_at)
                )
            )
            .select_from(WorkOrder)
            .where(*predicates)
        )
    ).scalar_one_or_none()
    return None if value is None else round(float(value) / 3600.0, 1)


async def contractor_profile(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    contractor_id: uuid.UUID,
    threshold: int,
) -> ContractorProfile | None:
    """§16.1's contractor track record.

    Suppressed below the same floor as everything else, and for a sharper
    reason: a contractor with two completed jobs and one dispute has a published
    "33% dispute rate" that is statistical noise presented as a finding about a
    named commercial entity. §16.4 ships the appeal path in the same phase as the
    accountability feature, and the honest first line of that defence is not
    publishing a rate that cannot mean anything.
    """
    with tenant_scope(tenant_id):
        entity = (
            await session.execute(
                select(
                    Contractor.id,
                    Contractor.name,
                    Contractor.registration_id,
                    Contractor.active_since,
                ).where(Contractor.tenant_id == tenant_id, Contractor.id == contractor_id)
            )
        ).one_or_none()
        if entity is None:
            return None

        counts = (
            await session.execute(
                select(
                    func.count().label("total"),
                    func.count()
                    .filter(WorkOrder.status == WorkOrderStatus.CLOSED)
                    .label("completed"),
                    func.count()
                    .filter(WorkOrder.status == WorkOrderStatus.DISPUTED)
                    .label("disputed"),
                    func.count()
                    .filter(
                        and_(
                            WorkOrder.status == WorkOrderStatus.CLOSED,
                            WorkOrder.sla_deadline.is_not(None),
                            WorkOrder.updated_at <= WorkOrder.sla_deadline,
                        )
                    )
                    .label("on_time"),
                    func.count()
                    .filter(
                        and_(
                            WorkOrder.status == WorkOrderStatus.CLOSED,
                            WorkOrder.sla_deadline.is_not(None),
                        )
                    )
                    .label("measurable"),
                ).where(
                    WorkOrder.tenant_id == tenant_id,
                    WorkOrder.assigned_to_type == AssigneeType.CONTRACTOR,
                    WorkOrder.assigned_to_id == contractor_id,
                )
            )
        ).one()

        certified = (
            (
                await session.execute(
                    select(TaxonomyNode.key)
                    .join(
                        ContractorCertification,
                        and_(
                            ContractorCertification.taxonomy_node_id == TaxonomyNode.id,
                            ContractorCertification.tenant_id == TaxonomyNode.tenant_id,
                        ),
                    )
                    .where(
                        ContractorCertification.tenant_id == tenant_id,
                        TaxonomyNode.tenant_id == tenant_id,
                        ContractorCertification.contractor_id == contractor_id,
                        # Only current certifications. A lapsed one published as
                        # a live capability is the exact defect Phase 5 replaced
                        # `categories_certified TEXT[]` to make impossible.
                        or_(
                            ContractorCertification.valid_until.is_(None),
                            ContractorCertification.valid_until >= func.current_date(),
                        ),
                    )
                    .order_by(TaxonomyNode.key)
                )
            )
            .scalars()
            .all()
        )

    completed = int(counts.completed)
    total = int(counts.total)
    if 0 < total < threshold:
        return ContractorProfile(
            contractor_id=entity.id,
            contractor_name=entity.name,
            registration_id=entity.registration_id,
            active_since=entity.active_since,
            work_orders_completed=0,
            work_orders_open=0,
            on_time_rate=None,
            disputed_count=0,
            certified_categories=tuple(str(key) for key in certified),
            suppressed=True,
        )

    measurable = int(counts.measurable)
    return ContractorProfile(
        contractor_id=entity.id,
        contractor_name=entity.name,
        registration_id=entity.registration_id,
        active_since=entity.active_since,
        work_orders_completed=completed,
        work_orders_open=total - completed - int(counts.disputed),
        on_time_rate=(round(int(counts.on_time) / measurable, 3) if measurable else None),
        disputed_count=int(counts.disputed),
        certified_categories=tuple(str(key) for key in certified),
        suppressed=False,
    )


async def budget_summary(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    zone_code: str,
    fiscal_year: str,
) -> BudgetSummary:
    """§17.6's ward budget view.

    **Not suppressed, and deliberately.** A budget allocation is a published
    public-finance figure about a municipality, not an observation about any
    citizen — there is no small-cell disclosure to make, and withholding a line
    because only one scheme funded a ward would hide precisely the thing an RTI
    applicant is looking for.

    Amounts are rendered as strings. They are ``NUMERIC`` in the database for
    the §17.2 rate-card reasoning, and turning them into JSON floats on the way
    out would reintroduce the sub-rupee ghosts that column type exists to
    prevent — in the one place where a reader will compare the number against a
    printed document.
    """
    with tenant_scope(tenant_id):
        rows = (
            (
                await session.execute(
                    select(
                        BudgetAllocation.funding_source,
                        func.sum(BudgetAllocation.allocated_amount).label("allocated"),
                        func.sum(BudgetAllocation.spent_amount).label("spent"),
                    )
                    .where(
                        BudgetAllocation.tenant_id == tenant_id,
                        BudgetAllocation.ward == zone_code,
                        BudgetAllocation.fiscal_year == fiscal_year,
                    )
                    .group_by(BudgetAllocation.funding_source)
                    .order_by(BudgetAllocation.funding_source)
                )
            )
            .tuples()
            .all()
        )

    lines = tuple(
        BudgetLine(
            funding_source=str(source),
            allocated_amount=f"{allocated:.2f}",
            spent_amount=f"{spent:.2f}",
            utilisation_rate=(round(float(spent) / float(allocated), 3) if allocated else None),
        )
        for source, allocated, spent in rows
    )
    return BudgetSummary(
        zone_code=zone_code,
        fiscal_year=fiscal_year,
        currency=PUBLISHED_CURRENCY,
        allocations=lines,
    )


async def zone_index(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    threshold: int,
    limit: int,
    offset: int,
) -> tuple[tuple[ZoneSummary, ...], int]:
    """Every published zone, so a consumer can discover them without guessing.

    Without this, §26.4 is three endpoints keyed by identifiers a caller has no
    way to learn — which makes a "public API" one that only works for people who
    already have an internal document. The listing carries the same suppression
    as the detail view.
    """
    with tenant_scope(tenant_id):
        total = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(Zone)
                    .where(Zone.tenant_id == tenant_id, Zone.is_active.is_(True))
                )
            ).scalar_one()
        )
        codes = (
            (
                await session.execute(
                    select(Zone.code)
                    .where(Zone.tenant_id == tenant_id, Zone.is_active.is_(True))
                    .order_by(Zone.path, Zone.code)
                    .limit(limit)
                    .offset(offset)
                )
            )
            .scalars()
            .all()
        )

    summaries: list[ZoneSummary] = []
    for code in codes:
        summary = await zone_summary(
            session, tenant_id=tenant_id, zone_code=str(code), threshold=threshold
        )
        if summary is not None:
            summaries.append(summary)
    return tuple(summaries), total


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


#: Re-exported so the route layer does not import sqlalchemy just to build a
#: rate. Kept as a function rather than inlined because a division by zero on a
#: quiet ward is the kind of thing that only shows up in production.
def rate(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else round(numerator / denominator, 3)
