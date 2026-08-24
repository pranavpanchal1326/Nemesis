"""The trust spine's three tables — Phase 8.

**Why these are written by a stage rather than by ``projections.writer``.** The
projection writer materialises exactly one row per entity into a fixed table,
keyed on the entity id. Two of the three tables here are one-to-many against a
complaint: a report may carry a photo and an audio clip, and a report may be
queued for review, decided, and queued again for a different reason. Forcing
that shape through a writer built for one row per entity would mean either
losing rows or inventing a synthetic entity type with a chain nobody appends to.

So they are written by the trust stage inside the orchestrator's transaction —
the seam ``StageContext`` already documents ("a provider that writes is writing
inside the same atomic unit as the events it returns"), and the same seam Phase
9 uses for embeddings, which ``projections.writer`` names in its own docstring.

**Two of the three are rebuildable from the log, and the third is not. That is
a deliberate asymmetry, not an omission.** ``trust.rebuild.rebuild_reviews``
reconstructs both review tables from ``review_queued`` and ``review_decided``
alone, and a test asserts the rebuild is byte-identical to what the pipeline
wrote — so §9.1's "current state is derived" holds for the queue exactly as it
holds for ``complaints``.

``submission_media`` cannot be, and the reason is the same one that makes it
worth having. It holds the EXIF coordinates, the capture time and the perceptual
hash — precisely the values §22.4 requires to be **purged after 90 days**. An
append-only hash chain is the one place in this system a value can never be
expired from, so putting them in an event payload would be choosing, permanently
and for every tenant, that the retention schedule cannot be honoured. The events
therefore carry what must be provable forever (that redaction happened, what did
it, how many faces) and this table carries what must be forgettable. It is an
*index over expiring evidence*, and it is documented as one rather than
described as a projection it can never be.

**Why the raw upload's location is recorded here and not only in the event.**
§22.4 retains the raw photo for 30 days and EXIF metadata for 90, then purges
both. A purge needs something to find, and finding it by replaying every
complaint chain in the tenant would make retention cost proportional to history
rather than to what is expiring. ``purge_raw_after`` and ``purge_exif_after``
are indexed for exactly that sweep, which Phase 26 owns and this phase makes
possible.

**Why ``perceptual_hash`` is a signed 64-bit integer and not bytes.** The
comparison the §11.1 check runs is a Hamming distance, and Postgres computes it
in one expression over ``BIGINT``: ``bit_count(a # b)``. Stored as ``bytea`` the
same query needs a function nobody can index or explain, and stored as a string
it needs a decode per row. The sign is an artefact of Postgres having no
unsigned type; ``trust.phash`` converts at the boundary in one place.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from nemesis.db.base import (
    Base,
    TenantScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)

#: Enum values mirrored into CHECK constraints. Mirrored, not imported: these
#: models are read by Alembic's autogenerate in a process that must not pull in
#: the trust package, and a migration whose DDL depended on an application
#: import would change shape the day that import moved. The mirroring is not
#: free-floating — ``test_trust_models.py`` asserts each tuple equals the values
#: of the enum it mirrors, so a member added on one side fails the build rather
#: than producing a constraint that silently rejects a legitimate row.
MEDIA_KINDS = ("image", "audio")
REVIEW_STATUSES = ("open", "decided")
REVIEW_REASONS = (
    "safety_trigger",
    "exif_mismatch",
    "perceptual_duplicate",
    "device_velocity",
    "geographic_cluster",
    # Phase 10. §14.1's ambiguous band, which routes a report the dedup engine
    # will not guess about to a human. Mirrored here because this tuple becomes
    # the CHECK constraint on `review_queue_items` and `review_decisions`.
    "ambiguous_dedup",
    "low_trust",
)
REVIEW_DECISIONS = ("approve", "reject", "escalate")


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class SubmissionMedia(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """One uploaded artefact, and everything the trust spine learned from it.

    The row exists from the moment the redaction stage runs, not from upload:
    before that the file is in quarantine and nothing has read it. A complaint
    whose media never reached the stage therefore has no row, which is
    distinguishable from one whose media was read and found to contain no faces
    — the second has ``faces_detected = 0`` and a ``redacted_uri``.
    """

    __tablename__ = "submission_media"
    __table_args__ = (
        # One row per (complaint, uploaded bytes). The redaction stage is
        # idempotent by the orchestrator's key, but a redelivery that got past
        # it must not be able to double-insert — and the content hash is the
        # only identity the upload actually has.
        UniqueConstraint(
            "tenant_id",
            "complaint_id",
            "quarantine_sha256",
            name="uq_submission_media_complaint_source",
        ),
        # The §11.1 near-duplicate search: same tenant, recent, hash present.
        # Partial, because a row with no hash (audio) is never a candidate and
        # indexing it would grow the index for rows the query excludes.
        Index(
            "ix_submission_media_tenant_hash",
            "tenant_id",
            "captured_or_reported_at",
            postgresql_where="perceptual_hash IS NOT NULL",
        ),
        # The §22.4 retention sweeps. Two indexes rather than one composite:
        # the raw-photo purge and the EXIF purge run on different clocks (30
        # days and 90) and neither filters on the other's column.
        Index(
            "ix_submission_media_purge_raw",
            "purge_raw_after",
            postgresql_where="raw_purged_at IS NULL",
        ),
        Index(
            "ix_submission_media_purge_exif",
            "purge_exif_after",
            postgresql_where="exif_purged_at IS NULL",
        ),
        CheckConstraint(f"kind IN ({_quoted(MEDIA_KINDS)})", name="kind_is_known"),
        CheckConstraint(
            "faces_blurred <= faces_detected",
            name="blurred_does_not_exceed_detected",
        ),
        CheckConstraint("faces_detected >= 0", name="detected_is_not_negative"),
        # §22.4's two clocks, in the order the schedule states them. A row whose
        # EXIF expires before the photograph it describes would leave the raw
        # image with nothing to review it against, which is the worst of both
        # retentions — the same invariant ``MediaRetentionPolicy`` validates one
        # layer up, enforced again here because a policy can be edited and a row
        # that is already wrong cannot be fixed by editing it.
        CheckConstraint("purge_exif_after >= purge_raw_after", name="exif_outlives_raw"),
        # A redacted artefact must name its detector. "We blurred it" with no
        # record of what did the blurring is not evidence, and the §22.1 claim
        # is only auditable if a specific model version can be named.
        CheckConstraint(
            "(redacted_uri IS NULL) = (detector_id IS NULL)",
            name="redaction_names_its_detector",
        ),
    )

    complaint_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("complaints.id", ondelete="RESTRICT", name="fk_submission_media_complaint"),
        nullable=False,
        index=True,
    )

    #: ``image`` or ``audio``. Platform structure, like a pipeline stage: each
    #: value has code that runs for it. Not a tenant vocabulary.
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)

    #: Where the unredacted upload lives. Internal-only, never served — see
    #: ``ingest.media`` on why the scheme is deliberately not ``http``.
    quarantine_uri: Mapped[str] = mapped_column(Text, nullable=False)
    quarantine_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    #: Where the redacted copy lives. ``NULL`` until the stage has run; the
    #: media endpoint resolves nothing else, so a NULL here is an artefact no
    #: HTTP path can reach.
    redacted_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    redacted_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    redacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    faces_detected: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    faces_blurred: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    #: Model identity and version, e.g. ``mediapipe:blaze_face_short_range@1``.
    detector_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    perceptual_hash: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    #: §11.1. Three distinct states, and the schema keeps them distinct:
    #: ``exif_present = false`` (stripped by a share flow — reduces trust),
    #: ``exif_present = true`` with no GPS (camera had it off), and
    #: ``exif_present = true`` with a distance.
    exif_present: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    exif_latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    exif_longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    exif_distance_meters: Mapped[float | None] = mapped_column(Float, nullable=True)
    exif_captured_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    #: EXIF capture time when it exists, the report's own time otherwise. The
    #: near-duplicate window is a claim about when the *photograph* was taken —
    #: a re-upload of last year's picture submitted today is exactly the case
    #: §11.1 wants caught, and ordering on the submission time would put it
    #: inside every window.
    captured_or_reported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    #: §22.4 retention clocks, resolved from the tenant's trust policy at the
    #: moment the artefact was processed. Stored rather than computed on read,
    #: so shortening the retention period does not retroactively purge material
    #: somebody is mid-dispute over.
    purge_raw_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    purge_exif_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exif_purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReviewQueueItem(TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin, Base):
    """§11.4: the destination every "flag for review" in the system has.

    **Open items are unique per (complaint, reason).** A report that trips the
    velocity check twice in an hour is one queue item with a rising
    ``occurrences`` count, not two rows a reviewer has to reconcile. A report
    that trips velocity *and* a phash match is two items, because they are two
    different judgements with two different evidence bundles and a reviewer may
    reasonably accept one and reject the other.

    **``evidence`` is a snapshot, not a pointer.** §11.4 requires the bundle —
    photo, EXIF, similarity scores, reason — to be in front of the reviewer.
    Recomputing it at read time would show the reviewer today's numbers for a
    decision made against last week's thresholds, and Phase 11 would then learn
    from a label attached to evidence that never produced it.
    """

    __tablename__ = "review_queue_items"
    __table_args__ = (
        # Partial and therefore an Index rather than a UniqueConstraint: a
        # *decided* item must not block the same reason being raised again
        # months later on new evidence, and a table constraint cannot say that.
        # Same mechanism as ``uq_policy_versions_one_active_per_kind``.
        Index(
            "uq_review_queue_one_open_per_reason",
            "tenant_id",
            "complaint_id",
            "reason",
            unique=True,
            postgresql_where="status = 'open'",
        ),
        Index(
            "ix_review_queue_tenant_status_priority",
            "tenant_id",
            "status",
            "priority",
            "created_at",
        ),
        CheckConstraint(f"reason IN ({_quoted(REVIEW_REASONS)})", name="reason_is_known"),
        CheckConstraint(f"status IN ({_quoted(REVIEW_STATUSES)})", name="status_is_known"),
        CheckConstraint("occurrences >= 1", name="occurrences_start_at_one"),
        # The two halves of "decided" move together or not at all. Without this
        # a status of 'decided' with a null timestamp is representable, and the
        # partial unique index above — which is what stops a second open item —
        # would then let one through on a row nobody can date.
        CheckConstraint(
            "(status = 'open') = (decided_at IS NULL)",
            name="decided_carries_its_timestamp",
        ),
    )

    complaint_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("complaints.id", ondelete="RESTRICT", name="fk_review_queue_complaint"),
        nullable=False,
        index=True,
    )

    #: Which check raised it. A ``ReviewReason`` value — platform structure,
    #: because each value names code that produced it and an evidence shape a
    #: reviewer is shown.
    reason: Mapped[str] = mapped_column(String(48), nullable=False)
    #: ``open`` | ``decided``. Two states, not four: an item is either waiting
    #: for a human or it has one attached decision. "In progress" is a claim
    #: about a person, which Phase 13 owns along with the person.
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="open")
    #: Lower sorts first. Derived from the reason and the trust delta, not typed
    #: by a human — a queue whose order is an opinion is a queue that gets
    #: reordered instead of worked.
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default="100")
    occurrences: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    evidence: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    #: Trust score at the moment of queueing, so a reviewer sees what the system
    #: thought rather than what it thinks now.
    trust_score: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")

    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReviewDecision(TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin, Base):
    """One human judgement — and, by construction, one Phase 11 training label.

    Architectural principle 4 says every human decision is training data, and
    the plan's critique-log defect #6 is that the previous revision threw the
    feedback loop away. This table is the mechanism, not a metaphor for it: it
    carries the *inputs* the human saw (``evidence_hash``, pointing at the
    frozen bundle) alongside the *outcome* they chose, which is exactly the pair
    a supervised example needs.

    **``evidence_hash`` rather than a second copy of the evidence.** The bundle
    is already frozen on the queue item; duplicating it would create two records
    that can disagree. The hash is what makes "this label was produced against
    that evidence" checkable rather than assumed.

    **One decision per item, enforced.** A queue item that accumulated three
    contradictory decisions would be three labels for one example with no way to
    pick, and Phase 11 would train on the union. Reopening is a new item.
    """

    __tablename__ = "review_decisions"
    __table_args__ = (
        UniqueConstraint("review_item_id", name="uq_review_decisions_one_per_item"),
        Index("ix_review_decisions_tenant_created", "tenant_id", "created_at"),
        Index("ix_review_decisions_tenant_label", "tenant_id", "reason", "decision"),
        CheckConstraint(f"reason IN ({_quoted(REVIEW_REASONS)})", name="reason_is_known"),
        CheckConstraint(f"decision IN ({_quoted(REVIEW_DECISIONS)})", name="decision_is_known"),
        # A label with no rationale is a training example with no explanation of
        # itself, and §11.4 makes this queue an accountability surface. Enforced
        # here as well as in the service because the column is what Phase 11
        # reads, and a NOT NULL that accepts '' is not the constraint it looks like.
        CheckConstraint("length(btrim(rationale)) > 0", name="rationale_is_not_blank"),
    )

    review_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("review_queue_items.id", ondelete="RESTRICT", name="fk_review_decisions_item"),
        nullable=False,
    )
    complaint_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("complaints.id", ondelete="RESTRICT", name="fk_review_decisions_complaint"),
        nullable=False,
        index=True,
    )

    #: Copied from the item so a label is a single row Phase 11 can read without
    #: a join it would otherwise do a million times.
    reason: Mapped[str] = mapped_column(String(48), nullable=False)
    #: ``approve`` | ``reject`` | ``escalate`` — §11.4's three actions.
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Required. §11.4's queue is an accountability surface; a decision with no
    #: stated reason answers "what" and refuses to answer "why", which is the
    #: same objection ``admin_action.justification`` exists to close.
    rationale: Mapped[str] = mapped_column(Text, nullable=False)

    #: Who decided. Nullable until Phase 13 gives operators identities; the
    #: control-plane token is a shared secret and recording it as a person would
    #: be a worse record than recording nothing.
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_review_decisions_user"),
        nullable=True,
    )
    #: Free text naming whoever acted when there is no user row, so an audit is
    #: not left with a null and nothing else.
    decided_by_label: Mapped[str] = mapped_column(String(128), nullable=False)

    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)


__all__ = [
    "MEDIA_KINDS",
    "REVIEW_DECISIONS",
    "REVIEW_REASONS",
    "REVIEW_STATUSES",
    "ReviewDecision",
    "ReviewQueueItem",
    "SubmissionMedia",
]
