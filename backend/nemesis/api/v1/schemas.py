"""Request and response models for the v1 complaint endpoints.

Every field is declared here rather than assembled in the handler, because these
models are the source of the OpenAPI document, which generates the TypeScript
client in Phase 18. A response built as a bare dict types as ``any`` on the
other side of that pipeline, which is how a frontend starts guessing at a shape
the backend already knows.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from nemesis.events.catalog import Latitude, Longitude
from nemesis.events.hashing import format_timestamp


class ComplaintSubmissionResponse(BaseModel):
    """§26.1's 202 body."""

    model_config = ConfigDict(frozen=True)

    complaint_id: uuid.UUID
    status: str
    estimated_processing_time_seconds: int


class SeverityBreakdown(BaseModel):
    """The §13.1 explainability payload, as stored.

    ``components`` and ``weights`` are separate maps rather than one flattened
    object because Phase 12's gate requires a scored complaint to reproduce its
    score from its own breakdown — which needs both the inputs and what each was
    multiplied by.
    """

    model_config = ConfigDict(frozen=True)

    components: dict[str, float] = Field(default_factory=dict)
    weights: dict[str, float] = Field(default_factory=dict)


class ComplaintResponse(BaseModel):
    """§26.2's 200 body, plus the fields the blueprint's sketch omits."""

    model_config = ConfigDict(frozen=True)

    complaint_id: uuid.UUID
    status: str
    category: str | None = None
    classification_confidence: float | None = None
    cluster_id: uuid.UUID | None = None
    severity_score: float | None = None
    severity_breakdown: SeverityBreakdown | None = None
    severity_policy_version: str | None = None
    work_order_id: uuid.UUID | None = None

    latitude: Latitude | None = None
    longitude: Longitude | None = None
    reported_at: str | None = None

    #: The log position this representation reflects, and the value the ETag is
    #: derived from. Exposed rather than hidden inside the header: a client
    #: polling §27.3's 5-second fallback can tell whether anything moved without
    #: diffing the whole body.
    version: int = 0

    #: §24.2. Present only when a stage took its fallback path, so a client can
    #: distinguish "still processing" from "parked, waiting for a human" —
    #: which look identical if the status is the only signal.
    degraded_stage: str | None = None
    degraded_fallback: str | None = None

    #: Deliberately absent from this model, and each for a stated reason:
    #: `description_text` and the media URIs are the citizen's own submission
    #: and are returned to nobody until Phase 13 can say who is asking;
    #: `submitter_device_fingerprint` is §11.3 abuse-detection data that §22
    #: forbids leaving the system at all.

    @classmethod
    def from_row(cls, row: Any) -> ComplaintResponse:
        """Build from a ``complaints`` row, never from a replay.

        The projection *is* the read model (§9.1). An earlier version of the
        handler read the row for its version and then replayed the whole chain
        to fill the body — which is the exact cost this table exists to avoid,
        paid on the endpoint §27.3 turns into a 5-second poll per client.

        ``reported_at`` goes through ``format_timestamp`` so a value read back
        from Postgres renders identically to the one the projector produced,
        whatever timezone the reading session happens to be in.
        """
        breakdown = row.severity_breakdown
        return cls(
            complaint_id=row.id,
            status=str(row.status),
            category=row.category,
            classification_confidence=row.classification_confidence,
            cluster_id=_as_uuid(row.cluster_id),
            severity_score=row.severity_score,
            severity_breakdown=(
                SeverityBreakdown.model_validate(breakdown) if isinstance(breakdown, dict) else None
            ),
            severity_policy_version=row.severity_policy_version,
            work_order_id=_as_uuid(row.work_order_id),
            latitude=row.latitude,
            longitude=row.longitude,
            reported_at=None if row.reported_at is None else format_timestamp(row.reported_at),
            version=int(row.version),
            degraded_stage=row.degraded_stage,
            degraded_fallback=row.degraded_fallback,
        )


def _as_uuid(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:  # pragma: no cover — projections store uuid strings
        return None
