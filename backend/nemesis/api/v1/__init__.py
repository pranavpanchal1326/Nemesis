"""Version 1 of the API surface.

Versioned in the path from the first endpoint, not retrofitted. Critique-log
defect #12 is that §16.3 promises journalists and civil society a durable public
interface and the previous plan had no versioning story at all; Phase 4 turns
that into a compatibility obligation with a published deprecation clock. A
prefix chosen now costs nothing and cannot be added later without breaking
everyone.
"""

from __future__ import annotations

from fastapi import APIRouter

from nemesis.api.v1.complaints import router as complaints_router
from nemesis.api.v1.control_plane import router as control_plane_router
from nemesis.api.v1.realtime import router as realtime_router

#: Mounted by the application factory. The realtime router carries no prefix —
#: §26.3 fixes the WebSocket path at /ws/pipeline-events, outside the versioned
#: namespace, and moving it would break the contract the blueprint states.
api_v1 = APIRouter(prefix="/api/v1")
api_v1.include_router(complaints_router)
# Phase 5. Versioned like everything else: the control plane is an API product
# with the same compatibility obligation as the citizen-facing endpoints, and a
# solutions engineer's onboarding script is exactly the kind of consumer §16.3's
# deprecation clock exists to protect.
api_v1.include_router(control_plane_router)

__all__ = ["api_v1", "realtime_router"]
