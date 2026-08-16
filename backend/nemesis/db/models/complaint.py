"""Complaints and clusters — the projection the whole pipeline converges on.

Both tables are **materialised current state derived from the event log**
(§9.1), never a directly-edited record. Every column here is written by a
projector applying an event, which is why they carry an optimistic version
counter and why nothing in the application is permitted to ``UPDATE`` them
outside that path.

Embedding column types are chosen, not defaulted:

``image_embedding halfvec(512)``
    CLIP ViT-B-32 output. ``halfvec`` stores IEEE half precision — half the
    storage and half the index memory of ``vector``, on a value whose useful
    signal is a cosine direction. The measured cost is in
    ``docs/reports/hnsw-recall.md``; the benefit is that the HNSW graph for a
    city's worth of complaints stays in memory on a 16 GB laptop shared with
    Ollama.
``text_embedding vector(384)``
    multilingual-e5-small. Left at full precision because it is small enough
    that halving it buys little, and because text similarity is the signal that
    disambiguates near-identical *photos* of different problems.

Deviations from §9.2, each deliberate:

- ``exif_location`` is ``geography(POINT, 4326)`` like ``location``, but
  nullable and *never* backfilled from ``location``. §11.1 requires absent EXIF
  to reduce trust rather than reject the report, which is only possible if
  "absent" stays distinguishable from "same as reported".
- ``category`` is text with no constraint, resolved against the tenant's
  taxonomy in Phase 5. §9.2's five-value comment is an example, not a schema.
- ``severity_breakdown`` is joined by ``severity_policy_version``, because §13.3
  promises the rubric improves over time and Phase 12's gate requires a scored
  complaint to reproduce its score from its own logged breakdown — impossible
  if the rubric that produced it cannot be identified.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from geoalchemy2 import Geography
from pgvector.sqlalchemy import HALFVEC, Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from nemesis.db.base import (
    Base,
    OptimisticVersionMixin,
    TenantScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from nemesis.domain.lifecycle import ComplaintStatus

#: Dimensions are asserted against ``ModelSettings`` by a test rather than
#: shared as an import: the model config is what the inference layer loads, and
#: a silent disagreement between the two produces a column that accepts vectors
#: the index can never match. Failing that assertion is a loud, early signal.
IMAGE_EMBEDDING_DIM = 512
TEXT_EMBEDDING_DIM = 384


class ComplaintCluster(
    UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, OptimisticVersionMixin, Base
):
    """A deduplicated incident — one real-world problem, many citizen reports."""

    __tablename__ = "complaint_clusters"
    __table_args__ = (
        # The Phase 10 stage-1 filter is `ST_DWithin` over this centroid, and it
        # must eliminate >= 90% of candidates before any embedding comparison.
        # A GiST index on geography is what makes that a bounded index scan
        # rather than a sequential scan of every open incident in the city.
        Index(
            "ix_complaint_clusters_centroid",
            "centroid",
            postgresql_using="gist",
        ),
        Index("ix_complaint_clusters_tenant_id_last_reported", "tenant_id", "last_reported"),
    )

    centroid: Mapped[object] = mapped_column(
        # GeoAlchemy2 would otherwise emit its own `idx_<table>_<column>` GiST
        # index alongside the one declared in __table_args__ — two identical
        # indexes on the hottest geospatial column in the system, doubling write
        # cost for nothing, and both invisible in the model where a reader
        # looks.
        Geography(geometry_type="POINT", srid=4326, spatial_index=False),
        nullable=False,
    )
    report_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    first_reported: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_reported: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_severity: Mapped[float | None] = mapped_column(Float, nullable=True)

    #: Set when a merge is undone. §14 requires merge reversibility via a
    #: compensating event rather than by mutating history, so a superseded
    #: cluster is retired and pointed at its replacement — it is never deleted,
    #: because events referencing it must stay resolvable forever.
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("complaint_clusters.id", ondelete="RESTRICT", name="fk_clusters_superseded_by"),
        nullable=True,
    )


class Complaint(
    UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, OptimisticVersionMixin, Base
):
    __tablename__ = "complaints"
    __table_args__ = (
        Index("ix_complaints_location", "location", postgresql_using="gist"),
        Index("ix_complaints_tenant_id_status", "tenant_id", "status"),
        Index("ix_complaints_tenant_id_reported_at", "tenant_id", "reported_at"),
        Index("ix_complaints_tenant_id_cluster_id", "tenant_id", "cluster_id"),
        # HNSW parameters come from the measured recall curve in
        # docs/reports/hnsw-recall.md, not from pgvector's defaults. Cosine
        # opclasses because both encoders produce direction-carrying vectors
        # whose magnitude is not meaningful.
        Index(
            "ix_complaints_image_embedding_hnsw",
            "image_embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 128},
            postgresql_ops={"image_embedding": "halfvec_cosine_ops"},
        ),
        Index(
            "ix_complaints_text_embedding_hnsw",
            "text_embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 128},
            postgresql_ops={"text_embedding": "vector_cosine_ops"},
        ),
    )

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=ComplaintStatus.SUBMITTED.value
    )

    #: A tenant taxonomy node key. No constraint, no enum — see the module
    #: docstring and Phase 5.
    category: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    classification_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    description_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    photo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    audio_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    location: Mapped[object] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False), nullable=False
    )
    #: No spatial index, deliberately. Nothing searches *by* EXIF position —
    #: §11.1 compares it against `location` on a row already in hand. An index
    #: here would cost every write and serve no query.
    exif_location: Mapped[object | None] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False), nullable=True
    )
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    text_embedding: Mapped[object | None] = mapped_column(Vector(TEXT_EMBEDDING_DIM), nullable=True)
    image_embedding: Mapped[object | None] = mapped_column(
        HALFVEC(IMAGE_EMBEDDING_DIM), nullable=True
    )

    cluster_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("complaint_clusters.id", ondelete="RESTRICT", name="fk_complaints_cluster"),
        nullable=True,
    )

    severity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    severity_breakdown: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    #: Which rubric produced ``severity_breakdown``. Phase 6 makes this a policy
    #: document id; until then it records the §13.5 rubric version string.
    severity_policy_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    ward: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("departments.id", ondelete="RESTRICT", name="fk_complaints_department"),
        nullable=True,
    )

    #: Not a citizen identifier. §11.3 clusters submissions by device to detect
    #: coordinated abuse; §22 forbids this leaving the system, so Phase 4's
    #: public API scrubs it and Phase 17's serialiser test proves it.
    submitter_device_fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)

    is_safety_flagged: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_fraud_flagged: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    #: Scheme or fund tag for the §17.6 ward budget view. Tenant data, so text.
    funding_source: Mapped[str | None] = mapped_column(String(128), nullable=True)
