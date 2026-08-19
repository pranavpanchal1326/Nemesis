"""Assembling the two configurations a comparison is between.

Written after reading the coverage report rather than after a failure — the same
route that found Phase 6's untested `update_draft`. `bundles.bundle_at` is the
function a dispute resolution runs ("what was deciding on 14 March?") and the
one shadow mode's historical counterpart will need, and nothing exercised it.

The properties that matter here are about *which* document gets picked up:

- `live_bundle` reads through, never through the resolver's TTL cache.
- `candidate_bundle` reads a specific revision **including a draft**, because
  evaluating a document before anyone approves it is the entire point.
- `bundle_at` answers an interval query, which is single-valued only because
  rollback moves forward rather than reviving a row (ADR-0026).
- Neither ever makes a draft live, and a test asserts it directly.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from nemesis.policy import baselines
from nemesis.policy import service as policy_service
from nemesis.policy.documents import PolicyKind
from nemesis.simulation import bundles, runs
from nemesis.simulation.errors import SimulationNotFoundError
from nemesis.tenancy.context import tenant_scope
from tests.conftest import postgres_required
from tests.test_simulation_corpus import BASE, seed_complaint
from tests.test_simulation_evaluation import draft_and_approve, rubric

pytestmark = [postgres_required, pytest.mark.integration]

KIND = PolicyKind.SEVERITY_RUBRIC


@asynccontextmanager
async def scoped(engine: AsyncEngine, tenant_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    with tenant_scope(tenant_id):
        async with maker() as session:
            yield session
            await session.commit()


# ---------------------------------------------------------------------------
# live_bundle
# ---------------------------------------------------------------------------


async def test_a_live_bundle_carries_every_kind_that_has_a_baseline(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Four required kinds always resolve; two optional ones may not.

    Routing rules and rate cards name departments and negotiated prices the
    platform cannot invent, so a tenant that has authored neither still scores,
    still triages, and still has SLAs.

    Phase 8 added ``trust_thresholds`` as a fifth baselined kind. It appears in
    ``stamps()`` — the bundle names every document in force — while
    ``DECIDABLE_KINDS`` keeps it out of what ``decide`` reads. The assertion
    below is written against ``baselines.SEEDED_KINDS`` rather than a literal
    set for exactly that reason: a sixth baselined kind should extend this test,
    not fail it, while a change to what the *engine consumes* has its own test.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        await policy_service.seed_baselines(session, tenant_id=tenant_id)
        bundle = await bundles.live_bundle(session, tenant_id=tenant_id)

    assert bundle.severity_rubric.revision == 1
    assert bundle.dedup_thresholds.revision == 1
    assert bundle.safety_ruleset.revision == 1
    assert bundle.sla_matrix.revision == 1
    assert bundle.trust_thresholds is not None
    assert bundle.trust_thresholds.revision == 1
    assert bundle.routing_rules is None
    assert bundle.rate_card is None
    assert set(bundle.stamps()) == {kind.value for kind in baselines.SEEDED_KINDS}
    assert PolicyKind.TRUST_THRESHOLDS.value in bundle.stamps()


async def test_an_unseeded_tenant_resolves_to_baselines(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A tenant that predates Phase 6 must still be backtestable.

    Refusing to run for exactly the tenants most likely to need a report would
    be a strange kind of safety.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        bundle = await bundles.live_bundle(session, tenant_id=tenant_id)

    assert bundle.severity_rubric.is_baseline is True
    assert bundle.severity_rubric.revision is None
    assert bundle.stamps()[PolicyKind.SEVERITY_RUBRIC.value] == "baseline"


# ---------------------------------------------------------------------------
# candidate_bundle
# ---------------------------------------------------------------------------


async def test_a_candidate_bundle_swaps_exactly_one_kind(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """One kind, never several.

    A bundle carrying two candidate documents produces a report whose rows
    cannot be attributed — "forty complaints changed department" is useless when
    both the routing rules and the rubric feeding them moved.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        await policy_service.seed_baselines(session, tenant_id=tenant_id)
        revision = await draft_and_approve(session, tenant_id=tenant_id, body=rubric(visual=0.9))
        bundle = await bundles.candidate_bundle(
            session, tenant_id=tenant_id, kind=KIND, revision=revision
        )

    assert bundle.severity_rubric.revision == revision
    assert bundle.sla_matrix.revision == 1, "every other kind stays as it is live"
    assert bundle.dedup_thresholds.revision == 1


async def test_a_candidate_may_be_an_unapproved_draft(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The whole point: the report exists so somebody can decide whether to approve.

    And evaluating one must not weaken Phase 6's gate clause — the live document
    is unchanged afterwards.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        await policy_service.seed_baselines(session, tenant_id=tenant_id)
        draft = await policy_service.draft(
            session,
            tenant_id=tenant_id,
            kind=KIND,
            body=rubric(visual=0.99),
            change_reason="never approved",
        )
        bundle = await bundles.candidate_bundle(
            session, tenant_id=tenant_id, kind=KIND, revision=draft.revision
        )
        live = await policy_service.active_version(session, tenant_id=tenant_id, kind=KIND)

    assert bundle.severity_rubric.revision == draft.revision
    assert live is not None
    assert live.revision == 1


async def test_an_unknown_revision_is_a_not_found(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    async with scoped(migrated_engine, tenant_id) as session:
        await policy_service.seed_baselines(session, tenant_id=tenant_id)
        with pytest.raises(SimulationNotFoundError):
            await bundles.candidate_bundle(session, tenant_id=tenant_id, kind=KIND, revision=999)


async def test_another_tenants_revision_is_invisible(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID, other_tenant_id: uuid.UUID
) -> None:
    """Not found, not forbidden — a revision number must not enumerate a neighbour."""
    async with scoped(migrated_engine, other_tenant_id) as session:
        await policy_service.seed_baselines(session, tenant_id=other_tenant_id)
        revision = await draft_and_approve(
            session, tenant_id=other_tenant_id, body=rubric(visual=0.9)
        )

    async with scoped(migrated_engine, tenant_id) as session:
        await policy_service.seed_baselines(session, tenant_id=tenant_id)
        with pytest.raises(SimulationNotFoundError):
            await bundles.candidate_bundle(
                session, tenant_id=tenant_id, kind=KIND, revision=revision
            )


# ---------------------------------------------------------------------------
# bundle_at — the interval query
# ---------------------------------------------------------------------------


async def test_bundle_at_reconstructs_what_was_deciding_then(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The query a dispute runs, and it is single-valued only because rollback
    moves forward (ADR-0026).

    With re-activation the effective-date intervals would overlap and this would
    have no one answer.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        await policy_service.seed_baselines(session, tenant_id=tenant_id)
        before_second = datetime.now(tz=UTC)

        revision = await draft_and_approve(session, tenant_id=tenant_id, body=rubric(visual=0.9))
        await policy_service.activate(
            session, tenant_id=tenant_id, kind=KIND, revision=revision, reason="second"
        )
        after_second = datetime.now(tz=UTC) + timedelta(seconds=1)

        earlier = await bundles.bundle_at(session, tenant_id=tenant_id, moment=before_second)
        later = await bundles.bundle_at(session, tenant_id=tenant_id, moment=after_second)

    assert earlier.severity_rubric.revision == 1
    assert later.severity_rubric.revision == revision
    assert earlier.severity_rubric.stamp != later.severity_rubric.stamp


async def test_bundle_at_before_any_document_falls_back_to_the_baseline(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Stamped `baseline`, not with a plausible-looking revision.

    That is what the resolver would have done at the time, and a reconstruction
    that used today's document instead would be a fabrication with a version
    number on it.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        await policy_service.seed_baselines(session, tenant_id=tenant_id)
        ancient = datetime(2020, 1, 1, tzinfo=UTC)
        bundle = await bundles.bundle_at(session, tenant_id=tenant_id, moment=ancient)

    assert bundle.severity_rubric.is_baseline is True
    assert bundle.severity_rubric.revision is None
    assert bundle.routing_rules is None, "an optional kind with no baseline stays absent"


async def test_replace_kind_leaves_every_other_kind_alone(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    async with scoped(migrated_engine, tenant_id) as session:
        await policy_service.seed_baselines(session, tenant_id=tenant_id)
        live = await bundles.live_bundle(session, tenant_id=tenant_id)

    swapped = bundles.replace_kind(live, kind=PolicyKind.SLA_MATRIX, resolved=live.sla_matrix)
    assert swapped.stamps() == live.stamps()
    assert swapped.severity_rubric is live.severity_rubric


# ---------------------------------------------------------------------------
# Run readers
# ---------------------------------------------------------------------------


async def test_runs_are_listed_newest_first_and_filtered_by_kind(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    async with scoped(migrated_engine, tenant_id) as session:
        await policy_service.seed_baselines(session, tenant_id=tenant_id)
        for index in range(40):
            await seed_complaint(session, tenant_id=tenant_id, at=BASE + timedelta(hours=index))
        revision = await draft_and_approve(session, tenant_id=tenant_id, body=rubric(visual=0.9))
        window = runs.default_window(now=BASE + timedelta(days=30))
        run, certificate = await runs.run_backtest(
            session,
            tenant_id=tenant_id,
            kind=KIND,
            revision=revision,
            window=window,
        )

        listed = await runs.list_runs(session, tenant_id=tenant_id, kind=KIND)
        other_kind = await runs.list_runs(session, tenant_id=tenant_id, kind=PolicyKind.RATE_CARD)
        fetched = await runs.require_run(session, tenant_id=tenant_id, run_id=run.id)

    assert certificate is None, "no certify flag means nothing is claimed"
    assert [entry.id for entry in listed] == [run.id]
    assert other_kind == []
    assert fetched.report is not None
    assert fetched.case_count == 40


async def test_an_unknown_run_is_a_not_found(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    async with scoped(migrated_engine, tenant_id) as session:
        with pytest.raises(SimulationNotFoundError):
            await runs.require_run(session, tenant_id=tenant_id, run_id=uuid.uuid4())


async def test_the_latest_certificate_is_the_newest_verdict_pass_or_fail(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """What the policy screen shows next to a draft.

    An author who sees "evaluated, failed on 4 of 40" before submitting for
    review does not need an approver's activation to be refused to find out.
    """
    from tests.test_simulation_evaluation import published_set_with_label

    async with scoped(migrated_engine, tenant_id) as session:
        await policy_service.seed_baselines(session, tenant_id=tenant_id)
        complaint_id = await seed_complaint(session, tenant_id=tenant_id, at=BASE)
        await published_set_with_label(session, tenant_id=tenant_id, complaint_id=complaint_id)
        revision = await draft_and_approve(session, tenant_id=tenant_id, body=rubric(visual=0.5))
        version = await policy_service.require_version(
            session, tenant_id=tenant_id, kind=KIND, revision=revision
        )
        content_hash = version.content_hash

        assert (
            await runs.latest_certificate(
                session, tenant_id=tenant_id, kind=KIND, content_hash=content_hash
            )
            is None
        ), "nothing has been claimed about this candidate yet"

        from nemesis.simulation import evaluation

        await evaluation.evaluate_candidate(
            session, tenant_id=tenant_id, kind=KIND, revision=revision
        )
        latest = await runs.latest_certificate(
            session, tenant_id=tenant_id, kind=KIND, content_hash=content_hash
        )

    assert latest is not None
    assert latest.candidate_revision == revision


def test_the_default_window_is_twelve_months_ending_at_the_given_instant() -> None:
    """`now` is a parameter so a test can pin it.

    It is also the only clock read in the runs module, and it happens where
    somebody can see it — a window is a decision about scope.
    """
    pinned = datetime(2026, 8, 19, 12, tzinfo=UTC)
    window = runs.default_window(now=pinned)

    assert window.end == pinned
    assert window.start == pinned - timedelta(days=runs.DEFAULT_WINDOW_DAYS)
    assert round(window.days) == runs.DEFAULT_WINDOW_DAYS
