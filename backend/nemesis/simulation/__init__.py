"""Phase 7 — configuration simulation, backtesting, and the activation guardrail.

Phase 6 made every behavioural knob governed data. It also left a hole it named
in its own notes: *activation currently depends on an approver reading a
document.* Somebody reads forty weights, forms an opinion, and presses a button
that changes how every future citizen report is scored. This package is what
replaces the opinion with a measurement.

Three capabilities, and they answer three different questions:

``backtest``
    *What would this change have done to last year?* Replays a candidate over
    history and quantifies the delta — how many complaints change severity, how
    many merges flip, which departments gain work.
``evaluation``
    *Does this change still get the cases we already argued about right?* Marks
    a candidate against a labelled set of human judgements and issues a
    certificate. A published set gates activation: ``policy.service.activate``
    refuses a candidate with no passing certificate over its exact bytes.
``shadow``
    *What is it doing to today?* Decides live traffic under both the live and
    the candidate bundle, records what differed, and acts on none of it.

The package splits along the line between what is *pure* and what touches the
database, because the phase's credibility rests on that line being real:

``engine``
    The decision, as a total function of (bundle, case). No session, no clock,
    no randomness. Calls production's arithmetic rather than reimplementing it.
``corpus``
    Reconstructing decision *inputs* from the event log. Observations only —
    never the decisions under test, and never the ``complaints`` projection.
``bundles``
    Assembling the two configurations a comparison is between, including
    candidates that are still drafts.
``backtest``
    The diff, and the report an approver reads.
``evaluation``
    Labelled sets, marking, and certificates.
``runs``
    Executing a run and recording it, including the failures.
``shadow``
    Live observation, under a read-only guarantee.
``tuning``
    Dedup threshold proposals derived from merges humans undid. Surfaced as
    drafts, never applied.
``readonly``
    The two-layer guarantee behind "shadow mode cannot mutate state".

**The dependency runs one way.** This package imports ``policy``; ``policy``
imports nothing from here. The guardrail reaches the activation path as *data* —
a row in ``policy_certificates`` — precisely so that it cannot be disabled by an
import going missing. See ``db.models.simulation``.

**Nothing here commits**, the same contract ``policy`` and ``control_plane``
state, for the same reason.
"""

from __future__ import annotations

from nemesis.simulation.errors import (
    CorpusTooSmallError,
    ShadowWriteError,
    SimulationConflictError,
    SimulationError,
    SimulationNotFoundError,
    SimulationValidationError,
)

__all__ = [
    "CorpusTooSmallError",
    "ShadowWriteError",
    "SimulationConflictError",
    "SimulationError",
    "SimulationNotFoundError",
    "SimulationValidationError",
]
