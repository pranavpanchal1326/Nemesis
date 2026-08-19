"""The simulation and evaluation HTTP surface — Phase 7.

Mounted beside the policy router under ``/api/v1/control-plane`` and following
its conventions exactly, because it is the same control plane: **reads are
tenant-scoped and token-free, writes carry the control-plane token.** Reading an
impact report is the same class of operation as reading which rubric is scoring
your complaints. Publishing an evaluation set changes what activations the
system will *refuse*, which is at least as consequential as changing a policy
and carries the same shared secret until Phase 13 replaces it with an operator
identity.

**A backtest is a write, and the token says so.** It reads history and decides
nothing — but it creates a run row, and in evaluation mode a certificate that
determines whether an activation may proceed. An endpoint that mints the
evidence a guardrail consumes is not a read, whatever it does to the corpus.

**There is no endpoint that activates from here, and no endpoint that waives a
certificate.** The waiver parameter on ``policy.service.activate`` exists for
``rollback`` and is unreachable over HTTP — deliberately, because an operator
who can skip the guardrail with a request body has a guardrail that will be
skipped on the first bad afternoon. If a genuine emergency needs it, the answer
is a rollback, which is the path that already has one and already records it.

**Route ordering repeats Phase 6's lesson.** FastAPI matches in registration
order, so the literal paths (``/runs``, ``/evaluation-sets``) are declared
before anything with a variable first segment. Phase 6 shipped a bug where
``POST /{kind}`` swallowed ``/seed-baselines``; the same shape here would make
``/runs`` parse as a policy kind.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, ConfigDict, Field

from nemesis.api.deps import ConfigDep, SessionDep, TenantDep
from nemesis.api.errors import (
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_422_UNPROCESSABLE,
    PROBLEM_BASE,
    ProblemDetailError,
)
from nemesis.api.v1.control_plane import TokenDep, _require_token
from nemesis.db.models.simulation import (
    EvaluationSet,
    PolicyCertificate,
    SimulationRun,
)
from nemesis.observability.logging import get_correlation_id
from nemesis.policy.documents import PolicyKind
from nemesis.simulation import evaluation, runs, shadow, tuning
from nemesis.simulation.corpus import ABSOLUTE_MAX_CASES, DEFAULT_MAX_CASES, CorpusWindow
from nemesis.simulation.errors import (
    SimulationConflictError,
    SimulationError,
    SimulationNotFoundError,
)
from nemesis.tenancy.context import tenant_scope

router = APIRouter(prefix="/control-plane/simulations", tags=["control-plane", "simulation"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class ApiModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class RunSummary(ApiModel):
    """A run without its report.

    The listing returns these. An impact report over twenty thousand cases is a
    substantial document, and a run list is dozens of rows — returning every
    report would make the history screen a multi-megabyte response for a table
    that shows a date, a verdict, and a count.
    """

    id: uuid.UUID
    kind: PolicyKind
    mode: str
    status: str
    candidate_revision: int | None
    candidate_content_hash: str
    window_start: datetime
    window_end: datetime
    case_count: int
    population: int
    sampling_stride: int
    affected: int
    failure_reason: str | None
    created_at: datetime


class RunDetail(RunSummary):
    """A run with the report it produced, exactly as it was stored.

    Returned unchanged rather than re-derived. A screen that rebuilt the report
    from the run's parameters could disagree with the record — and would, the
    first time the comparison logic changed — which is the failure the whole
    phase exists to prevent one level down.
    """

    baseline_stamps: dict[str, Any]
    report: dict[str, Any] | None


class CertificateResponse(ApiModel):
    id: uuid.UUID
    kind: PolicyKind
    candidate_revision: int | None
    candidate_content_hash: str
    verdict: str
    labels_evaluated: int
    labels_passed: int
    labels_unresolvable: int
    findings: dict[str, Any]
    created_at: datetime


class RunResponse(ApiModel):
    run: RunDetail
    #: Present only when the run was asked to certify. ``None`` means nobody
    #: claimed anything about whether this candidate may be activated.
    certificate: CertificateResponse | None = None


class EvaluationSetResponse(ApiModel):
    code: str
    name: str
    kind: PolicyKind
    status: str
    description: str
    label_count: int
    labels_hash: str | None
    pass_ratio: float
    published_at: datetime | None
    retired_at: datetime | None
    created_at: datetime


class LabelResponse(ApiModel):
    complaint_id: uuid.UUID
    rationale: str
    expected_severity_tier: str | None
    expected_severity_min: float | None
    expected_severity_max: float | None
    expected_safety_fired: bool | None
    expected_department_code: str | None
    expected_dedup_outcome: str | None


class ShadowSummaryResponse(ApiModel):
    candidate_content_hash: str
    observed: int
    diverged: int
    divergence_rate: float
    fields: dict[str, int]


class ProposalResponse(ApiModel):
    category: str | None
    current_threshold: float
    proposed_threshold: float
    revert_count: int
    highest_reverted_confidence: float


class ProposalsResponse(ApiModel):
    """What the revert history suggests, and the standing caveat.

    ``direction`` is a constant string and it is in the response on purpose: an
    operator reading a list of threshold increases needs to know that decreases
    are not absent because none are warranted, but because no event in the log
    could ever support one. See ``simulation.tuning``.
    """

    proposals: list[ProposalResponse]
    direction: str = (
        "Thresholds can only be proposed upward: the log records merges operators "
        "reverted, and nothing records merges that should have happened and did not."
    )


class DraftedProposalResponse(ApiModel):
    kind: PolicyKind
    revision: int
    content_hash: str
    detail: str = (
        "Drafted, not applied. It decides nothing until somebody reviews, approves, "
        "and activates it."
    )


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class SimulationRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RunRequest(SimulationRequestModel):
    kind: PolicyKind
    revision: int = Field(ge=1)
    #: Defaults to the last twelve months — the span the phase gate names, and
    #: the one that covers a full seasonal cycle of a civic complaint stream.
    window_start: datetime | None = None
    window_end: datetime | None = None
    max_cases: int = Field(default=DEFAULT_MAX_CASES, ge=1, le=ABSOLUTE_MAX_CASES)
    #: When true, additionally mark the candidate against the tenant's published
    #: evaluation set and issue a certificate.
    certify: bool = False


class CreateSetRequest(SimulationRequestModel):
    code: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_.-]{0,62}$")
    name: str = Field(min_length=1, max_length=200)
    kind: PolicyKind
    description: str = Field(min_length=1, max_length=4000)
    pass_ratio: float = Field(default=1.0, gt=0.0, le=1.0)


class AddLabelRequest(SimulationRequestModel):
    complaint_id: uuid.UUID
    rationale: str = Field(min_length=1, max_length=2000)
    expected_severity_tier: str | None = Field(default=None, max_length=64)
    expected_severity_min: float | None = Field(default=None, ge=0.0, le=10.0)
    expected_severity_max: float | None = Field(default=None, ge=0.0, le=10.0)
    expected_safety_fired: bool | None = None
    expected_department_code: str | None = Field(default=None, max_length=64)
    expected_dedup_outcome: str | None = Field(default=None, max_length=32)


class ObserveRequest(SimulationRequestModel):
    kind: PolicyKind
    revision: int = Field(ge=1)
    complaint_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


class ProposeRequest(SimulationRequestModel):
    window_start: datetime | None = None
    window_end: datetime | None = None


# ---------------------------------------------------------------------------
# Error translation
# ---------------------------------------------------------------------------


def _translate(error: SimulationError) -> ProblemDetailError:
    """One mapping from simulation errors to the RFC 9457 contract.

    ``CorpusTooSmallError`` lands on 422 rather than 404, and the distinction
    matters to the caller: the window is not missing, it is too narrow to
    support a conclusion, and the remedy is to widen the request — which is what
    a 422 with the count in the detail tells them to do.
    """
    if isinstance(error, SimulationNotFoundError):
        return ProblemDetailError(
            status_code=HTTP_404_NOT_FOUND,
            title="Not found",
            detail=str(error),
            problem_type=f"{PROBLEM_BASE}/not-found",
        )
    if isinstance(error, SimulationConflictError):
        return ProblemDetailError(
            status_code=HTTP_409_CONFLICT,
            title="Conflict",
            detail=str(error),
            problem_type=f"{PROBLEM_BASE}/conflict",
        )
    return ProblemDetailError(
        status_code=HTTP_422_UNPROCESSABLE,
        title="Simulation request rejected",
        detail=str(error),
        problem_type=f"{PROBLEM_BASE}/validation-error",
    )


def _run_summary(run: SimulationRun) -> RunSummary:
    return RunSummary(
        id=run.id,
        kind=PolicyKind(run.kind),
        mode=run.mode,
        status=run.status,
        candidate_revision=run.candidate_revision,
        candidate_content_hash=run.candidate_content_hash,
        window_start=run.window_start,
        window_end=run.window_end,
        case_count=run.case_count,
        population=run.population,
        sampling_stride=run.sampling_stride,
        affected=run.affected,
        failure_reason=run.failure_reason,
        created_at=run.created_at,
    )


def _run_detail(run: SimulationRun) -> RunDetail:
    return RunDetail(
        **_run_summary(run).model_dump(),
        baseline_stamps=dict(run.baseline_stamps),
        report=dict(run.report) if run.report else None,
    )


def _certificate(certificate: PolicyCertificate) -> CertificateResponse:
    return CertificateResponse(
        id=certificate.id,
        kind=PolicyKind(certificate.kind),
        candidate_revision=certificate.candidate_revision,
        candidate_content_hash=certificate.candidate_content_hash,
        verdict=certificate.verdict,
        labels_evaluated=certificate.labels_evaluated,
        labels_passed=certificate.labels_passed,
        labels_unresolvable=certificate.labels_unresolvable,
        findings=dict(certificate.findings),
        created_at=certificate.created_at,
    )


def _set_response(evaluation_set: EvaluationSet) -> EvaluationSetResponse:
    return EvaluationSetResponse(
        code=evaluation_set.code,
        name=evaluation_set.name,
        kind=PolicyKind(evaluation_set.kind),
        status=evaluation_set.status,
        description=evaluation_set.description,
        label_count=evaluation_set.label_count,
        labels_hash=evaluation_set.labels_hash,
        pass_ratio=evaluation_set.pass_ratio,
        published_at=evaluation_set.published_at,
        retired_at=evaluation_set.retired_at,
        created_at=evaluation_set.created_at,
    )


def _window(start: datetime | None, end: datetime | None) -> CorpusWindow | None:
    """Build a window from a partially-specified pair, or ``None`` for the default.

    A start with no end means "from then until now", which is what somebody
    typing one date means. An end with no start means the twelve months before
    it — the same span the default covers, moved. Neither is guessed silently:
    the resolved window is echoed back on the run row and in the report.
    """
    if start is None and end is None:
        return None
    resolved_end = end or datetime.now(tz=UTC)
    resolved_start = start or (resolved_end - timedelta(days=runs.DEFAULT_WINDOW_DAYS))
    return CorpusWindow(start=resolved_start, end=resolved_end)


# ---------------------------------------------------------------------------
# Reads — declared first, and literal paths before variable ones
# ---------------------------------------------------------------------------


@router.get("/runs", summary="Backtest history")
async def list_runs(
    tenant: TenantDep,
    session: SessionDep,
    kind: Annotated[PolicyKind | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[RunSummary]:
    with tenant_scope(tenant.id):
        found = await runs.list_runs(session, tenant_id=tenant.id, kind=kind, limit=limit)
    return [_run_summary(run) for run in found]


@router.get("/runs/{run_id}", summary="One run, with its impact report")
async def get_run(run_id: uuid.UUID, tenant: TenantDep, session: SessionDep) -> RunDetail:
    with tenant_scope(tenant.id):
        try:
            run = await runs.require_run(session, tenant_id=tenant.id, run_id=run_id)
        except SimulationError as exc:
            raise _translate(exc) from exc
    return _run_detail(run)


@router.get("/evaluation-sets", summary="Labelled evaluation sets")
async def list_sets(
    tenant: TenantDep,
    session: SessionDep,
    kind: Annotated[PolicyKind | None, Query()] = None,
) -> list[EvaluationSetResponse]:
    with tenant_scope(tenant.id):
        found = await evaluation.list_sets(session, tenant_id=tenant.id, kind=kind)
    return [_set_response(evaluation_set) for evaluation_set in found]


@router.get("/evaluation-sets/{code}/labels", summary="The judgements in a set")
async def list_labels(code: str, tenant: TenantDep, session: SessionDep) -> list[LabelResponse]:
    with tenant_scope(tenant.id):
        try:
            evaluation_set = await evaluation.require_set(session, tenant_id=tenant.id, code=code)
        except SimulationError as exc:
            raise _translate(exc) from exc
        labels = await evaluation.list_labels(
            session, tenant_id=tenant.id, set_id=evaluation_set.id
        )
    return [
        LabelResponse(
            complaint_id=label.complaint_id,
            rationale=label.rationale,
            expected_severity_tier=label.expected_severity_tier,
            expected_severity_min=label.expected_severity_min,
            expected_severity_max=label.expected_severity_max,
            expected_safety_fired=label.expected_safety_fired,
            expected_department_code=label.expected_department_code,
            expected_dedup_outcome=label.expected_dedup_outcome,
        )
        for label in labels
    ]


@router.get("/shadow/{content_hash}", summary="Divergence rate for one candidate")
async def shadow_summary(
    content_hash: str, tenant: TenantDep, session: SessionDep
) -> ShadowSummaryResponse:
    with tenant_scope(tenant.id):
        summary = await shadow.summarise(session, tenant_id=tenant.id, content_hash=content_hash)
    return ShadowSummaryResponse(
        candidate_content_hash=summary.candidate_content_hash,
        observed=summary.observed,
        diverged=summary.diverged,
        divergence_rate=round(summary.divergence_rate, 6),
        fields=summary.fields,
    )


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


@router.post(
    "/runs",
    status_code=status.HTTP_201_CREATED,
    summary="Backtest a candidate over history",
    responses={
        403: {"description": "Control-plane token missing or wrong"},
        422: {"description": "The window holds too few complaints to conclude anything"},
    },
)
async def create_run(
    request: RunRequest,
    tenant: TenantDep,
    session: SessionDep,
    settings: ConfigDep,
    token: TokenDep = None,
) -> RunResponse:
    """Replay a candidate over a window and store the impact report.

    A write, and behind the token, for the reason the module docstring gives: it
    creates the run row, and with ``certify`` it creates the certificate an
    activation will consult.
    """
    _require_token(settings, token)
    with tenant_scope(tenant.id):
        try:
            run, certificate = await runs.run_backtest(
                session,
                tenant_id=tenant.id,
                kind=request.kind,
                revision=request.revision,
                window=_window(request.window_start, request.window_end),
                max_cases=request.max_cases,
                certify=request.certify,
                correlation_id=get_correlation_id(),
            )
        except SimulationError as exc:
            raise _translate(exc) from exc
    return RunResponse(
        run=_run_detail(run),
        certificate=_certificate(certificate) if certificate is not None else None,
    )


@router.post(
    "/evaluation-sets",
    status_code=status.HTTP_201_CREATED,
    summary="Open a draft evaluation set",
)
async def create_set(
    request: CreateSetRequest,
    tenant: TenantDep,
    session: SessionDep,
    settings: ConfigDep,
    token: TokenDep = None,
) -> EvaluationSetResponse:
    """A draft set gates nothing. Publishing it is what turns the guardrail on."""
    _require_token(settings, token)
    with tenant_scope(tenant.id):
        try:
            created = await evaluation.create_set(
                session,
                tenant_id=tenant.id,
                code=request.code,
                name=request.name,
                kind=request.kind,
                description=request.description,
                pass_ratio=request.pass_ratio,
            )
        except SimulationError as exc:
            raise _translate(exc) from exc
    return _set_response(created)


@router.post(
    "/evaluation-sets/{code}/labels",
    status_code=status.HTTP_201_CREATED,
    summary="Record one human judgement",
)
async def add_label(
    code: str,
    request: AddLabelRequest,
    tenant: TenantDep,
    session: SessionDep,
    settings: ConfigDep,
    token: TokenDep = None,
) -> LabelResponse:
    """Draft sets only — a published set's labels are frozen."""
    _require_token(settings, token)
    with tenant_scope(tenant.id):
        try:
            label = await evaluation.add_label(
                session,
                tenant_id=tenant.id,
                code=code,
                complaint_id=request.complaint_id,
                rationale=request.rationale,
                expected_severity_tier=request.expected_severity_tier,
                expected_severity_min=request.expected_severity_min,
                expected_severity_max=request.expected_severity_max,
                expected_safety_fired=request.expected_safety_fired,
                expected_department_code=request.expected_department_code,
                expected_dedup_outcome=request.expected_dedup_outcome,
            )
        except SimulationError as exc:
            raise _translate(exc) from exc
    return LabelResponse(
        complaint_id=label.complaint_id,
        rationale=label.rationale,
        expected_severity_tier=label.expected_severity_tier,
        expected_severity_min=label.expected_severity_min,
        expected_severity_max=label.expected_severity_max,
        expected_safety_fired=label.expected_safety_fired,
        expected_department_code=label.expected_department_code,
        expected_dedup_outcome=label.expected_dedup_outcome,
    )


@router.post(
    "/evaluation-sets/{code}/publish",
    summary="Freeze the labels and gate this kind",
    responses={409: {"description": "Already published, or has no labels"}},
)
async def publish_set(
    code: str,
    tenant: TenantDep,
    session: SessionDep,
    settings: ConfigDep,
    token: TokenDep = None,
) -> EvaluationSetResponse:
    """From here, activating this kind requires a passing certificate.

    Any set already published for the same kind is retired in the same
    transaction — a kind has one exam, and two would make "which one gates this"
    a question about row order.
    """
    _require_token(settings, token)
    with tenant_scope(tenant.id):
        try:
            published = await evaluation.publish_set(
                session, tenant_id=tenant.id, code=code, correlation_id=get_correlation_id()
            )
        except SimulationError as exc:
            raise _translate(exc) from exc
    return _set_response(published)


@router.post(
    "/evaluation-sets/{code}/retire",
    summary="Stop gating this kind",
    responses={409: {"description": "Not currently published"}},
)
async def retire_set(
    code: str,
    tenant: TenantDep,
    session: SessionDep,
    settings: ConfigDep,
    token: TokenDep = None,
) -> EvaluationSetResponse:
    """Switching a guardrail off is an event on the tenant chain, not a quiet update."""
    _require_token(settings, token)
    with tenant_scope(tenant.id):
        try:
            retired = await evaluation.retire_set(
                session, tenant_id=tenant.id, code=code, correlation_id=get_correlation_id()
            )
        except SimulationError as exc:
            raise _translate(exc) from exc
    return _set_response(retired)


@router.post("/shadow", summary="Observe a candidate against named complaints")
async def observe(
    request: ObserveRequest,
    tenant: TenantDep,
    session: SessionDep,
    settings: ConfigDep,
    token: TokenDep = None,
) -> ShadowSummaryResponse:
    """Decide the named complaints twice and record what differed.

    The evaluation runs inside ``simulation.readonly.read_only``; the recording
    happens after it, on the ordinary session. That split is the gate clause —
    see ``simulation.shadow``.
    """
    _require_token(settings, token)
    with tenant_scope(tenant.id):
        try:
            observations = await shadow.observe(
                session,
                tenant_id=tenant.id,
                kind=request.kind,
                revision=request.revision,
                complaint_ids=request.complaint_ids,
            )
        except SimulationError as exc:
            raise _translate(exc) from exc
        await shadow.record(session, tenant_id=tenant.id, observations=observations)
        content_hash = observations[0].candidate_content_hash if observations else ""
        summary = await shadow.summarise(session, tenant_id=tenant.id, content_hash=content_hash)
    return ShadowSummaryResponse(
        candidate_content_hash=summary.candidate_content_hash,
        observed=summary.observed,
        diverged=summary.diverged,
        divergence_rate=round(summary.divergence_rate, 6),
        fields=summary.fields,
    )


@router.post("/tuning/dedup", summary="What the reverted merges suggest")
async def propose_dedup(
    request: ProposeRequest,
    tenant: TenantDep,
    session: SessionDep,
    settings: ConfigDep,
    token: TokenDep = None,
) -> ProposalsResponse:
    """Read-only in effect, behind the token by convention with its sibling.

    It computes proposals and writes nothing. ``/tuning/dedup/draft`` is the one
    that puts a document in the review queue, and separating them is what stops
    "show me what the data suggests" from being the same request as "put that in
    front of an approver".
    """
    _require_token(settings, token)
    window = _window(request.window_start, request.window_end) or runs.default_window()
    with tenant_scope(tenant.id):
        try:
            proposals = await tuning.propose_dedup_thresholds(
                session, tenant_id=tenant.id, window=window
            )
        except SimulationError as exc:
            raise _translate(exc) from exc
    return ProposalsResponse(
        proposals=[
            ProposalResponse(
                category=proposal.category,
                current_threshold=proposal.current_threshold,
                proposed_threshold=proposal.proposed_threshold,
                revert_count=proposal.revert_count,
                highest_reverted_confidence=proposal.highest_reverted_confidence,
            )
            for proposal in proposals
        ]
    )


@router.post(
    "/tuning/dedup/draft",
    status_code=status.HTTP_201_CREATED,
    summary="Draft the proposed thresholds for review",
    responses={422: {"description": "The window supports no proposal"}},
)
async def draft_dedup_proposal(
    request: ProposeRequest,
    tenant: TenantDep,
    session: SessionDep,
    settings: ConfigDep,
    token: TokenDep = None,
) -> DraftedProposalResponse:
    """Write the proposals into a draft revision. It activates nothing.

    The draft enters the ordinary policy lifecycle — review, approval,
    activation — and is subject to the same certification guardrail as any other
    candidate. An auto-tuner with a shortcut past that would be a second way for
    a document to reach production, which is the thing Phase 6's single mutation
    path exists to prevent.
    """
    _require_token(settings, token)
    window = _window(request.window_start, request.window_end) or runs.default_window()
    with tenant_scope(tenant.id):
        try:
            proposals = await tuning.propose_dedup_thresholds(
                session, tenant_id=tenant.id, window=window
            )
            version = await tuning.draft_from_proposals(
                session,
                tenant_id=tenant.id,
                proposals=proposals,
                correlation_id=get_correlation_id(),
            )
        except SimulationError as exc:
            raise _translate(exc) from exc
    return DraftedProposalResponse(
        kind=PolicyKind.DEDUP_THRESHOLDS,
        revision=version.revision,
        content_hash=version.content_hash,
    )
