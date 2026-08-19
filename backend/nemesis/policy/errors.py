"""Policy failures, as a small closed set the API layer can translate.

Mirrors ``control_plane.errors`` deliberately, and does not import from it. The
two packages are called from the same HTTP handlers today, but they answer
different questions — "does this tenant exist" versus "may this document go
live" — and collapsing them would mean a lifecycle rejection and a missing
taxonomy key arriving at the caller as the same type, distinguishable only by
reading the message.

Every one of these is a *refusal*, never a fallback. A policy engine that
guesses when it cannot resolve is worse than one that stops: the guess reaches
the event log stamped with a version that did not produce it, and the record
that Phase 7 backtests against becomes fiction.
"""

from __future__ import annotations


class PolicyError(Exception):
    """Base for every rejection this package makes."""


class PolicyNotFoundError(PolicyError):
    """No such policy document, or none visible to this tenant.

    The tenant qualifier carries the same weight it does in the control plane:
    "exists but belongs to someone else" and "does not exist" are one answer,
    or the endpoint becomes a way to enumerate another customer's rulesets one
    version number at a time.
    """


class PolicyConflictError(PolicyError):
    """The write collides with the state the document is already in.

    Two operators approving the same draft, an activation racing a rollback, a
    version number already taken. Separate from a validation failure because
    the remedy is to re-read and retry, not to correct the request.
    """


class PolicyValidationError(PolicyError):
    """The document is internally inconsistent, or violates a stated invariant.

    Raised while the document is still a draft wherever that is possible. A
    rubric whose weights do not sum to one is wrong the moment it is written,
    and catching it at activation instead would mean the error surfaces to
    whoever pressed the button rather than to whoever made the mistake.
    """


class PolicyTransitionError(PolicyError):
    """The requested lifecycle transition is not legal from the current status.

    Its own type because the remedy is neither "fix the request" nor "retry":
    it is "this document is in a state where that verb does not apply", and the
    message names the transitions that *are* available. A caller that gets a
    generic validation error here re-sends the same request with a tweaked
    body, which cannot ever succeed.
    """


class ExpressionError(PolicyValidationError):
    """A routing condition could not be compiled, or refused to compile.

    A subclass of validation rather than a peer: an unparseable condition is a
    malformed document, and it must fail at *draft* time. Discovering it at
    evaluation time would mean a routing rule that silently never matches, and
    a complaint that goes nowhere is indistinguishable from one nobody has
    picked up yet.
    """


class ExpressionLimitError(ExpressionError):
    """The expression exceeded a bound — size, depth, or evaluation steps.

    Distinguished from a syntax rejection because the two say different things
    to the author. A banned construct means "the sandbox does not allow that";
    a limit means "what you wrote is allowed but too large", and the second is
    usually a rule that wants splitting rather than a rule that wants rewriting.
    """


class PolicyCertificationError(PolicyError):
    """Activation was refused because the candidate has no passing certificate.

    Phase 7's gate clause, as a type: *a policy that regresses the labelled
    evaluation set cannot be activated.* "Cannot" is enforced in
    ``policy.service.activate``, which is the single mutation path — so the
    refusal has to be expressible in this package's vocabulary, and the
    certificate is read as **data** rather than by calling the simulation
    package. The dependency runs one way only: simulation knows about policy,
    policy knows about a table.

    Its own type rather than a ``PolicyTransitionError`` because the remedy is
    different in kind. A transition error means "this document is in the wrong
    state"; this one means "the document may well be fine and nobody has
    checked". The caller's next action is to run an evaluation, not to press a
    different button, and the detail says so.
    """
