"""Turning a decision into events — and undoing one without editing history.

Every outcome puts the report in exactly one cluster. That is a deliberate
invariant rather than a convenience: work orders attach to clusters (§15), so a
complaint with no cluster is a complaint no department can be assigned to, and
"distinct" would quietly become "dropped". A report that matched nothing is a
cluster of one, and the ``outcome`` field on ``complaint_clustered`` is what
keeps a cluster of one from being confused with a cluster that failed.

**Reversal is the part worth reading.** §14.3 requires an incorrect merge be
undoable, and undoable *without mutating history*, because the log is the
evidence trail the entire product sells. So ``revert`` appends three events and
deletes nothing: the old cluster records that it lost a member, a fresh cluster
records that it gained one, and the complaint records its new home. Anyone
reading the log afterwards sees that the system merged two reports, was told it
was wrong, and corrected itself — which is a different and more trustworthy
story than a log that only ever shows the corrected state.

**Nothing in this phase writes ``ComplaintCluster.superseded_by_id``, and that
is correct rather than unfinished.** Stage 1 filters on it, so the column is not
inert — but the reversal implemented here removes *one member* from a cluster
that goes on existing with its remaining members. Retiring a whole cluster in
favour of another is a cluster-to-cluster merge, which nothing has asked for
yet: it is what a reviewer resolving an ambiguous band would need, and the
review action that would call it arrives with Phase 14's workflow. The filter is
written now because a retired cluster must never accept new members the day that
path does exist, and adding the predicate later would mean auditing every query
that had been written without it.
"""

from __future__ import annotations

import uuid
from typing import Any, Final

from geoalchemy2 import Geometry
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.db.models.complaint import Complaint
from nemesis.dedup.decide import DedupOutcome
from nemesis.dedup.engine import DedupEvaluation
from nemesis.dedup.errors import DedupIntegrityError
from nemesis.domain.lifecycle import EntityType
from nemesis.pipeline.stages import EmittedEvent
from nemesis.projections.replay import replay_entity
from nemesis.trust.review import ReviewReason, queue

CLUSTER = EntityType.COMPLAINT_CLUSTER
COMPLAINT = EntityType.COMPLAINT

#: Stamped when a complaint's chain carries no dedup policy version — which can
#: only happen for a merge recorded before this field existed. Spelled out
#: rather than left as an empty string so it reads as a known gap in a log
#: rather than as a version somebody forgot to set.
_UNKNOWN_POLICY: Final = "unknown"


def events_for(
    evaluation: DedupEvaluation,
    *,
    complaint_id: uuid.UUID,
    latitude: float,
    longitude: float,
    new_cluster_id: uuid.UUID,
) -> tuple[EmittedEvent, ...]:
    """The events one decision produces, on both chains.

    ``new_cluster_id`` is passed in rather than generated here so the caller can
    make it deterministic. That matters for the harness and for replay: a
    function that minted a UUID internally could not be evaluated twice and
    compared, and the simulation engine's whole premise is comparing two runs.
    """
    decision = evaluation.decision

    if decision.outcome is DedupOutcome.MERGE:
        cluster_id = decision.cluster_id
        if cluster_id is None:
            raise DedupIntegrityError(
                "a MERGE decision carried no cluster_id; `decide` sets one on exactly the "
                "merge path, so this is a decision constructed by hand and not by the engine"
            )
        return (
            EmittedEvent(
                entity_type=CLUSTER,
                entity_id=cluster_id,
                event_type="cluster_match_found",
                payload={
                    "complaint_id": str(complaint_id),
                    "geo_distance_meters": decision.geo_distance_meters,
                    "image_similarity": decision.image_similarity,
                    "text_similarity": decision.text_similarity,
                    "combined_confidence": decision.combined_confidence,
                    "policy_version": evaluation.policy_version,
                    "report_count_after": decision.report_count_before + 1,
                },
            ),
            _clustered(
                complaint_id=complaint_id,
                cluster_id=cluster_id,
                outcome=decision.outcome,
                confidence=decision.combined_confidence,
                policy_version=evaluation.policy_version,
            ),
        )

    return (
        EmittedEvent(
            entity_type=CLUSTER,
            entity_id=new_cluster_id,
            event_type="cluster_created",
            payload={
                "seed_complaint_id": str(complaint_id),
                "latitude": latitude,
                "longitude": longitude,
            },
        ),
        _clustered(
            complaint_id=complaint_id,
            cluster_id=new_cluster_id,
            outcome=decision.outcome,
            # A cluster of one that matched nothing has nothing to be confident
            # about; one that was too close to call does, and the number is the
            # reason a human is being asked.
            confidence=(
                decision.combined_confidence
                if decision.outcome is DedupOutcome.INVESTIGATE
                else None
            ),
            policy_version=evaluation.policy_version,
        ),
    )


def _clustered(
    *,
    complaint_id: uuid.UUID,
    cluster_id: uuid.UUID,
    outcome: DedupOutcome,
    confidence: float | None,
    policy_version: str,
) -> EmittedEvent:
    return EmittedEvent(
        entity_type=COMPLAINT,
        entity_id=complaint_id,
        event_type="complaint_clustered",
        payload={
            "cluster_id": str(cluster_id),
            "outcome": outcome.value,
            "combined_confidence": confidence,
            "policy_version": policy_version,
        },
    )


async def queue_ambiguous(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    complaint_id: uuid.UUID,
    evaluation: DedupEvaluation,
    trust_score: float,
) -> EmittedEvent:
    """Raise the review item §14.1's middle band requires, and its event.

    Returns the event rather than appending it, for the reason
    ``trust.review.queue`` states: the orchestrator appends, so the row and the
    event land in one transaction with everything else the stage produced.
    """
    decision = evaluation.decision
    evidence: dict[str, Any] = {
        "combined_confidence": decision.combined_confidence,
        "image_similarity": decision.image_similarity,
        "text_similarity": decision.text_similarity,
        "geo_distance_meters": decision.geo_distance_meters,
        "merge_threshold": evaluation.band.merge_threshold,
        "investigate_threshold": evaluation.band.investigate_threshold,
        "policy_version": evaluation.policy_version,
        "candidates_considered": decision.considered,
        "runner_up_confidence": decision.runner_up_confidence,
        "ambiguous_between": [str(value) for value in decision.ambiguous_between],
    }
    queued = await queue(
        session,
        tenant_id=tenant_id,
        complaint_id=complaint_id,
        reason=ReviewReason.AMBIGUOUS_DEDUP,
        evidence=evidence,
        trust_score=trust_score,
    )
    return EmittedEvent(
        entity_type=COMPLAINT,
        entity_id=complaint_id,
        event_type="review_queued",
        payload={
            "review_item_id": str(queued.review_item_id),
            "reason": queued.reason.value,
            "priority": queued.priority,
            "occurrences": queued.occurrences,
            "trust_score": queued.trust_score,
            "evidence_hash": queued.evidence_hash,
        },
    )


async def revert(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    complaint_id: uuid.UUID,
    reason: str,
    reverted_by: uuid.UUID | None,
    new_cluster_id: uuid.UUID | None = None,
) -> tuple[EmittedEvent, ...]:
    """Undo one merge by appending, never by deleting (§14.3).

    Refuses to revert a complaint that is the only member of its cluster. That
    is not a merge, it is an origin, and "reverting" it would leave the cluster
    empty while its ``cluster_created`` event still names the complaint as its
    seed — a state no projector can represent and no reader could interpret.
    """
    row = (
        await session.execute(
            select(Complaint.cluster_id, Complaint.location).where(
                Complaint.tenant_id == tenant_id, Complaint.id == complaint_id
            )
        )
    ).one_or_none()
    if row is None:
        raise DedupIntegrityError(f"complaint {complaint_id} does not exist in this tenant")

    cluster_id, _ = row
    if cluster_id is None:
        raise DedupIntegrityError(
            f"complaint {complaint_id} is not in a cluster, so there is no merge to revert"
        )

    members = (
        await session.execute(
            select(Complaint.id).where(
                Complaint.tenant_id == tenant_id, Complaint.cluster_id == cluster_id
            )
        )
    ).scalars()
    if len(list(members)) <= 1:
        raise DedupIntegrityError(
            f"complaint {complaint_id} is the only member of cluster {cluster_id}; a "
            f"single-member cluster records that nothing matched, not that a merge "
            f"happened, and reverting it would orphan the cluster_created event that "
            f"names this complaint as the seed"
        )

    latitude, longitude = await _coordinates_of(
        session, tenant_id=tenant_id, complaint_id=complaint_id
    )
    replacement = new_cluster_id or uuid.uuid4()
    policy_version = await _policy_version_of(
        session, tenant_id=tenant_id, complaint_id=complaint_id
    )

    return (
        EmittedEvent(
            entity_type=CLUSTER,
            entity_id=cluster_id,
            event_type="cluster_merge_reverted",
            payload={
                "complaint_id": str(complaint_id),
                "reverted_by": str(reverted_by) if reverted_by else None,
                "reason": reason,
            },
        ),
        EmittedEvent(
            entity_type=CLUSTER,
            entity_id=replacement,
            event_type="cluster_created",
            payload={
                "seed_complaint_id": str(complaint_id),
                "latitude": latitude,
                "longitude": longitude,
            },
        ),
        _clustered(
            complaint_id=complaint_id,
            cluster_id=replacement,
            # The report is its own incident again. `distinct` rather than a
            # fourth outcome value: what a reader needs to know is that it now
            # stands alone, and the *reason* it stands alone is on the cluster
            # chain in the revert event, which is where a reversal belongs.
            outcome=DedupOutcome.DISTINCT,
            confidence=None,
            policy_version=policy_version,
        ),
    )


async def _coordinates_of(
    session: AsyncSession, *, tenant_id: uuid.UUID, complaint_id: uuid.UUID
) -> tuple[float, float]:
    """Latitude and longitude off the stored geography, in that order.

    ``ST_Y`` is latitude and ``ST_X`` is longitude — the opposite of the
    ``ST_MakePoint(longitude, latitude)`` argument order used everywhere this
    codebase writes a point. Both are correct and the asymmetry is PostGIS's,
    which is exactly why it is named here rather than left for a reader to
    rediscover from a merge that landed a hundred kilometres away.
    """
    row = (
        await session.execute(
            select(
                func.ST_Y(Complaint.location.cast(Geometry)),
                func.ST_X(Complaint.location.cast(Geometry)),
            ).where(Complaint.tenant_id == tenant_id, Complaint.id == complaint_id)
        )
    ).one()
    return float(row[0]), float(row[1])


async def _policy_version_of(
    session: AsyncSession, *, tenant_id: uuid.UUID, complaint_id: uuid.UUID
) -> str:
    """The dedup policy version already stamped on this complaint.

    Reused rather than re-resolved, because a revert is a statement about a
    decision made under a particular set of thresholds. Stamping the reversal
    with today's policy would make the log say the old merge was evaluated
    against a document that did not exist when it happened.
    """
    result = await replay_entity(
        session, tenant_id=tenant_id, entity_type=COMPLAINT.value, entity_id=complaint_id
    )
    stamped = result.state.get("dedup_policy_version")
    return stamped if isinstance(stamped, str) else _UNKNOWN_POLICY


__all__ = ["events_for", "queue_ambiguous", "revert"]
