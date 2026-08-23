"""The six governed structures, as validated documents.

Phase 6's thesis in one sentence: *every behavioural knob becomes governed
data*. These are the knobs. Each kind is a Pydantic model, each model is
registered against a ``PolicyKind``, and the lifecycle service in
``policy.service`` knows nothing about any of them beyond "validate this body
against its kind" — which is what lets a seventh kind be added without touching
the lifecycle, the API, or the hash chain.

**Why a whole document per kind, not a row per knob.** A severity rubric is only
correct as a set: the weights have to sum to one, and a table of weight rows can
be in a state where they do not while an operator is halfway through editing.
The document is the unit of approval because it is the unit of consistency —
you approve a rubric, never a weight.

**Why bodies are validated on write and again on activate.** Not belt and
braces. The models here change between releases, and a document approved under
last month's model can be activated under this month's. Re-validating at
activation is what turns "this field became required" from a silently
half-applied policy into a refusal naming the document — which is exactly the
class of failure ``events.registry``'s compatibility rules exist to prevent on
the log, applied to configuration.

**Why ``Decimal`` for money and ``float`` for scores.** A rate card multiplied
out in binary floating point produces a deviation detector (§17.2) that flags
₹0.01 discrepancies it created itself. Severity is a score in [0, 10] with no
such obligation, and forcing Decimal through the rubric arithmetic would buy
nothing and cost the ability to compare against a model output.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from itertools import pairwise
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nemesis.control_plane.schemas import LocaleTag, OrgCode, TaxonomyKey
from nemesis.policy.errors import ExpressionError, PolicyValidationError
from nemesis.policy.expressions import ROUTING_FACTS, compile_condition


class PolicyKind(StrEnum):
    """The governed structures, one per document type.

    A closed set, unlike most enums in this codebase, and for a reason that does
    not apply to tenant vocabulary: a kind is a *platform* capability — it needs
    a body model, a resolver, and something that consumes it. A tenant cannot
    invent one, so leaving the set open would only mean accepting a typo as a
    document nothing will ever read.
    """

    SEVERITY_RUBRIC = "severity_rubric"
    DEDUP_THRESHOLDS = "dedup_thresholds"
    SAFETY_RULESET = "safety_ruleset"
    SLA_MATRIX = "sla_matrix"
    ROUTING_RULES = "routing_rules"
    RATE_CARD = "rate_card"
    #: Phase 8. The §11.1/§11.3 knobs — EXIF mismatch distance, perceptual-hash
    #: tolerance, submission velocity, retention clocks — and the live-capture
    #: switch §11.1 names as *the real control*. Governed rather than constant
    #: for the reason architectural principle 1 gives: a campus with one gate
    #: and a city with nine million people cannot share a velocity limit, and
    #: "200 metres" is a municipal convention, not a law.
    TRUST_THRESHOLDS = "trust_thresholds"
    #: Phase 9. How a raw model similarity becomes a confidence, per category,
    #: and what confidence is too low to claim a category at all. Governed rather
    #: than constant for the reason the phase gate states: the numbers are
    #: *measured* from a tenant's own labelled data and change as that data
    #: accumulates (§13.3), so they are exactly the kind of value that must be
    #: approvable, effective-dated, and backtestable rather than deployed.
    PERCEPTION_CALIBRATION = "perception_calibration"


class PolicyStatus(StrEnum):
    """The lifecycle. Draft → review → approve → activate, plus the two ends.

    ``SUPERSEDED`` and ``ARCHIVED`` are different facts and both are needed.
    Superseded means "this was live and a newer version replaced it" — the
    version a complaint scored six months ago is in that state, and it must stay
    readable forever. Archived means "this never went live and never will",
    which is where a rejected draft ends up. Collapsing them would make
    "what was live in March" unanswerable without replaying the event log.
    """

    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


#: The statuses a decision may be made from. Exactly one member, and it is
#: written as a set rather than an ``== ACTIVE`` comparison because the gate
#: clause "an unapproved draft can never influence a production decision" is
#: enforced by a query filter, and a filter is easier to audit against a named
#: constant than against a literal repeated in four modules.
DECIDING_STATUSES: Final[frozenset[PolicyStatus]] = frozenset({PolicyStatus.ACTIVE})


class PolicyBody(BaseModel):
    """Base for every document body.

    ``extra="forbid"`` for the reason ``ControlPlaneModel`` states — a
    misspelled key that is silently dropped produces a policy that activates
    successfully and behaves differently from the one that was reviewed.
    ``frozen=True`` because a body is hashed into the event log, and a body that
    can be mutated after validation can be mutated after hashing.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# Severity rubric (§13.5)
# ---------------------------------------------------------------------------

#: A weight in a rubric. Bounded at both ends: a negative weight would make
#: evidence of damage *reduce* severity, which is never the intent and is very
#: hard to spot in a table of six numbers.
Weight = Annotated[float, Field(ge=0.0, le=1.0)]
Score = Annotated[float, Field(ge=0.0, le=10.0)]

#: A probability or a normalised confidence. The same bounds as ``Weight`` and
#: deliberately a different name: a weight is a share of a total and a
#: probability is a belief, and a reader who finds ``Weight`` on an abstain
#: threshold has to stop and work out which of the two was meant.
Probability = Annotated[float, Field(ge=0.0, le=1.0)]

#: Tolerance on the weights-sum-to-one check. Floating point addition of six
#: two-decimal weights does not land on 1.0 exactly, and refusing a rubric that
#: sums to 0.9999999999999999 would be a correctness theatre that makes
#: operators pick uglier numbers.
_WEIGHT_SUM_TOLERANCE: Final = 1e-6


class RubricComponent(PolicyBody):
    """One weighted input to a severity score.

    ``key`` is what appears in ``severity_scored.components``, so it is a
    contract in the same sense a taxonomy key is: renaming one orphans every
    breakdown already logged against it. The display name is separate for
    exactly that reason.
    """

    key: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,62}$", max_length=63)]
    display_name: str = Field(min_length=1, max_length=200)
    weight: Weight
    #: What the component measures, in the operator's words. Required, not
    #: optional: §13.1 promises a citizen an explainable score, and a component
    #: called ``poi_proximity`` with no description cannot be explained to
    #: anyone who did not write it.
    description: str = Field(min_length=1, max_length=1000)


class NodeSeverityOverride(PolicyBody):
    """What a taxonomy category implies regardless of the evidence.

    The §13.5 point that a rubric alone cannot express: an exposed live cable is
    severe even when the photograph is poor. Resolution walks the taxonomy from
    the classified node upward, so an override on ``electrical`` covers every
    child a tenant adds later without anyone editing this document.
    """

    category: TaxonomyKey
    floor: Score = 0.0
    multiplier: Annotated[float, Field(gt=0.0, le=5.0)] = 1.0
    #: §11.2's shortcut. A category that never waits for a score.
    bypasses_scoring: bool = False


class SeverityRubric(PolicyBody):
    """§13.5's rubric, as an approved document rather than six env vars.

    Replaces ``SeveritySettings`` in ``config.py``, which is the change the
    critique log's defect #2 asks for: an admin retuning a weight is a policy
    revision, not a deploy. The old settings object stays as the *fallback*
    baseline a tenant is provisioned with, so a deployment that has activated no
    rubric still scores rather than failing.
    """

    components: tuple[RubricComponent, ...] = Field(min_length=1, max_length=32)
    overrides: tuple[NodeSeverityOverride, ...] = Field(default=(), max_length=512)
    #: The score a complaint gets when a component has no measurement. Not zero
    #: by default: a missing measurement is not evidence of absence, and
    #: defaulting to zero silently biases every degraded complaint downward,
    #: which §24.2 would then show as a quiet severity drift during an outage.
    missing_component_score: Score = 5.0

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> SeverityRubric:
        total = sum(component.weight for component in self.components)
        if abs(total - 1.0) > _WEIGHT_SUM_TOLERANCE:
            raise ValueError(
                f"rubric weights must sum to 1.0, got {total:.6f}. An unnormalised "
                f"rubric makes the logged severity breakdown non-reproducible, which "
                f"breaks the §13.1 explainability guarantee — a citizen told 'your "
                f"report scored 6.2' must be able to see the 6.2 add up."
            )
        return self

    @model_validator(mode="after")
    def _component_keys_are_unique(self) -> SeverityRubric:
        keys = [component.key for component in self.components]
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        if duplicates:
            raise ValueError(
                f"duplicate rubric component keys: {', '.join(duplicates)}. "
                f"``severity_scored.components`` is a map, so the second would "
                f"silently overwrite the first in every logged breakdown."
            )
        return self

    @model_validator(mode="after")
    def _overrides_are_one_per_category(self) -> SeverityRubric:
        categories = [override.category for override in self.overrides]
        duplicates = sorted({key for key in categories if categories.count(key) > 1})
        if duplicates:
            raise ValueError(
                f"more than one severity override for {', '.join(duplicates)}; "
                f"which one applied would depend on document order, so the score "
                f"would not be reproducible"
            )
        return self


# ---------------------------------------------------------------------------
# Dedup thresholds (§14.3)
# ---------------------------------------------------------------------------


class DedupBand(PolicyBody):
    """Thresholds for one category, or the tenant default.

    §14.3's reason for making these per-category rather than global: a pothole
    and a garbage pile have different visual variance. Two photographs of the
    same pothole from opposite kerbs are barely similar; two photographs of
    unrelated garbage piles are extremely similar. One threshold serving both
    guarantees being wrong for at least one.
    """

    #: ``None`` is the tenant-wide default, used for any category with no band
    #: of its own after the ancestor walk. Exactly one default per document.
    category: TaxonomyKey | None = None
    geo_radius_meters: Annotated[float, Field(gt=0.0, le=5000.0)] = 50.0
    time_window_hours: Annotated[int, Field(gt=0, le=8760)] = 72
    #: At or above: merge automatically.
    merge_threshold: Annotated[float, Field(ge=0.0, le=1.0)] = 0.85
    #: At or above, below merge: hand to the §12 Investigation Agent.
    investigate_threshold: Annotated[float, Field(ge=0.0, le=1.0)] = 0.65
    #: How the two stage scores combine. Stated per band because an image-only
    #: category (a pothole) and a text-heavy one (a noise complaint) weight them
    #: differently, and a fixed 50/50 would be wrong for both.
    image_weight: Weight = 0.6
    text_weight: Weight = 0.4

    @model_validator(mode="after")
    def _bands_are_ordered(self) -> DedupBand:
        if self.investigate_threshold >= self.merge_threshold:
            raise ValueError(
                f"investigate_threshold ({self.investigate_threshold}) must be strictly "
                f"below merge_threshold ({self.merge_threshold}) for category "
                f"{self.category or '(default)'}; otherwise the ambiguous band that "
                f"routes to the Investigation Agent collapses to empty and §14.1 "
                f"silently degrades to a binary merge/no-merge decision"
            )
        return self

    @model_validator(mode="after")
    def _stage_weights_sum_to_one(self) -> DedupBand:
        total = self.image_weight + self.text_weight
        if abs(total - 1.0) > _WEIGHT_SUM_TOLERANCE:
            raise ValueError(
                f"image_weight and text_weight must sum to 1.0, got {total:.6f}; "
                f"otherwise the combined confidence is not on the same scale as the "
                f"thresholds it is compared against"
            )
        return self


class DedupThresholds(PolicyBody):
    """§14.3's thresholds, per category, as an approved document.

    Phase 7 auto-tunes these from human merge/split decisions and surfaces the
    result as a *draft*. That is the whole reason they are a policy document
    rather than a config block: a tuner that could write directly to a running
    threshold would be a system that retunes itself on production complaints
    with nobody in the loop.
    """

    bands: tuple[DedupBand, ...] = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def _exactly_one_default_band(self) -> DedupThresholds:
        defaults = [band for band in self.bands if band.category is None]
        if len(defaults) != 1:
            raise ValueError(
                f"exactly one band must have no category, as the tenant-wide default; "
                f"found {len(defaults)}. Without one, a category added after this "
                f"document was approved would have no thresholds at all, and dedup "
                f"would either skip it or invent a number."
            )
        return self

    @model_validator(mode="after")
    def _one_band_per_category(self) -> DedupThresholds:
        categories = [band.category for band in self.bands if band.category is not None]
        duplicates = sorted({key for key in categories if categories.count(key) > 1})
        if duplicates:
            raise ValueError(f"more than one dedup band for {', '.join(duplicates)}")
        return self


# ---------------------------------------------------------------------------
# Safety ruleset (§11.2)
# ---------------------------------------------------------------------------


class SafetyMatchMode(StrEnum):
    """How a term is matched against text.

    **No regular expressions, and that is a security decision.** A tenant-
    authored regex is a catastrophic-backtracking denial of service against the
    stage with the highest retry budget in the pipeline — the safety check would
    be the thing that takes the system down. Whole-word and substring matching
    cover what a safety keyword list actually needs and run in linear time on
    input the submitter controls.
    """

    #: Token match on word boundaries. ``gas`` does not fire on ``Gasworks Road``.
    WORD = "word"
    #: Anywhere in the text. For scripts without spaces between words, and for
    #: compound terms where word boundaries do not exist.
    SUBSTRING = "substring"


class SafetyRule(PolicyBody):
    """One deterministic danger rule (§11.2).

    Deterministic does not mean hardcoded (architectural principle 2). This is a
    hard rule with no probability in it — it fires or it does not, on the same
    input, every time — and it lives in an approved document rather than in
    source, which is the distinction the principle draws.
    """

    #: Recorded in ``safety_trigger_fired.rule_id``. A contract: it is how a
    #: fired trigger is traced back to the rule six months later.
    rule_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{0,62}$", max_length=63)]
    display_name: str = Field(min_length=1, max_length=200)
    #: Why this is dangerous, for the operator who sees the trigger fire. §6.1:
    #: prove, don't log — a bypassed queue must say what bypassed it and why.
    rationale: str = Field(min_length=1, max_length=1000)
    terms: tuple[str, ...] = Field(min_length=1, max_length=256)
    match_mode: SafetyMatchMode = SafetyMatchMode.WORD
    #: Which locales the terms are written for. Empty means every locale, which
    #: is right for a term that is the same word everywhere and wrong for one
    #: that is a harmless word in another language.
    locales: tuple[LocaleTag, ...] = ()
    #: §11.2's visual half. Scored by the perception layer against the image;
    #: carried here so the keyword list and the visual list are approved
    #: together, as one danger definition rather than two that can disagree.
    visual_prompts: tuple[str, ...] = Field(default=(), max_length=64)
    #: Severity floor applied when this rule fires, before any rubric runs.
    severity_floor: Score = 9.0
    is_active: bool = True

    @field_validator("terms")
    @classmethod
    def _terms_are_usable(cls, terms: tuple[str, ...]) -> tuple[str, ...]:
        """Reject blank and one-character terms.

        A single character in ``substring`` mode matches a large fraction of all
        text, and a safety rule that fires on everything is operationally
        identical to one that fires on nothing — both get switched off within a
        day, and the second one takes the real rules with it.
        """
        cleaned = tuple(term.strip() for term in terms)
        if any(len(term) < 2 for term in cleaned):
            raise ValueError(
                "safety terms must be at least two characters; a one-character term "
                "matches most text, and a rule that always fires gets switched off "
                "along with the rules that work"
            )
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("duplicate safety terms in one rule")
        return cleaned


class SafetyRuleset(PolicyBody):
    """§11.2's fail-safe, as governed data that still executes as a hard rule.

    **Evaluation order is document order and the first match wins.** Not "the
    highest severity wins", which sounds fairer and makes the outcome depend on
    a number an author can change without realising they reordered the ruleset.
    Deterministic means the operator can predict it, and document order is the
    only ordering an operator can see.
    """

    rules: tuple[SafetyRule, ...] = Field(min_length=1, max_length=256)
    #: What happens when the ruleset itself cannot be evaluated — no text to
    #: match, transcription degraded. ``review`` parks the complaint for a
    #: human; it is the only value, and it is a field rather than a constant so
    #: the document states its own failure posture where an approver reads it.
    on_indeterminate: Literal["review"] = "review"

    @model_validator(mode="after")
    def _rule_ids_are_unique(self) -> SafetyRuleset:
        ids = [rule.rule_id for rule in self.rules]
        duplicates = sorted({rule_id for rule_id in ids if ids.count(rule_id) > 1})
        if duplicates:
            raise ValueError(
                f"duplicate safety rule ids: {', '.join(duplicates)}. The id is what "
                f"``safety_trigger_fired`` records, so a duplicate makes a fired "
                f"trigger untraceable to the rule that fired it."
            )
        return self

    @model_validator(mode="after")
    def _at_least_one_rule_is_active(self) -> SafetyRuleset:
        if not any(rule.is_active for rule in self.rules):
            raise ValueError(
                "a safety ruleset with every rule inactive is a disabled fail-safe "
                "wearing the clothes of an active one. If the intent is to disable "
                "the danger path, that is a deployment decision with a runbook, not "
                "a policy edit that reads as a normal revision."
            )
        return self


# ---------------------------------------------------------------------------
# SLA matrix (§27.2)
# ---------------------------------------------------------------------------


class SlaEntry(PolicyBody):
    """One cell of the §27.2 matrix.

    Keyed by category *and* severity tier, either of which may be absent to mean
    "any". Resolution is most-specific-first, so a document can state a tenant
    baseline in one row and override two categories, rather than enumerating a
    full cross product that goes stale the moment a category is added.
    """

    category: TaxonomyKey | None = None
    severity_tier: Annotated[str, Field(max_length=64)] | None = None
    #: Working hours, resolved against a business calendar — not wall-clock
    #: hours. §27.2's numbers are working-time budgets; ``control_plane.calendars``
    #: owns turning one into an instant.
    response_hours: Annotated[float, Field(gt=0.0, le=8760.0)]
    resolution_hours: Annotated[float, Field(gt=0.0, le=8760.0)]
    #: Which calendar the budget is spent against. ``None`` uses the tenant
    #: default, which is what almost every entry wants; a 24/7 category (a gas
    #: leak) names a continuous calendar here.
    calendar_code: OrgCode | None = None
    #: Fraction of the budget at which the §27.2 escalation fires. Strictly
    #: below 1.0: an escalation at the deadline is a notification about a breach
    #: that already happened.
    escalate_at_fraction: Annotated[float, Field(gt=0.0, lt=1.0)] = 0.75

    @model_validator(mode="after")
    def _resolution_is_not_before_response(self) -> SlaEntry:
        if self.resolution_hours < self.response_hours:
            raise ValueError(
                f"resolution_hours ({self.resolution_hours}) is below response_hours "
                f"({self.response_hours}); the deadline to finish the work would fall "
                f"before the deadline to acknowledge it"
            )
        return self


class SeverityTier(PolicyBody):
    """A named band of the 0-10 severity scale.

    Tiers are policy rather than an enum because §27.2's four are a municipal
    convention, not a law: a campus runs three, an industrial park runs five
    with a regulatory one on top. ``min_score`` is inclusive, the tier extends to
    the next tier's floor, and the top tier extends to 10.
    """

    tier: Annotated[str, Field(min_length=1, max_length=64)]
    min_score: Score


class SlaMatrix(PolicyBody):
    """§27.2, calendar-aware, per tenant.

    Carries the tier definitions as well as the durations, because the two are
    only meaningful together: changing where "urgent" starts without changing
    what "urgent" costs is a silent redefinition of every deadline in the
    tenant, and it is far too easy to do when they live in separate documents.
    """

    tiers: tuple[SeverityTier, ...] = Field(min_length=1, max_length=16)
    entries: tuple[SlaEntry, ...] = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def _tiers_partition_the_scale(self) -> SlaMatrix:
        """Tiers must be ordered, distinct, and start at zero.

        Starting at zero is the clause that matters: a matrix whose lowest tier
        begins at 2.0 leaves complaints scoring 1.5 with no tier, and therefore
        no SLA — a complaint with no deadline is one that can never be late,
        which is the failure mode §27.2 exists to prevent.
        """
        ordered = sorted(self.tiers, key=lambda tier: tier.min_score)
        if ordered[0].min_score != 0.0:
            raise ValueError(
                f"the lowest severity tier must start at 0.0, not "
                f"{ordered[0].min_score}; otherwise a complaint scoring below it has "
                f"no tier, and a complaint with no tier has no deadline and can never "
                f"be reported late"
            )
        for lower, higher in pairwise(ordered):
            if lower.min_score == higher.min_score:
                raise ValueError(
                    f"tiers {lower.tier!r} and {higher.tier!r} both start at "
                    f"{lower.min_score}; which one a score falls into would depend on "
                    f"document order"
                )
        names = [tier.tier for tier in self.tiers]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"duplicate severity tier names: {', '.join(duplicates)}")
        return self

    @model_validator(mode="after")
    def _entries_reference_declared_tiers(self) -> SlaMatrix:
        declared = {tier.tier for tier in self.tiers}
        unknown = sorted(
            {
                entry.severity_tier
                for entry in self.entries
                if entry.severity_tier is not None and entry.severity_tier not in declared
            }
        )
        if unknown:
            raise ValueError(
                f"SLA entries reference tiers this document does not declare: "
                f"{', '.join(unknown)}. Declared tiers are {', '.join(sorted(declared))}."
            )
        return self

    @model_validator(mode="after")
    def _has_a_catch_all_entry(self) -> SlaMatrix:
        if not any(
            entry.category is None and entry.severity_tier is None for entry in self.entries
        ):
            raise ValueError(
                "the matrix needs one entry with neither a category nor a tier, as the "
                "fallback. Without it a category added after approval has no deadline, "
                "and the gap is invisible until somebody asks why a complaint was never "
                "reported as breaching."
            )
        return self

    @model_validator(mode="after")
    def _entries_are_unique(self) -> SlaMatrix:
        keys = [(entry.category, entry.severity_tier) for entry in self.entries]
        duplicates = sorted(
            {
                f"{category or '*'}/{tier or '*'}"
                for category, tier in keys
                if keys.count((category, tier)) > 1
            }
        )
        if duplicates:
            raise ValueError(f"duplicate SLA entries for {', '.join(duplicates)}")
        return self


# ---------------------------------------------------------------------------
# Routing rules (§15.2)
# ---------------------------------------------------------------------------


class RoutingRule(PolicyBody):
    """One condition → destination rule, evaluated in order.

    ``condition`` is compiled by ``policy.expressions`` at validation time, so a
    document containing an unparseable or misspelled condition cannot be saved,
    let alone approved. That is the point of compiling here rather than at
    routing time: the failure belongs to the author, in the editor, not to a
    complaint at 2am.
    """

    rule_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{0,62}$", max_length=63)]
    display_name: str = Field(min_length=1, max_length=200)
    #: A ``policy.expressions`` condition. ``"True"`` is the catch-all.
    condition: str = Field(min_length=1, max_length=800)
    #: Where matching work goes. A ``departments.code``; resolved against the
    #: tenant's own departments by the service, because a rule pointing at a
    #: department that does not exist routes into a void that looks, on a queue,
    #: exactly like a backlog.
    department_code: OrgCode
    #: Optional narrowing within the department (§15.3 picks the individual).
    team_code: OrgCode | None = None
    #: Whether evaluation stops here. Default true — an ordered ruleset where
    #: everything falls through is a ruleset whose last rule always wins, which
    #: is not what "ordered" means to the person reading it.
    stop_on_match: bool = True
    is_active: bool = True

    @field_validator("condition")
    @classmethod
    def _condition_compiles(cls, condition: str) -> str:
        try:
            compile_condition(condition, schema=ROUTING_FACTS)
        except ExpressionError as exc:
            raise ValueError(f"routing condition is not usable: {exc}") from exc
        return condition


class RoutingRules(PolicyBody):
    """§15.2's condition → department mapping, evaluated in document order.

    **No default destination field, deliberately.** The obvious design gives the
    document a ``fallback_department_code`` for complaints no rule matches. It
    was rejected: a fallback department is where misrouted work goes to be
    ignored, and it makes an incomplete ruleset look complete. Instead an
    unmatched complaint is *unrouted*, which is a state the triage queue shows
    and an operator fixes — by adding the rule that was missing. A tenant that
    genuinely wants a catch-all writes one, with a condition of ``True``, and
    then it is visible in the document an approver read.
    """

    rules: tuple[RoutingRule, ...] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def _rule_ids_are_unique(self) -> RoutingRules:
        ids = [rule.rule_id for rule in self.rules]
        duplicates = sorted({rule_id for rule_id in ids if ids.count(rule_id) > 1})
        if duplicates:
            raise ValueError(
                f"duplicate routing rule ids: {', '.join(duplicates)}. The id is "
                f"recorded in ``work_order_created.routing_rule_id``, so a duplicate "
                f"makes a misroute impossible to trace to its cause."
            )
        return self

    @model_validator(mode="after")
    def _no_rule_is_shadowed_by_an_earlier_catch_all(self) -> RoutingRules:
        """Refuse rules that can never be reached.

        A catch-all with ``stop_on_match`` in the middle of a ruleset makes
        everything below it dead, and dead rules are the reason "we added a
        routing rule and nothing changed" is such a common support ticket. This
        catches only the provable case — a literal ``True`` condition — rather
        than attempting general reachability, which is undecidable and would
        produce false refusals nobody could argue with.
        """
        for index, rule in enumerate(self.rules):
            if not (rule.is_active and rule.stop_on_match and rule.condition.strip() == "True"):
                continue
            unreachable = [later.rule_id for later in self.rules[index + 1 :] if later.is_active]
            if unreachable:
                raise ValueError(
                    f"rule {rule.rule_id!r} matches everything and stops evaluation, so "
                    f"{', '.join(unreachable)} can never fire. Move the catch-all last."
                )
        return self


# ---------------------------------------------------------------------------
# Rate card (§17.2)
# ---------------------------------------------------------------------------


class RateCardItem(PolicyBody):
    """One priced unit of work.

    ``Decimal`` throughout — see the module docstring. The §17.2 deviation
    detector compares an invoiced amount against rate times quantity, and in binary
    floating point that comparison manufactures discrepancies of its own, which
    then get raised against a contractor who did nothing wrong. §6.5 requires
    being fair to both sides, and that starts with arithmetic that does not
    invent the evidence.
    """

    code: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,62}$", max_length=63)]
    display_name: str = Field(min_length=1, max_length=200)
    unit: Annotated[str, Field(min_length=1, max_length=32)]
    rate: Annotated[Decimal, Field(gt=Decimal(0), max_digits=14, decimal_places=4)]
    #: Categories this rate applies to. Empty means every category, which is
    #: right for a unit like "supervisor hour" and wrong for one like "asphalt
    #: per square metre".
    categories: tuple[TaxonomyKey, ...] = ()
    #: Fractional tolerance before §17.2 flags a deviation. Per item because a
    #: fixed-price inspection has no legitimate variance and a materials line
    #: genuinely does.
    tolerance_fraction: Annotated[float, Field(ge=0.0, le=1.0)] = 0.10


class RateCard(PolicyBody):
    """§17.2's effective-dated rates.

    Effective dating is on the *document* (``effective_from`` on the version
    row), not on each item, and that is the design decision worth stating: a
    rate card is a negotiated set that takes effect on a date, and per-item
    dates would allow a card that is half old and half new — a state no contract
    describes and every dispute would turn on.
    """

    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] = "INR"
    items: tuple[RateCardItem, ...] = Field(min_length=1, max_length=1024)

    @model_validator(mode="after")
    def _item_codes_are_unique(self) -> RateCard:
        codes = [item.code for item in self.items]
        duplicates = sorted({code for code in codes if codes.count(code) > 1})
        if duplicates:
            raise ValueError(f"duplicate rate card item codes: {', '.join(duplicates)}")
        return self


# ---------------------------------------------------------------------------
# Trust thresholds (§11.1, §11.3, §22.4) — Phase 8
# ---------------------------------------------------------------------------


class ExifPolicy(PolicyBody):
    """§11.1's EXIF cross-check, as numbers an operator owns.

    **The three outcomes are configured separately because they are three
    different findings**, and collapsing them is how "absent EXIF reduces trust
    rather than rejecting" quietly becomes a rejection. A photograph whose EXIF
    says it was taken 3 km away is a claim that contradicts the report; a
    photograph with no EXIF at all is a WhatsApp share flow, which §11.1 names
    explicitly and which describes a large fraction of honest submissions.

    Every delta is signed and bounded. A positive ``matched_trust_delta`` is
    allowed — confirming evidence should be able to *raise* trust, or the score
    only ever falls and the scale is really a counter of suspicions.
    """

    #: Beyond this, the claimed and photographed locations disagree. §11.1 says
    #: "~200m"; the tilde is why this is a field.
    mismatch_distance_meters: Annotated[float, Field(gt=0.0, le=100_000.0)] = 200.0
    #: Applied when EXIF GPS is present and within the radius.
    matched_trust_delta: Annotated[float, Field(ge=-1.0, le=1.0)] = 0.15
    #: Applied when EXIF GPS is present and outside it. The strongest signal
    #: here, because it is the only one that is a contradiction rather than a
    #: silence.
    mismatch_trust_delta: Annotated[float, Field(ge=-1.0, le=1.0)] = -0.4
    #: Applied when the file carries no EXIF at all. Deliberately mild — see the
    #: class docstring, and §11.1's own note that this is the common case.
    absent_trust_delta: Annotated[float, Field(ge=-1.0, le=1.0)] = -0.1
    #: A mismatch is a contradiction and §11.4 says every flag has a
    #: destination, so this defaults on. Absence does not queue anything: a
    #: queue that receives every WhatsApp submission is a queue nobody reads.
    mismatch_queues_review: bool = True

    #: §11.1's *real* control for the stripped-EXIF case: refuse gallery uploads
    #: and require live capture. Off by default because turning it on excludes
    #: every citizen whose phone or browser cannot do it, which is an equity
    #: decision (§23) a tenant makes rather than a default the platform imposes.
    live_capture_only: bool = False


class PerceptualHashPolicy(PolicyBody):
    """§11.1 perceptual hashing — the re-upload check.

    ``max_hamming_distance`` is on a 64-bit dHash. The useful range is narrow
    and the ends are both failure modes: 0 catches only byte-identical
    re-encodes, which MD5 already does more cheaply, while above ~16 unrelated
    photographs of the same kind of scene start colliding and every pothole
    matches every other pothole. The default sits where the §11.1 claim —
    "catches re-uploaded/screenshotted images even after compression or resize"
    — is actually true.
    """

    max_hamming_distance: Annotated[int, Field(ge=0, le=32)] = 8
    #: How far back the search looks. Bounded because the query is a scan over
    #: the tenant's recent media, and an unbounded window makes the trust stage
    #: slower every day the deployment stays up.
    lookback_hours: Annotated[int, Field(gt=0, le=8760)] = 720
    trust_delta: Annotated[float, Field(ge=-1.0, le=1.0)] = -0.35
    queues_review: bool = True
    is_active: bool = True


class VelocityPolicy(PolicyBody):
    """§11.3 device/session velocity.

    §11.3 describes a Redis token bucket. This is deliberately a **query over
    submissions**, not a bucket: a bucket forgets, and the question a reviewer
    asks is "show me the other nineteen", which a counter cannot answer. The
    rate limiter in ``api.ratelimit`` is the Redis token bucket, and it already
    exists — it protects the *service*. This protects the *record*, and the two
    want different memories.
    """

    max_submissions_per_window: Annotated[int, Field(gt=0, le=10_000)] = 12
    window_hours: Annotated[float, Field(gt=0.0, le=168.0)] = 1.0
    trust_delta: Annotated[float, Field(ge=-1.0, le=1.0)] = -0.3
    queues_review: bool = True
    is_active: bool = True


class GeoClusterPolicy(PolicyBody):
    """§11.3 geographic clustering — several "different" reporters, one spot.

    The signal is *distinct device fingerprints inside one radius inside one
    window*, which is why ``min_distinct_devices`` is separate from the velocity
    check's count: one device filing twenty reports is velocity, and twenty
    devices filing one each on the same corner within an hour is coordination.
    Fired on the same evidence, they would be one detector that cannot tell a
    protest from a bot farm.
    """

    radius_meters: Annotated[float, Field(gt=0.0, le=10_000.0)] = 150.0
    window_hours: Annotated[float, Field(gt=0.0, le=168.0)] = 6.0
    min_distinct_devices: Annotated[int, Field(ge=2, le=1000)] = 4
    trust_delta: Annotated[float, Field(ge=-1.0, le=1.0)] = -0.25
    queues_review: bool = True
    is_active: bool = True


class MediaRetentionPolicy(PolicyBody):
    """§22.4's schedule, as the tenant's own clocks.

    Stated here rather than as constants because the table in §22.4 is a
    *statable* policy a customer negotiates — a campus under a different
    regulator keeps raw photographs for seven days, and a state utility may be
    required to keep them for a year. The sweep that acts on these is Phase 26;
    what this phase owes it is a stamped expiry on every artefact, which is
    ``submission_media.purge_raw_after``.
    """

    #: §22.4: raw uploaded photo, 30 days.
    raw_media_days: Annotated[int, Field(gt=0, le=3650)] = 30
    #: §22.4: EXIF metadata, 90 days.
    exif_days: Annotated[int, Field(gt=0, le=3650)] = 90

    @model_validator(mode="after")
    def _exif_outlives_the_image_it_describes(self) -> MediaRetentionPolicy:
        if self.exif_days < self.raw_media_days:
            raise ValueError(
                f"exif_days ({self.exif_days}) is below raw_media_days "
                f"({self.raw_media_days}); §22.4 keeps EXIF for the fraud-pattern "
                f"review window, which is the window that outlives the photograph. "
                f"Purging the metadata first leaves the raw image with nothing to "
                f"review it against, which is the worst of both retentions"
            )
        return self


class TrustThresholds(PolicyBody):
    """Every §11 knob the trust spine reads, in one approved document.

    **One document rather than five kinds.** They are read together, by one
    pipeline stage, on one complaint, and a tenant that tightened its velocity
    limit while its EXIF radius came from a revision six months older would have
    a trust posture nobody ever reviewed as a whole. The same argument
    ``PolicyBundle`` makes about resolving documents together, one level down.

    **Each detector carries its own ``is_active``, and the EXIF check does
    not.** A tenant can switch off velocity or geo-clustering — they are
    heuristics that will be noisy in some deployments. The EXIF cross-check has
    no switch because turning it off is indistinguishable from every photograph
    being verified, and §11.1's whole point is that absent evidence must stay
    visible as absent.

    **There is no switch for face blur here, deliberately.** §22.1 is a legal
    obligation, not a tuning parameter, and a policy field that disables it
    would be a documented, approvable path to a privacy breach. Redaction fails
    closed in ``trust.redaction`` instead: no detector means no redacted copy
    means nothing to serve.
    """

    exif: ExifPolicy = ExifPolicy()
    perceptual_hash: PerceptualHashPolicy = PerceptualHashPolicy()
    velocity: VelocityPolicy = VelocityPolicy()
    geo_cluster: GeoClusterPolicy = GeoClusterPolicy()
    retention: MediaRetentionPolicy = MediaRetentionPolicy()

    #: Trust at or below this queues the report for review regardless of which
    #: individual checks fired. §11.4's backstop: three mild signals that each
    #: decline to queue on their own still add up to a report worth a human's
    #: attention, and without this they would add up to nothing.
    review_trust_floor: Annotated[float, Field(ge=-10.0, le=10.0)] = -0.5


# ---------------------------------------------------------------------------
# Perception calibration (Phase 9, §13.3 / §43.1)
# ---------------------------------------------------------------------------


class CategoryCalibration(PolicyBody):
    """The measured curve for one category, as four numbers.

    **Why per category and not one global temperature.** Zero-shot similarity is
    not comparable across categories: "a pothole in a road" and "an overflowing
    garbage bin" sit at different places in CLIP's similarity band, so a single
    temperature makes one category systematically over-confident and the other
    systematically under-confident — and the two errors do not cancel, they show
    up as one category that never reaches its abstain floor and another that
    never leaves it.

    **``bias`` is additive on the *similarity*, before temperature**:
    ``logit = (cosine + bias) / temperature``. Platt scaling's shape, written in
    the model's own units, and chosen over a multiplicative correction because a
    multiplier and a temperature are the same knob wearing two names while an
    offset is genuinely independent — temperature controls how *sharp* the
    distribution is, bias controls where this category sits within it.

    **Before the temperature rather than after, and that ordering is the whole
    reason a per-category temperature is safe.** A softmax ignores a shift
    applied to every logit, so with one global temperature the offset would be
    decoration. With a temperature *per category* the logits are on different
    scales, and without a per-category centre the category with the smallest
    temperature wins every comparison on arithmetic rather than on evidence.
    Putting the offset in similarity space also puts it inside a range an
    approver can read: a cosine lives in [-1, 1], so the ±10 bound here is
    generous, whereas the same centring expressed as a logit offset is in the
    hundreds and no bound on it would mean anything.

    **``sample_size`` is required and is not decoration.** A temperature fitted
    on nine examples is a number with the same shape and none of the authority of
    one fitted on nine hundred, and the person approving this document is the
    only one positioned to tell the difference. Recording it means an approver is
    shown the evidence rather than the conclusion, which is architectural
    principle 4 applied to the model's own tuning.
    """

    category: TaxonomyKey
    #: Divides the cosine before the softmax. Small values sharpen. Bounded well
    #: away from zero — see ``scoring.MIN_TEMPERATURE`` on what a temperature at
    #: zero does to a confidence field the log keeps forever.
    temperature: Annotated[float, Field(gt=0.005, le=10.0)] = 0.05
    bias: Annotated[float, Field(ge=-10.0, le=10.0)] = 0.0
    #: Confidence below which this category is not claimed at all.
    abstain_below: Probability = 0.35
    #: Required lead over the runner-up, in probability. Zero disables the check.
    min_margin: Annotated[float, Field(ge=0.0, le=1.0)] = 0.05
    #: How many labelled examples the numbers above were fitted on.
    sample_size: Annotated[int, Field(ge=0)] = 0
    #: Free text: which harness run, on which corpus, measured when. Read by a
    #: human at approval time, by nothing at runtime.
    provenance: Annotated[str, Field(max_length=500)] = ""


class PerceptionCalibration(PolicyBody):
    """How the perception layer turns similarities into decisions, per tenant.

    **What is here and what is deliberately not.** The knobs below decide how
    *confident* the layer is allowed to be and when it must decline; they do not
    decide what the categories are (taxonomy), what describes them (prompt sets),
    or which model runs (deployment). Splitting it that way means the numbers a
    data scientist retunes weekly live in a document with an approval trail,
    while the ones that change with a release live in the release.

    **The defaults apply to every category without an entry, and that is the
    load-bearing behaviour.** Phase 9's gate says a new tenant category is
    classifiable by adding prompts alone. If a category required a calibration
    row before it could be scored, that would be false — so an absent row means
    "use the tenant default", not "cannot score".

    **``visual_safety_threshold`` is a raw cosine, not a probability**, and it is
    the one field here that feeds §11.2 rather than §43.1. A safety rule's visual
    prompts are not competing with the taxonomy — "is there fire in this image"
    is a yes/no against one phrase, not a ranking against forty — so a softmax
    probability would be meaningless for it. Kept in this document rather than in
    ``trust_thresholds`` because it is a property of the *model's* similarity
    scale: it has to be re-measured when the checkpoint changes, and it would be
    the one field in the trust document that a model upgrade invalidated.
    """

    default_temperature: Annotated[float, Field(gt=0.005, le=10.0)] = 0.05

    default_abstain_below: Probability = 0.15
    """Confidence floor for a category with no measured curve.

    **0.15 is a measured number, not a round one, and the number it replaced was
    round.** The floor started at 0.35, which reads like a sensible "more likely
    than not to be roughly right" threshold and is not one: the confidence it is
    compared against comes out of a softmax over every category *and* every
    category's contrast pool, so its ceiling falls as the taxonomy grows. On the
    nine-category `municipality` template that made the shipped default abstain
    on **100%** of the held-out corpus — a new tenant would have classified
    nothing at all until somebody approved a fitted document, and the symptom
    would have been an empty work list rather than an error.

    0.15 sits above the 1/9 ≈ 0.111 a nine-way coin flip reaches and below the
    0.164 operating point the harness fits on that template's own data, which is
    the interval a default should be in: better than chance, more cautious than a
    measurement. It is still a default and it is still taxonomy-size dependent —
    the fix for that is a fitted document per tenant, which is what
    `nem f1` produces and what §43.2's loop is for. See
    `docs/reports/perception-f1.md`.
    """

    default_min_margin: Annotated[float, Field(ge=0.0, le=1.0)] = 0.05

    #: How image and text evidence combine (``scoring.combine``). Below 0.5
    #: because a citizen's own words name the problem, while a street photograph
    #: contains a road, a sky, and some rubbish whatever is being reported.
    image_weight: Weight = 0.45

    #: Cosine at or above which a §11.2 visual prompt counts as matched. High,
    #: and biased that way on purpose: a false visual match halts a report and
    #: sends it to a human, which is survivable, but a threshold low enough to
    #: fire on ordinary street scenes turns §11.2's fail-safe into noise that
    #: gets switched off — and then the keyword half goes with it.
    visual_safety_threshold: Annotated[float, Field(ge=0.0, le=1.0)] = 0.28

    #: Below this, a detected language is recorded but treated as unverified: the
    #: transcript is still used, and the *locale-specific* prompt set is not,
    #: because scoring Marathi text against prompts chosen for a misdetected
    #: Hindi is worse than scoring it against the tenant's default locale.
    min_language_confidence: Probability = 0.5

    categories: tuple[CategoryCalibration, ...] = Field(default=(), max_length=512)

    @model_validator(mode="after")
    def _one_entry_per_category(self) -> PerceptionCalibration:
        keys = [entry.category for entry in self.categories]
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        if duplicates:
            raise ValueError(
                f"more than one calibration entry for {', '.join(duplicates)}; two "
                f"curves for one category means the applied one depends on document "
                f"order, which nobody reviewing this document would think to check"
            )
        return self


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

#: Kind → body model. The one place the lifecycle service consults, so adding a
#: ninth governed structure is this line plus a model, and touches neither the
#: service, the API, nor the migration.
BODY_MODELS: Final[dict[PolicyKind, type[PolicyBody]]] = {
    PolicyKind.SEVERITY_RUBRIC: SeverityRubric,
    PolicyKind.DEDUP_THRESHOLDS: DedupThresholds,
    PolicyKind.SAFETY_RULESET: SafetyRuleset,
    PolicyKind.SLA_MATRIX: SlaMatrix,
    PolicyKind.ROUTING_RULES: RoutingRules,
    PolicyKind.RATE_CARD: RateCard,
    PolicyKind.TRUST_THRESHOLDS: TrustThresholds,
    PolicyKind.PERCEPTION_CALIBRATION: PerceptionCalibration,
}


def body_model(kind: PolicyKind) -> type[PolicyBody]:
    """The model for one kind.

    Raises rather than returning ``None``: every member of a closed enum has a
    model by construction, so a miss here is a registry that has drifted from
    the enum, and continuing would write an unvalidated document into the table
    the whole phase exists to keep validated.
    """
    try:
        return BODY_MODELS[kind]
    except KeyError:  # pragma: no cover - unreachable while the enum is closed
        raise PolicyValidationError(
            f"no body model registered for policy kind {kind!r}; BODY_MODELS and "
            f"PolicyKind have drifted apart"
        ) from None


def validate_body(kind: PolicyKind, body: Any) -> PolicyBody:
    """Parse and validate a document body against its kind.

    The single entry point for every writer — the API, the provisioner, the
    template loader, and the tests — so a document that exists in the table has
    provably been through the same checks whatever created it.
    """
    model = body_model(kind)
    try:
        return model.model_validate(body)
    except Exception as exc:  # pydantic raises ValidationError; message is the payload
        raise PolicyValidationError(f"{kind.value} document is not valid: {exc}") from exc


__all__ = [
    "BODY_MODELS",
    "DECIDING_STATUSES",
    "CategoryCalibration",
    "DedupBand",
    "DedupThresholds",
    "ExifPolicy",
    "GeoClusterPolicy",
    "MediaRetentionPolicy",
    "NodeSeverityOverride",
    "PerceptionCalibration",
    "PerceptualHashPolicy",
    "PolicyBody",
    "PolicyKind",
    "PolicyStatus",
    "RateCard",
    "RateCardItem",
    "RoutingRule",
    "RoutingRules",
    "RubricComponent",
    "SafetyMatchMode",
    "SafetyRule",
    "SafetyRuleset",
    "SeverityRubric",
    "SeverityTier",
    "SlaEntry",
    "SlaMatrix",
    "TrustThresholds",
    "VelocityPolicy",
    "body_model",
    "validate_body",
]
