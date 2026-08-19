"""Versioned, effective-dated policy documents — the Phase 6 table.

**One table, not one per kind.** A severity rubric and a rate card share every
structural property that matters here: they are tenant-scoped, they version
monotonically, they move through the same lifecycle, and they are hashed into
the same chain. What differs is the *body*, which is JSONB validated by the
Pydantic model ``policy.documents`` registers for the kind. Six tables would be
six copies of the lifecycle columns, six migrations the next time the lifecycle
gains a state, and six places for the "at most one active" rule to be enforced
slightly differently.

**Versions are rows, never updates.** ``policy_versions`` is append-mostly: a
new revision is a new row, activation flips statuses, and nothing rewrites a
body that has been live. That is what makes ``severity_scored.policy_version``
resolvable years later, and it is the same reasoning the event store applies to
events — a record that can be edited is a record that cannot be evidence (§6.1).

**Rollback is forward.** Restoring version 3 does not resurrect the version 3
row; it creates version 8 with version 3's body and ``rolled_back_from``
pointing at it. The version sequence therefore only ever increases, which means
"which policy was live on 14 March" is answered by an interval query rather than
by reasoning about a row that was live, then was not, then was again.

**The one active-per-kind rule is a database constraint**, not a service check.
Phase 6's gate says an unapproved draft can never influence a production
decision; the resolver enforces that by reading only ``status = 'active'``, and
this index is what makes "only ``active``" also mean "exactly one". A service
check would hold until two operators pressed Activate at the same moment.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
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
from nemesis.domain.constants import HASH_HEX_LENGTH

#: The lifecycle, as the database understands it. Restated here rather than
#: imported from ``policy.documents`` for the reason ``domain.constants`` exists:
#: models must not import the service layer, or the dependency graph acquires a
#: cycle that surfaces as a partial-initialisation error three modules away. A
#: test asserts the two agree, so the duplication cannot drift silently.
POLICY_STATUSES: tuple[str, ...] = (
    "draft",
    "in_review",
    "approved",
    "active",
    "superseded",
    "archived",
)

#: The governed structures. Same duplication, same test, same reason.
POLICY_KINDS: tuple[str, ...] = (
    "severity_rubric",
    "dedup_thresholds",
    "safety_ruleset",
    "sla_matrix",
    "routing_rules",
    "rate_card",
    # Phase 8. Adding a kind means altering four CHECK constraints across two
    # tables — see the Phase 8 migration. That cost is the constraint doing its
    # job: it is what stopped a seventh governed structure from reaching the
    # table before anyone had decided it was one.
    "trust_thresholds",
    # Phase 9. Same cost, paid again deliberately: the perception layer's
    # per-category temperatures and abstain floors are measured numbers that
    # change as labelled data accumulates, which is precisely the profile of a
    # thing that must be approved rather than deployed.
    "perception_calibration",
)


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class PolicyVersion(
    UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, OptimisticVersionMixin, Base
):
    """One revision of one governed structure, for one tenant."""

    __tablename__ = "policy_versions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "kind", "revision", name="uq_policy_versions_tenant_id_kind_revision"
        ),
        # The gate clause, as an index. Partial on ``status = 'active'`` so the
        # table can hold any number of drafts, approvals, and superseded rows
        # per kind while the live one stays unique — which a plain unique
        # constraint over (tenant, kind) could not express.
        Index(
            "uq_policy_versions_one_active_per_kind",
            "tenant_id",
            "kind",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        # The resolver's read: newest active document for a kind. Ordered by
        # revision because effective dating resolves ties by recency and a scan
        # here is on the hottest path in the scoring pipeline.
        Index("ix_policy_versions_tenant_id_kind_status", "tenant_id", "kind", "status"),
        Index("ix_policy_versions_tenant_id_content_hash", "tenant_id", "content_hash"),
        CheckConstraint(f"kind IN ({_quoted(POLICY_KINDS)})", name="kind_is_known"),
        CheckConstraint(f"status IN ({_quoted(POLICY_STATUSES)})", name="status_is_known"),
        CheckConstraint("revision >= 1", name="revision_starts_at_one"),
        CheckConstraint(
            "effective_until IS NULL OR effective_until > effective_from",
            name="effective_window_is_ordered",
        ),
        # A document that reached ``active`` must carry who approved it. The
        # check is on the row rather than in the service because the approval is
        # the control the whole phase rests on: "who signed off on the rubric
        # that scored this complaint" has to be answerable from the row, and a
        # service-only check leaves a psql session able to activate anonymously.
        CheckConstraint(
            "status NOT IN ('active', 'superseded') OR approved_at IS NOT NULL",
            name="live_versions_were_approved",
        ),
    )

    #: A ``PolicyKind`` value. Constrained by CHECK rather than by a Postgres
    #: ENUM type: adding a seventh kind should be a migration that alters a
    #: constraint, not one that mutates a type other tables might come to share.
    kind: Mapped[str] = mapped_column(String(64), nullable=False)

    #: Monotonic per (tenant, kind), starting at 1. What
    #: ``severity_scored.policy_version`` and friends record, rendered as
    #: ``"<kind>@<revision>"`` by the service — a bare integer in the log would
    #: be ambiguous the moment a second kind stamps a decision.
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False)

    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="draft")

    #: The document. Validated against the kind's Pydantic model on every write
    #: and again on activation — see ``policy.documents``.
    body: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, server_default="{}")

    #: SHA-256 over the canonical JSON of ``body``. Two purposes, both real:
    #: it is what the event carries, so an auditor can confirm the document in
    #: the table is the one the chain recorded; and it makes "is this draft
    #: actually different from what is live" a comparison rather than a diff.
    content_hash: Mapped[str] = mapped_column(String(HASH_HEX_LENGTH), nullable=False)

    #: When this version starts applying. Set at activation, not at drafting: a
    #: draft has no effective date because it has no effect. Future-dating is
    #: permitted — a rate card negotiated in March to apply from April is the
    #: motivating case — and the resolver honours it.
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    #: Closed when a later version supersedes this one. Null while live. The
    #: pair is what makes "which policy was live on 14 March" an interval query.
    effective_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    #: What this was drafted from. Null for the first version of a kind and for
    #: a document authored from scratch. Kept so a revision chain is walkable
    #: without reconstructing it from revision numbers, which stop being
    #: contiguous as soon as a draft is archived.
    based_on_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        # RESTRICT: the ancestry of a live policy is part of its evidence trail.
        # Deleting the draft a live rubric was derived from would remove the
        # answer to "what changed", which is the first question asked when a
        # score is disputed.
        ForeignKey("policy_versions.id", ondelete="RESTRICT", name="fk_policy_versions_based_on"),
        nullable=True,
    )

    #: Set when this version exists because an operator restored an older one.
    #: Distinct from ``based_on_id`` because a rollback and an edit are
    #: operationally different events, and an incident review needs to tell them
    #: apart at a glance.
    rolled_back_from_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "policy_versions.id", ondelete="RESTRICT", name="fk_policy_versions_rolled_back_from"
        ),
        nullable=True,
    )

    #: Why this revision exists, in the author's words. Required at draft time
    #: by the service. An audit trail that answers "what changed" and refuses to
    #: answer "why" is the same half-record ``admin_action.justification``
    #: exists to prevent.
    change_reason: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    #: Free-text summary shown in the review queue. Optional — the reason is
    #: mandatory, a summary is a courtesy.
    change_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Actors. Nullable UUIDs with no foreign key to ``users``, deliberately:
    #: Phase 13 owns identity, the control-plane token carries no operator
    #: claim, and a foreign key to a table that cannot yet be populated would
    #: make every write from the current API fail. The column exists now so the
    #: history Phase 13 backfills into has somewhere to go.
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    activated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    #: Why a review was rejected. Present only on archived documents that were
    #: rejected, absent on ones that were simply abandoned — the difference
    #: matters to the author, who otherwise cannot tell whether anyone looked.
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    @property
    def stamp(self) -> str:
        """The identifier written into every decision this version influenced.

        ``"<kind>@<revision>"`` rather than a UUID. The UUID is unambiguous and
        unreadable; this is what an operator quotes during an incident, appears
        in ``severity_scored.policy_version``, and can be resolved back to
        exactly one row by the unique constraint on (tenant, kind, revision).
        """
        return f"{self.kind}@{self.revision}"
