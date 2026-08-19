"""Assembling the two configurations a backtest compares.

A backtest is a comparison between two *bundles*, and almost every way of
getting one wrong is a way of getting the comparison wrong quietly:

**Reading the candidate through the resolver.** The resolver's only read path
filters on ``active``, which is exactly right for production and useless here —
the whole point is to evaluate a document *before* anybody approves it. So the
candidate is read by revision, directly, through ``service.require_version``.

**Reading the baseline through the cache.** ``RESOLVER`` holds a thirty-second
snapshot. A backtest that read it could compare a candidate against a rubric
that was superseded twenty seconds ago and report a delta against a
configuration that is no longer live. Every bundle here is built by an
uncached resolver, constructed per call.

**Letting a draft escape.** Nothing in this module writes to ``RESOLVER``'s
cache or returns anything the pipeline reads. A candidate ``Resolved`` is a
value that exists for the duration of a run; the gate clause "an unapproved
draft can never influence a production decision" is not weakened by evaluating
one, and a test asserts that a run over a draft leaves the live document
unchanged.

**Two kinds of baseline, and the difference matters.** ``live_bundle`` answers
*"what would change if I activated this now"* — the question an operator asks
before pressing the button. ``bundle_at`` answers *"what was deciding on 14
March"* — the question a dispute asks, and the one shadow mode needs to compare
against what actually happened. They are different questions with different
answers, and offering only the first is how a backtest quietly starts claiming
that a change moved reports that a *previous* change had already moved.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.policy import baselines, service
from nemesis.policy.documents import PolicyBody, PolicyKind, validate_body
from nemesis.policy.resolver import BASELINE_STAMP, PolicyResolver, Resolved
from nemesis.simulation.engine import PolicyBundle
from nemesis.simulation.errors import SimulationNotFoundError

#: The kinds a bundle needs before it can decide anything. Routing rules and
#: rate cards are absent deliberately — they have no platform baseline, and a
#: tenant that has authored neither still scores, still triages, and still has
#: SLAs. See ``policy.baselines``.
REQUIRED_KINDS: tuple[PolicyKind, ...] = (
    PolicyKind.SEVERITY_RUBRIC,
    PolicyKind.DEDUP_THRESHOLDS,
    PolicyKind.SAFETY_RULESET,
    PolicyKind.SLA_MATRIX,
)


def _uncached() -> PolicyResolver:
    """A resolver that always reads through.

    ``reload_seconds=0`` disables the TTL entirely — the mode ``PolicyResolver``
    documents as the one the backtester uses, for the reason stated there: a
    backtest that read a stale snapshot would report a delta against a policy
    that was not the one it claimed to compare.
    """
    return PolicyResolver(reload_seconds=0)


async def live_bundle(session: AsyncSession, *, tenant_id: uuid.UUID) -> PolicyBundle:
    """What is deciding right now, read fresh.

    Baselines included: a tenant that has never been seeded is running on
    platform defaults, and a backtest that refused to run for it would refuse
    exactly the tenants most likely to need one.
    """
    resolver = _uncached()
    return PolicyBundle(
        severity_rubric=await resolver.severity_rubric(session, tenant_id=tenant_id),
        dedup_thresholds=await resolver.dedup_thresholds(session, tenant_id=tenant_id),
        safety_ruleset=await resolver.safety_ruleset(session, tenant_id=tenant_id),
        sla_matrix=await resolver.sla_matrix(session, tenant_id=tenant_id),
        routing_rules=await resolver.routing_rules(session, tenant_id=tenant_id),
        rate_card=await resolver.rate_card(session, tenant_id=tenant_id),
        trust_thresholds=await resolver.trust_thresholds(session, tenant_id=tenant_id),
        perception_calibration=await resolver.perception_calibration(session, tenant_id=tenant_id),
    )


async def bundle_at(
    session: AsyncSession, *, tenant_id: uuid.UUID, moment: datetime
) -> PolicyBundle:
    """The configuration that was deciding at ``moment``.

    An interval query per kind, through ``service.version_effective_at`` — which
    is only single-valued because rollback moves forward rather than reviving a
    row (ADR-0026). A kind with nothing effective then falls back to the
    platform baseline, stamped ``baseline``, because that is what the resolver
    would have done at the time and a reconstruction that used today's document
    instead would be a fabrication with a plausible revision number on it.
    """
    resolved: dict[PolicyKind, Resolved[PolicyBody] | None] = {}
    for kind in PolicyKind:
        version = await service.version_effective_at(
            session, tenant_id=tenant_id, kind=kind, moment=moment
        )
        if version is not None:
            resolved[kind] = Resolved(
                body=validate_body(kind, version.body),
                stamp=version.stamp,
                version_id=version.id,
                revision=version.revision,
                content_hash=version.content_hash,
                is_baseline=False,
            )
        elif baselines.has_baseline(kind):
            resolved[kind] = _baseline_resolved(kind)
        else:
            resolved[kind] = None

    return _assemble(resolved)


async def candidate_bundle(
    session: AsyncSession, *, tenant_id: uuid.UUID, kind: PolicyKind, revision: int
) -> PolicyBundle:
    """The live configuration with one kind swapped for a specific revision.

    One kind, never several. A bundle carrying two candidate documents produces
    a report whose rows cannot be attributed — "forty complaints changed
    department" is a useless sentence when both the routing rules and the rubric
    that feeds them moved. Evaluating a coordinated pair of changes is a real
    need and it is done by running two backtests, which is slower to read and
    impossible to misread.

    The revision may be in any status, including ``draft``. That is the whole
    point: the report exists so somebody can decide whether to approve it.
    """
    version = await service.get_version(session, tenant_id=tenant_id, kind=kind, revision=revision)
    if version is None:
        raise SimulationNotFoundError(
            f"no {kind.value} revision {revision} for this tenant, so there is nothing "
            f"to evaluate. Draft it first."
        )

    live = await live_bundle(session, tenant_id=tenant_id)
    swapped: Resolved[PolicyBody] = Resolved(
        body=validate_body(kind, version.body),
        stamp=version.stamp,
        version_id=version.id,
        revision=version.revision,
        content_hash=version.content_hash,
        is_baseline=False,
    )
    return replace_kind(live, kind=kind, resolved=swapped)


def replace_kind(
    bundle: PolicyBundle, *, kind: PolicyKind, resolved: Resolved[PolicyBody]
) -> PolicyBundle:
    """A copy of ``bundle`` with one kind's document replaced.

    Written out per kind rather than through ``dataclasses.replace`` with a
    computed field name, for the reason ``PolicyBundle.resolved_for`` gives: the
    attribute names matching the enum values is a coincidence, and code that
    relies on it fails silently rather than loudly when one is renamed.
    """
    current: dict[PolicyKind, Resolved[PolicyBody] | None] = {
        member: bundle.resolved_for(member) for member in PolicyKind
    }
    current[kind] = resolved
    return _assemble(current)


def _assemble(resolved: dict[PolicyKind, Resolved[PolicyBody] | None]) -> PolicyBundle:
    missing = [kind.value for kind in REQUIRED_KINDS if resolved.get(kind) is None]
    if missing:  # pragma: no cover - every required kind has a baseline
        raise SimulationNotFoundError(
            f"cannot build a policy bundle: no document and no baseline for {', '.join(missing)}"
        )
    return PolicyBundle(
        severity_rubric=resolved[PolicyKind.SEVERITY_RUBRIC],  # type: ignore[arg-type]
        dedup_thresholds=resolved[PolicyKind.DEDUP_THRESHOLDS],  # type: ignore[arg-type]
        safety_ruleset=resolved[PolicyKind.SAFETY_RULESET],  # type: ignore[arg-type]
        sla_matrix=resolved[PolicyKind.SLA_MATRIX],  # type: ignore[arg-type]
        routing_rules=resolved[PolicyKind.ROUTING_RULES],  # type: ignore[arg-type]
        rate_card=resolved[PolicyKind.RATE_CARD],  # type: ignore[arg-type]
        trust_thresholds=resolved[PolicyKind.TRUST_THRESHOLDS],  # type: ignore[arg-type]
        perception_calibration=resolved[  # type: ignore[arg-type]
            PolicyKind.PERCEPTION_CALIBRATION
        ],
    )


def _baseline_resolved(kind: PolicyKind) -> Resolved[PolicyBody]:
    body = baselines.baseline_body(kind)
    return Resolved(
        body=body,
        stamp=BASELINE_STAMP,
        version_id=None,
        revision=None,
        content_hash=service.content_hash(body.model_dump(mode="json")),
        is_baseline=True,
    )


__all__ = [
    "REQUIRED_KINDS",
    "bundle_at",
    "candidate_bundle",
    "live_bundle",
    "replace_kind",
]
