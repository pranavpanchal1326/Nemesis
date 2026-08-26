"""The two stages as SQL: do they find the right rows, and by the right route.

Split from ``test_dedup_decide`` because these fail for different reasons. The
decision rule fails when the arithmetic is wrong; these fail when a predicate is
missing, an index is not used, or a tenant boundary leaks — none of which can be
observed without a real Postgres with real PostGIS and real pgvector.

The elimination test is the phase gate clause that cannot be argued: **Stage 1
eliminates ≥ 90% of candidates before any embedding comparison, verified by
query plan.** It reads ``EXPLAIN (ANALYZE)`` rather than timing the query,
because a fast sequential scan over a seeded test database is exactly what this
gate is designed to stop somebody shipping.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.sql import Select

from nemesis.dedup.candidates import candidate_statement, find_candidates
from nemesis.dedup.similarity import score_candidates
from tests.conftest import postgres_required
from tests.dedup_fixtures import (
    BASE,
    PUNE_LAT,
    PUNE_LON,
    image_vector,
    make_cluster,
    make_incident,
    make_member,
    offset,
    text_vector,
)

pytestmark = [postgres_required, pytest.mark.integration]

RADIUS = 50.0
WINDOW = 72


@pytest.fixture
async def session(migrated_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    maker = async_sessionmaker(migrated_engine, expire_on_commit=False)
    async with maker() as opened:
        yield opened
        await opened.rollback()


async def stage1(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    category: str | None = "pothole",
    limit: int = 50,
    north_m: float = 0.0,
) -> Any:
    latitude, longitude = offset(PUNE_LAT, PUNE_LON, north_m=north_m)
    return await find_candidates(
        session,
        tenant_id=tenant_id,
        latitude=latitude,
        longitude=longitude,
        reported_at=BASE,
        radius_meters=RADIUS,
        window_hours=WINDOW,
        category=category,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# Stage 1 predicates
# ---------------------------------------------------------------------------


async def test_a_nearby_recent_same_category_cluster_is_a_candidate(
    session: AsyncSession, tenant_id: uuid.UUID
) -> None:
    cluster_id = await make_incident(session, tenant_id=tenant_id, seed=1.0, north_m=10.0)

    found = await stage1(session, tenant_id=tenant_id)

    assert [candidate.cluster_id for candidate in found.candidates] == [cluster_id]
    assert found.candidates[0].geo_distance_meters == pytest.approx(10.0, abs=1.0)


async def test_a_cluster_beyond_the_radius_is_eliminated(
    session: AsyncSession, tenant_id: uuid.UUID
) -> None:
    await make_incident(session, tenant_id=tenant_id, seed=1.0, north_m=200.0)

    found = await stage1(session, tenant_id=tenant_id)

    assert found.candidates == ()


async def test_a_cluster_outside_the_time_window_is_eliminated(
    session: AsyncSession, tenant_id: uuid.UUID
) -> None:
    """The pothole that was fixed and reopened.

    Same place, same category, months apart. Matching it would attach a new
    report to a closed work order, which is worse than not matching at all.
    """
    await make_incident(session, tenant_id=tenant_id, seed=1.0, north_m=5.0, age_hours=WINDOW + 24)

    found = await stage1(session, tenant_id=tenant_id)

    assert found.candidates == ()


async def test_a_different_category_is_eliminated_however_close(
    session: AsyncSession, tenant_id: uuid.UUID
) -> None:
    """Without this predicate the only thing between a pothole and a garbage
    pile at the same junction is an embedding threshold."""
    await make_incident(
        session, tenant_id=tenant_id, seed=1.0, north_m=2.0, category="garbage_pile"
    )

    found = await stage1(session, tenant_id=tenant_id, category="pothole")

    assert found.candidates == ()


async def test_an_unclassified_report_does_not_narrow_by_category(
    session: AsyncSession, tenant_id: uuid.UUID
) -> None:
    """Not knowing the category must mean "do not narrow", never "match other
    unknowns" — the latter is a different and much worse rule."""
    await make_incident(session, tenant_id=tenant_id, seed=1.0, north_m=5.0, category="pothole")

    found = await stage1(session, tenant_id=tenant_id, category=None)

    assert len(found.candidates) == 1


async def test_a_superseded_cluster_never_accepts_new_members(
    session: AsyncSession, tenant_id: uuid.UUID
) -> None:
    replacement = await make_cluster(session, tenant_id=tenant_id)
    retired = await make_cluster(session, tenant_id=tenant_id, superseded_by=replacement)
    await make_member(session, tenant_id=tenant_id, cluster_id=retired)

    found = await stage1(session, tenant_id=tenant_id)

    assert retired not in [candidate.cluster_id for candidate in found.candidates]


async def test_another_tenants_cluster_is_invisible(
    session: AsyncSession, tenant_id: uuid.UUID, other_tenant_id: uuid.UUID
) -> None:
    await make_incident(session, tenant_id=other_tenant_id, seed=1.0, north_m=1.0)

    found = await stage1(session, tenant_id=tenant_id)

    assert found.candidates == ()


async def test_truncation_is_reported_rather_than_absorbed(
    session: AsyncSession, tenant_id: uuid.UUID
) -> None:
    """A capped "no match" is a weaker claim than an uncapped one, and the
    caller has to be able to tell which it got."""
    for index in range(5):
        await make_incident(session, tenant_id=tenant_id, seed=float(index), north_m=index + 1.0)

    found = await stage1(session, tenant_id=tenant_id, limit=3)

    assert len(found.candidates) == 3
    assert found.truncated is True
    # Nearest kept, furthest dropped — so what was discarded is explainable.
    distances = [candidate.geo_distance_meters for candidate in found.candidates]
    assert distances == sorted(distances)


# ---------------------------------------------------------------------------
# The elimination gate
# ---------------------------------------------------------------------------


async def explain(session: AsyncSession, statement: Select[Any]) -> dict[str, Any]:
    compiled = statement.compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
    )
    rows = await session.execute(text(f"EXPLAIN (ANALYZE, FORMAT JSON) {compiled}"))
    plan: dict[str, Any] = rows.scalar_one()[0]["Plan"]
    return plan


def node_types(plan: dict[str, Any]) -> list[str]:
    found = [str(plan["Node Type"])]
    for child in plan.get("Plans", []):
        found.extend(node_types(child))
    return found


def scanned_relations(plan: dict[str, Any]) -> list[str]:
    found = [str(plan["Relation Name"])] if "Relation Name" in plan else []
    for child in plan.get("Plans", []):
        found.extend(scanned_relations(child))
    return found


def sequential_scans(plan: dict[str, Any]) -> list[str]:
    found = [str(plan.get("Relation Name", "?"))] if plan.get("Node Type") == "Seq Scan" else []
    for child in plan.get("Plans", []):
        found.extend(sequential_scans(child))
    return found


def index_names(plan: dict[str, Any]) -> list[str]:
    found = [str(plan["Index Name"])] if "Index Name" in plan else []
    for child in plan.get("Plans", []):
        found.extend(index_names(child))
    return found


async def test_stage_one_eliminates_at_least_ninety_percent_by_index(
    session: AsyncSession, tenant_id: uuid.UUID
) -> None:
    """The §14.1 gate clause, read off the query plan rather than the clock.

    200 incidents across the city, 4 of them inside the radius. Stage 1 must
    return only those 4 — a 98% elimination — and it must do it without
    sequential-scanning ``complaint_clusters``, because a sequential scan is the
    thing that is imperceptible at 200 rows and fatal at 200 000.
    """
    total = 200
    near = 4
    for index in range(near):
        await make_incident(session, tenant_id=tenant_id, seed=float(index), north_m=5.0 + index)
    for index in range(total - near):
        # Spread out along a line, all far outside the radius.
        await make_incident(
            session, tenant_id=tenant_id, seed=100.0 + index, north_m=500.0 + index * 50.0
        )
    await session.flush()
    # ANALYZE so the planner costs against the seeded distribution rather than
    # against an empty table it has never looked at. Without it Postgres has no
    # statistics and may reasonably pick a sequential scan, and the test would
    # be measuring the absence of statistics rather than the presence of an index.
    await session.execute(text("ANALYZE complaint_clusters"))
    await session.execute(text("ANALYZE complaints"))

    found = await stage1(session, tenant_id=tenant_id)

    eliminated = 1.0 - len(found.candidates) / total
    assert len(found.candidates) == near
    assert eliminated >= 0.90, f"Stage 1 eliminated only {eliminated:.1%} of {total} clusters"

    plan = await explain(
        session,
        candidate_statement(
            tenant_id=tenant_id,
            latitude=PUNE_LAT,
            longitude=PUNE_LON,
            reported_at=BASE,
            radius_meters=RADIUS,
            window_hours=WINDOW,
            category="pothole",
            limit=50,
        ),
    )
    types = node_types(plan)
    assert "complaint_clusters" not in sequential_scans(plan), (
        f"Stage 1 sequential-scanned complaint_clusters; plan nodes were {types}"
    )
    # Named, not merely "some index". The GiST index on the centroid is the one
    # that makes ST_DWithin bounded; satisfying this clause with the tenant_id
    # B-tree while still reading every cluster in the tenant would pass a
    # weaker test and fail the actual requirement.
    assert "ix_complaint_clusters_centroid" in index_names(plan), (
        f"Stage 1 did not use the GiST centroid index; indexes used were {index_names(plan)}, "
        f"plan nodes were {types}"
    )


# ---------------------------------------------------------------------------
# Stage 2
# ---------------------------------------------------------------------------


async def test_similarity_is_the_best_matching_member_not_the_average(
    session: AsyncSession, tenant_id: uuid.UUID
) -> None:
    """A cluster is one problem; matching any one of its reports is matching it.

    Seeded with one member that matches the query closely and three that do not.
    Under a mean the cluster would score poorly and the report would be split off
    into a duplicate incident.
    """
    cluster_id = await make_cluster(session, tenant_id=tenant_id)
    await make_member(
        session,
        tenant_id=tenant_id,
        cluster_id=cluster_id,
        text_embedding=text_vector(1.0),
        image_embedding=image_vector(1.0),
    )
    for index in range(3):
        await make_member(
            session,
            tenant_id=tenant_id,
            cluster_id=cluster_id,
            text_embedding=text_vector(50.0 + index),
            image_embedding=image_vector(50.0 + index),
        )
    await session.flush()

    scored = await score_candidates(
        session,
        tenant_id=tenant_id,
        cluster_ids=[cluster_id],
        text_embedding=text_vector(1.0),
        image_embedding=image_vector(1.0),
        exclude_complaint_id=None,
        max_members_per_cluster=25,
    )

    similarity = scored[cluster_id]
    assert similarity.text_similarity == pytest.approx(1.0, abs=1e-4)
    assert similarity.image_similarity == pytest.approx(1.0, abs=2e-3)
    assert similarity.members_compared == 4


async def test_a_member_without_a_photograph_is_not_a_zero_similarity_photograph(
    session: AsyncSession, tenant_id: uuid.UUID
) -> None:
    cluster_id = await make_cluster(session, tenant_id=tenant_id)
    await make_member(
        session, tenant_id=tenant_id, cluster_id=cluster_id, text_embedding=text_vector(1.0)
    )
    await session.flush()

    scored = await score_candidates(
        session,
        tenant_id=tenant_id,
        cluster_ids=[cluster_id],
        text_embedding=text_vector(1.0),
        image_embedding=image_vector(1.0),
        exclude_complaint_id=None,
        max_members_per_cluster=25,
    )

    assert scored[cluster_id].text_similarity == pytest.approx(1.0, abs=1e-4)
    assert scored[cluster_id].image_similarity is None


async def test_the_report_is_never_scored_against_itself(
    session: AsyncSession, tenant_id: uuid.UUID
) -> None:
    """The redelivery case. The complaint acquires its cluster_id in the same
    transaction as the merge, so a retry after a partial commit is exactly when
    a self-match would score a perfect 1.0 and merge a report into itself."""
    cluster_id = await make_cluster(session, tenant_id=tenant_id)
    subject = await make_member(
        session,
        tenant_id=tenant_id,
        cluster_id=cluster_id,
        text_embedding=text_vector(1.0),
        image_embedding=image_vector(1.0),
    )
    await make_member(
        session,
        tenant_id=tenant_id,
        cluster_id=cluster_id,
        text_embedding=text_vector(80.0),
        image_embedding=image_vector(80.0),
    )
    await session.flush()

    scored = await score_candidates(
        session,
        tenant_id=tenant_id,
        cluster_ids=[cluster_id],
        text_embedding=text_vector(1.0),
        image_embedding=image_vector(1.0),
        exclude_complaint_id=subject,
        max_members_per_cluster=25,
    )

    assert scored[cluster_id].members_compared == 1
    assert scored[cluster_id].text_similarity is not None
    assert scored[cluster_id].text_similarity < 0.99


async def test_members_are_capped_most_recent_first(
    session: AsyncSession, tenant_id: uuid.UUID
) -> None:
    cluster_id = await make_cluster(session, tenant_id=tenant_id)
    for index in range(10):
        await make_member(
            session,
            tenant_id=tenant_id,
            cluster_id=cluster_id,
            reported_at=BASE - timedelta(hours=index),
            text_embedding=text_vector(float(index)),
        )
    await session.flush()

    scored = await score_candidates(
        session,
        tenant_id=tenant_id,
        cluster_ids=[cluster_id],
        text_embedding=text_vector(0.0),
        image_embedding=None,
        exclude_complaint_id=None,
        max_members_per_cluster=3,
    )

    assert scored[cluster_id].members_compared == 3


async def test_another_tenants_members_are_never_compared(
    session: AsyncSession, tenant_id: uuid.UUID, other_tenant_id: uuid.UUID
) -> None:
    cluster_id = await make_cluster(session, tenant_id=other_tenant_id)
    await make_member(
        session, tenant_id=other_tenant_id, cluster_id=cluster_id, text_embedding=text_vector(1.0)
    )
    await session.flush()

    scored = await score_candidates(
        session,
        tenant_id=tenant_id,
        cluster_ids=[cluster_id],
        text_embedding=text_vector(1.0),
        image_embedding=None,
        exclude_complaint_id=None,
        max_members_per_cluster=25,
    )

    assert scored == {}
