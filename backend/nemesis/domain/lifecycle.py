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


class AssigneeType(StrEnum):
    STAFF = "staff"
    CONTRACTOR = "contractor"


class MilestoneStage(StrEnum):
    START = "start"
    MID = "mid"
    COMPLETE = "complete"
