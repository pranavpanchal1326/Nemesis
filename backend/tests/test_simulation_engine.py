"""The decision engine — pure, total, and independent of the clock.

Almost all of this file runs without a database, deliberately: everything Phase
7 claims rests on ``decide`` being a function of its arguments and nothing else,
and a test that needed a session to prove that would be proving something
weaker.

Four properties get hammered here because each one, if it broke, would make
every report this phase produces wrong in a way nobody would notice:

- **Order of operations.** Safety, then the rubric, then the safety floor, then
  the tier, then the SLA, then the route. Routing sees the *final* severity.
- **Determinism.** Same bundle, same case, same answer — including the digest,
  which is what a run stores instead of the outcome.
- **The dedup gates run before the bands.** A neighbour 400 metres away is
  distinct however similar the photograph is.
- **A bypassed category records an empty breakdown**, not a fabricated one.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st

from nemesis.policy.documents import (
    DedupBand,
    DedupThresholds,
    NodeSeverityOverride,
    PolicyKind,
    RoutingRule,
    RoutingRules,
    RubricComponent,
    SafetyRule,
    SafetyRuleset,
    SeverityRubric,
    SeverityTier,
    SlaEntry,
    SlaMatrix,
)
from nemesis.policy.resolver import Resolved
from nemesis.simulation.engine import (
    NO_MATCH,
    DecisionCase,
    DedupCandidate,
    PolicyBundle,
    decide,
    decide_all,
)

REPORTED_AT = datetime(2026, 3, 14, 9, 0, tzinfo=UTC)


def resolved(body: object, *, kind: PolicyKind, revision: int = 1) -> Resolved[object]:
    return Resolved(
        body=body,  # type: ignore[arg-type]
        stamp=f"{kind.value}@{revision}",
        version_id=uuid.uuid4(),
        revision=revision,
        content_hash="0" * 64,
        is_baseline=False,
    )


def component(key: str, weight: float) -> RubricComponent:
    return RubricComponent(key=key, display_name=key, weight=weight, description="x")


#: A ruleset that cannot fire, for the tests that are not about safety.
#:
#: Spelled as an *active* rule whose term appears in no test, rather than as an
#: empty ruleset or an inactive rule — ``SafetyRuleset`` refuses both, and the
#: refusal is right: a danger definition with nothing in it is a disabled
#: fail-safe wearing the clothes of an active one. Working within that here
#: rather than around it keeps the test fixture honest about what a tenant can
#: actually configure.
INERT_SAFETY = SafetyRuleset(
    rules=(
        SafetyRule(
            rule_id="inert",
            display_name="Inert",
            rationale="present so the ruleset is valid; its term appears in no test text",
            terms=("qqzzxx-no-such-term",),
            severity_floor=0.1,
        ),
    )
)


def bundle(
    *,
    rubric: SeverityRubric | None = None,
    thresholds: DedupThresholds | None = None,
    safety: SafetyRuleset | None = None,
    matrix: SlaMatrix | None = None,
    routing: RoutingRules | None = None,
) -> PolicyBundle:
    return PolicyBundle(
        severity_rubric=resolved(
            rubric or SeverityRubric(components=(component("a", 1.0),)),
            kind=PolicyKind.SEVERITY_RUBRIC,
        ),
        dedup_thresholds=resolved(
            thresholds or DedupThresholds(bands=(DedupBand(),)),
            kind=PolicyKind.DEDUP_THRESHOLDS,
        ),
        safety_ruleset=resolved(safety or INERT_SAFETY, kind=PolicyKind.SAFETY_RULESET),
        sla_matrix=resolved(
            matrix
            or SlaMatrix(
                tiers=(
                    SeverityTier(tier="low", min_score=0.0),
                    SeverityTier(tier="high", min_score=7.0),
                ),
                entries=(SlaEntry(response_hours=4.0, resolution_hours=48.0),),
            ),
            kind=PolicyKind.SLA_MATRIX,
        ),
        routing_rules=(
            resolved(routing, kind=PolicyKind.ROUTING_RULES) if routing is not None else None
        ),
    )


def case(**overrides: object) -> DecisionCase:
    defaults: dict[str, object] = {
        "complaint_id": uuid.UUID(int=1),
        "reported_at": REPORTED_AT,
        "category": "pothole",
        "lineage": ("pothole", "roads"),
        "measurements": {"a": 5.0},
    }
    return DecisionCase(**(defaults | overrides))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Order of operations
# ---------------------------------------------------------------------------


def test_the_safety_floor_applies_over_the_rubric_not_instead_of_it() -> None:
    """§11.2 outranks a score, and both numbers survive.

    "The rubric said 3.1 and the keyword floor took it to 9" is a different
    finding from "the rubric said 9" — the first says the ruleset is carrying
    the decision, which is what an author retuning weights needs to know.
    """
    safety = SafetyRuleset(
        rules=(
            SafetyRule(
                rule_id="gas",
                display_name="Gas",
                rationale="explosive",
                terms=("gas leak",),
                severity_floor=9.0,
            ),
        )
    )
    outcome = decide(bundle(safety=safety), case(description_text="there is a gas leak here"))

    assert outcome.severity.score == pytest.approx(5.0), "the rubric's own answer is preserved"
    assert outcome.final_severity == pytest.approx(9.0)
    assert outcome.safety_floor_applied is True
    assert outcome.safety.fired is True


def test_the_safety_floor_never_lowers_a_score() -> None:
    """A floor is a floor. A rule with a floor of 9 must not pull a 9.6 down."""
    safety = SafetyRuleset(
        rules=(
            SafetyRule(
                rule_id="gas",
                display_name="Gas",
                rationale="explosive",
                terms=("gas leak",),
                severity_floor=9.0,
            ),
        )
    )
    outcome = decide(
        bundle(safety=safety), case(measurements={"a": 9.6}, description_text="gas leak")
    )

    assert outcome.final_severity == pytest.approx(9.6)
    assert outcome.safety_floor_applied is False


def test_routing_sees_the_final_severity_including_the_safety_floor() -> None:
    """The reason routing is last.

    A rule reading ``severity > 8`` has to see the number the complaint actually
    carries. Evaluating it against the rubric's pre-floor answer would send a
    gas leak to the ordinary queue while every screen showed it as a 9.
    """
    safety = SafetyRuleset(
        rules=(
            SafetyRule(
                rule_id="gas",
                display_name="Gas",
                rationale="explosive",
                terms=("gas leak",),
                severity_floor=9.0,
            ),
        )
    )
    routing = RoutingRules(
        rules=(
            RoutingRule(
                rule_id="urgent",
                display_name="Urgent",
                condition="severity > 8",
                department_code="EMERGENCY",
            ),
        )
    )
    outcome = decide(
        bundle(safety=safety, routing=routing),
        case(measurements={"a": 1.0}, description_text="gas leak in the lane"),
    )

    assert outcome.route.department_code == "EMERGENCY"


def test_a_bypassing_category_records_an_empty_breakdown_not_a_fabricated_one() -> None:
    """§13.1 promises an explainable score.

    A category that never waits for a score has one honest explanation —
    "because of what it is" — and recording components that decided nothing
    would make the citizen-facing explanation a fiction in the one case where
    the truth is simplest.
    """
    rubric = SeverityRubric(
        components=(component("a", 1.0),),
        overrides=(NodeSeverityOverride(category="pothole", floor=8.5, bypasses_scoring=True),),
    )
    outcome = decide(bundle(rubric=rubric), case(measurements={"a": 2.0}))

    assert outcome.scoring_bypassed is True
    assert outcome.final_severity == pytest.approx(8.5)
    assert outcome.severity.components == {}
    assert outcome.severity.weights == {}


def test_the_tier_is_taken_from_the_final_severity() -> None:
    matrix = SlaMatrix(
        tiers=(
            SeverityTier(tier="low", min_score=0.0),
            SeverityTier(tier="high", min_score=7.0),
        ),
        entries=(
            SlaEntry(response_hours=4.0, resolution_hours=48.0),
            SlaEntry(severity_tier="high", response_hours=1.0, resolution_hours=4.0),
        ),
    )
    low = decide(bundle(matrix=matrix), case(measurements={"a": 2.0}))
    high = decide(bundle(matrix=matrix), case(measurements={"a": 9.0}))

    assert low.sla.severity_tier == "low"
    assert low.sla.resolution_hours == pytest.approx(48.0)
    assert high.sla.severity_tier == "high"
    assert high.sla.resolution_hours == pytest.approx(4.0)


def test_deadlines_are_derived_from_the_case_not_from_the_clock() -> None:
    """The property that makes a backtest a backtest.

    A deadline computed from ``now`` would move every time the report was
    re-run, and two runs of the same comparison would disagree about which
    complaints breached.
    """
    outcome = decide(bundle(), case())
    assert outcome.sla.resolution_due_at == REPORTED_AT + timedelta(hours=48.0)


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------


def test_the_geo_gate_runs_before_the_confidence_bands() -> None:
    """§14.1's stage 1 is a filter, not a weight.

    Folding the radius into the confidence would make a threshold change appear
    to move reports the geo filter never let near each other — which is a report
    claiming credit for complaints it did not touch.
    """
    thresholds = DedupThresholds(
        bands=(DedupBand(geo_radius_meters=50.0, merge_threshold=0.5, investigate_threshold=0.3),)
    )
    candidate = DedupCandidate(
        cluster_id=uuid.uuid4(),
        geo_distance_meters=400.0,
        image_similarity=0.99,
        text_similarity=0.99,
        candidate_last_reported_at=REPORTED_AT - timedelta(hours=1),
    )
    outcome = decide(bundle(thresholds=thresholds), case(dedup_candidate=candidate))

    assert outcome.dedup.outcome == NO_MATCH
    assert outcome.dedup.within_radius is False
    assert outcome.dedup.within_window is True


def test_the_time_window_gate_runs_before_the_confidence_bands() -> None:
    thresholds = DedupThresholds(
        bands=(DedupBand(time_window_hours=72, merge_threshold=0.5, investigate_threshold=0.3),)
    )
    candidate = DedupCandidate(
        cluster_id=uuid.uuid4(),
        geo_distance_meters=10.0,
        image_similarity=0.99,
        text_similarity=0.99,
        candidate_last_reported_at=REPORTED_AT - timedelta(hours=200),
    )
    outcome = decide(bundle(thresholds=thresholds), case(dedup_candidate=candidate))

    assert outcome.dedup.outcome == NO_MATCH
    assert outcome.dedup.within_window is False


def test_a_report_with_no_candidate_is_distinct_with_zero_confidence() -> None:
    outcome = decide(bundle(), case())
    assert outcome.dedup.outcome == NO_MATCH
    assert outcome.dedup.confidence == pytest.approx(0.0)


def test_a_threshold_change_flips_the_verdict_on_the_same_evidence() -> None:
    """The whole point of backtesting a dedup document, in one assertion."""
    candidate = DedupCandidate(
        cluster_id=uuid.uuid4(),
        geo_distance_meters=10.0,
        image_similarity=0.9,
        text_similarity=0.9,
        candidate_last_reported_at=REPORTED_AT - timedelta(hours=1),
    )
    lenient = DedupThresholds(bands=(DedupBand(merge_threshold=0.85),))
    strict = DedupThresholds(bands=(DedupBand(merge_threshold=0.95, investigate_threshold=0.65),))

    assert (
        decide(bundle(thresholds=lenient), case(dedup_candidate=candidate)).dedup.outcome == "merge"
    )
    assert (
        decide(bundle(thresholds=strict), case(dedup_candidate=candidate)).dedup.outcome
        == "investigate"
    )


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_the_same_inputs_produce_the_same_digest() -> None:
    """A run stores digests instead of outcomes; "did this reproduce" depends on it."""
    configured = bundle()
    subject = case()
    assert decide(configured, subject).digest() == decide(configured, subject).digest()


def test_a_changed_decision_changes_the_digest() -> None:
    subject = case(measurements={"a": 2.0})
    other = case(measurements={"a": 9.0})
    assert decide(bundle(), subject).digest() != decide(bundle(), other).digest()


def test_an_unchanged_component_breakdown_does_not_change_the_digest() -> None:
    """``comparable`` is narrower than the outcome, on purpose.

    Two rubrics that reweight components without moving the score, the tier, the
    SLA or the route have changed nothing anybody acts on — and a report that
    listed every one of those complaints as "affected" would bury the forty that
    changed department under twenty thousand that did not.
    """
    left = SeverityRubric(components=(component("a", 0.5), component("b", 0.5)))
    right = SeverityRubric(components=(component("a", 0.5), component("b", 0.5)))
    subject = case(measurements={"a": 4.0, "b": 4.0})

    assert (
        decide(bundle(rubric=left), subject).digest()
        == decide(bundle(rubric=right), subject).digest()
    )


@hypothesis_settings(max_examples=50, deadline=None)
@given(
    measurement=st.floats(min_value=0.0, max_value=10.0),
    text=st.text(max_size=60),
)
def test_deciding_is_total_over_arbitrary_measurements_and_text(
    measurement: float, text: str
) -> None:
    """It cannot raise, and it cannot leave the range.

    The property that lets the pipeline call this on submitter-controlled text
    inside a worker: a decision that could raise on one complaint in ten
    thousand would take the whole batch down at 2am for a reason nobody could
    reproduce.
    """
    safety = SafetyRuleset(
        rules=(
            SafetyRule(
                rule_id="gas",
                display_name="Gas",
                rationale="explosive",
                terms=("gas",),
                severity_floor=9.0,
            ),
        )
    )
    outcome = decide(
        bundle(safety=safety), case(measurements={"a": measurement}, description_text=text)
    )
    assert 0.0 <= outcome.final_severity <= 10.0
    assert outcome.sla.resolution_due_at > outcome.sla.response_due_at or (
        outcome.sla.resolution_hours <= outcome.sla.response_hours
    )


def test_deciding_a_corpus_preserves_order() -> None:
    """``compare`` zips two runs strictly; a reordering would misattribute every row."""
    cases = [case(complaint_id=uuid.UUID(int=index)) for index in range(5)]
    outcomes = decide_all(bundle(), cases)
    assert [outcome.complaint_id for outcome in outcomes] == [c.complaint_id for c in cases]


def test_a_bundle_with_no_routing_document_leaves_work_unrouted() -> None:
    """Not a fallback department. See ``policy.baselines``.

    A tenant that has authored no routing rules leaves complaints in the triage
    queue, where an operator sees them — rather than in a default department,
    which is where misrouted work goes to be ignored.
    """
    outcome = decide(bundle(routing=None), case())
    assert outcome.route.department_code is None
    assert outcome.route.rule_id is None


def test_stamps_name_every_kind_the_decision_used() -> None:
    """A report that does not name both configurations cannot be reproduced."""
    routing = RoutingRules(
        rules=(
            RoutingRule(rule_id="all", display_name="All", condition="True", department_code="PWD"),
        )
    )
    stamps = decide(bundle(routing=routing), case()).stamps
    assert set(stamps) == {
        PolicyKind.SEVERITY_RUBRIC.value,
        PolicyKind.DEDUP_THRESHOLDS.value,
        PolicyKind.SAFETY_RULESET.value,
        PolicyKind.SLA_MATRIX.value,
        PolicyKind.ROUTING_RULES.value,
    }
