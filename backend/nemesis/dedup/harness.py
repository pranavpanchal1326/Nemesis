"""Measuring the engine against the labelled corpus.

The §14 gate asks for two numbers and one absolute: precision and recall against
a labelled fixture set, and **zero false-positive merges**. This is where they
come from.

**The engine under test is the shipped one.** ``measure`` calls
``engine.evaluate`` — the same function ``dedup_stage`` calls, resolving the same
policy document, running the same two SQL stages against a real Postgres. It
does not re-implement the geospatial filter in Python, and it does not call
``decide`` with hand-built candidates. A harness that reproduced any part of the
decision would be measuring the reproduction, and the number it published would
be true of a program nobody runs. This is the same rule Phase 9's harness
follows and states.

**What the harness does own: applying the decision.** After each report is
evaluated the harness writes the resulting cluster membership itself, rather
than appending events and replaying projections. That is a deliberate, narrow
divergence: the measurement is of what ``evaluate`` *decided*, and the event path
that records the decision is proven separately and end to end by
``test_dedup_stage``. Driving the full orchestrator here would make a
seventy-report corpus into several hundred transactions and would make a harness
failure ambiguous between the engine and the projector.

**Reports are fed in chronological order, one at a time**, because dedup is
order-dependent by construction: the second report of an incident can only merge
into a cluster the first one created. Shuffling the corpus or evaluating pairs
in isolation would measure a system that does not exist.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.config import DedupSettings
from nemesis.db.models.complaint import Complaint, ComplaintCluster
from nemesis.dedup.corpus import Corpus, Report
from nemesis.dedup.decide import DedupOutcome
from nemesis.dedup.engine import evaluate
from nemesis.domain.lifecycle import ComplaintStatus
from nemesis.perception.embeddings import store
from nemesis.perception.encoders import QUERY_PREFIX, active_text_encoder


@dataclass(frozen=True, slots=True)
class Judgement:
    """What the engine did with one report, and whether it was right."""

    report_id: str
    incident_id: str
    outcome: DedupOutcome
    cluster_id: uuid.UUID | None
    combined_confidence: float
    candidates: int
    #: The incident every member of the joined cluster belongs to, when they all
    #: belong to one. ``None`` when the report joined nothing.
    joined_incident: str | None
    correct: bool
    #: Set when the engine merged this report into a cluster holding a report
    #: from a different incident. The gate's absolute: this list must be empty.
    false_merge_with: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Measurement:
    """Precision, recall, and the counts they were computed from."""

    corpus_id: str
    corpus_hash: str
    judgements: tuple[Judgement, ...]
    true_positives: int
    false_positives: int
    false_negatives: int
    true_negatives: int
    investigated: int
    encoder_id: str
    policy_version: str
    latencies_ms: tuple[float, ...] = field(default_factory=tuple)

    @property
    def precision(self) -> float:
        """Of the merges made, how many were right.

        Defined as 1.0 when nothing merged. A system that never merges has made
        no wrong merges, which is arithmetically true and exactly why precision
        is never read without recall beside it.
        """
        decided = self.true_positives + self.false_positives
        return 1.0 if decided == 0 else self.true_positives / decided

    @property
    def recall(self) -> float:
        """Of the merges that should have happened, how many did."""
        available = self.true_positives + self.false_negatives
        return 1.0 if available == 0 else self.true_positives / available

    @property
    def f1(self) -> float:
        if self.precision + self.recall == 0.0:
            return 0.0
        return 2 * self.precision * self.recall / (self.precision + self.recall)

    @property
    def false_merges(self) -> tuple[Judgement, ...]:
        return tuple(judgement for judgement in self.judgements if judgement.false_merge_with)

    @property
    def p95_latency_ms(self) -> float | None:
        if not self.latencies_ms:
            return None
        ordered = sorted(self.latencies_ms)
        index = min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))
        return ordered[index]


async def measure(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    corpus: Corpus,
    corpus_hash: str,
    settings: DedupSettings,
) -> Measurement:
    """Feed the corpus through the real engine and score the outcome."""
    encoder = active_text_encoder()
    await _clear(session, tenant_id=tenant_id)

    # Membership as the harness accumulates it: cluster id -> report ids.
    members: dict[uuid.UUID, list[str]] = {}
    truth = corpus.truth
    judgements: list[Judgement] = []
    latencies: list[float] = []
    policy_version = "unresolved"

    for report in corpus.reports:
        complaint_id = await _insert_report(
            session, tenant_id=tenant_id, report=report, encoder=encoder
        )

        started = datetime.now(tz=UTC).timestamp()
        evaluation = await evaluate(
            session,
            tenant_id=tenant_id,
            complaint_id=complaint_id,
            state=_state_of(report),
            settings=settings,
        )
        latencies.append((datetime.now(tz=UTC).timestamp() - started) * 1000.0)
        policy_version = evaluation.policy_version
        decision = evaluation.decision

        if decision.outcome is DedupOutcome.MERGE and decision.cluster_id is not None:
            cluster_id = decision.cluster_id
            await _join(session, tenant_id=tenant_id, complaint_id=complaint_id, cluster=cluster_id)
        else:
            cluster_id = await _new_cluster(session, tenant_id=tenant_id, report=report)
            await _join(session, tenant_id=tenant_id, complaint_id=complaint_id, cluster=cluster_id)

        existing = members.setdefault(cluster_id, [])
        # Computed before this report is added, so "who was already in here"
        # means exactly that.
        strangers = tuple(other for other in existing if truth[other] != report.incident_id)
        joined_incident = truth[existing[0]] if existing and not strangers else None
        existing.append(report.id)

        judgements.append(
            Judgement(
                report_id=report.id,
                incident_id=report.incident_id,
                outcome=decision.outcome,
                cluster_id=cluster_id,
                combined_confidence=decision.combined_confidence,
                candidates=decision.considered,
                joined_incident=joined_incident,
                # Filled by `_score`, which owns the whole right/wrong question.
                # Deciding it here as well would be the same rule in two places,
                # and the two places would disagree the first time one changed.
                correct=False,
                false_merge_with=strangers,
            )
        )

    return _score(
        corpus=corpus,
        corpus_hash=corpus_hash,
        judgements=tuple(judgements),
        encoder_id=encoder.model_id,
        policy_version=policy_version,
        latencies=tuple(latencies),
    )


def _score(
    *,
    corpus: Corpus,
    corpus_hash: str,
    judgements: Sequence[Judgement],
    encoder_id: str,
    policy_version: str,
    latencies: Sequence[float],
) -> Measurement:
    seen_incidents: set[str] = set()
    true_positives = false_positives = false_negatives = true_negatives = investigated = 0
    scored: list[Judgement] = []

    for judgement in judgements:
        # "Should have merged" means an earlier report of the same incident has
        # already gone through. The first report of an incident has nothing to
        # merge into and must never be counted as a missed merge — counting it
        # would make recall depend on how many incidents the corpus contains.
        should_merge = judgement.incident_id in seen_incidents
        merged = judgement.outcome is DedupOutcome.MERGE
        if judgement.outcome is DedupOutcome.INVESTIGATE:
            investigated += 1
        if merged and judgement.false_merge_with:
            false_positives += 1
            correct = False
        elif merged:
            true_positives += 1
            correct = True
        elif should_merge:
            false_negatives += 1
            correct = False
        else:
            true_negatives += 1
            correct = True
        scored.append(replace(judgement, correct=correct))
        seen_incidents.add(judgement.incident_id)

    return Measurement(
        corpus_id=corpus.corpus_id,
        corpus_hash=corpus_hash,
        judgements=tuple(scored),
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        true_negatives=true_negatives,
        investigated=investigated,
        encoder_id=encoder_id,
        policy_version=policy_version,
        latencies_ms=tuple(latencies),
    )


def _state_of(report: Report) -> Mapping[str, Any]:
    return {
        "latitude": report.latitude,
        "longitude": report.longitude,
        "reported_at": report.reported_at,
        "category": report.category,
        "description_text": report.text,
        "status": ComplaintStatus.CLASSIFIED.value,
    }


async def _insert_report(
    session: AsyncSession, *, tenant_id: uuid.UUID, report: Report, encoder: Any
) -> uuid.UUID:
    # QUERY_PREFIX on both sides, matching what `perception.stage` writes onto a
    # complaint. Using PASSAGE_PREFIX here would embed the corpus differently
    # from production and quietly measure a comparison the system never makes.
    vector = encoder.encode([report.text], prefix=QUERY_PREFIX)[0]
    complaint_id = uuid.uuid4()
    await session.execute(
        insert(Complaint).values(
            id=complaint_id,
            tenant_id=tenant_id,
            status=ComplaintStatus.CLASSIFIED.value,
            category=report.category,
            description_text=report.text,
            location=func.ST_SetSRID(func.ST_MakePoint(report.longitude, report.latitude), 4326),
            reported_at=report.reported_at,
            version=1,
        )
    )
    # Through `perception.embeddings.store`, not an inline column assignment.
    # The two vector columns are §9.1's one documented exception to the
    # projection rule, and that exception has exactly one writer — a guard test
    # enforces it, and it caught this module writing the column directly. Going
    # through the writer also buys the dimension check, so a corpus embedded by
    # the wrong model fails here rather than at the index.
    await session.flush()
    await store(session, tenant_id=tenant_id, complaint_id=complaint_id, text_embedding=vector)
    return complaint_id


async def _new_cluster(session: AsyncSession, *, tenant_id: uuid.UUID, report: Report) -> uuid.UUID:
    cluster_id = uuid.uuid4()
    await session.execute(
        insert(ComplaintCluster).values(
            id=cluster_id,
            tenant_id=tenant_id,
            centroid=func.ST_SetSRID(func.ST_MakePoint(report.longitude, report.latitude), 4326),
            # Zero, not one: `_join` immediately increments, and seeding at one
            # would double-count every cluster's first member.
            report_count=0,
            first_reported=report.reported_at,
            last_reported=report.reported_at,
            version=1,
        )
    )
    await session.flush()
    return cluster_id


async def _join(
    session: AsyncSession, *, tenant_id: uuid.UUID, complaint_id: uuid.UUID, cluster: uuid.UUID
) -> None:
    await session.execute(
        update(Complaint)
        .where(Complaint.tenant_id == tenant_id, Complaint.id == complaint_id)
        .values(cluster_id=cluster)
    )
    await session.execute(
        update(ComplaintCluster)
        .where(ComplaintCluster.tenant_id == tenant_id, ComplaintCluster.id == cluster)
        .values(report_count=ComplaintCluster.report_count + 1)
    )
    await session.flush()


async def _clear(session: AsyncSession, *, tenant_id: uuid.UUID) -> None:
    """Empty the scratch tenant, so a re-run measures the corpus and not the
    accumulated residue of the previous run."""
    await session.execute(
        update(Complaint).where(Complaint.tenant_id == tenant_id).values(cluster_id=None)
    )
    await session.execute(delete(ComplaintCluster).where(ComplaintCluster.tenant_id == tenant_id))
    await session.execute(delete(Complaint).where(Complaint.tenant_id == tenant_id))
    await session.flush()


async def cluster_sizes(
    session: AsyncSession, *, tenant_id: uuid.UUID
) -> Sequence[tuple[uuid.UUID, int]]:
    rows = await session.execute(
        select(ComplaintCluster.id, ComplaintCluster.report_count).where(
            ComplaintCluster.tenant_id == tenant_id
        )
    )
    return [(row[0], int(row[1])) for row in rows.all()]


__all__ = ["Judgement", "Measurement", "cluster_sizes", "measure"]
