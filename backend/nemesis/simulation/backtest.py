"""Two bundles, one corpus, and a quantified answer to "what would change".

The report this module produces is the thing an approver reads before pressing
Activate, so its failure modes are not "wrong number" — they are "reassuring
number". Three of them are designed against explicitly:

**Averaging away the tail.** A rubric change with a mean severity delta of 0.02
can still move four hundred complaints across a tier boundary and double one
department's urgent queue. So the report carries counts and extremes beside
means, and the tier transitions as a matrix rather than as a net. "Nothing moved
on average" is the sentence that gets a bad change approved.

**Reporting coverage that does not exist.** ``simulation.corpus`` cannot supply
``zone_code`` or ``tags`` — no event carries them yet — and an absent fact
compares ``False`` under every operator, so a candidate rule that turns on one
would show "0 complaints affected". Identical output to a genuinely inert change.
Every rule whose compiled condition references an unavailable fact is named in
``coverage_gaps``, and a report with gaps is not certifiable (see
``simulation.evaluation``).

**A sample that flatters.** The changed-case sample is not "the first hundred".
It is ordered by how much each case moved, so what an operator sees first is the
worst thing the change does. A sample taken in chain order shows whatever
happened in January.

**The diff is over values, not over fields.** ``CaseOutcome.comparable`` decides
what a difference *is*; this module compares two of those dicts key by key. A
hand-written field walk would silently stop covering whatever field was added
last, and the symptom would be a category of change that no report ever mentions.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

from nemesis.observability.logging import get_logger
from nemesis.policy.documents import PolicyKind, RoutingRules
from nemesis.policy.expressions import ROUTING_FACTS, compile_condition
from nemesis.simulation.corpus import UNAVAILABLE_FACTS, Corpus
from nemesis.simulation.engine import CaseOutcome, PolicyBundle, decide_all

log = get_logger(__name__)

#: How many changed cases the report carries in full. Bounded because a report
#: is a JSONB column and an operator's screen, and neither wants twenty thousand
#: rows. Ordered by impact rather than truncated by position — see the module
#: docstring.
MAX_SAMPLE: Final = 100

#: Severity movement below this is floating-point noise from re-running the same
#: arithmetic, not a change anybody made. Compared against the *rounded* values
#: in ``comparable`` so the threshold is about meaning rather than about IEEE 754.
SEVERITY_EPSILON: Final = 1e-9


@dataclass(frozen=True, slots=True)
class ChangedCase:
    """One complaint the candidate would have treated differently.

    Both sides are carried, not just the delta. "Severity moved by 1.4" is not
    actionable; "6.2 urgent → 7.6 critical, and it moved from Roads to
    Emergency" is, and it is the shape a dispute six months later needs.
    """

    complaint_id: uuid.UUID
    changed_fields: tuple[str, ...]
    baseline: Mapping[str, Any]
    candidate: Mapping[str, Any]
    severity_delta: float


@dataclass(frozen=True, slots=True)
class SeverityImpact:
    changed: int
    mean_delta: float
    max_increase: float
    max_decrease: float
    #: ``(from_tier, to_tier) -> count``, rendered as ``"from->to"`` keys so the
    #: whole report stays JSON without a custom encoder. A matrix rather than a
    #: net movement: "40 up, 40 down" and "0 moved" are the same net and very
    #: different changes.
    tier_transitions: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class SafetyImpact:
    newly_firing: int
    no_longer_firing: int
    #: Rule ids that started or stopped firing, so an author can tell whether
    #: the term they added is the one doing the work.
    rules_gained: tuple[str, ...]
    rules_lost: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DedupImpact:
    #: ``"merge->investigate" -> count``. The flip that matters most is
    #: ``distinct->merge``: a false merge suppresses a genuine citizen report,
    #: which §14.3 treats as strictly worse than an unmerged duplicate.
    transitions: Mapping[str, int]
    newly_merged: int
    no_longer_merged: int


@dataclass(frozen=True, slots=True)
class RoutingImpact:
    changed: int
    newly_unrouted: int
    newly_routed: int
    #: ``department_code -> net change in volume``. ``None`` is the unrouted
    #: bucket, rendered as ``"(unrouted)"``.
    department_deltas: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class SlaImpact:
    changed: int
    #: Complaints whose resolution budget shortened. Called out separately
    #: because a shorter budget is a new breach risk against work already in
    #: flight, while a longer one is only a relaxation.
    tightened: int
    loosened: int
    mean_resolution_hours_delta: float


@dataclass(frozen=True, slots=True)
class CoverageGap:
    """A rule the corpus cannot honestly evaluate, and why.

    Its own type rather than a string, because the API renders it and the
    certification path refuses on it. A gap is a *finding*, not a warning to be
    logged and moved past.
    """

    rule_id: str
    fact: str
    reason: str


@dataclass(frozen=True, slots=True)
class ImpactReport:
    """The whole answer, as one JSON-serialisable value.

    Stored on the run row and returned by the API unchanged. One shape, one
    place — a report the API assembles differently from the one the run stored
    is a report where the screen and the record can disagree, which is the exact
    failure the phase exists to prevent one level down.
    """

    tenant_id: uuid.UUID
    kind: PolicyKind
    baseline_stamps: Mapping[str, str]
    candidate_stamps: Mapping[str, str]
    window_start: datetime
    window_end: datetime
    case_count: int
    population: int
    sampling_stride: int
    affected: int
    severity: SeverityImpact
    safety: SafetyImpact
    dedup: DedupImpact
    routing: RoutingImpact
    sla: SlaImpact
    coverage_gaps: tuple[CoverageGap, ...]
    unknown_categories: tuple[str, ...]
    sample: tuple[ChangedCase, ...]
    generated_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    @property
    def affected_fraction(self) -> float:
        """Share of the corpus the candidate would have treated differently.

        Of the *corpus*, not of the population — and the report carries both
        numbers so the distinction is visible. Extrapolating a sampled fraction
        to the whole population is a reasonable thing to do and it is the
        caller's to do knowingly, not this module's to do silently.
        """
        return self.affected / self.case_count if self.case_count else 0.0

    @property
    def is_certifiable(self) -> bool:
        """Whether this report may back a certificate.

        False when any rule could not be evaluated. A certificate is a claim
        that the candidate was checked; issuing one off a report with a known
        blind spot would make the guardrail attest to a check that did not
        happen, which is worse than having no guardrail — an operator trusts it.
        """
        return not self.coverage_gaps


def compare(
    baseline: PolicyBundle,
    candidate: PolicyBundle,
    corpus: Corpus,
    *,
    tenant_id: uuid.UUID,
    kind: PolicyKind,
    calendars: Mapping[str | None, Any] | None = None,
) -> ImpactReport:
    """Run both bundles over the corpus and quantify the difference.

    Both bundles are run over the *same* case list in the same order, so a
    difference in the output is a difference in the policy and nothing else.
    Running the baseline from stored history instead — reusing what production
    decided at the time — was rejected: those decisions were made by a pipeline
    that has since changed in ways unrelated to policy, and the report would
    fold every one of those changes into the candidate's column.
    """
    before = decide_all(baseline, corpus.cases, calendars=calendars)
    after = decide_all(candidate, corpus.cases, calendars=calendars)

    changed = _changed_cases(before, after)
    report = ImpactReport(
        tenant_id=tenant_id,
        kind=kind,
        baseline_stamps=baseline.stamps(),
        candidate_stamps=candidate.stamps(),
        window_start=corpus.window.start,
        window_end=corpus.window.end,
        case_count=len(corpus.cases),
        population=corpus.population,
        sampling_stride=corpus.sampling_stride,
        affected=len(changed),
        severity=_severity_impact(before, after),
        safety=_safety_impact(before, after),
        dedup=_dedup_impact(before, after),
        routing=_routing_impact(before, after),
        sla=_sla_impact(before, after),
        coverage_gaps=coverage_gaps(candidate),
        unknown_categories=corpus.unknown_categories,
        sample=_sample(changed),
    )
    log.info(
        "backtest_compared",
        tenant_id=str(tenant_id),
        kind=kind.value,
        cases=report.case_count,
        affected=report.affected,
        coverage_gaps=len(report.coverage_gaps),
    )
    return report


def coverage_gaps(bundle: PolicyBundle) -> tuple[CoverageGap, ...]:
    """Rules whose conditions read a fact the corpus cannot supply.

    Compiled rather than pattern-matched against the source text. A condition
    mentioning ``zone_code`` inside a string literal is not reading the fact,
    and ``Condition.referenced`` — which the compiler already computes for the
    routing stage's benefit — is the exact set. Re-deriving it with a regex here
    would produce a check that is wrong in both directions.
    """
    routing = bundle.routing_rules
    if routing is None:
        return ()
    rules: RoutingRules = routing.body
    gaps: list[CoverageGap] = []
    for rule in rules.rules:
        if not rule.is_active:
            continue
        referenced = compile_condition(rule.condition, schema=ROUTING_FACTS).referenced
        for fact in sorted(referenced & set(UNAVAILABLE_FACTS)):
            gaps.append(
                CoverageGap(rule_id=rule.rule_id, fact=fact, reason=UNAVAILABLE_FACTS[fact])
            )
    return tuple(gaps)


# ---------------------------------------------------------------------------
# The diff
# ---------------------------------------------------------------------------


def _changed_cases(
    before: Sequence[CaseOutcome], after: Sequence[CaseOutcome]
) -> list[ChangedCase]:
    changed: list[ChangedCase] = []
    for baseline, candidate in zip(before, after, strict=True):
        left, right = baseline.comparable(), candidate.comparable()
        fields = tuple(key for key in sorted(left) if left[key] != right[key])
        if not fields:
            continue
        changed.append(
            ChangedCase(
                complaint_id=baseline.complaint_id,
                changed_fields=fields,
                baseline=left,
                candidate=right,
                severity_delta=candidate.final_severity - baseline.final_severity,
            )
        )
    return changed


def _sample(changed: Sequence[ChangedCase]) -> tuple[ChangedCase, ...]:
    """The most-moved cases first, deterministically.

    Sorted by absolute severity movement, then by how many fields moved, then by
    complaint id. The last key is what makes the sample stable across two runs
    of the same comparison — without it, cases that moved identically would be
    ordered by whatever the sort happened to see first, and "did this reproduce"
    would have a different answer each time.
    """
    ordered = sorted(
        changed,
        key=lambda case: (-abs(case.severity_delta), -len(case.changed_fields), case.complaint_id),
    )
    return tuple(ordered[:MAX_SAMPLE])


def _severity_impact(before: Sequence[CaseOutcome], after: Sequence[CaseOutcome]) -> SeverityImpact:
    deltas: list[float] = []
    transitions: dict[str, int] = {}
    for baseline, candidate in zip(before, after, strict=True):
        delta = candidate.final_severity - baseline.final_severity
        if abs(delta) > SEVERITY_EPSILON:
            deltas.append(delta)
        if baseline.sla.severity_tier != candidate.sla.severity_tier:
            key = f"{baseline.sla.severity_tier}->{candidate.sla.severity_tier}"
            transitions[key] = transitions.get(key, 0) + 1
    return SeverityImpact(
        changed=len(deltas),
        mean_delta=round(sum(deltas) / len(deltas), 6) if deltas else 0.0,
        max_increase=round(max(deltas), 6) if deltas else 0.0,
        max_decrease=round(min(deltas), 6) if deltas else 0.0,
        tier_transitions=dict(sorted(transitions.items())),
    )


def _safety_impact(before: Sequence[CaseOutcome], after: Sequence[CaseOutcome]) -> SafetyImpact:
    gained: set[str] = set()
    lost: set[str] = set()
    newly = no_longer = 0
    for baseline, candidate in zip(before, after, strict=True):
        if candidate.safety.fired and not baseline.safety.fired:
            newly += 1
            if candidate.safety.rule_id:
                gained.add(candidate.safety.rule_id)
        elif baseline.safety.fired and not candidate.safety.fired:
            no_longer += 1
            if baseline.safety.rule_id:
                lost.add(baseline.safety.rule_id)
    return SafetyImpact(
        newly_firing=newly,
        no_longer_firing=no_longer,
        rules_gained=tuple(sorted(gained)),
        rules_lost=tuple(sorted(lost)),
    )


def _dedup_impact(before: Sequence[CaseOutcome], after: Sequence[CaseOutcome]) -> DedupImpact:
    transitions: dict[str, int] = {}
    newly = no_longer = 0
    for baseline, candidate in zip(before, after, strict=True):
        if baseline.dedup.outcome == candidate.dedup.outcome:
            continue
        key = f"{baseline.dedup.outcome}->{candidate.dedup.outcome}"
        transitions[key] = transitions.get(key, 0) + 1
        if candidate.dedup.outcome == "merge":
            newly += 1
        elif baseline.dedup.outcome == "merge":
            no_longer += 1
    return DedupImpact(
        transitions=dict(sorted(transitions.items())),
        newly_merged=newly,
        no_longer_merged=no_longer,
    )


#: How the unrouted bucket appears in ``department_deltas``. A label rather than
#: a null key, because the map is JSON and a null key is not expressible — and
#: because "(unrouted)" is what an operator needs to read on a chart.
UNROUTED_LABEL: Final = "(unrouted)"


def _routing_impact(before: Sequence[CaseOutcome], after: Sequence[CaseOutcome]) -> RoutingImpact:
    deltas: dict[str, int] = {}
    changed = newly_unrouted = newly_routed = 0
    for baseline, candidate in zip(before, after, strict=True):
        left = baseline.route.department_code
        right = candidate.route.department_code
        if left == right and baseline.route.team_code == candidate.route.team_code:
            continue
        changed += 1
        if left is not None and right is None:
            newly_unrouted += 1
        elif left is None and right is not None:
            newly_routed += 1
        if left != right:
            deltas[left or UNROUTED_LABEL] = deltas.get(left or UNROUTED_LABEL, 0) - 1
            deltas[right or UNROUTED_LABEL] = deltas.get(right or UNROUTED_LABEL, 0) + 1
    return RoutingImpact(
        changed=changed,
        newly_unrouted=newly_unrouted,
        newly_routed=newly_routed,
        department_deltas=dict(sorted(deltas.items())),
    )


def _sla_impact(before: Sequence[CaseOutcome], after: Sequence[CaseOutcome]) -> SlaImpact:
    changed = tightened = loosened = 0
    deltas: list[float] = []
    for baseline, candidate in zip(before, after, strict=True):
        delta = candidate.sla.resolution_hours - baseline.sla.resolution_hours
        if (
            baseline.sla.resolution_hours == candidate.sla.resolution_hours
            and baseline.sla.response_hours == candidate.sla.response_hours
        ):
            continue
        changed += 1
        deltas.append(delta)
        if delta < 0:
            tightened += 1
        elif delta > 0:
            loosened += 1
    return SlaImpact(
        changed=changed,
        tightened=tightened,
        loosened=loosened,
        mean_resolution_hours_delta=round(sum(deltas) / len(deltas), 6) if deltas else 0.0,
    )


__all__ = [
    "MAX_SAMPLE",
    "SEVERITY_EPSILON",
    "UNROUTED_LABEL",
    "ChangedCase",
    "CoverageGap",
    "DedupImpact",
    "ImpactReport",
    "RoutingImpact",
    "SafetyImpact",
    "SeverityImpact",
    "SlaImpact",
    "compare",
    "coverage_gaps",
]
