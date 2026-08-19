"""Trust-spine failures, as a small closed set the API layer can translate.

A fourth hierarchy beside ``control_plane.errors``, ``policy.errors`` and
``simulation.errors``, and it imports from none of them. The question these
answer is different again: not "does this tenant exist", not "may this document
go live", not "is the evidence sound" — but **"can this submission be processed
without breaking a promise made to the person who sent it"**.

**Every failure here is a refusal, and two of them refuse in a direction that
looks unhelpful on purpose.** ``RedactionUnavailableError`` stops a pipeline
because no face detector is loaded, and ``RedactionFailedError`` stops it
because an image would not decode. Both could be "handled" by carrying on with
the original file, and both would then be a §22.1 breach dressed as resilience.
§24.2 asks for degraded paths that are real shipped behaviour; it does not ask
for a degraded path through a legal obligation. The declared fallback for the
trust stage is ``HALTED_FOR_REVIEW``, which parks the report for a human — the
report is never lost, and the unblurred image is never served.
"""

from __future__ import annotations


class TrustError(Exception):
    """Base for every refusal this package makes."""


class RedactionError(TrustError):
    """The §22.1 obligation could not be discharged for this artefact."""


class RedactionUnavailableError(RedactionError):
    """No face detector is registered in this process.

    Distinct from a detector *failing*, and the distinction decides the
    response — the same split ``StageUnavailableError`` draws one layer up. A
    detector that is not installed in this image will still not be installed in
    thirty seconds, so retrying burns the stage's budget to reach a conclusion
    that is already available.

    It is an error rather than a silent pass-through because the alternative is
    a build where redaction quietly becomes a no-op: the pipeline succeeds, the
    events say the media was processed, and every face in every photograph is
    served to every reviewer. That failure is invisible from the outside, which
    is precisely why it must be loud from the inside.
    """


class RedactionFailedError(RedactionError):
    """The bytes could not be decoded, blurred, or written.

    A truncated JPEG, an image whose dimensions exceed the decompression bomb
    guard, a full disk. Retryable only for the last of the three, which is why
    the caller maps this onto ``StagePermanentError`` for decode failures — five
    attempts at a file that will not decode is five attempts not spent on the
    complaints queued behind it.
    """


class MediaNotFoundError(TrustError):
    """The artefact named by an event is not on disk.

    Reachable two ways, and they are not the same event: the §22.4 retention
    sweep purged the raw upload (expected, and the row records when), or
    something deleted it (not expected, and the log is now describing a file
    that is gone). The message distinguishes them so an operator is not left
    reading a stack trace to work out which.
    """


class ReviewError(TrustError):
    """Base for the §11.4 queue's refusals."""


class ReviewNotFoundError(ReviewError):
    """No such queue item, or none visible to this tenant.

    The same conflation the rest of the codebase makes, for the same reason: an
    id that answers "exists but belongs to someone else" differently from "does
    not exist" is an id that enumerates another customer's flagged reports.
    """


class ReviewConflictError(ReviewError):
    """The item already carries a decision.

    §11.4's queue takes exactly one judgement per item — a second one would be a
    second Phase 11 label for one example, with no way to choose between them
    and a trainer that reads both. Changing a decision is a new item raised
    against the new evidence, which is also the only version of the change that
    leaves an honest record of what was thought first.
    """


class ReviewValidationError(ReviewError):
    """The decision is not one of §11.4's three, or carries no rationale."""
