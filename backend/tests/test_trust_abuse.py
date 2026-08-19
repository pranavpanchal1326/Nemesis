"""§11.3 coordinated abuse — two detectors that must not be one.

§11.3's demo scope is "3 seeded fake accounts targeting one ward". These tests
are that scenario and its inverse: the detectors have to fire on coordination
and stay quiet on a street that genuinely flooded, and — the part §11.3 states
most firmly — they must **flag, never block**.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from nemesis.tenancy.context import tenant_scope
from nemesis.trust.abuse import (
    AbusePattern,
    assess_device_velocity,
    assess_geographic_cluster,
)
from tests.conftest import postgres_required
from tests.test_trust_review import make_complaint

pytestmark = [postgres_required, pytest.mark.integration]

BASE = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
JUNCTION = (18.5204, 73.8567)

#: ~1.5 km north. Comfortably outside the 150 m default cluster radius.
FAR = (18.5340, 73.8567)


@pytest.fixture
def sessions(migrated_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(migrated_engine, expire_on_commit=False)


VELOCITY = {"window_hours": 1.0, "max_submissions": 12, "trust_delta": -0.3}
CLUSTER = {
    "radius_meters": 150.0,
    "window_hours": 6.0,
    "min_distinct_devices": 4,
    "trust_delta": -0.25,
}


# ---------------------------------------------------------------------------
# Velocity — one device, many reports
# ---------------------------------------------------------------------------


async def test_a_normal_reporter_does_not_trip_velocity(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> None:
    """§26.4's own example: a citizen photographing three potholes on one street.

    The limit exists to stop automated flooding, not to pace an engaged
    reporter, and a detector that fired here would put the most useful citizens
    in the review queue.
    """
    with tenant_scope(tenant_id):
        async with sessions() as session:
            for index in range(3):
                await make_complaint(
                    session,
                    tenant_id=tenant_id,
                    reported_at=BASE + timedelta(minutes=index * 5),
                    device_fingerprint="device-a",
                )
            current = await make_complaint(
                session,
                tenant_id=tenant_id,
                reported_at=BASE + timedelta(minutes=20),
                device_fingerprint="device-a",
            )
            await session.commit()

            finding = await assess_device_velocity(
                session,
                tenant_id=tenant_id,
                complaint_id=current,
                device_fingerprint="device-a",
                at=BASE + timedelta(minutes=20),
                **VELOCITY,
            )

    assert finding is not None
    assert not finding.fired
    assert finding.observation_count == 4
    # Zero delta on a non-firing detector. A detector that moved trust for an
    # observation it did not consider suspicious would make the trust score
    # drift downward for every active reporter.
    assert finding.trust_delta == 0.0
    assert "within the limit" in finding.reason


async def test_a_flood_from_one_device_trips_velocity_with_evidence(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> None:
    """And the bundle names the other reports, which is what §11.4 requires."""
    with tenant_scope(tenant_id):
        async with sessions() as session:
            for index in range(15):
                await make_complaint(
                    session,
                    tenant_id=tenant_id,
                    reported_at=BASE + timedelta(minutes=index),
                    device_fingerprint="bot-1",
                )
            current = await make_complaint(
                session,
                tenant_id=tenant_id,
                reported_at=BASE + timedelta(minutes=20),
                device_fingerprint="bot-1",
            )
            await session.commit()

            finding = await assess_device_velocity(
                session,
                tenant_id=tenant_id,
                complaint_id=current,
                device_fingerprint="bot-1",
                at=BASE + timedelta(minutes=20),
                **VELOCITY,
            )

    assert finding is not None
    assert finding.fired
    assert finding.pattern is AbusePattern.DEVICE_VELOCITY
    assert finding.observation_count == 16
    assert finding.trust_delta == VELOCITY["trust_delta"]
    ids = finding.evidence["recent_complaint_ids"]
    assert len(ids) == 10  # EVIDENCE_SAMPLE_LIMIT
    assert str(current) not in ids


async def test_a_submission_with_no_fingerprint_is_not_assessed(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> None:
    """``None``, not a zero-count finding.

    §22 minimises what is collected and a citizen may block the fingerprint.
    Reporting its absence as ``observation_count = 0`` would put a number in the
    evidence bundle that reads as a measurement — and treating absence as
    evidence would penalise privacy.
    """
    with tenant_scope(tenant_id):
        async with sessions() as session:
            current = await make_complaint(session, tenant_id=tenant_id, reported_at=BASE)
            await session.commit()
            finding = await assess_device_velocity(
                session,
                tenant_id=tenant_id,
                complaint_id=current,
                device_fingerprint=None,
                at=BASE,
                **VELOCITY,
            )
    assert finding is None


async def test_velocity_does_not_count_another_tenants_reports(
    sessions: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
    other_tenant_id: uuid.UUID,
) -> None:
    """A shared fingerprint across two customers is entirely normal — one person
    reporting on their campus and in their city — and counting both would flag
    them in whichever tenant they used second."""
    with tenant_scope(other_tenant_id):
        async with sessions() as session:
            for index in range(20):
                await make_complaint(
                    session,
                    tenant_id=other_tenant_id,
                    reported_at=BASE + timedelta(minutes=index),
                    device_fingerprint="shared",
                )
            await session.commit()

    with tenant_scope(tenant_id):
        async with sessions() as session:
            current = await make_complaint(
                session, tenant_id=tenant_id, reported_at=BASE, device_fingerprint="shared"
            )
            await session.commit()
            finding = await assess_device_velocity(
                session,
                tenant_id=tenant_id,
                complaint_id=current,
                device_fingerprint="shared",
                at=BASE,
                **VELOCITY,
            )

    assert finding is not None
    assert finding.observation_count == 1
    assert not finding.fired


async def test_reports_outside_the_window_are_not_counted(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> None:
    """Twenty reports last week is an engaged citizen; twenty in an hour is not."""
    with tenant_scope(tenant_id):
        async with sessions() as session:
            for index in range(20):
                await make_complaint(
                    session,
                    tenant_id=tenant_id,
                    reported_at=BASE - timedelta(days=3, minutes=index),
                    device_fingerprint="device-b",
                )
            current = await make_complaint(
                session, tenant_id=tenant_id, reported_at=BASE, device_fingerprint="device-b"
            )
            await session.commit()
            finding = await assess_device_velocity(
                session,
                tenant_id=tenant_id,
                complaint_id=current,
                device_fingerprint="device-b",
                at=BASE,
                **VELOCITY,
            )
    assert finding is not None
    assert finding.observation_count == 1


# ---------------------------------------------------------------------------
# Geographic clustering — many devices, one place
# ---------------------------------------------------------------------------


async def test_seeded_coordination_on_one_junction_is_detected(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> None:
    """§11.3's demo scenario: several "different" users, one spot, one window."""
    with tenant_scope(tenant_id):
        async with sessions() as session:
            for index in range(4):
                await make_complaint(
                    session,
                    tenant_id=tenant_id,
                    reported_at=BASE + timedelta(minutes=index * 10),
                    latitude=JUNCTION[0] + index * 0.0002,
                    longitude=JUNCTION[1],
                    device_fingerprint=f"sock-{index}",
                )
            current = await make_complaint(
                session,
                tenant_id=tenant_id,
                reported_at=BASE + timedelta(hours=1),
                latitude=JUNCTION[0],
                longitude=JUNCTION[1],
                device_fingerprint="sock-4",
            )
            await session.commit()

            finding = await assess_geographic_cluster(
                session,
                tenant_id=tenant_id,
                complaint_id=current,
                latitude=JUNCTION[0],
                longitude=JUNCTION[1],
                at=BASE + timedelta(hours=1),
                **CLUSTER,
            )

    assert finding.fired
    assert finding.pattern is AbusePattern.GEOGRAPHIC_CLUSTER
    assert finding.observation_count == 5
    assert finding.evidence["distinct_devices"] == 5
    assert len(finding.evidence["recent_complaint_ids"]) == 4


async def test_one_device_reporting_repeatedly_is_not_a_cluster(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> None:
    """The reason there are two detectors and not one.

    Twenty reports from one device at one junction is the *velocity* signal. A
    single detector counting submissions in a window would fire here and could
    not tell a bot farm from one stuck client.
    """
    with tenant_scope(tenant_id):
        async with sessions() as session:
            for index in range(20):
                await make_complaint(
                    session,
                    tenant_id=tenant_id,
                    reported_at=BASE + timedelta(minutes=index),
                    latitude=JUNCTION[0],
                    longitude=JUNCTION[1],
                    device_fingerprint="one-device",
                )
            current = await make_complaint(
                session,
                tenant_id=tenant_id,
                reported_at=BASE + timedelta(hours=1),
                latitude=JUNCTION[0],
                longitude=JUNCTION[1],
                device_fingerprint="one-device",
            )
            await session.commit()
            finding = await assess_geographic_cluster(
                session,
                tenant_id=tenant_id,
                complaint_id=current,
                latitude=JUNCTION[0],
                longitude=JUNCTION[1],
                at=BASE + timedelta(hours=1),
                **CLUSTER,
            )

    assert not finding.fired
    assert finding.observation_count == 1
    assert finding.evidence["total_reports"] == 21


async def test_anonymous_submitters_are_not_counted_as_conspirators(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> None:
    """``COUNT(DISTINCT ...)`` skips NULLs, which is the behaviour wanted here.

    A hundred unidentified submitters must not add up to one coordination
    signal, for the same reason velocity declines to assess them at all.
    """
    with tenant_scope(tenant_id):
        async with sessions() as session:
            for index in range(10):
                await make_complaint(
                    session,
                    tenant_id=tenant_id,
                    reported_at=BASE + timedelta(minutes=index),
                    latitude=JUNCTION[0],
                    longitude=JUNCTION[1],
                    device_fingerprint=None,
                )
            current = await make_complaint(
                session,
                tenant_id=tenant_id,
                reported_at=BASE + timedelta(hours=1),
                latitude=JUNCTION[0],
                longitude=JUNCTION[1],
                device_fingerprint=None,
            )
            await session.commit()
            finding = await assess_geographic_cluster(
                session,
                tenant_id=tenant_id,
                complaint_id=current,
                latitude=JUNCTION[0],
                longitude=JUNCTION[1],
                at=BASE + timedelta(hours=1),
                **CLUSTER,
            )

    assert not finding.fired
    assert finding.observation_count == 0
    assert finding.evidence["total_reports"] == 11


async def test_reports_outside_the_radius_are_not_in_the_cluster(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> None:
    """A ward is not a point. Six devices spread over a kilometre and a half is
    a busy neighbourhood, not a bot farm on one junction."""
    with tenant_scope(tenant_id):
        async with sessions() as session:
            for index in range(6):
                await make_complaint(
                    session,
                    tenant_id=tenant_id,
                    reported_at=BASE + timedelta(minutes=index),
                    latitude=FAR[0],
                    longitude=FAR[1],
                    device_fingerprint=f"far-{index}",
                )
            current = await make_complaint(
                session,
                tenant_id=tenant_id,
                reported_at=BASE + timedelta(hours=1),
                latitude=JUNCTION[0],
                longitude=JUNCTION[1],
                device_fingerprint="near",
            )
            await session.commit()
            finding = await assess_geographic_cluster(
                session,
                tenant_id=tenant_id,
                complaint_id=current,
                latitude=JUNCTION[0],
                longitude=JUNCTION[1],
                at=BASE + timedelta(hours=1),
                **CLUSTER,
            )
    assert not finding.fired
    assert finding.observation_count == 1


async def test_a_tenant_can_widen_the_radius_with_no_code_change(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> None:
    """The same reports, two policies, two answers.

    A campus where every report is inside one compound needs a radius a city
    would find absurd, and neither should wait for a deploy — architectural
    principle 1, at the detector level.
    """
    with tenant_scope(tenant_id):
        async with sessions() as session:
            for index in range(5):
                await make_complaint(
                    session,
                    tenant_id=tenant_id,
                    reported_at=BASE + timedelta(minutes=index),
                    latitude=FAR[0],
                    longitude=FAR[1],
                    device_fingerprint=f"campus-{index}",
                )
            current = await make_complaint(
                session,
                tenant_id=tenant_id,
                reported_at=BASE + timedelta(hours=1),
                latitude=JUNCTION[0],
                longitude=JUNCTION[1],
                device_fingerprint="campus-5",
            )
            await session.commit()

            tight = await assess_geographic_cluster(
                session,
                tenant_id=tenant_id,
                complaint_id=current,
                latitude=JUNCTION[0],
                longitude=JUNCTION[1],
                at=BASE + timedelta(hours=1),
                **CLUSTER,
            )
            wide = await assess_geographic_cluster(
                session,
                tenant_id=tenant_id,
                complaint_id=current,
                latitude=JUNCTION[0],
                longitude=JUNCTION[1],
                at=BASE + timedelta(hours=1),
                **{**CLUSTER, "radius_meters": 5000.0},
            )

    assert not tight.fired
    assert wide.fired
    assert wide.observation_count == 6


async def test_the_cluster_search_never_crosses_a_tenant_boundary(
    sessions: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
    other_tenant_id: uuid.UUID,
) -> None:
    """Two customers can serve the same city. One's traffic must not flag the
    other's citizens."""
    with tenant_scope(other_tenant_id):
        async with sessions() as session:
            for index in range(8):
                await make_complaint(
                    session,
                    tenant_id=other_tenant_id,
                    reported_at=BASE + timedelta(minutes=index),
                    latitude=JUNCTION[0],
                    longitude=JUNCTION[1],
                    device_fingerprint=f"theirs-{index}",
                )
            await session.commit()

    with tenant_scope(tenant_id):
        async with sessions() as session:
            current = await make_complaint(
                session,
                tenant_id=tenant_id,
                reported_at=BASE + timedelta(hours=1),
                latitude=JUNCTION[0],
                longitude=JUNCTION[1],
                device_fingerprint="mine",
            )
            await session.commit()
            finding = await assess_geographic_cluster(
                session,
                tenant_id=tenant_id,
                complaint_id=current,
                latitude=JUNCTION[0],
                longitude=JUNCTION[1],
                at=BASE + timedelta(hours=1),
                **CLUSTER,
            )

    assert not finding.fired
    assert finding.observation_count == 1


def test_neither_detector_can_express_a_block() -> None:
    """§11.3 says *flags, does not auto-block*, kept true by construction.

    ``AbuseFinding`` has no field for an enforcement action and neither detector
    writes. A schema with a slot for a block invites one, and the first false
    positive would suppress a real citizen's report about a real hazard.
    """
    from dataclasses import fields

    from nemesis.trust.abuse import AbuseFinding

    names = {field.name for field in fields(AbuseFinding)}
    assert not names & {"blocked", "block", "action", "action_taken", "reject"}
