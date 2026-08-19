"""Simulation, evaluation, and the certificate that gates an activation — Phase 7.

Four tables, and the interesting one is ``policy_certificates``, because it is
what makes the phase's second gate clause enforceable at all.

**Why the guardrail is a row rather than a call.** Phase 7's gate says *a policy
that regresses the labelled evaluation set cannot be activated*. "Cannot" has to
hold in ``policy.service.activate``, which is the single mutation path — but the
policy package must not import the simulation package. A service that called out
to a checker would also be a service whose guarantee depended on that import
still being wired up, and the failure mode of a missing wire is silent: every
activation succeeds, exactly as it did before the guardrail existed.

So the evidence is *data*. Simulation writes a certificate; policy reads a
table. The dependency runs one way, there is nothing to wire, and "was this
candidate checked" is a query anybody can run — including an auditor with a psql
session and no Python.

**Why the certificate is keyed by content hash, not by revision.** A revision
number identifies a row whose body was frozen at approval; a content hash
identifies the *bytes*. Keying on the revision would let a certificate issued
against revision 8 survive an edit to revision 8 — which the lifecycle forbids
today, and which a future "clone this draft" convenience could reintroduce
without anyone noticing that it also reintroduced a way to activate uncertified
content. The hash cannot be wrong about what was tested.

**Why an evaluation set has a partial unique index on published.** Same
reasoning as ``policy_versions``'s one-active-per-kind: the guardrail asks "is
there a published set for this kind", and two of them would make the answer
depend on which row sorted first. Publishing is the act that turns the guardrail
on for a kind, which is why there is no separate "require certification" flag —
a flag would be a second source of truth about the same fact, and the two would
disagree the first time somebody retired a set without clearing it.

**Why labels are frozen at publication.** A guardrail whose labels can be edited
after certificates were issued against them is a guardrail that can be made to
pass by editing the exam. ``labels_hash`` is recorded on both the set and every
certificate, and activation compares them — so a set that somehow changed
invalidates its certificates rather than silently vouching for them.
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
from nemesis.db.models.policy import POLICY_KINDS
from nemesis.domain.constants import HASH_HEX_LENGTH

#: An evaluation set's lifecycle. Shorter than a policy's on purpose: a set is
#: not a decision about how citizens' reports are handled, it is the exam those
#: decisions are marked against, and interposing a review step between writing
#: labels and using them would mostly stop people writing labels.
EVALUATION_STATUSES: tuple[str, ...] = ("draft", "published", "retired")

#: What a certificate concluded. Two values, and no "warning": a guardrail with
#: a middle state is a guardrail somebody has to decide about under pressure,
#: which is the same as not having one.
CERTIFICATE_VERDICTS: tuple[str, ...] = ("pass", "fail")

#: What a run was for. ``backtest`` quantifies impact over history; ``evaluation``
#: marks a candidate against labels and issues a certificate. The same machinery
#: with different outputs, and separated here because "how many runs did we do"
#: and "how many certifications did we do" are different operational questions.
RUN_MODES: tuple[str, ...] = ("backtest", "evaluation")

RUN_STATUSES: tuple[str, ...] = ("running", "completed", "failed")


def _quoted(values: tuple[str, ...]) -> str:
    """Render a tuple as a SQL value list for a CHECK constraint.

    Copied rather than imported from ``db.models.policy``, where the same three
    lines live. Reaching across for a private helper would make one model module
    depend on another's internals for a string join, and the shared version of
    this belongs in ``db.base`` on the day a third table needs it — not as an
    import that quietly couples two schemas.
    """
    return ", ".join(f"'{value}'" for value in values)


class SimulationRun(
    UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, OptimisticVersionMixin, Base
):
    """One replay of a candidate policy over a window of history."""

    __tablename__ = "simulation_runs"
    __table_args__ = (
        Index("ix_simulation_runs_tenant_id_kind_created", "tenant_id", "kind", "created_at"),
        Index("ix_simulation_runs_tenant_id_candidate_hash", "tenant_id", "candidate_content_hash"),
        CheckConstraint(f"kind IN ({_quoted(POLICY_KINDS)})", name="kind_is_known"),
        CheckConstraint(f"mode IN ({_quoted(RUN_MODES)})", name="mode_is_known"),
        CheckConstraint(f"status IN ({_quoted(RUN_STATUSES)})", name="status_is_known"),
        CheckConstraint("window_end > window_start", name="window_is_ordered"),
        CheckConstraint("case_count >= 0", name="case_count_is_not_negative"),
        CheckConstraint("sampling_stride >= 1", name="stride_starts_at_one"),
        # A completed run must carry its report. A row that says "completed" and
        # holds nothing is the worst of both: it satisfies "has this been
        # backtested" and answers nothing about what the backtest found.
        CheckConstraint(
            "status <> 'completed' OR report IS NOT NULL", name="completed_runs_have_a_report"
        ),
    )

    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False, server_default="backtest")
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="running")

    #: The revision under test. Nullable because a run may compare the live
    #: bundle against itself as a reproducibility check — which is how the gate
    #: proves the engine is deterministic against real data rather than against
    #: a fixture.
    candidate_revision: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    #: The bytes under test. What a certificate is keyed by, and what makes a
    #: run findable from an activation attempt six weeks later.
    candidate_content_hash: Mapped[str] = mapped_column(String(HASH_HEX_LENGTH), nullable=False)

    #: ``"<kind>@<revision>"`` for every kind in the baseline bundle, as JSON.
    #: The whole bundle, not just the kind under test: a report that names only
    #: the rubric it changed cannot be reproduced, because the SLA matrix that
    #: turned its scores into tiers has moved on since.
    baseline_stamps: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )

    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    case_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    #: Complaints in the window before sampling. Stored beside ``case_count``
    #: because "12,000 cases" and "12,000 of 480,000" are different claims and
    #: only one of them is what the report actually measured.
    population: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    sampling_stride: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    #: How many cases the candidate would have decided differently. Denormalised
    #: out of ``report`` so the run list can be sorted and filtered without
    #: unpacking a JSONB document per row.
    affected: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    #: The full ``ImpactReport``. JSONB rather than a set of columns for the
    #: reason ``policy_versions.body`` is JSONB: the shape is owned by a
    #: validated Python model, and a report that gained a field would otherwise
    #: be a migration.
    report: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)

    #: Why the run failed, when it did. A failed run is kept rather than deleted:
    #: "we tried to backtest this and could not" is exactly the record an
    #: incident review wants, and a table that only holds successes implies a
    #: diligence that did not happen.
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class EvaluationSet(
    UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, OptimisticVersionMixin, Base
):
    """A labelled regression set for one policy kind."""

    __tablename__ = "evaluation_sets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_evaluation_sets_tenant_id_code"),
        # One published set per kind. The guardrail asks "is there a published
        # set for this kind"; two of them would make the answer depend on which
        # row sorted first, which is not a property to leave to a query plan.
        Index(
            "uq_evaluation_sets_one_published_per_kind",
            "tenant_id",
            "kind",
            unique=True,
            postgresql_where=text("status = 'published'"),
        ),
        CheckConstraint(f"kind IN ({_quoted(POLICY_KINDS)})", name="kind_is_known"),
        CheckConstraint(f"status IN ({_quoted(EVALUATION_STATUSES)})", name="status_is_known"),
        # A published set with no labels would gate every activation on an exam
        # with no questions — which every candidate passes, so the guardrail
        # would read as on and behave as off.
        CheckConstraint(
            "status <> 'published' OR label_count > 0", name="published_sets_have_labels"
        ),
        CheckConstraint(
            "status <> 'published' OR labels_hash IS NOT NULL",
            name="published_sets_are_hashed",
        ),
    )

    #: Operator-chosen, stable, quoted in an incident. Same reasoning as a
    #: department code or a taxonomy key.
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="draft")

    #: What this set is for, in the labeller's words — "the twenty complaints
    #: the 2026 monsoon review said we scored wrong". Required at publication by
    #: the service, for the reason ``change_reason`` is.
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    label_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    #: SHA-256 over the canonical JSON of every label, computed at publication.
    #: Recorded again on each certificate so a set that changed cannot silently
    #: keep vouching for candidates tested against its previous contents.
    labels_hash: Mapped[str | None] = mapped_column(String(HASH_HEX_LENGTH), nullable=True)

    #: The share of labels a candidate must satisfy. Not fixed at 1.0: a set
    #: assembled from disputed complaints legitimately contains cases reasonable
    #: people score differently, and a threshold of "every single one" makes the
    #: guardrail impossible to satisfy and therefore certain to be switched off.
    #: The service refuses a value below ``MINIMUM_PASS_RATIO`` for the opposite
    #: reason.
    pass_ratio: Mapped[float] = mapped_column(Float, nullable=False, server_default="1.0")

    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EvaluationLabel(
    UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, OptimisticVersionMixin, Base
):
    """One human judgement about one complaint.

    Every expectation is optional and at least one must be present — the CHECK
    below. A label that asserts nothing passes every candidate, which would not
    be an empty test so much as a test that reports success, and a set of them
    would make an activation look guarded when it was not.
    """

    __tablename__ = "evaluation_labels"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "evaluation_set_id",
            "complaint_id",
            name="uq_evaluation_labels_set_complaint",
        ),
        Index("ix_evaluation_labels_tenant_id_set", "tenant_id", "evaluation_set_id"),
        CheckConstraint(
            "expected_severity_tier IS NOT NULL "
            "OR expected_severity_min IS NOT NULL "
            "OR expected_severity_max IS NOT NULL "
            "OR expected_safety_fired IS NOT NULL "
            "OR expected_department_code IS NOT NULL "
            "OR expected_dedup_outcome IS NOT NULL",
            name="a_label_must_assert_something",
        ),
        CheckConstraint(
            "expected_severity_min IS NULL "
            "OR expected_severity_max IS NULL "
            "OR expected_severity_max >= expected_severity_min",
            name="severity_bounds_are_ordered",
        ),
    )

    evaluation_set_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        # CASCADE, uniquely in this schema, and only because a label has no
        # meaning apart from its set: it is not evidence about a complaint, it
        # is one row of one exam. Deleting a *published* set is refused by the
        # service, so the cascade can only ever reach a draft.
        ForeignKey("evaluation_sets.id", ondelete="CASCADE", name="fk_evaluation_labels_set"),
        nullable=False,
    )

    #: No foreign key to ``complaints``. A label may name a complaint whose
    #: partition has since been archived, and a set that stopped being loadable
    #: because retention ran would be a guardrail with an expiry date nobody
    #: chose. The evaluation reports unresolvable labels rather than failing.
    complaint_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    expected_severity_tier: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expected_severity_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_severity_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_safety_fired: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    expected_department_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expected_dedup_outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)

    #: Why a human said so. §6.1 applied to the labels themselves: an evaluation
    #: set is evidence, and evidence that records a verdict without its reason
    #: cannot be reviewed when the verdict is later disputed.
    rationale: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    labelled_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class PolicyCertificate(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """Evidence that a specific document body was marked against a specific set.

    No optimistic version column, and that is deliberate: a certificate is never
    updated. A re-evaluation writes a new row, and the activation path reads the
    newest — so the history of "this candidate failed twice and then passed"
    stays legible, which is exactly the pattern somebody reviewing an incident
    wants to see.
    """

    __tablename__ = "policy_certificates"
    __table_args__ = (
        # The activation lookup: newest certificate for these exact bytes.
        Index(
            "ix_policy_certificates_lookup",
            "tenant_id",
            "kind",
            "candidate_content_hash",
            "created_at",
        ),
        Index("ix_policy_certificates_tenant_id_set", "tenant_id", "evaluation_set_id"),
        CheckConstraint(f"kind IN ({_quoted(POLICY_KINDS)})", name="kind_is_known"),
        CheckConstraint(f"verdict IN ({_quoted(CERTIFICATE_VERDICTS)})", name="verdict_is_known"),
        CheckConstraint("labels_evaluated >= 0", name="evaluated_is_not_negative"),
        CheckConstraint("labels_passed <= labels_evaluated", name="passed_within_evaluated"),
        # A pass with nothing evaluated is the failure this whole table exists
        # to prevent, arriving through arithmetic rather than through a missing
        # check: zero of zero is a ratio of 1.0 under most implementations.
        CheckConstraint(
            "verdict <> 'pass' OR labels_evaluated > 0", name="a_pass_evaluated_something"
        ),
    )

    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_content_hash: Mapped[str] = mapped_column(String(HASH_HEX_LENGTH), nullable=False)
    candidate_revision: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    evaluation_set_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        # RESTRICT: a certificate names the exam it passed, and an exam that can
        # be deleted out from under its certificates leaves rows attesting to
        # nothing.
        ForeignKey("evaluation_sets.id", ondelete="RESTRICT", name="fk_policy_certificates_set"),
        nullable=False,
    )
    #: The set's ``labels_hash`` at issue time. Compared at activation, so a set
    #: whose contents changed invalidates its certificates instead of vouching
    #: for candidates that were marked against different labels.
    labels_hash: Mapped[str] = mapped_column(String(HASH_HEX_LENGTH), nullable=False)

    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("simulation_runs.id", ondelete="RESTRICT", name="fk_policy_certificates_run"),
        nullable=True,
    )

    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    labels_evaluated: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    labels_passed: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    #: Labels whose complaint could not be reconstructed — archived, or never in
    #: this tenant. Counted separately from failures, because "we could not mark
    #: this" and "the candidate got this wrong" are different findings and only
    #: one of them is the candidate's fault.
    labels_unresolvable: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    #: Per-label outcomes, for the screen that explains a refusal. Bounded by
    #: the set's own size, which the service caps.
    findings: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, server_default="{}")
    issued_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class ShadowObservation(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """What a candidate policy *would* have decided, recorded beside what was.

    Append-only and never read by any decision path — a property the schema
    cannot enforce and the code can: nothing in ``policy`` or ``pipeline``
    imports this model, and a test asserts that by walking imports. The table is
    an observation log, and an observation that fed back into a decision would
    make shadow mode a slow rollout with no approval step.

    No foreign key to ``complaints`` for the reason ``EvaluationLabel`` gives:
    retention must be able to archive a complaint without breaking the record
    that something was watching it.
    """

    __tablename__ = "shadow_observations"
    __table_args__ = (
        # One observation per (complaint, candidate). A re-run of the same
        # candidate over the same complaint is not new information, and without
        # this a shadow worker restarting mid-batch would double every count in
        # the divergence rate.
        UniqueConstraint(
            "tenant_id",
            "complaint_id",
            "candidate_content_hash",
            name="uq_shadow_observations_complaint_candidate",
        ),
        Index(
            "ix_shadow_observations_tenant_id_kind_created",
            "tenant_id",
            "kind",
            "created_at",
        ),
        Index(
            "ix_shadow_observations_divergent",
            "tenant_id",
            "candidate_content_hash",
            postgresql_where=text("diverged"),
        ),
        CheckConstraint(f"kind IN ({_quoted(POLICY_KINDS)})", name="kind_is_known"),
    )

    complaint_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_revision: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    candidate_content_hash: Mapped[str] = mapped_column(String(HASH_HEX_LENGTH), nullable=False)

    #: The live bundle's stamps at observation time, so a divergence can be
    #: attributed to a specific pair of configurations rather than to "the
    #: policy at some point in March".
    live_stamps: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )

    #: ``CaseOutcome.digest()`` for each side. Equal digests mean the candidate
    #: agreed; storing the digests as well as the flag means a disagreement
    #: about whether they agreed is itself resolvable.
    live_digest: Mapped[str] = mapped_column(String(HASH_HEX_LENGTH), nullable=False)
    candidate_digest: Mapped[str] = mapped_column(String(HASH_HEX_LENGTH), nullable=False)
    diverged: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    #: The two ``comparable()`` mappings, kept only when they differ. An
    #: agreement carries no information beyond the digest, and storing the
    #: bodies for every observation would make shadow mode the largest table in
    #: the system within a week.
    difference: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
