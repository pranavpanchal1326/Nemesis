"""Version 2 of the API surface — preview.

**Why this exists at all in the phase that introduces versioning.** The Phase 4
gate is "a v1 consumer keeps working after v2 ships, proven by a pinned contract
test". That claim cannot be proven against a v2 which does not exist; a test
asserting v1 still works when nothing has changed asserts nothing. So v2 ships,
and it ships a genuinely breaking reshape rather than a cosmetic one — the sort
of change that *would* break a naive consumer if it had been applied to v1 in
place, which is exactly what the version registry exists to prevent.

**What v2 changes, and why each is breaking.**

- ``/ward/{code}/summary`` becomes ``/zone/{code}/summary``. ADR-0018 separated
  responsibility from place, and "ward" is one tenant's word for one kind of
  place — a campus has buildings and an industrial park has estates. A client
  with the old path gets 404 on v2, which is the definition of breaking.
- The counts move under a ``totals`` object. Flat counters alongside metadata
  made it impossible to add a second measurement family without the response
  becoming a bag; grouping them is right and it relocates every field a v1
  consumer reads.

Only the public surface is versioned to v2. The control plane and the ingest
endpoints keep their v1 contract, because nothing about them changed — bumping
every router in lockstep would force a migration on consumers whose contract is
identical, which is a version number pretending to be a release.
"""

from __future__ import annotations

from fastapi import APIRouter

from nemesis.api.v2.public import router as public_router

api_v2 = APIRouter(prefix="/api/v2")
api_v2.include_router(public_router)

__all__ = ["api_v2"]
