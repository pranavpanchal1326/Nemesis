"""The documents a tenant starts with, and falls back to.

**One source, two consumers.** Provisioning seeds these as real, approved,
active documents so a new tenant is governed from its first complaint. The
resolver falls back to the same objects when a tenant has no active document of
a kind — which happens exactly twice in practice: during the migration that
introduces Phase 6, and if somebody archives everything. If those two paths
built their defaults separately they would drift, and the symptom would be a
tenant that scores differently before and after someone opens the policy screen.

**Derived from the declared defaults, never from the environment.** ``config.py``
still carries ``SeveritySettings`` and ``DedupSettings``, and the baselines below
read their *model defaults* rather than a live ``Settings`` instance. That is
deliberate: an env var that shifted a baseline weight would make the same
complaint score differently on two workers with different environments, and the
whole point of a policy version stamp is that a score is reproducible from what
the log records. After Phase 6 the way to change a weight is to draft a rubric —
which is the critique-log defect #2 remedy, and leaving a second, quieter path
open would defeat it.

**Not every kind has a baseline, and that is not an omission.** Routing rules
name departments, and rate cards name a tenant's negotiated prices; neither has
a meaningful value the platform could invent. A tenant with no routing document
leaves complaints *unrouted*, which the triage queue shows and an operator
fixes — see ``RoutingRules`` on why there is no fallback department. A tenant
with no rate card simply has no §17.2 deviation detection until it negotiates
one.
"""

from __future__ import annotations

from typing import Final

from nemesis.config import DedupSettings, SeveritySettings
from nemesis.policy.documents import (
    DedupBand,
    DedupThresholds,
    PolicyBody,
    PolicyKind,
    RubricComponent,
    SafetyMatchMode,
    SafetyRule,
    SafetyRuleset,
    SeverityRubric,
    SeverityTier,
    SlaEntry,
    SlaMatrix,
    TrustThresholds,
)

#: The change reason recorded on every seeded document. Stated rather than blank
#: so a support engineer reading the history of a tenant that has never been
#: tuned sees "seeded at provisioning" instead of an empty field that looks like
#: somebody forgot.
SEEDED_REASON: Final = "Seeded at provisioning from the platform baseline"

_SEVERITY_DEFAULTS: Final = SeveritySettings()
_DEDUP_DEFAULTS: Final = DedupSettings()


def _severity_rubric() -> SeverityRubric:
    """§13.5 / Appendix B.3's rubric, as the starting document.

    The four components and their weights are the blueprint's, unchanged. What
    changes in Phase 6 is that they are now a document somebody can revise
    without a deploy, and every score records which revision produced it.

    ``description`` on each component is required by the model and is doing real
    work here: it is the text a citizen sees when §13.1 explains their score, so
    a baseline with placeholder descriptions would ship an unexplainable rubric
    to every tenant that never edits it.
    """
    return SeverityRubric(
        components=(
            RubricComponent(
                key="visual_damage",
                display_name="Visual damage",
                weight=_SEVERITY_DEFAULTS.weight_visual_damage,
                description=(
                    "How severe the defect looks in the submitted photograph, scored by "
                    "the perception layer against the category's prompt set."
                ),
            ),
            RubricComponent(
                key="road_class",
                display_name="Location importance",
                weight=_SEVERITY_DEFAULTS.weight_road_class,
                description=(
                    "How significant the affected location is — an arterial road or a "
                    "main corridor scores above a service lane."
                ),
            ),
            RubricComponent(
                key="poi_proximity",
                display_name="Proximity to sensitive places",
                weight=_SEVERITY_DEFAULTS.weight_poi_proximity,
                description=(
                    "How close the defect is to a school, hospital, or other place "
                    f"where harm is more likely; scored to zero beyond "
                    f"{_SEVERITY_DEFAULTS.poi_zero_score_radius_m:.0f} metres."
                ),
            ),
            RubricComponent(
                key="cluster_count",
                display_name="How many people reported it",
                weight=_SEVERITY_DEFAULTS.weight_cluster_count,
                description=(
                    "Independent reports of the same incident, capped at "
                    f"{_SEVERITY_DEFAULTS.cluster_report_count_cap} so a single busy "
                    "street cannot dominate the queue."
                ),
            ),
        ),
    )


def _dedup_thresholds() -> DedupThresholds:
    """§14.3's provisional, conservative thresholds as the default band.

    One band, with no category, which is the tenant-wide default every category
    resolves to until somebody tunes one. §14.3 requires the threshold be
    documented as provisional and biased conservative — Phase 7's auto-tuner
    proposes better ones as drafts, from real human merge decisions, which is
    the mechanism that makes this a starting point rather than a guess nobody
    revisits.
    """
    return DedupThresholds(
        bands=(
            DedupBand(
                category=None,
                geo_radius_meters=_DEDUP_DEFAULTS.geo_radius_meters,
                time_window_hours=_DEDUP_DEFAULTS.time_window_hours,
                merge_threshold=_DEDUP_DEFAULTS.merge_threshold,
                investigate_threshold=_DEDUP_DEFAULTS.investigate_threshold,
            ),
        )
    )


def _safety_ruleset() -> SafetyRuleset:
    """§11.2's fail-safe, seeded with the hazards that are not domain-specific.

    **The hard part of this function is what it must not contain.** A keyword
    list is the most tempting place in the system to hardcode a municipality's
    vocabulary, and ``check_domain_literals.py`` exists partly to stop exactly
    that. The rules below are limited to hazards that are hazards *everywhere* —
    a gas smell, exposed live wiring, a structure that is collapsing, a person
    in danger of drowning. A campus, an industrial park, and a city all need
    these; none of them is a category, a ward, or a role.

    Anything narrower is the tenant's to add, which is a policy draft rather
    than a release. That is the whole distinction architectural principle 2
    draws: this stays deterministic — it fires or it does not, on the same
    input, every time — while ceasing to be hardcoded.

    ``locales`` is empty on every rule, meaning "match in any locale". The terms
    are English, and a tenant serving other languages adds locale-scoped rules
    with the same ``rule_id`` prefix; seeding guessed translations would be
    worse than seeding none, because a mistranslated danger term fires on
    innocent text and gets the whole ruleset switched off.
    """
    return SafetyRuleset(
        rules=(
            SafetyRule(
                rule_id="hazard.gas",
                display_name="Suspected gas leak",
                rationale=(
                    "A reported gas smell is an ignition risk that cannot wait in a "
                    "scoring queue. Bypasses classification and goes straight to human "
                    "review under §11.2."
                ),
                terms=("gas leak", "gas smell", "smell of gas", "lpg leak", "cylinder leak"),
                match_mode=SafetyMatchMode.SUBSTRING,
                severity_floor=10.0,
            ),
            SafetyRule(
                rule_id="hazard.electrical",
                display_name="Exposed live electricity",
                rationale=(
                    "Exposed conductors and fallen lines are lethal on contact and are "
                    "severe regardless of how the photograph looks, which is precisely "
                    "the case a rubric alone scores wrongly."
                ),
                terms=(
                    "live wire",
                    "live wires",
                    "exposed wire",
                    "exposed cable",
                    "electric shock",
                    "fallen power line",
                    "sparking",
                ),
                match_mode=SafetyMatchMode.SUBSTRING,
                severity_floor=10.0,
            ),
            SafetyRule(
                rule_id="hazard.structural",
                display_name="Imminent structural collapse",
                rationale=(
                    "A structure described as collapsing endangers anyone nearby now, "
                    "not after the queue clears."
                ),
                terms=("collapsing", "collapsed", "about to fall", "building leaning"),
                severity_floor=9.5,
            ),
            SafetyRule(
                rule_id="hazard.open_shaft",
                display_name="Open shaft, manhole, or excavation",
                rationale=(
                    "An uncovered opening is a fall and drowning hazard, and the danger "
                    "is highest at night when it is least visible in a photograph."
                ),
                terms=("open manhole", "uncovered manhole", "open drain", "open pit", "sinkhole"),
                match_mode=SafetyMatchMode.SUBSTRING,
                severity_floor=9.0,
            ),
            SafetyRule(
                rule_id="hazard.person_at_risk",
                display_name="A person is in immediate danger",
                rationale=(
                    "A report describing someone trapped or injured is an emergency "
                    "referral, not a maintenance request. It reaches a human "
                    "immediately and the operator's first action is to escalate "
                    "outside this system."
                ),
                terms=(
                    "person trapped",
                    "someone trapped",
                    "child fell",
                    "unconscious",
                    "drowning",
                ),
                match_mode=SafetyMatchMode.SUBSTRING,
                severity_floor=10.0,
            ),
        ),
    )


def _sla_matrix() -> SlaMatrix:
    """§27.2's table, with tiers that partition the whole 0-10 scale.

    The durations are the blueprint's. The tiers are named rather than numbered
    because they appear on an operator's screen and in a citizen's status page,
    and "tier 2" answers nothing that "high" does not answer better.

    The catch-all entry at the bottom is required by ``SlaMatrix`` and is the
    clause that matters most operationally: a category added after this document
    was approved still has a deadline, so it can still be reported as breaching.
    A complaint with no deadline can never be late, which is the failure §27.2
    exists to prevent.
    """
    return SlaMatrix(
        tiers=(
            SeverityTier(tier="low", min_score=0.0),
            SeverityTier(tier="medium", min_score=4.0),
            SeverityTier(tier="high", min_score=6.5),
            SeverityTier(tier="urgent", min_score=8.5),
        ),
        entries=(
            SlaEntry(severity_tier="urgent", response_hours=2.0, resolution_hours=24.0),
            SlaEntry(severity_tier="high", response_hours=8.0, resolution_hours=72.0),
            SlaEntry(severity_tier="medium", response_hours=24.0, resolution_hours=168.0),
            SlaEntry(severity_tier="low", response_hours=48.0, resolution_hours=504.0),
            # The fallback. Deliberately equal to the medium row rather than to
            # the most generous one: a category with no tier resolved is a
            # configuration gap, and giving it the slowest SLA would hide the
            # gap behind a deadline nobody ever misses.
            SlaEntry(response_hours=24.0, resolution_hours=168.0),
        ),
    )


def _trust_thresholds() -> TrustThresholds:
    """Phase 8's §11 knobs, entirely at their declared defaults.

    **Nothing is restated here, and that is the point.** Every other baseline in
    this module has to compose parts — a rubric needs its six components spelled
    out, an SLA matrix needs four tiers and five rows — because the shape has no
    single sensible whole. ``TrustThresholds`` does: each field's default is
    argued at the field, next to the section it implements, which is where a
    reviewer reads it. Repeating the numbers here would create a second place
    for "200 metres" to live, and the two would disagree the first time somebody
    changed one of them.

    So this is the platform's *starting* posture: EXIF checked and mismatches
    queued, re-uploads caught, both §11.3 detectors on, §22.4's retention clocks
    running, and live-capture-only **off** — the one default that excludes
    citizens if it is wrong, which makes it a tenant's decision (§23) rather
    than a platform default.
    """
    return TrustThresholds()


#: Kind → baseline factory. Absent kinds have no baseline; see the module
#: docstring on why routing rules and rate cards are not here.
_BASELINES: Final[dict[PolicyKind, object]] = {
    PolicyKind.SEVERITY_RUBRIC: _severity_rubric,
    PolicyKind.DEDUP_THRESHOLDS: _dedup_thresholds,
    PolicyKind.SAFETY_RULESET: _safety_ruleset,
    PolicyKind.SLA_MATRIX: _sla_matrix,
    PolicyKind.TRUST_THRESHOLDS: _trust_thresholds,
}

#: The kinds a tenant is provisioned with. Ordered for a readable event log —
#: the tenant chain reads safety, then severity, then dedup, then SLA, which is
#: the order the pipeline consumes them in.
SEEDED_KINDS: Final[tuple[PolicyKind, ...]] = (
    PolicyKind.SAFETY_RULESET,
    # Second, immediately after safety: it is the other document the very first
    # complaint's pipeline reads, and the two are the tenant's §11 posture.
    PolicyKind.TRUST_THRESHOLDS,
    PolicyKind.SEVERITY_RUBRIC,
    PolicyKind.DEDUP_THRESHOLDS,
    PolicyKind.SLA_MATRIX,
)


def has_baseline(kind: PolicyKind) -> bool:
    return kind in _BASELINES


def baseline_body(kind: PolicyKind) -> PolicyBody:
    """The baseline document for a kind.

    Built fresh on each call rather than cached as a module constant. The bodies
    are frozen, so sharing one would be safe — but building it is microseconds,
    and a shared mutable-by-accident default is a class of bug that is very hard
    to see and very easy to introduce with one ``model_copy`` somewhere.
    """
    factory = _BASELINES.get(kind)
    if factory is None:
        raise KeyError(
            f"{kind.value} has no platform baseline: it names tenant-specific things "
            f"(departments, negotiated rates) that the platform cannot invent. Check "
            f"has_baseline() first."
        )
    return factory()  # type: ignore[operator, no-any-return]


__all__ = [
    "SEEDED_KINDS",
    "SEEDED_REASON",
    "baseline_body",
    "has_baseline",
]
