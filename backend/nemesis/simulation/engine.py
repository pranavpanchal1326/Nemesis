"""One complaint, one set of policies, one outcome — as a pure function.

Everything Phase 7 claims rests on this module being *total, deterministic, and
free of I/O*. A backtest that could take a different path on a Tuesday, read a
clock, or touch the database is not a backtest; it is a second production run
with a different name, and its report would be a description of the run rather
than of the policy.

So ``decide`` takes every input by value and returns every output by value. It
has no session parameter, so it cannot query. It has no ``now`` parameter, so it
cannot drift: every instant it produces is derived from the case's own
``reported_at``. It calls only the arithmetic in ``policy.resolver`` and the
calendar walk in ``control_plane.calendars`` — both already pure, both already
the ones production uses.

**That last clause is the point, and it is worth being blunt about.** A
simulator that reimplements the scoring it simulates measures its own
reimplementation. Every number here comes from the function the pipeline calls:
``score_severity``, ``evaluate_safety``, ``evaluate_routing``,
``resolve_sla_entry``, ``resolve_deadline``, ``dedup_outcome``. When Phases 8,
10 and 12 wire those into the live stages, they will be wiring in the same
functions this module already calls — so a divergence between the simulation and
production is a divergence in the *inputs*, which the corpus builder can be
audited for, rather than in the arithmetic, which cannot.

**Order of operations is fixed here and stated, because it is where the meaning
lives.** Safety first, because §11.2's fail-safe outranks a score. Then the
rubric, then the safety floor over the top of it, then the tier, then the SLA,
then the route. Routing sees the *final* severity, which is why it comes last:
a rule reading ``severity > 8`` must see the number the complaint actually
carries, including the floor a gas-leak keyword put there.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Final

from nemesis.control_plane.calendars import WorkingWeek, resolve_deadline
from nemesis.policy.documents import (
    DedupThresholds,
    PerceptionCalibration,
    PolicyKind,
    RateCard,
    RoutingRules,
    SafetyRuleset,
    SeverityRubric,
    SlaMatrix,
    TrustThresholds,
)
from nemesis.policy.resolver import (
    Resolved,
    RouteDecision,
    SafetyDecision,
    SeverityResult,
    combined_dedup_confidence,
    dedup_outcome,
    evaluate_routing,
    evaluate_safety,
    resolve_dedup_band,
    resolve_severity_override,
    resolve_severity_tier,
    resolve_sla_entry,
    routing_facts,
    score_severity,
)

#: The dedup verdict for a report that has no candidate to compare against, or
#: whose only candidate is outside the band's geo radius or time window. A named
#: constant because it appears in the report, in the diff, and in three tests,
#: and a bare ``"distinct"`` repeated in four files is how the fourth one ends
#: up spelled ``"unique"``.
NO_MATCH: Final = "distinct"

#: The kinds ``decide`` actually reads. A backtest of anything else would run
#: two identical decisions and report "nothing changed", which is indexed to the
#: candidate being inert and is in fact indexed to this function never having
#: looked at it — the same output for two opposite facts.
#:
#: ``rate_card``, ``trust_thresholds`` and ``perception_calibration`` are the
#: three outside the set, for different reasons and with the same consequence.
#: §17.2 deviation detection reads a rate card and lives in Phase 17; §11's
#: checks read pixels, EXIF and device fingerprints, which the corpus cannot
#: reconstruct because the log records what each check *concluded*, not what it
#: ran on. ``runs.run_backtest`` refuses all three by name rather than producing
#: a report about none of them.
#:
#: Perception is outside the set *today* rather than permanently, and the
#: difference is worth stating. ``classification_scored`` v2 records
#: ``raw_similarities`` — the model's opinion before any governed number touched
#: it — precisely so that a calibration change becomes replayable: temperatures,
#: biases and abstain floors are pure arithmetic over those numbers. What is
#: missing is the corpus reader, because ``CorpusCase`` is shaped around the
#: facts a severity or routing decision needs and has nowhere to carry a
#: similarity map. Building it is Phase 11's work, and claiming the kind were
#: decidable before that reader exists would produce exactly the answer Phase 7
#: names as worthless: "0 complaints affected", for a change that affects every
#: one of them.
DECIDABLE_KINDS: Final[frozenset[PolicyKind]] = frozenset(
    {
        PolicyKind.SEVERITY_RUBRIC,
        PolicyKind.DEDUP_THRESHOLDS,
        PolicyKind.SAFETY_RULESET,
        PolicyKind.SLA_MATRIX,
        PolicyKind.ROUTING_RULES,
    }
)

#: The continuous calendar used when a case names none and the tenant has no
#: default. Matches ``calendars.load_working_week``'s own fallback exactly —
#: §27.2's durations taken literally — so a simulated deadline and a production
#: deadline agree for a tenant that has configured no working hours.
CONTINUOUS_WEEK: Final = WorkingWeek(timezone="UTC", is_continuous=True)


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PolicyBundle:
    """Every document a decision needs, resolved together at one instant.

    A bundle rather than six arguments, and resolved *together* rather than one
    call per kind, for the reason ``Resolved`` exists at all: a report whose
    severity came from revision 7 and whose SLA came from revision 8 describes a
    configuration that was never live. The bundle is the unit that gets compared,
    so the unit that gets read has to be the same shape.

    ``routing_rules`` and ``rate_card`` are optional because they have no
    platform baseline — a tenant that has authored no routing document leaves
    work unrouted, which is a real state with a correct behaviour rather than an
    error. See ``policy.baselines``.

    ``trust_thresholds`` is carried but **``decide`` does not read it**, and the
    distinction is the reason ``DECIDABLE_KINDS`` below exists. Phase 8's §11
    checks consume EXIF distances, perceptual hashes and device fingerprints,
    none of which the corpus can reconstruct: the log records that a check ran
    and what it concluded, not the pixels it ran on. A bundle that silently
    omitted the document would make ``stamps()`` describe a configuration that
    is not the one in force; a ``decide`` that pretended to use it would report
    "0 complaints affected" for a real change, which Phase 7 already names as
    the exact shape of an answer with none of the content.
    """

    severity_rubric: Resolved[SeverityRubric]
    dedup_thresholds: Resolved[DedupThresholds]
    safety_ruleset: Resolved[SafetyRuleset]
    sla_matrix: Resolved[SlaMatrix]
    routing_rules: Resolved[RoutingRules] | None = None
    rate_card: Resolved[RateCard] | None = None
    trust_thresholds: Resolved[TrustThresholds] | None = None
    perception_calibration: Resolved[PerceptionCalibration] | None = None

    def stamps(self) -> dict[str, str]:
        """Which revision of each kind this bundle carries.

        Written into every outcome and into the run record. An impact report
        that does not name the two configurations it compared is a table of
        numbers nobody can reproduce six months later, which is the exact
        failure ``severity_scored.policy_version`` exists to prevent, one level
        up.
        """
        stamps = {
            PolicyKind.SEVERITY_RUBRIC.value: self.severity_rubric.stamp,
            PolicyKind.DEDUP_THRESHOLDS.value: self.dedup_thresholds.stamp,
            PolicyKind.SAFETY_RULESET.value: self.safety_ruleset.stamp,
            PolicyKind.SLA_MATRIX.value: self.sla_matrix.stamp,
        }
        if self.routing_rules is not None:
            stamps[PolicyKind.ROUTING_RULES.value] = self.routing_rules.stamp
        if self.rate_card is not None:
            stamps[PolicyKind.RATE_CARD.value] = self.rate_card.stamp
        if self.trust_thresholds is not None:
            stamps[PolicyKind.TRUST_THRESHOLDS.value] = self.trust_thresholds.stamp
        if self.perception_calibration is not None:
            stamps[PolicyKind.PERCEPTION_CALIBRATION.value] = self.perception_calibration.stamp
        return stamps

    def resolved_for(self, kind: PolicyKind) -> Resolved[Any] | None:
        """The document of one kind, or ``None`` for an unauthored optional kind.

        A mapping rather than ``getattr(self, kind.value)``: the attribute names
        happen to match the enum values today, and a lookup that depends on that
        coincidence breaks silently the first time one is renamed.
        """
        return {
            PolicyKind.SEVERITY_RUBRIC: self.severity_rubric,
            PolicyKind.DEDUP_THRESHOLDS: self.dedup_thresholds,
            PolicyKind.SAFETY_RULESET: self.safety_ruleset,
            PolicyKind.SLA_MATRIX: self.sla_matrix,
            PolicyKind.ROUTING_RULES: self.routing_rules,
            PolicyKind.RATE_CARD: self.rate_card,
            PolicyKind.TRUST_THRESHOLDS: self.trust_thresholds,
            PolicyKind.PERCEPTION_CALIBRATION: self.perception_calibration,
        }[kind]


@dataclass(frozen=True, slots=True)
class DedupCandidate:
    """The nearest prior incident this report was compared against.

    Reconstructed from ``cluster_match_found``, which Phase 3 shaped for exactly
    this: it records both stage similarities and the geo distance, so a
    threshold change can be replayed against what the old thresholds actually
    saw rather than against a re-run of the encoder. Re-embedding a year of
    photographs to backtest a number would cost more than the change is worth
    and would silently fold *model* drift into a report about *policy*.
    """

    cluster_id: uuid.UUID
    geo_distance_meters: float
    image_similarity: float | None
    text_similarity: float | None
    #: When the incident this was compared against was last reported. The time
    #: window is measured against it, so it has to travel with the candidate.
    candidate_last_reported_at: datetime


@dataclass(frozen=True, slots=True)
class DecisionCase:
    """One complaint's decision inputs, as they were at the time.

    Reconstructed from the complaint's own event chain — never from the
    ``complaints`` projection. The projection holds *current* state, which has
    been rewritten by every policy change since; replaying against it would
    compare a candidate policy to the results of the policy that is live now,
    and report a delta of zero for a change that would in fact have moved
    thousands of reports. See ``simulation.corpus``.

    Every field is what some stage *observed*, not what it decided. Observations
    are stable — a CLIP confidence recorded in March is still that confidence —
    while decisions are exactly the thing under test.
    """

    complaint_id: uuid.UUID
    reported_at: datetime
    #: The classified taxonomy key, and its ancestors most-specific-first. The
    #: lineage travels with the case rather than being re-derived at decision
    #: time because the taxonomy may have been reshaped since, and a backtest
    #: that silently re-parents last year's complaints is measuring two changes
    #: at once.
    category: str | None = None
    lineage: tuple[str, ...] = ()
    #: Rubric component measurements, keyed by ``RubricComponent.key``. A
    #: component the rubric declares but the case has no measurement for takes
    #: ``missing_component_score`` — which is what production does, and what
    #: makes a rubric that *adds* a component still scoreable against history.
    measurements: Mapping[str, float] = field(default_factory=dict)
    description_text: str | None = None
    locale: str | None = None
    visual_matches: tuple[str, ...] = ()
    zone_code: str | None = None
    report_count: int = 1
    trust_score: float | None = None
    submitted_via: str | None = None
    tags: tuple[str, ...] = ()
    dedup_candidate: DedupCandidate | None = None


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SlaOutcome:
    """The budget a complaint was given, and when it ran out."""

    severity_tier: str
    response_hours: float
    resolution_hours: float
    calendar_code: str | None
    response_due_at: datetime
    resolution_due_at: datetime


@dataclass(frozen=True, slots=True)
class DedupVerdict:
    """What dedup would have done, and on what evidence."""

    outcome: str
    confidence: float
    band_category: str | None
    within_window: bool
    within_radius: bool


@dataclass(frozen=True, slots=True)
class CaseOutcome:
    """Everything one policy bundle decided about one complaint.

    Frozen and built only from primitives and other frozen values, so two
    outcomes compare by value — which is what makes the diff in
    ``simulation.backtest`` a comparison rather than a hand-written field walk
    that acquires a bug the day somebody adds a field and forgets it.
    """

    complaint_id: uuid.UUID
    safety: SafetyDecision
    severity: SeverityResult
    #: The score after the safety floor, which is what everything downstream
    #: sees. ``severity.score`` is the rubric's own answer, kept separately
    #: because "the rubric said 3.1 and the keyword floor took it to 9" is a
    #: different finding from "the rubric said 9".
    final_severity: float
    scoring_bypassed: bool
    safety_floor_applied: bool
    sla: SlaOutcome
    route: RouteDecision
    dedup: DedupVerdict
    stamps: Mapping[str, str]

    def comparable(self) -> dict[str, Any]:
        """The fields a diff is *about*, as plain JSON values.

        Deliberately narrower than the whole outcome. Two runs of the same
        bundle produce identical breakdowns, so including every component score
        in the comparison key would be free; what it would cost is a report
        where "0.05 of visual damage moved" and "this complaint changed
        department" are the same kind of row. The comparison is over the
        decisions somebody acts on.
        """
        return {
            "safety_fired": self.safety.fired,
            "safety_rule_id": self.safety.rule_id,
            "final_severity": round(self.final_severity, 6),
            "severity_tier": self.sla.severity_tier,
            "response_hours": self.sla.response_hours,
            "resolution_hours": self.sla.resolution_hours,
            "resolution_due_at": self.sla.resolution_due_at.isoformat(),
            "department_code": self.route.department_code,
            "team_code": self.route.team_code,
            "routing_rule_id": self.route.rule_id,
            "dedup_outcome": self.dedup.outcome,
            "dedup_confidence": round(self.dedup.confidence, 6),
        }

    def digest(self) -> str:
        """A stable hash of ``comparable``.

        What a run record stores per case instead of the case itself. A
        twelve-month corpus is hundreds of thousands of outcomes, and storing
        them all would make one run a gigabyte; storing the digest keeps "did
        this rerun reproduce" answerable, which is the property that matters.
        """
        encoded = json.dumps(self.comparable(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------


def decide(
    bundle: PolicyBundle,
    case: DecisionCase,
    *,
    calendars: Mapping[str | None, WorkingWeek] | None = None,
) -> CaseOutcome:
    """Apply a bundle to a case. Pure, total, and independent of the clock.

    ``calendars`` maps an SLA entry's ``calendar_code`` to the working week it
    names, with ``None`` for the tenant default. Passed in rather than loaded so
    this function keeps its central property; ``simulation.corpus`` loads them
    once per run, because a per-case load would be a query per complaint against
    a table whose contents do not change during a backtest.

    A code with no entry falls back to a continuous week rather than raising. A
    calendar deleted since the complaint was filed is a real state, and refusing
    to score the whole corpus because one SLA row names a retired calendar would
    make the report hostage to an unrelated tidy-up.
    """
    weeks = calendars or {}

    safety = evaluate_safety(
        bundle.safety_ruleset.body,
        text=case.description_text,
        locale=case.locale,
        visual_matches=case.visual_matches,
    )

    rubric = bundle.severity_rubric.body
    floor, multiplier, bypasses = resolve_severity_override(rubric, lineage=case.lineage)
    if bypasses:
        # §11.2's shortcut: a category that never waits for a score. The floor
        # *is* the score, and the breakdown is empty rather than fabricated —
        # recording components that did not decide anything would make the
        # §13.1 explanation ("your report scored 9 because…") a fiction in the
        # one case where the honest answer is "because of what it is".
        severity = SeverityResult(
            score=floor,
            components={},
            weights={},
            floor_applied=floor,
            multiplier_applied=multiplier,
        )
    else:
        severity = score_severity(rubric, measurements=case.measurements, lineage=case.lineage)

    final_severity = severity.score
    safety_floor_applied = False
    if safety.fired and safety.severity_floor > final_severity:
        final_severity = min(safety.severity_floor, 10.0)
        safety_floor_applied = True

    matrix = bundle.sla_matrix.body
    tier = resolve_severity_tier(matrix, score=final_severity)
    entry = resolve_sla_entry(matrix, lineage=case.lineage, severity_tier=tier)
    week = weeks.get(entry.calendar_code) or weeks.get(None) or CONTINUOUS_WEEK
    sla = SlaOutcome(
        severity_tier=tier,
        response_hours=entry.response_hours,
        resolution_hours=entry.resolution_hours,
        calendar_code=entry.calendar_code,
        response_due_at=resolve_deadline(
            week=week, start=case.reported_at, budget=timedelta(hours=entry.response_hours)
        ).due_at,
        resolution_due_at=resolve_deadline(
            week=week, start=case.reported_at, budget=timedelta(hours=entry.resolution_hours)
        ).due_at,
    )

    if bundle.routing_rules is None:
        route = RouteDecision(department_code=None, team_code=None, rule_id=None)
    else:
        route = evaluate_routing(
            bundle.routing_rules.body,
            routing_facts(
                category=case.category,
                lineage=case.lineage,
                severity=final_severity,
                severity_tier=tier,
                zone_code=case.zone_code,
                report_count=case.report_count,
                is_safety_triggered=safety.fired,
                trust_score=case.trust_score,
                locale=case.locale,
                submitted_via=case.submitted_via,
                tags=case.tags,
            ),
        )

    return CaseOutcome(
        complaint_id=case.complaint_id,
        safety=safety,
        severity=severity,
        final_severity=final_severity,
        scoring_bypassed=bypasses,
        safety_floor_applied=safety_floor_applied,
        sla=sla,
        route=route,
        dedup=_dedup_verdict(bundle.dedup_thresholds.body, case),
        stamps=bundle.stamps(),
    )


def _dedup_verdict(thresholds: DedupThresholds, case: DecisionCase) -> DedupVerdict:
    """What dedup would have done with the candidate this report was compared to.

    The geo and time gates are applied *before* the confidence bands rather than
    alongside them, matching §14.1's two-stage design: stage 1 is a cheap filter
    that eliminates candidates, and a report whose only neighbour is 400 metres
    away is distinct regardless of how similar the photographs are. Folding the
    gates into the confidence would make a threshold change appear to move
    reports the geo filter never let near each other.
    """
    band = resolve_dedup_band(thresholds, lineage=case.lineage)
    candidate = case.dedup_candidate
    if candidate is None:
        return DedupVerdict(
            outcome=NO_MATCH,
            confidence=0.0,
            band_category=band.category,
            within_window=False,
            within_radius=False,
        )

    within_radius = candidate.geo_distance_meters <= band.geo_radius_meters
    delta = abs(case.reported_at - candidate.candidate_last_reported_at)
    within_window = delta <= timedelta(hours=band.time_window_hours)
    confidence = combined_dedup_confidence(
        band,
        image_similarity=candidate.image_similarity,
        text_similarity=candidate.text_similarity,
    )
    outcome = (
        dedup_outcome(band, confidence=confidence) if within_radius and within_window else NO_MATCH
    )
    return DedupVerdict(
        outcome=outcome,
        confidence=confidence,
        band_category=band.category,
        within_window=within_window,
        within_radius=within_radius,
    )


def decide_all(
    bundle: PolicyBundle,
    cases: Sequence[DecisionCase],
    *,
    calendars: Mapping[str | None, WorkingWeek] | None = None,
) -> list[CaseOutcome]:
    """``decide`` over a corpus, in order.

    A list comprehension with a name, and the name is what it buys: every caller
    that runs a bundle over a corpus goes through one function, so a future
    parallelisation or a progress hook has one place to land rather than four.
    """
    return [decide(bundle, case, calendars=calendars) for case in cases]


__all__ = [
    "CONTINUOUS_WEEK",
    "DECIDABLE_KINDS",
    "NO_MATCH",
    "CaseOutcome",
    "DecisionCase",
    "DedupCandidate",
    "DedupVerdict",
    "PolicyBundle",
    "SlaOutcome",
    "decide",
    "decide_all",
]
