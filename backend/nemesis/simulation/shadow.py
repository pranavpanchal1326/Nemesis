"""Shadow mode: what a candidate would have decided, recorded, never acted on.

Backtesting answers "what would this change have done to last year". Shadow mode
answers the question backtesting cannot: **what is it doing to today**, against
traffic nobody has seen yet, including the categories, phrasings and photograph
quality that did not exist when the window closed. A rubric that backtests
cleanly and diverges on 40% of this morning's reports is telling you something
the historical run had no way to know.

The whole design is arranged around one clause of the phase gate — *shadow mode
provably cannot mutate state or emit domain events* — and around the observation
that the natural implementation violates it by accident. A shadow evaluator that
"just" also updates a counter, warms a cache, or records a metric row has become
a second writer on the decision path, and the first symptom is a duplicated
event on a citizen's complaint chain.

So the split is structural rather than careful:

``observe`` **runs under** ``readonly.read_only``, which hands it a session on
its own connection where two layers refuse every write — Postgres's own ``SET
TRANSACTION READ ONLY`` and a statement guard. It returns *values*. It cannot
record anything, so it cannot be made to.

``record`` **is a separate call on the caller's session**, writing one row to
``shadow_observations``: a table no decision path reads, that no domain
projector touches, and that holds no state any pipeline stage consults. A test
walks the import graph to assert that nothing in ``policy`` or ``pipeline``
imports the model.

**``observe`` reads committed data**, because its transaction is not the
caller's — see ``readonly``. A caller that has just written the candidate it
wants observed must commit first. That is the correct reading for shadow mode
(an observation should describe what other readers can see) and it is stated
here because the alternative is somebody discovering it as an empty result.

**Divergence is stored, agreement is counted.** Ninety-eight percent of
observations agree, and storing both outcomes for every one of them would make
this the largest table in the system inside a week for no information — the
digests already prove agreement. What is stored in full is what differed, which
is the only part anybody reads.

**Nothing here samples.** A shadow runner that watched one complaint in ten
would report a divergence rate with a confidence interval nobody computed, and
the sampling would be invisible in the output. If shadow mode is too expensive
for full traffic, the honest fix is to run it on fewer *candidates*, not on
fewer citizens' reports.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.db.models.simulation import ShadowObservation
from nemesis.flags import get_flags
from nemesis.observability.logging import get_logger
from nemesis.policy.documents import PolicyKind
from nemesis.simulation import bundles, corpus
from nemesis.simulation.engine import DecisionCase, PolicyBundle, decide
from nemesis.simulation.errors import SimulationNotFoundError
from nemesis.simulation.readonly import read_only

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Observation:
    """One complaint, decided twice, with nothing written.

    A value, deliberately — the whole point of ``observe`` returning rather than
    persisting is that the evaluation half cannot write. Turning this into a row
    is ``record``'s job, on a session that is allowed to.
    """

    complaint_id: uuid.UUID
    kind: PolicyKind
    candidate_revision: int | None
    candidate_content_hash: str
    live_stamps: dict[str, str]
    live_digest: str
    candidate_digest: str
    diverged: bool
    difference: dict[str, Any] | None


async def observe(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    kind: PolicyKind,
    revision: int,
    complaint_ids: Sequence[uuid.UUID],
) -> list[Observation]:
    """Decide the named complaints under both the live and the candidate bundle.

    Every statement this issues runs inside ``read_only``, so the guarantee
    holds over the *whole* evaluation — the bundle reads, the corpus fold, the
    calendar loads — rather than only over the part somebody remembered to
    guard. A helper added here next year that writes will fail loudly on its
    first execution instead of quietly becoming a second writer.

    The kill switch is checked here rather than at the HTTP boundary, so it
    covers every caller — the endpoint, a future Celery task, an operator's
    script — rather than the one that happened to be written first. Killed, it
    returns no observations, which records nothing and changes nothing: shadow
    mode never made a decision, so turning it off cannot un-make one.
    """
    if not await get_flags().is_enabled("simulation_shadow_mode", tenant_id=str(tenant_id)):
        log.info("shadow_mode_disabled", tenant_id=str(tenant_id), kind=kind.value)
        return []

    async with read_only(session) as reader:
        live = await bundles.live_bundle(reader, tenant_id=tenant_id)
        candidate = await bundles.candidate_bundle(
            reader, tenant_id=tenant_id, kind=kind, revision=revision
        )
        cases, _ = await corpus.build_cases(reader, tenant_id=tenant_id, identifiers=complaint_ids)
        calendars = await corpus.load_calendars(
            reader,
            tenant_id=tenant_id,
            codes={
                entry.calendar_code
                for bundle in (live, candidate)
                for entry in bundle.sla_matrix.body.entries
            },
        )

    resolved = candidate.resolved_for(kind)
    if resolved is None:  # pragma: no cover - candidate_bundle always fills the kind
        raise SimulationNotFoundError(f"candidate bundle carries no {kind.value}")

    return [
        _observe_one(
            case,
            live=live,
            candidate=candidate,
            kind=kind,
            revision=resolved.revision,
            content_hash=resolved.content_hash,
            calendars=calendars,
        )
        for case in cases
    ]


def _observe_one(
    case: DecisionCase,
    *,
    live: PolicyBundle,
    candidate: PolicyBundle,
    kind: PolicyKind,
    revision: int | None,
    content_hash: str,
    calendars: dict[str | None, Any],
) -> Observation:
    before = decide(live, case, calendars=calendars)
    after = decide(candidate, case, calendars=calendars)
    left, right = before.comparable(), after.comparable()
    changed = {key: [left[key], right[key]] for key in sorted(left) if left[key] != right[key]}
    return Observation(
        complaint_id=case.complaint_id,
        kind=kind,
        candidate_revision=revision,
        candidate_content_hash=content_hash,
        live_stamps=live.stamps(),
        live_digest=before.digest(),
        candidate_digest=after.digest(),
        diverged=bool(changed),
        difference=changed or None,
    )


async def record(
    session: AsyncSession, *, tenant_id: uuid.UUID, observations: Sequence[Observation]
) -> int:
    """Persist observations, skipping any already recorded.

    Idempotent by the unique constraint on (complaint, candidate) rather than by
    a pre-check: a shadow worker that restarts mid-batch re-observes complaints
    it already wrote, and a read-then-write would let two workers both see
    "absent" and both insert — doubling every count in the divergence rate,
    which is the one number this table exists to produce.

    Each row is flushed in its own savepoint so one collision does not discard
    the batch. The alternative — one flush for the batch, retried on conflict —
    re-runs the whole batch to skip one row.
    """
    written = 0
    for observation in observations:
        row = ShadowObservation(
            tenant_id=tenant_id,
            complaint_id=observation.complaint_id,
            kind=observation.kind.value,
            candidate_revision=observation.candidate_revision,
            candidate_content_hash=observation.candidate_content_hash,
            live_stamps=observation.live_stamps,
            live_digest=observation.live_digest,
            candidate_digest=observation.candidate_digest,
            diverged=observation.diverged,
            difference=observation.difference,
        )
        try:
            async with session.begin_nested():
                session.add(row)
                await session.flush()
        except IntegrityError:
            continue
        written += 1
    return written


@dataclass(frozen=True, slots=True)
class ShadowSummary:
    """The divergence rate for one candidate, and what it disagreed about."""

    candidate_content_hash: str
    observed: int
    diverged: int
    #: ``field -> count``, so "it only ever moves the SLA" and "it reroutes a
    #: third of everything" are distinguishable at a glance. A single rate
    #: cannot tell those apart, and they call for different decisions.
    fields: dict[str, int]

    @property
    def divergence_rate(self) -> float:
        return self.diverged / self.observed if self.observed else 0.0


async def summarise(
    session: AsyncSession, *, tenant_id: uuid.UUID, content_hash: str
) -> ShadowSummary:
    """Aggregate what shadow mode has seen for one candidate."""
    totals = await session.execute(
        select(func.count(), func.count().filter(ShadowObservation.diverged)).where(
            ShadowObservation.tenant_id == tenant_id,
            ShadowObservation.candidate_content_hash == content_hash,
        )
    )
    observed, diverged = totals.one()

    rows = await session.execute(
        select(ShadowObservation.difference).where(
            ShadowObservation.tenant_id == tenant_id,
            ShadowObservation.candidate_content_hash == content_hash,
            ShadowObservation.diverged.is_(True),
        )
    )
    fields: dict[str, int] = {}
    for (difference,) in rows.all():
        for key in difference or {}:
            fields[key] = fields.get(key, 0) + 1

    return ShadowSummary(
        candidate_content_hash=content_hash,
        observed=int(observed or 0),
        diverged=int(diverged or 0),
        fields=dict(sorted(fields.items())),
    )


__all__ = ["Observation", "ShadowSummary", "observe", "record", "summarise"]
