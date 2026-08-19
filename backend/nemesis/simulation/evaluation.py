"""Labelled evaluation sets, and the certificates that gate an activation.

This is the module behind Phase 7's second gate clause: *a policy that regresses
the labelled evaluation set cannot be activated.* Everything about how it is
built follows from taking the word "cannot" literally.

**The guardrail turns itself on.** There is no ``require_certification`` flag.
Publishing a set for a kind is what makes that kind gated, and retiring the set
is what ungates it. A separate flag would be a second source of truth about the
same fact, and the two would disagree the first time somebody retired a set
without clearing it — leaving activations refused with no exam to pass, or
allowed with one nobody read.

**Labels freeze at publication.** A guardrail whose questions can be edited
after the answers were marked is a guardrail that can be made to pass by editing
the exam. ``labels_hash`` is computed at publication over the canonical JSON of
every label, recorded on the set and on every certificate issued against it, and
compared at activation.

**A certificate names bytes, not a revision number.** See
``db.models.simulation`` — the hash cannot be wrong about what was tested.

**Nothing here decides what "correct" means.** A label is a human judgement with
a written rationale, and this module only checks whether a candidate agrees with
one. That is the whole design: the alternative is a heuristic that decides a
rubric is better because its scores are higher, which is a machine forming an
opinion about severity — precisely what §13.2 says this system does not do.

**A pass ratio below 1.0 is allowed, and floored.** A set assembled from
disputed complaints legitimately contains cases reasonable people mark
differently, and demanding every single one makes the guardrail impossible to
satisfy and therefore certain to be switched off. Below ``MINIMUM_PASS_RATIO``
it stops being a guardrail at all, so the service refuses it — a threshold of
0.2 does not fail a change, it endorses one.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.db.models.simulation import EvaluationLabel, EvaluationSet, PolicyCertificate
from nemesis.events.canonical import JSONValue, canonicalise
from nemesis.events.store import EventStore
from nemesis.observability.logging import get_logger
from nemesis.policy import service as policy_service
from nemesis.policy.documents import PolicyKind
from nemesis.simulation import bundles, corpus
from nemesis.simulation.engine import CaseOutcome, decide
from nemesis.simulation.errors import (
    SimulationConflictError,
    SimulationNotFoundError,
    SimulationValidationError,
)

log = get_logger(__name__)

#: The lowest pass ratio a set may demand. Below this the guardrail stops
#: distinguishing a candidate that broadly agrees with the labels from one that
#: mostly does not — a threshold of 0.2 does not fail a change, it endorses one,
#: and it does so while appearing on screen as an active control.
MINIMUM_PASS_RATIO: Final = 0.5

#: The most labels one set may hold. A certificate carries per-label findings so
#: a refusal can be explained, and an unbounded set would make that JSONB column
#: unbounded with it. Two hundred labelled complaints is already a substantial
#: piece of human review.
MAX_LABELS: Final = 500

_STATUS_DRAFT: Final = "draft"
_STATUS_PUBLISHED: Final = "published"
_STATUS_RETIRED: Final = "retired"

_VERDICT_PASS: Final = "pass"
_VERDICT_FAIL: Final = "fail"


# ---------------------------------------------------------------------------
# Marking one case against one label
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LabelFinding:
    """Whether a candidate agreed with one human judgement, and where it did not.

    ``expectations`` records what was actually checked. A label may assert one
    thing or five, and a finding that only said "failed" would leave the author
    re-reading the label to work out which clause moved.
    """

    complaint_id: uuid.UUID
    passed: bool
    expectations: tuple[str, ...]
    failures: tuple[str, ...]

    def as_json(self) -> dict[str, Any]:
        return {
            "complaint_id": str(self.complaint_id),
            "passed": self.passed,
            "expectations": list(self.expectations),
            "failures": list(self.failures),
        }


def check_label(label: EvaluationLabel, outcome: CaseOutcome) -> LabelFinding:
    """Compare one outcome against one label's expectations.

    Only the expectations that are *set* are checked. A label asserting a
    severity band says nothing about routing, and treating an unset field as
    "expects null" would fail every candidate for not matching a judgement
    nobody made. The CHECK constraint on the table guarantees at least one is
    set, so this can never be a comparison of nothing that reports success.
    """
    checked: list[str] = []
    failures: list[str] = []

    if label.expected_severity_tier is not None:
        checked.append("severity_tier")
        if outcome.sla.severity_tier != label.expected_severity_tier:
            failures.append(
                f"severity_tier: expected {label.expected_severity_tier!r}, "
                f"got {outcome.sla.severity_tier!r}"
            )
    if label.expected_severity_min is not None:
        checked.append("severity_min")
        if outcome.final_severity < label.expected_severity_min:
            failures.append(
                f"severity: expected at least {label.expected_severity_min}, "
                f"got {outcome.final_severity:.4f}"
            )
    if label.expected_severity_max is not None:
        checked.append("severity_max")
        if outcome.final_severity > label.expected_severity_max:
            failures.append(
                f"severity: expected at most {label.expected_severity_max}, "
                f"got {outcome.final_severity:.4f}"
            )
    if label.expected_safety_fired is not None:
        checked.append("safety_fired")
        if outcome.safety.fired != label.expected_safety_fired:
            failures.append(
                f"safety_fired: expected {label.expected_safety_fired}, got {outcome.safety.fired}"
            )
    if label.expected_department_code is not None:
        checked.append("department_code")
        if outcome.route.department_code != label.expected_department_code:
            failures.append(
                f"department_code: expected {label.expected_department_code!r}, "
                f"got {outcome.route.department_code!r}"
            )
    if label.expected_dedup_outcome is not None:
        checked.append("dedup_outcome")
        if outcome.dedup.outcome != label.expected_dedup_outcome:
            failures.append(
                f"dedup_outcome: expected {label.expected_dedup_outcome!r}, "
                f"got {outcome.dedup.outcome!r}"
            )

    return LabelFinding(
        complaint_id=label.complaint_id,
        passed=not failures,
        expectations=tuple(checked),
        failures=tuple(failures),
    )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


async def get_set(
    session: AsyncSession, *, tenant_id: uuid.UUID, code: str
) -> EvaluationSet | None:
    row = await session.execute(
        select(EvaluationSet).where(
            EvaluationSet.tenant_id == tenant_id, EvaluationSet.code == code
        )
    )
    return row.scalar_one_or_none()


async def require_set(session: AsyncSession, *, tenant_id: uuid.UUID, code: str) -> EvaluationSet:
    found = await get_set(session, tenant_id=tenant_id, code=code)
    if found is None:
        raise SimulationNotFoundError(f"no evaluation set {code!r} for this tenant")
    return found


async def published_set(
    session: AsyncSession, *, tenant_id: uuid.UUID, kind: PolicyKind
) -> EvaluationSet | None:
    """The set gating this kind, or ``None`` if the kind is ungated.

    The query ``policy.service.activate`` runs. Single-valued by the partial
    unique index rather than by ``limit(1)`` — a ``limit`` here would silently
    pick one of two sets and gate against whichever the planner returned first.
    """
    row = await session.execute(
        select(EvaluationSet).where(
            EvaluationSet.tenant_id == tenant_id,
            EvaluationSet.kind == kind.value,
            EvaluationSet.status == _STATUS_PUBLISHED,
        )
    )
    return row.scalar_one_or_none()


async def list_sets(
    session: AsyncSession, *, tenant_id: uuid.UUID, kind: PolicyKind | None = None
) -> list[EvaluationSet]:
    statement = select(EvaluationSet).where(EvaluationSet.tenant_id == tenant_id)
    if kind is not None:
        statement = statement.where(EvaluationSet.kind == kind.value)
    rows = await session.execute(statement.order_by(EvaluationSet.kind, EvaluationSet.code))
    return list(rows.scalars().all())


async def list_labels(
    session: AsyncSession, *, tenant_id: uuid.UUID, set_id: uuid.UUID
) -> list[EvaluationLabel]:
    rows = await session.execute(
        select(EvaluationLabel)
        .where(
            EvaluationLabel.tenant_id == tenant_id,
            EvaluationLabel.evaluation_set_id == set_id,
        )
        .order_by(EvaluationLabel.complaint_id)
    )
    return list(rows.scalars().all())


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


async def create_set(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    code: str,
    name: str,
    kind: PolicyKind,
    description: str,
    pass_ratio: float = 1.0,
) -> EvaluationSet:
    """Open a draft set. It gates nothing until it is published."""
    if not description.strip():
        raise SimulationValidationError(
            "an evaluation set needs a stated purpose. 'Why does this set exist and "
            "who assembled it' is what somebody asks when a certificate refuses an "
            "activation at 3am, and it cannot be reconstructed from a list of "
            "complaint ids."
        )
    if not MINIMUM_PASS_RATIO <= pass_ratio <= 1.0:
        raise SimulationValidationError(
            f"pass_ratio must be between {MINIMUM_PASS_RATIO} and 1.0, got {pass_ratio}. "
            f"A lower threshold does not fail a change, it endorses one — while still "
            f"appearing on screen as an active control."
        )

    evaluation_set = EvaluationSet(
        tenant_id=tenant_id,
        code=code,
        name=name,
        kind=kind.value,
        status=_STATUS_DRAFT,
        description=description.strip(),
        pass_ratio=pass_ratio,
        label_count=0,
    )
    session.add(evaluation_set)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise SimulationConflictError(
            f"an evaluation set with code {code!r} already exists for this tenant"
        ) from exc
    return evaluation_set


async def add_label(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    code: str,
    complaint_id: uuid.UUID,
    rationale: str,
    expected_severity_tier: str | None = None,
    expected_severity_min: float | None = None,
    expected_severity_max: float | None = None,
    expected_safety_fired: bool | None = None,
    expected_department_code: str | None = None,
    expected_dedup_outcome: str | None = None,
    actor_id: uuid.UUID | None = None,
) -> EvaluationLabel:
    """Record one human judgement. Draft sets only.

    The ``rationale`` is mandatory for the same reason a policy's
    ``change_reason`` is: a label is evidence, and evidence that records a
    verdict without its reason cannot be reviewed when the verdict is disputed —
    which for an evaluation set happens the first time it blocks somebody.
    """
    evaluation_set = await require_set(session, tenant_id=tenant_id, code=code)
    _require_draft(evaluation_set)
    if not rationale.strip():
        raise SimulationValidationError(
            "a label needs a rationale: it is a human judgement that will refuse "
            "somebody's activation, and 'because the set says so' is not reviewable"
        )
    expectations = (
        expected_severity_tier,
        expected_severity_min,
        expected_severity_max,
        expected_safety_fired,
        expected_department_code,
        expected_dedup_outcome,
    )
    if all(value is None for value in expectations):
        raise SimulationValidationError(
            "a label must assert something. A label with no expectation passes every "
            "candidate, so a set of them reads as a guardrail and behaves as an "
            "endorsement."
        )
    if evaluation_set.label_count >= MAX_LABELS:
        raise SimulationValidationError(
            f"an evaluation set holds at most {MAX_LABELS} labels; this one is full"
        )

    label = EvaluationLabel(
        tenant_id=tenant_id,
        evaluation_set_id=evaluation_set.id,
        complaint_id=complaint_id,
        rationale=rationale.strip(),
        expected_severity_tier=expected_severity_tier,
        expected_severity_min=expected_severity_min,
        expected_severity_max=expected_severity_max,
        expected_safety_fired=expected_safety_fired,
        expected_department_code=expected_department_code,
        expected_dedup_outcome=expected_dedup_outcome,
        labelled_by=actor_id,
    )
    session.add(label)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise SimulationConflictError(
            f"complaint {complaint_id} is already labelled in set {code!r}; edit that "
            f"label rather than adding a second judgement about the same report"
        ) from exc

    await _recount(session, tenant_id=tenant_id, set_id=evaluation_set.id)
    return label


async def publish_set(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    code: str,
    actor_id: uuid.UUID | None = None,
    correlation_id: str | None = None,
) -> EvaluationSet:
    """Freeze the labels and turn the guardrail on for this kind.

    Any set already published for the same kind is retired first, in the same
    transaction and in that order — the partial unique index would otherwise
    refuse the second publication with an error about an index rather than about
    an evaluation set. The same reasoning, and the same ordering, as
    ``policy.service.activate`` superseding an incumbent.
    """
    evaluation_set = await require_set(session, tenant_id=tenant_id, code=code)
    _require_draft(evaluation_set)

    labels = await list_labels(session, tenant_id=tenant_id, set_id=evaluation_set.id)
    if not labels:
        raise SimulationValidationError(
            "an evaluation set with no labels gates every activation on an exam with "
            "no questions, which every candidate passes. Label some complaints first."
        )

    kind = PolicyKind(evaluation_set.kind)
    incumbent = await published_set(session, tenant_id=tenant_id, kind=kind)
    if incumbent is not None:
        await _set_status(
            session,
            tenant_id=tenant_id,
            set_id=incumbent.id,
            status=_STATUS_RETIRED,
            extra={"retired_at": datetime.now(tz=UTC)},
        )

    digest = labels_hash(labels)
    await _set_status(
        session,
        tenant_id=tenant_id,
        set_id=evaluation_set.id,
        status=_STATUS_PUBLISHED,
        extra={
            "labels_hash": digest,
            "label_count": len(labels),
            "published_at": datetime.now(tz=UTC),
            "published_by": actor_id,
        },
    )

    await _append(
        session,
        tenant_id=tenant_id,
        event_type="evaluation_set_published",
        payload={
            "code": code,
            "kind": kind.value,
            "label_count": len(labels),
            "labels_hash": digest,
            "pass_ratio": evaluation_set.pass_ratio,
            "retired_code": incumbent.code if incumbent is not None else None,
        },
        actor_id=actor_id,
        correlation_id=correlation_id,
    )
    return await require_set(session, tenant_id=tenant_id, code=code)


async def retire_set(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    code: str,
    actor_id: uuid.UUID | None = None,
    correlation_id: str | None = None,
) -> EvaluationSet:
    """Turn the guardrail off for this kind.

    An event, not a quiet update. Removing the control that stops a bad rubric
    reaching production is at least as consequential as changing the rubric, and
    a tenant chain that records the second and not the first would let the
    interesting half of an incident happen off the record.
    """
    evaluation_set = await require_set(session, tenant_id=tenant_id, code=code)
    if evaluation_set.status != _STATUS_PUBLISHED:
        raise SimulationConflictError(
            f"evaluation set {code!r} is {evaluation_set.status}, not published, so "
            f"there is nothing to retire"
        )
    await _set_status(
        session,
        tenant_id=tenant_id,
        set_id=evaluation_set.id,
        status=_STATUS_RETIRED,
        extra={"retired_at": datetime.now(tz=UTC)},
    )
    await _append(
        session,
        tenant_id=tenant_id,
        event_type="evaluation_set_retired",
        payload={"code": code, "kind": evaluation_set.kind},
        actor_id=actor_id,
        correlation_id=correlation_id,
    )
    return await require_set(session, tenant_id=tenant_id, code=code)


# ---------------------------------------------------------------------------
# Certification
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    """What marking a candidate against a set produced."""

    verdict: str
    evaluated: int
    passed: int
    unresolvable: int
    findings: tuple[LabelFinding, ...]
    pass_ratio_required: float

    @property
    def achieved_ratio(self) -> float:
        return self.passed / self.evaluated if self.evaluated else 0.0


async def evaluate_candidate(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    kind: PolicyKind,
    revision: int,
    run_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    correlation_id: str | None = None,
) -> PolicyCertificate:
    """Mark a candidate against the published set and record the verdict.

    Writes a certificate whichever way it goes. A failed evaluation that left no
    row would make "we tried and it failed" indistinguishable from "nobody ran
    one", and the first is the record that matters when the same candidate is
    activated a week later by somebody who did not know.
    """
    evaluation_set = await published_set(session, tenant_id=tenant_id, kind=kind)
    if evaluation_set is None:
        raise SimulationNotFoundError(
            f"this tenant has no published evaluation set for {kind.value}, so there "
            f"is nothing to certify against. Publishing one is what turns the "
            f"guardrail on."
        )

    version = await policy_service.get_version(
        session, tenant_id=tenant_id, kind=kind, revision=revision
    )
    if version is None:
        raise SimulationNotFoundError(f"no {kind.value} revision {revision} for this tenant")

    labels = await list_labels(session, tenant_id=tenant_id, set_id=evaluation_set.id)
    bundle = await bundles.candidate_bundle(
        session, tenant_id=tenant_id, kind=kind, revision=revision
    )
    identifiers = [label.complaint_id for label in labels]
    cases, _ = await corpus.build_cases(session, tenant_id=tenant_id, identifiers=identifiers)
    calendars = await corpus.load_calendars(
        session,
        tenant_id=tenant_id,
        codes={entry.calendar_code for entry in bundle.sla_matrix.body.entries},
    )
    outcomes = {case.complaint_id: decide(bundle, case, calendars=calendars) for case in cases}

    findings: list[LabelFinding] = []
    unresolvable = 0
    for label in labels:
        outcome = outcomes.get(label.complaint_id)
        if outcome is None:
            # Counted apart from a failure, and never as one. "This complaint is
            # no longer reconstructable" is a fact about retention; failing the
            # candidate for it would make every set expire silently as the
            # partitions it names are archived.
            unresolvable += 1
            continue
        findings.append(check_label(label, outcome))

    passed = sum(1 for finding in findings if finding.passed)
    evaluated = len(findings)
    achieved = passed / evaluated if evaluated else 0.0
    verdict = (
        _VERDICT_PASS if evaluated > 0 and achieved >= evaluation_set.pass_ratio else _VERDICT_FAIL
    )

    certificate = PolicyCertificate(
        tenant_id=tenant_id,
        kind=kind.value,
        candidate_content_hash=version.content_hash,
        candidate_revision=revision,
        evaluation_set_id=evaluation_set.id,
        labels_hash=evaluation_set.labels_hash or "",
        run_id=run_id,
        verdict=verdict,
        labels_evaluated=evaluated,
        labels_passed=passed,
        labels_unresolvable=unresolvable,
        findings={"labels": [finding.as_json() for finding in findings]},
        issued_by=actor_id,
    )
    session.add(certificate)
    await session.flush()

    await _append(
        session,
        tenant_id=tenant_id,
        event_type="policy_certified",
        payload={
            "kind": kind.value,
            "revision": revision,
            "content_hash": version.content_hash,
            "evaluation_set_code": evaluation_set.code,
            "labels_hash": evaluation_set.labels_hash,
            "verdict": verdict,
            "labels_evaluated": evaluated,
            "labels_passed": passed,
            "labels_unresolvable": unresolvable,
        },
        actor_id=actor_id,
        correlation_id=correlation_id,
    )
    log.info(
        "policy_certified",
        tenant_id=str(tenant_id),
        kind=kind.value,
        revision=revision,
        verdict=verdict,
        labels_passed=passed,
        labels_evaluated=evaluated,
    )
    return certificate


def labels_hash(labels: Sequence[EvaluationLabel]) -> str:
    """SHA-256 over the canonical JSON of every label, ordered by complaint.

    RFC 8785 canonicalisation, the same function the event chain and the policy
    content hash use — so two databases holding the same judgements produce the
    same digest, and "is this the set the certificate was issued against" is a
    string comparison rather than a row-by-row diff.

    Ordered by complaint id rather than by insertion, because the digest must
    not depend on the sequence somebody happened to label in.
    """
    payload: list[JSONValue] = [
        {
            "complaint_id": str(label.complaint_id),
            "expected_severity_tier": label.expected_severity_tier,
            "expected_severity_min": label.expected_severity_min,
            "expected_severity_max": label.expected_severity_max,
            "expected_safety_fired": label.expected_safety_fired,
            "expected_department_code": label.expected_department_code,
            "expected_dedup_outcome": label.expected_dedup_outcome,
        }
        for label in sorted(labels, key=lambda item: str(item.complaint_id))
    ]
    encoded: JSONValue = {"labels": payload}
    return hashlib.sha256(canonicalise(encoded)).hexdigest()


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _require_draft(evaluation_set: EvaluationSet) -> None:
    if evaluation_set.status == _STATUS_DRAFT:
        return
    raise SimulationConflictError(
        f"evaluation set {evaluation_set.code!r} is {evaluation_set.status} and its "
        f"labels are frozen. A set whose questions can be edited after certificates "
        f"were issued against it is one that can be made to pass by editing the exam. "
        f"Create a new set and publish it — publishing retires this one."
    )


async def _set_status(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    set_id: uuid.UUID,
    status: str,
    extra: dict[str, Any] | None = None,
) -> None:
    """An explicit UPDATE with its own tenant predicate.

    Never a mutation of the loaded instance: a dirty-object flush emits
    ``UPDATE ... WHERE id = ?`` with no tenant filter, which the runtime guard
    refuses from a stack frame nowhere near the assignment. The same discipline
    ``policy.service`` and ``control_plane.taxonomy`` follow, for the reason
    Phase 6's defect #1 records.
    """
    await session.execute(
        update(EvaluationSet)
        .where(EvaluationSet.tenant_id == tenant_id, EvaluationSet.id == set_id)
        .values(status=status, version=EvaluationSet.version + 1, **(extra or {}))
    )
    await session.flush()


async def _recount(session: AsyncSession, *, tenant_id: uuid.UUID, set_id: uuid.UUID) -> None:
    total = await session.execute(
        select(func.count())
        .select_from(EvaluationLabel)
        .where(
            EvaluationLabel.tenant_id == tenant_id,
            EvaluationLabel.evaluation_set_id == set_id,
        )
    )
    await session.execute(
        update(EvaluationSet)
        .where(EvaluationSet.tenant_id == tenant_id, EvaluationSet.id == set_id)
        .values(label_count=int(total.scalar() or 0), version=EvaluationSet.version + 1)
    )
    await session.flush()


async def _append(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any],
    actor_id: uuid.UUID | None,
    correlation_id: str | None,
) -> None:
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
    "MAX_LABELS",
    "MINIMUM_PASS_RATIO",
    "EvaluationResult",
    "LabelFinding",
    "add_label",
    "check_label",
    "create_set",
    "evaluate_candidate",
    "get_set",
    "labels_hash",
    "list_labels",
    "list_sets",
    "publish_set",
    "published_set",
    "require_set",
    "retire_set",
]
