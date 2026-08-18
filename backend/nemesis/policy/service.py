"""The policy lifecycle: draft → review → approve → activate, and back.

Every function takes a session and participates in the caller's transaction, and
none of them commits — the same contract ``control_plane.taxonomy`` states, for
the same reason. An activation that flips one row to ``active`` and fails before
superseding the previous one would leave two live policies, which the partial
unique index refuses; the refusal is only *useful* if the whole activation rolls
back together.

**Unlike the taxonomy service, mutations do publish.** The taxonomy batches four
hundred node writes into one ``taxonomy_published`` because they are one
operator action. A policy transition is never batched: each one is a decision a
named person made about a document that changes how citizens' reports are
scored, and collapsing two of them into one event would lose which document the
approver actually approved.

**The transition table is data, and it is the only place lifecycle is decided.**
Scattering ``if status == ...`` across the API, the CLI, and the service is how
a system acquires a path where a draft reaches ``active`` without passing
through approval. ``_TRANSITIONS`` below is small enough to read in one look and
every mutation consults it.

**Rollback creates a new version.** See ``db.models.policy`` — restoring
revision 3 produces revision 8 carrying revision 3's body. Re-activating the old
row would make the effective-date intervals overlap and turn "what was live on
14 March" into a question about row history rather than a query.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Final, cast

from sqlalchemy import Select, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.db.models.organisation import Department
from nemesis.db.models.policy import PolicyVersion
from nemesis.db.models.taxonomy import TaxonomyNode
from nemesis.events.canonical import JSONValue, canonicalise
from nemesis.events.store import EventStore
from nemesis.policy import baselines
from nemesis.policy.documents import (
    DECIDING_STATUSES,
    DedupThresholds,
    PolicyBody,
    PolicyKind,
    PolicyStatus,
    RateCard,
    RoutingRules,
    SeverityRubric,
    SlaMatrix,
    validate_body,
)
from nemesis.policy.errors import (
    PolicyConflictError,
    PolicyNotFoundError,
    PolicyTransitionError,
    PolicyValidationError,
)

#: The legal moves. Keys are the state a document is in; values are where it may
#: go. Read it as the whole lifecycle, because it is:
#:
#: - A draft may be submitted for review, or abandoned.
#: - A document in review may be approved, sent back to draft, or rejected.
#: - An approved document may be activated, or withdrawn back to draft if the
#:   world changed between approval and activation.
#: - An active document leaves only by being superseded, which activation of its
#:   successor does — there is no "deactivate", because a tenant with no active
#:   rubric cannot score, and the operation somebody actually wants is a
#:   rollback to the previous version.
#: - Superseded and archived are terminal. History does not move.
_TRANSITIONS: Final[dict[PolicyStatus, frozenset[PolicyStatus]]] = {
    PolicyStatus.DRAFT: frozenset({PolicyStatus.IN_REVIEW, PolicyStatus.ARCHIVED}),
    PolicyStatus.IN_REVIEW: frozenset(
        {PolicyStatus.APPROVED, PolicyStatus.DRAFT, PolicyStatus.ARCHIVED}
    ),
    PolicyStatus.APPROVED: frozenset({PolicyStatus.ACTIVE, PolicyStatus.DRAFT}),
    PolicyStatus.ACTIVE: frozenset({PolicyStatus.SUPERSEDED}),
    PolicyStatus.SUPERSEDED: frozenset(),
    PolicyStatus.ARCHIVED: frozenset(),
}

#: Statuses a document may still be edited in. Once a document has been approved
#: its bytes are frozen: an approver signed off on specific content, and letting
#: the author adjust "just the one weight" afterwards makes the approval a
#: signature on a document that no longer exists.
_EDITABLE_STATUSES: Final[frozenset[PolicyStatus]] = frozenset({PolicyStatus.DRAFT})


def content_hash(body: Mapping[str, Any]) -> str:
    """SHA-256 over the canonical JSON of a document body.

    RFC 8785 canonicalisation, the same function the event chain uses, so two
    databases seeded from the same template produce the same hash and "is this
    draft different from what is live" is one comparison. A hash over
    ``json.dumps`` would depend on key insertion order and answer "different"
    for two identical documents.
    """
    return hashlib.sha256(canonicalise(cast("JSONValue", dict(body)))).hexdigest()


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def _scoped(tenant_id: uuid.UUID) -> Select[Any]:
    return select(PolicyVersion).where(PolicyVersion.tenant_id == tenant_id)


async def get_version(
    session: AsyncSession, *, tenant_id: uuid.UUID, kind: PolicyKind, revision: int
) -> PolicyVersion | None:
    row = await session.execute(
        _scoped(tenant_id).where(
            PolicyVersion.kind == kind.value, PolicyVersion.revision == revision
        )
    )
    return row.scalar_one_or_none()


async def require_version(
    session: AsyncSession, *, tenant_id: uuid.UUID, kind: PolicyKind, revision: int
) -> PolicyVersion:
    version = await get_version(session, tenant_id=tenant_id, kind=kind, revision=revision)
    if version is None:
        raise PolicyNotFoundError(f"no {kind.value} revision {revision} for this tenant")
    return version


async def active_version(
    session: AsyncSession, *, tenant_id: uuid.UUID, kind: PolicyKind
) -> PolicyVersion | None:
    """The document currently deciding, or ``None`` if the tenant has none.

    ``DECIDING_STATUSES`` rather than a literal, and the effective-date filter
    rather than status alone: a future-dated activation is ``active`` in the
    table from the moment it is scheduled, and treating it as live on the day it
    was activated would silently apply next month's rate card this month.
    """
    now = datetime.now(tz=UTC)
    row = await session.execute(
        _scoped(tenant_id)
        .where(
            PolicyVersion.kind == kind.value,
            PolicyVersion.status.in_([status.value for status in DECIDING_STATUSES]),
            PolicyVersion.effective_from.is_not(None),
            PolicyVersion.effective_from <= now,
        )
        .order_by(PolicyVersion.revision.desc())
        .limit(1)
    )
    return row.scalar_one_or_none()


async def list_versions(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    kind: PolicyKind | None = None,
    statuses: Sequence[PolicyStatus] | None = None,
    limit: int = 100,
) -> list[PolicyVersion]:
    """Revision history, newest first, optionally narrowed by kind and status."""
    statement = _scoped(tenant_id)
    if kind is not None:
        statement = statement.where(PolicyVersion.kind == kind.value)
    if statuses:
        statement = statement.where(PolicyVersion.status.in_([status.value for status in statuses]))
    rows = await session.execute(
        statement.order_by(PolicyVersion.kind, PolicyVersion.revision.desc()).limit(limit)
    )
    return list(rows.scalars().all())


async def version_effective_at(
    session: AsyncSession, *, tenant_id: uuid.UUID, kind: PolicyKind, moment: datetime
) -> PolicyVersion | None:
    """Which document was deciding at ``moment``.

    The query Phase 7's backtester and every dispute resolution runs. It is an
    interval lookup precisely because rollback moves forward rather than
    reviving a row — with re-activation the intervals would overlap and this
    would have no single answer.
    """
    if moment.tzinfo is None:
        raise PolicyValidationError("moment must be timezone-aware")
    row = await session.execute(
        _scoped(tenant_id)
        .where(
            PolicyVersion.kind == kind.value,
            PolicyVersion.effective_from.is_not(None),
            PolicyVersion.effective_from <= moment,
            (PolicyVersion.effective_until.is_(None)) | (PolicyVersion.effective_until > moment),
            PolicyVersion.status.in_([PolicyStatus.ACTIVE.value, PolicyStatus.SUPERSEDED.value]),
        )
        .order_by(PolicyVersion.revision.desc())
        .limit(1)
    )
    return row.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


async def draft(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    kind: PolicyKind,
    body: Mapping[str, Any],
    change_reason: str,
    change_summary: str | None = None,
    based_on_revision: int | None = None,
    rolled_back_from_revision: int | None = None,
    actor_id: uuid.UUID | None = None,
    correlation_id: str | None = None,
) -> PolicyVersion:
    """Author a new revision, in ``draft``, and record it on the tenant chain.

    The body is validated here — not at approval, not at activation — so the
    error reaches the person who wrote it while they still have the document
    open. ``change_reason`` is mandatory for the same reason
    ``admin_action.justification`` is.
    """
    if not change_reason or not change_reason.strip():
        raise PolicyValidationError(
            "a policy revision needs a stated reason. 'Why did the rubric change in "
            "March' is the question this record exists to answer, and it cannot be "
            "reconstructed from a diff of forty weights."
        )

    validated = validate_body(kind, body)
    # Round-trip through the model before storing. What goes into the column is
    # then exactly what the model produces — defaults filled, aliases resolved —
    # so the stored document and the one the resolver reconstructs are the same
    # bytes, and the content hash is a hash of the meaning rather than of
    # whatever subset of fields the author happened to type.
    normalised = validated.model_dump(mode="json")

    based_on = (
        await require_version(session, tenant_id=tenant_id, kind=kind, revision=based_on_revision)
        if based_on_revision is not None
        else None
    )
    rolled_back_from = (
        await require_version(
            session, tenant_id=tenant_id, kind=kind, revision=rolled_back_from_revision
        )
        if rolled_back_from_revision is not None
        else None
    )

    await _assert_references_resolve(session, tenant_id=tenant_id, kind=kind, body=validated)

    revision = await _next_revision(session, tenant_id=tenant_id, kind=kind)
    digest = content_hash(normalised)

    version = PolicyVersion(
        tenant_id=tenant_id,
        kind=kind.value,
        revision=revision,
        status=PolicyStatus.DRAFT.value,
        body=normalised,
        content_hash=digest,
        based_on_id=based_on.id if based_on is not None else None,
        rolled_back_from_id=rolled_back_from.id if rolled_back_from is not None else None,
        change_reason=change_reason.strip(),
        change_summary=change_summary,
        created_by=actor_id,
    )
    session.add(version)
    try:
        await session.flush()
    except IntegrityError as exc:
        # Two operators drafting the same kind at the same instant both computed
        # the same next revision. The unique constraint caught it; translating
        # it here means the caller retries rather than seeing a driver error.
        raise PolicyConflictError(
            f"revision {revision} of {kind.value} was created concurrently; re-read and retry"
        ) from exc

    await _append(
        session,
        tenant_id=tenant_id,
        event_type="policy_drafted",
        payload={
            "kind": kind.value,
            "revision": revision,
            "content_hash": digest,
            "based_on_revision": based_on_revision,
            "rolled_back_from_revision": rolled_back_from_revision,
            "change_reason": change_reason.strip(),
        },
        actor_id=actor_id,
        correlation_id=correlation_id,
    )
    return version


async def update_draft(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    kind: PolicyKind,
    revision: int,
    body: Mapping[str, Any],
    change_reason: str | None = None,
    actor_id: uuid.UUID | None = None,
    correlation_id: str | None = None,
) -> PolicyVersion:
    """Replace a draft's body in place.

    Only a draft. Editing an approved document would make the approval a
    signature on content that no longer exists — see ``_EDITABLE_STATUSES``. The
    remedy for "the approved version is wrong" is a new draft, which is one
    extra click and leaves both documents in the record.

    Emits ``policy_drafted`` again with the new hash, rather than a distinct
    "edited" event: what the chain needs to carry is *which bytes existed under
    this revision number*, and the second draft event supersedes the first as
    the answer.
    """
    version = await require_version(session, tenant_id=tenant_id, kind=kind, revision=revision)
    _require_editable(version)

    validated = validate_body(kind, body)
    await _assert_references_resolve(session, tenant_id=tenant_id, kind=kind, body=validated)
    normalised = validated.model_dump(mode="json")
    digest = content_hash(normalised)
    reason = (change_reason or version.change_reason).strip()
    if not reason:
        raise PolicyValidationError("a policy revision needs a stated reason")

    # An explicit UPDATE with its own tenant predicate rather than mutating the
    # loaded instance: a dirty-object flush emits `UPDATE ... WHERE id = ?` with
    # no tenant filter, which the runtime guard refuses — from a stack frame
    # nowhere near the assignment. Same discipline as `control_plane.taxonomy`.
    await session.execute(
        update(PolicyVersion)
        .where(PolicyVersion.tenant_id == tenant_id, PolicyVersion.id == version.id)
        .values(
            body=normalised,
            content_hash=digest,
            change_reason=reason,
            version=PolicyVersion.version + 1,
        )
    )
    await session.flush()
    version = await _reload(session, tenant_id=tenant_id, version=version)

    await _append(
        session,
        tenant_id=tenant_id,
        event_type="policy_drafted",
        payload={
            "kind": kind.value,
            "revision": revision,
            "content_hash": digest,
            "based_on_revision": None,
            "rolled_back_from_revision": None,
            "change_reason": reason,
        },
        actor_id=actor_id,
        correlation_id=correlation_id,
    )
    return version


async def submit_for_review(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    kind: PolicyKind,
    revision: int,
    reason: str,
    actor_id: uuid.UUID | None = None,
    correlation_id: str | None = None,
) -> PolicyVersion:
    """draft → in_review."""
    return await _transition(
        session,
        tenant_id=tenant_id,
        kind=kind,
        revision=revision,
        to_status=PolicyStatus.IN_REVIEW,
        reason=reason,
        actor_id=actor_id,
        correlation_id=correlation_id,
        extra={"submitted_at": datetime.now(tz=UTC)},
    )


async def approve(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    kind: PolicyKind,
    revision: int,
    reason: str,
    actor_id: uuid.UUID | None = None,
    correlation_id: str | None = None,
) -> PolicyVersion:
    """in_review → approved.

    Re-validates the body. A document approved under one release of the models
    can be activated under the next, and re-checking here turns "this field
    became required" into a refusal naming the document rather than a policy
    that activates and behaves in a way nobody specified.
    """
    version = await require_version(session, tenant_id=tenant_id, kind=kind, revision=revision)
    validate_body(kind, version.body)
    return await _transition(
        session,
        tenant_id=tenant_id,
        kind=kind,
        revision=revision,
        to_status=PolicyStatus.APPROVED,
        reason=reason,
        actor_id=actor_id,
        correlation_id=correlation_id,
        extra={"approved_at": datetime.now(tz=UTC), "approved_by": actor_id},
        loaded=version,
    )


async def reject(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    kind: PolicyKind,
    revision: int,
    reason: str,
    actor_id: uuid.UUID | None = None,
    correlation_id: str | None = None,
) -> PolicyVersion:
    """in_review → archived, with the reviewer's reason recorded on the row.

    Archived rather than back to draft, and the distinction is for the author:
    a rejected document is one somebody looked at and said no to, an abandoned
    one is one nobody read. ``rejection_reason`` is what tells them apart.
    """
    return await _transition(
        session,
        tenant_id=tenant_id,
        kind=kind,
        revision=revision,
        to_status=PolicyStatus.ARCHIVED,
        reason=reason,
        actor_id=actor_id,
        correlation_id=correlation_id,
        extra={"rejection_reason": reason, "reviewed_by": actor_id},
    )


async def withdraw(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    kind: PolicyKind,
    revision: int,
    reason: str,
    actor_id: uuid.UUID | None = None,
    correlation_id: str | None = None,
) -> PolicyVersion:
    """in_review or approved → draft, clearing the approval.

    The approval fields are cleared rather than kept, deliberately. A document
    that goes back to draft becomes editable again, and an approval timestamp
    surviving that would attest to content that can now change — which is the
    exact failure ``_EDITABLE_STATUSES`` exists to prevent, arriving through the
    back door.
    """
    return await _transition(
        session,
        tenant_id=tenant_id,
        kind=kind,
        revision=revision,
        to_status=PolicyStatus.DRAFT,
        reason=reason,
        actor_id=actor_id,
        correlation_id=correlation_id,
        extra={"approved_at": None, "approved_by": None, "submitted_at": None},
    )


async def activate(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    kind: PolicyKind,
    revision: int,
    reason: str,
    effective_from: datetime | None = None,
    actor_id: uuid.UUID | None = None,
    correlation_id: str | None = None,
) -> PolicyVersion:
    """approved → active, superseding whatever was live.

    Both halves in one transaction, and the order matters: the incumbent is
    superseded *first*, so the partial unique index is never asked to hold two
    active rows even momentarily. Doing it the other way round works on a single
    connection and deadlocks under concurrency, which is the kind of bug that
    only appears on the day two operators are both reacting to an incident.

    ``effective_from`` may be in the future — a rate card negotiated in March to
    apply from April. It may not be in the past: back-dating would claim a
    document decided complaints it never saw, and every one of those decisions
    is in the log stamped with the version that actually made it.
    """
    version = await require_version(session, tenant_id=tenant_id, kind=kind, revision=revision)
    validate_body(kind, version.body)

    now = datetime.now(tz=UTC)
    starts_at = effective_from or now
    if starts_at.tzinfo is None:
        raise PolicyValidationError("effective_from must be timezone-aware")
    if starts_at < now:
        raise PolicyValidationError(
            f"effective_from ({starts_at.isoformat()}) is in the past. Back-dating an "
            f"activation would claim this document decided complaints it never saw — "
            f"and every one of those decisions is in the log stamped with the version "
            f"that actually made them."
        )

    incumbent = await _live_row(session, tenant_id=tenant_id, kind=kind)
    if incumbent is not None:
        if incumbent.id == version.id:
            raise PolicyConflictError(f"{kind.value} revision {revision} is already active")
        await session.execute(
            update(PolicyVersion)
            .where(PolicyVersion.tenant_id == tenant_id, PolicyVersion.id == incumbent.id)
            .values(
                status=PolicyStatus.SUPERSEDED.value,
                effective_until=starts_at,
                version=PolicyVersion.version + 1,
            )
        )
        await session.flush()

    activated = await _transition(
        session,
        tenant_id=tenant_id,
        kind=kind,
        revision=revision,
        to_status=PolicyStatus.ACTIVE,
        reason=reason,
        actor_id=actor_id,
        correlation_id=correlation_id,
        extra={
            "effective_from": starts_at,
            "effective_until": None,
            "activated_at": now,
            "activated_by": actor_id,
        },
        loaded=version,
        superseded_revision=incumbent.revision if incumbent is not None else None,
        effective_from=starts_at,
    )
    return activated


async def rollback(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    kind: PolicyKind,
    to_revision: int,
    reason: str,
    actor_id: uuid.UUID | None = None,
    correlation_id: str | None = None,
) -> PolicyVersion:
    """Restore an earlier version's content as a new, immediately active one.

    Forward-only, for the reason ``db.models.policy`` states: re-activating the
    old row would overlap the effective-date intervals and make "what was live
    on 14 March" a question about row history rather than a query.

    The restored version skips review and approval, and that is the deliberate
    part. A rollback happens during an incident — a rubric is scoring everything
    at 10, a routing rule is sending all work to one team — and requiring a
    second approver at 3am means the real remedy becomes "edit the database".
    The safety is that the content being restored *was already approved once*:
    ``rolled_back_from_id`` records which version, so nothing enters production
    that no human ever signed off on. A rollback to a draft is refused below.
    """
    target = await require_version(session, tenant_id=tenant_id, kind=kind, revision=to_revision)
    if target.approved_at is None:
        raise PolicyTransitionError(
            f"{kind.value} revision {to_revision} was never approved, so rolling back to "
            f"it would put content into production that no human signed off on. Only "
            f"versions that were live can be restored; draft it forward instead."
        )

    restored = await draft(
        session,
        tenant_id=tenant_id,
        kind=kind,
        body=target.body,
        change_reason=reason,
        change_summary=f"rollback to revision {to_revision}",
        rolled_back_from_revision=to_revision,
        actor_id=actor_id,
        correlation_id=correlation_id,
    )

    # The review states are walked rather than skipped, even though nobody is
    # reviewing at 3am. Writing straight to `approved` would be a second way for
    # a document to become live, and "does one exist" is the first thing an
    # auditor asks — the gate clause is that *every* transition is an event in
    # the chain, and a fast path with no events is exactly the exception that
    # makes the claim untrue.
    #
    # The approver recorded is whoever pressed rollback, not whoever approved
    # the original. That is the honest attribution: they are the one putting
    # this content into production now. What the original approval buys is the
    # guarantee above — the content was signed off once — and `rolled_back_from`
    # is the link that leads to who did it.
    rollback_reason = f"{reason.strip()} (rollback to revision {to_revision})"
    for verb in (submit_for_review, approve):
        await verb(
            session,
            tenant_id=tenant_id,
            kind=kind,
            revision=restored.revision,
            reason=rollback_reason,
            actor_id=actor_id,
            correlation_id=correlation_id,
        )

    return await activate(
        session,
        tenant_id=tenant_id,
        kind=kind,
        revision=restored.revision,
        reason=rollback_reason,
        actor_id=actor_id,
        correlation_id=correlation_id,
    )


async def seed_baselines(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
    correlation_id: str | None = None,
) -> list[PolicyVersion]:
    """Give a tenant the platform baseline documents, live, in one transaction.

    Called from provisioning so a new tenant is governed from its first
    complaint, and from the backfill endpoint so a tenant that predates Phase 6
    can be brought up to the same footing. **Idempotent**: a kind that already
    has any version is skipped, so running it twice across a fleet is safe and
    re-running it after a tenant has tuned its rubric cannot clobber the tuning.

    The full lifecycle is walked — draft, submit, approve, activate — rather than
    inserting an ``active`` row directly. That costs three extra events per kind
    and buys the property the whole phase rests on: there is exactly *one* way a
    document becomes live, so "was this ever approved" is answerable for a
    seeded policy by the same query as for a hand-authored one. A seeding path
    that wrote straight to ``active`` would be a second way, and the first thing
    an auditor asks is whether one exists.

    The actor on a seeded document is whoever provisioned the tenant, which is
    honest: nobody reviewed these, and the ``change_reason`` says so.
    """
    seeded: list[PolicyVersion] = []
    for kind in baselines.SEEDED_KINDS:
        existing = await session.execute(
            select(func.count())
            .select_from(PolicyVersion)
            .where(PolicyVersion.tenant_id == tenant_id, PolicyVersion.kind == kind.value)
        )
        if int(existing.scalar() or 0) > 0:
            continue

        body = baselines.baseline_body(kind).model_dump(mode="json")
        version = await draft(
            session,
            tenant_id=tenant_id,
            kind=kind,
            body=body,
            change_reason=baselines.SEEDED_REASON,
            actor_id=actor_id,
            correlation_id=correlation_id,
        )
        for verb in (submit_for_review, approve):
            await verb(
                session,
                tenant_id=tenant_id,
                kind=kind,
                revision=version.revision,
                reason=baselines.SEEDED_REASON,
                actor_id=actor_id,
                correlation_id=correlation_id,
            )
        await activate(
            session,
            tenant_id=tenant_id,
            kind=kind,
            revision=version.revision,
            reason=baselines.SEEDED_REASON,
            actor_id=actor_id,
            correlation_id=correlation_id,
        )
        seeded.append(version)
    return seeded


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _assert_references_resolve(
    session: AsyncSession, *, tenant_id: uuid.UUID, kind: PolicyKind, body: PolicyBody
) -> None:
    """Refuse a document naming a department or category the tenant does not have.

    A single Pydantic model cannot see this — it validates one document against
    itself, and whether ``PWD`` is a real department is a fact about other rows.
    So it lives here, and it runs at *draft* time for the reason everything else
    in Phase 6 does: a routing rule pointing at a department that does not exist
    routes into a void, and a void on a queue looks exactly like a backlog.

    Deactivated taxonomy nodes are accepted deliberately. A category can be
    retired while complaints classified into it are still open, and a rubric
    override that stops resolving the moment somebody tidies the taxonomy would
    silently change the score of work already in flight.
    """
    if isinstance(body, RoutingRules):
        wanted = {rule.department_code for rule in body.rules}
        wanted |= {rule.team_code for rule in body.rules if rule.team_code is not None}
        known = set(
            (
                await session.execute(
                    select(Department.code).where(
                        Department.tenant_id == tenant_id, Department.code.in_(wanted)
                    )
                )
            )
            .scalars()
            .all()
        )
        missing = sorted(wanted - known)
        if missing:
            raise PolicyValidationError(
                f"routing rules name departments this tenant does not have: "
                f"{', '.join(missing)}. A rule pointing at a department that does not "
                f"exist routes work into a void, which on a queue is indistinguishable "
                f"from a backlog."
            )
        return

    categories = _referenced_categories(body)
    if not categories:
        return
    known = set(
        (
            await session.execute(
                select(TaxonomyNode.key).where(
                    TaxonomyNode.tenant_id == tenant_id, TaxonomyNode.key.in_(categories)
                )
            )
        )
        .scalars()
        .all()
    )
    missing = sorted(categories - known)
    if missing:
        raise PolicyValidationError(
            f"{kind.value} names taxonomy categories this tenant does not define: "
            f"{', '.join(missing)}. The entry would never resolve, and an override "
            f"that never fires reads on screen exactly like one that works."
        )


def _referenced_categories(body: PolicyBody) -> set[str]:
    """Every taxonomy key a document mentions.

    Written per kind rather than by walking the dumped JSON for anything that
    looks like a key. A structural walk would silently start validating a field
    added later that happens to hold a similar-looking string, and the first
    symptom would be a valid document being refused.
    """
    if isinstance(body, SeverityRubric):
        return {override.category for override in body.overrides}
    if isinstance(body, DedupThresholds):
        return {band.category for band in body.bands if band.category is not None}
    if isinstance(body, SlaMatrix):
        return {entry.category for entry in body.entries if entry.category is not None}
    if isinstance(body, RateCard):
        return {category for item in body.items for category in item.categories}
    return set()


async def _reload(
    session: AsyncSession, *, tenant_id: uuid.UUID, version: PolicyVersion
) -> PolicyVersion:
    """Re-read a row this service just changed, under a tenant predicate.

    **Not ``session.refresh``.** That emits ``SELECT ... WHERE id = ?`` with no
    tenant filter, which the runtime guard correctly refuses — and would refuse
    from a stack frame nowhere near the call, since it fires on whatever
    statement the session happens to flush next. The same reasoning
    ``control_plane.taxonomy`` gives for never mutating loaded instances applies
    to reading them back: every statement states its own scope.

    Expiring first is what makes the re-read actually hit the database rather
    than returning the identity map's stale copy of a row this transaction has
    already updated out from under it.
    """
    # Read the identity *before* expiring. Touching an attribute of an expired
    # instance is itself a lazy load — `SELECT ... WHERE id = ?`, unscoped, from
    # inside an attribute access — so building the scoped query from the expired
    # object is how this function would trip the very guard it exists to satisfy.
    kind, revision = PolicyKind(version.kind), version.revision
    session.expire(version)
    return await require_version(session, tenant_id=tenant_id, kind=kind, revision=revision)


async def _next_revision(session: AsyncSession, *, tenant_id: uuid.UUID, kind: PolicyKind) -> int:
    """One past the highest revision for this kind, counting archived ones.

    Counting archived and rejected revisions is the point: revision numbers are
    identifiers, not a count of successes. Reusing the number of a rejected
    draft would make two different documents share a stamp, and
    ``severity_scored.policy_version`` would resolve to whichever survived.
    """
    highest = await session.execute(
        select(func.max(PolicyVersion.revision)).where(
            PolicyVersion.tenant_id == tenant_id, PolicyVersion.kind == kind.value
        )
    )
    return int(highest.scalar() or 0) + 1


async def _live_row(
    session: AsyncSession, *, tenant_id: uuid.UUID, kind: PolicyKind
) -> PolicyVersion | None:
    """The row holding ``active`` status, regardless of effective date.

    Different from ``active_version``, which additionally requires the effective
    date to have arrived. Activation has to displace a future-dated incumbent
    too — otherwise scheduling next month's rate card would block this month's
    correction, and the partial unique index would refuse the second activation
    with an error about an index rather than about a policy.
    """
    row = await session.execute(
        _scoped(tenant_id).where(
            PolicyVersion.kind == kind.value, PolicyVersion.status == PolicyStatus.ACTIVE.value
        )
    )
    return row.scalar_one_or_none()


def _require_editable(version: PolicyVersion) -> None:
    status = PolicyStatus(version.status)
    if status in _EDITABLE_STATUSES:
        return
    raise PolicyTransitionError(
        f"{version.kind} revision {version.revision} is {status.value} and cannot be "
        f"edited. An approver signed off on specific content; changing it afterwards "
        f"would make that signature attest to a document that no longer exists. Draft "
        f"a new revision instead."
    )


async def _transition(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    kind: PolicyKind,
    revision: int,
    to_status: PolicyStatus,
    reason: str,
    actor_id: uuid.UUID | None,
    correlation_id: str | None,
    extra: Mapping[str, Any] | None = None,
    loaded: PolicyVersion | None = None,
    superseded_revision: int | None = None,
    effective_from: datetime | None = None,
) -> PolicyVersion:
    """Move one document, checking the transition table and writing the event.

    The single mutation path for status. Every verb above delegates here, so
    there is exactly one place that can move a document, one place that consults
    ``_TRANSITIONS``, and one place that appends ``policy_transitioned`` — which
    is what makes "every transition is an event in the hash chain" a structural
    property rather than a convention four functions have to remember.
    """
    if not reason or not reason.strip():
        raise PolicyValidationError(
            "every policy transition needs a stated reason; a lifecycle record that "
            "answers 'what' and refuses to answer 'why' is the half nobody needs "
            "during an incident"
        )

    version = loaded or await require_version(
        session, tenant_id=tenant_id, kind=kind, revision=revision
    )
    from_status = PolicyStatus(version.status)
    allowed = _TRANSITIONS[from_status]
    if to_status not in allowed:
        available = ", ".join(sorted(status.value for status in allowed)) or "nothing"
        raise PolicyTransitionError(
            f"{kind.value} revision {revision} is {from_status.value}; it cannot become "
            f"{to_status.value}. Available transitions from here: {available}."
        )

    values: dict[str, Any] = {
        "status": to_status.value,
        "version": PolicyVersion.version + 1,
        **(extra or {}),
    }
    result = await session.execute(
        update(PolicyVersion)
        .where(
            PolicyVersion.tenant_id == tenant_id,
            PolicyVersion.id == version.id,
            # The status predicate is the concurrency control. Two operators
            # approving the same draft both pass the check above; the second
            # UPDATE matches no row, and reporting that as a conflict is what
            # stops the second one's event claiming a transition that never
            # happened.
            PolicyVersion.status == from_status.value,
        )
        .values(**values)
    )
    # `getattr` rather than `.rowcount`: `session.execute` is typed as returning
    # `Result`, which does not declare the attribute even though every UPDATE
    # returns a `CursorResult` that has it. The same idiom the outbox writer and
    # the webhook dispatcher use, for the same reason.
    if int(getattr(result, "rowcount", 0) or 0) == 0:
        raise PolicyConflictError(
            f"{kind.value} revision {revision} changed status concurrently; re-read and retry"
        )
    await session.flush()
    version = await _reload(session, tenant_id=tenant_id, version=version)

    await _append(
        session,
        tenant_id=tenant_id,
        event_type="policy_transitioned",
        payload={
            "kind": kind.value,
            "revision": revision,
            "from_status": from_status.value,
            "to_status": to_status.value,
            "reason": reason.strip(),
            "effective_from": effective_from.isoformat() if effective_from else None,
            "superseded_revision": superseded_revision,
        },
        actor_id=actor_id,
        correlation_id=correlation_id,
    )
    return version


async def _append(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    event_type: str,
    payload: Mapping[str, Any],
    actor_id: uuid.UUID | None,
    correlation_id: str | None,
) -> None:
    """One event on the tenant chain, in the caller's transaction."""
    await EventStore(session).append(
        entity_id=tenant_id,
        event_type=event_type,
        payload=payload,
        tenant_id=tenant_id,
        actor_id=actor_id,
        correlation_id=correlation_id,
        occurred_at=datetime.now(tz=UTC),
    )


__all__ = [
    "activate",
    "active_version",
    "approve",
    "content_hash",
    "draft",
    "get_version",
    "list_versions",
    "reject",
    "require_version",
    "rollback",
    "seed_baselines",
    "submit_for_review",
    "update_draft",
    "version_effective_at",
    "withdraw",
]
