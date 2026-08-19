"""Reconstructing decision inputs from the log — against a real event store.

The claim this file defends is the one a backtest's honesty rests on: a
``DecisionCase`` carries what the pipeline *observed* and never what it
*decided*. Everything else in Phase 7 could be correct and the reports would
still be worthless if this were wrong, and the failure would be silent — a
corpus fed its own answers reports "no change" for every candidate, convincingly.

Real Postgres throughout: the fold is over a real hash chain, through
production's own projectors, because a corpus built from hand-made dictionaries
would prove nothing about the events the pipeline actually writes.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from nemesis.db.models.taxonomy import TaxonomyNode
from nemesis.events.store import EventStore
from nemesis.simulation import corpus
from nemesis.simulation.errors import CorpusTooSmallError, SimulationValidationError
from nemesis.tenancy.context import tenant_scope
from tests.conftest import postgres_required

pytestmark = [postgres_required, pytest.mark.integration]

BASE = datetime(2026, 1, 10, 9, 0, tzinfo=UTC)
WINDOW = corpus.CorpusWindow(
    start=datetime(2026, 1, 1, tzinfo=UTC), end=datetime(2027, 1, 1, tzinfo=UTC)
)


@asynccontextmanager
async def scoped(engine: AsyncEngine, tenant_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    with tenant_scope(tenant_id):
        async with maker() as session:
            yield session
            await session.commit()


async def seed_complaint(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    at: datetime,
    category: str = "pothole",
    text: str = "a deep pothole",
    components: dict[str, float] | None = None,
    score: float = 6.5,
    submitted_via: str = "whatsapp",
) -> uuid.UUID:
    """One complaint's full chain, the way the pipeline writes it."""
    complaint_id = uuid.uuid4()
    store = EventStore(session)
    await store.append(
        entity_id=complaint_id,
        event_type="complaint_submitted",
        payload={
            "latitude": 19.076,
            "longitude": 72.8777,
            "description_text": text,
            "locale": "en",
            "submitted_via": submitted_via,
        },
        occurred_at=at,
    )
    await store.append(
        entity_id=complaint_id,
        event_type="exif_check_completed",
        payload={"exif_present": True, "distance_meters": 4.0, "trust_delta": 0.3},
        occurred_at=at + timedelta(seconds=10),
    )
    await store.append(
        entity_id=complaint_id,
        event_type="classification_scored",
        payload={
            "category": category,
            "confidence": 0.9,
            "model_id": "clip",
            "prompt_set_version": "1",
        },
        occurred_at=at + timedelta(seconds=20),
    )
    await store.append(
        entity_id=complaint_id,
        event_type="severity_scored",
        payload={
            "score": score,
            "components": components or {"visual_damage": 7.0, "road_class": 5.0},
            "weights": {"visual_damage": 0.6, "road_class": 0.4},
            "policy_version": "severity_rubric@1",
        },
        occurred_at=at + timedelta(seconds=30),
    )
    return complaint_id


async def seed_many(
    session: AsyncSession, *, tenant_id: uuid.UUID, count: int, start: datetime = BASE
) -> list[uuid.UUID]:
    return [
        await seed_complaint(session, tenant_id=tenant_id, at=start + timedelta(hours=index))
        for index in range(count)
    ]


# ---------------------------------------------------------------------------
# Observations, not decisions
# ---------------------------------------------------------------------------


async def test_a_case_carries_measurements_and_never_the_score_they_produced(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The distinction the whole module exists to hold.

    ``severity_scored.components`` is what the stage measured and is a fair
    input to a replay; ``severity_scored.score`` is what a rubric concluded and
    is the thing under test. A case carrying the score would make every
    candidate agree with the incumbent.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        complaint_id = await seed_complaint(
            session,
            tenant_id=tenant_id,
            at=BASE,
            components={"visual_damage": 8.0, "road_class": 3.0},
            score=9.9,
        )
        cases, _ = await corpus.build_cases(
            session, tenant_id=tenant_id, identifiers=[complaint_id]
        )

    subject = cases[0]
    assert subject.measurements == {"visual_damage": 8.0, "road_class": 3.0}
    assert not hasattr(subject, "severity_score")
    assert not hasattr(subject, "severity_tier")
    assert not hasattr(subject, "department_code")


def test_the_case_type_declares_no_decision_field() -> None:
    """Asserted by field name, so the distinction survives a convenient shortcut.

    A ``DecisionCase`` that gained ``severity_score`` "just for the report" would
    make the corpus a source of the answers, and nothing else in the suite would
    fail.
    """
    from nemesis.simulation.engine import DecisionCase

    fields = set(DecisionCase.__dataclass_fields__)
    assert fields.isdisjoint(corpus.DECISION_KEYS)


async def test_observations_come_from_the_chain_including_the_submission_channel(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """``submitted_via`` is a declared routing fact the projector drops.

    Read from the event rather than by changing the projector: bumping
    ``PROJECTOR_VERSION`` invalidates every snapshot in the system, which is a
    large price for a batch job's convenience.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        await seed_complaint(session, tenant_id=tenant_id, at=BASE, submitted_via="whatsapp")
        built = await corpus.build_corpus(
            session, tenant_id=tenant_id, window=WINDOW, minimum_cases=1
        )

    subject = built.cases[0]
    assert subject.submitted_via == "whatsapp"
    assert subject.locale == "en"
    assert subject.description_text == "a deep pothole"
    assert subject.trust_score == pytest.approx(0.3)


async def test_the_lineage_is_resolved_against_the_taxonomy(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    async with scoped(migrated_engine, tenant_id) as session:
        session.add(
            TaxonomyNode(
                tenant_id=tenant_id,
                key="pothole",
                display_name="Pothole",
                path="roads/pothole",
                depth=1,
            )
        )
        await session.flush()
        await seed_complaint(session, tenant_id=tenant_id, at=BASE, category="pothole")
        built = await corpus.build_corpus(
            session, tenant_id=tenant_id, window=WINDOW, minimum_cases=1
        )

    assert built.cases[0].lineage == ("pothole", "roads")
    assert built.unknown_categories == ()


async def test_a_category_the_tenant_no_longer_defines_is_reported_not_dropped(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A retired category is a fact about the taxonomy, not about the rubric.

    Dropping the complaint would shrink the corpus silently; scoring it against
    the default band is what the resolver does in production.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        await seed_complaint(session, tenant_id=tenant_id, at=BASE, category="retired_key")
        built = await corpus.build_corpus(
            session, tenant_id=tenant_id, window=WINDOW, minimum_cases=1
        )

    assert built.unknown_categories == ("retired_key",)
    assert built.cases[0].lineage == ("retired_key",)


# ---------------------------------------------------------------------------
# The window, the floor, and the sampling
# ---------------------------------------------------------------------------


async def test_a_corpus_below_the_floor_is_refused_rather_than_reported(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The most important error in the module.

    A backtest over three complaints reports "no regressions" with exactly the
    same confidence whether or not that is true, and it arrives at the moment
    somebody is looking for permission to activate.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        await seed_many(session, tenant_id=tenant_id, count=3)
        with pytest.raises(CorpusTooSmallError) as excinfo:
            await corpus.build_corpus(session, tenant_id=tenant_id, window=WINDOW)

    assert "at least 30" in str(excinfo.value)


async def test_a_window_selects_complaints_not_events(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A report filed on the last day has its whole chain folded.

    Selecting events instead would truncate exactly the complaints nearest the
    boundary, and a truncated chain looks like a classification that never ran.
    """
    edge = datetime(2026, 12, 31, 23, 30, tzinfo=UTC)
    async with scoped(migrated_engine, tenant_id) as session:
        await seed_complaint(session, tenant_id=tenant_id, at=edge)
        built = await corpus.build_corpus(
            session, tenant_id=tenant_id, window=WINDOW, minimum_cases=1
        )

    assert len(built.cases) == 1
    assert built.cases[0].category == "pothole", "the classification event fell outside the window"


async def test_sampling_is_systematic_and_reported(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Taking the most recent N would turn twelve months into three weeks.

    Silently — the report would read identically. So the stride is recorded and
    the sample spans the ordering by submission time.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        await seed_many(session, tenant_id=tenant_id, count=40)
        built = await corpus.build_corpus(
            session, tenant_id=tenant_id, window=WINDOW, max_cases=10, minimum_cases=1
        )

    assert built.population == 40
    assert built.sampling_stride == 4
    assert built.is_sampled is True
    assert len(built.cases) == 10

    spread = max(case.reported_at for case in built.cases) - min(
        case.reported_at for case in built.cases
    )
    assert spread > timedelta(hours=30), "a sample that clusters at one end is not a sample"


async def test_the_same_window_produces_the_same_corpus_twice(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """ "Did this reproduce" has to have an answer."""
    async with scoped(migrated_engine, tenant_id) as session:
        await seed_many(session, tenant_id=tenant_id, count=40)
        first = await corpus.build_corpus(
            session, tenant_id=tenant_id, window=WINDOW, max_cases=10, minimum_cases=1
        )
        second = await corpus.build_corpus(
            session, tenant_id=tenant_id, window=WINDOW, max_cases=10, minimum_cases=1
        )

    assert sorted(str(case.complaint_id) for case in first.cases) == sorted(
        str(case.complaint_id) for case in second.cases
    )


def test_a_naive_window_boundary_is_refused() -> None:
    """ "The server's zone" is not a fact about the tenant."""
    with pytest.raises(SimulationValidationError):
        # The naive boundary is the subject of the test, not an oversight —
        # DTZ001 is exactly the mistake `CorpusWindow` exists to refuse at
        # runtime for callers the linter never sees.
        corpus.CorpusWindow(
            start=datetime(2026, 1, 1),  # noqa: DTZ001
            end=datetime(2027, 1, 1, tzinfo=UTC),
        )


def test_a_window_that_ends_before_it_starts_is_refused() -> None:
    with pytest.raises(SimulationValidationError):
        corpus.CorpusWindow(
            start=datetime(2027, 1, 1, tzinfo=UTC), end=datetime(2026, 1, 1, tzinfo=UTC)
        )


async def test_a_corpus_is_scoped_to_its_tenant(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID, other_tenant_id: uuid.UUID
) -> None:
    """One tenant's history can never enter another's report."""
    async with scoped(migrated_engine, other_tenant_id) as session:
        await seed_many(session, tenant_id=other_tenant_id, count=5)

    async with scoped(migrated_engine, tenant_id) as session:
        await seed_many(session, tenant_id=tenant_id, count=2)
        built = await corpus.build_corpus(
            session, tenant_id=tenant_id, window=WINDOW, minimum_cases=1
        )

    assert len(built.cases) == 2


# ---------------------------------------------------------------------------
# Dedup evidence
# ---------------------------------------------------------------------------


async def test_the_dedup_candidate_comes_from_the_cluster_chain(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Replayed against what the encoders produced, never re-embedded.

    Re-running the encoder would fold a year of *model* drift into a report
    about a *policy* change, and the two would be indistinguishable.
    """
    cluster_id = uuid.uuid4()
    async with scoped(migrated_engine, tenant_id) as session:
        seed = await seed_complaint(session, tenant_id=tenant_id, at=BASE)
        later = await seed_complaint(session, tenant_id=tenant_id, at=BASE + timedelta(hours=2))
        store = EventStore(session)
        await store.append(
            entity_id=cluster_id,
            event_type="cluster_created",
            payload={"seed_complaint_id": str(seed), "latitude": 19.0, "longitude": 72.8},
            occurred_at=BASE,
        )
        await store.append(
            entity_id=cluster_id,
            event_type="cluster_match_found",
            payload={
                "complaint_id": str(later),
                "geo_distance_meters": 12.0,
                "image_similarity": 0.91,
                "text_similarity": 0.77,
                "combined_confidence": 0.85,
                "policy_version": "dedup_thresholds@1",
                "report_count_after": 2,
            },
            occurred_at=BASE + timedelta(hours=2),
        )
        cases, _ = await corpus.build_cases(session, tenant_id=tenant_id, identifiers=[later])

    subject = cases[0]
    assert subject.dedup_candidate is not None
    assert subject.dedup_candidate.image_similarity == pytest.approx(0.91)
    assert subject.dedup_candidate.geo_distance_meters == pytest.approx(12.0)
    assert subject.dedup_candidate.candidate_last_reported_at == BASE
    assert subject.report_count == 2, "the count at routing time, not the cluster's total today"
