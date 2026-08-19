"""Simulation failures, as a small closed set the API layer can translate.

A third hierarchy beside ``control_plane.errors`` and ``policy.errors``, and it
does not import from either. The reason is the one ``policy.errors`` gives,
applied one layer out: these answer a different question again — not "does this
tenant exist" and not "may this document go live", but **"is the evidence for
letting it go live sound"**. A backtest that ran against four complaints and an
evaluation set with no labels are both refusals to *certify*, and neither is a
statement about the document.

Every one of these is a refusal. Nothing in this package falls back, guesses, or
downgrades a failed comparison into a passing one with a warning — a simulation
that reports "probably fine" when it could not do its job is worse than one that
stops, because the whole point of the phase is that somebody is about to press
Activate on the strength of what it says.
"""

from __future__ import annotations


class SimulationError(Exception):
    """Base for every rejection this package makes."""


class SimulationNotFoundError(SimulationError):
    """No such run, evaluation set, or certificate — or none visible to this tenant.

    Same conflation as everywhere else in this codebase: "exists but belongs to
    someone else" and "does not exist" are one answer, or a run id becomes a way
    to enumerate another customer's policy experiments.
    """


class SimulationConflictError(SimulationError):
    """The write collides with the state the resource is already in.

    Publishing an evaluation set that is already published, labelling a case in
    a published set, two runs racing for the same candidate. The remedy is to
    re-read and retry, not to correct the request.
    """


class SimulationValidationError(SimulationError):
    """The request is internally inconsistent, or violates a stated invariant.

    An evaluation label naming a component the rubric does not have, a window
    whose end precedes its start, a candidate revision of a different kind from
    the baseline it is being compared against.
    """


class CorpusTooSmallError(SimulationValidationError):
    """The historical window produced too few cases for the report to mean anything.

    Its own type, and the most important error in this module. A backtest over
    three complaints will happily report "0 severity changes, no regressions"
    and that sentence is *true* and completely worthless — it is the shape of an
    answer with none of the content, arriving at the exact moment somebody is
    looking for permission to activate. Refusing is the only honest response,
    and the message names the count and the floor so the operator widens the
    window rather than wondering what they did wrong.
    """


class ShadowWriteError(SimulationError):
    """Something inside a shadow evaluation tried to write.

    Raised by the read-only guard rather than by the database, so the traceback
    points at the statement instead of at the driver. It should be unreachable:
    reaching it means a code path that was supposed to be an observation has
    become a decision, which is the one failure shadow mode exists to make
    impossible. It is an error rather than an assertion because assertions are
    stripped under ``-O`` and this guarantee is not optional.
    """
