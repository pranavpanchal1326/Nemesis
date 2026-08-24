"""Stage 1 — cheap, index-backed elimination before anything reads a vector.

§14.1's two-stage shape exists for one reason: comparing embeddings is the
expensive half, and almost every pair of reports in a city is separated by
something a B-tree or a GiST index can rule out in microseconds. The phase gate
puts a number on it — **Stage 1 must eliminate ≥ 90% of candidates before any
embedding comparison, verified by query plan** — which is why this module
returns candidates rather than scores, and why ``test_dedup_query_plan`` reads
``EXPLAIN`` output instead of trusting that the index is used.

**Three predicates, and why each is here.**

*Geography.* ``ST_DWithin`` against the GiST index on
``complaint_clusters.centroid``. ``ST_Distance(...) < r`` computes the same
answer and cannot use the index — it sequential-scans every incident the tenant
has ever recorded, which is exactly the query that looks fine in a demo and
falls over in year two.

*Time.* A cluster whose last report is outside the band's window is a different
incident at the same place, which is the normal case for a pothole that was
fixed and reopened. Matching it would silently attach a new complaint to a
closed work order.

*Category.* Restricted by an ``EXISTS`` over the cluster's members rather than a
column on the cluster, because a cluster has no category of its own — it has
members that do. A pothole report must not merge into a garbage cluster however
close and however recent, and without this predicate the only thing standing
between those two is an embedding threshold.

The category predicate is skipped when the report has no category — perception
degraded, or the tenant's taxonomy could not resolve one. Skipping is the honest
behaviour: filtering on ``category IS NULL`` would restrict the search to other
unclassified reports, which is a *different* and much worse rule than "we do not
know, so do not narrow".
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Select, and_, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.db.models.complaint import Complaint, ComplaintCluster
from nemesis.dedup.errors import DedupIntegrityError


@dataclass(frozen=True, slots=True)
class Candidate:
    """A cluster that survived Stage 1, with the distance that let it through."""

    cluster_id: uuid.UUID
    geo_distance_meters: float
    report_count: int
    last_reported: datetime


@dataclass(frozen=True, slots=True)
class CandidateSet:
    """Survivors, plus what it took to find them.

    ``truncated`` is the field that earns this wrapper. A cap that silently
    drops candidates turns "no match found" into "no match found among the
    arbitrary subset the database happened to return first", and those two
    sentences have very different consequences for a citizen whose report goes
    unmerged. When the cap binds, the stage says so in the log and in a metric.
    """

    candidates: tuple[Candidate, ...]
    truncated: bool

    def __len__(self) -> int:
        return len(self.candidates)


def candidate_statement(
    *,
    tenant_id: uuid.UUID,
    latitude: float,
    longitude: float,
    reported_at: datetime,
    radius_meters: float,
    window_hours: int,
    category: str | None,
    limit: int,
) -> Select[tuple[uuid.UUID, float, int, datetime]]:
    """The Stage 1 query, built but not executed.

    Separated from ``find_candidates`` so the query-plan test can ``EXPLAIN`` the
    exact statement the engine runs. A test that explains a hand-written copy of
    the query proves the copy uses the index.
    """
    point = func.ST_SetSRID(func.ST_MakePoint(longitude, latitude), 4326).cast(
        ComplaintCluster.centroid.type
    )
    window_start = reported_at - timedelta(hours=window_hours)

    # The tenant predicate is NOT in this list. It is written inline in the
    # `.where()` below, because `check_tenant_scoping.py` reads the AST at the
    # `select()` call site and cannot see a predicate composed behind a variable
    # — which it duly proved by failing this module the first time the filter
    # lived here. A tenant-scoped query the isolation check cannot verify is
    # worth exactly as much as an unscoped one.
    predicates = [
        func.ST_DWithin(ComplaintCluster.centroid, point, radius_meters),
        ComplaintCluster.last_reported >= window_start,
        # A superseded cluster is one a revert retired. Events referencing it
        # must stay resolvable forever, so it is never deleted — but nothing new
        # may join it, or a corrected mistake quietly re-accumulates.
        ComplaintCluster.superseded_by_id.is_(None),
    ]

    if category is not None:
        predicates.append(
            exists(
                select(Complaint.id)
                .where(
                    Complaint.tenant_id == tenant_id,
                    Complaint.cluster_id == ComplaintCluster.id,
                    Complaint.category == category,
                )
                .correlate(ComplaintCluster)
            )
        )

    return (
        select(
            ComplaintCluster.id,
            func.ST_Distance(ComplaintCluster.centroid, point).label("distance_meters"),
            ComplaintCluster.report_count,
            ComplaintCluster.last_reported,
        )
        .where(ComplaintCluster.tenant_id == tenant_id, and_(*predicates))
        # Nearest first, so a cap that binds keeps the most plausible matches
        # rather than an arbitrary page. Ordering by distance also means the
        # truncation is explainable: everything dropped was further away than
        # everything kept.
        .order_by("distance_meters")
        .limit(limit + 1)
    )


async def find_candidates(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    latitude: float,
    longitude: float,
    reported_at: datetime,
    radius_meters: float,
    window_hours: int,
    category: str | None,
    limit: int,
) -> CandidateSet:
    """Run Stage 1 and report whether the cap bound.

    Fetches ``limit + 1`` rows so "there were more" is knowable without a second
    ``COUNT(*)`` over the same predicates — the count would double the work to
    learn a boolean.
    """
    rows = (
        await session.execute(
            candidate_statement(
                tenant_id=tenant_id,
                latitude=latitude,
                longitude=longitude,
                reported_at=reported_at,
                radius_meters=radius_meters,
                window_hours=window_hours,
                category=category,
                limit=limit,
            )
        )
    ).all()

    truncated = len(rows) > limit

    candidates = []
    for cluster_id, distance, report_count, last_reported in rows[:limit]:
        if distance is None:
            raise DedupIntegrityError(
                f"cluster {cluster_id} returned a NULL distance from ST_Distance, which "
                f"means its centroid is NULL — a cluster with no location cannot have "
                f"passed ST_DWithin, so the query and the data disagree"
            )
        candidates.append(
            Candidate(
                cluster_id=cluster_id,
                geo_distance_meters=float(distance),
                report_count=int(report_count),
                last_reported=last_reported,
            )
        )

    return CandidateSet(candidates=tuple(candidates), truncated=truncated)


__all__ = ["Candidate", "CandidateSet", "candidate_statement", "find_candidates"]
