"""§11.2 — the deterministic safety fail-safe, as a pipeline stage.

This is the highest-credibility-per-hour item in the system (§11) and the
shortest module in this package, which is not a coincidence: §11.2's entire
argument is that danger detection should be *less* clever than everything around
it. False negatives on danger signals are unacceptable, so the check is a hard
rule with no probability in it — it fires or it does not, on the same input,
every time.

**What "deterministic does not mean hardcoded" buys, concretely.** The rules are
a Phase 6 policy document, so a tenant adds "elevator entrapment" or "ammonia"
without a deploy — but ``policy.resolver.evaluate_safety`` still executes them
as a hard rule, in document order, first match wins, no regular expressions, and
linear in the length of submitter-controlled text. Architectural principle 2 in
one function call.

**Why this stage runs on ``QUEUE_SAFETY`` and what that actually guarantees.**
The safety queue is served by ``worker-io``, an image that has never imported
torch. The ``ml`` queue is served by ``worker-ml``, a different container with a
different process and a different memory cap. So "a saturated ml queue cannot
delay a danger signal" is not a scheduling promise that could be broken by a
prefetch setting — it is two operating-system processes, and the only way to
break it is to route the stage elsewhere, which is one line in ``stages.py`` and
is asserted by a test.

**Why the halt is a halt and not a flag.** §11.2 says a triggered report
*bypasses the entire scoring pipeline*. ``StageResult.halt`` stops the graph
before classification is enqueued — the successor stages are never dispatched,
rather than dispatched and declining to act, which would be four no-ops holding
queue slots behind a gas leak.

**The visual half is not here, and is not silently dropped.** §11.2 names a CLIP
zero-shot trigger prompt set alongside the keywords, and ``SafetyRule`` already
carries ``visual_prompts`` so both halves are approved together as one danger
definition. Scoring them needs the perception layer, which is Phase 9. Until
then ``visual_matches`` is empty, Phase 7's ``UNAVAILABLE_FACTS`` already names
the visual half as a coverage gap, and ``rules_with_unscored_visual_prompts``
below names every rule whose visual half is inert — so the shortfall is
reportable rather than silent.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from nemesis.domain.lifecycle import EntityType
from nemesis.observability import metrics
from nemesis.observability.logging import get_logger
from nemesis.pipeline.stages import EmittedEvent, StageContext, StageResult
from nemesis.policy.documents import SafetyRuleset
from nemesis.policy.resolver import RESOLVER, evaluate_safety
from nemesis.trust.review import ReviewReason, queue

log = get_logger(__name__)

COMPLAINT = EntityType.COMPLAINT


def rules_with_unscored_visual_prompts(ruleset: SafetyRuleset) -> tuple[str, ...]:
    """Active rules whose visual half nothing in this build can score.

    §11.2 names a CLIP zero-shot trigger prompt set alongside the keywords, and
    ``SafetyRule`` carries both so they are approved together as one danger
    definition. Only the keyword half executes today; the perception layer that
    scores prompts is Phase 9.

    That is worth *reporting* rather than leaving implicit, and the reason is
    the one Phase 7 gives about routing rules reading facts the corpus cannot
    supply: an approver looking at a rule with three visual prompts reasonably
    believes the system is watching for them. It is not, yet. Naming the rules
    turns a silent partial-inertness into something an operator can be told at
    activation time instead of discovering after the incident the rule was
    written to catch.

    Note that a rule can never be *entirely* visual — ``SafetyRule.terms``
    requires at least one keyword, deliberately, so no rule is wholly inert.
    """
    return tuple(rule.rule_id for rule in ruleset.rules if rule.is_active and rule.visual_prompts)


async def safety_stage(ctx: StageContext) -> StageResult:
    """Run the tenant's approved ruleset against the report's text.

    The text is the description plus the transcript when one exists — and today
    one never does, because transcription is Phase 9 and runs *after* this
    stage. That ordering is deliberate and it is a real limitation: a voice-only
    report of a gas leak reaches the safety check with nothing to match. It is
    recorded in the phase notes rather than papered over with a re-run, because
    the honest fix is a second safety pass after transcription, and a second
    pass needs Phase 9's output to exist before it can be tested.
    """
    resolved = await RESOLVER.safety_ruleset(ctx.session, tenant_id=ctx.tenant_id)
    ruleset: SafetyRuleset = resolved.body

    text = _text_of(ctx.state)
    decision = evaluate_safety(
        ruleset,
        text=text,
        locale=_optional_str(ctx.state.get("locale")),
        # Empty until Phase 9. Passed explicitly rather than defaulted so the
        # absence is visible at the call site instead of hidden in a signature.
        visual_matches=(),
    )

    if not decision.fired:
        # No event. A safety check that emitted "nothing dangerous" on every
        # submission would double the size of the log to record the absence of a
        # rare thing — and the orchestrator's redelivery guard keys on the first
        # append, so a stage that emits nothing is idempotent by having changed
        # nothing rather than by being guarded.
        return StageResult()

    assert decision.rule_id is not None  # `fired` implies a rule matched
    metrics.safety_triggers_total.labels(
        rule_id=decision.rule_id, detection_source=decision.detection_source
    ).inc()

    evidence = {
        "rule_id": decision.rule_id,
        "ruleset_version": resolved.stamp,
        "matched_terms": list(decision.matched_terms),
        "detection_source": decision.detection_source,
        "severity_floor": decision.severity_floor,
        "text_examined": text[:500] if text else None,
    }
    queued = await queue(
        ctx.session,
        tenant_id=ctx.tenant_id,
        complaint_id=ctx.complaint_id,
        reason=ReviewReason.SAFETY_TRIGGER,
        evidence=evidence,
        trust_score=float(ctx.state.get("trust_score", 0.0)),
    )

    log.warning(
        "safety_trigger_fired",
        complaint_id=str(ctx.complaint_id),
        rule_id=decision.rule_id,
        ruleset_version=resolved.stamp,
        detection_source=decision.detection_source,
        matched_terms=list(decision.matched_terms),
        runbook="docs/runbooks/safety-trigger-fired.md",
    )

    return StageResult(
        emitted=(
            EmittedEvent(
                entity_type=COMPLAINT,
                entity_id=ctx.complaint_id,
                event_type="safety_trigger_fired",
                payload={
                    "rule_id": decision.rule_id,
                    "ruleset_version": resolved.stamp,
                    "matched_terms": list(decision.matched_terms),
                    "detection_source": decision.detection_source,
                },
            ),
            EmittedEvent(
                entity_type=COMPLAINT,
                entity_id=ctx.complaint_id,
                event_type="review_queued",
                payload={
                    "review_item_id": str(queued.review_item_id),
                    "reason": queued.reason.value,
                    "priority": queued.priority,
                    "occurrences": queued.occurrences,
                    "trust_score": queued.trust_score,
                    "evidence_hash": queued.evidence_hash,
                },
            ),
        ),
        halt=True,
        halt_reason=(
            f"§11.2 safety rule {decision.rule_id!r} fired on "
            f"{', '.join(decision.matched_terms) or 'a visual match'}; the scoring "
            f"pipeline is bypassed entirely and a human is the next step"
        ),
    )


def _text_of(state: Any) -> str | None:
    """Description and transcript, joined. ``None`` when there is neither.

    ``None`` rather than an empty string: ``SafetyRuleset.on_indeterminate``
    exists because "no text to match" is a distinct state from "text that
    matched nothing", and collapsing them here would make the document's stated
    failure posture unreachable.
    """
    parts: Sequence[str] = tuple(
        value
        for key in ("description_text", "transcript")
        if isinstance(value := state.get(key), str) and value.strip()
    )
    return "\n".join(parts) if parts else None


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


__all__ = ["rules_with_unscored_visual_prompts", "safety_stage"]
