"""The trust stage as the pipeline actually runs it.

Every other Phase 8 test file exercises one check in isolation, which is where
the arithmetic can be pinned down. This one runs the whole stage through the
real orchestrator, against a real quarantined file, with a real policy document
— because the things that only go wrong at the seams are the ordering (EXIF and
the hash must be read before redaction strips and blurs), the atomicity (the
media row and the events commit together or not at all), and the failure posture
(§22.1 fails closed).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from nemesis.config import Settings
from nemesis.db.models.complaint import Complaint
from nemesis.db.models.trust import ReviewQueueItem, SubmissionMedia
from nemesis.events.store import EventStore
from nemesis.ingest.media import MEDIA_SCHEME
from nemesis.observability.metrics import PipelineStage
from nemesis.pipeline.orchestrator import execute_stage
from nemesis.pipeline.stages import StagePermanentError, provider_scope
from nemesis.policy import service as policy_service
from nemesis.policy.documents import PolicyKind
from nemesis.policy.resolver import RESOLVER
from nemesis.tenancy.context import tenant_scope
from nemesis.trust import stores
from nemesis.trust.detectors import FaceBox, detector_scope
from nemesis.trust.redaction import RedactedStore
from nemesis.trust.verification import trust_stage
from tests.conftest import postgres_required
from tests.test_trust_review import make_complaint
from tests.trust_fixtures import FixedDetector, image_with_exif, noisy_patch_image

pytestmark = [postgres_required, pytest.mark.integration]

BASE = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
PUNE = (18.5204, 73.8567)
FACE = FaceBox(x=4, y=4, width=24, height=16, confidence=0.9)


@pytest.fixture(autouse=True)
async def close_flag_store() -> AsyncIterator[None]:
    """Dispose the process-global feature-flag client after each test.

    The trust stage reads ``trust_abuse_detection`` through ``get_flags()``,
    which lazily opens a Redis connection and caches it for the process. Nothing
    in a unit test closes it, so the socket is finalised by the garbage
    collector at interpreter shutdown — which under ``filterwarnings =
    ["error"]`` is a ``ResourceWarning`` that fails the run, attributed to
    whichever test happened to trigger the collection.

    The same shape as ``conftest.api_client``'s teardown, applied here because
    this is the other place in the suite that exercises a flag read.
    """
    yield
    from nemesis.flags import close_flags

    await close_flags()


@pytest.fixture
def sessions(migrated_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(migrated_engine, expire_on_commit=False)


@pytest.fixture
def uploads(tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point both media roots at a scratch directory for this test.

    Patched on ``nemesis.trust.stores`` rather than on ``nemesis.config``,
    because ``stores`` binds the name at import — patching the definition site
    would leave the already-bound reference pointing at the real settings, and
    the stage would read an empty production upload directory while every
    assertion here looked correct.

    ``stores.reset`` rather than constructing stores by hand: the caches exist
    so that one process has one root, and a test that reached around them would
    be exercising a configuration production never has.
    """
    monkeypatch.setattr(
        "nemesis.trust.stores.get_settings",
        lambda: settings.model_copy(update={"upload_dir": tmp_path}),
    )
    stores.reset()
    yield tmp_path
    stores.reset()


def quarantine(uploads: Path, data: bytes) -> str:
    """Write ``data`` where ``MediaStore`` would have, and return its URI."""
    import hashlib

    digest = hashlib.sha256(data).hexdigest()
    path = uploads / "quarantine" / digest[:2] / f"{digest}.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return f"{MEDIA_SCHEME}://{digest[:2]}/{digest}.jpg"


async def run_trust(tenant_id: uuid.UUID, complaint_id: uuid.UUID, detector: object) -> object:
    with (
        detector_scope(detector),
        provider_scope(  # type: ignore[arg-type]
            PipelineStage.TRUST_VERIFICATION, trust_stage
        ),
    ):
        return await execute_stage(
            tenant_id=tenant_id,
            complaint_id=complaint_id,
            stage=PipelineStage.TRUST_VERIFICATION.value,
        )


# ---------------------------------------------------------------------------
# The happy path, and the ordering inside it
# ---------------------------------------------------------------------------


async def test_a_photograph_is_checked_hashed_and_redacted_in_one_stage(
    bound_session: None,
    sessions: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
    uploads: Path,
) -> None:
    """The whole §11.1/§22.1 pass, and the order that makes it possible.

    EXIF is read and the hash computed from the *original* bytes; redaction then
    strips and blurs. Reversed, the EXIF check would find nothing on every
    submission and the hash would encode the blur rather than the photograph.
    """
    photo = image_with_exif(latitude=PUNE[0] + 0.0004, longitude=PUNE[1])
    uri = quarantine(uploads, photo)

    with tenant_scope(tenant_id):
        async with sessions() as session:
            await policy_service.seed_baselines(session, tenant_id=tenant_id)
            complaint_id = await make_complaint(
                session,
                tenant_id=tenant_id,
                reported_at=BASE,
                latitude=PUNE[0],
                longitude=PUNE[1],
                photo_uri=uri,
                device_fingerprint="device-a",
            )
            await session.commit()
    RESOLVER.invalidate(tenant_id=tenant_id)

    execution = await run_trust(tenant_id, complaint_id, FixedDetector([FACE]))
    assert execution.next_stage == PipelineStage.CLASSIFICATION.value  # type: ignore[attr-defined]
    # Never halts: every §11.1 outcome is a flag, not a rejection.
    assert not execution.halted  # type: ignore[attr-defined]

    with tenant_scope(tenant_id):
        async with sessions() as session:
            events = [
                event.event_type
                for event in await EventStore(session).read_stream(
                    entity_type="complaint", entity_id=complaint_id
                )
            ]
            media = (
                await session.execute(
                    select(SubmissionMedia).where(
                        SubmissionMedia.tenant_id == tenant_id,
                        SubmissionMedia.complaint_id == complaint_id,
                    )
                )
            ).scalar_one()

    assert events == ["complaint_submitted", "exif_check_completed", "media_redacted"]
    assert media.exif_present
    assert media.exif_latitude == pytest.approx(PUNE[0] + 0.0004, abs=1e-4)
    assert media.exif_distance_meters is not None and media.exif_distance_meters < 200
    assert media.perceptual_hash is not None
    assert media.faces_detected == 1 and media.faces_blurred == 1
    assert media.detector_id == "fixed-test@1"
    # §22.4's clocks, stamped at processing time from the tenant's own policy.
    assert (media.purge_raw_after - media.redacted_at).days == 30  # type: ignore[operator]
    assert (media.purge_exif_after - media.redacted_at).days == 90  # type: ignore[operator]

    # The redacted copy exists and is servable; the original stays in quarantine
    # (§22.4 retains it for 30 days) and no route can reach it.
    assert media.redacted_uri is not None
    assert RedactedStore(uploads).resolve(media.redacted_uri).exists()
    assert (uploads / "quarantine").exists()


async def test_absent_exif_reduces_trust_without_queueing_a_review(
    bound_session: None,
    sessions: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
    uploads: Path,
) -> None:
    """§11.1's common case, end to end.

    A queue that received every WhatsApp submission is a queue nobody reads, so
    absence moves trust and stops there — while a *mismatch*, which is a
    contradiction rather than a silence, does queue.
    """
    uri = quarantine(uploads, noisy_patch_image())

    with tenant_scope(tenant_id):
        async with sessions() as session:
            await policy_service.seed_baselines(session, tenant_id=tenant_id)
            complaint_id = await make_complaint(
                session,
                tenant_id=tenant_id,
                reported_at=BASE,
                latitude=PUNE[0],
                longitude=PUNE[1],
                photo_uri=uri,
            )
            await session.commit()
    RESOLVER.invalidate(tenant_id=tenant_id)

    await run_trust(tenant_id, complaint_id, FixedDetector([]))

    with tenant_scope(tenant_id):
        async with sessions() as session:
            events = await EventStore(session).read_stream(
                entity_type="complaint", entity_id=complaint_id
            )
            items = (
                (
                    await session.execute(
                        select(ReviewQueueItem).where(
                            ReviewQueueItem.tenant_id == tenant_id,
                            ReviewQueueItem.complaint_id == complaint_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            complaint = (
                await session.execute(
                    select(Complaint.status, Complaint.is_fraud_flagged).where(
                        Complaint.tenant_id == tenant_id, Complaint.id == complaint_id
                    )
                )
            ).one()

    exif_event = next(event for event in events if event.event_type == "exif_check_completed")
    assert exif_event.payload["exif_present"] is False
    assert exif_event.payload["trust_delta"] == -0.1
    assert exif_event.payload["distance_meters"] is None
    assert items == []
    # Not rejected, not flagged, not moved out of the pipeline.
    assert complaint == ("submitted", False)


async def test_a_gps_mismatch_flags_and_queues_but_does_not_block(
    bound_session: None,
    sessions: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
    uploads: Path,
) -> None:
    """A contradiction is worth a human's attention; it is not a rejection."""
    photo = image_with_exif(latitude=19.0760, longitude=72.8777)  # Mumbai
    uri = quarantine(uploads, photo)

    with tenant_scope(tenant_id):
        async with sessions() as session:
            await policy_service.seed_baselines(session, tenant_id=tenant_id)
            complaint_id = await make_complaint(
                session,
                tenant_id=tenant_id,
                reported_at=BASE,
                latitude=PUNE[0],
                longitude=PUNE[1],
                photo_uri=uri,
            )
            await session.commit()
    RESOLVER.invalidate(tenant_id=tenant_id)

    execution = await run_trust(tenant_id, complaint_id, FixedDetector([]))
    assert execution.next_stage == PipelineStage.CLASSIFICATION.value  # type: ignore[attr-defined]

    with tenant_scope(tenant_id):
        async with sessions() as session:
            item = (
                await session.execute(
                    select(ReviewQueueItem).where(
                        ReviewQueueItem.tenant_id == tenant_id,
                        ReviewQueueItem.complaint_id == complaint_id,
                    )
                )
            ).scalar_one()
            events = [
                event.event_type
                for event in await EventStore(session).read_stream(
                    entity_type="complaint", entity_id=complaint_id
                )
            ]

    assert item.reason == "exif_mismatch"
    assert item.evidence["distance_meters"] > 100_000
    assert "review_queued" in events


async def test_a_re_uploaded_photograph_is_detected_against_history(
    bound_session: None,
    sessions: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
    uploads: Path,
) -> None:
    """§11.1's re-upload check, through the stage rather than the query.

    The second submission carries a *recompressed* copy — the case §11.1 names
    — so the match is a perceptual one and not a SHA-256 comparison the media
    store already does for free.
    """
    import io

    from PIL import Image

    original = noisy_patch_image()
    with Image.open(io.BytesIO(original)) as image:
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=45)
    recompressed = buffer.getvalue()

    first_uri = quarantine(uploads, original)
    second_uri = quarantine(uploads, recompressed)

    with tenant_scope(tenant_id):
        async with sessions() as session:
            await policy_service.seed_baselines(session, tenant_id=tenant_id)
            first = await make_complaint(
                session, tenant_id=tenant_id, reported_at=BASE, photo_uri=first_uri
            )
            await session.commit()
    RESOLVER.invalidate(tenant_id=tenant_id)
    await run_trust(tenant_id, first, FixedDetector([]))

    with tenant_scope(tenant_id):
        async with sessions() as session:
            second = await make_complaint(
                session,
                tenant_id=tenant_id,
                reported_at=BASE.replace(hour=14),
                photo_uri=second_uri,
            )
            await session.commit()
    await run_trust(tenant_id, second, FixedDetector([]))

    with tenant_scope(tenant_id):
        async with sessions() as session:
            events = await EventStore(session).read_stream(
                entity_type="complaint", entity_id=second
            )
            item = (
                await session.execute(
                    select(ReviewQueueItem).where(
                        ReviewQueueItem.tenant_id == tenant_id,
                        ReviewQueueItem.complaint_id == second,
                        ReviewQueueItem.reason == "perceptual_duplicate",
                    )
                )
            ).scalar_one()
            complaint = (
                await session.execute(
                    select(Complaint.is_fraud_flagged, Complaint.status).where(
                        Complaint.tenant_id == tenant_id, Complaint.id == second
                    )
                )
            ).one()

    duplicate = next(
        event for event in events if event.event_type == "perceptual_duplicate_detected"
    )
    assert uuid.UUID(duplicate.payload["matched_complaint_id"]) == first
    assert duplicate.payload["hamming_distance"] <= duplicate.payload["threshold"]
    assert item.evidence["matches"][0]["complaint_id"] == str(first)
    # Flagged for a human, and still in the pipeline. §22.2 forbids a system
    # flag being presented as settled fact.
    assert complaint == (True, "submitted")


async def test_several_non_queueing_signals_still_reach_a_human(
    bound_session: None,
    sessions: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
    uploads: Path,
) -> None:
    """§11.4's converse: signals too mild to queue individually must not add up
    to nothing.

    The tenant here has deliberately switched *off* queueing for the velocity
    detector — a reasonable thing to do when it is noisy — and absent EXIF never
    queues on its own. Both still move trust, and the total crosses the floor.
    Without the backstop the trust score would be a number that is computed and
    never used, which is how a fraud signal becomes decoration.
    """
    uri = quarantine(uploads, noisy_patch_image())

    with tenant_scope(tenant_id):
        async with sessions() as session:
            await policy_service.seed_baselines(session, tenant_id=tenant_id)
            version = await policy_service.draft(
                session,
                tenant_id=tenant_id,
                kind=PolicyKind.TRUST_THRESHOLDS,
                body={
                    "exif": {"absent_trust_delta": -0.3},
                    "velocity": {"trust_delta": -0.3, "queues_review": False},
                },
                change_reason="Velocity is noisy here; keep the signal, drop the queueing",
            )
            for step, reason in (
                (policy_service.submit_for_review, "review"),
                (policy_service.approve, "approved"),
                (policy_service.activate, "live"),
            ):
                await step(
                    session,
                    tenant_id=tenant_id,
                    kind=PolicyKind.TRUST_THRESHOLDS,
                    revision=version.revision,
                    reason=reason,
                )
            for index in range(15):
                await make_complaint(
                    session,
                    tenant_id=tenant_id,
                    reported_at=BASE.replace(minute=index),
                    device_fingerprint="flooder",
                )
            complaint_id = await make_complaint(
                session,
                tenant_id=tenant_id,
                reported_at=BASE.replace(minute=40),
                photo_uri=uri,
                device_fingerprint="flooder",
            )
            await session.commit()
    RESOLVER.invalidate(tenant_id=tenant_id)

    await run_trust(tenant_id, complaint_id, FixedDetector([]))

    with tenant_scope(tenant_id):
        async with sessions() as session:
            items = (
                (
                    await session.execute(
                        select(ReviewQueueItem).where(
                            ReviewQueueItem.tenant_id == tenant_id,
                            ReviewQueueItem.complaint_id == complaint_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            events = [
                event.event_type
                for event in await EventStore(session).read_stream(
                    entity_type="complaint", entity_id=complaint_id
                )
            ]

    # The detector fired and is on the record — switching off *queueing* must
    # not switch off the evidence.
    assert "abuse_pattern_flagged" in events
    # ...but it raised no item of its own, and the backstop is what caught it.
    assert [item.reason for item in items] == ["low_trust"]
    assert items[0].evidence["trust_score"] == pytest.approx(-0.6)
    assert items[0].evidence["floor"] == -0.5


async def test_a_queueing_detector_suppresses_the_backstop_for_its_own_report(
    bound_session: None,
    sessions: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
    uploads: Path,
) -> None:
    """The backstop must not double-queue what a detector already raised.

    A report carrying both a ``device_velocity`` item and a ``low_trust`` item
    is two judgements for one reviewer to make about the same evidence, and the
    second one adds nothing they cannot see in the first.
    """
    uri = quarantine(uploads, noisy_patch_image())

    with tenant_scope(tenant_id):
        async with sessions() as session:
            await policy_service.seed_baselines(session, tenant_id=tenant_id)
            version = await policy_service.draft(
                session,
                tenant_id=tenant_id,
                kind=PolicyKind.TRUST_THRESHOLDS,
                body={"exif": {"absent_trust_delta": -0.3}, "velocity": {"trust_delta": -0.3}},
                change_reason="Tighter deltas",
            )
            for step, reason in (
                (policy_service.submit_for_review, "review"),
                (policy_service.approve, "approved"),
                (policy_service.activate, "live"),
            ):
                await step(
                    session,
                    tenant_id=tenant_id,
                    kind=PolicyKind.TRUST_THRESHOLDS,
                    revision=version.revision,
                    reason=reason,
                )
            for index in range(15):
                await make_complaint(
                    session,
                    tenant_id=tenant_id,
                    reported_at=BASE.replace(minute=index),
                    device_fingerprint="flooder-2",
                )
            complaint_id = await make_complaint(
                session,
                tenant_id=tenant_id,
                reported_at=BASE.replace(minute=40),
                photo_uri=uri,
                device_fingerprint="flooder-2",
            )
            await session.commit()
    RESOLVER.invalidate(tenant_id=tenant_id)

    await run_trust(tenant_id, complaint_id, FixedDetector([]))

    with tenant_scope(tenant_id):
        async with sessions() as session:
            reasons = sorted(
                row.reason
                for row in (
                    (
                        await session.execute(
                            select(ReviewQueueItem).where(
                                ReviewQueueItem.tenant_id == tenant_id,
                                ReviewQueueItem.complaint_id == complaint_id,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            )
    assert reasons == ["device_velocity"]


# ---------------------------------------------------------------------------
# Failing closed
# ---------------------------------------------------------------------------


async def test_no_detector_halts_the_complaint_and_serves_nothing(
    bound_session: None,
    sessions: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
    uploads: Path,
) -> None:
    """§22.1 fails closed, and the gate's second clause is what that buys.

    The stage raises ``StagePermanentError``, the declared fallback is
    ``HALTED_FOR_REVIEW``, and — the part that matters — **no redacted artefact
    and no media row exist**, so there is nothing for a later change to serve
    and no temptation to "just show the original".
    """
    uri = quarantine(uploads, noisy_patch_image())

    with tenant_scope(tenant_id):
        async with sessions() as session:
            await policy_service.seed_baselines(session, tenant_id=tenant_id)
            complaint_id = await make_complaint(
                session, tenant_id=tenant_id, reported_at=BASE, photo_uri=uri
            )
            await session.commit()
    RESOLVER.invalidate(tenant_id=tenant_id)

    with (
        provider_scope(PipelineStage.TRUST_VERIFICATION, trust_stage),
        pytest.raises(StagePermanentError, match="face detector"),
    ):
        await execute_stage(
            tenant_id=tenant_id,
            complaint_id=complaint_id,
            stage=PipelineStage.TRUST_VERIFICATION.value,
        )

    with tenant_scope(tenant_id):
        async with sessions() as session:
            media = (
                (
                    await session.execute(
                        select(SubmissionMedia).where(
                            SubmissionMedia.tenant_id == tenant_id,
                            SubmissionMedia.complaint_id == complaint_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            events = [
                event.event_type
                for event in await EventStore(session).read_stream(
                    entity_type="complaint", entity_id=complaint_id
                )
            ]

    assert media == []
    assert events == ["complaint_submitted"]
    assert not (uploads / "redacted").exists() or not list((uploads / "redacted").rglob("*.jpg"))


async def test_an_undecodable_upload_halts_rather_than_proceeding_unredacted(
    bound_session: None,
    sessions: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
    uploads: Path,
) -> None:
    """Permanent, not retryable: the file will not decode in thirty seconds either.

    And it halts rather than skipping. A complaint that reached classification
    with no redacted artefact would be one whose only image is the original.
    """
    uri = quarantine(uploads, b"\xff\xd8\xff\xe0 truncated garbage")

    with tenant_scope(tenant_id):
        async with sessions() as session:
            await policy_service.seed_baselines(session, tenant_id=tenant_id)
            complaint_id = await make_complaint(
                session, tenant_id=tenant_id, reported_at=BASE, photo_uri=uri
            )
            await session.commit()
    RESOLVER.invalidate(tenant_id=tenant_id)

    with (
        detector_scope(FixedDetector([])),
        provider_scope(PipelineStage.TRUST_VERIFICATION, trust_stage),
        pytest.raises(StagePermanentError),
    ):
        await execute_stage(
            tenant_id=tenant_id,
            complaint_id=complaint_id,
            stage=PipelineStage.TRUST_VERIFICATION.value,
        )


async def test_a_missing_quarantine_file_is_permanent_and_names_retention(
    bound_session: None,
    sessions: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
    uploads: Path,
) -> None:
    """The §22.4 sweep removing a raw upload is expected; the message says so,
    rather than sending an operator to look for a broken volume mount."""
    with tenant_scope(tenant_id):
        async with sessions() as session:
            await policy_service.seed_baselines(session, tenant_id=tenant_id)
            complaint_id = await make_complaint(
                session,
                tenant_id=tenant_id,
                reported_at=BASE,
                photo_uri=f"{MEDIA_SCHEME}://ab/{'a' * 64}.jpg",
            )
            await session.commit()
    RESOLVER.invalidate(tenant_id=tenant_id)

    with (
        detector_scope(FixedDetector([])),
        provider_scope(PipelineStage.TRUST_VERIFICATION, trust_stage),
        pytest.raises(StagePermanentError, match="retention"),
    ):
        await execute_stage(
            tenant_id=tenant_id,
            complaint_id=complaint_id,
            stage=PipelineStage.TRUST_VERIFICATION.value,
        )


# ---------------------------------------------------------------------------
# Atomicity and idempotency
# ---------------------------------------------------------------------------


async def test_a_redelivered_stage_appends_nothing_and_writes_no_second_row(
    bound_session: None,
    sessions: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
    uploads: Path,
) -> None:
    """``task_acks_late`` means the broker will redeliver. Both halves matter:
    the log takes one set of events, and ``submission_media`` takes one row."""
    uri = quarantine(uploads, image_with_exif(latitude=PUNE[0], longitude=PUNE[1]))

    with tenant_scope(tenant_id):
        async with sessions() as session:
            await policy_service.seed_baselines(session, tenant_id=tenant_id)
            complaint_id = await make_complaint(
                session,
                tenant_id=tenant_id,
                reported_at=BASE,
                latitude=PUNE[0],
                longitude=PUNE[1],
                photo_uri=uri,
            )
            await session.commit()
    RESOLVER.invalidate(tenant_id=tenant_id)

    first = await run_trust(tenant_id, complaint_id, FixedDetector([FACE]))
    second = await run_trust(tenant_id, complaint_id, FixedDetector([FACE]))

    assert first.ran and first.events_appended == 2  # type: ignore[attr-defined]
    assert second.already_ran and second.events_appended == 0  # type: ignore[attr-defined]

    with tenant_scope(tenant_id):
        async with sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(SubmissionMedia).where(
                            SubmissionMedia.tenant_id == tenant_id,
                            SubmissionMedia.complaint_id == complaint_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            events = [
                event.event_type
                for event in await EventStore(session).read_stream(
                    entity_type="complaint", entity_id=complaint_id
                )
            ]

    assert len(rows) == 1
    assert events == ["complaint_submitted", "exif_check_completed", "media_redacted"]


async def test_a_report_with_no_photograph_still_runs_the_abuse_checks(
    bound_session: None,
    sessions: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
    uploads: Path,
) -> None:
    """§26.1 allows an audio-only submission. §11.3 does not depend on an image.

    A stage that returned early with no photo would silently disable
    coordinated-abuse detection for every voice report — which is exactly the
    submission path §8.4 says the least-served citizens use.
    """
    with tenant_scope(tenant_id):
        async with sessions() as session:
            await policy_service.seed_baselines(session, tenant_id=tenant_id)
            for index in range(15):
                await make_complaint(
                    session,
                    tenant_id=tenant_id,
                    reported_at=BASE.replace(minute=index),
                    device_fingerprint="voice-flooder",
                )
            complaint_id = await make_complaint(
                session,
                tenant_id=tenant_id,
                reported_at=BASE.replace(minute=40),
                device_fingerprint="voice-flooder",
                photo_uri=None,
            )
            await session.commit()
    RESOLVER.invalidate(tenant_id=tenant_id)

    await run_trust(tenant_id, complaint_id, FixedDetector([]))

    with tenant_scope(tenant_id):
        async with sessions() as session:
            events = [
                event.event_type
                for event in await EventStore(session).read_stream(
                    entity_type="complaint", entity_id=complaint_id
                )
            ]
    assert "abuse_pattern_flagged" in events
    assert "media_redacted" not in events


async def test_the_stage_reads_the_tenants_own_thresholds(
    bound_session: None,
    sessions: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
    uploads: Path,
) -> None:
    """Architectural principle 1, at the stage that consumes it.

    The same photograph, the same claimed location, two trust policies, two
    outcomes — and the change is an approved document, not a deploy.
    """
    # ~33 km north of the reported location: far outside the 200 m default and
    # comfortably inside the 100 km ceiling ``ExifPolicy`` puts on the knob.
    photo = image_with_exif(latitude=PUNE[0] + 0.3, longitude=PUNE[1])
    uri = quarantine(uploads, photo)

    with tenant_scope(tenant_id):
        async with sessions() as session:
            await policy_service.seed_baselines(session, tenant_id=tenant_id)
            body = {
                "exif": {
                    "mismatch_distance_meters": 100_000.0,
                    "mismatch_queues_review": True,
                }
            }
            version = await policy_service.draft(
                session,
                tenant_id=tenant_id,
                kind=PolicyKind.TRUST_THRESHOLDS,
                body=body,
                change_reason="Regional deployment; photographs are taken far from the report",
            )
            for step, reason in (
                (policy_service.submit_for_review, "review"),
                (policy_service.approve, "approved"),
                (policy_service.activate, "live"),
            ):
                await step(
                    session,
                    tenant_id=tenant_id,
                    kind=PolicyKind.TRUST_THRESHOLDS,
                    revision=version.revision,
                    reason=reason,
                )
            complaint_id = await make_complaint(
                session,
                tenant_id=tenant_id,
                reported_at=BASE,
                latitude=PUNE[0],
                longitude=PUNE[1],
                photo_uri=uri,
            )
            await session.commit()
    RESOLVER.invalidate(tenant_id=tenant_id)

    await run_trust(tenant_id, complaint_id, FixedDetector([]))

    with tenant_scope(tenant_id):
        async with sessions() as session:
            items = (
                (
                    await session.execute(
                        select(ReviewQueueItem).where(
                            ReviewQueueItem.tenant_id == tenant_id,
                            ReviewQueueItem.complaint_id == complaint_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            events = await EventStore(session).read_stream(
                entity_type="complaint", entity_id=complaint_id
            )

    exif_event = next(event for event in events if event.event_type == "exif_check_completed")
    # 33 km, inside a 100 km radius: confirmed rather than contradicted. The
    # same photograph under the seeded baseline would have been a mismatch.
    assert exif_event.payload["trust_delta"] > 0
    assert exif_event.payload["distance_meters"] > 30_000
    assert [item.reason for item in items] == []
