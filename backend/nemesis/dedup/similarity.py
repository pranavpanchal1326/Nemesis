"""Stage 2 — how close the report is to each surviving candidate.

**A cluster has no embedding of its own, and deliberately gets none.** The
obvious design is a centroid vector on ``complaint_clusters``, updated on every
merge. It is wrong for the reason a mean is usually wrong: two photographs of
the same pothole from opposite kerbs sit some distance apart, their mean sits
between them and resembles neither, and every subsequent report is compared
against a synthetic vector no citizen ever submitted. The centroid also drifts
with each merge, so the rule that admitted the tenth member is not the rule that
admitted the second, and a disputed merge cannot be re-argued against it.

So a candidate is scored against its *members*, and the score is the **best**
match among them. A cluster is one real-world problem; a new report that closely
matches any one existing report of that problem is a report of that problem. Max
is the permissive aggregator and that is a deliberate, bounded risk: the members
are there because they were themselves similar, the band thresholds are set
conservatively per §14.3, and the tie rule in ``decide`` catches the case where
two clusters both look right. The alternative — mean — makes large clusters
progressively harder to join, which is backwards, since a cluster with twenty
reports is *more* likely to be the right home for the twenty-first.

**Why this is exact rather than an approximate-nearest-neighbour search.** The
HNSW indexes on the two embedding columns exist and are well tuned
(``docs/reports/hnsw-recall.md``), and this query does not use them. After Stage
1 the candidate set is a handful of clusters, so an exact scan of their members
is both cheap and *right*, and rightness is the whole argument: that report
measured a distance ratio of 1.0105, meaning HNSW returns near-ties in a
different order than exact search. Near-tie reordering is harmless in a search
box and unacceptable in a merge decision that §14.3 requires be reproducible
from the event log — the same report, replayed, must reach the same cluster.
ADR-0036 records the trade and the conditions under which it would be revisited.

That is also why ``hnsw.iterative_scan`` is not set here: it is the fix for a
filtered ANN search silently under-returning, and there is no ANN search on this
path to under-return. The setting belongs with the query that needs it, and this
one does not.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import Float, Select, cast, func, null, select
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.db.models.complaint import Complaint


@dataclass(frozen=True, slots=True)
class ClusterSimilarity:
    """Best image and text similarity between one report and one cluster.

    ``None`` means *not comparable* — one side or the other had no vector — and
    is carried through to ``combined_dedup_confidence`` rather than collapsed to
    zero, because those two mean different things to the band arithmetic.
    """

    cluster_id: uuid.UUID
    image_similarity: float | None
    text_similarity: float | None
    #: How many member rows were in scope for this cluster after the per-cluster
    #: cap. Recorded so a merge decided against three of a cluster's forty
    #: members is distinguishable from one decided against all three it has.
    members_compared: int


def similarity_statement(
    *,
    tenant_id: uuid.UUID,
    cluster_ids: Sequence[uuid.UUID],
    text_embedding: Sequence[float] | None,
    image_embedding: Sequence[float] | None,
    exclude_complaint_id: uuid.UUID | None,
    max_members_per_cluster: int,
) -> Select[tuple[uuid.UUID, float | None, float | None, int]]:
    """The Stage 2 query, built but not executed.

    The per-cluster cap is a window function rather than a ``LIMIT``, because a
    ``LIMIT`` over a grouped query bounds the number of *clusters* and the thing
    worth bounding is the number of *members* — one pathological cluster with
    ten thousand reports should not turn a ten-second budget into a minute.
    Ordered by ``reported_at`` descending, so the members kept are the most
    recent: an incident that has changed appearance over a week is best matched
    against how it looks now.
    """
    ranked = (
        select(
            Complaint.id.label("complaint_id"),
            Complaint.cluster_id.label("cluster_id"),
            Complaint.text_embedding.label("text_embedding"),
            Complaint.image_embedding.label("image_embedding"),
            func.row_number()
            .over(
                partition_by=Complaint.cluster_id,
                order_by=Complaint.reported_at.desc(),
            )
            .label("member_rank"),
        )
        .where(
            Complaint.tenant_id == tenant_id,
            Complaint.cluster_id.in_(cluster_ids),
        )
        .subquery()
    )

    predicates = [ranked.c.member_rank <= max_members_per_cluster]
    if exclude_complaint_id is not None:
        # A redelivered stage must not score the report against itself. Left in
        # as a predicate rather than assumed impossible: the complaint acquires
        # its cluster_id in the same transaction as the merge, so a retry after
        # a partial commit is exactly when this would otherwise return 1.0.
        predicates.append(ranked.c.complaint_id != exclude_complaint_id)

    # `1 - cosine_distance` is the similarity. MIN over distance is MAX over
    # similarity, and MIN skips NULLs, which is the behaviour wanted: a member
    # with no photograph must not count as a zero-similarity photograph.
    # A typed NULL, not an untyped one: the column has to come back as a float
    # so the row unpacking below is uniform whether or not the report carried
    # that modality. `cast(null(), Float)` says so in one expression.
    image_expr = (
        func.min(ranked.c.image_embedding.cosine_distance(image_embedding))
        if image_embedding is not None
        else cast(null(), Float)
    )
    text_expr = (
        func.min(ranked.c.text_embedding.cosine_distance(text_embedding))
        if text_embedding is not None
        else cast(null(), Float)
    )

    return (
        select(
            ranked.c.cluster_id,
            image_expr.label("image_distance"),
            text_expr.label("text_distance"),
            func.count(ranked.c.complaint_id).label("members_compared"),
        )
        .where(*predicates)
        .group_by(ranked.c.cluster_id)
    )


async def score_candidates(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    cluster_ids: Sequence[uuid.UUID],
    text_embedding: Sequence[float] | None,
    image_embedding: Sequence[float] | None,
    exclude_complaint_id: uuid.UUID | None,
    max_members_per_cluster: int,
) -> Mapping[uuid.UUID, ClusterSimilarity]:
    """Score every candidate cluster in one round trip.

    Returns a mapping rather than a sequence because the caller joins it back
    onto the Stage 1 candidates, and a cluster with no comparable members
    legitimately produces no row here — a lookup miss is the natural way to say
    "nothing to compare", where a positional sequence would need a sentinel.
    """
    if not cluster_ids:
        return {}
    if text_embedding is None and image_embedding is None:
        # Nothing to compare against. Returning early is not just an
        # optimisation: the query below would emit two NULL casts and aggregate
        # members for no reason, and the caller cannot tell that result from a
        # genuine set of incomparable clusters.
        return {}

    rows = (
        await session.execute(
            similarity_statement(
                tenant_id=tenant_id,
                cluster_ids=cluster_ids,
                text_embedding=text_embedding,
                image_embedding=image_embedding,
                exclude_complaint_id=exclude_complaint_id,
                max_members_per_cluster=max_members_per_cluster,
            )
        )
    ).all()

    return {
        cluster_id: ClusterSimilarity(
            cluster_id=cluster_id,
            image_similarity=None if image_distance is None else 1.0 - float(image_distance),
            text_similarity=None if text_distance is None else 1.0 - float(text_distance),
            members_compared=int(members_compared),
        )
        for cluster_id, image_distance, text_distance, members_compared in rows
    }


__all__ = ["ClusterSimilarity", "score_candidates", "similarity_statement"]
