"""Reading policy at decision time: hot reload, ancestor walks, version stamps.

Three obligations from the phase gate, and each shapes something here:

*"Changing a severity weight takes effect within one reload interval, with no
deploy."* Every decision reading the database would satisfy that and cost a
round trip on the hottest path in the pipeline. So this module holds an
in-process snapshot per (tenant, kind), refreshed on a TTL — the same trade
``flags.service`` makes, stated the same way: **a change takes effect within one
reload interval, not instantly.** Thirty seconds by default, because a policy
revision is a reviewed, approved act, not a kill switch, and the honest number
is the one an operator can plan around.

*"Every scored complaint records the exact policy version that scored it."* Every
resolution returns a ``Resolved`` carrying the document *and* its stamp. Not two
calls — one to read the policy and one to ask which version it was — because two
calls across a reload boundary can disagree, and the disagreement would show up
as a complaint scored by revision 7 and stamped revision 8.

*"An unapproved draft can never influence a production decision."* The snapshot
loader reads through ``service.active_version``, which filters on
``DECIDING_STATUSES`` and on the effective date. There is no other read path in
this module, and no parameter that widens it — a caller cannot ask this resolver
for a draft even by accident.

**Resolution walks the taxonomy upward.** A rubric override on ``electrical``
applies to ``electrical.exposed_cable`` without anyone editing the document, and
keeps applying to a child the tenant adds next year. The walk is over the
materialised ``path`` the Phase 5 taxonomy maintains, so it costs one query for
the node and then pure string work.

**A tenant with no active policy gets the baseline, not an error.** Provisioning
seeds baselines, so in practice every tenant has one; but a deployment mid-
migration must still score rather than 500, and the baseline is the same
document provisioning would have written. What it must never do is silently
differ from it — ``policy.baselines`` is the single source, used by both.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.db.models.taxonomy import TaxonomyNode
from nemesis.observability.logging import get_logger
from nemesis.policy import baselines, service
from nemesis.policy.documents import (
    DedupBand,
    DedupThresholds,
    PolicyBody,
    PolicyKind,
    RateCard,
    RoutingRule,
    RoutingRules,
    SafetyMatchMode,
    SafetyRuleset,
    SeverityRubric,
    SlaEntry,
    SlaMatrix,
    validate_body,
)
from nemesis.policy.errors import PolicyNotFoundError, PolicyValidationError
from nemesis.policy.expressions import ROUTING_FACTS, Condition, compile_condition

log = get_logger(__name__)

#: How long a loaded document is trusted before the next read refreshes it.
#: Thirty seconds, not the flag system's five: a flag is an emergency handle
#: pulled during an outage, a policy revision is an approved change somebody
#: scheduled. Both numbers are honest about their latency; they are honest about
#: different things.
DEFAULT_RELOAD_SECONDS: Final = 30.0

#: The stamp used when a decision was made against a baseline rather than a
#: tenant document. Deliberately not a plausible-looking revision number: a
#: complaint scored before its tenant had a rubric must be identifiable as such
#: forever, and ``severity_rubric@1`` would make it look like it was scored by a
#: document somebody approved.
BASELINE_STAMP: Final = "baseline"


@dataclass(frozen=True, slots=True)
class Resolved[BodyT: PolicyBody]:
    """A policy document and the identity of the version it came from.

    One object rather than two return values, so a caller physically cannot
    stamp a decision with a version other than the one that decided it.
    """

    body: BodyT
    stamp: str
    #: ``None`` for a baseline. Present for anything an operator approved, and
    #: the link an incident review follows from a decision back to the document.
    version_id: uuid.UUID | None
    revision: int | None
    content_hash: str
    #: True when this came from ``policy.baselines`` because the tenant has no
    #: active document of this kind. Explicit rather than inferred from a null
    #: revision, because "inferred from a null" is how a monitoring query ends
    #: up silently counting the wrong thing.
    is_baseline: bool


@dataclass(frozen=True, slots=True)
class _Entry:
    """One cached document, with when it was loaded."""

    resolved: Resolved[Any]
    loaded_at: float


class PolicyResolver:
    """Per-process policy cache with a TTL, and the resolution helpers.

    Instantiated per application, not per request — a cache that lives for one
    request caches nothing. ``reload_seconds=0`` disables caching entirely,
    which is what the tests and the Phase 7 backtester use: a backtest that read
    a stale snapshot would report a delta against a policy that was not the one
    it claimed to compare.
    """

    def __init__(self, *, reload_seconds: float = DEFAULT_RELOAD_SECONDS) -> None:
        self._reload_seconds = reload_seconds
        self._entries: dict[tuple[uuid.UUID, PolicyKind], _Entry] = {}

    # -- cache ------------------------------------------------------------

    def invalidate(self, *, tenant_id: uuid.UUID | None = None) -> None:
        """Drop cached documents, for one tenant or for all.

        Called after an activation in the same process, so an operator who
        activates and immediately re-reads sees their own change rather than
        waiting out a TTL and concluding the button did nothing. It is a
        *courtesy*, not the mechanism: other processes still refresh on the TTL,
        which is why the reload interval is documented as the real latency.
        """
        if tenant_id is None:
            self._entries.clear()
            return
        for key in [key for key in self._entries if key[0] == tenant_id]:
            del self._entries[key]

    async def document(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, kind: PolicyKind
    ) -> Resolved[Any] | None:
        """The active document for a kind, from cache or from the database.

        ``None`` means the tenant has no active document *and* the kind has no
        platform baseline — routing rules and rate cards, which name departments
        and negotiated prices the platform cannot invent. It is a real state
        with a correct behaviour (leave the complaint unrouted; run no deviation
        detection), so it is returned rather than raised, and the type makes
        every caller decide what to do about it.
        """
        key = (tenant_id, kind)
        entry = self._entries.get(key)
        now = time.monotonic()
        if (
            entry is not None
            and self._reload_seconds > 0
            and now - entry.loaded_at < self._reload_seconds
        ):
            return entry.resolved

        resolved = await self._load(session, tenant_id=tenant_id, kind=kind)
        if resolved is not None:
            self._entries[key] = _Entry(resolved=resolved, loaded_at=now)
        return resolved

    async def require_document(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, kind: PolicyKind
    ) -> Resolved[Any]:
        """As ``document``, for the four kinds that always resolve to something.

        Exists so the typed accessors below can promise a non-optional result
        for the kinds that have a baseline, rather than making every caller
        write a ``None`` branch that is unreachable by construction. The raise
        is the honest failure if the baseline registry and this promise ever
        drift apart.
        """
        resolved = await self.document(session, tenant_id=tenant_id, kind=kind)
        if resolved is None:  # pragma: no cover - unreachable for baselined kinds
            raise PolicyNotFoundError(
                f"no active {kind.value} for this tenant and no platform baseline"
            )
        return resolved

    async def _load(
        self, session: AsyncSession, *, tenant_id: uuid.UUID, kind: PolicyKind
    ) -> Resolved[Any] | None:
        version = await service.active_version(session, tenant_id=tenant_id, kind=kind)
        if version is None:
            if not baselines.has_baseline(kind):
                return None
            body = baselines.baseline_body(kind)
            log.info(
                "policy_baseline_used",
                tenant_id=str(tenant_id),
                kind=kind.value,
                reason="tenant has no active document of this kind",
            )
            return Resolved(
                body=body,
                stamp=BASELINE_STAMP,
                version_id=None,
                revision=None,
                content_hash=service.content_hash(body.model_dump(mode="json")),
                is_baseline=True,
            )
        return Resolved(
            body=validate_body(kind, version.body),
            stamp=version.stamp,
            version_id=version.id,
            revision=version.revision,
            content_hash=version.content_hash,
            is_baseline=False,
        )

    # -- typed accessors --------------------------------------------------
    #
    # One per kind, rather than a single generic `document()` every caller
    # casts. The cast is the bug: a routing stage that reads a `SeverityRubric`
    # by mistake fails at the first attribute access, deep inside a worker, and
    # mypy would have caught it here.

    async def severity_rubric(
        self, session: AsyncSession, *, tenant_id: uuid.UUID
    ) -> Resolved[SeverityRubric]:
        return await self.require_document(
            session, tenant_id=tenant_id, kind=PolicyKind.SEVERITY_RUBRIC
        )

    async def dedup_thresholds(
        self, session: AsyncSession, *, tenant_id: uuid.UUID
    ) -> Resolved[DedupThresholds]:
        return await self.require_document(
            session, tenant_id=tenant_id, kind=PolicyKind.DEDUP_THRESHOLDS
        )

    async def safety_ruleset(
        self, session: AsyncSession, *, tenant_id: uuid.UUID
    ) -> Resolved[SafetyRuleset]:
        return await self.require_document(
            session, tenant_id=tenant_id, kind=PolicyKind.SAFETY_RULESET
        )

    async def sla_matrix(
        self, session: AsyncSession, *, tenant_id: uuid.UUID
    ) -> Resolved[SlaMatrix]:
        return await self.require_document(session, tenant_id=tenant_id, kind=PolicyKind.SLA_MATRIX)

    async def routing_rules(
        self, session: AsyncSession, *, tenant_id: uuid.UUID
    ) -> Resolved[RoutingRules] | None:
        """``None`` when the tenant has authored no routing rules — see ``document``."""
        return await self.document(session, tenant_id=tenant_id, kind=PolicyKind.ROUTING_RULES)

    async def rate_card(
        self, session: AsyncSession, *, tenant_id: uuid.UUID
    ) -> Resolved[RateCard] | None:
        """``None`` when the tenant has negotiated no rates — see ``document``."""
        return await self.document(session, tenant_id=tenant_id, kind=PolicyKind.RATE_CARD)


# ---------------------------------------------------------------------------
# Taxonomy-aware resolution
# ---------------------------------------------------------------------------


async def category_lineage(
    session: AsyncSession, *, tenant_id: uuid.UUID, category: str
) -> tuple[str, ...]:
    """The category and its ancestors, most specific first.

    Derived from the materialised ``path`` the taxonomy maintains, which is why
    this is one query rather than one per level. A category the tenant does not
    define returns just itself: an unknown key must still resolve to the default
    band rather than raising, because the classifier can emit a key for a node
    that was deactivated between classification and scoring.
    """
    row = await session.execute(
        select(TaxonomyNode.path).where(
            TaxonomyNode.tenant_id == tenant_id, TaxonomyNode.key == category
        )
    )
    path = row.scalar_one_or_none()
    if path is None:
        return (category,)
    return tuple(reversed(path.split("/")))


def resolve_severity_override(
    rubric: SeverityRubric, *, lineage: Sequence[str]
) -> tuple[float, float, bool]:
    """The floor, multiplier, and bypass flag for a category.

    Most-specific wins and the walk stops there — overrides do not compose. A
    parent floor of 5 and a child multiplier of 2 combining into "floor 5,
    multiplier 2" reads reasonably and is impossible to predict once the tree is
    four levels deep, which is precisely when somebody needs to predict it.
    """
    by_category = {override.category: override for override in rubric.overrides}
    for key in lineage:
        override = by_category.get(key)
        if override is not None:
            return override.floor, override.multiplier, override.bypasses_scoring
    return 0.0, 1.0, False


def resolve_dedup_band(thresholds: DedupThresholds, *, lineage: Sequence[str]) -> DedupBand:
    """The band for a category, falling back through ancestors to the default.

    The default band is guaranteed to exist by ``DedupThresholds``'s own
    validator, so this cannot return ``None`` and the caller needs no fallback
    of its own — which is the point of validating the invariant at the document
    rather than checking it at every read.
    """
    by_category = {band.category: band for band in thresholds.bands if band.category is not None}
    for key in lineage:
        band = by_category.get(key)
        if band is not None:
            return band
    return next(band for band in thresholds.bands if band.category is None)


def resolve_severity_tier(matrix: SlaMatrix, *, score: float) -> str:
    """Which tier a score falls into.

    The highest tier whose floor the score reaches. ``SlaMatrix`` guarantees a
    tier at 0.0, so every score in range has one and this cannot fall through.
    """
    ordered = sorted(matrix.tiers, key=lambda tier: tier.min_score, reverse=True)
    for tier in ordered:
        if score >= tier.min_score:
            return tier.tier
    return ordered[-1].tier


def resolve_sla_entry(matrix: SlaMatrix, *, lineage: Sequence[str], severity_tier: str) -> SlaEntry:
    """The most specific SLA entry for a category and tier.

    Specificity order, and it is a decision rather than an obvious ordering:
    category+tier, then category alone, then tier alone, then the catch-all. A
    category-specific row beats a tier-specific one because "gas leaks are
    always four hours" is a statement about the work, while "urgent is twelve
    hours" is a statement about the queue — and when they disagree, the work
    wins.
    """
    for key in lineage:
        for entry in matrix.entries:
            if entry.category == key and entry.severity_tier == severity_tier:
                return entry
    for key in lineage:
        for entry in matrix.entries:
            if entry.category == key and entry.severity_tier is None:
                return entry
    for entry in matrix.entries:
        if entry.category is None and entry.severity_tier == severity_tier:
            return entry
    return next(
        entry for entry in matrix.entries if entry.category is None and entry.severity_tier is None
    )


@dataclass(frozen=True, slots=True)
class RouteDecision:
    """Where a complaint goes, and which rule sent it there.

    ``rule_id`` is written into ``work_order_created.routing_rule_id``, which is
    what makes silent misrouting diagnosable — "everything went to Roads" is
    answerable by reading one field rather than by re-deriving the rules against
    a policy that may since have changed.
    """

    department_code: str | None
    team_code: str | None
    rule_id: str | None
    #: Every active rule that matched, in order. Longer than one when rules do
    #: not stop on match; kept because "why did this go to Sanitation rather
    #: than Roads" is a question about the rules that *also* matched.
    matched_rule_ids: tuple[str, ...] = ()


def evaluate_routing(rules: RoutingRules, facts: Mapping[str, Any]) -> RouteDecision:
    """Run an ordered ruleset against one complaint's facts.

    Cannot raise: every condition was compiled at document-validation time
    against the same fact schema, and a compiled condition is total (see
    ``policy.expressions``). An unmatched complaint returns a decision with no
    department — *unrouted*, which the triage queue shows — rather than a
    fallback department where misrouted work goes to be ignored.
    """
    matched: list[str] = []
    destination: RoutingRule | None = None
    for rule in rules.rules:
        if not rule.is_active:
            continue
        condition = _condition_for(rule.condition)
        if not condition.evaluate(facts):
            continue
        matched.append(rule.rule_id)
        if destination is None:
            destination = rule
        if rule.stop_on_match:
            break

    if destination is None:
        return RouteDecision(
            department_code=None, team_code=None, rule_id=None, matched_rule_ids=()
        )
    return RouteDecision(
        department_code=destination.department_code,
        team_code=destination.team_code,
        rule_id=destination.rule_id,
        matched_rule_ids=tuple(matched),
    )


#: Compiled conditions, keyed by source text. Compilation is pure and the source
#: is bounded by ``MAX_EXPRESSION_CHARS``, so caching is safe and the cache
#: cannot be grown without bound by anything an author can write — a tenant's
#: rulesets hold at most a few hundred distinct conditions.
_CONDITION_CACHE: dict[str, Condition] = {}


def _condition_for(source: str) -> Condition:
    cached = _CONDITION_CACHE.get(source)
    if cached is None:
        cached = compile_condition(source, schema=ROUTING_FACTS)
        _CONDITION_CACHE[source] = cached
    return cached


# ---------------------------------------------------------------------------
# Safety evaluation (§11.2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SafetyDecision:
    """Whether the danger path fires, and on what evidence.

    ``matched_terms`` reaches ``safety_trigger_fired.matched_terms``, so a
    citizen or an operator can see exactly which words bypassed the queue. §6.1:
    prove, don't log.
    """

    fired: bool
    rule_id: str | None = None
    severity_floor: float = 0.0
    matched_terms: tuple[str, ...] = ()
    detection_source: str = "keyword"


def evaluate_safety(
    ruleset: SafetyRuleset,
    *,
    text: str | None,
    locale: str | None = None,
    visual_matches: Sequence[str] = (),
) -> SafetyDecision:
    """Run the ruleset. Deterministic: same inputs, same decision, always.

    No regular expressions (see ``SafetyMatchMode``), no set iteration whose
    order could vary, no early exit that depends on anything but document order.
    Matching is case-insensitive because a shouted "GAS LEAK" is the same
    report, and ``casefold`` rather than ``lower`` because the two differ for
    scripts this system explicitly serves.

    Rules are tried in document order and the first match wins — not the highest
    severity, which would make the outcome depend on a number an author can
    change without realising they reordered the ruleset.
    """
    haystack = (text or "").casefold()
    tokens = frozenset(_tokenise(haystack))
    visual = {match.casefold() for match in visual_matches}

    for rule in ruleset.rules:
        if not rule.is_active:
            continue
        if rule.locales and locale is not None and locale not in rule.locales:
            continue

        keyword_hits = tuple(
            term
            for term in rule.terms
            if _matches(term.casefold(), haystack=haystack, tokens=tokens, mode=rule.match_mode)
        )
        visual_hits = tuple(prompt for prompt in rule.visual_prompts if prompt.casefold() in visual)
        if not keyword_hits and not visual_hits:
            continue

        if keyword_hits and visual_hits:
            source = "both"
        elif visual_hits:
            source = "visual"
        else:
            source = "keyword"
        return SafetyDecision(
            fired=True,
            rule_id=rule.rule_id,
            severity_floor=rule.severity_floor,
            matched_terms=keyword_hits + visual_hits,
            detection_source=source,
        )

    return SafetyDecision(fired=False)


def _matches(term: str, *, haystack: str, tokens: frozenset[str], mode: SafetyMatchMode) -> bool:
    if mode is SafetyMatchMode.SUBSTRING:
        return term in haystack
    # Word mode. A multi-word term cannot be a single token, so it falls back to
    # a substring test bounded by the tokeniser's own separators — which is what
    # "whole word" means for a phrase.
    if " " in term:
        return term in haystack
    return term in tokens


#: Characters that separate words. Explicit rather than ``str.split()`` because
#: punctuation matters here: "gas-leak" and "gas leak" are the same report, and
#: a tokeniser that only splits on whitespace misses one of them.
_SEPARATORS: Final = frozenset(" \t\n\r\f\v.,;:!?()[]{}\"'`/\\|<>@#$%^&*+=~-_")


def _tokenise(text: str) -> list[str]:
    """Split casefolded text into words, on punctuation as well as whitespace.

    Hand-rolled rather than a regex, for the reason ``SafetyMatchMode``
    documents: this runs on submitter-controlled text in the stage with the
    highest retry budget in the pipeline, and it must be linear with no
    backtracking, provably, by inspection.
    """
    tokens: list[str] = []
    current: list[str] = []
    for character in text:
        if character in _SEPARATORS:
            if current:
                tokens.append("".join(current))
                current = []
        else:
            current.append(character)
    if current:
        tokens.append("".join(current))
    return tokens


# ---------------------------------------------------------------------------
# Severity arithmetic (§13.5)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SeverityResult:
    """A score with the breakdown that reproduces it.

    Phase 12's gate requires a scored complaint to reproduce its score from its
    own logged breakdown. That is only possible if ``components``, ``weights``,
    and the two adjustments are all recorded next to the total — which is what
    this carries and what ``severity_scored`` writes.
    """

    score: float
    components: dict[str, float]
    weights: dict[str, float]
    floor_applied: float
    multiplier_applied: float


def score_severity(
    rubric: SeverityRubric,
    *,
    measurements: Mapping[str, float],
    lineage: Sequence[str] = (),
) -> SeverityResult:
    """Apply a rubric to a set of component measurements.

    Order of operations, and it is stated because it is the part people get
    wrong: **weighted sum, then multiplier, then floor, then clamp.** The floor
    is applied after the multiplier so a category floor means "never below this,
    whatever else happened" — applying it first would let a multiplier below one
    push a floored score back under its own floor.

    A component with no measurement takes ``missing_component_score`` rather
    than zero. Zero is not the neutral value on a 0-10 scale; it is the most
    extreme value at one end, and defaulting to it biases every degraded
    complaint downward in a way that only shows up as a slow drift during an
    outage.
    """
    components: dict[str, float] = {}
    weights: dict[str, float] = {}
    total = 0.0
    for component in rubric.components:
        value = measurements.get(component.key, rubric.missing_component_score)
        # Clamped rather than trusted. Measurements come from model outputs and
        # from geospatial queries, and a component of 11.4 would produce a score
        # outside the range `severity_scored` accepts — rejected at append time,
        # inside a worker, after the work was done.
        value = min(max(value, 0.0), 10.0)
        components[component.key] = value
        weights[component.key] = component.weight
        total += value * component.weight

    floor, multiplier, _ = resolve_severity_override(rubric, lineage=lineage)
    scored = min(max(total * multiplier, floor), 10.0)
    return SeverityResult(
        score=scored,
        components=components,
        weights=weights,
        floor_applied=floor,
        multiplier_applied=multiplier,
    )


def combined_dedup_confidence(
    band: DedupBand, *, image_similarity: float | None, text_similarity: float | None
) -> float:
    """Combine the two stage scores under a band's weights.

    When one stage is missing — no photo, no transcript — the other carries the
    whole decision rather than being averaged against a zero. Averaging against
    zero would push every text-only report below the merge threshold, which
    reads as "dedup is conservative" and is actually "dedup is off for audio
    submissions".
    """
    if image_similarity is None and text_similarity is None:
        return 0.0
    if image_similarity is None:
        return float(text_similarity or 0.0)
    if text_similarity is None:
        return float(image_similarity)
    return image_similarity * band.image_weight + text_similarity * band.text_weight


def dedup_outcome(band: DedupBand, *, confidence: float) -> str:
    """Which band a confidence falls into: ``merge``, ``investigate``, ``distinct``.

    Inclusive at the lower edge of each band, so a confidence exactly equal to a
    threshold takes the *more* conservative action of the two — the direction
    §14.3 requires, because a false merge suppresses a genuine citizen report
    while an unmerged duplicate only costs an operator some time.
    """
    if confidence >= band.merge_threshold:
        return "merge"
    if confidence >= band.investigate_threshold:
        return "investigate"
    return "distinct"


def within_dedup_window(band: DedupBand, *, earlier: datetime, later: datetime) -> bool:
    """Whether two reports are close enough in time to be the same incident."""
    if earlier.tzinfo is None or later.tzinfo is None:
        raise PolicyValidationError("dedup window comparison needs timezone-aware timestamps")
    return abs(later - earlier) <= timedelta(hours=band.time_window_hours)


def routing_facts(
    *,
    category: str | None,
    lineage: Sequence[str] = (),
    severity: float | None = None,
    severity_tier: str | None = None,
    zone_code: str | None = None,
    report_count: int = 1,
    is_safety_triggered: bool = False,
    trust_score: float | None = None,
    locale: str | None = None,
    submitted_via: str | None = None,
    tags: Sequence[str] = (),
) -> dict[str, Any]:
    """Build the fact mapping ``evaluate_routing`` consumes.

    A builder rather than a dict literal at the call site, because the fact
    names are a contract with every approved routing rule in every tenant. A
    typo at a call site would make every rule referencing that fact stop
    matching — silently, since an absent fact compares ``False`` — and the only
    symptom would be complaints quietly going unrouted.

    Absent values are *omitted* rather than set to ``None``, which is what makes
    ``policy.expressions``'s absent-fact semantics apply to them.
    """
    facts: dict[str, Any] = {
        "category_ancestors": frozenset(lineage) | ({category} if category else set()),
        "report_count": float(report_count),
        "is_safety_triggered": is_safety_triggered,
        "tags": frozenset(tags),
    }
    if category is not None:
        facts["category"] = category
    if severity is not None:
        facts["severity"] = float(severity)
    if severity_tier is not None:
        facts["severity_tier"] = severity_tier
    if zone_code is not None:
        facts["zone_code"] = zone_code
    if trust_score is not None:
        facts["trust_score"] = float(trust_score)
    if locale is not None:
        facts["locale"] = locale
    if submitted_via is not None:
        facts["submitted_via"] = submitted_via
    return facts


#: The process-wide resolver. A module singleton for the same reason the flag
#: service has one: a cache instantiated per request caches nothing, and passing
#: one through five layers of pipeline plumbing to reach the scoring stage would
#: be threading a global by hand.
RESOLVER: Final = PolicyResolver()


__all__ = [
    "BASELINE_STAMP",
    "DEFAULT_RELOAD_SECONDS",
    "RESOLVER",
    "PolicyResolver",
    "Resolved",
    "RouteDecision",
    "SafetyDecision",
    "SeverityResult",
    "category_lineage",
    "combined_dedup_confidence",
    "dedup_outcome",
    "evaluate_routing",
    "evaluate_safety",
    "resolve_dedup_band",
    "resolve_severity_override",
    "resolve_severity_tier",
    "resolve_sla_entry",
    "routing_facts",
    "score_severity",
    "within_dedup_window",
]
