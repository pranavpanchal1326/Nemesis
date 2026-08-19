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
from nemesis.api.v1.developers import portal_router
from nemesis.api.v1.developers import router as developers_router
from nemesis.api.v1.integrations import router as integrations_router
from nemesis.api.v1.policies import router as policies_router
from nemesis.api.v1.public import router as public_router
from nemesis.api.v1.realtime import router as realtime_router
from nemesis.api.v1.review import router as review_router
from nemesis.api.v1.simulations import router as simulations_router

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
# Phase 6. A sibling of the control-plane router rather than part of it: the
# two raise unrelated error hierarchies on purpose (see policy.errors), so one
# module would need two translation functions and a reader would have to work
# out which applied to each handler.
api_v1.include_router(policies_router)
# Phase 7. A third sibling, and a third error hierarchy, for the same reason
# the second one exists: `SimulationError` answers "is the evidence for
# letting this go live sound", which is a different question from "may this
# document go live" and needs a different translation table.
api_v1.include_router(simulations_router)
# Phase 8. Not under /control-plane: the review queue is operational work on
# citizen reports, not configuration of the tenant, and a solutions engineer
# with control-plane access is not automatically the person who should be
# deciding whether a flagged complaint is fraudulent. The split is a prefix
# today and the seam Phase 13 attaches two different permissions to.
api_v1.include_router(review_router)
# Phase 4. The public surface is mounted last of the three, deliberately: it is
# the only unauthenticated router, so a path collision between it and a
# tenant-scoped one must resolve in favour of the authenticated route rather
# than silently exposing it.
api_v1.include_router(public_router)
api_v1.include_router(integrations_router)
api_v1.include_router(developers_router)

__all__ = ["api_v1", "portal_router", "realtime_router"]
