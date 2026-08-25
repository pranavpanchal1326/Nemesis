"""Turn a validated submission into history.

One function does the writing, and it does everything in a single transaction:
the ``complaint_submitted`` event, the projection into ``complaints``, and the
outbox row. §9.1 requires the first two to commit together; the outbox row joins
them because a realtime notification for a submission that rolled back is a
claim about the world the database never agreed to.

**Dispatch happens after the commit, and the ordering is the whole point.** A
Celery task enqueued inside the transaction can be picked up by a worker before
the commit lands, at which point the worker replays a complaint that does not
exist yet. The task would fail, retry, and eventually succeed — so the bug is
invisible in development and shows up as unexplained retries under load.

**Idempotency is the caller's key, not a guess about the payload.** §26.1 has no
natural key: two citizens photographing the same pothole from the same corner
within a second are two genuine reports, not a duplicate submission — that is
what §14's dedup is *for*. So a repeated submission is recognised only when the
client says it is one, via ``Idempotency-Key``, and the answer is the original
complaint id rather than a second complaint.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.db.session import session_scope
from nemesis.domain.lifecycle import EntityType
from nemesis.events.store import EventStore
from nemesis.observability import metrics
from nemesis.observability.logging import get_logger
from nemesis.outbox import writer as outbox
from nemesis.projections.replay import replay_entity
from nemesis.projections.writer import write_projection
from nemesis.tenancy.context import tenant_scope

log = get_logger(__name__)

#: Namespace for a client-supplied idempotency key. Prefixed so a citizen
#: cannot craft a key that collides with the ``pipeline:`` keys the orchestrator
#: mints and thereby suppress a stage on somebody else's complaint.
SUBMISSION_KEY_PREFIX: Final = "submit"

COMPLAINT: Final = EntityType.COMPLAINT.value


@dataclass(frozen=True, slots=True)
class Submission:
    """A validated §26.1 submission, ready to be recorded."""

    latitude: float
    longitude: float
    description_text: str | None = None
    photo_uri: str | None = None
    audio_uri: str | None = None
    locale: str | None = None
    device_fingerprint: str | None = None
    submitted_via: str = "web"


@dataclass(frozen=True, slots=True)
class SubmissionReceipt:
    complaint_id: uuid.UUID
    status: str
    #: True when an idempotency key matched an earlier submission and this call
    #: recorded nothing. Returned rather than hidden: the caller decides whether
    #: that means 202 or 200, and a notification path needs to know not to fire
    #: twice.
    duplicate: bool
    #: The complaint's chain head after this append — ADR-0044, §E17.3.
    #:
    #: At this instant it is also ``complaint_submitted``'s own ``event_hash``,
    #: because the chain is exactly one event long. That coincidence is why the
    #: receipt can carry the *head* without a second query: the head lock was
    #: already taken, the value was already computed, and re-reading
    #: ``event_chain_heads`` a microsecond later would be a round trip to
    #: recover a value this function had in hand.
    #:
    #: On a redelivery it is the head as it stood when the *original* submission
    #: landed, not as it stands now. That is the correct value and not a
    #: limitation: the receipt attests the record that was created, and a client
    #: retrying after a timeout must be handed the same receipt it would have
    #: received the first time or the retry has produced a different document.
    chain_hash: str


async def submit(
    *,
    tenant_id: uuid.UUID,
    submission: Submission,
    correlation_id: str | None = None,
    idempotency_key: str | None = None,
    occurred_at: datetime | None = None,
) -> SubmissionReceipt:
    """Record a submission. Returns the complaint id, new or existing."""
    complaint_id = uuid.uuid4()
    key = None if idempotency_key is None else f"{SUBMISSION_KEY_PREFIX}:{idempotency_key}"

    with tenant_scope(tenant_id):
        async with session_scope() as session:
            event = await EventStore(session).append(
                entity_id=complaint_id,
                event_type="complaint_submitted",
                payload={
                    "latitude": submission.latitude,
                    "longitude": submission.longitude,
                    "description_text": submission.description_text,
                    "photo_url": submission.photo_uri,
                    "audio_url": submission.audio_uri,
                    "locale": submission.locale,
                    "device_fingerprint": submission.device_fingerprint,
                    "submitted_via": submission.submitted_via,
                },
                tenant_id=tenant_id,
                correlation_id=correlation_id,
                idempotency_key=key,
                occurred_at=occurred_at or datetime.now(tz=UTC),
            )

            if event.was_redelivery:
                # The key matched an earlier submission, so the entity id minted
                # above is discarded and the *original* complaint is returned.
                # Returning the new id would hand the client a handle to a
                # complaint that does not exist.
                metrics.ingest_submissions_total.labels(outcome="duplicate").inc()
                return SubmissionReceipt(
                    complaint_id=event.entity_id,
                    status="submitted",
                    duplicate=True,
                    chain_hash=event.event_hash,
                )

            await _materialise(session, tenant_id=tenant_id, complaint_id=complaint_id)
            await outbox.enqueue(session, event)

    metrics.pipeline_events_total.labels(event_type="complaint_submitted").inc()
    metrics.ingest_submissions_total.labels(outcome="accepted").inc()
    log.info(
        "complaint_submitted",
        complaint_id=str(complaint_id),
        has_photo=submission.photo_uri is not None,
        has_audio=submission.audio_uri is not None,
        submitted_via=submission.submitted_via,
    )
    return SubmissionReceipt(
        complaint_id=complaint_id,
        status="submitted",
        duplicate=False,
        chain_hash=event.event_hash,
    )


async def _materialise(
    session: AsyncSession, *, tenant_id: uuid.UUID, complaint_id: uuid.UUID
) -> None:
    projection = await replay_entity(
        session, tenant_id=tenant_id, entity_type=COMPLAINT, entity_id=complaint_id
    )
    await write_projection(session, tenant_id=tenant_id, result=projection)
