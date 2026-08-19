"""Reconstructing what the pipeline *observed*, from the log, for a window of history.

This is the module a backtest's honesty actually depends on. The arithmetic in
``simulation.engine`` is production's own; the report is only as true as the
inputs fed into it, and there are exactly two ways to get those inputs and only
one of them is right.

**The wrong way: read the ``complaints`` projection.** It is current state. Its
``severity_score`` was written by whichever rubric was live when the complaint
was scored, its ``category`` may have been corrected by an operator last week,
and its ``cluster_id`` reflects merges that a *previous* threshold change
already moved. A backtest built on it compares a candidate policy against the
accumulated results of every policy change since — and reports a delta of zero
for a change that would in fact have moved thousands of reports, because the
projection has already absorbed the effect.

**The right way: fold the complaint's own event chain.** The fold is
``projections.registry.project`` — production's projectors, unchanged, because a
corpus builder that reimplemented them would be measuring its reimplementation
(the same argument ``simulation.engine`` makes about the arithmetic). What this
module adds is the *selection*: out of the folded state it takes **observations
only**.

The line between an observation and a decision is worth stating precisely,
because every field here sits on one side of it:

*Observations* — what a stage measured. ``classification_confidence`` is what
CLIP returned. ``severity_scored.components`` are the measured component values,
not the score they produced. ``trust_score`` is the accumulated §11.1 EXIF
evidence. ``cluster_match_found.image_similarity`` is what the encoder computed.
None of these change when a policy changes, which is what makes them a fair
input to a replay.

*Decisions* — what a policy concluded. The score, the tier, the SLA, the
department, the merge. Every one of them is the thing under test, and not one of
them is read into a ``DecisionCase``. A test asserts that, by field name, so the
distinction survives somebody adding a convenient shortcut.

**The category is an observation and stays one.** The *classifier's* output is
an observation; where that key sits in the taxonomy today is not. So the lineage
is resolved once, at corpus build time, against the taxonomy as it stands — and
the report says so. A backtest that silently re-parented last year's complaints
under this year's tree would be measuring two changes at once and attributing
both to the rubric.

**Three routing facts have no source in the log yet, and this module says so out
loud rather than defaulting them.** ``zone_code`` waits on Phase 19's geospatial
assignment, ``tags`` on Phase 14's workflow, and the visual half of the safety
ruleset on Phase 9's perception layer. ``policy.expressions`` gives absent facts
``False`` under every operator, so a candidate ruleset whose rules turn on one of
them would backtest as *"no complaints affected"* — the most dangerous possible
output, because it is indistinguishable from a change that genuinely moves
nothing. ``UNAVAILABLE_FACTS`` names them, the corpus carries the list, and
``simulation.backtest`` refuses to report coverage it does not have when a
candidate's conditions reference one.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.control_plane.calendars import WorkingWeek, load_working_week
from nemesis.db.models.calendar import BusinessCalendar
from nemesis.db.models.event import Event
from nemesis.db.models.taxonomy import TaxonomyNode
from nemesis.domain.lifecycle import EntityType
from nemesis.observability.logging import get_logger
from nemesis.projections.registry import ProjectedState, project
from nemesis.projections.replay import to_projection_event
from nemesis.simulation.engine import DecisionCase, DedupCandidate
from nemesis.simulation.errors import CorpusTooSmallError, SimulationValidationError

log = get_logger(__name__)

#: Below this, a comparison is not a finding. A backtest over three complaints
#: reports "no severity changes, no regressions" — a sentence that is true, is
#: the exact shape of an answer, and carries none of the content, arriving at
#: the moment somebody is looking for permission to activate. Thirty is not a
#: statistical claim; it is the point below which the report stops being able to
#: mislead by accident. ``CorpusTooSmallError`` names the number.
MINIMUM_CASES: Final = 30

#: The ceiling on a single run. A city-year is hundreds of thousands of
#: complaints, and folding all of them into memory is neither necessary nor
#: kind to the API worker doing it. Above this the window is **systematically
#: sampled** rather than truncated — see ``_sample``.
DEFAULT_MAX_CASES: Final = 20_000

#: Hard ceiling a caller may not raise. Present so ``max_cases`` is a knob for
#: making a run *smaller*, never a way to ask one API request to fold a million
#: event chains.
ABSOLUTE_MAX_CASES: Final = 100_000

_SUBMITTED: Final = "complaint_submitted"
_MATCH_FOUND: Final = "cluster_match_found"
_CLUSTER_CREATED: Final = "cluster_created"

#: Routing facts no event in the current catalog carries, each with the phase
#: that will supply it. Declared rather than quietly defaulted, because an
#: absent fact compares ``False`` under every operator — so a rule turning on one
#: of these backtests as "nothing affected", which reads exactly like a safe
#: change and is in fact a measurement that was never taken.
#:
#: This mapping is the reason ``simulation.backtest`` inspects a candidate's
#: compiled conditions: a report that cannot cover a rule has to say which rule,
#: rather than averaging the gap into a reassuring total.
UNAVAILABLE_FACTS: Final[Mapping[str, str]] = {
    "zone_code": "Phase 19 — zone assignment is geospatial and not yet an event",
    "tags": "Phase 14 — tags are department workflow state",
}


@dataclass(frozen=True, slots=True)
class CorpusWindow:
    """The span of history a run covers.

    Half-open, ``[start, end)``, so two adjacent windows partition the history
    between them without double-counting the complaint filed exactly on the
    boundary.
    """

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise SimulationValidationError(
                "a backtest window must be timezone-aware at both ends; a naive "
                "boundary silently means 'the server's zone', which is not a fact "
                "about the tenant"
            )
        if self.end <= self.start:
            raise SimulationValidationError(
                f"window end ({self.end.isoformat()}) must be after its start "
                f"({self.start.isoformat()})"
            )

    @property
    def days(self) -> float:
        return (self.end - self.start).total_seconds() / 86400.0


@dataclass(frozen=True, slots=True)
class Corpus:
    """The cases a run will decide over, and how honestly they represent the window.

    ``population`` and ``sampling_stride`` are carried rather than dropped
    because the difference between "12,000 complaints" and "12,000 of 480,000,
    every fortieth" is the difference between a report and a claim. Both appear
    in the impact report, and the API surfaces them.
    """

    window: CorpusWindow
    cases: tuple[DecisionCase, ...]
    #: How many complaints the window actually holds, before sampling.
    population: int
    #: 1 when every complaint in the window is present. ``n`` when every ``n``-th
    #: was taken.
    sampling_stride: int
    #: Distinct taxonomy keys the classifier emitted that the tenant no longer
    #: defines. Reported rather than hidden: an override on a retired category
    #: appearing to have no effect is a fact about the taxonomy, not the rubric.
    unknown_categories: tuple[str, ...]
    #: Routing facts this corpus cannot supply — ``UNAVAILABLE_FACTS``, carried
    #: on the corpus so the report can name them without importing this module's
    #: constants and drifting from them.
    unavailable_facts: tuple[str, ...] = tuple(sorted(UNAVAILABLE_FACTS))

    @property
    def is_sampled(self) -> bool:
        return self.sampling_stride > 1


async def build_corpus(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    window: CorpusWindow,
    max_cases: int = DEFAULT_MAX_CASES,
    minimum_cases: int = MINIMUM_CASES,
) -> Corpus:
    """Reconstruct every complaint filed in ``window`` as a set of decision inputs.

    The window selects *complaints*, not events: a report filed on the last day
    of the window has its whole chain folded, including the stages that ran the
    following morning. Selecting events instead would truncate exactly the
    complaints nearest the boundary, and truncation looks like a classification
    that never happened.
    """
    if max_cases < 1 or max_cases > ABSOLUTE_MAX_CASES:
        raise SimulationValidationError(
            f"max_cases must be between 1 and {ABSOLUTE_MAX_CASES}; a run larger than "
            f"that is a batch job, not an API request"
        )

    identifiers, population, stride = await _select_complaints(
        session, tenant_id=tenant_id, window=window, max_cases=max_cases
    )
    if len(identifiers) < minimum_cases:
        raise CorpusTooSmallError(
            f"{len(identifiers)} complaint(s) in {window.start.date()}..{window.end.date()}, "
            f"and a comparison needs at least {minimum_cases} to say anything. A report "
            f"over a handful of reports says 'no regressions' with the same confidence "
            f"whether or not that is true. Widen the window."
        )

    cases, unknown = await build_cases(session, tenant_id=tenant_id, identifiers=identifiers)
    log.info(
        "backtest_corpus_built",
        tenant_id=str(tenant_id),
        cases=len(cases),
        population=population,
        sampling_stride=stride,
        window_days=round(window.days, 2),
    )
    return Corpus(
        window=window,
        cases=cases,
        population=population,
        sampling_stride=stride,
        unknown_categories=tuple(sorted(unknown)),
    )


async def build_cases(
    session: AsyncSession, *, tenant_id: uuid.UUID, identifiers: Sequence[uuid.UUID]
) -> tuple[tuple[DecisionCase, ...], set[str]]:
    """Reconstruct named complaints, without a window.

    The route an *evaluation* takes: a labelled set names specific complaints,
    chosen by a human because they are the ones worth arguing about, and
    selecting them by date would be selecting different ones.

    Complaints that cannot be reconstructed — archived out of the partitions, or
    never in this tenant — are silently absent from the result rather than
    raising. The caller compares what it asked for against what it got and
    reports the difference as *unresolvable*, which is a different finding from
    a candidate getting a label wrong and must not be counted as one.
    """
    states, channels = await _fold_states(session, tenant_id=tenant_id, identifiers=identifiers)
    candidates = await _dedup_candidates(session, tenant_id=tenant_id, identifiers=identifiers)

    categories = {
        str(state["category"]) for state in states.values() if state.get("category") is not None
    }
    lineages, unknown = await _lineages(session, tenant_id=tenant_id, categories=categories)

    cases = tuple(
        _case(
            complaint_id,
            state,
            lineages,
            candidates.get(complaint_id),
            channels.get(complaint_id),
        )
        for complaint_id, state in states.items()
        if state.get("reported_at") is not None
    )
    return cases, unknown


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


async def _select_complaints(
    session: AsyncSession, *, tenant_id: uuid.UUID, window: CorpusWindow, max_cases: int
) -> tuple[tuple[uuid.UUID, ...], int, int]:
    """Which complaints the run covers, and how they were chosen.

    When the window holds more than ``max_cases``, the excess is handled by
    **systematic sampling over the ordering by submission time**, not by taking
    the most recent N. Taking the most recent would turn "backtested over twelve
    months" into "backtested over the last three weeks" without changing a word
    of the report — and seasonality is the single largest source of variation in
    a civic complaint stream, so the three weeks it silently picks are the least
    representative choice available.

    The stride is deterministic, so re-running the same window produces the same
    corpus and "did this reproduce" stays answerable.
    """
    rows = await session.execute(
        select(Event.entity_id)
        .where(
            Event.tenant_id == tenant_id,
            Event.entity_type == EntityType.COMPLAINT.value,
            Event.event_type == _SUBMITTED,
            Event.occurred_at >= window.start,
            Event.occurred_at < window.end,
        )
        .order_by(Event.occurred_at, Event.entity_id)
    )
    ordered = [row[0] for row in rows.all()]
    population = len(ordered)
    if population <= max_cases:
        return tuple(ordered), population, 1

    stride = math.ceil(population / max_cases)
    return tuple(ordered[::stride]), population, stride


async def _fold_states(
    session: AsyncSession, *, tenant_id: uuid.UUID, identifiers: Sequence[uuid.UUID]
) -> tuple[dict[uuid.UUID, ProjectedState], dict[uuid.UUID, str]]:
    """Fold every selected complaint's chain, in one query rather than N.

    ``replay_entity`` is the per-entity route and is right for one complaint;
    twenty thousand of them is twenty thousand round trips. The fold itself is
    the same ``project`` call, on the same upcasted events, in the same
    sequence order — so this is a batching of the read, not a second projector.

    Snapshots are deliberately not used. They are keyed to ``PROJECTOR_VERSION``
    and exist to make a hot read cheap; a backtest is a cold batch, and reading
    from sequence 1 every time removes a whole class of "the snapshot was
    written by a different build" question from a report somebody is about to
    make a decision on.

    The submission *channel* is collected alongside the fold rather than out of
    it. ``complaint_submitted`` carries ``submitted_via`` and the complaint
    projector legitimately drops it — nothing in current state needs to know
    whether a report arrived by web or by WhatsApp — but it is a declared
    routing fact, so a corpus that omitted it would make every rule mentioning
    ``submitted_via`` backtest as matching nothing. Reading it here costs one
    dictionary lookup on a row already in hand; the alternative is changing a
    production projector, which bumps ``PROJECTOR_VERSION`` and invalidates
    every snapshot in the system to serve a batch job.
    """
    if not identifiers:
        return {}, {}

    grouped: dict[uuid.UUID, list[Event]] = {identifier: [] for identifier in identifiers}
    channels: dict[uuid.UUID, str] = {}
    for chunk in _chunks(identifiers, 1_000):
        rows = await session.execute(
            select(Event)
            .where(
                Event.tenant_id == tenant_id,
                Event.entity_type == EntityType.COMPLAINT.value,
                Event.entity_id.in_(chunk),
            )
            .order_by(Event.entity_id, Event.sequence)
        )
        for event in rows.scalars().all():
            grouped[event.entity_id].append(event)
            if event.event_type == _SUBMITTED:
                channel = event.payload.get("submitted_via")
                if isinstance(channel, str) and channel:
                    channels[event.entity_id] = channel

    states = {
        identifier: project(
            EntityType.COMPLAINT.value, [to_projection_event(event) for event in events]
        )
        for identifier, events in grouped.items()
        if events
    }
    return states, channels


async def _dedup_candidates(
    session: AsyncSession, *, tenant_id: uuid.UUID, identifiers: Sequence[uuid.UUID]
) -> dict[uuid.UUID, tuple[DedupCandidate, int]]:
    """The neighbour each report was compared against, from the cluster chains.

    ``cluster_match_found`` records both stage similarities and the geo distance
    — Phase 3 shaped it that way for this phase — so a threshold change replays
    against what the encoders actually produced. Nothing here re-embeds a
    photograph: doing so would fold a year of *model* drift into a report about
    a *policy* change, and the two would be indistinguishable in the output.

    ``candidate_last_reported_at`` is the ``occurred_at`` of the previous event
    on the same cluster chain — the creation, or the previous match. That is
    exactly "when this incident was last reported", read off the chain rather
    than off the cluster projection, whose ``last_reported`` has since moved on.

    The report count travels with it, from ``report_count_after``, for the same
    reason: it is a declared routing fact, and the cluster projection's current
    count is the total the incident reached rather than the number that existed
    when this report was routed. A rule reading ``report_count >= 5`` scored
    against today's total would match reports that arrived when the count was 1.
    """
    wanted = set(identifiers)
    if not wanted:
        return {}

    rows = await session.execute(
        select(Event)
        .where(
            Event.tenant_id == tenant_id,
            Event.entity_type == EntityType.COMPLAINT_CLUSTER.value,
            Event.event_type.in_([_CLUSTER_CREATED, _MATCH_FOUND]),
        )
        .order_by(Event.entity_id, Event.sequence)
    )

    candidates: dict[uuid.UUID, tuple[DedupCandidate, int]] = {}
    previous_at: dict[uuid.UUID, datetime] = {}
    for event in rows.scalars().all():
        if event.event_type == _MATCH_FOUND:
            payload: Mapping[str, Any] = event.payload
            complaint_id = uuid.UUID(str(payload["complaint_id"]))
            last_reported = previous_at.get(event.entity_id)
            if complaint_id in wanted and last_reported is not None:
                candidates[complaint_id] = (
                    DedupCandidate(
                        cluster_id=event.entity_id,
                        geo_distance_meters=float(payload["geo_distance_meters"]),
                        image_similarity=_optional_float(payload.get("image_similarity")),
                        text_similarity=_optional_float(payload.get("text_similarity")),
                        candidate_last_reported_at=last_reported,
                    ),
                    int(payload.get("report_count_after") or 1),
                )
        previous_at[event.entity_id] = event.occurred_at
    return candidates


async def _lineages(
    session: AsyncSession, *, tenant_id: uuid.UUID, categories: set[str]
) -> tuple[dict[str, tuple[str, ...]], set[str]]:
    """Category → ancestor chain, most specific first, in one query.

    ``policy.resolver.category_lineage`` is the per-category route and issues one
    query each; a corpus has tens of distinct categories and thousands of
    complaints, so the walk is done once per *key*. The derivation is identical —
    the materialised ``path`` split and reversed — and a test asserts the two
    agree, because a corpus that resolved lineage differently from production
    would report deltas that are artefacts of this module.
    """
    if not categories:
        return {}, set()

    rows = await session.execute(
        select(TaxonomyNode.key, TaxonomyNode.path).where(
            TaxonomyNode.tenant_id == tenant_id, TaxonomyNode.key.in_(sorted(categories))
        )
    )
    lineages = {key: tuple(reversed(path.split("/"))) for key, path in rows.all()}
    unknown = categories - set(lineages)
    # An unknown key still resolves to itself, exactly as the resolver does: the
    # classifier can emit a key for a node deactivated between classification
    # and scoring, and dropping the complaint would be a worse answer than
    # scoring it against the default band.
    for key in unknown:
        lineages[key] = (key,)
    return lineages, unknown


async def load_calendars(
    session: AsyncSession, *, tenant_id: uuid.UUID, codes: Iterable[str | None]
) -> dict[str | None, WorkingWeek]:
    """Working weeks for every calendar an SLA matrix names, plus the default.

    Loaded once per run and passed into ``engine.decide``, which keeps that
    function pure. A code naming a calendar the tenant does not have is skipped
    rather than raised: the engine falls back to the default and then to a
    continuous week, which is what ``load_working_week`` itself does, and a
    backtest that refused to run because one SLA row names a retired calendar
    would be hostage to an unrelated tidy-up.
    """
    weeks: dict[str | None, WorkingWeek] = {
        None: await load_working_week(session, tenant_id=tenant_id)
    }
    wanted = sorted({code for code in codes if code is not None})
    if not wanted:
        return weeks

    rows = await session.execute(
        select(BusinessCalendar.code, BusinessCalendar.id).where(
            BusinessCalendar.tenant_id == tenant_id, BusinessCalendar.code.in_(wanted)
        )
    )
    for code, calendar_id in rows.all():
        weeks[code] = await load_working_week(session, tenant_id=tenant_id, calendar_id=calendar_id)
    return weeks


# ---------------------------------------------------------------------------
# Projection → case
# ---------------------------------------------------------------------------

#: State keys a ``DecisionCase`` may read. An allow-list rather than a
#: deny-list, and the reason is the module docstring's central claim: the
#: projection also holds ``severity_score``, ``severity_tier``, ``cluster_id``
#: and ``department_id``, every one of which is a *decision* under test. A
#: deny-list would let the next field added to the projection default into the
#: corpus, and the symptom would be a backtest that reports no change because it
#: was fed the answer.
OBSERVATION_KEYS: Final[frozenset[str]] = frozenset(
    {
        "reported_at",
        "category",
        "description_text",
        "transcript",
        "locale",
        "trust_score",
        "severity_breakdown",
    }
)

#: Projection keys a case may never read, with what each one is. Named
#: explicitly rather than left to "anything not in ``OBSERVATION_KEYS``", so the
#: test that guards this distinction can assert both directions — that every
#: observation is used and that no decision is. A backtest fed its own answer
#: reports "no change" for every candidate, and reports it convincingly.
DECISION_KEYS: Final[frozenset[str]] = frozenset(
    {
        "status",
        "severity_score",
        "severity_policy_version",
        "is_safety_flagged",
        "safety_rule_id",
        "safety_ruleset_version",
        "cluster_id",
        "department_id",
    }
)


def _case(
    complaint_id: uuid.UUID,
    state: ProjectedState,
    lineages: Mapping[str, tuple[str, ...]],
    candidate: tuple[DedupCandidate, int] | None,
    submitted_via: str | None,
) -> DecisionCase:
    category = state.get("category")
    category_key = str(category) if category is not None else None
    # The transcript is a fallback rather than an addition. §11.2 matching runs
    # over one haystack, and concatenating a description with its own audio
    # transcript would double-count a term that appears in both — which turns a
    # substring rule into a rule that fires on half the evidence.
    text = state.get("description_text") or state.get("transcript")
    return DecisionCase(
        complaint_id=complaint_id,
        reported_at=_as_datetime(state["reported_at"]),
        category=category_key,
        lineage=lineages.get(category_key, (category_key,)) if category_key else (),
        measurements=_measurements(state),
        description_text=str(text) if text is not None else None,
        locale=_optional_str(state.get("locale")),
        # Empty, always, and deliberately: the visual half of §11.2 is Phase 9's
        # perception layer, which emits no event yet. Fabricating prompts from
        # the classifier's category would make a visual rule appear to fire on
        # evidence nothing produced.
        visual_matches=(),
        zone_code=None,
        report_count=candidate[1] if candidate is not None else 1,
        trust_score=_optional_float(state.get("trust_score")),
        submitted_via=submitted_via,
        tags=(),
        dedup_candidate=candidate[0] if candidate is not None else None,
    )


def _measurements(state: ProjectedState) -> dict[str, float]:
    """The component values a previous scoring *measured*, not the score it produced.

    ``severity_scored.components`` is the only place these survive, which is why
    Phase 2 put them in the payload rather than only in the projection: a rubric
    that reweights existing components can be replayed exactly, and one that
    adds a component scores the missing one at ``missing_component_score``,
    which is what production would do for a complaint filed the day before the
    component existed.

    A complaint that was never scored — degraded classification, or one still in
    flight — contributes an empty mapping and is scored entirely from
    ``missing_component_score`` under both bundles. It therefore appears in the
    population and cannot appear as a *changed* case unless the rubric's missing
    handling itself moved, which is correct: nothing was measured, so nothing
    about it can distinguish two rubrics that treat absence identically.
    """
    breakdown = state.get("severity_breakdown")
    raw = breakdown.get("components") if isinstance(breakdown, dict) else None
    if not isinstance(raw, dict):
        return {}
    measurements: dict[str, float] = {}
    for key, value in raw.items():
        number = _optional_float(value)
        if number is not None:
            measurements[str(key)] = number
    return measurements


def _chunks(values: Sequence[uuid.UUID], size: int) -> Iterable[Sequence[uuid.UUID]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _as_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:  # pragma: no cover - projections format with a zone
        raise SimulationValidationError(
            "a projected reported_at carried no timezone; a case with no zone cannot "
            "be compared against an SLA deadline"
        )
    return parsed


def _optional_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _optional_str(value: object) -> str | None:
    return str(value) if isinstance(value, str) and value else None


def _string_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, list | tuple):
        return tuple(str(item) for item in value if isinstance(item, str))
    return ()


__all__ = [
    "ABSOLUTE_MAX_CASES",
    "DECISION_KEYS",
    "DEFAULT_MAX_CASES",
    "MINIMUM_CASES",
    "OBSERVATION_KEYS",
    "UNAVAILABLE_FACTS",
    "Corpus",
    "CorpusWindow",
    "build_cases",
    "build_corpus",
    "load_calendars",
]
