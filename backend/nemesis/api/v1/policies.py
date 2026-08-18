"""The policy HTTP surface — Phase 6's "no deploy" made literal.

Mounted under ``/api/v1/control-plane/policies`` and following the control
plane's conventions exactly, because it *is* the control plane: **reads are
tenant-scoped and token-free, writes carry the control-plane token.** Reading
which rubric is scoring your complaints is the same class of operation as
reading your own taxonomy. Changing one redefines what every future complaint
scores, which is at least as consequential as editing the taxonomy and carries
the same shared secret until Phase 13 replaces it with an operator identity.

**Why this is a separate module from ``control_plane.py``** rather than sixteen
more endpoints in an already 870-line file: the two have different error
vocabularies. ``ControlPlaneError`` and ``PolicyError`` are deliberately
unrelated hierarchies (see ``policy.errors``), so one file would need two
translation functions and a reader would have to check which applied to each
handler. The token check and the tenant resolution are imported rather than
duplicated.

**Activation invalidates the local cache and says what it did not do.** The
resolver's TTL is the real propagation latency across processes, and the
response states it. An operator who activates a rubric and immediately re-reads
sees the new one because this process dropped its own cache entry; the workers
pick it up within the reload interval. Reporting "activated" without that
distinction is how somebody concludes the button is broken and presses it four
more times.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.api.deps import ConfigDep, SessionDep, TenantDep
from nemesis.api.errors import (
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_422_UNPROCESSABLE,
    PROBLEM_BASE,
    ProblemDetailError,
)
from nemesis.api.v1.control_plane import TokenDep, _require_token
from nemesis.db.models.policy import PolicyVersion
from nemesis.observability.logging import get_correlation_id
from nemesis.policy import baselines, service
from nemesis.policy.documents import PolicyKind, PolicyStatus
from nemesis.policy.errors import (
    PolicyConflictError,
    PolicyError,
    PolicyNotFoundError,
    PolicyTransitionError,
)
from nemesis.policy.expressions import ROUTING_FACTS
from nemesis.policy.resolver import DEFAULT_RELOAD_SECONDS, RESOLVER
from nemesis.tenancy.context import tenant_scope

router = APIRouter(prefix="/control-plane/policies", tags=["control-plane", "policy"])


# ---------------------------------------------------------------------------
# Response models — declared, never assembled as bare dicts
# ---------------------------------------------------------------------------


class ApiModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class PolicyVersionSummary(ApiModel):
    """A version without its body.

    Listing endpoints return these. A safety ruleset is kilobytes of terms and a
    revision list is dozens of rows; returning every body would make the review
    queue a megabyte-scale response for a screen that shows status and a date.
    """

    kind: PolicyKind
    revision: int
    status: PolicyStatus
    content_hash: str
    change_reason: str
    change_summary: str | None
    effective_from: datetime | None
    effective_until: datetime | None
    based_on_revision: int | None
    rolled_back_from_revision: int | None
    submitted_at: datetime | None
    approved_at: datetime | None
    activated_at: datetime | None
    rejection_reason: str | None
    created_at: datetime


class PolicyVersionDetail(PolicyVersionSummary):
    """A version with its body, for the editor and for an audit."""

    body: dict[str, Any]


class ActivePolicyResponse(ApiModel):
    """What is deciding right now, for one kind.

    ``is_baseline`` is surfaced rather than hidden behind a plausible-looking
    revision number. A tenant that has never been seeded is running on platform
    defaults, and an operator looking at this screen needs to know that — it is
    the difference between "our rubric" and "the one that came in the box".
    """

    kind: PolicyKind
    stamp: str
    revision: int | None
    content_hash: str
    is_baseline: bool
    body: dict[str, Any]


class ReloadNotice(ApiModel):
    """How long until this change is live everywhere.

    Returned on every activation and rollback because the honest answer is not
    "immediately". This process dropped its cache; other workers refresh on
    their own TTL. Stating it is what stops an operator concluding the button
    did nothing.
    """

    local_cache_invalidated: bool = True
    reload_interval_seconds: float = DEFAULT_RELOAD_SECONDS
    detail: str = (
        "This process now serves the new version. Other workers pick it up "
        "within one reload interval."
    )


class ActivationResponse(ApiModel):
    version: PolicyVersionDetail
    superseded_revision: int | None
    reload: ReloadNotice = ReloadNotice()


class SeedResponse(ApiModel):
    seeded_kinds: list[PolicyKind]
    skipped_kinds: list[PolicyKind]


class FactResponse(ApiModel):
    name: str
    kind: str
    description: str


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class PolicyRequestModel(BaseModel):
    """``extra="forbid"`` for the reason every control-plane input uses it: a
    misspelled field in a policy write must fail rather than be dropped."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DraftRequest(PolicyRequestModel):
    body: dict[str, Any]
    change_reason: str = Field(min_length=1, max_length=2000)
    change_summary: str | None = Field(default=None, max_length=4000)
    #: Which revision this was edited from, when the editor loaded one. Recorded
    #: rather than inferred, so a revision chain survives a draft being archived.
    based_on_revision: int | None = Field(default=None, ge=1)


class UpdateDraftRequest(PolicyRequestModel):
    body: dict[str, Any]
    change_reason: str | None = Field(default=None, max_length=2000)


class TransitionRequest(PolicyRequestModel):
    reason: str = Field(min_length=1, max_length=2000)


class ActivateRequest(TransitionRequest):
    #: Future-dating is supported — a rate card negotiated in March to apply
    #: from April. Back-dating is refused by the service; see ``activate``.
    effective_from: datetime | None = None


class RollbackRequest(TransitionRequest):
    to_revision: int = Field(ge=1)


# ---------------------------------------------------------------------------
# Error translation
# ---------------------------------------------------------------------------


def _translate(error: PolicyError) -> ProblemDetailError:
    """One mapping from policy errors to the RFC 9457 contract.

    ``PolicyTransitionError`` is a 409 rather than a 422, and the distinction is
    for the client: 422 says "fix the body and resend", which for an illegal
    transition can never work. 409 says "the resource is not in a state where
    this applies", which is the truth, and the detail names the transitions that
    are available.
    """
    if isinstance(error, PolicyNotFoundError):
        return ProblemDetailError(
            status_code=HTTP_404_NOT_FOUND,
            title="Not found",
            detail=str(error),
            problem_type=f"{PROBLEM_BASE}/not-found",
        )
    if isinstance(error, PolicyConflictError | PolicyTransitionError):
        return ProblemDetailError(
            status_code=HTTP_409_CONFLICT,
            title="Conflict",
            detail=str(error),
            problem_type=f"{PROBLEM_BASE}/conflict",
        )
    return ProblemDetailError(
        status_code=HTTP_422_UNPROCESSABLE,
        title="Policy request rejected",
        detail=str(error),
        problem_type=f"{PROBLEM_BASE}/validation-error",
    )


def _summary(version: PolicyVersion, *, revisions: dict[uuid.UUID, int]) -> PolicyVersionSummary:
    return PolicyVersionSummary(
        kind=PolicyKind(version.kind),
        revision=version.revision,
        status=PolicyStatus(version.status),
        content_hash=version.content_hash,
        change_reason=version.change_reason,
        change_summary=version.change_summary,
        effective_from=version.effective_from,
        effective_until=version.effective_until,
        based_on_revision=revisions.get(version.based_on_id) if version.based_on_id else None,
        rolled_back_from_revision=(
            revisions.get(version.rolled_back_from_id) if version.rolled_back_from_id else None
        ),
        submitted_at=version.submitted_at,
        approved_at=version.approved_at,
        activated_at=version.activated_at,
        rejection_reason=version.rejection_reason,
        created_at=version.created_at,
    )


def _detail(version: PolicyVersion, *, revisions: dict[uuid.UUID, int]) -> PolicyVersionDetail:
    return PolicyVersionDetail(
        **_summary(version, revisions=revisions).model_dump(), body=dict(version.body)
    )


async def _revision_index(
    session: AsyncSession, *, tenant_id: uuid.UUID, versions: list[PolicyVersion]
) -> dict[uuid.UUID, int]:
    """Resolve ancestry ids to revision numbers.

    The API speaks revisions, not UUIDs — an operator quotes "revision 7", and a
    response full of ids for the same information is a response nobody can read
    aloud during an incident. Built from rows already fetched where possible,
    with one extra query for ancestors outside the page.
    """
    index = {version.id: version.revision for version in versions}
    wanted = {
        ancestor
        for version in versions
        for ancestor in (version.based_on_id, version.rolled_back_from_id)
        if ancestor is not None and ancestor not in index
    }
    if not wanted:
        return index

    rows = await session.execute(
        select(PolicyVersion.id, PolicyVersion.revision).where(
            PolicyVersion.tenant_id == tenant_id, PolicyVersion.id.in_(wanted)
        )
    )
    for identifier, revision in rows.all():
        index[identifier] = revision
    return index


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


@router.get("/facts", summary="The vocabulary routing conditions may use")
async def list_facts() -> list[FactResponse]:
    """Read-only and tenant-free: the fact schema is a property of the build.

    Exists so a policy editor can offer autocompletion and so an author can
    discover the vocabulary without reading the source. It is the same schema
    ``RoutingRule`` validates against, so a name that appears here is a name
    that will compile.
    """
    return [
        FactResponse(name=fact.name, kind=fact.kind.value, description=fact.description)
        for fact in sorted(ROUTING_FACTS.facts, key=lambda fact: fact.name)
    ]


@router.get("", summary="Policy revision history")
async def list_policies(
    tenant: TenantDep,
    session: SessionDep,
    kind: Annotated[PolicyKind | None, Query()] = None,
    policy_status: Annotated[PolicyStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[PolicyVersionSummary]:
    """Every revision the tenant has, newest first within each kind."""
    with tenant_scope(tenant.id):
        versions = await service.list_versions(
            session,
            tenant_id=tenant.id,
            kind=kind,
            statuses=[policy_status] if policy_status else None,
            limit=limit,
        )
        revisions = await _revision_index(session, tenant_id=tenant.id, versions=versions)
    return [_summary(version, revisions=revisions) for version in versions]


@router.get("/{kind}/active", summary="The document currently deciding")
async def get_active(
    kind: PolicyKind,
    tenant: TenantDep,
    session: SessionDep,
) -> ActivePolicyResponse:
    """What this kind resolves to right now, baseline included.

    Deliberately routed through the resolver rather than querying the table, so
    what this returns is *by construction* what the pipeline reads — including
    the baseline fallback and the effective-date filter. An endpoint that ran
    its own query would be able to disagree with the thing it claims to report.
    """
    with tenant_scope(tenant.id):
        resolved = await RESOLVER.document(session, tenant_id=tenant.id, kind=kind)
    if resolved is None:
        raise ProblemDetailError(
            status_code=HTTP_404_NOT_FOUND,
            title="No active policy",
            detail=(
                f"This tenant has no active {kind.value}, and the platform has no "
                f"baseline for it because it names tenant-specific things. Author and "
                f"activate one."
            ),
            problem_type=f"{PROBLEM_BASE}/not-found",
        )
    return ActivePolicyResponse(
        kind=kind,
        stamp=resolved.stamp,
        revision=resolved.revision,
        content_hash=resolved.content_hash,
        is_baseline=resolved.is_baseline,
        body=resolved.body.model_dump(mode="json"),
    )


@router.get("/{kind}/{revision}", summary="One revision, with its body")
async def get_version(
    kind: PolicyKind,
    revision: int,
    tenant: TenantDep,
    session: SessionDep,
) -> PolicyVersionDetail:
    with tenant_scope(tenant.id):
        try:
            version = await service.require_version(
                session, tenant_id=tenant.id, kind=kind, revision=revision
            )
        except PolicyError as exc:
            raise _translate(exc) from exc
        revisions = await _revision_index(session, tenant_id=tenant.id, versions=[version])
    return _detail(version, revisions=revisions)


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


@router.post("/seed-baselines", summary="Give a tenant the platform baseline documents")
async def seed_baselines(
    tenant: TenantDep,
    session: SessionDep,
    settings: ConfigDep,
    token: TokenDep = None,
) -> SeedResponse:
    """The backfill path for tenants provisioned before Phase 6.

    Idempotent: a kind that already has any revision is skipped, so running this
    across a fleet twice is safe and it cannot clobber a tenant that has since
    tuned its own rubric. New tenants get this automatically at provisioning —
    this endpoint exists for the ones that predate the table.
    """
    with tenant_scope(tenant.id):
        _require_token(settings, token)
        seeded = await service.seed_baselines(
            session, tenant_id=tenant.id, correlation_id=get_correlation_id()
        )
    seeded_kinds = [PolicyKind(version.kind) for version in seeded]
    RESOLVER.invalidate(tenant_id=tenant.id)
    return SeedResponse(
        seeded_kinds=seeded_kinds,
        skipped_kinds=[kind for kind in baselines.SEEDED_KINDS if kind not in seeded_kinds],
    )


@router.post(
    "/{kind}",
    status_code=status.HTTP_201_CREATED,
    summary="Draft a new revision",
    responses={
        403: {"description": "Control-plane token missing or wrong"},
        422: {"description": "The document is not valid for its kind"},
    },
)
async def draft_policy(
    kind: PolicyKind,
    request: DraftRequest,
    tenant: TenantDep,
    session: SessionDep,
    settings: ConfigDep,
    token: TokenDep = None,
) -> PolicyVersionDetail:
    """Author a revision. It is a draft, and drafts decide nothing.

    The body is validated against the kind's model here, which is why a routing
    document with an unparseable condition fails at this call rather than
    silently at 2am — see ``policy.expressions`` on why that is the whole point
    of compiling at document-validation time.
    """
    _require_token(settings, token)
    with tenant_scope(tenant.id):
        try:
            version = await service.draft(
                session,
                tenant_id=tenant.id,
                kind=kind,
                body=request.body,
                change_reason=request.change_reason,
                change_summary=request.change_summary,
                based_on_revision=request.based_on_revision,
                correlation_id=get_correlation_id(),
            )
        except PolicyError as exc:
            raise _translate(exc) from exc
        revisions = await _revision_index(session, tenant_id=tenant.id, versions=[version])
    return _detail(version, revisions=revisions)


@router.put("/{kind}/{revision}", summary="Replace a draft's body")
async def update_draft(
    kind: PolicyKind,
    revision: int,
    request: UpdateDraftRequest,
    tenant: TenantDep,
    session: SessionDep,
    settings: ConfigDep,
    token: TokenDep = None,
) -> PolicyVersionDetail:
    """Editable only while it is a draft. See ``service.update_draft``."""
    _require_token(settings, token)
    with tenant_scope(tenant.id):
        try:
            version = await service.update_draft(
                session,
                tenant_id=tenant.id,
                kind=kind,
                revision=revision,
                body=request.body,
                change_reason=request.change_reason,
                correlation_id=get_correlation_id(),
            )
        except PolicyError as exc:
            raise _translate(exc) from exc
        revisions = await _revision_index(session, tenant_id=tenant.id, versions=[version])
    return _detail(version, revisions=revisions)


@router.post(
    "/{kind}/{revision}/activate",
    summary="Make an approved revision live",
    responses={409: {"description": "Not approved, or already active"}},
)
async def activate_policy(
    kind: PolicyKind,
    revision: int,
    request: ActivateRequest,
    tenant: TenantDep,
    session: SessionDep,
    settings: ConfigDep,
    token: TokenDep = None,
) -> ActivationResponse:
    """Supersede whatever was live and put this in its place.

    The cache invalidation happens *after* the service call returns, so a failed
    activation cannot leave this process serving a version the database rejected.
    """
    _require_token(settings, token)
    with tenant_scope(tenant.id):
        try:
            incumbent = await service.active_version(session, tenant_id=tenant.id, kind=kind)
            superseded = incumbent.revision if incumbent is not None else None
            version = await service.activate(
                session,
                tenant_id=tenant.id,
                kind=kind,
                revision=revision,
                reason=request.reason,
                effective_from=request.effective_from,
                correlation_id=get_correlation_id(),
            )
        except PolicyError as exc:
            raise _translate(exc) from exc
        revisions = await _revision_index(session, tenant_id=tenant.id, versions=[version])
    RESOLVER.invalidate(tenant_id=tenant.id)
    return ActivationResponse(
        version=_detail(version, revisions=revisions), superseded_revision=superseded
    )


@router.post(
    "/{kind}/rollback",
    summary="Restore a previously live revision",
    responses={409: {"description": "The target revision was never approved"}},
)
async def rollback_policy(
    kind: PolicyKind,
    request: RollbackRequest,
    tenant: TenantDep,
    session: SessionDep,
    settings: ConfigDep,
    token: TokenDep = None,
) -> ActivationResponse:
    """Create a new revision carrying an old one's content, and activate it.

    Forward-only. The response's ``version`` is the *new* revision, not the one
    named in the request — which is what an operator needs to see, because that
    is the number every subsequent decision will be stamped with.
    """
    _require_token(settings, token)
    with tenant_scope(tenant.id):
        try:
            incumbent = await service.active_version(session, tenant_id=tenant.id, kind=kind)
            superseded = incumbent.revision if incumbent is not None else None
            version = await service.rollback(
                session,
                tenant_id=tenant.id,
                kind=kind,
                to_revision=request.to_revision,
                reason=request.reason,
                correlation_id=get_correlation_id(),
            )
        except PolicyError as exc:
            raise _translate(exc) from exc
        revisions = await _revision_index(session, tenant_id=tenant.id, versions=[version])
    RESOLVER.invalidate(tenant_id=tenant.id)
    return ActivationResponse(
        version=_detail(version, revisions=revisions), superseded_revision=superseded
    )


# ---------------------------------------------------------------------------
# Review transitions — registered last, deliberately
# ---------------------------------------------------------------------------
#
# FastAPI matches routes in registration order, and `/{kind}/{revision}/{verb}`
# is a catch-all over that shape. Declared above `activate` it would swallow it,
# and the endpoint that changes what production does would be reachable by
# varying a path segment on the review endpoint. Registering it last is what
# makes the specific routes win.

#: The lifecycle verbs that are a plain status move, mapped to their service
#: function. A table rather than four near-identical handlers: the bodies would
#: differ only in which function they call, and four copies of the same
#: try/except is four places for the error translation to drift.
_SIMPLE_TRANSITIONS = {
    "submit": service.submit_for_review,
    "approve": service.approve,
    "reject": service.reject,
    "withdraw": service.withdraw,
}


@router.post(
    "/{kind}/{revision}/{verb}",
    summary="Move a revision through review",
    responses={409: {"description": "Not a legal transition from the current status"}},
)
async def transition_policy(
    kind: PolicyKind,
    revision: int,
    verb: str,
    request: TransitionRequest,
    tenant: TenantDep,
    session: SessionDep,
    settings: ConfigDep,
    token: TokenDep = None,
) -> PolicyVersionSummary:
    """``submit``, ``approve``, ``reject``, or ``withdraw``.

    Activation and rollback are separate endpoints rather than more verbs here,
    because they take different request bodies and — more importantly — because
    they are the two that change what production does. An endpoint that can
    activate should not be reachable by varying a path segment.
    """
    _require_token(settings, token)
    action = _SIMPLE_TRANSITIONS.get(verb)
    if action is None:
        raise ProblemDetailError(
            status_code=HTTP_404_NOT_FOUND,
            title="Unknown transition",
            detail=(
                f"{verb!r} is not a review transition. Available: "
                f"{', '.join(sorted(_SIMPLE_TRANSITIONS))}. Activation and rollback "
                f"have their own endpoints."
            ),
            problem_type=f"{PROBLEM_BASE}/not-found",
        )
    with tenant_scope(tenant.id):
        try:
            version = await action(
                session,
                tenant_id=tenant.id,
                kind=kind,
                revision=revision,
                reason=request.reason,
                correlation_id=get_correlation_id(),
            )
        except PolicyError as exc:
            raise _translate(exc) from exc
        revisions = await _revision_index(session, tenant_id=tenant.id, versions=[version])
    return _summary(version, revisions=revisions)
