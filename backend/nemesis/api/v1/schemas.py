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
    """§26.1's 202 body, and §E17.3's receipt."""

    model_config = ConfigDict(frozen=True)

    complaint_id: uuid.UUID
    status: str
    estimated_processing_time_seconds: int

    #: The complaint's hash-chain head at the moment it was recorded — ADR-0044.
    #:
    #: §E17.3 asks the receipt to carry *"the complaint id and chain hash"*, and
    #: says why the hash matters: *"Nobody reads the hash. Everybody feels that
    #: this system keeps records."* The feeling is the product; this is what
    #: makes the feeling true.
    #:
    #: **It is on this response and deliberately not on the polled read.** The
    #: head advances on every event, including ones that leave the projection
    #: untouched, while ``version`` — which the ETag is derived from — advances
    #: only when the projection changes. A hash served under that validator
    #: would be stale behind a 304, and a stale hash on a document whose claim is
    #: *"this record cannot be edited"* is worse than no hash at all. The live
    #: head is read from ``GET /complaints/{id}/events``, which has no cache.
    chain_hash: str


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

    #: How many reports this incident now holds, and when the first of them
    #: arrived — §E17.2's payoff, which is the one sentence in the citizen
    #: product that converts a solitary act into a collective one:
    #:
    #:     **You're the 4th person to report this.** First reported 6 days ago.
    #:
    #: Read from the `complaint_clusters` projection rather than from the
    #: stream. `cluster_match_found` carries `report_count` on the *cluster's*
    #: chain, and §E14.3's rule is that the socket is a hint — *"nothing renders
    #: a fact from it"*. A sentence a citizen is asked to believe is a fact, so
    #: it comes from the read path.
    #:
    #: Null until Phase 10's dedup stage has clustered the report, which is the
    #: honest state for the first few seconds of its life and the permanent
    #: state for a report nothing matched.
    cluster_report_count: int | None = None
    cluster_first_reported: str | None = None

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
            cluster_report_count=row.cluster_report_count,
            cluster_first_reported=(
                None
                if row.cluster_first_reported is None
                else format_timestamp(row.cluster_first_reported)
            ),
            latitude=row.latitude,
            longitude=row.longitude,
            reported_at=None if row.reported_at is None else format_timestamp(row.reported_at),
            version=int(row.version),
            degraded_stage=row.degraded_stage,
            degraded_fallback=row.degraded_fallback,
        )


class ComplaintHistoryEvent(BaseModel):
    """One row of §E17.4's ledger — ADR-0043.

    **Every event on the chain appears here**, in sequence, whatever its type.
    ``payload`` is shaped by ``nemesis.events.disclosure`` and is very often
    ``{}``; the row is still a row. A history that hid the entries it could not
    fully disclose would leave holes that are either invisible — in which case a
    removed event and a suppressed one look identical, and §E17.3's *"this record
    cannot be edited"* is unverifiable — or visible, in which case they announce
    themselves anyway.
    """

    model_config = ConfigDict(frozen=True)

    #: 1-based position on this complaint's chain. Contiguous by construction:
    #: a gap here means an event is missing, which is exactly what an
    #: append-only log is supposed to make impossible to hide.
    sequence: int
    event_type: str
    #: Business time — when the thing happened, not when the row was written.
    occurred_at: str
    #: The link from the previous event. ``GENESIS_HASH`` on sequence 1.
    previous_hash: str
    #: This event's link. The next row's ``previous_hash``, and — on the last
    #: row — the value ``ComplaintHistory.chain_head`` repeats.
    event_hash: str
    #: The disclosed subset, per ``events/disclosure.py``. ``{}`` means the type
    #: has no declared citizen-facing shape, or has one that discloses nothing.
    payload: dict[str, Any] = Field(default_factory=dict)
    #: False when the shaper returned nothing for a type that *has* stored
    #: fields — so a reader can tell "this event carries no data" apart from
    #: "this event carries data you are not being shown". §E3.3: the omission is
    #: visible rather than faked.
    payload_disclosed: bool = True


class ComplaintHistory(BaseModel):
    """§E17.4's ledger and §E17.3's live chain head — ADR-0043, ADR-0044.

    Returned uncached, deliberately. This is the one representation whose
    correctness depends on being current: ``chain_head`` is what a reader checks
    their receipt against, and a head served from a cache is a head that may
    already have moved.
    """

    model_config = ConfigDict(frozen=True)

    complaint_id: uuid.UUID
    #: The entity's chain head **now**. Equal to the last returned event's
    #: ``event_hash`` when the whole history fits in one page, and to something
    #: later when it does not — which is why it is returned separately rather
    #: than inferred from the tail.
    chain_head: str
    chain_head_sequence: int
    events: list[ComplaintHistoryEvent]
    #: Total events on the chain, so a paging client knows what it has not read.
    total: int
    limit: int
    offset: int


def _as_uuid(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:  # pragma: no cover — projections store uuid strings
        return None
