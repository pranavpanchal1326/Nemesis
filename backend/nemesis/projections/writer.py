"""Materialise a projected state into the §9.2 current-state tables.

``replay.py`` answers "what does the log say this entity is?" as a dict. This
module is what makes that answer queryable: it upserts the dict into
``complaints``, ``complaint_clusters``, or ``work_orders`` so the API can render
a row without replaying four hundred events, and so PostGIS and pgvector have
something to index.

**These tables are a cache of the log, not a second source of truth.** Three
rules keep that true rather than aspirational:

1. **Nothing outside this module writes them.** Every column here is derived
   from a projected state, which is derived from events. A direct ``UPDATE`` on
   ``complaints`` would produce a row the log does not explain — and the
   §9.1 write-path rule exists precisely to prevent that.
2. **Writes are idempotent upserts keyed on the entity id.** Replaying the same
   events must converge on the same row, whether it is the first write or the
   thousandth, because that convergence is the whole basis of the rebuild path.
3. **The projected `sequence` is stored as the row's `version`.** It is not a
   counter this module increments — it is the log position the row reflects. A
   stale writer therefore cannot overwrite a newer row: the guard is
   ``version <= :sequence``, which is a fact about the log rather than an
   assumption about write ordering.

Embeddings are **deliberately absent** from every mapping. They are large binary
columns written by the Phase 9 perception layer directly, not carried through
event payloads — inlining a 512-dimensional vector into an append-only log that
must live for years would multiply its size for data that is regenerable from
the photo.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Final

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.db.models.complaint import Complaint, ComplaintCluster
from nemesis.db.models.event import EventChainHead
from nemesis.db.models.work_order import WorkOrder
from nemesis.domain.lifecycle import EntityType
from nemesis.projections.replay import ReplayResult, replay_entity


class ProjectionWriteError(RuntimeError):
    """A projected state cannot be materialised as written."""


def _point(state: Mapping[str, Any], lat_key: str, lon_key: str) -> Any:
    """WGS84 point literal for a geography column.

    ``ST_MakePoint`` takes **longitude first**. Getting that backwards is the
    classic PostGIS defect: it produces valid geometry at a plausible-looking
    location, so nothing errors and every distance query is silently wrong —
    which for §14's dedup means real duplicate reports stop matching.
    """
    latitude, longitude = state.get(lat_key), state.get(lon_key)
    if latitude is None or longitude is None:
        return None
    return func.ST_SetSRID(func.ST_MakePoint(longitude, latitude), 4326)


def _complaint_values(
    entity_id: uuid.UUID, tenant_id: uuid.UUID, result: ReplayResult
) -> dict[str, Any]:
    state = result.state
    severity = state.get("severity_breakdown")
    return {
        "id": entity_id,
        "tenant_id": tenant_id,
        "status": state["status"],
        "category": state.get("category"),
        "classification_confidence": state.get("classification_confidence"),
        "description_text": state.get("description_text"),
        "photo_url": state.get("photo_url"),
        "audio_url": state.get("audio_url"),
        "location": _point(state, "latitude", "longitude"),
        "exif_location": _point(state, "exif_latitude", "exif_longitude"),
        "reported_at": _as_datetime(state["reported_at"], field="reported_at"),
        "cluster_id": _as_uuid(state.get("cluster_id")),
        "severity_score": state.get("severity_score"),
        "severity_breakdown": severity,
        "severity_policy_version": state.get("severity_policy_version"),
        "ward": state.get("ward"),
        "department_id": _as_uuid(state.get("department_id")),
        "submitter_device_fingerprint": state.get("submitter_device_fingerprint"),
        "is_safety_flagged": state.get("is_safety_flagged", False),
        "is_fraud_flagged": state.get("is_fraud_flagged", False),
        "funding_source": state.get("funding_source"),
        "version": result.sequence,
    }


def _cluster_values(
    entity_id: uuid.UUID, tenant_id: uuid.UUID, result: ReplayResult
) -> dict[str, Any]:
    state = result.state
    return {
        "id": entity_id,
        "tenant_id": tenant_id,
        "centroid": _point(state, "latitude", "longitude"),
        "report_count": state.get("report_count", 1),
        "confidence": state.get("confidence"),
        "first_reported": _as_datetime(state["first_reported"], field="first_reported"),
        "last_reported": _as_datetime(state["last_reported"], field="last_reported"),
        "current_severity": state.get("current_severity"),
        "version": result.sequence,
    }


def _work_order_values(
    entity_id: uuid.UUID, tenant_id: uuid.UUID, result: ReplayResult
) -> dict[str, Any]:
    state = result.state
    return {
        "id": entity_id,
        "tenant_id": tenant_id,
        "complaint_cluster_id": _as_uuid(state["cluster_id"]),
        "department_id": _as_uuid(state.get("department_id")),
        "assigned_to_type": state.get("assignee_type"),
        "assigned_to_id": _as_uuid(state.get("assignee_id")),
        "status": state["status"],
        "sla_deadline": _as_datetime(state.get("sla_deadline"), field="sla_deadline"),
        "sla_policy_version": state.get("sla_policy_version"),
        "ssim_score": state.get("ssim_score"),
        "milestone_stage": state.get("milestone_stage"),
        "version": result.sequence,
    }


#: Entity type -> (model, value builder). A projected entity with no entry here
#: has no current-state table and is log-only — `admin_action` and
#: `system_degradation` are the two, and neither describes a thing with a
#: lifecycle to materialise.
_MATERIALISED: Final[dict[str, tuple[Any, Any]]] = {
    EntityType.COMPLAINT.value: (Complaint, _complaint_values),
    EntityType.COMPLAINT_CLUSTER.value: (ComplaintCluster, _cluster_values),
    EntityType.WORK_ORDER.value: (WorkOrder, _work_order_values),
}


def is_materialised(entity_type: str) -> bool:
    return entity_type in _MATERIALISED


async def write_projection(
    session: AsyncSession, *, tenant_id: uuid.UUID, result: ReplayResult
) -> bool:
    """Upsert one projected entity into its current-state table.

    Returns ``False`` when the entity type has no table (log-only), or when the
    row already reflects a *newer* log position than this projection — the
    stale-writer case, resolved by the database rather than by ordering
    assumptions the caller cannot actually make.
    """
    entry = _MATERIALISED.get(result.entity_type)
    if entry is None:
        return False
    if result.sequence == 0:
        # Nothing was ever appended for this entity. Writing a row here would
        # invent current state for an entity with no history.
        return False

    model, build_values = entry
    try:
        values = build_values(result.entity_id, tenant_id, result)
    except KeyError as exc:
        raise ProjectionWriteError(
            f"projected {result.entity_type} {result.entity_id} is missing {exc}; "
            f"an event that establishes it was never applied"
        ) from exc

    updatable = {key: value for key, value in values.items() if key not in {"id", "tenant_id"}}

    statement = (
        pg_insert(model)
        .values(**values)
        .on_conflict_do_update(
            index_elements=[model.id],
            set_=updatable,
            # The stale-writer guard. Two workers can replay the same entity at
            # different log positions concurrently; without this the one that
            # finishes last wins regardless of which read more history, and the
            # projection silently goes backwards.
            where=model.version <= result.sequence,
        )
        .returning(model.id)
    )
    written = (await session.execute(statement)).scalar_one_or_none()
    return written is not None


async def rebuild_entity(
    session: AsyncSession, *, tenant_id: uuid.UUID, entity_type: str, entity_id: uuid.UUID
) -> bool:
    """Replay one entity from the log and materialise the result.

    Snapshots are bypassed. A rebuild exists because the projection is suspect —
    a projector was fixed, a table was truncated, a row looks wrong — and a
    cached intermediate is exactly what should not be trusted at that moment.
    """
    result = await replay_entity(
        session,
        tenant_id=tenant_id,
        entity_type=entity_type,
        entity_id=entity_id,
        use_snapshots=False,
    )
    return await write_projection(session, tenant_id=tenant_id, result=result)


async def rebuild_tenant(session: AsyncSession, *, tenant_id: uuid.UUID) -> dict[str, int]:
    """Rebuild every materialised entity for one tenant, from the log alone.

    This is the operation the Phase 2 gate is about: truncate the current-state
    tables, run this, and the rows must come back byte-identical. It is also the
    real recovery procedure — a corrupted projection is repairable precisely
    because it was never the source of truth.

    Scoped to one tenant rather than offering a global rebuild. A cross-tenant
    rebuild would hold connections for as long as the largest customer's history
    takes, and there is no operation for which "all tenants at once" is the
    right blast radius.
    """
    rebuilt: dict[str, int] = {}
    for entity_type in sorted(_MATERIALISED):
        heads = (
            (
                await session.execute(
                    select(EventChainHead.entity_id).where(
                        EventChainHead.tenant_id == tenant_id,
                        EventChainHead.entity_type == entity_type,
                        EventChainHead.sequence > 0,
                    )
                )
            )
            .scalars()
            .all()
        )

        written = 0
        for entity_id in heads:
            if await rebuild_entity(
                session, tenant_id=tenant_id, entity_type=entity_type, entity_id=entity_id
            ):
                written += 1
        rebuilt[entity_type] = written

    return rebuilt


def _as_uuid(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _as_datetime(value: Any, *, field: str) -> datetime | None:
    """Parse a projected timestamp back into a ``datetime`` for a ``timestamptz``.

    Projected state stores timestamps as ISO-8601 **strings**, deliberately: a
    projection has to serialise identically on every replay, and a ``datetime``
    renders differently depending on the reader's session timezone. The
    conversion therefore belongs here, at the boundary where the value stops
    being a projection and becomes a column.

    Raises rather than silently dropping an unparseable value. A ``NULL``
    ``sla_deadline`` means "no deadline", which is a very different claim from
    "we could not read the deadline" — and the Phase 12 sweeper would never
    escalate it.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise ProjectionWriteError(f"{field} is not a parseable timestamp: {value!r}") from exc
