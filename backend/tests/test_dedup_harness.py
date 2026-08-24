"""The measuring instrument, measured.

Phase 10's published precision and recall come out of ``dedup.harness``. If the
harness scores wrongly, the report is wrong in a way that no other test would
catch and that the numbers themselves would not look odd about — a harness that
never counts a false merge publishes "zero false merges" very convincingly.

So this file checks the instrument against situations whose answer is known by
construction: an encoder that makes same-incident text identical must yield
perfect recall, and one that makes *everything* identical must produce false
merges the harness actually reports. The second case matters more than the
first.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence

import pytest
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from nemesis.config import DedupSettings
from nemesis.db.models.complaint import TEXT_EMBEDDING_DIM
from nemesis.dedup import corpus as corpus_module
from nemesis.dedup.harness import Measurement, measure
from nemesis.perception.encoders import EncoderKind, encoder_scope
from tests.conftest import postgres_required
from tests.dedup_fixtures import unit_vector

pytestmark = [postgres_required, pytest.mark.integration]

SETTINGS = DedupSettings()


class _KeyedEncoder:
    """Embeds by a caller-chosen key rather than by meaning.

    The point of a fake here is control over the *similarity structure*, not
    realism: these tests assert what the harness does with a given structure,
    and a real encoder would make the structure an input nobody chose. Realism
    is `scripts/eval_dedup.py`'s job, which runs the same harness against
    multilingual-e5.
    """

    dimensions = TEXT_EMBEDDING_DIM
    footprint_bytes = 1024
    model_id = "fake-dedup-text@1"

    def __init__(self, key: object) -> None:
        self._key = key

    def encode(self, texts: Sequence[str], *, prefix: str) -> tuple[tuple[float, ...], ...]:
        del prefix
        return tuple(
            tuple(unit_vector(TEXT_EMBEDDING_DIM, seed=self._key(text)))  # type: ignore[operator]
            for text in texts
        )


@pytest.fixture
def sessions(migrated_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(migrated_engine, expire_on_commit=False)


@pytest.fixture
async def tenant(
    sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> AsyncIterator[uuid.UUID]:
    yield tenant_id


@pytest.fixture(scope="module")
def corpus() -> corpus_module.Corpus:
    return corpus_module.load()


def _by_incident(corpus: corpus_module.Corpus) -> _KeyedEncoder:
    """Identical vectors within an incident, orthogonal across incidents."""
    lookup = {report.text: report.incident_id for report in corpus.reports}
    incidents = {incident.id: float(index) for index, incident in enumerate(corpus.incidents)}
    return _KeyedEncoder(lambda text: incidents[lookup[text]])


async def run_measure(
    sessions: async_sessionmaker[AsyncSession],
    *,
    tenant: uuid.UUID,
    corpus: corpus_module.Corpus,
    encoder: _KeyedEncoder,
) -> Measurement:
    """Measure once under a given encoder, and leave the database as it was.

    A helper rather than an inline `async with` pair, because `encoder_scope` is
    a *sync* context manager and the session factory is an async one — the two
    cannot share a single `async with`, and nesting them at every call site
    buries the assertion four levels in.
    """
    with encoder_scope(EncoderKind.TEXT, encoder):
        async with sessions() as session:
            measurement = await measure(
                session,
                tenant_id=tenant,
                corpus=corpus,
                corpus_hash="test",
                settings=SETTINGS,
            )
            await session.rollback()
    return measurement


async def test_a_perfectly_separable_corpus_scores_perfectly(
    sessions: async_sessionmaker[AsyncSession],
    tenant: uuid.UUID,
    corpus: corpus_module.Corpus,
) -> None:
    """The instrument's zero point.

    Same incident, same vector; different incident, orthogonal vector. Anything
    less than perfect precision and recall here is the harness or the engine
    being wrong, because the input carries no ambiguity at all.
    """
    measurement = await run_measure(
        sessions, tenant=tenant, corpus=corpus, encoder=_by_incident(corpus)
    )

    assert measurement.false_merges == ()
    assert measurement.precision == 1.0
    assert measurement.recall == 1.0
    assert measurement.f1 == 1.0


async def test_an_encoder_that_cannot_tell_anything_apart_is_caught(
    sessions: async_sessionmaker[AsyncSession],
    tenant: uuid.UUID,
    corpus: corpus_module.Corpus,
) -> None:
    """The test that matters: can the harness report a false merge at all.

    Every report gets the same vector, so the only thing keeping two incidents
    apart is geography and category — and the corpus deliberately contains
    same-category incidents inside one radius. The harness must notice and say
    so, or its "zero false merges" is worthless.
    """
    measurement = await run_measure(
        sessions, tenant=tenant, corpus=corpus, encoder=_KeyedEncoder(lambda text: 1.0)
    )

    assert measurement.false_merges, "a blind encoder produced no false merge; the harness is blind"
    assert measurement.precision < 1.0
    for judgement in measurement.false_merges:
        assert judgement.false_merge_with
        assert judgement.correct is False


async def test_counts_and_rates_agree(
    sessions: async_sessionmaker[AsyncSession],
    tenant: uuid.UUID,
    corpus: corpus_module.Corpus,
) -> None:
    """Precision and recall must be derivable from the counts printed beside
    them. A report whose rates and tallies disagree is unarguable-with."""
    measurement = await run_measure(
        sessions, tenant=tenant, corpus=corpus, encoder=_by_incident(corpus)
    )

    total = (
        measurement.true_positives
        + measurement.false_positives
        + measurement.false_negatives
        + measurement.true_negatives
    )
    assert total == len(corpus.reports)
    assert len(measurement.judgements) == len(corpus.reports)
    assert measurement.false_positives == len(measurement.false_merges)
    assert measurement.p95_latency_ms is not None


async def test_the_first_report_of_an_incident_is_never_a_missed_merge(
    sessions: async_sessionmaker[AsyncSession],
    tenant: uuid.UUID,
    corpus: corpus_module.Corpus,
) -> None:
    """It has nothing to merge into. Counting it as a false negative would make
    recall depend on how many incidents the corpus happens to contain, which is
    a property of the fixture rather than of the engine."""
    measurement = await run_measure(
        sessions, tenant=tenant, corpus=corpus, encoder=_by_incident(corpus)
    )

    seen: set[str] = set()
    for judgement in measurement.judgements:
        if judgement.incident_id not in seen:
            assert judgement.correct, f"{judgement.report_id} was the first of its incident"
        seen.add(judgement.incident_id)


async def test_a_rerun_clears_the_previous_run(
    sessions: async_sessionmaker[AsyncSession],
    tenant: uuid.UUID,
    corpus: corpus_module.Corpus,
) -> None:
    """Otherwise the second measurement scores the corpus against the residue of
    the first, and every number drifts a little on each run."""
    encoder = _by_incident(corpus)
    with encoder_scope(EncoderKind.TEXT, encoder):
        async with sessions() as session:
            first = await measure(
                session, tenant_id=tenant, corpus=corpus, corpus_hash="test", settings=SETTINGS
            )
            second = await measure(
                session, tenant_id=tenant, corpus=corpus, corpus_hash="test", settings=SETTINGS
            )
            await session.rollback()

    assert (first.precision, first.recall) == (second.precision, second.recall)
    assert len(second.judgements) == len(corpus.reports)


async def test_precision_is_one_when_nothing_merged(
    sessions: async_sessionmaker[AsyncSession],
    tenant: uuid.UUID,
) -> None:
    """A system that never merges has made no wrong merges. Arithmetically true,
    and exactly why the report never prints precision without recall."""
    single = corpus_module.load()
    # Every report gets a vector of its own, so no merge is ever licensed.
    # `hash` is not used: it is salted per process, and a fixture whose
    # similarity structure changes between runs cannot support any claim.
    unique = _KeyedEncoder(lambda text: float(len(text) * 1000 + sum(map(ord, text[:8]))))
    measurement = await run_measure(sessions, tenant=tenant, corpus=single, encoder=unique)

    assert measurement.true_positives == 0
    assert measurement.precision == 1.0
    assert measurement.recall < 1.0


async def test_the_scratch_tenant_is_isolated(
    sessions: async_sessionmaker[AsyncSession],
    tenant: uuid.UUID,
    other_tenant_id: uuid.UUID,
    corpus: corpus_module.Corpus,
) -> None:
    """The harness clears "its" tenant before measuring. It must not clear
    anybody else's — a measurement run against a shared database is not worth a
    deleted complaint."""
    async with sessions() as session:
        await session.execute(
            sql_text(
                "INSERT INTO complaint_clusters "
                "(id, tenant_id, centroid, report_count, first_reported, last_reported, version) "
                "VALUES (gen_random_uuid(), :tenant, "
                "ST_SetSRID(ST_MakePoint(73.8, 18.5), 4326), 1, now(), now(), 1)"
            ).bindparams(tenant=other_tenant_id)
        )
        await session.commit()

    with encoder_scope(EncoderKind.TEXT, _by_incident(corpus)):
        async with sessions() as session:
            await measure(
                session, tenant_id=tenant, corpus=corpus, corpus_hash="test", settings=SETTINGS
            )
            surviving = (
                await session.execute(
                    sql_text(
                        "SELECT count(*) FROM complaint_clusters WHERE tenant_id = :tenant"
                    ).bindparams(tenant=other_tenant_id)
                )
            ).scalar_one()
            await session.rollback()

    assert surviving == 1
