"""Seeding helpers for the Phase 10 query tests.

These insert rows directly rather than driving the event store, and that is a
deliberate split rather than a shortcut: ``test_dedup_stage`` exercises the
event path end to end, while ``test_dedup_queries`` is about whether two SQL
statements find the right rows and use the right indexes. Building a hundred
event chains to test a ``ST_DWithin`` predicate would make the query tests slow
enough that nobody runs them, and would make a failure ambiguous between the
query and the projection that fed it.
"""

from __future__ import annotations

import math
import random
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, insert
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.db.models.complaint import (
    IMAGE_EMBEDDING_DIM,
    TEXT_EMBEDDING_DIM,
    Complaint,
    ComplaintCluster,
)
from nemesis.domain.lifecycle import ComplaintStatus

BASE = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)
#: Pune, matching the fixtures the Phase 8 tests already use.
PUNE_LAT = 18.5204
PUNE_LON = 73.8567

#: Metres per degree of latitude, near enough at this latitude for a test that
#: needs "about 30 m away" rather than a survey.
_METRES_PER_DEGREE = 111_320.0


def offset(latitude: float, longitude: float, *, north_m: float) -> tuple[float, float]:
    """A point a stated number of metres north. Longitude untouched."""
    return latitude + north_m / _METRES_PER_DEGREE, longitude


def unit_vector(dim: int, *, seed: float, spread: float = 0.0) -> list[float]:
    """A deterministic L2-normalised vector.

    ``seed`` picks a direction and ``spread`` rotates away from it, so a caller
    can say "very similar to seed 1" or "unrelated to seed 1" without
    hand-writing 512 floats. Deterministic because a dedup fixture whose
    similarity changes between runs cannot be the basis of a zero-false-positive
    claim.

    **Gaussian components, not a sinusoid.** The first version of this helper
    built each component from ``sin(seed * k + index * c)``, which looks like it
    scatters and does not: every vector it produces is a sample of the same
    smooth wave at a different phase, so two unrelated seeds came out at cosine
    0.997 and a test asserting "these are different reports" was asserting
    nothing. Independent Gaussian components normalised to the sphere are
    near-orthogonal in high dimensions — the property the real encoders have and
    the one the fixtures have to share, or the corpus measures the generator
    instead of the engine.
    """
    rng = random.Random(f"{seed}:{dim}")
    base = [rng.gauss(0.0, 1.0) for _ in range(dim)]
    if spread:
        noise_rng = random.Random(f"noise:{seed}:{spread}:{dim}")
        base = [value + spread * noise_rng.gauss(0.0, 1.0) for value in base]
    norm = math.sqrt(sum(value * value for value in base)) or 1.0
    return [value / norm for value in base]


def text_vector(seed: float, *, spread: float = 0.0) -> list[float]:
    return unit_vector(TEXT_EMBEDDING_DIM, seed=seed, spread=spread)


def image_vector(seed: float, *, spread: float = 0.0) -> list[float]:
    return unit_vector(IMAGE_EMBEDDING_DIM, seed=seed, spread=spread)


def _point(latitude: float, longitude: float) -> object:
    return func.ST_SetSRID(func.ST_MakePoint(longitude, latitude), 4326)


async def make_cluster(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    latitude: float = PUNE_LAT,
    longitude: float = PUNE_LON,
    last_reported: datetime | None = None,
    report_count: int = 1,
    superseded_by: uuid.UUID | None = None,
) -> uuid.UUID:
    cluster_id = uuid.uuid4()
    when = last_reported or BASE
    await session.execute(
        insert(ComplaintCluster).values(
            id=cluster_id,
            tenant_id=tenant_id,
            centroid=_point(latitude, longitude),
            report_count=report_count,
            first_reported=when,
            last_reported=when,
            superseded_by_id=superseded_by,
            version=1,
        )
    )
    return cluster_id


async def make_member(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    cluster_id: uuid.UUID | None,
    category: str | None = "pothole",
    latitude: float = PUNE_LAT,
    longitude: float = PUNE_LON,
    reported_at: datetime | None = None,
    text_embedding: Sequence[float] | None = None,
    image_embedding: Sequence[float] | None = None,
) -> uuid.UUID:
    complaint_id = uuid.uuid4()
    await session.execute(
        insert(Complaint).values(
            id=complaint_id,
            tenant_id=tenant_id,
            status=ComplaintStatus.CLUSTERED.value,
            category=category,
            description_text="a hole in the road",
            location=_point(latitude, longitude),
            reported_at=reported_at or BASE,
            cluster_id=cluster_id,
            text_embedding=list(text_embedding) if text_embedding is not None else None,
            image_embedding=list(image_embedding) if image_embedding is not None else None,
            version=1,
        )
    )
    return complaint_id


async def make_incident(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    seed: float,
    north_m: float = 0.0,
    category: str | None = "pothole",
    age_hours: float = 0.0,
    members: int = 1,
) -> uuid.UUID:
    """A cluster and its members, all at one place with one visual identity."""
    latitude, longitude = offset(PUNE_LAT, PUNE_LON, north_m=north_m)
    when = BASE - timedelta(hours=age_hours)
    cluster_id = await make_cluster(
        session,
        tenant_id=tenant_id,
        latitude=latitude,
        longitude=longitude,
        last_reported=when,
        report_count=members,
    )
    for index in range(members):
        await make_member(
            session,
            tenant_id=tenant_id,
            cluster_id=cluster_id,
            category=category,
            latitude=latitude,
            longitude=longitude,
            reported_at=when,
            text_embedding=text_vector(seed, spread=index * 0.01),
            image_embedding=image_vector(seed, spread=index * 0.01),
        )
    return cluster_id
