"""The pipeline graph, as declarations rather than as control flow.

**What this module is, and what it deliberately is not.** Phase 3 owns
*orchestration*: transactions, idempotency, ordering, retry budgets, and what
happens when a stage cannot run. It does not own perception, deduplication, or
scoring — those are Phases 8 through 12, and inventing a placeholder classifier
here would be the Phase 2 gate's twelfth defect repeated on purpose: work
reported as shipped, wearing the same words, proven against a stand-in.

So each stage is declared here with everything the orchestrator needs to run it,
and its *implementation* is a provider registered at runtime by the phase that
owns it. Today most of the providers are absent, and the orchestrator takes each
stage's declared degradation path — which is not a stub. §24.2 requires the
degraded path to be real shipped behaviour, and a report that reaches
``pending_classification`` because no classifier exists is behaving exactly as a
report will behave the day the classifier is down.

**Why the sequence is a linked list of declarations.** A Celery ``chain`` fixes
the graph at enqueue time, so a stage cannot decide that the next one is
unnecessary, and a redelivered task in the middle of a chain has no way to know
what already ran. Each stage enqueueing its successor by name means the graph is
readable in one table here, and every hop is an independently retryable,
independently idempotent unit.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

from nemesis.domain.lifecycle import DegradationFallback, EntityType
from nemesis.observability.metrics import PipelineStage
from nemesis.worker.celery_app import QUEUE_IO, QUEUE_ML, QUEUE_SAFETY


class StageError(RuntimeError):
    """A stage could not complete. Retryable unless a subclass says otherwise."""


class StageUnavailableError(StageError):
    """No provider is registered for this stage in this build.

    Distinct from a provider *failing*, and the distinction decides the response:
    a failure is retried against its budget because the next attempt may succeed,
    while an unregistered provider will still be unregistered in thirty seconds.
    Retrying it would burn the whole budget to reach a conclusion available
    immediately, and delay the degraded path §24.2 promises by minutes.
    """


class StagePermanentError(StageError):
    """The input is wrong in a way no retry fixes — a malformed image, say.

    Skips the retry budget and goes straight to the stage's fallback. Spending
    five attempts on a file that will not decode is five attempts not spent on
    the complaints behind it in the queue.
    """


@dataclass(frozen=True, slots=True)
class EmittedEvent:
    """An event a provider wants appended, on whichever chain it belongs to.

    A provider returns these rather than appending them itself. That is the
    seam: the provider knows *what happened*, the orchestrator knows how to get
    it into the log atomically, keyed for idempotency, projected, and enqueued
    for fan-out. A provider holding the session and appending directly would
    have to re-implement all four, once per phase.
    """

    entity_type: EntityType
    entity_id: Any
    event_type: str
    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class StageResult:
    """What a provider produced.

    ``halt`` stops the pipeline without it being a failure. The safety fail-safe
    is the case that matters: §11.2 says a triggered report bypasses the scoring
    pipeline entirely, and "bypasses" has to mean the successor stages are never
    enqueued, not that they run and are ignored.
    """

    emitted: Sequence[EmittedEvent] = field(default_factory=tuple)
    halt: bool = False
    halt_reason: str | None = None


@dataclass(frozen=True, slots=True)
class StageContext:
    """Everything a provider may read. Narrow, and narrow on purpose.

    ``state`` is the complaint's projected state, not an ORM row: a provider
    that reached for the ``complaints`` table could read a column no event
    explains, and the §9.1 rule that current state is derived would quietly stop
    being true. ``session`` is supplied because Phase 10's dedup genuinely needs
    to query PostGIS and pgvector across other complaints — but it is the
    orchestrator's transaction, so a provider that writes is writing inside the
    same atomic unit as the events it returns.
    """

    session: Any
    tenant_id: Any
    complaint_id: Any
    state: Mapping[str, Any]
    correlation_id: str | None
    attempt: int


class StageProvider(Protocol):
    """The contract Phases 8-12 implement."""

    def __call__(self, ctx: StageContext) -> Awaitable[StageResult]: ...


@dataclass(frozen=True, slots=True)
class StageSpec:
    """One stage's operational declaration."""

    stage: PipelineStage
    queue: str
    #: Total attempts including the first. Budgets differ by what failure means:
    #: the safety check is the §11.2 fail-safe and gets the most, while
    #: classification is CPU-bound inference where a third attempt on a saturated
    #: worker mostly adds queue depth.
    max_attempts: int
    #: Base for exponential backoff, in seconds. Jittered by the caller so a
    #: dependency coming back up is not hit by every retry at the same instant.
    retry_backoff_seconds: float
    #: What happens when the budget is exhausted or no provider exists.
    fallback: DegradationFallback
    #: The stage that runs next on success, or ``None`` for the last one.
    next_stage: PipelineStage | None
    #: Whether the pipeline continues after this stage degrades. False for the
    #: stages whose absence makes everything downstream meaningless.
    continue_on_degrade: bool
    blueprint: str
    owner_phase: str


#: The graph. Order here is the §10 lifecycle, steps 2 through 6.
_SPECS: Final[tuple[StageSpec, ...]] = (
    StageSpec(
        stage=PipelineStage.SAFETY_CHECK,
        queue=QUEUE_SAFETY,
        # Five, the highest budget in the graph. A missed danger signal is the
        # worst outcome this system can produce, so it is the stage most worth
        # spending attempts on before giving up.
        max_attempts=5,
        retry_backoff_seconds=2.0,
        # Never "skip". A report the danger check never saw must not be scored
        # and routed as though it had been cleared.
        fallback=DegradationFallback.HALTED_FOR_REVIEW,
        next_stage=PipelineStage.CLASSIFICATION,
        continue_on_degrade=False,
        blueprint="11.2",
        owner_phase="Phase 8",
    ),
    StageSpec(
        stage=PipelineStage.CLASSIFICATION,
        queue=QUEUE_ML,
        max_attempts=3,
        retry_backoff_seconds=5.0,
        # §24.2 by name: the complaint is created, parked as
        # `pending_classification`, and routed to manual review. Never lost, and
        # never given a guessed category.
        fallback=DegradationFallback.PENDING_CLASSIFICATION,
        next_stage=PipelineStage.DEDUP,
        continue_on_degrade=False,
        blueprint="24.2",
        owner_phase="Phase 9",
    ),
    StageSpec(
        stage=PipelineStage.DEDUP,
        queue=QUEUE_IO,
        max_attempts=3,
        retry_backoff_seconds=3.0,
        # A failed dedup means a duplicate stays unmerged, which §14.3 already
        # names as the *preferable* error direction — a false merge suppresses a
        # genuine citizen report, an unmerged duplicate only costs an operator
        # some time. So the pipeline continues.
        fallback=DegradationFallback.SKIPPED_STAGE,
        next_stage=PipelineStage.SEVERITY_SCORING,
        continue_on_degrade=True,
        blueprint="14.1",
        owner_phase="Phase 10",
    ),
    StageSpec(
        stage=PipelineStage.SEVERITY_SCORING,
        queue=QUEUE_IO,
        max_attempts=3,
        retry_backoff_seconds=3.0,
        # Routing reads severity to pick an SLA tier, so an unscored complaint
        # cannot be routed. Halt rather than route everything as low severity,
        # which would be indistinguishable downstream from a genuine low score.
        fallback=DegradationFallback.HALTED_FOR_REVIEW,
        next_stage=PipelineStage.ROUTING,
        continue_on_degrade=False,
        blueprint="13.5",
        owner_phase="Phase 12",
    ),
    StageSpec(
        stage=PipelineStage.ROUTING,
        queue=QUEUE_IO,
        max_attempts=3,
        retry_backoff_seconds=3.0,
        fallback=DegradationFallback.HALTED_FOR_REVIEW,
        next_stage=None,
        continue_on_degrade=False,
        blueprint="15.1",
        owner_phase="Phase 12",
    ),
)

SPECS: Final[dict[str, StageSpec]] = {spec.stage.value: spec for spec in _SPECS}

#: The stage every submission enters at.
FIRST_STAGE: Final[PipelineStage] = _SPECS[0].stage

#: Execution order, for tests and for the ops surface that renders the graph.
PIPELINE_SEQUENCE: Final[tuple[PipelineStage, ...]] = tuple(spec.stage for spec in _SPECS)


def spec_for(stage: str) -> StageSpec:
    try:
        return SPECS[stage]
    except KeyError:
        raise StageError(
            f"'{stage}' is not a pipeline stage; declared stages are "
            f"{sorted(SPECS)}. A stage name that does not resolve here has no "
            f"retry budget and no fallback, so it must not be dispatchable."
        ) from None


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

_providers: dict[str, StageProvider] = {}


def register_provider(stage: PipelineStage, provider: StageProvider) -> None:
    """Bind an implementation to a stage. Called by the phase that owns it.

    Refuses to replace an existing registration. Two providers for one stage is
    not a configuration choice, it is two teams having each assumed they owned
    the stage — and the one that loses is decided by import order, which is the
    worst possible way to decide it.
    """
    key = spec_for(stage.value).stage.value
    if key in _providers:
        raise StageError(
            f"a provider for stage '{key}' is already registered "
            f"({_providers[key]!r}); a stage has exactly one implementation"
        )
    _providers[key] = provider


def provider_for(stage: str) -> StageProvider | None:
    return _providers.get(stage)


def registered_stages() -> frozenset[str]:
    return frozenset(_providers)


@contextmanager
def provider_scope(stage: PipelineStage, provider: StageProvider) -> Iterator[None]:
    """Register a provider for the duration of a block.

    Exists for tests, and named so that is obvious. Phase 3's gate has to prove
    the orchestration emits the full §10 sequence in order on a valid chain, and
    that claim needs *something* at the seam — this is the honest way to supply
    it, rather than shipping a fake classifier and calling the phase complete.
    """
    key = stage.value
    previous = _providers.get(key)
    _providers[key] = provider
    try:
        yield
    finally:
        if previous is None:
            _providers.pop(key, None)
        else:
            _providers[key] = previous


ProviderFactory = Callable[[], StageProvider]
