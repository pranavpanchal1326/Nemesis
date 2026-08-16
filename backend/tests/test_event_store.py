"""Event store integration tests — real Postgres, real locking, real partitions.

Nothing here is mocked. The two properties being tested — that concurrent
appends cannot fork a chain, and that a stored payload re-canonicalises
identically — are properties of Postgres row locking and ``jsonb`` round-tripping
respectively. A mocked datastore would assert that the mock behaves as expected,
which is not a fact about NEMESIS.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from nemesis.db.models.event import Event
from nemesis.domain.lifecycle import EntityType
from nemesis.events.canonical import canonicalise
from nemesis.events.hashing import GENESIS_HASH
from nemesis.events.registry import UnknownEventTypeError
from nemesis.events.store import EventStore
from nemesis.events.verify import BreakKind, verify_chain
from nemesis.tenancy.context import tenant_scope
from tests.conftest import postgres_required

pytestmark = postgres_required


def submission(latitude: float = 19.0760, longitude: float = 72.8777) -> dict[str, object]:
    return {
        "latitude": latitude,
        "longitude": longitude,
        "description_text": "Large pothole near the junction",
        "photo_url": "https://uploads.invalid/p1.jpg",
    }


@pytest.fixture
async def sessions(
    migrated_engine: AsyncEngine,
    tenants: tuple[uuid.UUID, uuid.UUID],
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Depends on ``tenants`` so the truncate-and-seed happens *before* any
    session exists. Without that ordering a session opened first would hold a
    transaction while another connection tried to TRUNCATE the same tables."""
    yield async_sessionmaker(bind=migrated_engine, expire_on_commit=False, autoflush=False)


@pytest.fixture
async def session(
    sessions: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with sessions() as active:
        yield active
        await active.rollback()


async def test_first_append_chains_from_genesis(
    session: AsyncSession, tenant_id: uuid.UUID
) -> None:
    store = EventStore(session)
    complaint_id = uuid.uuid4()

    with tenant_scope(tenant_id):
        event = await store.append(
            entity_id=complaint_id,
            event_type="complaint_submitted",
            payload=submission(),
        )

    assert event.sequence == 1
    assert event.previous_hash == GENESIS_HASH
    assert event.entity_type == EntityType.COMPLAINT
    assert len(event.event_hash) == 64


async def test_sequential_appends_link(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    store = EventStore(session)
    complaint_id = uuid.uuid4()

    with tenant_scope(tenant_id):
        first = await store.append(
            entity_id=complaint_id, event_type="complaint_submitted", payload=submission()
        )
        second = await store.append(
            entity_id=complaint_id,
            event_type="exif_check_completed",
            payload={"exif_present": True, "distance_meters": 4.2, "trust_delta": 0.1},
        )

    assert second.sequence == 2
    assert second.previous_hash == first.event_hash


async def test_stored_payload_recanonicalises_identically(
    session: AsyncSession, tenant_id: uuid.UUID
) -> None:
    """The property the whole chain rests on, tested against real ``jsonb``.

    Postgres reorders object keys, collapses duplicates, and stores every number
    as ``numeric``. If canonicalisation depended on any of that, verification
    would pass on write and fail on read — for every event, permanently.
    """
    store = EventStore(session)
    complaint_id = uuid.uuid4()
    payload = {
        "score": 7.0,  # whole-numbered float: comes back as an int
        "components": {"visual_damage": 0.30000000000000004, "road_class": 1e-7},
        "weights": {"z_last": 0.5, "a_first": 0.5},
        "policy_version": "severity_rubric_v1",
    }

    with tenant_scope(tenant_id):
        appended = await store.append(
            entity_id=complaint_id, event_type="severity_scored", payload=payload
        )
        await session.commit()

        stored = (
            await session.execute(
                text(
                    "SELECT payload FROM events WHERE tenant_id = :tenant AND id = :id"
                ).bindparams(tenant=tenant_id, id=appended.id)
            )
        ).scalar_one()

    assert canonicalise(stored) == canonicalise(appended.payload)

    with tenant_scope(tenant_id):
        result = await verify_chain(
            session,
            tenant_id=tenant_id,
            entity_type=EntityType.COMPLAINT,
            entity_id=complaint_id,
        )
    assert result.is_intact


async def test_redelivery_with_the_same_key_appends_nothing(
    session: AsyncSession, tenant_id: uuid.UUID
) -> None:
    store = EventStore(session)
    complaint_id = uuid.uuid4()

    with tenant_scope(tenant_id):
        first = await store.append(
            entity_id=complaint_id,
            event_type="complaint_submitted",
            payload=submission(),
            idempotency_key="test_idem_key_001",
        )
        await session.commit()
        second = await store.append(
            entity_id=complaint_id,
            event_type="complaint_submitted",
            payload=submission(),
            idempotency_key="test_idem_key_001",
        )

    assert second.was_redelivery is True
    assert second.id == first.id
    assert second.event_hash == first.event_hash

    with tenant_scope(tenant_id):
        head = await store.chain_head(entity_type=EntityType.COMPLAINT, entity_id=complaint_id)
    assert head is not None
    assert head[0] == 1


async def test_tampering_is_detected_at_the_exact_offset(
    session: AsyncSession, tenant_id: uuid.UUID
) -> None:
    """The gate: a deliberately altered row is found, and its neighbours are not.

    The assertion that only *one* content break appears matters as much as
    detection itself. A verifier that cascaded would report the whole chain as
    corrupt, which tells an on-call nothing about what happened or when.
    """
    store = EventStore(session)
    complaint_id = uuid.uuid4()

    with tenant_scope(tenant_id):
        for index in range(5):
            await store.append(
                entity_id=complaint_id,
                event_type="exif_check_completed" if index else "complaint_submitted",
                payload=submission()
                if not index
                else {"exif_present": True, "trust_delta": 0.1 * index},
            )
        await session.commit()

        await session.execute(
            text(
                "UPDATE events SET payload = jsonb_set(payload, '{trust_delta}', '99') "
                "WHERE tenant_id = :tenant AND entity_id = :entity AND sequence = 3"
            ).bindparams(tenant=tenant_id, entity=complaint_id)
        )
        await session.commit()

        result = await verify_chain(
            session,
            tenant_id=tenant_id,
            entity_type=EntityType.COMPLAINT,
            entity_id=complaint_id,
        )

    assert not result.is_intact
    assert result.events_checked == 5
    content_breaks = [b for b in result.breaks if b.kind is BreakKind.CONTENT_ALTERED]
    assert [b.sequence for b in content_breaks] == [3]


async def test_deleting_an_event_is_detected_as_a_gap(
    session: AsyncSession, tenant_id: uuid.UUID
) -> None:
    store = EventStore(session)
    complaint_id = uuid.uuid4()

    with tenant_scope(tenant_id):
        for index in range(4):
            await store.append(
                entity_id=complaint_id,
                event_type="complaint_submitted" if not index else "exif_check_completed",
                payload=submission() if not index else {"exif_present": True, "trust_delta": 0.0},
            )
        await session.commit()

        await session.execute(
            text(
                "DELETE FROM events WHERE tenant_id = :tenant AND entity_id = :entity "
                "AND sequence = 2"
            ).bindparams(tenant=tenant_id, entity=complaint_id)
        )
        await session.commit()

        result = await verify_chain(
            session,
            tenant_id=tenant_id,
            entity_type=EntityType.COMPLAINT,
            entity_id=complaint_id,
        )

    kinds = {b.kind for b in result.breaks}
    assert BreakKind.SEQUENCE_GAP in kinds
    assert BreakKind.LINK_BROKEN in kinds


async def test_concurrent_appends_never_fork_the_chain(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> None:
    """The Phase 2 gate: 1000 appends across 50 entities, zero forks.

    Each append runs in its own transaction on its own connection, which is what
    makes this a test of Postgres row locking rather than of asyncio scheduling.
    Twenty appends per entity, interleaved by the event loop, is the shape that
    breaks a read-then-write implementation of ``MAX(sequence) + 1``.
    """
    entity_count, per_entity = 50, 20
    entities = [uuid.uuid4() for _ in range(entity_count)]

    async def append_one(entity_id: uuid.UUID, index: int) -> None:
        # `tenant_scope` is a plain context manager, and it is set *inside* the
        # coroutine on purpose: asyncio.gather wraps each coroutine in a Task
        # with its own copy of the context, so a ContextVar set here cannot leak
        # into a sibling append — which is the property production relies on to
        # keep one request's tenant out of another's.
        async with sessions() as own_session:
            with tenant_scope(tenant_id):
                store = EventStore(own_session)
                await store.append(
                    entity_id=entity_id,
                    event_type="exif_check_completed",
                    payload={"exif_present": True, "trust_delta": float(index)},
                )
                await own_session.commit()

    await asyncio.gather(
        *(append_one(entity_id, index) for entity_id in entities for index in range(per_entity))
    )

    async with sessions() as verifier:
        with tenant_scope(tenant_id):
            for entity_id in entities:
                result = await verify_chain(
                    verifier,
                    tenant_id=tenant_id,
                    entity_type=EntityType.COMPLAINT,
                    entity_id=entity_id,
                )
                assert result.events_checked == per_entity, entity_id
                assert result.is_intact, (entity_id, result.breaks)


async def test_events_land_in_the_month_partition_not_the_default(
    session: AsyncSession, tenant_id: uuid.UUID
) -> None:
    """A row in the default partition is a latent outage, so it is asserted against."""
    store = EventStore(session)
    with tenant_scope(tenant_id):
        await store.append(
            entity_id=uuid.uuid4(), event_type="complaint_submitted", payload=submission()
        )
        await session.commit()

    in_default = (
        await session.execute(text("SELECT count(*) FROM ONLY events_default"))
    ).scalar_one()
    assert in_default == 0


async def test_occurred_at_and_recorded_at_are_distinct_concepts(
    session: AsyncSession, tenant_id: uuid.UUID
) -> None:
    """An offline field submission occurred long before it was recorded."""
    captured = datetime(2026, 8, 1, 6, 30, tzinfo=UTC)
    store = EventStore(session)

    with tenant_scope(tenant_id):
        event = await store.append(
            entity_id=uuid.uuid4(),
            event_type="complaint_submitted",
            payload=submission(),
            occurred_at=captured,
        )

    assert event.occurred_at == captured
    assert event.recorded_at > captured


async def test_unregistered_event_type_cannot_be_appended(
    session: AsyncSession, tenant_id: uuid.UUID
) -> None:
    store = EventStore(session)
    with (
        tenant_scope(tenant_id),
        pytest.raises(UnknownEventTypeError, match="not a registered event type"),
    ):
        await store.append(entity_id=uuid.uuid4(), event_type="totally_made_up", payload={})


async def test_payload_that_violates_its_schema_is_rejected(
    session: AsyncSession, tenant_id: uuid.UUID
) -> None:
    """Rejected before anything is written, not after a partial transaction.

    A latitude of 200 is off the planet. The specific exception type is asserted
    because a blind ``Exception`` here would also pass if the append failed for
    an unrelated reason — a connection error, a missing table — and the test
    would keep reporting that validation works after it had stopped.
    """
    store = EventStore(session)
    with tenant_scope(tenant_id), pytest.raises(ValidationError):
        await store.append(
            entity_id=uuid.uuid4(),
            event_type="complaint_submitted",
            payload={"latitude": 200.0, "longitude": 0.0},
        )

    remaining = (await session.execute(text("SELECT count(*) FROM events"))).scalar_one()
    assert remaining == 0


async def test_two_tenants_chains_are_independent(
    session: AsyncSession, tenant_id: uuid.UUID, other_tenant_id: uuid.UUID
) -> None:
    """The same entity id under two tenants must not share a chain.

    Not a hypothetical: a seeded demo tenant and a pilot tenant can easily be
    loaded from the same fixture file, and a shared chain would let one
    customer's event count and hash depend on another's.
    """
    store = EventStore(session)
    shared_id = uuid.uuid4()

    with tenant_scope(tenant_id):
        first = await store.append(
            entity_id=shared_id, event_type="complaint_submitted", payload=submission()
        )
    with tenant_scope(other_tenant_id):
        second = await store.append(
            entity_id=shared_id, event_type="complaint_submitted", payload=submission()
        )

    assert first.sequence == second.sequence == 1
    assert first.previous_hash == second.previous_hash == GENESIS_HASH
    # Same payload, same sequence, different tenant — and therefore a different
    # hash, because tenant_id is in the preimage (ADR-0010). Equal hashes here
    # would mean a row could be moved between tenants undetected.
    assert first.event_hash != second.event_hash


async def test_event_rows_are_never_updated_by_the_store(
    session: AsyncSession, tenant_id: uuid.UUID
) -> None:
    store = EventStore(session)
    complaint_id = uuid.uuid4()
    with tenant_scope(tenant_id):
        await store.append(
            entity_id=complaint_id, event_type="complaint_submitted", payload=submission()
        )
        await session.commit()
        rows = (
            (
                await session.execute(
                    text("SELECT xmin::text::bigint FROM events WHERE tenant_id = :t").bindparams(
                        t=tenant_id
                    )
                )
            )
            .scalars()
            .all()
        )

        await store.append(
            entity_id=complaint_id,
            event_type="exif_check_completed",
            payload={"exif_present": False, "trust_delta": -0.2},
        )
        await session.commit()
        after = (
            (
                await session.execute(
                    text(
                        "SELECT xmin::text::bigint FROM events WHERE tenant_id = :t "
                        "ORDER BY sequence LIMIT 1"
                    ).bindparams(t=tenant_id)
                )
            )
            .scalars()
            .all()
        )

    assert rows[0] == after[0], "appending must not rewrite an existing event row"


async def test_read_stream_returns_chain_order(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    store = EventStore(session)
    complaint_id = uuid.uuid4()
    with tenant_scope(tenant_id):
        await store.append(
            entity_id=complaint_id, event_type="complaint_submitted", payload=submission()
        )
        for index in range(3):
            await store.append(
                entity_id=complaint_id,
                event_type="exif_check_completed",
                payload={"exif_present": True, "trust_delta": float(index)},
            )
        stream: list[Event] = list(
            await store.read_stream(entity_type=EntityType.COMPLAINT, entity_id=complaint_id)
        )

    assert [e.sequence for e in stream] == [1, 2, 3, 4]
