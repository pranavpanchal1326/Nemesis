"""Projections — current state derived from the event log (§9.1).

Importing this package registers every handler. A partially-registered projector
set would silently produce a projection missing whatever the unimported module
handled, and the result would look like valid state rather than like an error.
"""

from __future__ import annotations

from nemesis.projections import handlers as handlers  # re-exported: registration side effect
from nemesis.projections.registry import (
    PROJECTOR_VERSION,
    ProjectedState,
    ProjectionError,
    ProjectionEvent,
    has_projector,
    project,
    state_hash,
    unhandled_event_types,
)
from nemesis.projections.replay import (
    SNAPSHOT_INTERVAL,
    ReplayResult,
    replay_entity,
    to_projection_event,
    write_snapshot_if_due,
)
from nemesis.projections.writer import (
    ProjectionWriteError,
    is_materialised,
    rebuild_entity,
    rebuild_tenant,
    write_projection,
)

__all__ = [
    "PROJECTOR_VERSION",
    "SNAPSHOT_INTERVAL",
    "ProjectedState",
    "ProjectionError",
    "ProjectionEvent",
    "ProjectionWriteError",
    "ReplayResult",
    "has_projector",
    "is_materialised",
    "project",
    "rebuild_entity",
    "rebuild_tenant",
    "replay_entity",
    "state_hash",
    "to_projection_event",
    "unhandled_event_types",
    "write_projection",
    "write_snapshot_if_due",
]
