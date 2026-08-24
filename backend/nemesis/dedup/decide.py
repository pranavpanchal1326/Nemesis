"""The merge rule, as a pure function over already-gathered evidence.

No session, no policy lookup, no clock. Everything this module needs is passed
in, which is what makes §14.3's central claim — *zero false-positive merges* —
something a property test can attack directly with generated inputs rather than
something that can only be observed by running a pipeline and hoping the right
case came up.

**Why the decision is separated from the queries at all.** ``candidates`` and
``similarity`` are I/O whose correctness is "did the index get used"; this is
arithmetic whose correctness is "would this suppress a citizen's report". They
fail differently, they are tested differently, and a single function doing both
could only be tested the weaker of the two ways.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from nemesis.dedup.errors import DedupIntegrityError
from nemesis.policy.documents import DedupBand
from nemesis.policy.resolver import combined_dedup_confidence, dedup_outcome

#: Cosine over L2-normalised vectors cannot leave this range. A value outside it
#: means the column holds something that is not a unit vector, or that the
#: query compared the wrong pair of columns — either way the number is not a
#: similarity and must not reach a threshold comparison.
_COSINE_MIN: Final = -1.0
_COSINE_MAX: Final = 1.0
#: Floating-point slack, so a stored vector whose norm is 0.9999999 does not
#: raise. Wide enough for accumulated float32 error, far too narrow to admit a
#: genuinely wrong value.
_COSINE_TOLERANCE: Final = 1e-6


class DedupOutcome(StrEnum):
    """The three bands, named. Values match ``policy.resolver.dedup_outcome``.

    Kept as an enum here rather than passing the resolver's raw strings onward,
    because these values become an event payload field and a metric label. A
    typo in a string literal is a silently empty Grafana panel; a typo in an
    enum member is an ImportError at startup.
    """

    MERGE = "merge"
    INVESTIGATE = "investigate"
    DISTINCT = "distinct"


@dataclass(frozen=True, slots=True)
class ScoredCandidate:
    """One candidate cluster with both stage scores attached.

    ``image_similarity`` and ``text_similarity`` are ``None`` — not zero — when
    the comparison could not be made at all, because ``combined_dedup_confidence``
    treats the two differently and the distinction is load-bearing: a text-only
    report averaged against a zero image score can never clear a merge
    threshold, which reads as caution and is actually dedup being switched off
    for everyone who submits without a photograph.
    """

    cluster_id: uuid.UUID
    geo_distance_meters: float
    image_similarity: float | None
    text_similarity: float | None
    report_count: int

    def __post_init__(self) -> None:
        if self.geo_distance_meters < 0.0:
            raise DedupIntegrityError(
                f"candidate {self.cluster_id} reported a negative distance "
                f"({self.geo_distance_meters}); ST_Distance cannot produce one, so the "
                f"query returned a column that is not a distance"
            )
        for label, value in (
            ("image_similarity", self.image_similarity),
            ("text_similarity", self.text_similarity),
        ):
            if value is None:
                continue
            if not (_COSINE_MIN - _COSINE_TOLERANCE <= value <= _COSINE_MAX + _COSINE_TOLERANCE):
                raise DedupIntegrityError(
                    f"candidate {self.cluster_id} reported {label}={value}, outside the "
                    f"range cosine can produce; the column is not holding unit vectors or "
                    f"the query compared mismatched embeddings"
                )


@dataclass(frozen=True, slots=True)
class DedupDecision:
    """What the engine concluded, in the shape the event payload needs.

    Carries the losing evidence as well as the winning evidence — ``considered``
    and ``runner_up_confidence`` — because §14.3's reversibility promise is only
    worth something if a disputed merge can be re-argued, and "what else was
    close" is the first question anyone asks about a wrong merge.
    """

    outcome: DedupOutcome
    cluster_id: uuid.UUID | None
    combined_confidence: float
    image_similarity: float | None
    text_similarity: float | None
    geo_distance_meters: float | None
    report_count_before: int
    #: How many candidates survived Stage 1 and were scored.
    considered: int
    #: The second-best combined confidence, when there was a second candidate.
    runner_up_confidence: float | None
    #: Set when two candidates were too close to separate. The outcome is
    #: downgraded to ``INVESTIGATE`` and this says why, so the downgrade is not
    #: mistaken for a weak match.
    ambiguous_between: tuple[uuid.UUID, ...] = ()
    #: True when no similarity could be computed against any candidate — the
    #: report carries no usable embedding, or none of the candidates' members
    #: do. The outcome is ``DISTINCT`` and a new cluster is correct, but it is
    #: correct by *absence of evidence* rather than by evidence of difference,
    #: and those two must not look the same in a log.
    blind: bool = False


def decide(
    candidates: Sequence[ScoredCandidate],
    *,
    band: DedupBand,
    ambiguous_margin: float,
) -> DedupDecision:
    """Pick the best candidate and apply the band, conservatively.

    **The tie rule, and why it is not just "take the highest".** Two candidate
    clusters can both clear the merge threshold. Taking the higher one is a
    coin-flip dressed as a decision: the two clusters are either duplicates of
    each other — in which case merging into one of them is arbitrary and the
    real fix is a cluster-to-cluster merge nothing has asked for yet — or they
    are genuinely different incidents and at least one match is wrong. §14.3
    values a missed merge far below a false one, so when the top two are within
    ``ambiguous_margin`` the outcome is downgraded to ``INVESTIGATE`` and a
    human — Phase 16's Investigation Agent, once it exists — separates them.

    Without this rule the zero-false-positive gate would be measurable only on
    fixtures that happen to contain one plausible match each, which is not the
    situation the rule exists for.
    """
    if not candidates:
        return DedupDecision(
            outcome=DedupOutcome.DISTINCT,
            cluster_id=None,
            combined_confidence=0.0,
            image_similarity=None,
            text_similarity=None,
            geo_distance_meters=None,
            report_count_before=0,
            considered=0,
            runner_up_confidence=None,
        )

    scored = sorted(
        (
            (
                combined_dedup_confidence(
                    band,
                    image_similarity=candidate.image_similarity,
                    text_similarity=candidate.text_similarity,
                ),
                candidate,
            )
            for candidate in candidates
        ),
        # Distance breaks a confidence tie: same score, nearer cluster. Ordering
        # by the id would be stable too, and would make the choice depend on a
        # UUID, which is not a reason anybody could defend to a citizen.
        key=lambda pair: (-pair[0], pair[1].geo_distance_meters),
    )
    best_confidence, best = scored[0]
    runner_up_confidence = scored[1][0] if len(scored) > 1 else None

    blind = all(
        candidate.image_similarity is None and candidate.text_similarity is None
        for candidate in candidates
    )

    outcome = DedupOutcome(dedup_outcome(band, confidence=best_confidence))

    ambiguous: tuple[uuid.UUID, ...] = ()
    if (
        outcome is DedupOutcome.MERGE
        and runner_up_confidence is not None
        and best_confidence - runner_up_confidence <= ambiguous_margin
    ):
        outcome = DedupOutcome.INVESTIGATE
        ambiguous = (best.cluster_id, scored[1][1].cluster_id)

    return DedupDecision(
        outcome=outcome,
        # An investigated or distinct report is not attached to anything. Naming
        # the near-miss cluster here would put a cluster_id on a decision that
        # explicitly did not join it, and every downstream reader would have to
        # remember that this one is not membership.
        cluster_id=best.cluster_id if outcome is DedupOutcome.MERGE else None,
        combined_confidence=best_confidence,
        image_similarity=best.image_similarity,
        text_similarity=best.text_similarity,
        geo_distance_meters=best.geo_distance_meters,
        report_count_before=best.report_count,
        considered=len(candidates),
        runner_up_confidence=runner_up_confidence,
        ambiguous_between=ambiguous,
        blind=blind,
    )


__all__ = ["DedupDecision", "DedupOutcome", "ScoredCandidate", "decide"]
