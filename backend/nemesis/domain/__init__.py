"""Domain vocabulary shared by the models, the event catalog, and the pipeline."""

from __future__ import annotations

from nemesis.domain.lifecycle import (
    AssigneeType,
    ComplaintStatus,
    EntityType,
    MilestoneStage,
    WorkOrderStatus,
)

__all__ = [
    "AssigneeType",
    "ComplaintStatus",
    "EntityType",
    "MilestoneStage",
    "WorkOrderStatus",
]
