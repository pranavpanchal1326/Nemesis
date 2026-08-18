"""Document validation — the invariants that must hold before approval.

Every rejection in this file is an invariant that is *cheap* here and expensive
anywhere else. A rubric whose weights do not sum to one produces scores nobody
can reproduce; an SLA matrix with no tier at zero produces complaints that can
never be reported late. Both are silent in production and obvious at the moment
the document is written, which is the whole argument for validating the document
rather than checking the consequence.

No database. These are pure models, and a test that needed one would be testing
the persistence layer instead.
"""

from __future__ import annotations

import re
from decimal import Decimal

import pytest

from nemesis.db.models.policy import POLICY_KINDS, POLICY_STATUSES
from nemesis.policy import baselines
from nemesis.policy.documents import (
    BODY_MODELS,
    DedupBand,
    DedupThresholds,
    NodeSeverityOverride,
    PolicyKind,
    PolicyStatus,
    RateCard,
    RateCardItem,
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
    validate_body,
)
from nemesis.policy.errors import PolicyValidationError


def component(key: str, weight: float) -> RubricComponent:
    return RubricComponent(
        key=key, display_name=key.replace("_", " ").title(), weight=weight, description="x"
    )


# ---------------------------------------------------------------------------
# The model / schema duplication, which must not drift
# ---------------------------------------------------------------------------


def test_the_database_check_constraints_match_the_enums() -> None:
    """``db.models.policy`` restates both enums, and the restatement is checked.

    The duplication is deliberate — a model that imported the service layer
    would make the dependency graph cyclic, which surfaces as a partial
    initialisation error three modules from the cause. What makes it safe is
    this test: a kind added to ``PolicyKind`` without the matching CHECK
    constraint would otherwise be rejected by the database at the first write,
    in production, with a constraint-violation message.
    """
    assert set(POLICY_KINDS) == {kind.value for kind in PolicyKind}
    assert set(POLICY_STATUSES) == {status.value for status in PolicyStatus}


def test_every_kind_has_a_body_model() -> None:
    """The registry is what makes the lifecycle generic over kinds.

    A kind with no model would be a document the service happily stores
    unvalidated, which is the one thing the phase exists to prevent.
    """
    assert set(BODY_MODELS) == set(PolicyKind)


# ---------------------------------------------------------------------------
# Severity rubric
# ---------------------------------------------------------------------------


def test_rubric_weights_must_sum_to_one() -> None:
    """§13.1 promises a citizen an explainable score; the score must add up."""
    with pytest.raises(ValueError, match=re.escape("must sum to 1.0")):
        SeverityRubric(components=(component("a", 0.5), component("b", 0.2)))


def test_rubric_weights_tolerate_floating_point_reality() -> None:
    """Six two-decimal weights do not sum to exactly 1.0 in binary.

    Refusing 0.9999999999999999 would be correctness theatre that pushes
    operators toward uglier numbers to satisfy the machine.
    """
    weights = (0.17, 0.17, 0.17, 0.17, 0.16, 0.16)
    rubric = SeverityRubric(
        components=tuple(component(f"c{index}", w) for index, w in enumerate(weights))
    )
    assert len(rubric.components) == 6


def test_duplicate_component_keys_are_refused() -> None:
    """``severity_scored.components`` is a map: a duplicate silently overwrites."""
    with pytest.raises(ValueError, match="duplicate rubric component keys"):
        SeverityRubric(components=(component("a", 0.5), component("a", 0.5)))


def test_two_overrides_for_one_category_are_refused() -> None:
    """Which applied would depend on document order, so the score would not
    be reproducible — and reproducibility is the point of the version stamp."""
    with pytest.raises(ValueError, match="more than one severity override"):
        SeverityRubric(
            components=(component("a", 1.0),),
            overrides=(
                NodeSeverityOverride(category="roads", floor=2.0),
                NodeSeverityOverride(category="roads", floor=5.0),
            ),
        )


def test_a_component_description_is_required() -> None:
    """A component nobody can explain cannot appear in an explanation."""
    with pytest.raises(ValueError):
        RubricComponent(key="a", display_name="A", weight=1.0, description="")


def test_a_zero_multiplier_is_refused() -> None:
    """Disabling scoring for a category must be visible, not a hidden zero."""
    with pytest.raises(ValueError):
        NodeSeverityOverride(category="roads", multiplier=0.0)


# ---------------------------------------------------------------------------
# Dedup thresholds
# ---------------------------------------------------------------------------


def test_dedup_bands_must_be_ordered() -> None:
    """An inverted pair collapses §14.1's ambiguous band to empty.

    Silently: dedup keeps working and simply stops ever consulting the
    Investigation Agent, which looks like the agent has nothing to do.
    """
    with pytest.raises(ValueError, match="strictly below merge_threshold"):
        DedupBand(merge_threshold=0.6, investigate_threshold=0.7)


def test_dedup_stage_weights_must_sum_to_one() -> None:
    """Otherwise the combined confidence is not on the thresholds' scale."""
    with pytest.raises(ValueError, match=re.escape("must sum to 1.0")):
        DedupBand(image_weight=0.8, text_weight=0.4)


def test_exactly_one_default_dedup_band_is_required() -> None:
    """A category added after approval must still have thresholds."""
    with pytest.raises(ValueError, match="exactly one band must have no category"):
        DedupThresholds(bands=(DedupBand(category="roads"),))

    with pytest.raises(ValueError, match="exactly one band must have no category"):
        DedupThresholds(bands=(DedupBand(), DedupBand()))


def test_one_band_per_category() -> None:
    with pytest.raises(ValueError, match="more than one dedup band"):
        DedupThresholds(
            bands=(DedupBand(), DedupBand(category="roads"), DedupBand(category="roads"))
        )


# ---------------------------------------------------------------------------
# Safety ruleset
# ---------------------------------------------------------------------------


def safety_rule(**overrides: object) -> SafetyRule:
    base: dict[str, object] = {
        "rule_id": "hazard.test",
        "display_name": "Test hazard",
        "rationale": "why this is dangerous",
        "terms": ("gas leak",),
    }
    return SafetyRule(**(base | overrides))  # type: ignore[arg-type]


def test_a_one_character_term_is_refused() -> None:
    """A rule that fires on everything gets switched off, taking the real ones."""
    with pytest.raises(ValueError, match="at least two characters"):
        safety_rule(terms=("a",))


def test_duplicate_terms_in_one_rule_are_refused() -> None:
    with pytest.raises(ValueError, match="duplicate safety terms"):
        safety_rule(terms=("gas leak", "gas leak"))


def test_duplicate_rule_ids_are_refused() -> None:
    """``safety_trigger_fired.rule_id`` must resolve to exactly one rule."""
    with pytest.raises(ValueError, match="duplicate safety rule ids"):
        SafetyRuleset(rules=(safety_rule(), safety_rule()))


def test_a_ruleset_with_every_rule_inactive_is_refused() -> None:
    """A disabled fail-safe wearing the clothes of an active one.

    Disabling the danger path is a deployment decision with a runbook, and it
    must not be reachable by a policy edit that reads like a normal revision.
    """
    with pytest.raises(ValueError, match="disabled fail-safe"):
        SafetyRuleset(rules=(safety_rule(is_active=False),))


def test_a_ruleset_keeps_its_declared_order() -> None:
    """First match wins, so order is meaning and must survive validation."""
    ruleset = SafetyRuleset(
        rules=(
            safety_rule(rule_id="first", terms=("gas leak",)),
            safety_rule(rule_id="second", terms=("live wire",)),
        )
    )
    assert [rule.rule_id for rule in ruleset.rules] == ["first", "second"]


def test_a_safety_rule_carries_no_regular_expression_mode() -> None:
    """No regex, and it is a security decision rather than a simplification.

    A tenant-authored regex is a catastrophic-backtracking denial of service
    against the stage with the highest retry budget in the pipeline — the safety
    check would be the thing that takes the system down.
    """
    assert {mode.value for mode in SafetyMatchMode} == {"word", "substring"}


# ---------------------------------------------------------------------------
# SLA matrix
# ---------------------------------------------------------------------------


def sla_matrix(**overrides: object) -> SlaMatrix:
    base: dict[str, object] = {
        "tiers": (SeverityTier(tier="low", min_score=0.0),),
        "entries": (SlaEntry(response_hours=1.0, resolution_hours=2.0),),
    }
    return SlaMatrix(**(base | overrides))  # type: ignore[arg-type]


def test_the_lowest_tier_must_start_at_zero() -> None:
    """A complaint with no tier has no deadline and can never be late.

    That is the failure §27.2 exists to prevent, and a matrix whose lowest tier
    starts at 2.0 produces it for every complaint scoring below two.
    """
    with pytest.raises(ValueError, match=re.escape("must start at 0.0")):
        sla_matrix(tiers=(SeverityTier(tier="medium", min_score=2.0),))


def test_two_tiers_at_the_same_floor_are_refused() -> None:
    with pytest.raises(ValueError, match="both start at"):
        sla_matrix(
            tiers=(
                SeverityTier(tier="low", min_score=0.0),
                SeverityTier(tier="medium", min_score=0.0),
            )
        )


def test_an_entry_referencing_an_undeclared_tier_is_refused() -> None:
    with pytest.raises(ValueError, match="tiers this document does not declare"):
        sla_matrix(
            entries=(
                SlaEntry(response_hours=1.0, resolution_hours=2.0),
                SlaEntry(severity_tier="urgent", response_hours=1.0, resolution_hours=2.0),
            )
        )


def test_a_catch_all_entry_is_required() -> None:
    """Without it a category added after approval silently has no deadline."""
    with pytest.raises(ValueError, match="entry with neither a category nor a tier"):
        sla_matrix(
            entries=(SlaEntry(severity_tier="low", response_hours=1.0, resolution_hours=2.0),)
        )


def test_resolution_may_not_fall_before_response() -> None:
    with pytest.raises(ValueError, match="below response_hours"):
        SlaEntry(response_hours=48.0, resolution_hours=24.0)


def test_escalation_must_be_strictly_before_the_deadline() -> None:
    """An escalation at the deadline notifies about a breach that happened."""
    with pytest.raises(ValueError):
        SlaEntry(response_hours=1.0, resolution_hours=2.0, escalate_at_fraction=1.0)


# ---------------------------------------------------------------------------
# Routing rules
# ---------------------------------------------------------------------------


def routing_rule(**overrides: object) -> RoutingRule:
    base: dict[str, object] = {
        "rule_id": "r1",
        "display_name": "Rule one",
        "condition": "True",
        "department_code": "PWD",
    }
    return RoutingRule(**(base | overrides))  # type: ignore[arg-type]


def test_an_uncompilable_condition_fails_the_document() -> None:
    """The failure belongs to the author, in the editor — not to a complaint."""
    with pytest.raises(ValueError, match="not usable"):
        routing_rule(condition="sevrity > 7")


def test_duplicate_routing_rule_ids_are_refused() -> None:
    """``work_order_created.routing_rule_id`` must trace a misroute to a cause."""
    with pytest.raises(ValueError, match="duplicate routing rule ids"):
        RoutingRules(rules=(routing_rule(), routing_rule()))


def test_a_rule_shadowed_by_an_earlier_catch_all_is_refused() -> None:
    """ "We added a routing rule and nothing changed" is a common ticket.

    Only the provable case is caught — a literal ``True`` that stops evaluation.
    General reachability is undecidable, and attempting it would produce false
    refusals an author could not argue with.
    """
    with pytest.raises(ValueError, match="can never fire"):
        RoutingRules(
            rules=(
                routing_rule(rule_id="catch.all", condition="True"),
                routing_rule(rule_id="never.fires", condition="severity > 9"),
            )
        )


def test_a_catch_all_placed_last_is_accepted() -> None:
    rules = RoutingRules(
        rules=(
            routing_rule(rule_id="urgent", condition="severity > 9"),
            routing_rule(rule_id="catch.all", condition="True"),
        )
    )
    assert rules.rules[-1].rule_id == "catch.all"


def test_a_non_stopping_catch_all_does_not_shadow() -> None:
    """Fall-through is a legitimate design; only ``stop_on_match`` shadows."""
    rules = RoutingRules(
        rules=(
            routing_rule(rule_id="tag.all", condition="True", stop_on_match=False),
            routing_rule(rule_id="urgent", condition="severity > 9"),
        )
    )
    assert len(rules.rules) == 2


def test_there_is_no_fallback_department_field() -> None:
    """A fallback department is where misrouted work goes to be ignored.

    Unmatched means *unrouted*, which the triage queue shows. A tenant that
    genuinely wants a catch-all writes one, and then it is visible in the
    document an approver read.
    """
    assert "fallback_department_code" not in RoutingRules.model_fields


# ---------------------------------------------------------------------------
# Rate card
# ---------------------------------------------------------------------------


def test_rates_are_decimal_not_float() -> None:
    """§17.2 must not manufacture the discrepancies it reports.

    Binary floating point creates them, and §6.5 requires being fair to both
    sides — which starts with arithmetic that does not invent the evidence
    against a contractor.
    """
    item = RateCardItem(
        code="ASPHALT", display_name="Asphalt", unit="m2", rate=Decimal("1234.5600")
    )
    assert isinstance(item.rate, Decimal)
    assert item.rate == Decimal("1234.5600")


def test_a_zero_rate_is_refused() -> None:
    with pytest.raises(ValueError):
        RateCardItem(code="X", display_name="X", unit="ea", rate=Decimal("0"))


def test_duplicate_item_codes_are_refused() -> None:
    item = RateCardItem(code="X", display_name="X", unit="ea", rate=Decimal("1"))
    with pytest.raises(ValueError, match="duplicate rate card item codes"):
        RateCard(items=(item, item))


def test_a_rate_card_has_no_per_item_effective_dates() -> None:
    """Effective dating is on the document, because a card is a negotiated set.

    Per-item dates would allow a card that is half old and half new — a state no
    contract describes and every dispute would turn on.
    """
    assert "effective_from" not in RateCardItem.model_fields


# ---------------------------------------------------------------------------
# The generic entry point, and the baselines
# ---------------------------------------------------------------------------


def test_validate_body_wraps_failures_in_a_policy_error() -> None:
    """The service must not leak Pydantic's exception type to the API layer."""
    with pytest.raises(PolicyValidationError, match="severity_rubric document is not valid"):
        validate_body(PolicyKind.SEVERITY_RUBRIC, {"components": []})


def test_an_unknown_field_is_refused_rather_than_dropped() -> None:
    """A silently dropped field produces a policy that activates successfully
    and behaves differently from the one that was reviewed."""
    with pytest.raises(PolicyValidationError):
        validate_body(
            PolicyKind.SEVERITY_RUBRIC,
            {"components": [component("a", 1.0).model_dump()], "wieghts": {}},
        )


@pytest.mark.parametrize("kind", baselines.SEEDED_KINDS)
def test_every_seeded_baseline_validates_against_its_own_model(kind: PolicyKind) -> None:
    """The baselines are what a tenant gets on day one and what the resolver
    falls back to. A baseline that failed its own validator would make
    provisioning fail for every new customer."""
    body = baselines.baseline_body(kind)
    assert validate_body(kind, body.model_dump(mode="json")) == body


def test_kinds_without_a_baseline_say_so_rather_than_inventing_one() -> None:
    """Routing rules name departments; rate cards name negotiated prices.

    Neither has a value the platform could invent, and inventing one would put
    a department code into every tenant that has no such department.
    """
    assert not baselines.has_baseline(PolicyKind.ROUTING_RULES)
    assert not baselines.has_baseline(PolicyKind.RATE_CARD)
    with pytest.raises(KeyError):
        baselines.baseline_body(PolicyKind.ROUTING_RULES)


def test_the_baseline_safety_ruleset_carries_no_tenant_vocabulary() -> None:
    """The most tempting place in the system to hardcode a domain model.

    Every seeded term must be a hazard *everywhere* — a gas smell, live wiring,
    a collapse. A category name, a ward, or a role here would be critique-log
    defect #1 walking back in through the door Phase 5 closed.
    """
    ruleset = baselines.baseline_body(PolicyKind.SAFETY_RULESET)
    assert isinstance(ruleset, SafetyRuleset)
    terms = {term.casefold() for rule in ruleset.rules for term in rule.terms}
    forbidden = {"pothole", "garbage", "streetlight", "drain", "water", "ward", "engineer"}
    assert not (terms & forbidden), f"tenant vocabulary in the baseline: {terms & forbidden}"


def test_the_baseline_rubric_matches_the_blueprint_weights() -> None:
    """Phase 6 moves §13.5's rubric into data without changing it.

    A tenant that never opens the policy screen must score exactly as it did
    before this phase landed, or the migration is a silent behaviour change.
    """
    rubric = baselines.baseline_body(PolicyKind.SEVERITY_RUBRIC)
    assert isinstance(rubric, SeverityRubric)
    weights = {c.key: c.weight for c in rubric.components}
    assert weights == {
        "visual_damage": 0.40,
        "road_class": 0.25,
        "poi_proximity": 0.20,
        "cluster_count": 0.15,
    }
