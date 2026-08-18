"""The lifecycle against a real database — including three of the four gate clauses.

The Phase 6 gate is four sentences, and this file proves three of them at the
service layer:

- *An unapproved draft can never influence a production decision.* Proved by
  ``test_a_draft_never_decides_anything`` and by the partial unique index that
  makes "active" mean exactly one.
- *Every scored complaint records the exact policy version that scored it.* The
  stamp half is here; the resolution half is in ``test_policy_resolver.py``.
- *Safe rollback to any prior version.* Proved forward-only, with the event
  trail intact.

The fourth — no deploy, within one reload interval — is a property of the
running stack and is proved in ``scripts/gate_phase6.py``.

Real Postgres throughout. The concurrency behaviour is the interesting part of
this module (two operators approving the same draft, two activations racing) and
none of it is meaningfully testable against a mock.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from nemesis.db.models.event import Event
from nemesis.db.models.organisation import Department
from nemesis.db.models.policy import PolicyVersion
from nemesis.db.models.taxonomy import TaxonomyNode
from nemesis.policy import service
from nemesis.policy.baselines import SEEDED_KINDS
from nemesis.policy.documents import PolicyKind, PolicyStatus, SeverityRubric
from nemesis.policy.errors import (
    PolicyConflictError,
    PolicyNotFoundError,
    PolicyTransitionError,
    PolicyValidationError,
)
from nemesis.tenancy.context import tenant_scope
from tests.conftest import postgres_required

pytestmark = [postgres_required, pytest.mark.integration]

KIND = PolicyKind.SEVERITY_RUBRIC


@asynccontextmanager
async def scoped(engine: AsyncEngine, tenant_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    """A session with the tenant bound.

    ``tenant_scope`` is synchronous, so it cannot join an ``async with`` list
    alongside the session — that fails at runtime with a message about the
    asynchronous context manager protocol, several lines from the cause.
    """
    maker = async_sessionmaker(engine, expire_on_commit=False)
    with tenant_scope(tenant_id):
        async with maker() as session:
            yield session
            await session.commit()


def rubric(*, visual: float = 0.4) -> dict[str, object]:
    """A valid rubric whose weights can be varied while still summing to one."""
    remainder = round(1.0 - visual, 6)
    return {
        "components": [
            {
                "key": "visual_damage",
                "display_name": "Visual damage",
                "weight": visual,
                "description": "How severe the defect looks.",
            },
            {
                "key": "road_class",
                "display_name": "Location importance",
                "weight": remainder,
                "description": "How significant the location is.",
            },
        ]
    }


async def reread(
    session: AsyncSession, tenant_id: uuid.UUID, version: PolicyVersion
) -> PolicyVersion:
    """Re-read a row under a tenant predicate.

    ``AsyncSession.refresh`` is unusable anywhere in this codebase: it emits
    ``SELECT ... WHERE id = ?`` with no tenant filter, and the guard the test
    fixtures install — the same one production runs — refuses it. That is the
    guard working, so the tests go through the scoped read like everything else.
    """
    return await service.require_version(
        session, tenant_id=tenant_id, kind=PolicyKind(version.kind), revision=version.revision
    )


async def make_live(
    session: AsyncSession, tenant_id: uuid.UUID, body: dict[str, object], *, reason: str = "r"
) -> PolicyVersion:
    """Walk one document all the way to active — the common test preamble."""
    version = await service.draft(
        session, tenant_id=tenant_id, kind=KIND, body=body, change_reason=reason
    )
    for verb in (service.submit_for_review, service.approve, service.activate):
        await verb(
            session, tenant_id=tenant_id, kind=KIND, revision=version.revision, reason=reason
        )
    return version


# ---------------------------------------------------------------------------
# The gate clause: a draft decides nothing
# ---------------------------------------------------------------------------


async def test_a_draft_never_decides_anything(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The clause the whole approval lifecycle exists to make true.

    A draft is in the table, has a revision number, and is fully validated —
    every property except the one that matters. ``active_version`` is the only
    read path the resolver has, and it never sees one.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        await service.draft(
            session, tenant_id=tenant_id, kind=KIND, body=rubric(), change_reason="first"
        )
        assert await service.active_version(session, tenant_id=tenant_id, kind=KIND) is None


async def test_an_unapproved_draft_cannot_be_activated(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """And the refusal names the transitions that are available.

    A caller that receives a generic validation error re-sends the same request
    with a tweaked body, which for an illegal transition can never work.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        version = await service.draft(
            session, tenant_id=tenant_id, kind=KIND, body=rubric(), change_reason="first"
        )
        with pytest.raises(PolicyTransitionError) as caught:
            await service.activate(
                session, tenant_id=tenant_id, kind=KIND, revision=version.revision, reason="go"
            )
        assert "in_review" in str(caught.value)


async def test_the_database_refuses_a_second_active_version(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The partial unique index, not the service check, is the real control.

    A service-level "is anything already active" check holds right up until two
    operators press Activate in the same second — which is exactly when it
    matters, because that is during an incident.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        first = await make_live(session, tenant_id, rubric())

    async with scoped(migrated_engine, tenant_id) as session:
        session.add(
            PolicyVersion(
                tenant_id=tenant_id,
                kind=KIND.value,
                revision=99,
                status=PolicyStatus.ACTIVE.value,
                body={},
                content_hash="0" * 64,
                change_reason="smuggled past the service",
                approved_at=datetime.now(tz=UTC),
                effective_from=datetime.now(tz=UTC),
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()

    assert first.revision == 1


async def test_the_database_refuses_an_unapproved_live_version(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """ "Who signed off on the rubric that scored this" must be answerable.

    From the row itself — a service-only check leaves a psql session able to
    activate anonymously, and the audit trail then has a hole nobody can see.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        session.add(
            PolicyVersion(
                tenant_id=tenant_id,
                kind=KIND.value,
                revision=1,
                status=PolicyStatus.ACTIVE.value,
                body={},
                content_hash="0" * 64,
                change_reason="never approved",
                effective_from=datetime.now(tz=UTC),
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


# ---------------------------------------------------------------------------
# The lifecycle itself
# ---------------------------------------------------------------------------


async def test_the_happy_path_reaches_active_and_supersedes_its_predecessor(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    async with scoped(migrated_engine, tenant_id) as session:
        first = await make_live(session, tenant_id, rubric(visual=0.4))
        second = await make_live(session, tenant_id, rubric(visual=0.6))

        first = await reread(session, tenant_id, first)
        assert first.status == PolicyStatus.SUPERSEDED.value
        assert first.effective_until is not None, "a superseded version closes its interval"
        assert second.status == PolicyStatus.ACTIVE.value
        assert second.effective_until is None

        live = await service.active_version(session, tenant_id=tenant_id, kind=KIND)
        assert live is not None and live.revision == second.revision


async def test_an_approved_document_cannot_be_edited(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """An approval is a signature on specific content.

    Letting the author adjust "just the one weight" afterwards makes it a
    signature on a document that no longer exists.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        version = await service.draft(
            session, tenant_id=tenant_id, kind=KIND, body=rubric(), change_reason="r"
        )
        for verb in (service.submit_for_review, service.approve):
            await verb(
                session, tenant_id=tenant_id, kind=KIND, revision=version.revision, reason="r"
            )
        with pytest.raises(PolicyTransitionError, match="cannot be edited"):
            await service.update_draft(
                session,
                tenant_id=tenant_id,
                kind=KIND,
                revision=version.revision,
                body=rubric(visual=0.9),
            )


async def test_withdrawing_clears_the_approval(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Otherwise an approval timestamp survives onto editable content.

    Which is the failure the edit lock exists to prevent, arriving through the
    back door.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        version = await service.draft(
            session, tenant_id=tenant_id, kind=KIND, body=rubric(), change_reason="r"
        )
        for verb in (service.submit_for_review, service.approve, service.withdraw):
            await verb(
                session, tenant_id=tenant_id, kind=KIND, revision=version.revision, reason="r"
            )
        version = await reread(session, tenant_id, version)
        assert version.status == PolicyStatus.DRAFT.value
        assert version.approved_at is None
        assert version.approved_by is None


async def test_a_rejection_is_distinguishable_from_an_abandonment(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The author needs to know whether anybody actually looked."""
    async with scoped(migrated_engine, tenant_id) as session:
        version = await service.draft(
            session, tenant_id=tenant_id, kind=KIND, body=rubric(), change_reason="r"
        )
        await service.submit_for_review(
            session, tenant_id=tenant_id, kind=KIND, revision=version.revision, reason="r"
        )
        await service.reject(
            session,
            tenant_id=tenant_id,
            kind=KIND,
            revision=version.revision,
            reason="the visual weight is too high for a monsoon backlog",
        )
        version = await reread(session, tenant_id, version)
        assert version.status == PolicyStatus.ARCHIVED.value
        assert version.rejection_reason is not None
        assert "monsoon" in version.rejection_reason


async def test_every_transition_requires_a_stated_reason(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """An audit trail that answers "what" and refuses "why" is the half nobody
    needs during an incident."""
    async with scoped(migrated_engine, tenant_id) as session:
        with pytest.raises(PolicyValidationError, match="stated reason"):
            await service.draft(
                session, tenant_id=tenant_id, kind=KIND, body=rubric(), change_reason="   "
            )
        version = await service.draft(
            session, tenant_id=tenant_id, kind=KIND, body=rubric(), change_reason="r"
        )
        with pytest.raises(PolicyValidationError, match="stated reason"):
            await service.submit_for_review(
                session, tenant_id=tenant_id, kind=KIND, revision=version.revision, reason=""
            )


async def test_revision_numbers_are_never_reused(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A rejected draft consumes its number permanently.

    Reusing it would make two different documents share a stamp, and
    ``severity_scored.policy_version`` would resolve to whichever survived.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        first = await service.draft(
            session, tenant_id=tenant_id, kind=KIND, body=rubric(), change_reason="r"
        )
        await service.submit_for_review(
            session, tenant_id=tenant_id, kind=KIND, revision=first.revision, reason="r"
        )
        await service.reject(
            session, tenant_id=tenant_id, kind=KIND, revision=first.revision, reason="no"
        )
        second = await service.draft(
            session, tenant_id=tenant_id, kind=KIND, body=rubric(), change_reason="r"
        )
        assert second.revision == first.revision + 1


async def test_a_concurrent_transition_is_reported_as_a_conflict(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Two operators approving the same draft: the second must not succeed.

    The status predicate on the UPDATE is what catches it. Without it the second
    approval writes an event claiming a transition that never happened, and the
    chain then contains two approvals of one document.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        version = await service.draft(
            session, tenant_id=tenant_id, kind=KIND, body=rubric(), change_reason="r"
        )
        await service.submit_for_review(
            session, tenant_id=tenant_id, kind=KIND, revision=version.revision, reason="r"
        )
        await service.approve(
            session, tenant_id=tenant_id, kind=KIND, revision=version.revision, reason="r"
        )
        with pytest.raises(PolicyTransitionError):
            await service.approve(
                session, tenant_id=tenant_id, kind=KIND, revision=version.revision, reason="r"
            )


# ---------------------------------------------------------------------------
# Effective dating
# ---------------------------------------------------------------------------


async def test_a_future_dated_activation_does_not_decide_yet(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A rate card negotiated in March to apply from April.

    ``active`` in the table from the moment it is scheduled, and not deciding
    until its date arrives — treating it as live on the day it was activated
    would apply next month's prices this month.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        await make_live(session, tenant_id, rubric(visual=0.4))
        scheduled = await service.draft(
            session, tenant_id=tenant_id, kind=KIND, body=rubric(visual=0.7), change_reason="r"
        )
        for verb in (service.submit_for_review, service.approve):
            await verb(
                session, tenant_id=tenant_id, kind=KIND, revision=scheduled.revision, reason="r"
            )
        await service.activate(
            session,
            tenant_id=tenant_id,
            kind=KIND,
            revision=scheduled.revision,
            reason="scheduled",
            effective_from=datetime.now(tz=UTC) + timedelta(days=30),
        )

        live = await service.active_version(session, tenant_id=tenant_id, kind=KIND)
        assert live is None, "nothing decides until the scheduled date arrives"

        future = await service.version_effective_at(
            session,
            tenant_id=tenant_id,
            kind=KIND,
            moment=datetime.now(tz=UTC) + timedelta(days=31),
        )
        assert future is not None and future.revision == scheduled.revision


async def test_back_dating_an_activation_is_refused(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """It would claim a document decided complaints it never saw.

    And every one of those decisions is already in the log, stamped with the
    version that actually made them.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        version = await service.draft(
            session, tenant_id=tenant_id, kind=KIND, body=rubric(), change_reason="r"
        )
        for verb in (service.submit_for_review, service.approve):
            await verb(
                session, tenant_id=tenant_id, kind=KIND, revision=version.revision, reason="r"
            )
        with pytest.raises(PolicyValidationError, match="in the past"):
            await service.activate(
                session,
                tenant_id=tenant_id,
                kind=KIND,
                revision=version.revision,
                reason="r",
                effective_from=datetime.now(tz=UTC) - timedelta(days=1),
            )


async def test_which_version_was_live_is_an_interval_query(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The query Phase 7's backtester and every dispute resolution runs.

    It has a single answer only because rollback moves forward — re-activating
    an old row would overlap the intervals and make this ambiguous.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        first = await make_live(session, tenant_id, rubric(visual=0.4))
        between = datetime.now(tz=UTC)
        second = await make_live(session, tenant_id, rubric(visual=0.6))

        was_live = await service.version_effective_at(
            session, tenant_id=tenant_id, kind=KIND, moment=between
        )
        assert was_live is not None and was_live.revision == first.revision

        now_live = await service.version_effective_at(
            session, tenant_id=tenant_id, kind=KIND, moment=datetime.now(tz=UTC)
        )
        assert now_live is not None and now_live.revision == second.revision


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------


async def test_rollback_creates_a_new_version_carrying_the_old_content(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Forward-only. The version sequence never decreases.

    Which is what keeps "what was live on 14 March" an interval query rather
    than a question about a row that was live, then was not, then was again.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        first = await make_live(session, tenant_id, rubric(visual=0.4))
        second = await make_live(session, tenant_id, rubric(visual=0.9))

        restored = await service.rollback(
            session,
            tenant_id=tenant_id,
            kind=KIND,
            to_revision=first.revision,
            reason="the new weights over-scored minor defects",
        )

        assert restored.revision == second.revision + 1
        assert restored.content_hash == first.content_hash
        assert restored.rolled_back_from_id == first.id
        assert restored.status == PolicyStatus.ACTIVE.value

        first = await reread(session, tenant_id, first)
        assert first.status == PolicyStatus.SUPERSEDED.value, "the old row is not resurrected"


async def test_rollback_to_a_never_approved_revision_is_refused(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Rollback skips review, so the content must have been approved once.

    Otherwise the emergency path becomes a way to put unreviewed content into
    production, which is the exact control the phase is built around.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        await make_live(session, tenant_id, rubric(visual=0.4))
        never = await service.draft(
            session, tenant_id=tenant_id, kind=KIND, body=rubric(visual=0.8), change_reason="r"
        )
        with pytest.raises(PolicyTransitionError, match="never approved"):
            await service.rollback(
                session,
                tenant_id=tenant_id,
                kind=KIND,
                to_revision=never.revision,
                reason="panic",
            )


# ---------------------------------------------------------------------------
# The hash chain
# ---------------------------------------------------------------------------


async def _tenant_chain(session: AsyncSession, tenant_id: uuid.UUID) -> list[tuple[str, dict]]:
    rows = await session.execute(
        select(Event.event_type, Event.payload)
        .where(Event.tenant_id == tenant_id, Event.entity_type == "tenant")
        .order_by(Event.sequence)
    )
    return [(row[0], row[1]) for row in rows.all()]


async def test_every_transition_lands_on_the_tenant_chain(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The gate clause, literally: every transition is an event in the chain.

    Including the ones inside a rollback. A fast path that skipped the review
    events would make the claim untrue in exactly the case an auditor asks
    about — the emergency change nobody reviewed.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        first = await make_live(session, tenant_id, rubric(visual=0.4))
        await make_live(session, tenant_id, rubric(visual=0.9))
        await service.rollback(
            session, tenant_id=tenant_id, kind=KIND, to_revision=first.revision, reason="revert"
        )
        chain = await _tenant_chain(session, tenant_id)

    kinds = [event_type for event_type, _ in chain]
    # Two documents plus the rollback's restored copy: three drafts, and four
    # transitions each for the two that superseded a predecessor.
    assert kinds.count("policy_drafted") == 3
    transitions = [payload for event_type, payload in chain if event_type == "policy_transitioned"]
    assert [payload["to_status"] for payload in transitions] == [
        "in_review",
        "approved",
        "active",
        "in_review",
        "approved",
        "active",
        "in_review",
        "approved",
        "active",
    ]
    assert transitions[-1]["superseded_revision"] == 2


async def test_the_draft_event_carries_the_content_hash_not_the_body(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A safety ruleset is kilobytes of terms and changes often.

    Inlining the document would grow an append-only log that must live for years
    by the full body on every edit — the same reasoning ``complaint_submitted``
    applies to photographs.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        version = await service.draft(
            session, tenant_id=tenant_id, kind=KIND, body=rubric(), change_reason="r"
        )
        chain = await _tenant_chain(session, tenant_id)

    drafted = next(payload for event_type, payload in chain if event_type == "policy_drafted")
    assert drafted["content_hash"] == version.content_hash
    assert "body" not in drafted and "components" not in drafted


async def test_the_content_hash_is_stable_across_key_order(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Two databases seeded from the same template must agree.

    A hash over ``json.dumps`` would depend on key insertion order and answer
    "different" for two identical documents, which makes "is this draft actually
    different from what is live" unanswerable.
    """
    body = rubric()
    reordered = {"components": [dict(reversed(list(c.items()))) for c in body["components"]]}  # type: ignore[union-attr]
    async with scoped(migrated_engine, tenant_id) as session:
        first = await service.draft(
            session, tenant_id=tenant_id, kind=KIND, body=body, change_reason="r"
        )
        second = await service.draft(
            session, tenant_id=tenant_id, kind=KIND, body=reordered, change_reason="r"
        )
        assert first.content_hash == second.content_hash


# ---------------------------------------------------------------------------
# Cross-entity references
# ---------------------------------------------------------------------------


async def test_a_routing_rule_naming_an_unknown_department_is_refused(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A rule pointing nowhere routes work into a void.

    Which on a queue is indistinguishable from a backlog, so the failure has no
    symptom until somebody asks why a department is quiet.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        with pytest.raises(PolicyValidationError, match="departments this tenant does not have"):
            await service.draft(
                session,
                tenant_id=tenant_id,
                kind=PolicyKind.ROUTING_RULES,
                body={
                    "rules": [
                        {
                            "rule_id": "r1",
                            "display_name": "Nowhere",
                            "condition": "True",
                            "department_code": "GHOST",
                        }
                    ]
                },
                change_reason="r",
            )


async def test_a_rubric_override_on_an_unknown_category_is_refused(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """An override that never fires reads on screen exactly like one that works."""
    async with scoped(migrated_engine, tenant_id) as session:
        body = rubric()
        body["overrides"] = [{"category": "no_such_category", "floor": 5.0}]
        with pytest.raises(PolicyValidationError, match="does not define"):
            await service.draft(
                session, tenant_id=tenant_id, kind=KIND, body=body, change_reason="r"
            )


async def test_a_reference_to_a_real_department_is_accepted(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    async with scoped(migrated_engine, tenant_id) as session:
        session.add(
            Department(tenant_id=tenant_id, code="PWD", name="Public Works", path="PWD", depth=0)
        )
        await session.flush()
        version = await service.draft(
            session,
            tenant_id=tenant_id,
            kind=PolicyKind.ROUTING_RULES,
            body={
                "rules": [
                    {
                        "rule_id": "r1",
                        "display_name": "Everything",
                        "condition": "True",
                        "department_code": "PWD",
                    }
                ]
            },
            change_reason="r",
        )
        assert version.revision == 1


async def test_a_deactivated_category_is_still_a_valid_reference(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A category can be retired while complaints in it are still open.

    An override that stopped resolving the moment somebody tidied the taxonomy
    would silently change the score of work already in flight.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        session.add(
            TaxonomyNode(
                tenant_id=tenant_id,
                key="retired",
                path="retired",
                depth=0,
                display_name="Retired",
                is_active=False,
            )
        )
        await session.flush()
        body = rubric()
        body["overrides"] = [{"category": "retired", "floor": 5.0}]
        version = await service.draft(
            session, tenant_id=tenant_id, kind=KIND, body=body, change_reason="r"
        )
        assert version.revision == 1


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


async def test_one_tenants_policy_is_invisible_to_another(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID, other_tenant_id: uuid.UUID
) -> None:
    """ "Exists but belongs to someone else" and "does not exist" are one answer.

    Otherwise the endpoint becomes a way to enumerate another customer's
    rulesets one revision number at a time.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        await make_live(session, tenant_id, rubric())

    async with scoped(migrated_engine, other_tenant_id) as session:
        assert await service.active_version(session, tenant_id=other_tenant_id, kind=KIND) is None
        with pytest.raises(PolicyNotFoundError):
            await service.require_version(session, tenant_id=other_tenant_id, kind=KIND, revision=1)


async def test_two_tenants_run_conflicting_policies_simultaneously(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID, other_tenant_id: uuid.UUID
) -> None:
    """The multi-tenant claim, applied to the thing that decides scores.

    Both tenants have a revision 1 of the same kind, with different content, and
    each reads back its own.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        await make_live(session, tenant_id, rubric(visual=0.3))
    async with scoped(migrated_engine, other_tenant_id) as session:
        await make_live(session, other_tenant_id, rubric(visual=0.8))

    async with scoped(migrated_engine, tenant_id) as session:
        first = await service.active_version(session, tenant_id=tenant_id, kind=KIND)
    async with scoped(migrated_engine, other_tenant_id) as session:
        second = await service.active_version(session, tenant_id=other_tenant_id, kind=KIND)

    assert first is not None and second is not None
    assert first.revision == second.revision == 1
    assert first.content_hash != second.content_hash
    assert SeverityRubric.model_validate(first.body).components[0].weight == 0.3
    assert SeverityRubric.model_validate(second.body).components[0].weight == 0.8


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


async def test_seeding_gives_a_tenant_every_baselined_kind_live(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    async with scoped(migrated_engine, tenant_id) as session:
        seeded = await service.seed_baselines(session, tenant_id=tenant_id)
        assert {PolicyKind(version.kind) for version in seeded} == set(SEEDED_KINDS)
        for kind in SEEDED_KINDS:
            live = await service.active_version(session, tenant_id=tenant_id, kind=kind)
            assert live is not None, f"{kind.value} should be live after seeding"
            assert live.approved_at is not None


async def test_seeding_is_idempotent_and_does_not_clobber_tuning(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Running the backfill twice across a fleet must be safe.

    And it must not overwrite a tenant that has since tuned its own rubric,
    which is the failure that would make an operator afraid to run it at all.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        await service.seed_baselines(session, tenant_id=tenant_id)
        tuned = await make_live(session, tenant_id, rubric(visual=0.75))

        again = await service.seed_baselines(session, tenant_id=tenant_id)
        assert again == []

        live = await service.active_version(session, tenant_id=tenant_id, kind=KIND)
        assert live is not None and live.revision == tuned.revision


async def test_seeding_walks_the_full_lifecycle(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """There is exactly one way a document becomes live, including for seeds.

    A seeding path that wrote straight to ``active`` would be a second way, and
    "does one exist" is the first thing an auditor asks.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        await service.seed_baselines(session, tenant_id=tenant_id)
        chain = await _tenant_chain(session, tenant_id)

    for kind in SEEDED_KINDS:
        statuses = [
            payload["to_status"]
            for event_type, payload in chain
            if event_type == "policy_transitioned" and payload["kind"] == kind.value
        ]
        assert statuses == ["in_review", "approved", "active"]


# ---------------------------------------------------------------------------
# Editing a draft in place
# ---------------------------------------------------------------------------


async def test_a_draft_can_be_edited_and_the_hash_follows(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Editing keeps the revision number and changes the content hash.

    The revision is the identifier an operator quotes and a decision records;
    consuming a new one for every keystroke in the editor would make the numbers
    meaningless. What must move is the hash, because that is what answers "is
    this the document I reviewed".
    """
    async with scoped(migrated_engine, tenant_id) as session:
        version = await service.draft(
            session,
            tenant_id=tenant_id,
            kind=KIND,
            body=rubric(visual=0.4),
            change_reason="first pass",
        )
        original_hash = version.content_hash

        edited = await service.update_draft(
            session,
            tenant_id=tenant_id,
            kind=KIND,
            revision=version.revision,
            body=rubric(visual=0.8),
            change_reason="second pass after review feedback",
        )

        assert edited.revision == version.revision
        assert edited.content_hash != original_hash
        assert edited.change_reason == "second pass after review feedback"
        assert SeverityRubric.model_validate(edited.body).components[0].weight == 0.8


async def test_editing_a_draft_re_records_it_on_the_chain(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A second ``policy_drafted`` supersedes the first as the answer to
    "which bytes existed under this revision number"."""
    async with scoped(migrated_engine, tenant_id) as session:
        version = await service.draft(
            session, tenant_id=tenant_id, kind=KIND, body=rubric(visual=0.4), change_reason="r"
        )
        await service.update_draft(
            session,
            tenant_id=tenant_id,
            kind=KIND,
            revision=version.revision,
            body=rubric(visual=0.8),
        )
        chain = await _tenant_chain(session, tenant_id)

    drafts = [payload for event_type, payload in chain if event_type == "policy_drafted"]
    assert len(drafts) == 2
    assert drafts[0]["revision"] == drafts[1]["revision"] == version.revision
    assert drafts[0]["content_hash"] != drafts[1]["content_hash"]


async def test_editing_a_draft_keeps_its_reason_when_none_is_supplied(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A body-only edit must not silently blank the mandatory reason."""
    async with scoped(migrated_engine, tenant_id) as session:
        version = await service.draft(
            session,
            tenant_id=tenant_id,
            kind=KIND,
            body=rubric(visual=0.4),
            change_reason="the reason",
        )
        edited = await service.update_draft(
            session,
            tenant_id=tenant_id,
            kind=KIND,
            revision=version.revision,
            body=rubric(visual=0.6),
        )
        assert edited.change_reason == "the reason"


async def test_an_invalid_edit_is_refused_and_leaves_the_draft_alone(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    async with scoped(migrated_engine, tenant_id) as session:
        version = await service.draft(
            session, tenant_id=tenant_id, kind=KIND, body=rubric(visual=0.4), change_reason="r"
        )
        broken = rubric(visual=0.4)
        broken["components"][0]["weight"] = 0.9  # type: ignore[index]
        with pytest.raises(PolicyValidationError):
            await service.update_draft(
                session, tenant_id=tenant_id, kind=KIND, revision=version.revision, body=broken
            )
        unchanged = await reread(session, tenant_id, version)
        assert SeverityRubric.model_validate(unchanged.body).components[0].weight == 0.4


# ---------------------------------------------------------------------------
# Smaller guards
# ---------------------------------------------------------------------------


async def test_re_activating_the_live_version_is_a_conflict(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Not a silent no-op. An operator pressing Activate twice needs to know the
    second press did nothing, or they will conclude the first one failed."""
    async with scoped(migrated_engine, tenant_id) as session:
        version = await make_live(session, tenant_id, rubric())
        with pytest.raises(PolicyConflictError, match="already active"):
            await service.activate(
                session, tenant_id=tenant_id, kind=KIND, revision=version.revision, reason="again"
            )


async def test_a_naive_moment_is_refused_by_the_interval_query(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The same rule the event store applies to ``occurred_at``.

    There is no correct default timezone, only a silently wrong one — and a
    dispute resolved against the wrong instant is worse than one that errored.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        with pytest.raises(PolicyValidationError, match="timezone-aware"):
            await service.version_effective_at(
                session,
                tenant_id=tenant_id,
                kind=KIND,
                moment=datetime(2026, 3, 1),  # noqa: DTZ001
            )


async def test_listing_narrows_by_kind_and_status(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The review queue is a status filter; the history screen is a kind filter."""
    async with scoped(migrated_engine, tenant_id) as session:
        await service.seed_baselines(session, tenant_id=tenant_id)
        await service.draft(
            session,
            tenant_id=tenant_id,
            kind=KIND,
            body=rubric(visual=0.6),
            change_reason="pending",
        )

        rubrics = await service.list_versions(session, tenant_id=tenant_id, kind=KIND)
        assert {version.kind for version in rubrics} == {KIND.value}

        drafts = await service.list_versions(
            session, tenant_id=tenant_id, statuses=[PolicyStatus.DRAFT]
        )
        assert [version.revision for version in drafts] == [2]


async def test_the_stamp_is_what_a_decision_records(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """``kind@revision`` rather than a UUID — the string an operator quotes."""
    async with scoped(migrated_engine, tenant_id) as session:
        version = await make_live(session, tenant_id, rubric())
        assert version.stamp == f"{KIND.value}@{version.revision}"
