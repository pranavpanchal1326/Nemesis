"""§11.3 coordinated abuse — velocity, geographic clustering, and nothing else.

**Flags, never blocks.** §11.3 says so in one sentence and this module is built
so the sentence cannot quietly stop being true: every function here returns a
*finding*, none of them writes, none of them raises to stop a pipeline, and the
projector for ``abuse_pattern_flagged`` deliberately does not move the
complaint's status. A detector with an enforcement path is a detector whose
first false positive suppresses a real citizen's report about a real hazard.

**Why two detectors and not one.** They fire on the same shape of evidence —
several submissions, one window — and they mean opposite things:

* *Velocity* is **one device, many reports**. A stuck retry loop, a script, or
  one person filing twenty complaints in an hour.
* *Geographic clustering* is **many devices, one place**. §11.3's own example:
  several "different" users converging on one ward in a tight window.

A single detector counting submissions in a window would fire on both and could
distinguish neither, so a reviewer would be handed "suspicious activity" with no
way to tell a bot farm from a street that genuinely flooded. The evidence
bundles differ, the thresholds differ, and a tenant can switch either off.

**Why a query and not the Redis token bucket §11.3 mentions.** The bucket
exists — ``api.ratelimit`` is exactly that, and it protects the *service* from
being flooded. This protects the *record*, and the two want different memories:
a bucket holds a count and forgets, while the question a reviewer asks is "show
me the other nineteen", which only a query can answer. Both ship; neither
replaces the other.

**Why the queries read ``complaints`` rather than folding the log.** This is a
search across *other* complaints, which is the case ``StageContext`` names when
it explains why a provider is handed a session at all — the same reason Phase 10
will query PostGIS and pgvector. Folding every chain in the tenant to answer
"how many in the last hour" would replay a year of history per submission.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.db.models.complaint import Complaint
from nemesis.observability.logging import get_logger

log = get_logger(__name__)

#: How many prior reports the evidence bundle names. §11.4 requires the bundle
#: in front of the reviewer; it does not require all four hundred of them, and a
#: bundle that inlines an unbounded list is a JSONB column that grows without a
#: ceiling on the hottest write in the phase.
EVIDENCE_SAMPLE_LIMIT = 10


class AbusePattern(StrEnum):
    """Which detector fired. Platform structure: each value names code.

    Recorded in ``abuse_pattern_flagged.pattern`` and in
    ``review_queue_items.reason``, so it is a contract in the same sense a
    taxonomy key is — renaming one orphans every flag already raised under it.
    """

    DEVICE_VELOCITY = "device_velocity"
    GEOGRAPHIC_CLUSTER = "geographic_cluster"


@dataclass(frozen=True, slots=True)
class AbuseFinding:
    """One detector's conclusion.

    ``fired`` is a field rather than being implied by returning ``None``,
    because the *negative* result is worth carrying: the evidence bundle shows
    a reviewer that velocity was checked and found three submissions against a
    limit of twelve, which is a different thing from velocity not having run.
    """

    pattern: AbusePattern
    fired: bool
    observation_count: int
    window_hours: float
    trust_delta: float
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)


async def assess_device_velocity(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    complaint_id: uuid.UUID,
    device_fingerprint: str | None,
    at: datetime,
    window_hours: float,
    max_submissions: int,
    trust_delta: float,
) -> AbuseFinding | None:
    """Reports from this device inside the window.

    Returns ``None`` — not a non-firing finding — when the submission carries no
    fingerprint. The distinction matters to the reviewer: "this device filed
    three reports" and "there was no device to count" are different states, and
    reporting the second as ``observation_count = 0`` would put a zero in the
    evidence bundle that reads as a measurement.

    A submission with no fingerprint is normal and is not itself suspicious —
    §22 minimises what is collected, a citizen may block it, and the public API
    scrubs it. Treating its absence as evidence would penalise privacy.
    """
    if not device_fingerprint:
        return None

    window_start = at - timedelta(hours=window_hours)
    statement = select(
        func.count(Complaint.id),
    ).where(
        Complaint.tenant_id == tenant_id,
        Complaint.submitter_device_fingerprint == device_fingerprint,
        Complaint.reported_at >= window_start,
        Complaint.reported_at <= at,
    )
    count = int((await session.execute(statement)).scalar_one())

    fired = count > max_submissions
    evidence: dict[str, Any] = {
        "window_start": window_start.isoformat(),
        "window_end": at.isoformat(),
        "limit": max_submissions,
        "observed": count,
    }
    if fired:
        evidence["recent_complaint_ids"] = await _recent_ids_for_device(
            session,
            tenant_id=tenant_id,
            device_fingerprint=device_fingerprint,
            window_start=window_start,
            at=at,
            exclude=complaint_id,
        )
    return AbuseFinding(
        pattern=AbusePattern.DEVICE_VELOCITY,
        fired=fired,
        observation_count=count,
        window_hours=window_hours,
        trust_delta=trust_delta if fired else 0.0,
        reason=(
            f"{count} submissions from this device in {window_hours:g}h, against a "
            f"limit of {max_submissions}"
            if fired
            else f"{count} submissions from this device in {window_hours:g}h, within "
            f"the limit of {max_submissions}"
        ),
        evidence=evidence,
    )


async def assess_geographic_cluster(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    complaint_id: uuid.UUID,
    latitude: float,
    longitude: float,
    at: datetime,
    radius_meters: float,
    window_hours: float,
    min_distinct_devices: int,
    trust_delta: float,
) -> AbuseFinding:
    """Distinct devices reporting inside one radius inside one window.

    **Distinct devices, not reports.** Twenty reports from one device at one
    junction is the velocity signal; twenty devices is the coordination signal,
    and counting rows rather than fingerprints would make the two indistinguish-
    able. ``COUNT(DISTINCT ...)`` skips NULLs, which is the behaviour wanted
    here for the reason ``assess_device_velocity`` gives about absent
    fingerprints — an unidentified submitter must not be counted as a
    conspirator, and a hundred of them must not add up to one.

    ``ST_DWithin`` on the ``geography`` column, which uses the GiST index
    declared on ``complaints.location``. ``ST_Distance < r`` would produce the
    same answer by sequential-scanning every complaint the tenant has ever
    received.
    """
    window_start = at - timedelta(hours=window_hours)
    point = func.ST_SetSRID(func.ST_MakePoint(longitude, latitude), 4326).cast(
        Complaint.location.type
    )
    # The window predicate, composed once and reused by the sample query below.
    # The tenant filter is deliberately **not** in it: it is written inline at
    # each `select()` instead, because `check_tenant_scoping.py` reads the AST
    # at the call site and a predicate hidden behind a variable is one it cannot
    # see. A tenant-scoped query the isolation check cannot verify is worth
    # exactly as much as an unscoped one.
    within = and_(
        Complaint.reported_at >= window_start,
        Complaint.reported_at <= at,
        func.ST_DWithin(Complaint.location, point, radius_meters),
    )
    statement = select(
        func.count(func.distinct(Complaint.submitter_device_fingerprint)),
        func.count(Complaint.id),
    ).where(Complaint.tenant_id == tenant_id, within)
    row = (await session.execute(statement)).one()
    distinct_devices, total_reports = int(row[0]), int(row[1])

    fired = distinct_devices >= min_distinct_devices
    evidence: dict[str, Any] = {
        "window_start": window_start.isoformat(),
        "window_end": at.isoformat(),
        "radius_meters": radius_meters,
        "distinct_devices": distinct_devices,
        "total_reports": total_reports,
        "min_distinct_devices": min_distinct_devices,
        "centre": {"latitude": latitude, "longitude": longitude},
    }
    if fired:
        evidence["recent_complaint_ids"] = await _recent_ids_within(
            session, tenant_id=tenant_id, where=within, exclude=complaint_id
        )
    return AbuseFinding(
        pattern=AbusePattern.GEOGRAPHIC_CLUSTER,
        fired=fired,
        observation_count=distinct_devices,
        window_hours=window_hours,
        trust_delta=trust_delta if fired else 0.0,
        reason=(
            f"{distinct_devices} distinct devices reported within {radius_meters:g} m "
            f"in {window_hours:g}h, at or above the threshold of {min_distinct_devices}"
            if fired
            else f"{distinct_devices} distinct devices reported within "
            f"{radius_meters:g} m in {window_hours:g}h, below the threshold of "
            f"{min_distinct_devices}"
        ),
        evidence=evidence,
    )


async def _recent_ids_for_device(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    device_fingerprint: str,
    window_start: datetime,
    at: datetime,
    exclude: uuid.UUID,
) -> list[str]:
    statement = (
        select(Complaint.id)
        .where(
            Complaint.tenant_id == tenant_id,
            Complaint.submitter_device_fingerprint == device_fingerprint,
            Complaint.reported_at >= window_start,
            Complaint.reported_at <= at,
            Complaint.id != exclude,
        )
        .order_by(Complaint.reported_at.desc())
        .limit(EVIDENCE_SAMPLE_LIMIT)
    )
    return [str(value) for value in (await session.execute(statement)).scalars()]


async def _recent_ids_within(
    session: AsyncSession, *, tenant_id: uuid.UUID, where: Any, exclude: uuid.UUID
) -> list[str]:
    """Sample ids for a cluster finding.

    Takes the *window* predicate composed by the caller — so the radius and the
    time bounds cannot drift between the count and the sample — and writes the
    tenant filter here, inline. That split is deliberate: the part that must be
    identical in both queries is shared, and the part an isolation check has to
    be able to read is visible at both call sites.
    """
    statement = (
        select(Complaint.id)
        .where(Complaint.tenant_id == tenant_id, where, Complaint.id != exclude)
        .order_by(Complaint.reported_at.desc())
        .limit(EVIDENCE_SAMPLE_LIMIT)
    )
    return [str(value) for value in (await session.execute(statement)).scalars()]


__all__ = [
    "EVIDENCE_SAMPLE_LIMIT",
    "AbuseFinding",
    "AbusePattern",
    "assess_device_velocity",
    "assess_geographic_cluster",
]
