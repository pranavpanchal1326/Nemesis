"""The §11.4 human review queue, and the only route that serves an image.

**Two surfaces in one router, and they belong together.** §11.4 requires the
reviewer to see the flagged item *and its evidence bundle*, and for a fraud flag
the bundle is largely a photograph. A queue with no way to look at the picture
is a queue where every decision is made on a distance in metres, so the media
route lives beside the queue it exists to serve.

**The media route resolves the redacted store and nothing else.** It cannot
express a quarantine path — ``RedactedStore.resolve`` refuses the scheme
explicitly — and it looks the artefact up by ``redacted_sha256`` scoped to the
tenant, so the URL contains a content address that is meaningless without a row.
``scripts/check_media_redaction.py`` asserts this is the only handler in the
repository that reads a media file at all.

**Reads are tenant-scoped and token-free; the decision write carries the
control-plane token.** The same split Phase 6 and Phase 7 use, for the same
reason: reading which of your complaints are flagged is the same class of
operation as reading which rubric is scoring them, while recording a judgement
changes what the system believes about a citizen's report and becomes a Phase 11
training label. Until Phase 13 gives operators identities the token is the
strongest control available, and ``decided_by_label`` records that honestly
rather than inventing a person.

**There is no bulk-decide endpoint, deliberately.** §11.4's value is a human
looking at the evidence; an endpoint that accepted fifty item ids and one
rationale would produce fifty Phase 11 labels backed by one glance, which is
worse than no labels — the model would learn the reviewer's fatigue.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from nemesis.api.deps import ConfigDep, SessionDep, TenantDep
from nemesis.api.errors import (
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_422_UNPROCESSABLE,
    PROBLEM_BASE,
    ProblemDetailError,
)
from nemesis.api.v1.control_plane import TokenDep, _require_token
from nemesis.db.models.trust import ReviewQueueItem, SubmissionMedia
from nemesis.observability.logging import get_correlation_id
from nemesis.trust import review as review_service
from nemesis.trust.errors import (
    MediaNotFoundError,
    ReviewConflictError,
    ReviewError,
    ReviewNotFoundError,
    ReviewValidationError,
)
from nemesis.trust.redaction import RedactedStore
from nemesis.trust.review import ReviewDecisionKind, ReviewReason

router = APIRouter(prefix="/review", tags=["review", "trust"])

#: How long a browser may keep a redacted artefact. Immutable because the URL is
#: a content address: the bytes at a given SHA-256 cannot change, so a long TTL
#: costs nothing and saves a reviewer paging a queue from re-fetching every
#: photograph. Private rather than public — this is a tenant's material behind a
#: tenant-scoped route, and a shared cache must not hold it.
MEDIA_CACHE_CONTROL = "private, max-age=86400, immutable"


class ApiModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class ReviewItem(ApiModel):
    """One queue row, with the frozen bundle §11.4 requires."""

    id: uuid.UUID
    complaint_id: uuid.UUID
    reason: str
    status: str
    priority: int
    occurrences: int
    trust_score: float
    evidence: dict[str, Any]
    #: Content addresses of this complaint's redacted artefacts, ready to append
    #: to ``/api/v1/review/media/``. The *redacted* hash, never the source: the
    #: source address would be a working handle to an unblurred image, sitting
    #: in a JSON response, one guessed route away from being served.
    redacted_media: list[str]
    created_at: str
    decided_at: str | None


class ReviewPage(ApiModel):
    items: list[ReviewItem]
    total: int
    limit: int
    offset: int


class DecisionRequest(ApiModel):
    decision: ReviewDecisionKind
    rationale: str = Field(min_length=1, max_length=2000)
    #: Who decided, when the caller knows. Phase 13 replaces this with the
    #: authenticated operator; until then it is recorded as supplied and the
    #: field name says it is a label rather than an identity.
    decided_by_label: str = Field(default="control-plane token", max_length=128)


class DecisionResponse(ApiModel):
    id: uuid.UUID
    review_item_id: uuid.UUID
    complaint_id: uuid.UUID
    reason: str
    decision: str
    rationale: str
    decided_by_label: str
    evidence_hash: str
    created_at: str


@router.get("/queue", response_model=ReviewPage, summary="The §11.4 review queue")
async def list_queue(
    tenant: TenantDep,
    session: SessionDep,
    status_filter: Annotated[
        str | None,
        Query(
            alias="status",
            description="'open' (default), 'decided', or omit with all=true for both.",
        ),
    ] = "open",
    reason: Annotated[ReviewReason | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ReviewPage:
    """Open items first, by priority then age. See ``review.list_items``."""
    if status_filter not in (None, "open", "decided"):
        raise ProblemDetailError(
            status_code=HTTP_422_UNPROCESSABLE,
            title="Unknown review status",
            detail=(
                f"status must be 'open' or 'decided', not {status_filter!r}. The queue "
                f"has two states on purpose — 'in progress' is a claim about a person, "
                f"which Phase 13 owns along with the person."
            ),
            problem_type=f"{PROBLEM_BASE}/review-status-unknown",
        )

    rows, total = await review_service.list_items(
        session,
        tenant_id=tenant.id,
        status=status_filter,
        reason=reason,
        limit=limit,
        offset=offset,
    )
    media = await _redacted_media_for(session, tenant_id=tenant.id, items=rows)
    return ReviewPage(
        items=[_to_item(row, media.get(row.complaint_id, [])) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/queue/{item_id}", response_model=ReviewItem, summary="One item and its bundle")
async def get_queue_item(
    item_id: uuid.UUID,
    tenant: TenantDep,
    session: SessionDep,
) -> ReviewItem:
    try:
        item = await review_service.get_item(session, tenant_id=tenant.id, item_id=item_id)
    except ReviewError as exc:
        raise _problem(exc) from exc
    media = await _redacted_media_for(session, tenant_id=tenant.id, items=[item])
    return _to_item(item, media.get(item.complaint_id, []))


@router.post(
    "/queue/{item_id}/decision",
    response_model=DecisionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record the human judgement (§11.4), and the Phase 11 label",
)
async def decide_item(
    item_id: uuid.UUID,
    body: DecisionRequest,
    tenant: TenantDep,
    session: SessionDep,
    settings: ConfigDep,
    token: TokenDep = None,
) -> DecisionResponse:
    """Approve, reject, or escalate. One judgement per item, ever."""
    _require_token(settings, token)
    try:
        record = await review_service.decide(
            session,
            tenant_id=tenant.id,
            item_id=item_id,
            decision=body.decision,
            rationale=body.rationale,
            decided_by_label=body.decided_by_label,
            correlation_id=get_correlation_id(),
        )
    except ReviewError as exc:
        raise _problem(exc) from exc

    return DecisionResponse(
        id=record.id,
        review_item_id=record.review_item_id,
        complaint_id=record.complaint_id,
        reason=record.reason,
        decision=record.decision,
        rationale=record.rationale,
        decided_by_label=record.decided_by_label,
        evidence_hash=record.evidence_hash,
        created_at=record.created_at.isoformat(),
    )


@router.get(
    "/media/{redacted_sha256}",
    summary="A redacted artefact — the only image any route serves",
    responses={200: {"content": {"image/jpeg": {}}}},
)
async def get_media(
    redacted_sha256: str,
    tenant: TenantDep,
    session: SessionDep,
    settings: ConfigDep,
) -> Response:
    """Serve one blurred image, looked up by content address within the tenant.

    **The lookup goes through ``submission_media`` and not straight to the
    filesystem**, and that is the tenant boundary. The redacted root is one
    directory shared by every tenant on a deployment — content-addressed storage
    deduplicates identical bytes by design — so resolving the path from the URL
    alone would let any tenant fetch any other tenant's photograph by guessing
    or by observing a hash. The row is what says this artefact belongs here.
    """
    if len(redacted_sha256) != 64 or not all(
        character in "0123456789abcdef" for character in redacted_sha256
    ):
        # Rejected before touching the database or the disk. The value is a path
        # component, and ``RedactedStore.resolve`` already refuses traversal —
        # but a check that happens before any I/O is one an auditor can see.
        raise ProblemDetailError(
            status_code=HTTP_404_NOT_FOUND,
            title="Media not found",
            problem_type=f"{PROBLEM_BASE}/media-not-found",
        )

    row = (
        await session.execute(
            select(SubmissionMedia.redacted_uri, SubmissionMedia.content_type).where(
                SubmissionMedia.tenant_id == tenant.id,
                SubmissionMedia.redacted_sha256 == redacted_sha256,
            )
        )
    ).first()
    if row is None or row.redacted_uri is None:
        raise ProblemDetailError(
            status_code=HTTP_404_NOT_FOUND,
            title="Media not found",
            problem_type=f"{PROBLEM_BASE}/media-not-found",
        )

    try:
        path = RedactedStore(settings.upload_dir).resolve(row.redacted_uri)
    except MediaNotFoundError as exc:
        # 404 rather than 500: the §22.4 retention sweep removing a raw artefact
        # is expected behaviour, and an operator reading a 500 goes looking for a
        # broken deployment instead of an expired one.
        raise ProblemDetailError(
            status_code=HTTP_404_NOT_FOUND,
            title="Media not found",
            detail=str(exc),
            problem_type=f"{PROBLEM_BASE}/media-not-found",
        ) from exc

    return Response(
        content=path.read_bytes(),
        media_type=row.content_type,
        headers={
            "Cache-Control": MEDIA_CACHE_CONTROL,
            # The bytes are an image and nothing else. Without this a crafted
            # upload that sniffs as an image and parses as HTML would execute in
            # the reviewer's session — the classic stored-XSS-via-upload, on the
            # one route in this system that returns attacker-influenced bytes.
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'inline; filename="{redacted_sha256[:16]}.jpg"',
        },
    )


async def _redacted_media_for(
    session: SessionDep, *, tenant_id: uuid.UUID, items: Any
) -> dict[uuid.UUID, list[str]]:
    """Redacted content addresses per complaint, in one query for the page.

    One query rather than one per row: a fifty-item page would otherwise make
    fifty round trips to render a list, which is the N+1 that turns a queue
    screen into a slow one exactly when the queue is long.
    """
    complaint_ids = [item.complaint_id for item in items]
    if not complaint_ids:
        return {}
    rows = await session.execute(
        select(SubmissionMedia.complaint_id, SubmissionMedia.redacted_sha256).where(
            SubmissionMedia.tenant_id == tenant_id,
            SubmissionMedia.complaint_id.in_(complaint_ids),
            SubmissionMedia.redacted_sha256.is_not(None),
        )
    )
    grouped: dict[uuid.UUID, list[str]] = {}
    for row in rows:
        grouped.setdefault(row.complaint_id, []).append(row.redacted_sha256)
    return grouped


def _to_item(row: ReviewQueueItem, media: list[str]) -> ReviewItem:
    return ReviewItem(
        id=row.id,
        complaint_id=row.complaint_id,
        reason=row.reason,
        status=row.status,
        priority=row.priority,
        occurrences=row.occurrences,
        trust_score=row.trust_score,
        evidence=dict(row.evidence),
        redacted_media=media,
        created_at=row.created_at.isoformat(),
        decided_at=row.decided_at.isoformat() if row.decided_at else None,
    )


#: Error type → status. A table rather than a chain of ``isinstance`` branches,
#: for the reason the policy router gives: a new error type that nobody maps
#: surfaces as a 500, and a table makes the omission visible in review.
_STATUS: dict[type[ReviewError], int] = {
    ReviewNotFoundError: HTTP_404_NOT_FOUND,
    ReviewConflictError: HTTP_409_CONFLICT,
    ReviewValidationError: HTTP_422_UNPROCESSABLE,
}


def _title_of(error_type: type[ReviewError]) -> str:
    """The error class's first docstring line, as the Problem Details title.

    A fallback rather than an assertion because ``python -O`` strips docstrings:
    the RFC 9457 contract must not depend on how the interpreter was invoked.
    """
    doc = error_type.__doc__
    return doc.splitlines()[0].strip() if doc else "Review error"


def _problem(exc: ReviewError) -> ProblemDetailError:
    status_code = _STATUS.get(type(exc), HTTP_422_UNPROCESSABLE)
    slug = type(exc).__name__.removesuffix("Error").lower()
    return ProblemDetailError(
        status_code=status_code,
        title=_title_of(type(exc)),
        detail=str(exc),
        problem_type=f"{PROBLEM_BASE}/review-{slug}",
    )


__all__ = ["router"]
