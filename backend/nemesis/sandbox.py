"""A sandbox tenant with synthetic data, for integrators.

**Why a real tenant and not a mock.** A mocked response proves the shape and
nothing else. An integration written against one passes every test and then
meets suppression, empty buckets, pagination, and a 404 for a zone that has no
complaints — none of which a fixture author thinks to mock, and all of which are
the states an integrator actually has to handle. The sandbox runs the real
queries against generated rows, so those states arrive during development
instead of during a launch.

**The data is synthetic and says so.** Every generated complaint carries the
sandbox tenant, its own invented zone vocabulary, and no media at all. Copying a
production tenant's data into a sandbox is the obvious shortcut and it is a
disclosure: the aggregates are scrubbed, but the underlying rows would be real
citizens' reports sitting in a tenant whose whole purpose is to be handed to
strangers.

**Determinism is a parameter.** ``seed`` makes a sandbox reproducible, which is
what lets an integrator write an assertion against a specific figure. Without it
every re-provision changes the numbers and the only testable assertion is
"a number came back".
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from geoalchemy2.elements import WKTElement
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.control_plane import provisioning
from nemesis.control_plane.schemas import ProvisioningRequest
from nemesis.db.models.complaint import Complaint, ComplaintCluster
from nemesis.db.models.organisation import Contractor, Zone
from nemesis.db.models.tenant import Tenant
from nemesis.db.models.work_order import BudgetAllocation, WorkOrder
from nemesis.domain.lifecycle import AssigneeType, ComplaintStatus, WorkOrderStatus
from nemesis.events.store import EventStore
from nemesis.tenancy.context import tenant_scope

#: A vocabulary that is obviously not a real municipality. Deliberate: an
#: integrator reading "Northgate" in their logs must never wonder whether they
#: are looking at a live customer's data.
SANDBOX_ZONES: Final[tuple[tuple[str, str], ...]] = (
    ("SBX-N", "Northgate"),
    ("SBX-S", "Southmarket"),
    ("SBX-E", "Easthollow"),
    ("SBX-W", "Westferry"),
)

SANDBOX_CATEGORIES: Final[tuple[str, ...]] = (
    "road_surface_defect",
    "streetlight_outage",
    "drain_blockage",
    "waste_overflow",
    "signage_damage",
)

#: Around a fictional centre. Coordinates are published coarsened anyway, but
#: keeping the sandbox geographically distinct from any real deployment means a
#: misconfigured client pointing at production is visible on a map rather than
#: plausible.
_CENTRE = (18.9, 72.8)


@dataclass(frozen=True, slots=True)
class SandboxSummary:
    tenant_id: uuid.UUID
    slug: str
    zones: int
    complaints: int
    work_orders: int
    contractors: int
    seed: int


async def provision_sandbox(
    session: AsyncSession,
    *,
    slug: str,
    complaints: int = 240,
    seed: int = 20260817,
) -> SandboxSummary:
    """Create a publishing sandbox tenant and fill it with generated data.

    Goes through ``control_plane.provisioning`` rather than inserting a tenant
    row directly. That is the point of the exercise as much as the data is: if
    the sandbox needed a special path, the special path would be the thing being
    demonstrated, and the ordinary onboarding path would be the one nobody
    exercises.
    """
    rng = random.Random(seed)

    result = await provisioning.provision(
        session,
        request=ProvisioningRequest.model_validate(
            {
                "tenant": {
                    "slug": slug,
                    "name": "NEMESIS Sandbox",
                    "locales": ["en"],
                    "primary_locale": "en",
                    "timezone": "UTC",
                },
                "taxonomy": [
                    {"key": key, "display_name": key.replace("_", " ").title()}
                    for key in SANDBOX_CATEGORIES
                ],
                "zones": [
                    {"code": code, "name": name, "kind": "district"} for code, name in SANDBOX_ZONES
                ],
                "departments": [
                    {"code": "SBX-OPS", "name": "Sandbox Operations", "kind": "department"}
                ],
            }
        ),
    )
    tenant_id = result.tenant_id

    with tenant_scope(tenant_id):
        # The sandbox publishes by definition — it exists to be read by people
        # who have no relationship with this deployment.
        await session.execute(
            update(Tenant).where(Tenant.id == tenant_id).values(public_api_enabled=True)
        )
        await _seed_zone_centroids(session, tenant_id=tenant_id, rng=rng)
        contractors = await _seed_contractors(session, tenant_id=tenant_id, rng=rng)
        orders = await _seed_activity(
            session,
            tenant_id=tenant_id,
            complaints=complaints,
            contractors=contractors,
            rng=rng,
        )
        await _seed_budgets(session, tenant_id=tenant_id, rng=rng)

    return SandboxSummary(
        tenant_id=tenant_id,
        slug=slug,
        zones=len(SANDBOX_ZONES),
        complaints=complaints,
        work_orders=orders,
        contractors=len(contractors),
        seed=seed,
    )


async def seed_history(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    complaints: int = 400,
    days: int = 365,
    seed: int = 20260819,
) -> int:
    """Append complete complaint **chains**, spread over a window, to the event log.

    Added by Phase 7, and it fills a gap the rest of this module has: everything
    above writes *projection rows*. That is enough for the public API, which
    reads projections — and it is useless to a backtest, which folds the event
    log by design (ADR-0029) and would find a sandbox tenant with hundreds of
    complaints and no history at all.

    So this writes through the real ``EventStore``: real hashes, real chain
    tails, real idempotency, real ordering. Nothing here inserts an event row
    directly, for the reason the Phase 6 migration gives at length — rows that
    look like chain entries and fail ``verify_chain`` are worse than no rows.

    ``occurred_at`` is back-dated; ``recorded_at`` is not, and cannot be. That is
    the honest shape of seeded history: it says these things happened over the
    last year and were written today, which is exactly what happened.

    Deterministic under ``seed``, so a gate can assert a specific figure rather
    than "a number came back".
    """
    rng = random.Random(seed)
    store = EventStore(session)
    now = datetime.now(tz=UTC)
    slot = days / max(complaints, 1)
    written = 0

    for index in range(complaints):
        complaint_id = uuid.uuid4()
        # Spread evenly across the window rather than randomly, so a systematic
        # sample of the corpus covers the whole span — a random spread with a
        # small `complaints` leaves gaps that read as seasonality.
        #
        # The jitter stays *inside* each complaint's own slot, so every report
        # lands within [now - days, now). The obvious version — an even spread
        # with a few random hours subtracted — puts the first report a fraction
        # of a day before the window it claims to fill, and a caller asking for
        # "365 days" then finds 399 of 400 complaints in a 365-day window and
        # has to work out which boundary is lying.
        reported = (
            now
            - timedelta(days=days)
            + timedelta(days=slot * index)
            + timedelta(days=rng.uniform(0.0, slot))
        )
        category = SANDBOX_CATEGORIES[index % len(SANDBOX_CATEGORIES)]

        await store.append(
            entity_id=complaint_id,
            event_type="complaint_submitted",
            payload={
                "latitude": round(19.05 + rng.uniform(-0.05, 0.05), 6),
                "longitude": round(72.85 + rng.uniform(-0.05, 0.05), 6),
                "description_text": f"Synthetic sandbox report {index} about {category}",
                "locale": "en",
                "submitted_via": "web" if index % 3 else "whatsapp",
            },
            tenant_id=tenant_id,
            occurred_at=reported,
        )
        await store.append(
            entity_id=complaint_id,
            event_type="exif_check_completed",
            payload={
                "exif_present": index % 5 != 0,
                "distance_meters": round(rng.uniform(0.0, 40.0), 2),
                "trust_delta": round(rng.uniform(-0.2, 0.4), 4),
            },
            tenant_id=tenant_id,
            occurred_at=reported + timedelta(seconds=20),
        )
        await store.append(
            entity_id=complaint_id,
            event_type="classification_scored",
            payload={
                "category": category,
                "confidence": round(rng.uniform(0.55, 0.99), 4),
                "model_id": "sandbox-synthetic",
                "prompt_set_version": "sandbox-1",
            },
            tenant_id=tenant_id,
            occurred_at=reported + timedelta(seconds=40),
        )
        # The component *measurements*, which is what a backtest replays. The
        # score beside them is what the rubric of the day concluded, and no
        # corpus reads it — see `simulation.corpus`.
        components = {
            "visual_damage": round(rng.uniform(0.0, 10.0), 3),
            "road_class": round(rng.uniform(0.0, 10.0), 3),
            "poi_proximity": round(rng.uniform(0.0, 10.0), 3),
            "cluster_reports": round(rng.uniform(0.0, 10.0), 3),
        }
        await store.append(
            entity_id=complaint_id,
            event_type="severity_scored",
            payload={
                "score": round(sum(components.values()) / len(components), 4),
                "components": components,
                "weights": dict.fromkeys(components, 0.25),
                "policy_version": "severity_rubric@1",
            },
            tenant_id=tenant_id,
            occurred_at=reported + timedelta(seconds=60),
        )
        written += 1

    return written


async def _seed_zone_centroids(
    session: AsyncSession, *, tenant_id: uuid.UUID, rng: random.Random
) -> None:
    rows = (
        (
            await session.execute(
                select(Zone.id).where(Zone.tenant_id == tenant_id).order_by(Zone.code)
            )
        )
        .scalars()
        .all()
    )
    for index, zone_id in enumerate(rows):
        lat = _CENTRE[0] + (index - 1.5) * 0.02 + rng.uniform(-0.005, 0.005)
        lng = _CENTRE[1] + (index - 1.5) * 0.02 + rng.uniform(-0.005, 0.005)
        await session.execute(
            update(Zone)
            .where(Zone.tenant_id == tenant_id, Zone.id == zone_id)
            .values(centroid=WKTElement(f"POINT({lng} {lat})", srid=4326))
        )


async def _seed_contractors(
    session: AsyncSession, *, tenant_id: uuid.UUID, rng: random.Random
) -> list[uuid.UUID]:
    names = ("Northgate Works Ltd", "Sandbox Civil Co", "Harbourline Maintenance")
    created: list[uuid.UUID] = []
    for index, name in enumerate(names):
        contractor = Contractor(
            tenant_id=tenant_id,
            name=name,
            registration_id=f"SBX-REG-{index + 1:04d}",
            active_since=(datetime.now(tz=UTC) - timedelta(days=rng.randint(400, 2000))).date(),
        )
        session.add(contractor)
        await session.flush()
        created.append(contractor.id)
    return created


async def _seed_activity(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    complaints: int,
    contractors: list[uuid.UUID],
    rng: random.Random,
) -> int:
    """Generate complaints, clusters, and work orders with a plausible spread.

    **One zone is deliberately left thin.** ``SBX-W`` receives a handful of
    reports, below any sensible suppression floor — so an integrator sees a
    genuinely suppressed response during development rather than discovering
    that branch when a real quiet ward hits it.
    """
    now = datetime.now(tz=UTC)
    orders = 0

    for index in range(complaints):
        zone_index = 3 if index % 60 == 0 else rng.randrange(3)
        zone_code = SANDBOX_ZONES[zone_index][0]
        reported = now - timedelta(days=rng.randint(0, 180), hours=rng.randint(0, 23))
        lat = _CENTRE[0] + (zone_index - 1.5) * 0.02 + rng.uniform(-0.008, 0.008)
        lng = _CENTRE[1] + (zone_index - 1.5) * 0.02 + rng.uniform(-0.008, 0.008)

        cluster = ComplaintCluster(
            tenant_id=tenant_id,
            centroid=WKTElement(f"POINT({lng} {lat})", srid=4326),
            report_count=1,
            first_reported=reported,
            last_reported=reported,
            current_severity=round(rng.uniform(1.0, 9.5), 2),
        )
        session.add(cluster)
        await session.flush()

        resolved = rng.random() < 0.62
        session.add(
            Complaint(
                tenant_id=tenant_id,
                status=(
                    ComplaintStatus.RESOLVED if resolved else ComplaintStatus.IN_PROGRESS
                ).value,
                category=SANDBOX_CATEGORIES[rng.randrange(len(SANDBOX_CATEGORIES))],
                classification_confidence=round(rng.uniform(0.55, 0.99), 3),
                # No description, no media. Synthetic prose in a field the scrub
                # is designed to withhold would be a test fixture teaching the
                # wrong lesson about what this column is for.
                location=WKTElement(f"POINT({lng} {lat})", srid=4326),
                reported_at=reported,
                cluster_id=cluster.id,
                severity_score=round(rng.uniform(1.0, 9.5), 2),
                ward=zone_code,
            )
        )

        if resolved:
            closed_at = reported + timedelta(hours=rng.randint(4, 400))
            deadline = reported + timedelta(hours=72)
            session.add(
                WorkOrder(
                    tenant_id=tenant_id,
                    complaint_cluster_id=cluster.id,
                    status=WorkOrderStatus.CLOSED.value,
                    assigned_to_type=AssigneeType.CONTRACTOR.value,
                    assigned_to_id=contractors[rng.randrange(len(contractors))],
                    sla_deadline=deadline,
                    created_at=reported,
                    updated_at=closed_at,
                    # Two thirds carry an SSIM score, so the §44 auto-confirmed
                    # count in the published summary is non-zero and non-total —
                    # a figure an integrator can see move.
                    ssim_score=(round(rng.uniform(0.4, 0.95), 3) if rng.random() < 0.66 else None),
                )
            )
            orders += 1

    return orders


async def _seed_budgets(session: AsyncSession, *, tenant_id: uuid.UUID, rng: random.Random) -> None:
    from decimal import Decimal

    for code, _name in SANDBOX_ZONES:
        for source in ("municipal_capital", "state_grant"):
            allocated = Decimal(rng.randrange(500_000, 5_000_000)).quantize(Decimal("1.00"))
            session.add(
                BudgetAllocation(
                    tenant_id=tenant_id,
                    ward=code,
                    funding_source=source,
                    fiscal_year="2026-27",
                    allocated_amount=allocated,
                    spent_amount=(allocated * Decimal(rng.randrange(10, 95)) / 100).quantize(
                        Decimal("1.00")
                    ),
                )
            )


def sandbox_payload(summary: SandboxSummary) -> dict[str, Any]:
    """What the API returns after provisioning one."""
    return {
        "tenant_id": str(summary.tenant_id),
        "slug": summary.slug,
        "public_api_base": f"/api/v1/public/{summary.slug}",
        "zones": summary.zones,
        "complaints": summary.complaints,
        "work_orders": summary.work_orders,
        "contractors": summary.contractors,
        "seed": summary.seed,
        "notice": (
            "Synthetic data. No row here originated from a citizen, and no zone, "
            "contractor, or budget figure corresponds to a real one."
        ),
    }


def _main() -> int:  # pragma: no cover — operator entry point
    """``python -m nemesis.sandbox [slug] [--complaints N] [--seed N]``.

    A CLI rather than an HTTP endpoint. Provisioning a sandbox is an operator
    action taken once per integrator, and adding a route for it would put a
    tenant-creating write behind the same shared token that already guards
    provisioning — a second way to do the same thing, with its own tests.
    """
    import argparse
    import asyncio
    import json
    import sys

    from nemesis.db.session import dispose_engine, session_scope

    parser = argparse.ArgumentParser(prog="nemesis.sandbox")
    parser.add_argument("slug", nargs="?", default="sandbox")
    parser.add_argument("--complaints", type=int, default=240)
    parser.add_argument("--seed", type=int, default=20260817)
    # Phase 7. Off by default: seeding a year of chains is the slow part, and
    # an integrator provisioning a sandbox for the public API does not need it.
    parser.add_argument("--history", type=int, default=0, metavar="COMPLAINTS")
    parser.add_argument("--history-days", type=int, default=365)
    args = parser.parse_args()

    async def run() -> dict[str, Any]:
        try:
            async with session_scope() as session:
                summary = await provision_sandbox(
                    session, slug=args.slug, complaints=args.complaints, seed=args.seed
                )
            payload = sandbox_payload(summary)
            if args.history:
                async with session_scope() as session:
                    with tenant_scope(summary.tenant_id):
                        payload["history_events"] = await seed_history(
                            session,
                            tenant_id=summary.tenant_id,
                            complaints=args.history,
                            days=args.history_days,
                            seed=args.seed,
                        )
            return payload
        finally:
            await dispose_engine()

    # `sys.stdout.write`, matching the other CLIs in this repository. Phase 0
    # and Phase 1a both hit the same defect from binding a stream at import
    # time; writing to the attribute at call time is what avoids it.
    sys.stdout.write(json.dumps(asyncio.run(run()), indent=2) + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
