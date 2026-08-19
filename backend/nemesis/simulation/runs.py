"""Executing a backtest and recording what it found.

The thin layer between the HTTP surface and the pure machinery: it opens a run
row, builds the corpus, compares the bundles, stores the report, and — in
evaluation mode — issues a certificate.

**A failed run is recorded, not discarded.** The row is written *before* the
work starts and closed either way. A table that only holds successes implies a
diligence that did not happen: "we tried to backtest this and the window was
empty" is exactly the record an incident review wants, and it is also the record
that stops the same person trying the same window three more times.

**The report is stored as the model dumped it.** The API returns the stored
document rather than re-deriving one, so the screen and the record cannot
disagree. That is the same reasoning ``policy.service`` applies to a document
body, one level up: a report assembled twice is a report with two versions.

**Nothing here commits.** Same contract as ``policy.service`` and
``control_plane.taxonomy``, for the same reason — a run that stored its report
and failed before its certificate would leave evidence of a check that did not
finish.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.db.models.simulation import PolicyCertificate, SimulationRun
from nemesis.db.session import session_scope
from nemesis.observability.logging import get_logger
from nemesis.policy import service as policy_service
from nemesis.policy.documents import PolicyKind
from nemesis.simulation import backtest, bundles, corpus, evaluation
from nemesis.simulation.engine import DECIDABLE_KINDS, PolicyBundle
from nemesis.simulation.errors import (
    SimulationError,
    SimulationNotFoundError,
    SimulationValidationError,
)
from nemesis.tenancy.context import tenant_scope

log = get_logger(__name__)

#: The window a run covers when the caller names none. Twelve months, because
#: that is what the phase gate asks for and because a civic complaint stream is
#: strongly seasonal — a monsoon and a dry season have different mixes of
#: category, photograph quality, and volume, and a rubric tuned against one of
#: them will surprise somebody in the other.
DEFAULT_WINDOW_DAYS: Final = 365

MODE_BACKTEST: Final = "backtest"
MODE_EVALUATION: Final = "evaluation"

_STATUS_COMPLETED: Final = "completed"
_STATUS_FAILED: Final = "failed"


def default_window(*, now: datetime | None = None) -> corpus.CorpusWindow:
    """The last twelve months, ending now.

    ``now`` is a parameter rather than a call to the clock inside the window
    object, so a test can pin it. The default is the only clock read in this
    module, and it happens here rather than deep in the corpus builder — a
    window is a decision about scope, and decisions about scope belong where
    somebody can see them.
    """
    end = now or datetime.now(tz=UTC)
    return corpus.CorpusWindow(start=end - timedelta(days=DEFAULT_WINDOW_DAYS), end=end)


async def run_backtest(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    kind: PolicyKind,
    revision: int,
    window: corpus.CorpusWindow | None = None,
    max_cases: int = corpus.DEFAULT_MAX_CASES,
    certify: bool = False,
    actor_id: uuid.UUID | None = None,
    correlation_id: str | None = None,
) -> tuple[SimulationRun, PolicyCertificate | None]:
    """Replay a candidate over history and store the impact report.

    ``certify=True`` additionally marks the candidate against the tenant's
    published evaluation set and issues a certificate. The two are one call
    because they read the same corpus and the same bundle, and doing them as two
    requests would let a certificate be issued against a candidate whose
    backtest was never looked at — which is the shape of a guardrail that
    produces paperwork instead of a decision.
    """
    if kind not in DECIDABLE_KINDS:
        # Refused before anything is read, and refused loudly. Running it would
        # succeed: the corpus builds, both bundles decide, and the report says
        # "0 affected" — which reads as "this change is safe" and means "this
        # comparison never looked at the document". Phase 7 spends a paragraph
        # on that exact confusion for coverage gaps; this is the same failure
        # one level up, where the whole kind is the gap.
        raise SimulationValidationError(
            f"{kind.value} cannot be backtested: the decision engine does not read it, "
            f"so a comparison would report no change for any candidate — including one "
            f"that inverts every value. Decidable kinds are "
            f"{', '.join(sorted(member.value for member in DECIDABLE_KINDS))}."
        )

    version = await policy_service.get_version(
        session, tenant_id=tenant_id, kind=kind, revision=revision
    )
    if version is None:
        raise SimulationNotFoundError(f"no {kind.value} revision {revision} for this tenant")

    span = window or default_window()
    baseline = await bundles.live_bundle(session, tenant_id=tenant_id)

    try:
        candidate = await bundles.candidate_bundle(
            session, tenant_id=tenant_id, kind=kind, revision=revision
        )
        built = await corpus.build_corpus(
            session, tenant_id=tenant_id, window=span, max_cases=max_cases
        )
        calendars = await corpus.load_calendars(
            session,
            tenant_id=tenant_id,
            codes={
                entry.calendar_code
                for bundle in (baseline, candidate)
                for entry in bundle.sla_matrix.body.entries
            },
        )
        report = backtest.compare(
            baseline,
            candidate,
            built,
            tenant_id=tenant_id,
            kind=kind,
            calendars=calendars,
        )
    except SimulationError as exc:
        await _record_failure(
            tenant_id=tenant_id,
            kind=kind,
            revision=revision,
            content_hash=version.content_hash,
            baseline=baseline,
            window=span,
            failure=str(exc),
            actor_id=actor_id,
        )
        raise

    run = SimulationRun(
        tenant_id=tenant_id,
        kind=kind.value,
        mode=MODE_EVALUATION if certify else MODE_BACKTEST,
        status=_STATUS_COMPLETED,
        candidate_revision=revision,
        candidate_content_hash=version.content_hash,
        baseline_stamps=baseline.stamps(),
        window_start=span.start,
        window_end=span.end,
        case_count=report.case_count,
        population=report.population,
        sampling_stride=report.sampling_stride,
        affected=report.affected,
        report=to_json(report),
        created_by=actor_id,
    )
    session.add(run)
    await session.flush()
    run_id = run.id

    certificate = None
    if certify:
        certificate = await evaluation.evaluate_candidate(
            session,
            tenant_id=tenant_id,
            kind=kind,
            revision=revision,
            run_id=run_id,
            actor_id=actor_id,
            correlation_id=correlation_id,
        )

    log.info(
        "simulation_run_completed",
        tenant_id=str(tenant_id),
        run_id=str(run_id),
        kind=kind.value,
        revision=revision,
        cases=report.case_count,
        affected=report.affected,
        certified=certificate.verdict if certificate is not None else None,
    )
    return await require_run(session, tenant_id=tenant_id, run_id=run_id), certificate


async def get_run(
    session: AsyncSession, *, tenant_id: uuid.UUID, run_id: uuid.UUID
) -> SimulationRun | None:
    row = await session.execute(
        select(SimulationRun).where(
            SimulationRun.tenant_id == tenant_id, SimulationRun.id == run_id
        )
    )
    return row.scalar_one_or_none()


async def require_run(
    session: AsyncSession, *, tenant_id: uuid.UUID, run_id: uuid.UUID
) -> SimulationRun:
    run = await get_run(session, tenant_id=tenant_id, run_id=run_id)
    if run is None:
        raise SimulationNotFoundError(f"no simulation run {run_id} for this tenant")
    return run


async def list_runs(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    kind: PolicyKind | None = None,
    limit: int = 50,
) -> list[SimulationRun]:
    statement = select(SimulationRun).where(SimulationRun.tenant_id == tenant_id)
    if kind is not None:
        statement = statement.where(SimulationRun.kind == kind.value)
    rows = await session.execute(statement.order_by(SimulationRun.created_at.desc()).limit(limit))
    return list(rows.scalars().all())


async def latest_certificate(
    session: AsyncSession, *, tenant_id: uuid.UUID, kind: PolicyKind, content_hash: str
) -> PolicyCertificate | None:
    """The most recent verdict on these exact bytes, pass or fail.

    What the policy screen shows next to a draft, so an author sees "evaluated,
    failed on 4 of 40" before they submit it for review rather than after an
    approver's activation is refused.
    """
    row = await session.execute(
        select(PolicyCertificate)
        .where(
            PolicyCertificate.tenant_id == tenant_id,
            PolicyCertificate.kind == kind.value,
            PolicyCertificate.candidate_content_hash == content_hash,
        )
        .order_by(PolicyCertificate.created_at.desc())
        .limit(1)
    )
    return row.scalar_one_or_none()


async def _record_failure(
    *,
    tenant_id: uuid.UUID,
    kind: PolicyKind,
    revision: int,
    content_hash: str,
    baseline: PolicyBundle,
    window: corpus.CorpusWindow,
    failure: str,
    actor_id: uuid.UUID | None,
) -> None:
    """Record a refused run **in its own committed transaction**.

    The one place in this package that opens a session of its own, and the
    reason is the promise in the module docstring. A failed run written into the
    caller's transaction is a failed run that vanishes: the exception propagates,
    the request handler rolls back, and "we tried to backtest this and the
    window was empty" leaves no trace — which reads exactly like nobody having
    tried.

    The failure of *this* write is swallowed rather than raised. The caller is
    already receiving a ``SimulationError`` that describes what actually went
    wrong; replacing it with a database error from the bookkeeping would hide
    the real diagnosis behind an unrelated one.
    """
    try:
        # `tenant_scope` is synchronous, so it cannot join the `async with` list
        # — that fails at runtime with a message about the asynchronous context
        # manager protocol, several lines from the cause. The same note
        # `tests/test_policy_lifecycle` makes about its own helper.
        with tenant_scope(tenant_id):
            async with session_scope() as recorder:
                recorder.add(
                    SimulationRun(
                        tenant_id=tenant_id,
                        kind=kind.value,
                        mode=MODE_BACKTEST,
                        status=_STATUS_FAILED,
                        candidate_revision=revision,
                        candidate_content_hash=content_hash,
                        baseline_stamps=baseline.stamps(),
                        window_start=window.start,
                        window_end=window.end,
                        failure_reason=failure,
                        created_by=actor_id,
                    )
                )
    except Exception as bookkeeping_error:  # pragma: no cover - must not mask the cause
        # The reason is logged, not discarded. A bare swallow here hid a
        # programming error during development — a synchronous context manager
        # in an `async with` list — and the only symptom was a row that never
        # appeared. Whatever goes wrong with the bookkeeping has to be
        # diagnosable without reproducing it.
        log.warning(
            "simulation_run_failure_not_recorded",
            tenant_id=str(tenant_id),
            kind=kind.value,
            revision=revision,
            error=repr(bookkeeping_error),
        )


def to_json(value: Any) -> Any:
    """Render nested dataclasses, UUIDs and datetimes as JSON-safe values.

    Written out rather than reached for through Pydantic. The report is a tree
    of frozen dataclasses because it is built in a hot loop over twenty thousand
    cases, where Pydantic validation would cost more than the comparison it
    describes — and converting once, here, at the storage boundary, is the whole
    price of that choice.
    """
    if is_dataclass(value) and not isinstance(value, type):
        return {key: to_json(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): to_json(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [to_json(item) for item in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, PolicyKind):
        return value.value
    return value


__all__ = [
    "DEFAULT_WINDOW_DAYS",
    "MODE_BACKTEST",
    "MODE_EVALUATION",
    "default_window",
    "get_run",
    "latest_certificate",
    "list_runs",
    "require_run",
    "run_backtest",
    "to_json",
]
