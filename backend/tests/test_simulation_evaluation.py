"""Labelled sets, certificates, and the clause that says *cannot*.

Phase 7's second gate clause is **a policy that regresses the labelled
evaluation set cannot be activated**, and this file is where "cannot" is
tested — at the service layer, through ``policy.service.activate``, which is the
single mutation path every caller goes through.

The interesting tests are the ones that try to get *past* the guardrail rather
than the ones that confirm it works in the happy case. Four routes are closed
here, and each one would have been a real hole:

- Activating with no certificate at all.
- Activating with a *failing* certificate.
- Activating with a certificate issued against a different body.
- Editing the labels after a certificate was issued against them.

Plus the one route that is deliberately open — rollback — and the event that
makes it visible.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from nemesis.db.models.event import Event
from nemesis.db.models.simulation import PolicyCertificate
from nemesis.policy import service as policy_service
from nemesis.policy.documents import PolicyKind
from nemesis.policy.errors import PolicyCertificationError
from nemesis.simulation import evaluation
from nemesis.simulation.errors import (
    SimulationConflictError,
    SimulationNotFoundError,
    SimulationValidationError,
)
from nemesis.tenancy.context import tenant_scope
from tests.conftest import postgres_required
from tests.test_simulation_corpus import BASE, seed_complaint

pytestmark = [postgres_required, pytest.mark.integration]

KIND = PolicyKind.SEVERITY_RUBRIC


@asynccontextmanager
async def scoped(engine: AsyncEngine, tenant_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    with tenant_scope(tenant_id):
        async with maker() as session:
            yield session
            await session.commit()


def rubric(*, visual: float) -> dict[str, object]:
    return {
        "components": [
            {
                "key": "visual_damage",
                "display_name": "Visual",
                "weight": visual,
                "description": "x",
            },
            {
                "key": "road_class",
                "display_name": "Road",
                "weight": round(1.0 - visual, 6),
                "description": "x",
            },
        ]
    }


async def draft_and_approve(
    session: AsyncSession, *, tenant_id: uuid.UUID, body: dict[str, object]
) -> int:
    version = await policy_service.draft(
        session, tenant_id=tenant_id, kind=KIND, body=body, change_reason="under test"
    )
    for verb in (policy_service.submit_for_review, policy_service.approve):
        await verb(
            session, tenant_id=tenant_id, kind=KIND, revision=version.revision, reason="under test"
        )
    return int(version.revision)


async def published_set_with_label(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    complaint_id: uuid.UUID,
    expected_tier: str = "medium",
    pass_ratio: float = 1.0,
    code: str = "monsoon-review",
) -> None:
    await evaluation.create_set(
        session,
        tenant_id=tenant_id,
        code=code,
        name="Monsoon review",
        kind=KIND,
        description="The complaints the 2026 monsoon review said we scored wrong",
        pass_ratio=pass_ratio,
    )
    await evaluation.add_label(
        session,
        tenant_id=tenant_id,
        code=code,
        complaint_id=complaint_id,
        rationale="Reviewed by the ward engineer; this is a medium, not an urgent",
        expected_severity_tier=expected_tier,
    )
    await evaluation.publish_set(session, tenant_id=tenant_id, code=code)


# ---------------------------------------------------------------------------
# The set's own lifecycle
# ---------------------------------------------------------------------------


async def test_a_set_with_no_labels_cannot_be_published(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """An exam with no questions is one every candidate passes.

    Publishing it would turn the guardrail on and make it vacuous at the same
    time — the worst combination, because the screen says "gated".
    """
    async with scoped(migrated_engine, tenant_id) as session:
        await evaluation.create_set(
            session,
            tenant_id=tenant_id,
            code="empty",
            name="Empty",
            kind=KIND,
            description="nothing in it",
        )
        with pytest.raises(SimulationValidationError):
            await evaluation.publish_set(session, tenant_id=tenant_id, code="empty")


async def test_labels_freeze_at_publication(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A guardrail whose questions can be edited can be made to pass by editing the exam."""
    async with scoped(migrated_engine, tenant_id) as session:
        await policy_service.seed_baselines(session, tenant_id=tenant_id)
        complaint_id = await seed_complaint(session, tenant_id=tenant_id, at=BASE)
        await published_set_with_label(session, tenant_id=tenant_id, complaint_id=complaint_id)

        with pytest.raises(SimulationConflictError) as excinfo:
            await evaluation.add_label(
                session,
                tenant_id=tenant_id,
                code="monsoon-review",
                complaint_id=uuid.uuid4(),
                rationale="sneaking one in",
                expected_severity_tier="low",
            )

    assert "frozen" in str(excinfo.value)


async def test_a_label_must_assert_something(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A label with no expectation passes every candidate.

    A set of them reads as a guardrail and behaves as an endorsement.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        await evaluation.create_set(
            session,
            tenant_id=tenant_id,
            code="vague",
            name="Vague",
            kind=KIND,
            description="a set whose labels claim nothing",
        )
        with pytest.raises(SimulationValidationError):
            await evaluation.add_label(
                session,
                tenant_id=tenant_id,
                code="vague",
                complaint_id=uuid.uuid4(),
                rationale="no expectation given",
            )


async def test_a_pass_ratio_below_the_floor_is_refused(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A threshold of 0.2 does not fail a change, it endorses one."""
    async with scoped(migrated_engine, tenant_id) as session:
        with pytest.raises(SimulationValidationError):
            await evaluation.create_set(
                session,
                tenant_id=tenant_id,
                code="lax",
                name="Lax",
                kind=KIND,
                description="barely a control",
                pass_ratio=0.2,
            )


async def test_publishing_retires_the_incumbent_set_for_the_kind(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """One kind, one exam. Two would make "which gates this" a question about row order."""
    async with scoped(migrated_engine, tenant_id) as session:
        await policy_service.seed_baselines(session, tenant_id=tenant_id)
        complaint_id = await seed_complaint(session, tenant_id=tenant_id, at=BASE)
        await published_set_with_label(
            session, tenant_id=tenant_id, complaint_id=complaint_id, code="first"
        )
        await published_set_with_label(
            session, tenant_id=tenant_id, complaint_id=complaint_id, code="second"
        )

        first = await evaluation.require_set(session, tenant_id=tenant_id, code="first")
        gating = await evaluation.published_set(session, tenant_id=tenant_id, kind=KIND)

    assert first.status == "retired"
    assert gating is not None
    assert gating.code == "second"


async def test_publishing_and_retiring_are_both_on_the_tenant_chain(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Switching a control off is at least as consequential as switching it on.

    A chain that recorded the second and not the first would let the interesting
    half of an incident happen off the record.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        await policy_service.seed_baselines(session, tenant_id=tenant_id)
        complaint_id = await seed_complaint(session, tenant_id=tenant_id, at=BASE)
        await published_set_with_label(session, tenant_id=tenant_id, complaint_id=complaint_id)
        await evaluation.retire_set(session, tenant_id=tenant_id, code="monsoon-review")

        rows = await session.execute(
            select(Event.event_type).where(
                Event.tenant_id == tenant_id,
                Event.event_type.in_(["evaluation_set_published", "evaluation_set_retired"]),
            )
        )
        recorded = sorted(row[0] for row in rows.all())

    assert recorded == ["evaluation_set_published", "evaluation_set_retired"]


# ---------------------------------------------------------------------------
# The guardrail
# ---------------------------------------------------------------------------


async def test_a_kind_with_no_published_set_activates_exactly_as_before(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Publication is the switch. Nothing is gated until somebody does the work."""
    async with scoped(migrated_engine, tenant_id) as session:
        await policy_service.seed_baselines(session, tenant_id=tenant_id)
        revision = await draft_and_approve(session, tenant_id=tenant_id, body=rubric(visual=0.9))
        activated = await policy_service.activate(
            session, tenant_id=tenant_id, kind=KIND, revision=revision, reason="ungated"
        )

    assert activated.status == "active"


async def test_an_uncertified_candidate_cannot_be_activated(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The gate clause, at the single mutation path.

    The refusal names the evaluation set and tells the caller to run one, which
    is the only action that can succeed — a message saying "forbidden" would
    send them looking for a bigger token.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        await policy_service.seed_baselines(session, tenant_id=tenant_id)
        complaint_id = await seed_complaint(session, tenant_id=tenant_id, at=BASE)
        await published_set_with_label(session, tenant_id=tenant_id, complaint_id=complaint_id)

        revision = await draft_and_approve(session, tenant_id=tenant_id, body=rubric(visual=0.9))
        with pytest.raises(PolicyCertificationError) as excinfo:
            await policy_service.activate(
                session, tenant_id=tenant_id, kind=KIND, revision=revision, reason="unchecked"
            )

    assert "monsoon-review" in str(excinfo.value)
    assert "evaluation" in str(excinfo.value)


async def test_a_failing_certificate_does_not_unlock_activation(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A certificate is evidence of a verdict, not permission to proceed.

    The activation query filters on ``verdict = 'pass'``; a check that only
    looked for a certificate's *existence* would let every failed evaluation
    open the gate it was run to close.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        await policy_service.seed_baselines(session, tenant_id=tenant_id)
        complaint_id = await seed_complaint(session, tenant_id=tenant_id, at=BASE, score=6.5)
        # The label demands a tier the extreme rubric cannot produce.
        await published_set_with_label(
            session, tenant_id=tenant_id, complaint_id=complaint_id, expected_tier="never-a-tier"
        )

        revision = await draft_and_approve(session, tenant_id=tenant_id, body=rubric(visual=0.9))
        certificate = await evaluation.evaluate_candidate(
            session, tenant_id=tenant_id, kind=KIND, revision=revision
        )
        assert certificate.verdict == "fail"

        with pytest.raises(PolicyCertificationError):
            await policy_service.activate(
                session, tenant_id=tenant_id, kind=KIND, revision=revision, reason="but it failed"
            )


async def test_a_passing_certificate_unlocks_exactly_the_body_it_marked(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Keyed by content hash, not by revision number.

    A certificate for revision 2 must not vouch for revision 3, even when both
    were drafted by the same person in the same minute.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        await policy_service.seed_baselines(session, tenant_id=tenant_id)
        complaint_id = await seed_complaint(session, tenant_id=tenant_id, at=BASE)
        await published_set_with_label(
            session, tenant_id=tenant_id, complaint_id=complaint_id, expected_tier="medium"
        )

        certified = await draft_and_approve(session, tenant_id=tenant_id, body=rubric(visual=0.5))
        other = await draft_and_approve(session, tenant_id=tenant_id, body=rubric(visual=0.7))

        result = await evaluation.evaluate_candidate(
            session, tenant_id=tenant_id, kind=KIND, revision=certified
        )
        assert result.verdict == "pass", result.findings

        with pytest.raises(PolicyCertificationError):
            await policy_service.activate(
                session,
                tenant_id=tenant_id,
                kind=KIND,
                revision=other,
                reason="borrowing the other one's certificate",
            )

        activated = await policy_service.activate(
            session, tenant_id=tenant_id, kind=KIND, revision=certified, reason="checked"
        )

    assert activated.status == "active"


async def test_a_certificate_is_recorded_on_the_chain_whether_it_passes_or_fails(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """ "We evaluated this and it failed" is the record that matters a week later.

    A chain holding only passes makes a history of refusals indistinguishable
    from a history of nobody looking.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        await policy_service.seed_baselines(session, tenant_id=tenant_id)
        complaint_id = await seed_complaint(session, tenant_id=tenant_id, at=BASE)
        await published_set_with_label(
            session, tenant_id=tenant_id, complaint_id=complaint_id, expected_tier="never-a-tier"
        )
        revision = await draft_and_approve(session, tenant_id=tenant_id, body=rubric(visual=0.9))
        await evaluation.evaluate_candidate(
            session, tenant_id=tenant_id, kind=KIND, revision=revision
        )

        rows = await session.execute(
            select(Event.payload).where(
                Event.tenant_id == tenant_id, Event.event_type == "policy_certified"
            )
        )
        payloads = [row[0] for row in rows.all()]

    assert len(payloads) == 1
    assert payloads[0]["verdict"] == "fail"
    assert payloads[0]["labels_evaluated"] == 1


async def test_rollback_activates_under_a_recorded_waiver(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The one open route, and the event that keeps it visible.

    A rollback restores bytes that were live and therefore already passed
    whatever gate existed. Requiring a fresh twelve-month evaluation at 3am
    would make the emergency path depend on a batch job, so the waiver exists —
    and writes ``policy_certification_waived``, so "which activations skipped
    the set" stays a query rather than an inference.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        await policy_service.seed_baselines(session, tenant_id=tenant_id)
        complaint_id = await seed_complaint(session, tenant_id=tenant_id, at=BASE)
        await published_set_with_label(session, tenant_id=tenant_id, complaint_id=complaint_id)

        restored = await policy_service.rollback(
            session, tenant_id=tenant_id, kind=KIND, to_revision=1, reason="incident 42"
        )

        rows = await session.execute(
            select(Event.payload).where(
                Event.tenant_id == tenant_id,
                Event.event_type == "policy_certification_waived",
            )
        )
        payloads = [row[0] for row in rows.all()]

    assert restored.status == "active"
    assert len(payloads) == 1
    assert payloads[0]["evaluation_set_code"] == "monsoon-review"
    assert "previously live" in payloads[0]["waiver"]


async def test_a_label_naming_an_unreconstructable_complaint_is_not_a_failure(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Retention is not the candidate's fault.

    Counting an archived complaint as a failed label would make every set expire
    silently as its partitions aged out — a guardrail with a hidden clock.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        await policy_service.seed_baselines(session, tenant_id=tenant_id)
        complaint_id = await seed_complaint(session, tenant_id=tenant_id, at=BASE)
        await evaluation.create_set(
            session,
            tenant_id=tenant_id,
            code="mixed",
            name="Mixed",
            kind=KIND,
            description="one real complaint and one that no longer exists",
        )
        for identifier, tier in ((complaint_id, "medium"), (uuid.uuid4(), "medium")):
            await evaluation.add_label(
                session,
                tenant_id=tenant_id,
                code="mixed",
                complaint_id=identifier,
                rationale="reviewed",
                expected_severity_tier=tier,
            )
        await evaluation.publish_set(session, tenant_id=tenant_id, code="mixed")

        revision = await draft_and_approve(session, tenant_id=tenant_id, body=rubric(visual=0.5))
        certificate = await evaluation.evaluate_candidate(
            session, tenant_id=tenant_id, kind=KIND, revision=revision
        )

    assert certificate.labels_unresolvable == 1
    assert certificate.labels_evaluated == 1
    assert certificate.verdict == "pass"


async def test_certifying_without_a_published_set_is_refused(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """There is nothing to certify against, and saying so beats issuing a vacuous pass."""
    async with scoped(migrated_engine, tenant_id) as session:
        await policy_service.seed_baselines(session, tenant_id=tenant_id)
        revision = await draft_and_approve(session, tenant_id=tenant_id, body=rubric(visual=0.5))
        with pytest.raises(SimulationNotFoundError):
            await evaluation.evaluate_candidate(
                session, tenant_id=tenant_id, kind=KIND, revision=revision
            )


async def test_a_certificate_never_crosses_a_tenant_boundary(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID, other_tenant_id: uuid.UUID
) -> None:
    """One tenant's evidence cannot unlock another's activation."""
    async with scoped(migrated_engine, tenant_id) as session:
        await policy_service.seed_baselines(session, tenant_id=tenant_id)
        complaint_id = await seed_complaint(session, tenant_id=tenant_id, at=BASE)
        await published_set_with_label(session, tenant_id=tenant_id, complaint_id=complaint_id)
        revision = await draft_and_approve(session, tenant_id=tenant_id, body=rubric(visual=0.5))
        await evaluation.evaluate_candidate(
            session, tenant_id=tenant_id, kind=KIND, revision=revision
        )

    async with scoped(migrated_engine, other_tenant_id) as session:
        rows = await session.execute(
            select(PolicyCertificate).where(PolicyCertificate.tenant_id == other_tenant_id)
        )
        assert rows.scalars().all() == []


def test_the_labels_hash_does_not_depend_on_insertion_order() -> None:
    """Two databases holding the same judgements must produce the same digest."""
    from nemesis.db.models.simulation import EvaluationLabel

    first = uuid.UUID(int=1)
    second = uuid.UUID(int=2)

    def label(complaint_id: uuid.UUID, tier: str) -> EvaluationLabel:
        return EvaluationLabel(
            tenant_id=uuid.UUID(int=9),
            evaluation_set_id=uuid.UUID(int=8),
            complaint_id=complaint_id,
            expected_severity_tier=tier,
            rationale="x",
        )

    forwards = [label(first, "low"), label(second, "high")]
    backwards = [label(second, "high"), label(first, "low")]
    assert evaluation.labels_hash(forwards) == evaluation.labels_hash(backwards)


def test_an_expectation_the_label_does_not_set_is_not_checked() -> None:
    """A label asserting a tier says nothing about routing.

    Treating unset as "expects null" would fail every candidate for not matching
    a judgement nobody made.
    """
    from nemesis.db.models.simulation import EvaluationLabel
    from nemesis.simulation.engine import decide
    from tests.test_simulation_engine import bundle, case

    outcome = decide(bundle(), case())
    label = EvaluationLabel(
        tenant_id=uuid.uuid4(),
        evaluation_set_id=uuid.uuid4(),
        complaint_id=outcome.complaint_id,
        expected_safety_fired=False,
        rationale="only asserts that the danger path stays quiet",
    )
    finding = evaluation.check_label(label, outcome)

    assert finding.passed is True
    assert finding.expectations == ("safety_fired",)


def test_a_certificate_carries_no_optimistic_version_column() -> None:
    """Re-evaluation writes a new row rather than updating one.

    "This failed twice and then passed" is exactly the pattern an incident
    review wants to see, and an UPDATE path would erase the first two. The
    absence of the column is what makes that structural rather than a habit.
    """
    assert "version" not in PolicyCertificate.__table__.c


def test_the_policy_package_never_imports_the_simulation_package() -> None:
    """The dependency runs one way: simulation knows policy, policy knows a table.

    Checked against the parsed import statements rather than against the text of
    the file — the modules *discuss* each other at length in their docstrings,
    and a grep would either fail on the prose or be loosened until it stopped
    catching a real import.

    This is the property the guardrail's reliability rests on. An
    ``activate`` that called into a checker would fail *open* the day that
    wiring changed, and failing open is indistinguishable from having no
    guardrail at all.
    """
    import ast
    import pathlib

    import nemesis.policy as policy_package

    root = pathlib.Path(policy_package.__file__).parent
    offenders: list[str] = []
    for path in sorted(root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "nemesis.simulation"
            ):
                offenders.append(f"{path.name}:{node.lineno}")
            if isinstance(node, ast.Import):
                offenders.extend(
                    f"{path.name}:{node.lineno}"
                    for alias in node.names
                    if alias.name.startswith("nemesis.simulation")
                )

    assert offenders == [], f"policy imports simulation at {', '.join(offenders)}"
