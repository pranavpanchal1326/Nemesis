"""Shadow mode — the "provably cannot mutate state" clause.

Phase 7's third gate clause is *shadow mode provably cannot mutate state or emit
domain events*, and "provably" rules out the test everybody writes first: run
shadow mode, then assert the complaint table is unchanged. That proves the code
did not write **today**. It says nothing about the helper somebody adds next
year three frames down.

So the guarantee is two independent mechanisms, and this file tests each of them
**separately, by disabling the other** — because two layers that are only ever
tested together are one layer with a spare.

- ``test_the_statement_guard_refuses_a_write``: the guard alone, on a session
  where Postgres would have allowed it.
- ``test_postgres_refuses_a_write_the_guard_cannot_see``: raw ``text()`` SQL,
  which the guard is explicitly unable to parse, refused by the database.

The rest covers what shadow mode is *for*: it observes, it records what
differed, it is idempotent under a restart, and its kill switch works.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from nemesis.db.models.complaint import Complaint
from nemesis.db.models.event import Event
from nemesis.db.models.simulation import ShadowObservation
from nemesis.policy import service as policy_service
from nemesis.policy.documents import PolicyKind
from nemesis.simulation import shadow
from nemesis.simulation.errors import ShadowWriteError
from nemesis.simulation.readonly import read_only
from nemesis.tenancy.context import tenant_scope
from tests.conftest import postgres_required
from tests.test_simulation_corpus import BASE, seed_complaint
from tests.test_simulation_evaluation import draft_and_approve, rubric

pytestmark = [postgres_required, pytest.mark.integration]

KIND = PolicyKind.SEVERITY_RUBRIC


@pytest.fixture(autouse=True)
async def _close_flags_after_each_test() -> AsyncIterator[None]:
    """Release the feature-flag singleton this module causes to be built.

    ``shadow.observe`` consults the kill switch, so calling it lazily
    constructs the process-wide ``FeatureFlags`` — and outside the ``api_client``
    fixture nothing tears that down. The Redis client is then finalised by the
    garbage collector after the event loop has closed, which
    ``filterwarnings = ["error"]`` turns into a ``PytestUnraisableExceptionWarning``
    attributed to whichever unrelated test happened to be running. Intermittent,
    and it blames the wrong test every time — the same failure mode the
    ``client`` fixture's docstring describes.

    Closing per test rather than per session is deliberate: the store is built
    on *this* test's event loop, and awaiting its ``close()`` from a later one
    raises "Event loop is closed".
    """
    yield
    from nemesis.flags import close_flags

    await close_flags()


@asynccontextmanager
async def scoped(engine: AsyncEngine, tenant_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    with tenant_scope(tenant_id):
        async with maker() as session:
            yield session
            await session.commit()


# ---------------------------------------------------------------------------
# The two layers, tested apart
# ---------------------------------------------------------------------------


async def test_the_statement_guard_refuses_a_write(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Layer one, alone: the error names the statement rather than the driver.

    An asyncpg ``ReadOnlySqlTransactionError`` several frames away tells a
    developer that *something* in a forty-statement run wrote. This tells them
    which one, before the database is touched.
    """
    async with scoped(migrated_engine, tenant_id) as session, read_only(session) as reader:
        with pytest.raises(ShadowWriteError) as excinfo:
            await reader.execute(
                ShadowObservation.__table__.insert().values(
                    tenant_id=tenant_id,
                    complaint_id=uuid.uuid4(),
                    kind=KIND.value,
                    candidate_content_hash="0" * 64,
                    live_digest="1" * 64,
                    candidate_digest="2" * 64,
                    diverged=False,
                )
            )

    assert "INSERT" in str(excinfo.value)
    assert "shadow evaluation" in str(excinfo.value)


async def test_postgres_refuses_a_write_the_guard_cannot_see(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Layer two, alone: raw SQL, which the statement guard explicitly cannot parse.

    ``text()`` is the hole in every AST-shaped defence in this codebase — the
    tenancy guard says so about itself. Here the database closes it, which is
    the whole reason both layers exist rather than the nicer one.
    """
    async with scoped(migrated_engine, tenant_id) as session, read_only(session) as reader:
        with pytest.raises(DBAPIError) as excinfo:
            await reader.execute(
                text(
                    "INSERT INTO simulation_runs (tenant_id) VALUES (CAST(:tenant AS uuid))"
                ).bindparams(tenant=str(tenant_id))
            )

    assert "read-only" in str(excinfo.value).lower()


async def test_a_read_is_still_a_read(migrated_engine: AsyncEngine, tenant_id: uuid.UUID) -> None:
    """The guarantee must not be "nothing works"."""
    async with scoped(migrated_engine, tenant_id) as session, read_only(session) as reader:
        total = await reader.execute(
            select(func.count()).select_from(Complaint).where(Complaint.tenant_id == tenant_id)
        )
    assert int(total.scalar() or 0) == 0


async def test_a_nested_scope_does_not_unguard_the_outer_one(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A helper that also asks for read-only must not lift the outer guarantee.

    Each scope owns its own connection, so the inner one's ``event.remove``
    cannot reach the outer one's listener — but that is a property of the
    current implementation rather than of the interface, and this is the test
    that would fail if a future version went back to sharing a connection and
    unguarded the remainder of the outer block on the inner exit.
    """
    async with scoped(migrated_engine, tenant_id) as session, read_only(session) as outer:
        async with read_only(outer):
            pass
        with pytest.raises(ShadowWriteError):
            await outer.execute(
                ShadowObservation.__table__.insert().values(
                    tenant_id=tenant_id,
                    complaint_id=uuid.uuid4(),
                    kind=KIND.value,
                    candidate_content_hash="0" * 64,
                    live_digest="1" * 64,
                    candidate_digest="2" * 64,
                    diverged=False,
                )
            )


async def test_the_guard_is_lifted_when_the_scope_ends(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Scoped to a block, not to a process.

    A read-only mode somebody can leave on is a read-only mode whose failure
    symptom is that production stops recording complaints.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        async with read_only(session):
            pass
        # The caller's session was never marked read-only, so it can still write.
        await seed_complaint(session, tenant_id=tenant_id, at=BASE)
        total = await session.execute(
            select(func.count()).select_from(Event).where(Event.tenant_id == tenant_id)
        )
    assert int(total.scalar() or 0) > 0


# ---------------------------------------------------------------------------
# Observing
# ---------------------------------------------------------------------------


async def test_observing_emits_no_event_and_writes_no_domain_row(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The gate clause as an end-to-end assertion, on top of the structural ones."""
    async with scoped(migrated_engine, tenant_id) as session:
        await policy_service.seed_baselines(session, tenant_id=tenant_id)
        complaint_id = await seed_complaint(session, tenant_id=tenant_id, at=BASE)
        revision = await draft_and_approve(session, tenant_id=tenant_id, body=rubric(visual=0.95))
        # `observe` reads on its own connection, so the setup has to be visible
        # to other readers before it can see it. See `simulation.readonly`.
        await session.commit()

        before = await session.execute(
            select(func.count()).select_from(Event).where(Event.tenant_id == tenant_id)
        )
        observations = await shadow.observe(
            session,
            tenant_id=tenant_id,
            kind=KIND,
            revision=revision,
            complaint_ids=[complaint_id],
        )
        after = await session.execute(
            select(func.count()).select_from(Event).where(Event.tenant_id == tenant_id)
        )

    assert len(observations) == 1
    assert int(before.scalar() or 0) == int(after.scalar() or 0)


async def test_a_divergent_observation_stores_what_differed(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Agreement is counted; divergence is stored.

    Storing both outcomes for every agreement would make this the largest table
    in the system inside a week, for information the digests already carry.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        await policy_service.seed_baselines(session, tenant_id=tenant_id)
        complaint_id = await seed_complaint(session, tenant_id=tenant_id, at=BASE)
        revision = await draft_and_approve(session, tenant_id=tenant_id, body=rubric(visual=0.99))
        await session.commit()
        observations = await shadow.observe(
            session,
            tenant_id=tenant_id,
            kind=KIND,
            revision=revision,
            complaint_ids=[complaint_id],
        )
        written = await shadow.record(session, tenant_id=tenant_id, observations=observations)
        summary = await shadow.summarise(
            session,
            tenant_id=tenant_id,
            content_hash=observations[0].candidate_content_hash,
        )

    assert written == 1
    assert summary.observed == 1
    if observations[0].diverged:
        assert observations[0].difference is not None
        assert summary.diverged == 1
        assert set(summary.fields) <= set(observations[0].difference)
    else:
        assert observations[0].difference is None


async def test_recording_the_same_observation_twice_is_a_no_op(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A worker restarting mid-batch must not double every count.

    The divergence rate is the one number this table exists to produce, and a
    read-then-write would let two workers both see "absent" and both insert.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        await policy_service.seed_baselines(session, tenant_id=tenant_id)
        complaint_id = await seed_complaint(session, tenant_id=tenant_id, at=BASE)
        revision = await draft_and_approve(session, tenant_id=tenant_id, body=rubric(visual=0.99))
        await session.commit()
        observations = await shadow.observe(
            session,
            tenant_id=tenant_id,
            kind=KIND,
            revision=revision,
            complaint_ids=[complaint_id],
        )
        first = await shadow.record(session, tenant_id=tenant_id, observations=observations)
        second = await shadow.record(session, tenant_id=tenant_id, observations=observations)
        summary = await shadow.summarise(
            session,
            tenant_id=tenant_id,
            content_hash=observations[0].candidate_content_hash,
        )

    assert (first, second) == (1, 0)
    assert summary.observed == 1


async def test_the_kill_switch_stops_observation_without_changing_a_decision(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Killed, it records nothing — and un-makes nothing, because it decided nothing."""
    import nemesis.flags as flags_module
    from nemesis.flags import FeatureFlags, FlagOverride, MemoryFlagStore

    # The singleton is swapped and restored, never closed. Closing it would
    # reach a Redis connection opened on an earlier test's event loop, and
    # awaiting that from this one raises "Event loop is closed" — a failure with
    # nothing to do with feature flags, attributed to whichever test ran second.
    #
    # It is swapped rather than mutated because `get_flags` caches an evaluator
    # behind its own TTL: an override written into the store underneath would
    # not be seen until the interval elapsed, and a test that waits out a TTL is
    # sometimes flaky and always slow.
    previous = flags_module._flags
    flags_module._flags = FeatureFlags(
        MemoryFlagStore({"simulation_shadow_mode": FlagOverride(enabled=False)}),
        reload_interval_seconds=0.0,
    )
    try:
        async with scoped(migrated_engine, tenant_id) as session:
            await policy_service.seed_baselines(session, tenant_id=tenant_id)
            complaint_id = await seed_complaint(session, tenant_id=tenant_id, at=BASE)
            revision = await draft_and_approve(
                session, tenant_id=tenant_id, body=rubric(visual=0.99)
            )
            await session.commit()
            observations = await shadow.observe(
                session,
                tenant_id=tenant_id,
                kind=KIND,
                revision=revision,
                complaint_ids=[complaint_id],
            )
    finally:
        flags_module._flags = previous

    assert observations == []


async def test_a_shadow_observation_is_scoped_to_its_tenant(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID, other_tenant_id: uuid.UUID
) -> None:
    async with scoped(migrated_engine, tenant_id) as session:
        await policy_service.seed_baselines(session, tenant_id=tenant_id)
        complaint_id = await seed_complaint(session, tenant_id=tenant_id, at=BASE)
        revision = await draft_and_approve(session, tenant_id=tenant_id, body=rubric(visual=0.99))
        await session.commit()
        observations = await shadow.observe(
            session,
            tenant_id=tenant_id,
            kind=KIND,
            revision=revision,
            complaint_ids=[complaint_id],
        )
        await shadow.record(session, tenant_id=tenant_id, observations=observations)
        content_hash = observations[0].candidate_content_hash

    async with scoped(migrated_engine, other_tenant_id) as session:
        summary = await shadow.summarise(
            session, tenant_id=other_tenant_id, content_hash=content_hash
        )

    assert summary.observed == 0


async def test_observing_a_draft_leaves_the_live_document_untouched(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Evaluating an unapproved draft does not weaken Phase 6's gate clause.

    A candidate ``Resolved`` is a value that lives for the duration of a call;
    nothing here reaches the resolver's cache or the ``active`` row.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        await policy_service.seed_baselines(session, tenant_id=tenant_id)
        complaint_id = await seed_complaint(session, tenant_id=tenant_id, at=BASE)
        draft = await policy_service.draft(
            session,
            tenant_id=tenant_id,
            kind=KIND,
            body=rubric(visual=0.99),
            change_reason="never approved",
        )
        await session.commit()
        await shadow.observe(
            session,
            tenant_id=tenant_id,
            kind=KIND,
            revision=draft.revision,
            complaint_ids=[complaint_id],
        )
        live = await policy_service.active_version(session, tenant_id=tenant_id, kind=KIND)

    assert live is not None
    assert live.revision == 1, "the draft is revision 2 and must not have become live"


def test_the_observation_table_is_read_by_no_decision_path() -> None:
    """Walked as imports, not as a grep.

    An observation that fed back into a decision would make shadow mode a slow
    rollout with no approval step — the one thing it must not become.
    """
    import ast
    import pathlib

    import nemesis.pipeline as pipeline_package
    import nemesis.policy as policy_package

    offenders: list[str] = []
    for package in (policy_package, pipeline_package):
        root = pathlib.Path(package.__file__).parent  # type: ignore[arg-type]
        for path in sorted(root.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = (
                    [alias.name for alias in node.names]
                    if isinstance(node, ast.ImportFrom | ast.Import)
                    else []
                )
                if (
                    isinstance(node, ast.ImportFrom)
                    and node.module == "nemesis.db.models.simulation"
                ):
                    offenders.extend(
                        f"{path.name}:{node.lineno}:{name}"
                        for name in names
                        if name == "ShadowObservation"
                    )

    assert offenders == [], f"a decision path imports ShadowObservation at {offenders}"


async def test_an_observation_records_which_configurations_it_compared(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A divergence attributed to "the policy at some point in March" is not attributable."""
    async with scoped(migrated_engine, tenant_id) as session:
        await policy_service.seed_baselines(session, tenant_id=tenant_id)
        complaint_id = await seed_complaint(
            session, tenant_id=tenant_id, at=BASE + timedelta(days=1)
        )
        revision = await draft_and_approve(session, tenant_id=tenant_id, body=rubric(visual=0.99))
        await session.commit()
        observations = await shadow.observe(
            session,
            tenant_id=tenant_id,
            kind=KIND,
            revision=revision,
            complaint_ids=[complaint_id],
        )

    stamps = observations[0].live_stamps
    assert stamps[PolicyKind.SEVERITY_RUBRIC.value] == "severity_rubric@1"
    assert PolicyKind.SLA_MATRIX.value in stamps, "the whole bundle, not just the kind under test"
    assert observations[0].candidate_revision == revision
