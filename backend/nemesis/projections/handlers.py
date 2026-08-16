"""Projection handlers for the three aggregates that have current-state tables.

Each handler is a pure ``(state, event) -> state`` fold. They are grouped in one
module because reading them together is how you check the invariant that matters
most: **no handler reads anything the event did not carry.** That is easy to
verify across forty short functions in one file and easy to lose across three.

Status transitions are recorded here rather than enforced here. A projector's
job is to say what the log implies; refusing an out-of-order transition would
mean the projection disagrees with history, and history wins. Phase 14's state
machine is what prevents the invalid event from being appended in the first
place — the guard belongs at the write, not at the read.
"""

from __future__ import annotations

from typing import Any

from nemesis.domain.lifecycle import ComplaintStatus, EntityType, WorkOrderStatus
from nemesis.projections.registry import ProjectedState, ProjectionEvent, projector

# ---------------------------------------------------------------------------
# Complaint
# ---------------------------------------------------------------------------


@projector(EntityType.COMPLAINT, "complaint_submitted")
def _complaint_submitted(state: ProjectedState, event: ProjectionEvent) -> ProjectedState:
    payload = event.payload
    state.update(
        {
            "status": ComplaintStatus.SUBMITTED.value,
            "latitude": payload["latitude"],
            "longitude": payload["longitude"],
            "description_text": payload.get("description_text"),
            "photo_url": payload.get("photo_url"),
            "audio_url": payload.get("audio_url"),
            "locale": payload.get("locale"),
            "submitter_device_fingerprint": payload.get("device_fingerprint"),
            "reported_at": event.occurred_at,
            # Trust starts neutral and is moved by evidence. Recorded in the
            # projection because §11.1 makes "we could not verify this" a
            # first-class outcome rather than an absence.
            "trust_score": 0.0,
            "is_safety_flagged": False,
            "is_fraud_flagged": False,
        }
    )
    return state


@projector(EntityType.COMPLAINT, "exif_check_completed")
def _exif_check_completed(state: ProjectedState, event: ProjectionEvent) -> ProjectedState:
    payload = event.payload
    state["exif_present"] = payload["exif_present"]
    state["exif_distance_meters"] = payload.get("distance_meters")
    state["trust_score"] = round(float(state.get("trust_score", 0.0)) + payload["trust_delta"], 6)
    return state


@projector(EntityType.COMPLAINT, "safety_trigger_fired")
def _safety_trigger_fired(state: ProjectedState, event: ProjectionEvent) -> ProjectedState:
    """§11.2. Sets the flag *and* the status, because a safety trigger bypasses
    the queue — a projection showing the flag while the status still reads
    ``submitted`` would put the report back in the normal work list."""
    state["is_safety_flagged"] = True
    state["status"] = ComplaintStatus.FLAGGED.value
    state["safety_rule_id"] = event.payload["rule_id"]
    state["safety_ruleset_version"] = event.payload["ruleset_version"]
    return state


@projector(EntityType.COMPLAINT, "classification_scored")
def _classification_scored(state: ProjectedState, event: ProjectionEvent) -> ProjectedState:
    payload = event.payload
    state.update(
        {
            "category": payload["category"],
            "classification_confidence": payload["confidence"],
            "classifier_model_id": payload["model_id"],
            "prompt_set_version": payload["prompt_set_version"],
        }
    )
    if payload.get("transcript") is not None:
        state["transcript"] = payload["transcript"]
        state["detected_language"] = payload.get("detected_language")
    # A safety flag outranks classification: it was routed out of the normal
    # pipeline and must not be walked back into it by a later stage completing.
    if not state.get("is_safety_flagged"):
        state["status"] = ComplaintStatus.CLASSIFIED.value
    return state


@projector(EntityType.COMPLAINT, "severity_scored")
def _severity_scored(state: ProjectedState, event: ProjectionEvent) -> ProjectedState:
    payload = event.payload
    state.update(
        {
            "severity_score": payload["score"],
            "severity_breakdown": {
                "components": payload["components"],
                "weights": payload["weights"],
            },
            "severity_policy_version": payload["policy_version"],
        }
    )
    if not state.get("is_safety_flagged"):
        state["status"] = ComplaintStatus.SCORED.value
    return state


# ---------------------------------------------------------------------------
# Cluster
# ---------------------------------------------------------------------------


@projector(EntityType.COMPLAINT_CLUSTER, "cluster_created")
def _cluster_created(state: ProjectedState, event: ProjectionEvent) -> ProjectedState:
    payload = event.payload
    state.update(
        {
            "latitude": payload["latitude"],
            "longitude": payload["longitude"],
            "complaint_ids": [str(payload["seed_complaint_id"])],
            "report_count": 1,
            "first_reported": event.occurred_at,
            "last_reported": event.occurred_at,
        }
    )
    return state


@projector(EntityType.COMPLAINT_CLUSTER, "cluster_match_found")
def _cluster_match_found(state: ProjectedState, event: ProjectionEvent) -> ProjectedState:
    payload = event.payload
    members: list[str] = list(state.get("complaint_ids", []))
    complaint_id = str(payload["complaint_id"])
    # Idempotent membership. A retried merge task must not inflate the report
    # count — §12.5 re-scores on report count crossing a threshold, so a double
    # count is a severity escalation manufactured by a redelivery.
    if complaint_id not in members:
        members.append(complaint_id)
    state["complaint_ids"] = members
    state["report_count"] = len(members)
    state["last_reported"] = event.occurred_at
    state["confidence"] = payload["combined_confidence"]
    state["dedup_policy_version"] = payload["policy_version"]
    return state


@projector(EntityType.COMPLAINT_CLUSTER, "cluster_merge_reverted")
def _cluster_merge_reverted(state: ProjectedState, event: ProjectionEvent) -> ProjectedState:
    """§14.3. The compensating event removes the member; the merge event stays
    in the log. History records the mistake and the correction, not just the
    corrected result."""
    members: list[str] = [
        member
        for member in state.get("complaint_ids", [])
        if member != str(event.payload["complaint_id"])
    ]
    state["complaint_ids"] = members
    state["report_count"] = len(members)
    reverts: list[Any] = list(state.get("reverted_merges", []))
    reverts.append(
        {
            "complaint_id": str(event.payload["complaint_id"]),
            "reason": event.payload["reason"],
            "at": event.occurred_at,
        }
    )
    state["reverted_merges"] = reverts
    return state


# ---------------------------------------------------------------------------
# Work order
# ---------------------------------------------------------------------------


@projector(EntityType.WORK_ORDER, "work_order_created")
def _work_order_created(state: ProjectedState, event: ProjectionEvent) -> ProjectedState:
    payload = event.payload
    state.update(
        {
            "status": WorkOrderStatus.CREATED.value,
            "cluster_id": str(payload["cluster_id"]),
            "department_id": _optional_str(payload.get("department_id")),
            "routing_rule_id": payload.get("routing_rule_id"),
            "routing_policy_version": payload.get("policy_version"),
        }
    )
    return state


@projector(EntityType.WORK_ORDER, "work_order_assigned")
def _work_order_assigned(state: ProjectedState, event: ProjectionEvent) -> ProjectedState:
    payload = event.payload
    state.update(
        {
            "status": WorkOrderStatus.ASSIGNED.value,
            "assignee_type": payload["assignee_type"],
            "assignee_id": str(payload["assignee_id"]),
            "sla_deadline": payload["sla_deadline"],
            "sla_policy_version": payload.get("sla_policy_version"),
            "selection_rank": payload.get("selection_rank"),
        }
    )
    return state


@projector(EntityType.WORK_ORDER, "ssim_verification_completed")
def _ssim_verification_completed(state: ProjectedState, event: ProjectionEvent) -> ProjectedState:
    payload = event.payload
    state["ssim_score"] = payload["ssim_score"]
    state["ssim_threshold"] = payload["threshold"]
    state["ssim_passed"] = payload["passed"]
    state["perceptual_hash_matched"] = payload["perceptual_hash_matched"]
    # A perceptual-hash match means the "after" photo *is* the "before" photo.
    # It cannot advance to pending_verification however high the SSIM score is —
    # in fact a resubmitted photo scores near 1.0, which is why the two signals
    # are read together here rather than either one alone.
    if payload["passed"] and not payload["perceptual_hash_matched"]:
        state["status"] = WorkOrderStatus.PENDING_VERIFICATION.value
    else:
        state["status"] = WorkOrderStatus.IN_PROGRESS.value
    return state


@projector(EntityType.WORK_ORDER, "citizen_confirmation_requested")
def _citizen_confirmation_requested(
    state: ProjectedState, event: ProjectionEvent
) -> ProjectedState:
    state["confirmation_requested_at"] = event.occurred_at
    state["confirmation_window_hours"] = event.payload["window_hours"]
    state["confirmation_channel"] = event.payload["channel"]
    return state


@projector(EntityType.WORK_ORDER, "citizen_confirmed")
def _citizen_confirmed(state: ProjectedState, event: ProjectionEvent) -> ProjectedState:
    """§44 requires an auto-confirmed closure to stay distinguishable from a
    real one everywhere it surfaces — so the projection carries the distinction
    rather than collapsing both into ``closed``."""
    state["status"] = WorkOrderStatus.CLOSED.value
    state["auto_confirmed"] = event.payload["auto_confirmed"]
    state["confirmed_by"] = _optional_str(event.payload.get("confirmed_by"))
    state["closed_at"] = event.occurred_at
    return state


@projector(EntityType.WORK_ORDER, "citizen_disputed")
def _citizen_disputed(state: ProjectedState, event: ProjectionEvent) -> ProjectedState:
    state["status"] = WorkOrderStatus.DISPUTED.value
    state["dispute_reason"] = event.payload["reason"]
    state["disputed_by"] = _optional_str(event.payload.get("disputed_by"))
    state["disputed_at"] = event.occurred_at
    return state


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)
