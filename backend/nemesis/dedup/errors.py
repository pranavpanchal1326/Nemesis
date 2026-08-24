"""What deduplication can fail at, and what each failure means for a report.

The split matters because the two failures have opposite correct responses.
``DedupUnavailableError`` means *the engine could not decide* — the database was
unreachable, the policy document would not resolve — and §14.3's error direction
says the report should proceed unmerged rather than be held: an unmerged
duplicate costs an operator some time, while a held report is a citizen waiting.
``DedupIntegrityError`` means *the engine decided something impossible* — a
candidate cluster in another tenant, a similarity outside [-1, 1] — and that
must halt loudly, because the next step after a nonsensical decision is a merge
that suppresses a real report.

Neither is an HTTP error and neither is a Celery exception. This package is
called from a Celery stage and from a benchmarking script, and a module that
knew about either could not be called from the other — the same rule ``policy``,
``simulation`` and ``perception`` follow.
"""

from __future__ import annotations


class DedupError(Exception):
    """Base for everything this package raises."""


class DedupUnavailableError(DedupError):
    """The engine could not reach something it needed to decide.

    Retryable. The stage's ``max_attempts`` budget applies, and on exhaustion
    the pipeline degrades to ``SKIPPED_STAGE`` and continues — which is the
    §14.3 direction, stated in ``stages.py`` where the spec is declared.
    """


class DedupIntegrityError(DedupError):
    """The engine produced a result that cannot be true.

    Never retryable, because a retry recomputes the same impossibility. Raised
    on cross-tenant candidates and on similarities outside the range cosine can
    produce — both of which mean a query or a vector column is wrong, and both
    of which would otherwise reach ``decide`` and be compared against a
    threshold as though they were meaningful.
    """
