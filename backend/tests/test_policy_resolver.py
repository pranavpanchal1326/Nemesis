"""Reading policy at decision time — the ancestor walks, the arithmetic, the cache.

Two halves, deliberately separated:

The **pure** half — ancestor resolution, severity arithmetic, dedup banding,
safety matching, routing evaluation — needs no database, and most of this file
is that. These are the functions that decide what a citizen's report is worth
and where it goes, so they get hammered directly rather than through a pipeline.

The **stateful** half — the TTL cache, the baseline fallback, the taxonomy path
lookup — needs a real database, because what is being proved is that the
resolver reads *only* approved documents and that a change propagates.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from nemesis.db.models.taxonomy import TaxonomyNode
from nemesis.policy import baselines, resolver, service
from nemesis.policy.documents import (
    DedupBand,
    DedupThresholds,
    NodeSeverityOverride,
    PolicyKind,
    RoutingRule,
    RoutingRules,
    RubricComponent,
    SafetyMatchMode,
    SafetyRule,
    SafetyRuleset,
    SeverityRubric,
    SeverityTier,
    SlaEntry,
    SlaMatrix,
)
from nemesis.tenancy.context import tenant_scope
from tests.conftest import postgres_required


def component(key: str, weight: float) -> RubricComponent:
    return RubricComponent(key=key, display_name=key, weight=weight, description="x")


# ---------------------------------------------------------------------------
# Severity arithmetic (§13.5)
# ---------------------------------------------------------------------------


def test_a_score_is_the_weighted_sum_of_its_components() -> None:
    """Phase 12's gate: a score reproduces from its own logged breakdown.

    Which is only possible if what is recorded next to the total is every
    weighted input that produced it.
    """
    rubric = SeverityRubric(components=(component("a", 0.75), component("b", 0.25)))
    result = resolver.score_severity(rubric, measurements={"a": 8.0, "b": 4.0})

    assert result.score == pytest.approx(7.0)
    assert result.components == {"a": 8.0, "b": 4.0}
    assert result.weights == {"a": 0.75, "b": 0.25}
    recomputed = sum(result.components[k] * result.weights[k] for k in result.components)
    assert recomputed == pytest.approx(result.score)


def test_a_missing_measurement_takes_the_declared_default_not_zero() -> None:
    """Zero is not the neutral value on a 0-10 scale — it is one extreme.

    Defaulting to it biases every degraded complaint downward, which shows up
    only as a slow severity drift during an outage and is very hard to attribute
    afterwards.
    """
    rubric = SeverityRubric(
        components=(component("a", 0.5), component("b", 0.5)), missing_component_score=5.0
    )
    result = resolver.score_severity(rubric, measurements={"a": 9.0})
    assert result.components["b"] == 5.0
    assert result.score == pytest.approx(7.0)


def test_a_measurement_outside_the_scale_is_clamped_not_trusted() -> None:
    """Measurements come from model outputs and geospatial queries.

    A component of 11.4 would produce a score outside the range
    ``severity_scored`` accepts — rejected at append time, inside a worker,
    after the work was done.
    """
    rubric = SeverityRubric(components=(component("a", 1.0),))
    assert resolver.score_severity(rubric, measurements={"a": 99.0}).score == 10.0
    assert resolver.score_severity(rubric, measurements={"a": -5.0}).score == 0.0


def test_the_floor_is_applied_after_the_multiplier() -> None:
    """The order people get wrong, and the reason it is stated in the source.

    A category floor means "never below this, whatever else happened". Applying
    it before a multiplier below one would let the multiplier push a floored
    score back under its own floor.
    """
    rubric = SeverityRubric(
        components=(component("a", 1.0),),
        overrides=(NodeSeverityOverride(category="gas", floor=8.0, multiplier=0.5),),
    )
    result = resolver.score_severity(rubric, measurements={"a": 4.0}, lineage=("gas",))
    assert result.score == 8.0, "the floor must survive a shrinking multiplier"


def test_an_override_resolves_through_the_taxonomy_lineage() -> None:
    """An override on ``electrical`` covers a child added next year.

    Which is the whole reason resolution walks the tree instead of matching the
    exact key: a tenant that adds ``electrical.substation`` should not have to
    remember to edit the rubric too.
    """
    rubric = SeverityRubric(
        components=(component("a", 1.0),),
        overrides=(NodeSeverityOverride(category="electrical", floor=9.0),),
    )
    lineage = ("electrical.exposed_cable", "electrical")
    assert resolver.score_severity(rubric, measurements={"a": 1.0}, lineage=lineage).score == 9.0


def test_the_most_specific_override_wins_and_does_not_compose() -> None:
    """A parent floor and a child multiplier do not combine.

    Composing reads reasonably and becomes impossible to predict once the tree
    is four levels deep — which is exactly when somebody needs to predict it.
    """
    rubric = SeverityRubric(
        components=(component("a", 1.0),),
        overrides=(
            NodeSeverityOverride(category="electrical", floor=9.0),
            NodeSeverityOverride(category="electrical.minor", floor=2.0),
        ),
    )
    lineage = ("electrical.minor", "electrical")
    assert resolver.score_severity(rubric, measurements={"a": 1.0}, lineage=lineage).score == 2.0


@given(
    measurements=st.dictionaries(
        st.sampled_from(["a", "b", "c"]),
        st.floats(min_value=-50, max_value=50, allow_nan=False, allow_infinity=False),
        max_size=3,
    )
)
@hypothesis_settings(max_examples=200, deadline=None)
def test_a_score_is_always_inside_the_scale(measurements: dict[str, float]) -> None:
    """``severity_scored`` bounds the score at 0-10 and rejects anything else.

    A rubric that can produce 10.0000001 fails validation inside the event store
    — in a worker, in a transaction, after the scoring work is done — so the
    arithmetic has to guarantee the range rather than hope for it.
    """
    rubric = SeverityRubric(
        components=(component("a", 0.5), component("b", 0.3), component("c", 0.2)),
        overrides=(NodeSeverityOverride(category="x", floor=3.0, multiplier=5.0),),
    )
    for lineage in ((), ("x",)):
        score = resolver.score_severity(rubric, measurements=measurements, lineage=lineage).score
        assert 0.0 <= score <= 10.0


# ---------------------------------------------------------------------------
# Dedup banding (§14.3)
# ---------------------------------------------------------------------------


def thresholds(*bands: DedupBand) -> DedupThresholds:
    return DedupThresholds(bands=(DedupBand(), *bands))


def test_a_category_falls_back_through_its_ancestors_to_the_default() -> None:
    document = thresholds(DedupBand(category="waste", geo_radius_meters=25.0))
    assert (
        resolver.resolve_dedup_band(document, lineage=("waste.overflow", "waste")).geo_radius_meters
        == 25.0
    )
    assert resolver.resolve_dedup_band(document, lineage=("roads",)).geo_radius_meters == 50.0


def test_a_threshold_boundary_takes_the_conservative_action() -> None:
    """§14.3's required error direction, as an inclusive lower edge.

    A false merge suppresses a genuine citizen report; an unmerged duplicate
    only costs an operator some time. So a confidence exactly at the merge
    threshold merges, and one exactly at the investigate threshold investigates.
    """
    band = DedupBand(merge_threshold=0.85, investigate_threshold=0.65)
    assert resolver.dedup_outcome(band, confidence=0.85) == "merge"
    assert resolver.dedup_outcome(band, confidence=0.8499) == "investigate"
    assert resolver.dedup_outcome(band, confidence=0.65) == "investigate"
    assert resolver.dedup_outcome(band, confidence=0.6499) == "distinct"


def test_a_missing_stage_does_not_average_against_zero() -> None:
    """Averaging a text-only report against a zero image score is not caution.

    It pushes every audio submission below the merge threshold, which reads as
    "dedup is conservative" and is actually "dedup is off for audio".
    """
    band = DedupBand(image_weight=0.6, text_weight=0.4)
    assert resolver.combined_dedup_confidence(
        band, image_similarity=None, text_similarity=0.9
    ) == pytest.approx(0.9)
    assert resolver.combined_dedup_confidence(
        band, image_similarity=0.9, text_similarity=None
    ) == pytest.approx(0.9)
    assert resolver.combined_dedup_confidence(
        band, image_similarity=0.9, text_similarity=0.4
    ) == pytest.approx(0.9 * 0.6 + 0.4 * 0.4)


def test_the_dedup_window_is_measured_against_the_band() -> None:
    band = DedupBand(time_window_hours=72)
    now = datetime.now(tz=UTC)
    assert resolver.within_dedup_window(band, earlier=now - timedelta(hours=71), later=now)
    assert not resolver.within_dedup_window(band, earlier=now - timedelta(hours=73), later=now)


@given(
    image=st.one_of(st.none(), st.floats(min_value=0, max_value=1)),
    text=st.one_of(st.none(), st.floats(min_value=0, max_value=1)),
)
@hypothesis_settings(max_examples=200, deadline=None)
def test_a_combined_confidence_is_always_a_probability(
    image: float | None, text: float | None
) -> None:
    """It is compared against thresholds in [0, 1], so it must live there too.

    ``cluster_match_found.combined_confidence`` is bounded in the payload schema,
    so a value of 1.0000001 would be rejected when the merge is recorded — after
    the merge decision has already been made.
    """
    band = DedupBand(image_weight=0.6, text_weight=0.4)
    value = resolver.combined_dedup_confidence(band, image_similarity=image, text_similarity=text)
    assert 0.0 <= value <= 1.0


# ---------------------------------------------------------------------------
# SLA resolution (§27.2)
# ---------------------------------------------------------------------------


def matrix() -> SlaMatrix:
    return SlaMatrix(
        tiers=(
            SeverityTier(tier="low", min_score=0.0),
            SeverityTier(tier="high", min_score=6.5),
            SeverityTier(tier="urgent", min_score=8.5),
        ),
        entries=(
            SlaEntry(response_hours=24.0, resolution_hours=168.0),
            SlaEntry(severity_tier="urgent", response_hours=2.0, resolution_hours=24.0),
            SlaEntry(category="gas", response_hours=1.0, resolution_hours=4.0),
            SlaEntry(
                category="gas", severity_tier="urgent", response_hours=0.5, resolution_hours=2.0
            ),
        ),
    )


@pytest.mark.parametrize(
    ("score", "tier"),
    [(0.0, "low"), (6.49, "low"), (6.5, "high"), (8.49, "high"), (8.5, "urgent"), (10.0, "urgent")],
)
def test_a_tier_covers_from_its_floor_to_the_next(score: float, tier: str) -> None:
    assert resolver.resolve_severity_tier(matrix(), score=score) == tier


def test_a_category_specific_rule_beats_a_tier_specific_one() -> None:
    """ "Gas leaks are always four hours" is a statement about the work.

    "Urgent is twenty-four hours" is a statement about the queue. When they
    disagree, the work wins — which is a decision rather than an obvious
    ordering, so it is pinned here.
    """
    entry = resolver.resolve_sla_entry(matrix(), lineage=("gas",), severity_tier="high")
    assert entry.resolution_hours == 4.0


def test_the_most_specific_cell_wins() -> None:
    entry = resolver.resolve_sla_entry(matrix(), lineage=("gas",), severity_tier="urgent")
    assert entry.resolution_hours == 2.0


def test_an_unknown_category_still_gets_a_deadline() -> None:
    """A complaint with no deadline can never be reported late.

    That is the failure §27.2 exists to prevent, and it is exactly what happens
    to a category added after the matrix was approved unless the catch-all
    resolves.
    """
    entry = resolver.resolve_sla_entry(matrix(), lineage=("brand_new",), severity_tier="low")
    assert entry.resolution_hours == 168.0


def test_the_sla_lineage_walk_reaches_an_ancestor() -> None:
    entry = resolver.resolve_sla_entry(
        matrix(), lineage=("gas.pipeline", "gas"), severity_tier="low"
    )
    assert entry.resolution_hours == 4.0


# ---------------------------------------------------------------------------
# Safety evaluation (§11.2)
# ---------------------------------------------------------------------------


def ruleset(*rules: SafetyRule) -> SafetyRuleset:
    return SafetyRuleset(rules=rules)


def rule(**overrides: object) -> SafetyRule:
    base: dict[str, object] = {
        "rule_id": "hazard.gas",
        "display_name": "Gas",
        "rationale": "ignition risk",
        "terms": ("gas leak",),
        "match_mode": SafetyMatchMode.SUBSTRING,
    }
    return SafetyRule(**(base | overrides))  # type: ignore[arg-type]


def test_a_matching_term_fires_and_reports_its_evidence() -> None:
    """§6.1: prove, don't log. A bypassed queue says what bypassed it."""
    decision = resolver.evaluate_safety(ruleset(rule()), text="There is a GAS LEAK on the corner")
    assert decision.fired is True
    assert decision.rule_id == "hazard.gas"
    assert decision.matched_terms == ("gas leak",)
    assert decision.detection_source == "keyword"


def test_word_mode_does_not_fire_on_a_longer_word() -> None:
    """``gas`` must not fire on ``Gasworks Road``.

    A rule that fires on a street name gets switched off within a day, and it
    takes the rules that work with it.
    """
    word_rule = rule(rule_id="hazard.g", terms=("gas",), match_mode=SafetyMatchMode.WORD)
    assert resolver.evaluate_safety(ruleset(word_rule), text="Gasworks Road").fired is False
    assert resolver.evaluate_safety(ruleset(word_rule), text="smell of gas here").fired is True


def test_punctuation_separates_words() -> None:
    """ "gas-leak" and "gas leak" are the same report.

    A tokeniser that only split on whitespace would miss one of them, which is
    the sort of gap that is invisible until a real submission hits it.
    """
    word_rule = rule(rule_id="hazard.g", terms=("gas",), match_mode=SafetyMatchMode.WORD)
    assert resolver.evaluate_safety(ruleset(word_rule), text="gas-leak here").fired is True


def test_the_first_matching_rule_wins_not_the_most_severe() -> None:
    """Document order is the only ordering an operator can see.

    "Highest severity wins" sounds fairer and makes the outcome depend on a
    number an author can change without realising they reordered the ruleset.
    """
    first = rule(rule_id="hazard.first", terms=("gas leak",), severity_floor=9.0)
    second = rule(rule_id="hazard.second", terms=("gas leak",), severity_floor=10.0)
    decision = resolver.evaluate_safety(ruleset(first, second), text="gas leak")
    assert decision.rule_id == "hazard.first"
    assert decision.severity_floor == 9.0


def test_an_inactive_rule_never_fires() -> None:
    decision = resolver.evaluate_safety(
        ruleset(rule(is_active=False), rule(rule_id="hazard.other", terms=("live wire",))),
        text="gas leak",
    )
    assert decision.fired is False


def test_a_locale_scoped_rule_is_skipped_for_another_locale() -> None:
    """A term that is a hazard in one language is often innocent in another."""
    scoped_rule = rule(locales=("hi",))
    assert (
        resolver.evaluate_safety(ruleset(scoped_rule), text="gas leak", locale="en").fired is False
    )
    assert (
        resolver.evaluate_safety(ruleset(scoped_rule), text="gas leak", locale="hi").fired is True
    )


def test_a_visual_match_fires_without_any_text() -> None:
    """§11.2's visual half. A photograph of a fire needs no caption."""
    visual_rule = rule(rule_id="hazard.fire", terms=("fire",), visual_prompts=("an open flame",))
    decision = resolver.evaluate_safety(
        ruleset(visual_rule), text=None, visual_matches=("an open flame",)
    )
    assert decision.fired is True
    assert decision.detection_source == "visual"


def test_both_sources_are_reported_when_both_match() -> None:
    both = rule(rule_id="hazard.fire", terms=("fire",), visual_prompts=("an open flame",))
    decision = resolver.evaluate_safety(
        ruleset(both), text="there is a fire", visual_matches=("an open flame",)
    )
    assert decision.detection_source == "both"


def test_no_text_and_no_visual_match_does_not_fire() -> None:
    assert resolver.evaluate_safety(ruleset(rule()), text=None).fired is False


@given(text=st.text(max_size=200), locale=st.sampled_from([None, "en", "hi", "mr"]))
@hypothesis_settings(max_examples=300, deadline=None)
def test_the_fail_safe_is_deterministic_for_any_input(text: str, locale: str | None) -> None:
    """Architectural principle 2, as the gate clause states it.

    "The safety fail-safe remains provably deterministic under policy control —
    same input, same outcome, every time." Governed data did not make it
    probabilistic; this is what proves that, over generated text rather than
    three examples.
    """
    document = baselines.baseline_body(PolicyKind.SAFETY_RULESET)
    assert isinstance(document, SafetyRuleset)
    first = resolver.evaluate_safety(document, text=text, locale=locale)
    for _ in range(5):
        assert resolver.evaluate_safety(document, text=text, locale=locale) == first


@given(text=st.text(max_size=200))
@hypothesis_settings(max_examples=200, deadline=None)
def test_safety_evaluation_never_raises(text: str) -> None:
    """The stage with the highest retry budget must not be the thing that breaks.

    Its input is submitter-controlled text, and the matching is linear with no
    backtracking by construction — no regular expressions anywhere near it.
    """
    document = baselines.baseline_body(PolicyKind.SAFETY_RULESET)
    assert isinstance(document, SafetyRuleset)
    assert isinstance(resolver.evaluate_safety(document, text=text).fired, bool)


# ---------------------------------------------------------------------------
# Routing (§15.2)
# ---------------------------------------------------------------------------


def routing(*rules: RoutingRule) -> RoutingRules:
    return RoutingRules(rules=rules)


def routing_rule(**overrides: object) -> RoutingRule:
    base: dict[str, object] = {
        "rule_id": "r1",
        "display_name": "Rule",
        "condition": "True",
        "department_code": "PWD",
    }
    return RoutingRule(**(base | overrides))  # type: ignore[arg-type]


def test_the_first_matching_rule_decides() -> None:
    rules = routing(
        routing_rule(rule_id="urgent", condition="severity >= 8.5", department_code="EMR"),
        routing_rule(rule_id="catch.all", condition="True", department_code="PWD"),
    )
    facts = resolver.routing_facts(category="pothole", severity=9.0)
    decision = resolver.evaluate_routing(rules, facts)
    assert decision.department_code == "EMR"
    assert decision.rule_id == "urgent"


def test_an_unmatched_complaint_is_unrouted_not_defaulted() -> None:
    """There is no fallback department, and that is the design.

    A fallback department is where misrouted work goes to be ignored, and it
    makes an incomplete ruleset look complete. Unrouted is a state the triage
    queue shows and an operator fixes by adding the rule that was missing.
    """
    rules = routing(routing_rule(rule_id="only", condition="severity >= 9.9"))
    decision = resolver.evaluate_routing(rules, resolver.routing_facts(category="x", severity=1.0))
    assert decision.department_code is None
    assert decision.rule_id is None


def test_every_matching_rule_is_reported_even_when_one_decides() -> None:
    """ "Why did this go to Sanitation rather than Roads" is a question about
    the rules that *also* matched."""
    rules = routing(
        routing_rule(rule_id="tag", condition="True", department_code="OPS", stop_on_match=False),
        routing_rule(rule_id="decide", condition="severity > 5", department_code="PWD"),
    )
    decision = resolver.evaluate_routing(rules, resolver.routing_facts(category="x", severity=7.0))
    assert decision.rule_id == "tag", "the first match still decides"
    assert decision.matched_rule_ids == ("tag", "decide")


def test_an_inactive_rule_is_skipped() -> None:
    rules = routing(
        routing_rule(rule_id="off", condition="True", department_code="OFF", is_active=False),
        routing_rule(rule_id="on", condition="True", department_code="PWD"),
    )
    decision = resolver.evaluate_routing(rules, resolver.routing_facts(category="x"))
    assert decision.department_code == "PWD"


def test_a_subtree_condition_matches_a_descendant_category() -> None:
    """A rule approved before a category existed still covers it.

    Which is the reason ``category_ancestors`` is in the fact schema at all: it
    lets a rule name a part of the tree rather than an enumeration of leaves
    that goes stale.
    """
    rules = routing(
        routing_rule(
            rule_id="roads", condition='"roads" in category_ancestors', department_code="PWD"
        )
    )
    facts = resolver.routing_facts(
        category="roads.pothole.deep", lineage=("roads.pothole.deep", "roads.pothole", "roads")
    )
    assert resolver.evaluate_routing(rules, facts).department_code == "PWD"


def test_routing_facts_omits_absent_values_rather_than_nulling_them() -> None:
    """Which is what makes the absent-fact semantics apply to them.

    A ``None`` in the mapping would be a present fact whose value is None, and
    the comparison would then depend on Python's ordering rules rather than on
    the documented "absent compares False".
    """
    facts = resolver.routing_facts(category="x")
    assert "severity" not in facts
    assert "zone_code" not in facts
    assert facts["category"] == "x"


def test_a_rule_referencing_an_absent_fact_simply_does_not_match() -> None:
    """A complaint whose scoring degraded must not be routed as though scored."""
    rules = routing(routing_rule(rule_id="urgent", condition="severity >= 8.5"))
    decision = resolver.evaluate_routing(rules, resolver.routing_facts(category="x"))
    assert decision.rule_id is None


# ---------------------------------------------------------------------------
# The stateful half
# ---------------------------------------------------------------------------

pytestmark_db = [postgres_required, pytest.mark.integration]


@asynccontextmanager
async def scoped(engine: AsyncEngine, tenant_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    with tenant_scope(tenant_id):
        async with maker() as session:
            yield session
            await session.commit()


def simple_rubric(visual: float) -> dict[str, object]:
    remainder = round(1.0 - visual, 6)
    return {
        "components": [
            {"key": "a", "display_name": "A", "weight": visual, "description": "x"},
            {"key": "b", "display_name": "B", "weight": remainder, "description": "x"},
        ]
    }


@postgres_required
@pytest.mark.integration
async def test_a_tenant_with_no_document_gets_the_baseline_and_says_so(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A deployment mid-migration must score, not 500.

    And the stamp must not look like a revision number: a complaint scored
    before its tenant had a rubric has to stay identifiable as such forever.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        cold = resolver.PolicyResolver(reload_seconds=0)
        resolved = await cold.severity_rubric(session, tenant_id=tenant_id)

    assert resolved.is_baseline is True
    assert resolved.stamp == resolver.BASELINE_STAMP
    assert resolved.revision is None
    assert resolved.body == baselines.baseline_body(PolicyKind.SEVERITY_RUBRIC)


@postgres_required
@pytest.mark.integration
async def test_the_resolver_never_sees_a_draft(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The gate clause, at the layer that actually decides.

    ``test_policy_lifecycle`` proves ``active_version`` ignores drafts; this
    proves the resolver has no other read path — including no parameter that
    could widen it.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        await service.seed_baselines(session, tenant_id=tenant_id)
        await service.draft(
            session,
            tenant_id=tenant_id,
            kind=PolicyKind.SEVERITY_RUBRIC,
            body=simple_rubric(0.99),
            change_reason="not approved",
        )
        cold = resolver.PolicyResolver(reload_seconds=0)
        resolved = await cold.severity_rubric(session, tenant_id=tenant_id)

    assert resolved.revision == 1, "the draft is revision 2 and must not decide"
    assert {c.key for c in resolved.body.components} == {
        "visual_damage",
        "road_class",
        "poi_proximity",
        "cluster_count",
    }


@postgres_required
@pytest.mark.integration
async def test_an_activated_change_is_served_after_invalidation(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The "no deploy" half, at the resolver.

    The cached snapshot is what makes a policy read cost a dict lookup instead
    of a round trip, and ``invalidate`` is what makes an operator's own re-read
    show their own change rather than waiting out a TTL.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        await service.seed_baselines(session, tenant_id=tenant_id)
        cache = resolver.PolicyResolver(reload_seconds=3600)
        before = await cache.severity_rubric(session, tenant_id=tenant_id)

        version = await service.draft(
            session,
            tenant_id=tenant_id,
            kind=PolicyKind.SEVERITY_RUBRIC,
            body=simple_rubric(0.7),
            change_reason="tuned",
        )
        for verb in (service.submit_for_review, service.approve, service.activate):
            await verb(
                session,
                tenant_id=tenant_id,
                kind=PolicyKind.SEVERITY_RUBRIC,
                revision=version.revision,
                reason="tuned",
            )

        stale = await cache.severity_rubric(session, tenant_id=tenant_id)
        assert stale.stamp == before.stamp, "the TTL is the honest latency, not zero"

        cache.invalidate(tenant_id=tenant_id)
        after = await cache.severity_rubric(session, tenant_id=tenant_id)

    assert after.revision == version.revision
    assert after.body.components[0].weight == 0.7


@postgres_required
@pytest.mark.integration
async def test_a_kind_with_no_baseline_resolves_to_nothing(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Routing rules name departments the platform cannot invent.

    ``None`` is the honest answer, and the type makes the caller decide what to
    do about it — which for routing is "leave the complaint unrouted".
    """
    async with scoped(migrated_engine, tenant_id) as session:
        cold = resolver.PolicyResolver(reload_seconds=0)
        assert await cold.routing_rules(session, tenant_id=tenant_id) is None
        assert await cold.rate_card(session, tenant_id=tenant_id) is None


@postgres_required
@pytest.mark.integration
async def test_the_lineage_comes_from_the_materialised_path(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """One query, then pure string work — the reason ``path`` exists at all."""
    async with scoped(migrated_engine, tenant_id) as session:
        session.add(
            TaxonomyNode(
                tenant_id=tenant_id,
                key="exposed_cable",
                path="utilities/electrical/exposed_cable",
                depth=2,
                display_name="Exposed cable",
            )
        )
        await session.flush()
        lineage = await resolver.category_lineage(
            session, tenant_id=tenant_id, category="exposed_cable"
        )

    assert lineage == ("exposed_cable", "electrical", "utilities")


@postgres_required
@pytest.mark.integration
async def test_an_unknown_category_resolves_to_itself(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The classifier can emit a key for a node deactivated since classification.

    Raising here would strand the complaint; returning just the key lets it fall
    through to the default band, which is the correct conservative outcome.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        lineage = await resolver.category_lineage(session, tenant_id=tenant_id, category="vanished")
    assert lineage == ("vanished",)


@postgres_required
@pytest.mark.integration
async def test_two_tenants_resolve_their_own_documents(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID, other_tenant_id: uuid.UUID
) -> None:
    """The cache is keyed by tenant, which is the only thing standing between a
    shared process and one customer's rubric scoring another's complaints."""
    cache = resolver.PolicyResolver(reload_seconds=3600)

    async with scoped(migrated_engine, tenant_id) as session:
        await service.seed_baselines(session, tenant_id=tenant_id)
        version = await service.draft(
            session,
            tenant_id=tenant_id,
            kind=PolicyKind.SEVERITY_RUBRIC,
            body=simple_rubric(0.9),
            change_reason="tuned",
        )
        for verb in (service.submit_for_review, service.approve, service.activate):
            await verb(
                session,
                tenant_id=tenant_id,
                kind=PolicyKind.SEVERITY_RUBRIC,
                revision=version.revision,
                reason="r",
            )
        first = await cache.severity_rubric(session, tenant_id=tenant_id)

    async with scoped(migrated_engine, other_tenant_id) as session:
        second = await cache.severity_rubric(session, tenant_id=other_tenant_id)

    assert first.revision == 2 and first.is_baseline is False
    assert second.is_baseline is True, "the other tenant must not inherit a cached document"
