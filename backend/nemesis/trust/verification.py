"""The trust-verification stage — §11.1, §11.3, §22.1 in one atomic unit.

**Why one stage and not four.** Each check is independent and they could each be
their own hop in the graph. They are not, for a reason that is specific to this
phase rather than general: three of the four need the *image bytes*, and a hop
per check would decode the same JPEG three times, on the worker with the
tightest memory cap in the system. The fourth (abuse) needs a database round
trip that costs nothing next to a decode. So the unit of work is "everything we
learn from this submission's media plus its submitter", which is also the unit a
reviewer is shown.

The cost of that choice is stated rather than absorbed: a failure in any check
degrades the whole stage. The declared fallback is ``HALTED_FOR_REVIEW``, which
parks the report for a human — never lost, never scored as though it had been
checked, and never served with an unredacted image.

**Order is load-bearing, in one place.** EXIF and the perceptual hash are read
from the *original* bytes and must run before redaction, because the redacted
copy has no EXIF by construction and differs from its source wherever a face
was blurred. Redaction then runs, and nothing downstream ever sees the original.

**What this stage does not do.** It does not decide. Trust moves, flags are
raised, and items reach the §11.4 queue — but the complaint's status is
untouched except by the safety fail-safe, which is a different stage. §11.3 says
coordinated-abuse detection *flags, does not auto-block*, and the way to keep
that true a year from now is for the code that could block to not exist.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert

from nemesis.domain.lifecycle import EntityType
from nemesis.flags import get_flags
from nemesis.observability import metrics
from nemesis.observability.logging import get_logger
from nemesis.pipeline.stages import (
    EmittedEvent,
    StageContext,
    StagePermanentError,
    StageResult,
)
from nemesis.policy.documents import TrustThresholds
from nemesis.policy.resolver import RESOLVER
from nemesis.trust import abuse, exif, phash
from nemesis.trust.errors import RedactionFailedError, RedactionUnavailableError
from nemesis.trust.redaction import redact_image
from nemesis.trust.review import ReviewReason, queue
from nemesis.trust.stores import media_store, redacted_store

log = get_logger(__name__)

COMPLAINT = EntityType.COMPLAINT


@dataclass
class _Accumulator:
    """Events, review items, and the running trust delta for one submission.

    A mutable accumulator rather than a chain of pure functions returning
    tuples. The checks are genuinely sequential — the perceptual hash needs the
    capture time EXIF produced, the low-trust backstop needs every other delta —
    and threading six values through six signatures would obscure that the
    order is a dependency rather than a style.
    """

    emitted: list[EmittedEvent] = field(default_factory=list)
    trust_delta: float = 0.0
    #: Reason → evidence bundle. A dict so a check that fires twice (it cannot
    #: today) would replace rather than double-queue, matching the queue's own
    #: one-open-item-per-reason rule.
    reviews: dict[ReviewReason, dict[str, Any]] = field(default_factory=dict)

    def event(self, complaint_id: uuid.UUID, event_type: str, payload: dict[str, Any]) -> None:
        self.emitted.append(
            EmittedEvent(
                entity_type=COMPLAINT,
                entity_id=complaint_id,
                event_type=event_type,
                payload=payload,
            )
        )


async def trust_stage(ctx: StageContext) -> StageResult:
    """Run every §11 check this build can run, and record what each concluded."""
    resolved = await RESOLVER.trust_thresholds(ctx.session, tenant_id=ctx.tenant_id)
    policy: TrustThresholds = resolved.body
    stamp = resolved.stamp

    reported_at = _reported_at(ctx.state)
    accumulator = _Accumulator()

    photo_uri = _optional_str(ctx.state.get("photo_url"))
    if photo_uri is not None:
        await _process_image(
            ctx,
            policy=policy,
            stamp=stamp,
            photo_uri=photo_uri,
            reported_at=reported_at,
            accumulator=accumulator,
        )

    if await get_flags().is_enabled("trust_abuse_detection", tenant_id=str(ctx.tenant_id)):
        await _detect_abuse(
            ctx, policy=policy, stamp=stamp, reported_at=reported_at, accumulator=accumulator
        )
    else:
        # Logged, not silent. A detector that has been killed and a detector
        # that found nothing produce identical output — no event, no flag — and
        # the difference is the whole question when somebody asks a week later
        # why the review queue went quiet.
        log.info(
            "abuse_detection_disabled",
            complaint_id=str(ctx.complaint_id),
            flag="trust_abuse_detection",
            note="§11.3 detectors skipped by kill switch; §11.1 checks still ran",
        )

    # The backstop, last, because it reads every other check's contribution.
    # §11.4's point is that no flag is a dead end; this is the converse — three
    # signals too mild to queue individually must not add up to nothing.
    #
    # **Only when nothing else queued.** If a detector already raised an item, a
    # human is already looking at this report, and a second item saying "and the
    # trust score is low" is a second judgement about the same evidence that
    # adds nothing the first does not show. The backstop exists for the case
    # where the individual checks all declined — which is exactly when a tenant
    # has turned queueing off on the noisy ones, and precisely when the total
    # is the only thing left that can reach a person.
    final_trust = round(float(ctx.state.get("trust_score", 0.0)) + accumulator.trust_delta, 6)
    if final_trust <= policy.review_trust_floor and not accumulator.reviews:
        accumulator.reviews[ReviewReason.LOW_TRUST] = {
            "trust_score": final_trust,
            "floor": policy.review_trust_floor,
            "policy_version": stamp,
            "contributions": round(accumulator.trust_delta, 6),
        }

    for reason, evidence in accumulator.reviews.items():
        queued = await queue(
            ctx.session,
            tenant_id=ctx.tenant_id,
            complaint_id=ctx.complaint_id,
            reason=reason,
            evidence=evidence,
            trust_score=final_trust,
        )
        accumulator.event(
            ctx.complaint_id,
            "review_queued",
            {
                "review_item_id": str(queued.review_item_id),
                "reason": queued.reason.value,
                "priority": queued.priority,
                "occurrences": queued.occurrences,
                "trust_score": queued.trust_score,
                "evidence_hash": queued.evidence_hash,
            },
        )

    log.info(
        "trust_verification_completed",
        complaint_id=str(ctx.complaint_id),
        policy_version=stamp,
        trust_delta=round(accumulator.trust_delta, 6),
        trust_score=final_trust,
        reviews=[reason.value for reason in accumulator.reviews],
        events=len(accumulator.emitted),
    )
    # Never halts. Every §11.1/§11.3 outcome is a flag, and the report continues
    # to classification — which is what "reduces trust rather than rejecting"
    # and "flags, does not auto-block" mean when written as control flow.
    return StageResult(emitted=tuple(accumulator.emitted))


async def _process_image(
    ctx: StageContext,
    *,
    policy: TrustThresholds,
    stamp: str,
    photo_uri: str,
    reported_at: datetime,
    accumulator: _Accumulator,
) -> None:
    """Read the original once, and learn everything from that one read."""
    source = _read_quarantined(photo_uri)

    metadata = exif.extract(source)
    finding = exif.cross_check(
        metadata,
        claimed_latitude=float(ctx.state["latitude"]),
        claimed_longitude=float(ctx.state["longitude"]),
        mismatch_distance_meters=policy.exif.mismatch_distance_meters,
        matched_trust_delta=policy.exif.matched_trust_delta,
        mismatch_trust_delta=policy.exif.mismatch_trust_delta,
        absent_trust_delta=policy.exif.absent_trust_delta,
    )
    accumulator.trust_delta += finding.trust_delta
    accumulator.event(
        ctx.complaint_id,
        "exif_check_completed",
        {
            "exif_present": finding.present,
            "distance_meters": finding.distance_meters,
            "trust_delta": finding.trust_delta,
            "reason": finding.reason,
        },
    )
    if finding.is_mismatch and policy.exif.mismatch_queues_review:
        accumulator.reviews[ReviewReason.EXIF_MISMATCH] = {
            "outcome": finding.outcome.value,
            "distance_meters": finding.distance_meters,
            "threshold_meters": policy.exif.mismatch_distance_meters,
            "claimed": {
                "latitude": ctx.state["latitude"],
                "longitude": ctx.state["longitude"],
            },
            "exif": {"latitude": metadata.latitude, "longitude": metadata.longitude},
            "policy_version": stamp,
        }

    # The capture time, when the camera recorded one. Falling back to the
    # report's own time rather than to "now": the stage may run minutes or
    # (after a degradation) days after submission, and a window anchored on
    # processing time would silently widen with every retry.
    captured_at = metadata.captured_at or reported_at

    image_hash = phash.perceptual_hash(source)
    if image_hash is not None and policy.perceptual_hash.is_active:
        await _check_duplicates(
            ctx,
            policy=policy,
            stamp=stamp,
            image_hash=image_hash,
            captured_at=captured_at,
            accumulator=accumulator,
        )

    result = _redact(source)
    metrics.media_faces_blurred.observe(result.faces_blurred)
    accumulator.event(
        ctx.complaint_id,
        "media_redacted",
        {
            "source_sha256": _sha_of(photo_uri, source),
            "redacted_sha256": result.sha256,
            "media_kind": "image",
            "content_type": result.content_type,
            "faces_detected": result.faces_detected,
            "faces_blurred": result.faces_blurred,
            "detector_id": result.detector_id,
            "exif_stripped": result.exif_stripped,
        },
    )

    await _record_media(
        ctx,
        policy=policy,
        photo_uri=photo_uri,
        source=source,
        metadata=metadata,
        finding=finding,
        captured_at=captured_at,
        image_hash=image_hash,
        redacted_uri=result.uri,
        redacted_sha256=result.sha256,
        faces_detected=result.faces_detected,
        faces_blurred=result.faces_blurred,
        detector_id=result.detector_id,
    )


async def _check_duplicates(
    ctx: StageContext,
    *,
    policy: TrustThresholds,
    stamp: str,
    image_hash: int,
    captured_at: datetime,
    accumulator: _Accumulator,
) -> None:
    matches = await phash.find_duplicates(
        ctx.session,
        tenant_id=ctx.tenant_id,
        complaint_id=ctx.complaint_id,
        image_hash=image_hash,
        captured_at=captured_at,
        max_distance=policy.perceptual_hash.max_hamming_distance,
        lookback_hours=policy.perceptual_hash.lookback_hours,
    )
    if not matches:
        return

    # The closest match only. `find_duplicates` orders by distance, and an event
    # per match would put twelve near-identical rows on the chain of a complaint
    # that re-used a stock photograph — the evidence bundle carries the rest.
    closest = matches[0]
    metrics.perceptual_duplicates_total.inc()
    accumulator.trust_delta += policy.perceptual_hash.trust_delta
    accumulator.event(
        ctx.complaint_id,
        "perceptual_duplicate_detected",
        {
            "matched_complaint_id": str(closest.complaint_id),
            "matched_media_sha256": closest.media_sha256,
            "hamming_distance": closest.hamming_distance,
            "threshold": policy.perceptual_hash.max_hamming_distance,
            "age_hours": closest.age_hours,
            "trust_delta": policy.perceptual_hash.trust_delta,
            "policy_version": stamp,
        },
    )
    if policy.perceptual_hash.queues_review:
        accumulator.reviews[ReviewReason.PERCEPTUAL_DUPLICATE] = {
            "threshold": policy.perceptual_hash.max_hamming_distance,
            "lookback_hours": policy.perceptual_hash.lookback_hours,
            "policy_version": stamp,
            "matches": [
                {
                    "complaint_id": str(match.complaint_id),
                    "media_sha256": match.media_sha256,
                    "hamming_distance": match.hamming_distance,
                    "age_hours": round(match.age_hours, 3),
                }
                for match in matches
            ],
        }


async def _detect_abuse(
    ctx: StageContext,
    *,
    policy: TrustThresholds,
    stamp: str,
    reported_at: datetime,
    accumulator: _Accumulator,
) -> None:
    findings: list[abuse.AbuseFinding] = []

    if policy.velocity.is_active:
        velocity = await abuse.assess_device_velocity(
            ctx.session,
            tenant_id=ctx.tenant_id,
            complaint_id=ctx.complaint_id,
            device_fingerprint=_optional_str(ctx.state.get("submitter_device_fingerprint")),
            at=reported_at,
            window_hours=policy.velocity.window_hours,
            max_submissions=policy.velocity.max_submissions_per_window,
            trust_delta=policy.velocity.trust_delta,
        )
        if velocity is not None:
            findings.append(velocity)

    if policy.geo_cluster.is_active:
        findings.append(
            await abuse.assess_geographic_cluster(
                ctx.session,
                tenant_id=ctx.tenant_id,
                complaint_id=ctx.complaint_id,
                latitude=float(ctx.state["latitude"]),
                longitude=float(ctx.state["longitude"]),
                at=reported_at,
                radius_meters=policy.geo_cluster.radius_meters,
                window_hours=policy.geo_cluster.window_hours,
                min_distinct_devices=policy.geo_cluster.min_distinct_devices,
                trust_delta=policy.geo_cluster.trust_delta,
            )
        )

    queues = {
        abuse.AbusePattern.DEVICE_VELOCITY: policy.velocity.queues_review,
        abuse.AbusePattern.GEOGRAPHIC_CLUSTER: policy.geo_cluster.queues_review,
    }
    reasons = {
        abuse.AbusePattern.DEVICE_VELOCITY: ReviewReason.DEVICE_VELOCITY,
        abuse.AbusePattern.GEOGRAPHIC_CLUSTER: ReviewReason.GEOGRAPHIC_CLUSTER,
    }
    for found in findings:
        if not found.fired:
            continue
        metrics.abuse_patterns_total.labels(pattern=found.pattern.value).inc()
        accumulator.trust_delta += found.trust_delta
        accumulator.event(
            ctx.complaint_id,
            "abuse_pattern_flagged",
            {
                "pattern": found.pattern.value,
                "observation_count": found.observation_count,
                "window_hours": found.window_hours,
                "trust_delta": found.trust_delta,
                "policy_version": stamp,
                "evidence": found.evidence,
            },
        )
        if queues[found.pattern]:
            accumulator.reviews[reasons[found.pattern]] = {
                "reason": found.reason,
                "policy_version": stamp,
                **found.evidence,
            }


async def _record_media(
    ctx: StageContext,
    *,
    policy: TrustThresholds,
    photo_uri: str,
    source: bytes,
    metadata: exif.ExifData,
    finding: exif.ExifFinding,
    captured_at: datetime,
    image_hash: int | None,
    redacted_uri: str,
    redacted_sha256: str,
    faces_detected: int,
    faces_blurred: int,
    detector_id: str,
) -> None:
    """Index the artefact, with its §22.4 expiry stamped on at processing time.

    ``ON CONFLICT DO UPDATE`` rather than ``DO NOTHING``: a redelivery that got
    past the orchestrator's guard — possible only when the stage emitted no
    events, which cannot happen once there is an image — must converge on the
    same row rather than leave a half-written one. Every value written here is
    a pure function of the source bytes and the policy, so the update is
    idempotent by construction.
    """
    now = datetime.now(tz=UTC)
    values = {
        "tenant_id": ctx.tenant_id,
        "complaint_id": ctx.complaint_id,
        "kind": "image",
        "content_type": "image/jpeg",
        "quarantine_uri": photo_uri,
        "quarantine_sha256": _sha_of(photo_uri, source),
        "redacted_uri": redacted_uri,
        "redacted_sha256": redacted_sha256,
        "redacted_at": now,
        "faces_detected": faces_detected,
        "faces_blurred": faces_blurred,
        "detector_id": detector_id,
        "perceptual_hash": None if image_hash is None else phash.to_signed(image_hash),
        "exif_present": finding.present,
        "exif_latitude": metadata.latitude,
        "exif_longitude": metadata.longitude,
        "exif_distance_meters": finding.distance_meters,
        "exif_captured_at": metadata.captured_at,
        "captured_or_reported_at": captured_at,
        "purge_raw_after": now + timedelta(days=policy.retention.raw_media_days),
        "purge_exif_after": now + timedelta(days=policy.retention.exif_days),
    }
    from nemesis.db.models.trust import SubmissionMedia  # see below

    await ctx.session.execute(
        pg_insert(SubmissionMedia)
        .values(**values)
        .on_conflict_do_update(
            constraint="uq_submission_media_complaint_source",
            set_={
                key: value
                for key, value in values.items()
                # The identity columns are the conflict target; re-setting them
                # to themselves is noise in the generated SQL and a trap for
                # anyone who later adds a column to the constraint.
                if key not in {"tenant_id", "complaint_id", "quarantine_sha256"}
            },
        )
    )


def _redact(source: bytes) -> Any:
    """§22.1, with the two failure modes mapped onto the right stage errors.

    A missing detector and an undecodable image both mean this complaint's photo
    will not be redacted on this attempt, and they want opposite retry
    behaviour: the detector will still be missing in thirty seconds, and so will
    the malformed JPEG. Both therefore skip the budget — but they are raised as
    ``StagePermanentError`` rather than swallowed, so the report degrades to
    ``HALTED_FOR_REVIEW`` with the reason on its own chain.

    There is deliberately no third branch that proceeds without redacting.
    """
    try:
        result = redact_image(source, store=redacted_store())
    except RedactionUnavailableError as exc:
        metrics.media_redactions_total.labels(outcome="unavailable").inc()
        raise StagePermanentError(str(exc)) from exc
    except RedactionFailedError as exc:
        metrics.media_redactions_total.labels(outcome="failed").inc()
        raise StagePermanentError(str(exc)) from exc
    metrics.media_redactions_total.labels(outcome="redacted").inc()
    return result


def _read_quarantined(photo_uri: str) -> bytes:
    """The one read of the quarantine root in this repository.

    ``check_media_redaction.py`` asserts that, by parsing every module for a
    call to ``MediaStore.resolve``. Concentrating it here is what makes the
    §22.1 guarantee a property of the code's shape rather than of everyone
    remembering — see ``trust.redaction``'s docstring for the full argument.
    """
    path = media_store().resolve(photo_uri)
    try:
        return path.read_bytes()
    except FileNotFoundError as exc:
        raise StagePermanentError(
            f"the quarantined upload for {photo_uri!r} is not on disk. Either the "
            f"§22.4 retention sweep removed it before this stage ran — check the "
            f"complaint's age against the tenant's raw_media_days — or the volume "
            f"is not mounted on this worker."
        ) from exc


def _sha_of(photo_uri: str, source: bytes) -> str:
    """The upload's content address, taken from the URI it is stored under.

    Recomputed from the bytes only if the URI does not carry it. The URI *is*
    the digest — ``MediaStore`` names every file by its SHA-256 — so parsing it
    is exact and free, while re-hashing 15 MB on every submission is neither.
    The fallback exists because this function's input comes out of an event
    payload, and a payload is where an unexpected value survives longest.
    """
    import hashlib  # used only on the fallback path

    stem = photo_uri.rsplit("/", 1)[-1].split(".", 1)[0]
    if len(stem) == 64 and all(character in "0123456789abcdef" for character in stem):
        return stem
    return hashlib.sha256(source).hexdigest()


def _reported_at(state: Any) -> datetime:
    """The complaint's own submission time, from its own chain.

    ``datetime.now()`` is deliberately not the fallback for a malformed value:
    every window in this stage is anchored here, and anchoring on the clock
    would make a report that degraded and retried three days later compare
    against a different three days of history than the first attempt did.
    """
    raw = state.get("reported_at")
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:  # pragma: no cover — projections write ISO timestamps
            pass
    raise StagePermanentError(
        "the complaint's projected state carries no usable reported_at, so every "
        "§11.3 window would be anchored on the wrong instant. This means the "
        "chain does not start with a complaint_submitted event."
    )


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


__all__ = ["trust_stage"]
