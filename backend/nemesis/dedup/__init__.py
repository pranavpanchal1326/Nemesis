"""Phase 10 — deduplication and clustering. The moat (§14), never simplified.

What this package does, in one sentence: it decides whether a new report is a
fresh incident or another citizen reporting one the system already knows about,
and it is wrong in a specific direction on purpose.

**The asymmetry that shapes every decision in here.** §14.3: a false merge
suppresses a real citizen's report — they are told their problem is already
being handled when the thing they photographed is not the thing in the cluster —
while a missed merge only costs an operator some time reconciling two work
orders. The two errors are not comparable, so the engine is not tuned to
minimise total error. It is tuned so that the error it makes is the recoverable
one. That is why the band thresholds are conservative, why an exact tie
downgrades to human review instead of picking a winner, why Stage 2 is exact
rather than approximate, and why the phase gate is *zero* false-positive merges
rather than a percentage.

The modules split along what fails independently:

``candidates``
    Stage 1. Geography, time and category, all index-backed. Cheap elimination.
``similarity``
    Stage 2. Exact cosine against candidate members. The expensive half, run
    only on what Stage 1 could not rule out.
``decide``
    The band arithmetic, as a pure function. No session, no clock, no policy
    lookup — so the zero-false-merge claim is property-testable directly.
``engine``
    The sequence, and the only place that resolves policy or reads a vector.
``merge``
    Decisions become events, on both chains. Reversal appends, never deletes.
``stage``
    The pipeline provider: idempotency, metrics, logging.
``harness``
    Precision and recall over a labelled pair corpus, published as a committed
    artefact and reproduced by one command.

Nothing here commits a transaction and nothing raises an HTTP error — the same
rule ``policy``, ``simulation``, ``trust`` and ``perception`` follow, for the
same reason: the stage runs in Celery, the harness runs in a script, and a
package that knew about either could not be called from the other.
"""

from __future__ import annotations

from nemesis.dedup.decide import DedupDecision, DedupOutcome, ScoredCandidate, decide
from nemesis.dedup.errors import DedupError, DedupIntegrityError, DedupUnavailableError

__all__ = [
    "DedupDecision",
    "DedupError",
    "DedupIntegrityError",
    "DedupOutcome",
    "DedupUnavailableError",
    "ScoredCandidate",
    "decide",
]
