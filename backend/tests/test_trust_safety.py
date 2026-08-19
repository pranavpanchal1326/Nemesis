"""§11.2 — the deterministic safety fail-safe, through the real orchestrator.

The Phase 8 gate's first clause is *the safety bypass provably fires before any
scoring stage*. "Provably" is doing work there: it is not enough that the stage
is listed first, because a stage that ran first and let the pipeline continue
would look identical in the graph and be a completely different system. So the
tests here run the real orchestrator and assert on what was **enqueued next**.

The fourth clause — *a tenant with custom safety keywords gets correct behaviour
with no code change* — is the other half, and it is tested by activating a
policy document rather than by patching a constant. A test that reached into
``baselines`` would prove the resolver works and say nothing about whether an
operator can do it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from nemesis.db.models.trust import ReviewQueueItem
from nemesis.domain.lifecycle import ComplaintStatus
from nemesis.events.store import EventStore
from nemesis.observability.metrics import PipelineStage
from nemesis.pipeline.orchestrator import execute_stage
from nemesis.pipeline.stages import PIPELINE_SEQUENCE, SPECS, provider_scope
from nemesis.policy import service as policy_service
from nemesis.policy.documents import PolicyKind, SafetyRuleset
from nemesis.policy.resolver import RESOLVER
from nemesis.tenancy.context import tenant_scope
from nemesis.trust.safety import rules_with_unscored_visual_prompts, safety_stage
from nemesis.worker.celery_app import QUEUE_ML, QUEUE_SAFETY
from tests.conftest import postgres_required
from tests.test_trust_review import make_complaint

pytestmark = [postgres_required, pytest.mark.integration]

BASE = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def sessions(migrated_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(migrated_engine, expire_on_commit=False)


def campus_ruleset() -> dict[str, object]:
    """A ruleset naming hazards a municipal baseline has never heard of.

    The point of the fourth gate clause: a campus has no potholes and no open
    drains, it has liquid-nitrogen spills and stuck lifts. Nothing in source
    knows those words.
    """
    return {
        "rules": [
            {
                "rule_id": "cryogen_spill",
                "display_name": "Cryogenic spill",
                "rationale": "Liquid nitrogen displaces oxygen in a closed lab.",
                "terms": ["liquid nitrogen", "cryogen"],
                "match_mode": "substring",
                "severity_floor": 9.5,
            },
            {
                "rule_id": "lift_entrapment",
                "display_name": "Lift entrapment",
                "rationale": "A person is inside and cannot get out.",
                "terms": ["trapped in lift", "stuck in elevator"],
                "match_mode": "substring",
                "severity_floor": 9.0,
            },
        ]
    }


async def activate(session: AsyncSession, *, tenant_id: uuid.UUID, body: dict[str, object]) -> None:
    """Draft → review → approve → activate. The only path a document goes live by.

    Walked in full rather than shortcut, because the claim under test is that an
    *operator* can change safety behaviour without a deploy — and an operator
    only has this path.
    """
    version = await policy_service.draft(
        session,
        tenant_id=tenant_id,
        kind=PolicyKind.SAFETY_RULESET,
        body=body,
        change_reason="Campus hazards",
    )
    await policy_service.submit_for_review(
        session,
        tenant_id=tenant_id,
        kind=PolicyKind.SAFETY_RULESET,
        revision=version.revision,
        reason="Campus safety review",
    )
    await policy_service.approve(
        session,
        tenant_id=tenant_id,
        kind=PolicyKind.SAFETY_RULESET,
        revision=version.revision,
        reason="Approved by the campus safety officer",
    )
    await policy_service.activate(
        session,
        tenant_id=tenant_id,
        kind=PolicyKind.SAFETY_RULESET,
        revision=version.revision,
        reason="Go live",
    )
    await session.commit()
    RESOLVER.invalidate(tenant_id=tenant_id)


# ---------------------------------------------------------------------------
# Gate clause 1 — the bypass fires before any scoring stage
# ---------------------------------------------------------------------------


def test_the_safety_check_is_the_first_stage_in_the_graph() -> None:
    """Necessary, and on its own not sufficient — see the test below it."""
    assert PIPELINE_SEQUENCE[0] is PipelineStage.SAFETY_CHECK


def test_the_safety_stage_runs_on_its_own_queue_and_scoring_does_not() -> None:
    """The gate's third clause, as a structural fact rather than a benchmark.

    ``QUEUE_SAFETY`` is served by ``worker-io`` and ``QUEUE_ML`` by
    ``worker-ml`` — different containers, different processes, different memory
    caps. "A saturated ml queue cannot delay a danger signal" is therefore not a
    scheduling promise that a prefetch setting could break; the only way to
    break it is to move the stage, which is this line.
    """
    assert SPECS[PipelineStage.SAFETY_CHECK.value].queue == QUEUE_SAFETY
    assert SPECS[PipelineStage.TRUST_VERIFICATION.value].queue == QUEUE_ML
    assert SPECS[PipelineStage.CLASSIFICATION.value].queue == QUEUE_ML
    # And the safety stage has the largest retry budget in the graph: a missed
    # danger signal is the worst outcome the system can produce.
    assert SPECS[PipelineStage.SAFETY_CHECK.value].max_attempts == max(
        spec.max_attempts for spec in SPECS.values()
    )


async def test_a_triggered_report_never_reaches_a_scoring_stage(
    bound_session: None, sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> None:
    """The gate clause, proven by what the orchestrator says runs next.

    §11.2 says a triggered report *bypasses the scoring pipeline entirely*, and
    "bypasses" has to mean the successor stages are never enqueued — not that
    they run and decline to act, which would be four no-ops holding queue slots
    behind a gas leak.
    """
    with tenant_scope(tenant_id):
        async with sessions() as session:
            await policy_service.seed_baselines(session, tenant_id=tenant_id)
            complaint_id = await make_complaint(
                session,
                tenant_id=tenant_id,
                description_text="there is a gas leak on the corner, smells terrible",
            )
            await session.commit()

    with provider_scope(PipelineStage.SAFETY_CHECK, safety_stage):
        execution = await execute_stage(
            tenant_id=tenant_id,
            complaint_id=complaint_id,
            stage=PipelineStage.SAFETY_CHECK.value,
        )

    assert execution.halted
    # The load-bearing assertion. `next_stage` is what `pipeline.tasks` enqueues.
    assert execution.next_stage is None
    assert execution.halt_reason is not None and "11.2" in execution.halt_reason


async def test_a_clean_report_continues_to_the_trust_stage(
    bound_session: None, sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> None:
    """A fail-safe that halted everything would also pass the test above."""
    with tenant_scope(tenant_id):
        async with sessions() as session:
            await policy_service.seed_baselines(session, tenant_id=tenant_id)
            complaint_id = await make_complaint(
                session, tenant_id=tenant_id, description_text="the streetlight is out"
            )
            await session.commit()

    with provider_scope(PipelineStage.SAFETY_CHECK, safety_stage):
        execution = await execute_stage(
            tenant_id=tenant_id,
            complaint_id=complaint_id,
            stage=PipelineStage.SAFETY_CHECK.value,
        )

    assert not execution.halted
    assert execution.next_stage == PipelineStage.TRUST_VERIFICATION.value
    # No event, deliberately: a safety check that recorded "nothing dangerous"
    # on every submission would double the log's size to record the absence of
    # a rare thing.
    assert execution.events_appended == 0


async def test_a_trigger_records_the_rule_the_terms_and_the_version(
    bound_session: None, sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> None:
    """§6.1, prove don't log: a bypassed queue must say what bypassed it.

    A citizen or an operator can see exactly which words did it, and the
    ruleset version means the decision stays traceable after the ruleset
    changes.
    """
    with tenant_scope(tenant_id):
        async with sessions() as session:
            await policy_service.seed_baselines(session, tenant_id=tenant_id)
            complaint_id = await make_complaint(
                session, tenant_id=tenant_id, description_text="live wire hanging over the road"
            )
            await session.commit()

    with provider_scope(PipelineStage.SAFETY_CHECK, safety_stage):
        await execute_stage(
            tenant_id=tenant_id,
            complaint_id=complaint_id,
            stage=PipelineStage.SAFETY_CHECK.value,
        )

    with tenant_scope(tenant_id):
        async with sessions() as session:
            events = await EventStore(session).read_stream(
                entity_type="complaint", entity_id=complaint_id
            )
            item = (
                await session.execute(
                    select(ReviewQueueItem).where(
                        ReviewQueueItem.tenant_id == tenant_id,
                        ReviewQueueItem.complaint_id == complaint_id,
                    )
                )
            ).scalar_one()

    types = [event.event_type for event in events]
    assert types == ["complaint_submitted", "safety_trigger_fired", "review_queued"]
    payload = events[1].payload
    assert payload["rule_id"]
    assert payload["matched_terms"] == ["live wire"]
    assert payload["detection_source"] == "keyword"
    assert payload["ruleset_version"]

    # §11.4: the flag reached a destination, with the evidence attached.
    assert item.reason == "safety_trigger"
    assert item.evidence["matched_terms"] == ["live wire"]
    assert item.priority == 0


async def test_the_projection_shows_the_report_as_flagged_and_out_of_the_work_list(
    bound_session: None, sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> None:
    """A projection showing the flag while the status still read ``submitted``
    would put the report back into the normal work list."""
    from nemesis.db.models.complaint import Complaint

    with tenant_scope(tenant_id):
        async with sessions() as session:
            await policy_service.seed_baselines(session, tenant_id=tenant_id)
            complaint_id = await make_complaint(
                session, tenant_id=tenant_id, description_text="building collapsed on the lane"
            )
            await session.commit()

    with provider_scope(PipelineStage.SAFETY_CHECK, safety_stage):
        await execute_stage(
            tenant_id=tenant_id,
            complaint_id=complaint_id,
            stage=PipelineStage.SAFETY_CHECK.value,
        )

    with tenant_scope(tenant_id):
        async with sessions() as session:
            row = (
                await session.execute(
                    select(Complaint.status, Complaint.is_safety_flagged).where(
                        Complaint.tenant_id == tenant_id, Complaint.id == complaint_id
                    )
                )
            ).one()
    assert row == (ComplaintStatus.FLAGGED.value, True)


# ---------------------------------------------------------------------------
# Gate clause 4 — custom keywords, no code change
# ---------------------------------------------------------------------------


async def test_a_tenant_specific_hazard_fires_with_no_code_change(
    bound_session: None, sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> None:
    """Nothing in this repository contains the phrase "liquid nitrogen".

    The rule reaches the deterministic evaluator through the same approved
    document path an operator uses, which is the whole of critique-log defect #1
    ("safety keywords in source") reversed.
    """
    from nemesis.policy import baselines

    # The claim, checked rather than asserted in prose: the platform baseline
    # this tenant was provisioned with has never heard of a cryogen.
    baseline = baselines.baseline_body(PolicyKind.SAFETY_RULESET)
    assert isinstance(baseline, SafetyRuleset)
    assert not any("nitrogen" in term.casefold() for rule in baseline.rules for term in rule.terms)

    with tenant_scope(tenant_id):
        async with sessions() as session:
            await policy_service.seed_baselines(session, tenant_id=tenant_id)
            await activate(session, tenant_id=tenant_id, body=campus_ruleset())
            complaint_id = await make_complaint(
                session,
                tenant_id=tenant_id,
                description_text="liquid nitrogen spilled in lab 3, room is fogging up",
            )
            await session.commit()

    with provider_scope(PipelineStage.SAFETY_CHECK, safety_stage):
        execution = await execute_stage(
            tenant_id=tenant_id,
            complaint_id=complaint_id,
            stage=PipelineStage.SAFETY_CHECK.value,
        )

    assert execution.halted
    assert execution.next_stage is None

    with tenant_scope(tenant_id):
        async with sessions() as session:
            events = await EventStore(session).read_stream(
                entity_type="complaint", entity_id=complaint_id
            )
    assert events[1].payload["rule_id"] == "cryogen_spill"


async def test_the_same_report_is_clean_under_the_platform_baseline(
    bound_session: None, sessions: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> None:
    """The control for the test above.

    Without it, "the campus rule fired" is consistent with a baseline that
    happens to contain the word — and the claim being made is that the *tenant's
    document* is what decided.
    """
    with tenant_scope(tenant_id):
        async with sessions() as session:
            await policy_service.seed_baselines(session, tenant_id=tenant_id)
            complaint_id = await make_complaint(
                session,
                tenant_id=tenant_id,
                description_text="liquid nitrogen spilled in lab 3, room is fogging up",
            )
            await session.commit()

    with provider_scope(PipelineStage.SAFETY_CHECK, safety_stage):
        execution = await execute_stage(
            tenant_id=tenant_id,
            complaint_id=complaint_id,
            stage=PipelineStage.SAFETY_CHECK.value,
        )
    assert not execution.halted


async def test_one_tenants_ruleset_does_not_govern_another(
    bound_session: None,
    sessions: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
    other_tenant_id: uuid.UUID,
) -> None:
    """Multi-tenant from row zero, at the stage with the strongest obligation."""
    with tenant_scope(tenant_id):
        async with sessions() as session:
            await policy_service.seed_baselines(session, tenant_id=tenant_id)
            await activate(session, tenant_id=tenant_id, body=campus_ruleset())
            await session.commit()

    with tenant_scope(other_tenant_id):
        async with sessions() as session:
            await policy_service.seed_baselines(session, tenant_id=other_tenant_id)
            complaint_id = await make_complaint(
                session,
                tenant_id=other_tenant_id,
                description_text="liquid nitrogen everywhere",
            )
            await session.commit()

    with provider_scope(PipelineStage.SAFETY_CHECK, safety_stage):
        execution = await execute_stage(
            tenant_id=other_tenant_id,
            complaint_id=complaint_id,
            stage=PipelineStage.SAFETY_CHECK.value,
        )
    assert not execution.halted


# ---------------------------------------------------------------------------
# The visual half, which is Phase 9's and is not pretended otherwise
# ---------------------------------------------------------------------------


def test_rules_with_visual_prompts_are_reported_as_partially_inert() -> None:
    """The shortfall is named, not hidden.

    An approver reading a rule with three CLIP prompts reasonably believes the
    system is watching for them. It is not until Phase 9 — and the honest
    treatment is the one Phase 7 gives routing rules that read a fact the corpus
    cannot supply: name them, rather than report an absence of findings.
    """
    ruleset = SafetyRuleset.model_validate(
        {
            "rules": [
                {
                    "rule_id": "sees_and_reads",
                    "display_name": "Structural collapse",
                    "rationale": "Both halves approved together as one definition.",
                    "terms": ["collapsed"],
                    "visual_prompts": ["structural collapse"],
                },
                {
                    "rule_id": "reads_only",
                    "display_name": "Gas leak",
                    "rationale": "Keywords are enough for this one.",
                    "terms": ["gas leak"],
                },
                {
                    "rule_id": "switched_off",
                    "display_name": "Retired rule",
                    "rationale": "Inactive, so not a live shortfall.",
                    "terms": ["obsolete"],
                    "visual_prompts": ["something"],
                    "is_active": False,
                },
            ]
        }
    )
    assert rules_with_unscored_visual_prompts(ruleset) == ("sees_and_reads",)


def test_no_rule_can_be_wholly_visual_and_therefore_wholly_inert() -> None:
    """``SafetyRule.terms`` requires at least one keyword, deliberately.

    A rule with only prompts would be a rule that cannot fire at all in this
    build — approved, listed, and silent — which is the failure mode the helper
    above exists to make visible and this constraint exists to make impossible.
    """
    with pytest.raises(ValueError, match="terms"):
        SafetyRuleset.model_validate(
            {
                "rules": [
                    {
                        "rule_id": "visual_only",
                        "display_name": "Seen, never read",
                        "rationale": "No words at all.",
                        "terms": [],
                        "visual_prompts": ["structural collapse"],
                    }
                ]
            }
        )
