"""Resolving a coordinate to the place tree — §E17.1's *Place* card.

**Why this endpoint exists.** §E17.1 is explicit that the citizen app must state
where it thinks you are rather than hand you a map:

    Auto-located and presented as a *card*, not a picker: **"Paud Road, near
    Karve Statue · Kothrud · Ward 14"** … Never ask someone standing in traffic
    beside a pothole to pinch-zoom a map.

The place tree that produces the second half of that sentence has existed since
Phase 5 — `zones` carries a `MULTIPOLYGON` boundary, a self-referencing parent
and a GiST index — and **nothing published a read of it**. `GET
/control-plane/zones` returns `OrgUnitResponse`, which is name, code, kind,
parent and path: enough to draw the tree, and nothing about where any of it is.
So a browser could learn that Ward 14 exists and could not learn that it is
standing in it.

Track E cannot answer this on the client. Point-in-polygon needs the polygons,
publishing every ward boundary in a tenant to answer "which one am I in" is a
payload measured in megabytes, and the alternative — a third-party geocoder —
is banned outright by §6 Principle #6. So the question is answered where the
geometry already lives, by the index that already exists.

**What it does not do.** It does not produce *"Paud Road, near Karve Statue"*.
Street-level reverse geocoding needs a street graph this product does not have
and may not fetch from anybody else's. §E3.3's rule is that an omission is shown
rather than faked, so the card states the zone chain it genuinely knows and says
plainly that it does not know the street — which is recorded as a defect against
§E17.1 rather than papered over with a nearby landmark nobody verified.

**Privacy.** The coordinate is an input, supplied by the person standing on it,
and it is neither stored nor logged. What comes back is a ward — the coarsest
thing in the tree that is still useful, and coarser than the ~110 m
`GPS_DECIMALS` already permits on the public stream.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from geoalchemy2 import Geography
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import cast, func, select

from nemesis.api.deps import SessionDep, TenantDep
from nemesis.db.models.organisation import Zone
from nemesis.events.catalog import Latitude, Longitude
from nemesis.tenancy.context import tenant_scope

router = APIRouter(tags=["places"])


class PlaceUnit(BaseModel):
    """One unit of place, on the way up the tree."""

    model_config = ConfigDict(frozen=True)

    code: str
    name: str
    #: Tenant-defined: ``ward``, ``site``, ``building``, ``campus_block``. Not a
    #: platform enum — §E18's *"two hierarchies"* rule is that the shape of a
    #: city is the tenant's to declare.
    kind: str
    depth: int


class PlaceResolution(BaseModel):
    """Where a coordinate falls, from the smallest containing unit outward.

    ``units`` is ordered **innermost first**, so a card renders
    ``units[0].name`` as the headline and joins the rest as context — which is
    the order §E17.1's example is written in (*"Kothrud · Ward 14"*).

    An empty list is a legitimate answer and is not an error. A tenant that
    onboarded with ward *names* and no shapefile — which
    `nemesis/db/models/organisation.py` calls the common case at onboarding —
    has no geometry to test the point against, and saying so is more useful than
    guessing the nearest centroid.
    """

    model_config = ConfigDict(frozen=True)

    units: list[PlaceUnit] = Field(default_factory=list)
    #: True when this tenant has **no** zone boundaries at all, as opposed to
    #: having them and the point falling outside every one. The two look
    #: identical in `units` and mean completely different things to a citizen:
    #: *"we do not map places here"* versus *"you appear to be outside the
    #: city"*. §E3.3 — the difference is rendered rather than flattened.
    boundaries_configured: bool = True


@router.get(
    "/places/resolve",
    response_model=PlaceResolution,
    summary="Which units of place contain this coordinate",
)
async def resolve_place(
    tenant: TenantDep,
    session: SessionDep,
    latitude: Annotated[Latitude, Query()],
    longitude: Annotated[Longitude, Query()],
) -> PlaceResolution:
    """The zone chain containing a point, innermost first.

    **Two queries, and the second one is why §E17.1's card reads as a sentence.**

    The first finds the smallest zone whose boundary covers the point.
    `ix_zones_boundary` is GiST over the geography column, so `ST_Covers` is an
    index scan rather than a sweep of every ward in the tenant.

    The second walks *up*. Only the leaves of a place tree carry geometry in
    practice — a tenant draws its wards and lets the zone and the city be their
    union — so a purely spatial answer is one row, and §E17.1 asks for
    *"Kothrud · Ward 14"*, which is a chain. `Zone.path` is the materialised
    `/`-joined code chain, so every ancestor is recoverable by splitting one
    string and selecting the codes in it. Cheaper than a recursive CTE and
    exactly as correct, because `path` is maintained by the same writer that
    maintains `parent_id`.

    **Ordering, where boxes overlap.** Deepest first, then smallest area, then
    code. Overlap is not hypothetical: a locality inside a ward is a deliberate
    nesting, and two hand-drawn ward boxes that share an edge is a data-entry
    reality. Depth resolves the deliberate case; area resolves the accidental
    one in favour of the more specific claim; code makes the remaining tie
    deterministic, because a card that names a different ward on each refresh is
    worse than one that is consistently arguable.

    `ST_Covers` rather than `ST_Contains`: a coordinate that lands exactly on a
    boundary is *in* the ward, not in neither.
    """
    point = func.ST_SetSRID(func.ST_MakePoint(longitude, latitude), 4326)

    with tenant_scope(tenant.id):
        innermost = (
            await session.execute(
                select(Zone.path)
                .where(
                    Zone.tenant_id == tenant.id,
                    Zone.is_active.is_(True),
                    Zone.boundary.isnot(None),
                    func.ST_Covers(Zone.boundary, cast(point, Geography)),
                )
                .order_by(
                    Zone.depth.desc(),
                    func.ST_Area(Zone.boundary).asc(),
                    Zone.code.asc(),
                )
                .limit(1)
            )
        ).one_or_none()

        if innermost is not None:
            ancestry = [segment for segment in str(innermost.path).split("/") if segment]
            rows = (
                await session.execute(
                    select(Zone.code, Zone.name, Zone.kind, Zone.depth)
                    .where(
                        Zone.tenant_id == tenant.id,
                        Zone.is_active.is_(True),
                        Zone.code.in_(ancestry),
                    )
                    .order_by(Zone.depth.desc())
                )
            ).all()
            return PlaceResolution(
                units=[
                    PlaceUnit(code=row.code, name=row.name, kind=row.kind, depth=row.depth)
                    for row in rows
                ]
            )

        # No match. Distinguish "this tenant maps no places" from "you are
        # outside every place it maps", because a card cannot say the right
        # thing about the citizen's position without knowing which.
        configured = (
            await session.execute(
                select(func.count())
                .select_from(Zone)
                .where(
                    Zone.tenant_id == tenant.id,
                    Zone.is_active.is_(True),
                    Zone.boundary.isnot(None),
                )
            )
        ).scalar_one()

    return PlaceResolution(units=[], boundaries_configured=configured > 0)
