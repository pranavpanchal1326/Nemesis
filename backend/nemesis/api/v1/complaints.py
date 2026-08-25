"""§26.1 submission and §26.2 retrieval.

**Why the submission handler is short.** Everything it does that could be wrong
lives somewhere testable without HTTP: the media store enforces the cap and
sniffs the type, the ingest service owns the transaction, the limiter owns the
budget. What is left here is translation — form fields in, problem details or a
202 out — which is exactly as much as a route handler should be.

**Why the GET carries an ETag.** §27.3's documented fallback for a dead
WebSocket is the client polling this endpoint every five seconds. Without a
conditional request that is a full projection read and a full JSON render per
client per five seconds, for a resource that changes perhaps six times in its
life. The ETag is the projection's ``version`` — the log position the row
reflects — which is not a hash of the body but the thing the body is derived
from, so it is exactly as strong as the representation and free to compute.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Final

from fastapi import APIRouter, File, Form, Header, Query, Request, Response, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from geoalchemy2 import Geometry
from sqlalchemy import cast, func, select

from nemesis.api.deps import ConfigDep, SessionDep, TenantContext, TenantDep
from nemesis.api.errors import (
    HTTP_404_NOT_FOUND,
    HTTP_413_CONTENT_TOO_LARGE,
    HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    HTTP_422_UNPROCESSABLE,
    HTTP_429_TOO_MANY_REQUESTS,
    PROBLEM_BASE,
    ProblemDetailError,
)
from nemesis.api.ratelimit import get_limiter
from nemesis.api.v1.schemas import (
    ComplaintHistory,
    ComplaintHistoryEvent,
    ComplaintResponse,
    ComplaintSubmissionResponse,
)
from nemesis.config import Settings
from nemesis.db.models.complaint import Complaint, ComplaintCluster
from nemesis.db.models.event import Event, EventChainHead
from nemesis.db.models.work_order import WorkOrder
from nemesis.domain.lifecycle import EntityType
from nemesis.events.catalog import Latitude, Longitude
from nemesis.events.disclosure import history_payload
from nemesis.events.hashing import format_timestamp
from nemesis.ingest.media import (
    MediaStore,
    StoredMedia,
    UnsupportedMediaError,
    UploadError,
    UploadTooLargeError,
)
from nemesis.ingest.service import Submission, submit
from nemesis.observability import metrics
from nemesis.observability.logging import get_correlation_id
from nemesis.pipeline.tasks import dispatch_pipeline
from nemesis.policy.resolver import RESOLVER
from nemesis.tenancy.context import tenant_scope
from nemesis.trust import exif

router = APIRouter(tags=["complaints"])

IDEMPOTENCY_HEADER: Final = "Idempotency-Key"

#: §26.1: "required if audio absent" / "required if photo absent". Stated here
#: because the rule is about the *pair*, which no single field validator can
#: express.
_NO_MEDIA_DETAIL: Final = (
    "A submission needs at least one of photo or audio. A report with neither "
    "has no evidence to verify, cluster, or score."
)


@router.post(
    "/complaints",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit a complaint",
    responses={
        # Declared here rather than as `response_model=` because these handlers
        # return a raw ``Response`` — they own their own ETag, 304 and status
        # code. FastAPI cannot infer a schema from that, so without this the
        # published contract for the two hottest reads in the product is
        # literally `{}`: `api_contract_lock.json` records an empty response and
        # protects nothing, and a generated TypeScript client gets `unknown`.
        #
        # The frontend's Law 2 (docs/FRONTEND-EXECUTION-PLAN.md) is that no
        # screen is ever built against a shape the client invented. That law is
        # only enforceable if the shape is published. The model already existed;
        # only its declaration was missing.
        202: {"model": ComplaintSubmissionResponse, "description": "Accepted"},
        413: {"description": "Upload exceeded the size cap"},
        415: {"description": "Unsupported media type"},
        429: {"description": "Rate limit exceeded"},
    },
)
async def submit_complaint(
    request: Request,
    tenant: TenantDep,
    session: SessionDep,
    settings: ConfigDep,
    latitude: Annotated[Latitude, Form()],
    longitude: Annotated[Longitude, Form()],
    device_fingerprint: Annotated[str, Form(max_length=128)],
    photo: Annotated[UploadFile | None, File()] = None,
    audio: Annotated[UploadFile | None, File()] = None,
    description_text: Annotated[str | None, Form()] = None,
    locale: Annotated[str | None, Form(max_length=35)] = None,
    idempotency_key: Annotated[str | None, Header(alias=IDEMPOTENCY_HEADER)] = None,
) -> Response:
    """Accept a report, acknowledge it, and hand it to the pipeline.

    202 rather than 201: §27.1 budgets the acknowledgment at under two seconds
    and the full pipeline at up to thirty, so the resource is *accepted*, not
    complete. Returning 201 would tell every well-behaved HTTP client that the
    representation at the returned location is final, which it is not for
    another half minute.
    """
    correlation_id = get_correlation_id()

    await _enforce_rate_limit(
        settings=settings, tenant=tenant, device_fingerprint=device_fingerprint, request=request
    )

    max_chars = settings.ingest.max_description_chars
    if description_text is not None and len(description_text) > max_chars:
        metrics.ingest_submissions_total.labels(outcome="rejected").inc()
        raise ProblemDetailError(
            status_code=HTTP_422_UNPROCESSABLE,
            title="Description too long",
            detail=f"description_text is limited to {max_chars} characters.",
            problem_type=f"{PROBLEM_BASE}/validation-error",
        )

    if photo is None and audio is None:
        metrics.ingest_submissions_total.labels(outcome="rejected").inc()
        raise ProblemDetailError(
            status_code=HTTP_422_UNPROCESSABLE,
            title="No evidence supplied",
            detail=_NO_MEDIA_DETAIL,
            problem_type=f"{PROBLEM_BASE}/validation-error",
        )

    store = MediaStore(settings.upload_dir)
    stored_photo = await _store_upload(
        store, photo, allowed=settings.ingest.allowed_image_types, settings=settings, field="photo"
    )
    stored_audio = await _store_upload(
        store, audio, allowed=settings.ingest.allowed_audio_types, settings=settings, field="audio"
    )
    # A photo that stored successfully before an audio part was rejected stays in
    # quarantine, unreferenced. That is deliberate, and the tempting fix is
    # worse: the store is content-addressed, so deleting "the file this request
    # wrote" can delete the file a *different* complaint references, because
    # identical bytes are one file. Trading an orphan for the possible deletion
    # of another citizen's evidence is not a trade worth making at this layer.
    # Phase 8 already has to walk quarantine to blur and promote, and reclaiming
    # what the log does not reference belongs in that sweep, where the log is
    # the authority on what is referenced.

    await _enforce_live_capture(
        session, tenant=tenant, settings=settings, stored_photo=stored_photo
    )

    receipt = await submit(
        tenant_id=tenant.id,
        submission=Submission(
            latitude=latitude,
            longitude=longitude,
            description_text=description_text,
            photo_uri=None if stored_photo is None else stored_photo.uri,
            audio_uri=None if stored_audio is None else stored_audio.uri,
            locale=locale or tenant.primary_locale,
            device_fingerprint=device_fingerprint,
            submitted_via="web",
        ),
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
    )

    if not receipt.duplicate:
        # After the commit, never inside it — a worker that picks the task up
        # first would replay a complaint that does not exist yet.
        #
        # In a thread, because `apply_async` is *synchronous* Redis I/O. Called
        # directly it blocks this process's event loop for the round trip, which
        # stalls not just this request but every other request the process is
        # serving — and §27.1 budgets the acknowledgment at under two seconds.
        # A broker that is merely slow would turn one submission into a
        # process-wide latency spike.
        await run_in_threadpool(
            dispatch_pipeline,
            tenant_id=tenant.id,
            complaint_id=receipt.complaint_id,
            correlation_id=correlation_id,
        )

    body = {
        "complaint_id": str(receipt.complaint_id),
        "status": receipt.status,
        "estimated_processing_time_seconds": settings.ingest.estimated_processing_seconds,
        # §E17.3's receipt, ADR-0044. The head is already in hand from the
        # append that just happened; reading `event_chain_heads` again to
        # recover it would be a round trip for a value this request computed.
        "chain_hash": receipt.chain_hash,
    }
    response = Response(
        content=_json(body),
        status_code=status.HTTP_202_ACCEPTED,
        media_type="application/json",
    )
    response.headers["Location"] = f"/api/v1/complaints/{receipt.complaint_id}"
    if receipt.duplicate:
        # An explicit, greppable signal that the key matched. A client retrying
        # after a timeout needs to distinguish "my retry created a second
        # report" from "my first attempt had already landed".
        response.headers["Idempotent-Replay"] = "true"
    return response


async def _enforce_live_capture(
    session: SessionDep,
    *,
    tenant: TenantContext,
    settings: Settings,
    stored_photo: StoredMedia | None,
) -> None:
    """§11.1's live-capture-only mode — the *real* control for stripped EXIF.

    §11.1 is explicit that absent EXIF must reduce trust rather than reject, and
    then equally explicit about what to do when a tenant needs more than reduced
    trust: **require live camera capture and block gallery upload**. Those are
    two different controls and this is the second one. It is off by default,
    because turning it on excludes every citizen whose phone or browser cannot
    do it — an equity decision (§23) a tenant makes rather than a default the
    platform imposes.

    **Why it is here and not in the trust stage.** A block has to happen where
    the submitter is still listening. Rejecting in the pipeline would accept the
    report with a 202, tell the citizen it was received, and then discard it
    somewhere they cannot see — which is worse than not having the control.

    **Why the file is read back rather than buffered.** The upload streams to
    disk in bounded chunks precisely so a 15 MB photograph never sits in memory;
    holding it to check four EXIF tags would undo that on every submission,
    including the overwhelming majority for tenants that have this switched off.
    The read happens only when the switch is on, and in a threadpool, because
    blocking the event loop on file I/O is what ``run_in_threadpool`` exists to
    prevent one function above.

    The quarantined file is left behind on a rejection, for the reason the
    comment above this call states: the store is content-addressed, so deleting
    "the file this request wrote" can delete a file another complaint
    references.
    """
    if stored_photo is None:
        return
    resolved = await RESOLVER.trust_thresholds(session, tenant_id=tenant.id)
    if not resolved.body.exif.live_capture_only:
        return

    store = MediaStore(settings.upload_dir)
    path = store.resolve(stored_photo.uri)
    metadata = await run_in_threadpool(lambda: exif.extract(path.read_bytes()))
    if metadata.latitude is not None and metadata.longitude is not None:
        return

    metrics.ingest_submissions_total.labels(outcome="rejected").inc()
    raise ProblemDetailError(
        status_code=HTTP_422_UNPROCESSABLE,
        title="Live capture required",
        detail=(
            "This deployment accepts photographs taken in the app, not chosen from "
            "the gallery, and the upload carries no camera location metadata. Share "
            "flows such as WhatsApp strip it, so a forwarded photograph will always "
            "be refused here — take the picture in the app instead."
        ),
        problem_type=f"{PROBLEM_BASE}/live-capture-required",
        extra={"policy_version": resolved.stamp},
    )


@router.get(
    "/complaints/{complaint_id}",
    summary="Retrieve a complaint",
    responses={
        200: {"model": ComplaintResponse, "description": "Current state of the complaint"},
        304: {"description": "Not modified"},
        404: {"description": "Not found"},
    },
)
async def get_complaint(
    complaint_id: uuid.UUID,
    tenant: TenantDep,
    session: SessionDep,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> Response:
    """Current state of one complaint, read from the projection.

    **One query, no replay.** The projection exists precisely so a read does not
    fold four hundred events, and §27.3 makes this endpoint a 5-second poll per
    client whenever the WebSocket is unavailable — so a replay here would be the
    cost the whole Phase 2 projection layer was built to avoid, paid on the
    hottest read in the system. The Phase 2 gate already proves the table and the
    log agree; re-deriving that per request buys nothing.

    Coordinates come out through ``ST_Y``/``ST_X`` rather than being carried in a
    parallel pair of float columns, so there is exactly one authoritative
    position per complaint and no way for the two to disagree.

    ``work_order_id`` is resolved by a correlated subquery. §26.2 lists the field
    and it is not a complaint column — a complaint reaches its work order through
    its cluster — so the alternative was returning a field that is permanently
    ``null``, which is worse than not returning it at all.

    A subquery rather than an outer join, for two reasons and one of them was
    found by the tenancy guard. A cluster can carry more than one work order over
    its life (§14.3 merge reversal, a reopened dispute), so a join multiplies the
    row and needs an ordering and a limit on the *outer* query to survive it. And
    the guard refused the join outright: it could not see a ``tenant_id``
    predicate on ``work_orders`` in the ON clause, which is the guard being
    right — a column-to-column comparison scopes the join, not the table. The
    subquery carries its own explicit predicate, which is unambiguous to the
    guard, to the planner, and to a reader.
    """
    # §E17.2's payoff, from the projection. Scalar subqueries rather than a
    # join, for the reason the work-order one below states: the tenancy guard
    # cannot see a `tenant_id` predicate inside a join's ON clause, and a
    # subquery carries its own explicit one — unambiguous to the guard, to the
    # planner, and to a reader. Two of them rather than one join because each
    # returns a single scalar and the planner folds them into the same index
    # lookup on the cluster's primary key.
    cluster_report_count = (
        select(ComplaintCluster.report_count)
        .where(
            ComplaintCluster.tenant_id == tenant.id,
            ComplaintCluster.id == Complaint.cluster_id,
        )
        .scalar_subquery()
        .label("cluster_report_count")
    )
    cluster_first_reported = (
        select(ComplaintCluster.first_reported)
        .where(
            ComplaintCluster.tenant_id == tenant.id,
            ComplaintCluster.id == Complaint.cluster_id,
        )
        .scalar_subquery()
        .label("cluster_first_reported")
    )

    newest_work_order = (
        select(WorkOrder.id)
        .where(
            WorkOrder.tenant_id == tenant.id,
            WorkOrder.complaint_cluster_id == Complaint.cluster_id,
        )
        .order_by(WorkOrder.created_at.desc())
        .limit(1)
        .scalar_subquery()
        .label("work_order_id")
    )

    with tenant_scope(tenant.id):
        row = (
            await session.execute(
                select(
                    Complaint.id,
                    Complaint.version,
                    Complaint.status,
                    Complaint.category,
                    Complaint.classification_confidence,
                    Complaint.cluster_id,
                    Complaint.severity_score,
                    Complaint.severity_breakdown,
                    Complaint.severity_policy_version,
                    Complaint.reported_at,
                    Complaint.degraded_stage,
                    Complaint.degraded_fallback,
                    func.ST_Y(cast(Complaint.location, Geometry)).label("latitude"),
                    func.ST_X(cast(Complaint.location, Geometry)).label("longitude"),
                    newest_work_order,
                    cluster_report_count,
                    cluster_first_reported,
                ).where(Complaint.tenant_id == tenant.id, Complaint.id == complaint_id)
            )
        ).one_or_none()

        if row is None:
            raise ProblemDetailError(
                status_code=HTTP_404_NOT_FOUND,
                title="Complaint not found",
                detail="No complaint with that identifier exists for this tenant.",
                problem_type=f"{PROBLEM_BASE}/not-found",
            )

    etag = _etag(complaint_id, int(row.version))
    if if_none_match is not None and _matches(if_none_match, etag):
        # No body, and the ETag is repeated: RFC 9110 requires a 304 to carry
        # the validator that would have been sent with a 200, or the client's
        # next request has nothing to condition on.
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})

    payload = ComplaintResponse.from_row(row)
    return Response(
        content=payload.model_dump_json(),
        media_type="application/json",
        headers={
            "ETag": etag,
            # Five seconds, matching §27.3's polling fallback. Longer would make
            # the fallback visibly laggier than the WebSocket it replaces;
            # shorter would defeat the conditional request entirely.
            "Cache-Control": "private, max-age=5",
        },
    )


#: A page of history. 200 rather than 50: a complaint's whole life is a dozen
#: events, and the ceiling exists to bound a pathological chain rather than to
#: paginate a normal one. §E17.4's ledger is meant to be read in one screen.
HISTORY_MAX_LIMIT: Final = 200

COMPLAINT_ENTITY: Final = EntityType.COMPLAINT.value


@router.get(
    "/complaints/{complaint_id}/events",
    response_model=ComplaintHistory,
    summary="This complaint's own history, from the log",
    responses={404: {"description": "Not found"}},
)
async def get_complaint_history(
    complaint_id: uuid.UUID,
    tenant: TenantDep,
    session: SessionDep,
    response: Response,
    limit: Annotated[int, Query(ge=1, le=HISTORY_MAX_LIMIT)] = HISTORY_MAX_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ComplaintHistory:
    """§E17.4's ledger, read from the append-only log — ADR-0043, ADR-0044.

    **Why this exists and the projection does not answer it.**
    ``GET /complaints/{id}`` returns *what is true now*. §E17.4's argument is
    that a status is not an answer — *"'In Progress' is the enemy"* — and that a
    citizen is owed the sequence of things that happened, in order, with times.
    That sequence is in ``events`` and nowhere else; a ledger reconstructed from
    the projection would be a status badge with extra steps.

    **What may be read, and by whom.** The complaint id is a UUIDv4 the system
    hands to the submitter and to the officers who work the report, which makes
    this a capability-scoped read rather than a broadcast — a different question
    from the one ADR-0016 answers, and answered separately in
    ``nemesis/events/disclosure.py``. Every row on the chain is returned;
    ``payload`` carries only what a shaper there declares, and
    ``payload_disclosed`` says which of the two kinds of empty a reader is
    looking at.

    **Why the chain head is returned separately from the last row's hash.** They
    are equal when the whole history fits in one page and they are not equal
    when it does not, and the value §E17.3's receipt is checked against is the
    head. Inferring it from the tail of a page would be right until the day a
    complaint had more than ``limit`` events, which is the worst kind of wrong.

    **Uncached, deliberately.** ``Cache-Control: no-store`` rather than the
    read path's five seconds: this is the one representation whose correctness
    is its currency.
    """
    with tenant_scope(tenant.id):
        head = (
            await session.execute(
                select(EventChainHead.sequence, EventChainHead.head_hash).where(
                    EventChainHead.tenant_id == tenant.id,
                    EventChainHead.entity_type == COMPLAINT_ENTITY,
                    EventChainHead.entity_id == complaint_id,
                )
            )
        ).one_or_none()

        if head is None or int(head.sequence) == 0:
            # No chain, or a head row created without an append. Either way this
            # tenant has no such complaint, and the answer is the read path's
            # 404 rather than an empty ledger — an empty ledger for a complaint
            # that does not exist is a lie in the shape of a valid response.
            raise ProblemDetailError(
                status_code=HTTP_404_NOT_FOUND,
                title="Complaint not found",
                detail="No complaint with that identifier exists for this tenant.",
                problem_type=f"{PROBLEM_BASE}/not-found",
            )

        rows = (
            await session.execute(
                select(
                    Event.sequence,
                    Event.event_type,
                    Event.occurred_at,
                    Event.payload,
                    Event.previous_hash,
                    Event.event_hash,
                )
                .where(
                    Event.tenant_id == tenant.id,
                    Event.entity_type == COMPLAINT_ENTITY,
                    Event.entity_id == complaint_id,
                )
                # `ix_events_entity` is (tenant_id, entity_type, entity_id,
                # sequence), so this is an index scan in order across whatever
                # monthly partitions the complaint's life spans — not a sort.
                .order_by(Event.sequence)
                .offset(offset)
                .limit(limit)
            )
        ).all()

    response.headers["Cache-Control"] = "no-store"
    return ComplaintHistory(
        complaint_id=complaint_id,
        chain_head=str(head.head_hash),
        chain_head_sequence=int(head.sequence),
        events=[_to_history_event(row) for row in rows],
        # The head sequence *is* the count: sequences are 1-based and contiguous
        # by construction, which is the property `event_chain_heads` exists to
        # enforce. Counting the rows instead would be a second query that can
        # only ever agree with this number or reveal a fork.
        total=int(head.sequence),
        limit=limit,
        offset=offset,
    )


def _to_history_event(row: Any) -> ComplaintHistoryEvent:
    """Shape one stored row for a reader holding the complaint id.

    ``payload_disclosed`` distinguishes the two empties. A shaper that returns
    nothing for an event that stored nothing is not withholding anything; a
    shaper that returns nothing for an event that stored eight fields is. §E3.3
    asks for the omission to be visible rather than faked, and a single ``{}``
    with no marker beside it makes both cases look like the first.
    """
    stored = row.payload if isinstance(row.payload, dict) else {}
    shaped = history_payload(row.event_type, stored)
    return ComplaintHistoryEvent(
        sequence=int(row.sequence),
        event_type=str(row.event_type),
        occurred_at=format_timestamp(row.occurred_at),
        previous_hash=str(row.previous_hash),
        event_hash=str(row.event_hash),
        payload=shaped,
        payload_disclosed=bool(shaped) or not stored,
    )


async def _enforce_rate_limit(
    *, settings: Settings, tenant: TenantContext, device_fingerprint: str, request: Request
) -> None:
    """Spend one token, or refuse with a Retry-After.

    Identity is the device fingerprint, falling back to the peer address. The
    fingerprint is the better key — §11.3 already treats it as the unit of abuse
    — and the address is the only thing available when a client omits one, which
    a client trying to evade a limit certainly will.
    """
    limiter = get_limiter(settings.redis_url, settings.rate_limit)
    identity = device_fingerprint or (request.client.host if request.client else "unknown")
    decision = await limiter.check(tenant_id=tenant.id, plan=tenant.plan, identity=identity)
    if decision.allowed:
        return

    metrics.ingest_submissions_total.labels(outcome="rate_limited").inc()
    raise ProblemDetailError(
        status_code=HTTP_429_TOO_MANY_REQUESTS,
        title="Rate limit exceeded",
        detail=(
            f"The {decision.tier} tier allows a limited number of submissions. "
            f"Retry in {decision.retry_after_seconds} seconds."
        ),
        problem_type=f"{PROBLEM_BASE}/rate-limited",
        extra={"retry_after_seconds": decision.retry_after_seconds, "tier": decision.tier},
    )


async def _store_upload(
    store: MediaStore,
    upload: UploadFile | None,
    *,
    allowed: tuple[str, ...],
    settings: Settings,
    field: str,
) -> StoredMedia | None:
    """Persist one part, translating storage errors into problem details."""
    if upload is None:
        return None
    try:
        stored = await store.store(
            upload,
            allowed_types=allowed,
            max_bytes=settings.ingest.max_upload_bytes,
            chunk_bytes=settings.ingest.stream_chunk_bytes,
        )
    except UploadTooLargeError as exc:
        metrics.ingest_submissions_total.labels(outcome="rejected").inc()
        raise ProblemDetailError(
            status_code=HTTP_413_CONTENT_TOO_LARGE,
            title="Upload too large",
            detail=str(exc),
            problem_type=f"{PROBLEM_BASE}/upload-too-large",
            extra={"field": field, "max_bytes": settings.ingest.max_upload_bytes},
        ) from exc
    except UnsupportedMediaError as exc:
        metrics.ingest_submissions_total.labels(outcome="rejected").inc()
        raise ProblemDetailError(
            status_code=HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            title="Unsupported media type",
            detail=str(exc),
            problem_type=f"{PROBLEM_BASE}/unsupported-media",
            extra={"field": field},
        ) from exc
    except UploadError as exc:
        metrics.ingest_submissions_total.labels(outcome="rejected").inc()
        raise ProblemDetailError(
            status_code=HTTP_422_UNPROCESSABLE,
            title="Upload rejected",
            detail=str(exc),
            problem_type=f"{PROBLEM_BASE}/validation-error",
            extra={"field": field},
        ) from exc

    metrics.ingest_upload_bytes.observe(stored.size_bytes)
    return stored


def _etag(complaint_id: uuid.UUID, version: int) -> str:
    """Strong validator over the projection's log position.

    Strong rather than weak (``W/``) because two representations with the same
    version are byte-identical by construction — the body is a pure function of
    the projection at that sequence.
    """
    return f'"{complaint_id.hex}-{version}"'


def _matches(if_none_match: str, etag: str) -> bool:
    """RFC 9110 If-None-Match: a list, or ``*``."""
    candidate = if_none_match.strip()
    if candidate == "*":
        return True
    return any(part.strip().removeprefix("W/") == etag for part in candidate.split(","))


def _json(body: dict[str, object]) -> str:
    import json

    return json.dumps(body)
