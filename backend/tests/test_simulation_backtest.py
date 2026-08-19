"""The impact report — and the three ways it could reassure somebody wrongly.

A backtest report is read by an approver deciding whether to press Activate, so
its failure mode is not "wrong number", it is "comforting number". Each of these
is tested against explicitly:

- **Averaging away the tail.** A mean delta of 0.02 that moves four hundred
  complaints across a tier boundary is not a small change.
- **Reporting coverage that does not exist.** A rule turning on ``zone_code``
  matches nothing in the corpus, and "0 complaints affected" is identical output
  to a genuinely inert change.
- **A sample that flatters.** The changed-case sample leads with the worst
  movement, not with January.

Almost all of this is pure — the comparison takes two bundles and a corpus, and
the corpus can be built by hand here because ``test_simulation_corpus`` already
proves it can be built from the log.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from nemesis.policy.documents import (
    PolicyKind,
    RoutingRule,
    RoutingRules,
    SeverityRubric,
    SeverityTier,
    SlaEntry,
    SlaMatrix,
)
from nemesis.simulation.backtest import MAX_SAMPLE, compare, coverage_gaps
from nemesis.simulation.corpus import Corpus, CorpusWindow
from nemesis.simulation.engine import DecisionCase
from tests.test_simulation_engine import bundle, component

TENANT = uuid.UUID(int=7)
WINDOW = CorpusWindow(start=datetime(2026, 1, 1, tzinfo=UTC), end=datetime(2027, 1, 1, tzinfo=UTC))
REPORTED_AT = datetime(2026, 3, 14, 9, 0, tzinfo=UTC)


def corpus_of(
    cases: list[DecisionCase], *, population: int | None = None, stride: int = 1
) -> Corpus:
    return Corpus(
        window=WINDOW,
        cases=tuple(cases),
        population=population if population is not None else len(cases),
        sampling_stride=stride,
        unknown_categories=(),
    )


def case(index: int, *, visual: float, category: str = "pothole") -> DecisionCase:
    return DecisionCase(
        complaint_id=uuid.UUID(int=index),
        reported_at=REPORTED_AT,
        category=category,
        lineage=(category,),
        measurements={"visual": visual, "road": 5.0},
    )


def rubric(*, visual: float) -> SeverityRubric:
    return SeverityRubric(
        components=(component("visual", visual), component("road", round(1.0 - visual, 6)))
    )


TIERS = SlaMatrix(
    tiers=(
        SeverityTier(tier="low", min_score=0.0),
        SeverityTier(tier="high", min_score=7.0),
    ),
    entries=(
        SlaEntry(response_hours=8.0, resolution_hours=72.0),
        SlaEntry(severity_tier="high", response_hours=1.0, resolution_hours=8.0),
    ),
)


def report_for(cases: list[DecisionCase], *, before: float, after: float):
    return compare(
        bundle(rubric=rubric(visual=before), matrix=TIERS),
        bundle(rubric=rubric(visual=after), matrix=TIERS),
        corpus_of(cases),
        tenant_id=TENANT,
        kind=PolicyKind.SEVERITY_RUBRIC,
    )


# ---------------------------------------------------------------------------
# The diff
# ---------------------------------------------------------------------------


def test_an_identical_candidate_affects_nothing() -> None:
    """The control. A comparison that reports movement against itself is broken."""
    report = report_for([case(index, visual=8.0) for index in range(40)], before=0.5, after=0.5)
    assert report.affected == 0
    assert report.severity.changed == 0
    assert report.affected_fraction == pytest.approx(0.0)


def test_tier_movement_is_a_matrix_not_a_net() -> None:
    """ "40 up, 40 down" and "0 moved" are the same net and very different changes.

    A report that only carried the net would describe the second while the first
    was happening.
    """
    cases = [case(index, visual=9.5) for index in range(20)] + [
        case(20 + index, visual=1.0) for index in range(20)
    ]
    report = compare(
        bundle(rubric=rubric(visual=0.1), matrix=TIERS),
        bundle(rubric=rubric(visual=0.9), matrix=TIERS),
        corpus_of(cases),
        tenant_id=TENANT,
        kind=PolicyKind.SEVERITY_RUBRIC,
    )

    assert report.severity.tier_transitions, "a change this large must move somebody"
    assert all("->" in key for key in report.severity.tier_transitions)
    assert sum(report.severity.tier_transitions.values()) > 0


def test_extremes_are_reported_beside_the_mean() -> None:
    """The mean is the number that hides the tail; the extremes are why it is not alone."""
    cases = [case(index, visual=9.9) for index in range(5)] + [
        case(5 + index, visual=5.0) for index in range(35)
    ]
    report = report_for(cases, before=0.1, after=0.9)

    assert report.severity.max_increase > 0.0
    assert report.severity.changed > 0
    assert abs(report.severity.mean_delta) <= max(
        abs(report.severity.max_increase), abs(report.severity.max_decrease)
    )


def test_the_sla_impact_separates_tightening_from_loosening() -> None:
    """A shorter budget is a new breach risk on work already in flight.

    A longer one is only a relaxation, and folding both into "changed" would put
    them in the same column.
    """
    cases = [case(index, visual=9.9) for index in range(40)]
    report = report_for(cases, before=0.1, after=0.9)

    assert report.sla.changed == report.sla.tightened + report.sla.loosened
    assert report.sla.tightened > 0, "raising the weight should push these into the urgent tier"


def test_routing_changes_are_attributed_per_department() -> None:
    """ "Everything moved" is not actionable; "Roads lost 400, Emergency gained 400" is."""
    routing_before = RoutingRules(
        rules=(
            RoutingRule(
                rule_id="all", display_name="All", condition="True", department_code="ROADS"
            ),
        )
    )
    routing_after = RoutingRules(
        rules=(
            RoutingRule(
                rule_id="urgent",
                display_name="Urgent",
                condition="severity >= 7",
                department_code="EMERGENCY",
            ),
            RoutingRule(
                rule_id="all", display_name="All", condition="True", department_code="ROADS"
            ),
        )
    )
    cases = [case(index, visual=9.5) for index in range(40)]
    report = compare(
        bundle(rubric=rubric(visual=0.5), matrix=TIERS, routing=routing_before),
        bundle(rubric=rubric(visual=0.5), matrix=TIERS, routing=routing_after),
        corpus_of(cases),
        tenant_id=TENANT,
        kind=PolicyKind.ROUTING_RULES,
    )

    assert report.routing.changed == 40
    assert report.routing.department_deltas["ROADS"] == -40
    assert report.routing.department_deltas["EMERGENCY"] == 40
    assert report.routing.newly_unrouted == 0


def test_a_rule_that_stops_matching_is_reported_as_newly_unrouted() -> None:
    """Unrouted work looks, on a queue, exactly like a backlog.

    So the count is its own field rather than part of "changed" — an operator
    scanning a report needs this number to jump out.
    """
    routing_before = RoutingRules(
        rules=(
            RoutingRule(
                rule_id="all", display_name="All", condition="True", department_code="ROADS"
            ),
        )
    )
    routing_after = RoutingRules(
        rules=(
            RoutingRule(
                rule_id="narrow",
                display_name="Narrow",
                condition="severity > 9.99",
                department_code="ROADS",
            ),
        )
    )
    cases = [case(index, visual=1.0) for index in range(40)]
    report = compare(
        bundle(rubric=rubric(visual=0.5), matrix=TIERS, routing=routing_before),
        bundle(rubric=rubric(visual=0.5), matrix=TIERS, routing=routing_after),
        corpus_of(cases),
        tenant_id=TENANT,
        kind=PolicyKind.ROUTING_RULES,
    )

    assert report.routing.newly_unrouted == 40


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------


def test_a_rule_reading_an_unavailable_fact_is_named_as_a_gap() -> None:
    """The most dangerous possible output is "0 complaints affected".

    An absent fact compares ``False`` under every operator, so a rule turning on
    ``zone_code`` backtests as inert — identical output to a change that
    genuinely moves nothing. Naming the rule is the difference between a report
    and a reassurance.
    """
    routing = RoutingRules(
        rules=(
            RoutingRule(
                rule_id="by_zone",
                display_name="By zone",
                condition='zone_code == "north"',
                department_code="ROADS",
            ),
        )
    )
    gaps = coverage_gaps(bundle(routing=routing))

    assert len(gaps) == 1
    assert gaps[0].rule_id == "by_zone"
    assert gaps[0].fact == "zone_code"
    assert "Phase 19" in gaps[0].reason


def test_a_report_with_a_coverage_gap_is_not_certifiable() -> None:
    """A certificate is a claim that the candidate was checked.

    Issuing one off a report with a known blind spot would make the guardrail
    attest to a check that did not happen — worse than no guardrail, because an
    operator trusts it.
    """
    routing = RoutingRules(
        rules=(
            RoutingRule(
                rule_id="by_zone",
                display_name="By zone",
                condition='zone_code == "north"',
                department_code="ROADS",
            ),
        )
    )
    cases = [case(index, visual=5.0) for index in range(40)]
    report = compare(
        bundle(rubric=rubric(visual=0.5), matrix=TIERS),
        bundle(rubric=rubric(visual=0.5), matrix=TIERS, routing=routing),
        corpus_of(cases),
        tenant_id=TENANT,
        kind=PolicyKind.ROUTING_RULES,
    )

    assert report.is_certifiable is False
    assert report.coverage_gaps[0].fact == "zone_code"


def test_a_rule_using_only_available_facts_leaves_no_gap() -> None:
    routing = RoutingRules(
        rules=(
            RoutingRule(
                rule_id="fine",
                display_name="Fine",
                condition='severity > 5 and category == "pothole"',
                department_code="ROADS",
            ),
        )
    )
    assert coverage_gaps(bundle(routing=routing)) == ()


def test_an_inactive_rule_is_not_a_gap() -> None:
    """A rule nobody evaluates cannot have been evaluated wrongly."""
    routing = RoutingRules(
        rules=(
            RoutingRule(
                rule_id="off",
                display_name="Off",
                condition='zone_code == "north"',
                department_code="ROADS",
                is_active=False,
            ),
            RoutingRule(rule_id="on", display_name="On", condition="True", department_code="ROADS"),
        )
    )
    assert coverage_gaps(bundle(routing=routing)) == ()


# ---------------------------------------------------------------------------
# Honesty about the sample
# ---------------------------------------------------------------------------


def test_the_sample_leads_with_the_largest_movement() -> None:
    """A sample taken in chain order shows whatever happened in January."""
    cases = [case(index, visual=5.0) for index in range(39)] + [case(99, visual=10.0)]
    report = report_for(cases, before=0.1, after=0.9)

    assert report.sample, "a change this size must move something"
    deltas = [abs(entry.severity_delta) for entry in report.sample]
    assert deltas == sorted(deltas, reverse=True)
    assert report.sample[0].complaint_id == uuid.UUID(int=99)


def test_the_sample_is_bounded() -> None:
    """A report is a JSONB column and a screen; neither wants twenty thousand rows."""
    cases = [case(index, visual=float(index % 10)) for index in range(MAX_SAMPLE + 50)]
    report = report_for(cases, before=0.1, after=0.9)
    assert len(report.sample) <= MAX_SAMPLE


def test_the_sample_is_stable_across_two_runs() -> None:
    """ "Did this reproduce" must not depend on the sort seeing ties in a given order."""
    cases = [case(index, visual=8.0) for index in range(40)]
    first = report_for(cases, before=0.1, after=0.9)
    second = report_for(cases, before=0.1, after=0.9)
    assert [entry.complaint_id for entry in first.sample] == [
        entry.complaint_id for entry in second.sample
    ]


def test_a_report_carries_both_the_corpus_size_and_the_population() -> None:
    """ "12,000 complaints" and "12,000 of 480,000" are different claims.

    Extrapolating the fraction is a reasonable thing for a caller to do
    knowingly; it is not something the report should do silently.
    """
    cases = [case(index, visual=8.0) for index in range(40)]
    report = compare(
        bundle(rubric=rubric(visual=0.1), matrix=TIERS),
        bundle(rubric=rubric(visual=0.9), matrix=TIERS),
        corpus_of(cases, population=1600, stride=40),
        tenant_id=TENANT,
        kind=PolicyKind.SEVERITY_RUBRIC,
    )

    assert report.case_count == 40
    assert report.population == 1600
    assert report.sampling_stride == 40
    assert report.affected_fraction == pytest.approx(report.affected / 40)


def test_a_report_names_both_configurations_it_compared() -> None:
    """A table of numbers nobody can reproduce is not evidence."""
    cases = [case(index, visual=8.0) for index in range(40)]
    report = report_for(cases, before=0.1, after=0.9)

    assert PolicyKind.SEVERITY_RUBRIC.value in report.baseline_stamps
    assert PolicyKind.SLA_MATRIX.value in report.baseline_stamps, (
        "the whole bundle, because the matrix that turned scores into tiers is "
        "part of what produced these numbers"
    )
    assert report.candidate_stamps.keys() == report.baseline_stamps.keys()


def test_a_changed_case_carries_both_sides() -> None:
    """ "Severity moved by 1.4" is not actionable.

    "6.2 urgent → 7.6 critical, and it moved from Roads to Emergency" is, and it
    is the shape a dispute six months later needs.
    """
    cases = [case(index, visual=9.5) for index in range(40)]
    report = report_for(cases, before=0.1, after=0.9)

    entry = report.sample[0]
    assert entry.baseline["final_severity"] != entry.candidate["final_severity"]
    assert "final_severity" in entry.changed_fields
    assert entry.baseline.keys() == entry.candidate.keys()
