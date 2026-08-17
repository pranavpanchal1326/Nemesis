"""Lifecycle states — the one enumeration that is deliberately *not* tenant data.

The program plan is emphatic that anything a customer could plausibly want
different must be configuration, and the critique log's first defect is a
hardcoded domain model. So an enum in source needs a justification, not a
convention.

The line drawn here: **what a complaint is about is tenant data; what stage it
has reached is platform structure.** A campus renames "pothole" to "elevator
fault" and adds fifteen categories of its own — that is Phase 5 taxonomy, and
nothing in this file constrains it. But "verified", "clustered", "scored",
"pending_verification" are the stages of the pipeline this product *is*. Each
one has code that runs at it, an event that produces it, and a projector that
applies it. A tenant cannot add a stage without new code, so pretending the set
is configurable would be a lie told in JSON.

The consequence is accepted honestly: adding a state is a migration plus an
upcaster, which is the correct cost for changing the shape of the pipeline.

These are ``StrEnum``, so they compare equal to the strings stored in the
database and serialise into event payloads without a conversion step that
somebody would eventually forget on one code path.
"""

from __future__ import annotations

from enum import StrEnum


class ComplaintStatus(StrEnum):
    """§9.2 complaint lifecycle."""

    SUBMITTED = "submitted"
    VERIFYING = "verifying"
    CLASSIFIED = "classified"
    #: Reached when the classifier is unavailable (§24.2). A degraded pipeline
    #: parks the report for human review; it never drops it, and it never
    #: guesses a category that would then be indistinguishable from a confident
    #: one downstream.
    PENDING_CLASSIFICATION = "pending_classification"
    CLUSTERED = "clustered"
    SCORED = "scored"
    ROUTED = "routed"
    IN_PROGRESS = "in_progress"
    PENDING_VERIFICATION = "pending_verification"
    RESOLVED = "resolved"
    CLOSED = "closed"
    DISPUTED = "disputed"
    FLAGGED = "flagged"


class WorkOrderStatus(StrEnum):
    """§9.2 work order lifecycle."""

    CREATED = "created"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    PENDING_VERIFICATION = "pending_verification"
    CLOSED = "closed"
    DISPUTED = "disputed"


class EntityType(StrEnum):
    """What an event's ``entity_id`` refers to.

    A closed set because the hash chain is per entity: a value not listed here
    has no chain head table row, no projector, and no verification path, so
    accepting one would create history nobody can verify.
    """

    COMPLAINT = "complaint"
    COMPLAINT_CLUSTER = "complaint_cluster"
    WORK_ORDER = "work_order"
    CONTRACTOR = "contractor"
    BUDGET = "budget"
    ADMIN_ACTION = "admin_action"
    TENANT = "tenant"
    #: Degradations and integrity findings belong to the deployment, not to any
    #: complaint. Giving them their own chain keeps a noisy dependency outage
    #: from interleaving into the history of an unrelated citizen report.
    SYSTEM = "system"


class DegradationFallback(StrEnum):
    """What a pipeline stage did instead of its job (§24.2).

    Here rather than in ``nemesis.pipeline`` because a *projector* branches on
    it, and the projection layer must not depend on the orchestration layer —
    the dependency direction is domain ← projections ← pipeline, and a projector
    that imported a Celery module would drag the broker into replay.

    Each member is a distinct operational outcome, not a severity ranking:

    ``PENDING_CLASSIFICATION``
        §24.2 by name. The classifier is unavailable, so the report is parked
        for manual review with a status that says so. It is never lost and it
        never receives a guessed category, which downstream would be
        indistinguishable from a confident one.
    ``HALTED_FOR_REVIEW``
        A stage that cannot be skipped failed — the safety fail-safe being the
        one that matters. Proceeding would mean scoring a report the danger
        check never saw, so the pipeline stops and a human is the next step.
    ``SKIPPED_STAGE``
        The stage is optional and its absence only reduces information. EXIF
        cross-check is the example: §11.1 already treats absent EXIF as reduced
        trust rather than as a rejection, so an unavailable checker lands in
        the same place the missing-metadata case already does.
    """

    PENDING_CLASSIFICATION = "pending_classification"
    HALTED_FOR_REVIEW = "halted_for_review"
    SKIPPED_STAGE = "skipped_stage"


class AssigneeType(StrEnum):
    STAFF = "staff"
    CONTRACTOR = "contractor"


class MilestoneStage(StrEnum):
    START = "start"
    MID = "mid"
    COMPLETE = "complete"
