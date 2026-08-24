"""Stage 1, Stage 2 and the band, run in order against one report.

This module owns the *sequence* and nothing else. It resolves the policy, reads
the report's own vectors, calls the two query modules, hands the evidence to
``decide``, and returns what happened. It appends no events and writes no rows —
that is ``merge``'s job — because the harness needs to evaluate thousands of
pairs without writing any, and an engine that decided and committed in one call
could only be measured by measuring its side effects.

**Why the report's embeddings are read here rather than taken from state.**
``StageContext.state`` is the projection, and the two vector columns are the
documented exception to the projection rule (``perception.embeddings``): they
live on the ``complaints`` row and no event carries them, because a 512-float
payload in an append-only log is a cost paid forever for a value that is
derivable. So the engine reads the row. It is the one place in the package that
reads a complaint's own columns rather than its projected state, and it is
deliberate rather than an oversight.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.config import DedupSettings
from nemesis.db.models.complaint import Complaint
from nemesis.dedup.candidates import find_candidates
from nemesis.dedup.decide import DedupDecision, ScoredCandidate, decide
from nemesis.dedup.errors import DedupIntegrityError
from nemesis.dedup.similarity import score_candidates
from nemesis.policy.documents import DedupBand, DedupThresholds
from nemesis.policy.resolver import RESOLVER, category_lineage, resolve_dedup_band


@dataclass(frozen=True, slots=True)
class DedupEvaluation:
    """The decision, plus everything needed to explain and stamp it."""

    decision: DedupDecision
    band: DedupBand
    #: The Phase 6 policy version that produced the band. Recorded on the event
    #: so a threshold change never rewrites what an old decision meant.
    policy_version: str
    #: True when Stage 1 hit its candidate cap. A "no match" under truncation is
    #: a weaker statement than a "no match" over the whole neighbourhood, and
    #: the caller logs it differently.
    truncated: bool
    #: Candidates Stage 1 returned, before Stage 2 scored any of them. The
    #: numerator of the phase gate's elimination ratio.
    stage1_candidates: int


async def load_embeddings(
    session: AsyncSession, *, tenant_id: uuid.UUID, complaint_id: uuid.UUID
) -> tuple[list[float] | None, list[float] | None]:
    """The report's own vectors, or ``None`` where perception produced none."""
    row = (
        await session.execute(
            select(Complaint.text_embedding, Complaint.image_embedding).where(
                Complaint.tenant_id == tenant_id, Complaint.id == complaint_id
            )
        )
    ).one_or_none()
    if row is None:
        return None, None
    text, image = row
    return (
        None if text is None else [float(value) for value in text],
        None if image is None else [float(value) for value in image],
    )


async def evaluate(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    complaint_id: uuid.UUID,
    state: Mapping[str, Any],
    settings: DedupSettings,
) -> DedupEvaluation:
    """Decide what this report is a duplicate of, if anything."""
    latitude, longitude = _coordinates(state, complaint_id=complaint_id)
    reported_at = _reported_at(state, complaint_id=complaint_id)
    category = state.get("category")
    category = category if isinstance(category, str) else None

    resolved = await RESOLVER.dedup_thresholds(session, tenant_id=tenant_id)
    thresholds: DedupThresholds = resolved.body
    lineage: Sequence[str] = (
        await category_lineage(session, tenant_id=tenant_id, category=category)
        if category is not None
        else ()
    )
    band = resolve_dedup_band(thresholds, lineage=lineage)

    found = await find_candidates(
        session,
        tenant_id=tenant_id,
        latitude=latitude,
        longitude=longitude,
        reported_at=reported_at,
        # The band's radius and window, not the platform defaults. §14.3 makes
        # these per-category for a reason, and reading `settings` here would
        # quietly restore the single global threshold the control plane exists
        # to abolish.
        radius_meters=band.geo_radius_meters,
        window_hours=band.time_window_hours,
        category=category,
        limit=settings.max_candidates,
    )

    text_embedding, image_embedding = await load_embeddings(
        session, tenant_id=tenant_id, complaint_id=complaint_id
    )
    similarities = await score_candidates(
        session,
        tenant_id=tenant_id,
        cluster_ids=[candidate.cluster_id for candidate in found.candidates],
        text_embedding=text_embedding,
        image_embedding=image_embedding,
        exclude_complaint_id=complaint_id,
        max_members_per_cluster=settings.max_members_per_cluster,
    )

    scored = tuple(
        ScoredCandidate(
            cluster_id=candidate.cluster_id,
            geo_distance_meters=candidate.geo_distance_meters,
            image_similarity=(
                similarities[candidate.cluster_id].image_similarity
                if candidate.cluster_id in similarities
                else None
            ),
            text_similarity=(
                similarities[candidate.cluster_id].text_similarity
                if candidate.cluster_id in similarities
                else None
            ),
            report_count=candidate.report_count,
        )
        for candidate in found.candidates
    )

    return DedupEvaluation(
        decision=decide(scored, band=band, ambiguous_margin=settings.ambiguous_margin),
        band=band,
        policy_version=resolved.stamp,
        truncated=found.truncated,
        stage1_candidates=len(found),
    )


def _coordinates(state: Mapping[str, Any], *, complaint_id: uuid.UUID) -> tuple[float, float]:
    latitude, longitude = state.get("latitude"), state.get("longitude")
    if not isinstance(latitude, int | float) or not isinstance(longitude, int | float):
        raise DedupIntegrityError(
            f"complaint {complaint_id} reached dedup with no coordinates in its projected "
            f"state; `complaint_submitted` requires both, so either the projection is "
            f"incomplete or the stage ran against the wrong entity"
        )
    return float(latitude), float(longitude)


def _reported_at(state: Mapping[str, Any], *, complaint_id: uuid.UUID) -> datetime:
    reported_at = state.get("reported_at")
    if isinstance(reported_at, str):
        reported_at = datetime.fromisoformat(reported_at)
    if not isinstance(reported_at, datetime):
        raise DedupIntegrityError(
            f"complaint {complaint_id} reached dedup with no `reported_at`; the time window "
            f"cannot be applied without it, and a window silently skipped would merge "
            f"reports months apart"
        )
    if reported_at.tzinfo is None:
        raise DedupIntegrityError(
            f"complaint {complaint_id} has a naive `reported_at`; the dedup window is "
            f"compared across tenants in different zones and a naive timestamp would be "
            f"interpreted as whatever the worker's locale happens to be"
        )
    return reported_at


__all__ = ["DedupEvaluation", "evaluate", "load_embeddings"]
