"""The §11.4 queue over HTTP, and the one route in the system that serves an image.

Two things are being checked here that no lower-level test can reach. The first
is the tenant boundary on **content-addressed** media: the redacted root is one
directory shared by every tenant, because identical bytes deduplicate by design,
so the boundary has to be the database row rather than the path. The second is
that the media route cannot be made to serve an original — not by a traversal,
not by handing it a quarantine hash, not by guessing.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine

from nemesis.api.v1.control_plane import CONTROL_PLANE_TOKEN_HEADER
from nemesis.config import Settings
from nemesis.db.models.trust import SubmissionMedia
from nemesis.tenancy.context import tenant_scope
from nemesis.trust import stores
from nemesis.trust.detectors import FaceBox
from nemesis.trust.redaction import RedactedStore, redact_image
from nemesis.trust.review import ReviewReason, queue
from tests.conftest import postgres_required
from tests.test_trust_review import make_complaint
from tests.trust_fixtures import FixedDetector, noisy_patch_image

pytestmark = [postgres_required, pytest.mark.integration]

REVIEW = "/api/v1/review"
DEV_TOKEN = "dev-only-insecure-control-plane-token-change-me"
FACE = FaceBox(x=4, y=4, width=24, height=16, confidence=0.9)


def headers(tenant_id: uuid.UUID, *, token: bool = False) -> dict[str, str]:
    result = {"X-Tenant-ID": str(tenant_id)}
    if token:
        result[CONTROL_PLANE_TOKEN_HEADER] = DEV_TOKEN
    return result


@pytest.fixture
def upload_root(app_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point the store accessors at the app's own upload directory.

    ``app_settings`` already redirects ``upload_dir`` to a scratch path for the
    handler; the module-level accessors resolve settings independently, so both
    have to be told or the fixture that writes the artefact and the handler that
    serves it end up looking at different directories.
    """
    monkeypatch.setattr("nemesis.trust.stores.get_settings", lambda: app_settings)
    stores.reset()
    yield app_settings.upload_dir
    stores.reset()


async def seed_item(
    app_settings: Settings,
    *,
    tenant_id: uuid.UUID,
    reason: ReviewReason = ReviewReason.EXIF_MISMATCH,
    with_media: bool = False,
) -> tuple[uuid.UUID, uuid.UUID, str | None]:
    """One complaint, one queue item, optionally one redacted artefact.

    Returns ``(complaint_id, review_item_id, redacted_sha256)``. Uses the app's
    engine so the rows are visible to the handler under test.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    engine = create_async_engine(app_settings.database_url, pool_pre_ping=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    digest: str | None = None
    try:
        with tenant_scope(tenant_id):
            async with maker() as session:
                complaint_id = await make_complaint(session, tenant_id=tenant_id)
                queued = await queue(
                    session,
                    tenant_id=tenant_id,
                    complaint_id=complaint_id,
                    reason=reason,
                    evidence={"distance_meters": 3100.0},
                    trust_score=-0.4,
                )
                if with_media:
                    result = redact_image(
                        noisy_patch_image(),
                        store=RedactedStore(app_settings.upload_dir),
                        detector=FixedDetector([FACE]),
                    )
                    digest = result.sha256
                    session.add(
                        SubmissionMedia(
                            tenant_id=tenant_id,
                            complaint_id=complaint_id,
                            kind="image",
                            content_type=result.content_type,
                            quarantine_uri="nemesis+quarantine://ab/" + "a" * 64 + ".jpg",
                            quarantine_sha256="a" * 64,
                            redacted_uri=result.uri,
                            redacted_sha256=result.sha256,
                            detector_id=result.detector_id,
                            faces_detected=result.faces_detected,
                            faces_blurred=result.faces_blurred,
                            captured_or_reported_at=(await make_time()),
                            purge_raw_after=await make_time(days=30),
                            purge_exif_after=await make_time(days=90),
                        )
                    )
                await session.commit()
    finally:
        await engine.dispose()
    return complaint_id, queued.review_item_id, digest


async def make_time(days: int = 0):  # type: ignore[no-untyped-def]
    from datetime import UTC, datetime, timedelta

    return datetime(2026, 3, 1, 12, 0, tzinfo=UTC) + timedelta(days=days)


# ---------------------------------------------------------------------------
# The queue
# ---------------------------------------------------------------------------


async def test_the_queue_lists_open_items_with_their_evidence(
    api_client: AsyncClient, app_settings: Settings, tenant_id: uuid.UUID
) -> None:
    """§11.4's filtered table, over HTTP."""
    _, item_id, _ = await seed_item(app_settings, tenant_id=tenant_id)

    response = await api_client.get(f"{REVIEW}/queue", headers=headers(tenant_id))
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["id"] == str(item_id)
    assert item["reason"] == "exif_mismatch"
    assert item["status"] == "open"
    assert item["evidence"]["distance_meters"] == 3100.0


async def test_the_queue_never_shows_another_tenants_items(
    api_client: AsyncClient,
    app_settings: Settings,
    tenant_id: uuid.UUID,
    other_tenant_id: uuid.UUID,
) -> None:
    _, item_id, _ = await seed_item(app_settings, tenant_id=other_tenant_id)

    listing = await api_client.get(f"{REVIEW}/queue", headers=headers(tenant_id))
    single = await api_client.get(f"{REVIEW}/queue/{item_id}", headers=headers(tenant_id))

    assert listing.json()["total"] == 0
    # 404, not 403: a distinguishable "exists but forbidden" enumerates another
    # customer's flagged complaints one request at a time.
    assert single.status_code == 404


async def test_an_unknown_status_filter_is_refused_with_a_usable_message(
    api_client: AsyncClient, tenant_id: uuid.UUID
) -> None:
    response = await api_client.get(
        f"{REVIEW}/queue", headers=headers(tenant_id), params={"status": "in_progress"}
    )
    assert response.status_code == 422
    assert "Phase 13" in response.json()["detail"]


async def test_a_decision_requires_the_control_plane_token(
    api_client: AsyncClient, app_settings: Settings, tenant_id: uuid.UUID
) -> None:
    """Reads are token-free; recording a judgement is not.

    A decision changes what the system believes about a citizen's report and
    becomes a Phase 11 training label, which is at least as consequential as
    changing a policy.
    """
    _, item_id, _ = await seed_item(app_settings, tenant_id=tenant_id)
    response = await api_client.post(
        f"{REVIEW}/queue/{item_id}/decision",
        headers=headers(tenant_id),
        json={"decision": "approve", "rationale": "Confirmed."},
    )
    # 403, matching every other control-plane write: the caller is identified as
    # a tenant, they are simply not permitted this operation without the shared
    # secret. A 401 would imply an authentication scheme this phase does not
    # have — Phase 13 owns that.
    assert response.status_code == 403


async def test_a_decision_is_recorded_and_the_item_closes(
    api_client: AsyncClient, app_settings: Settings, tenant_id: uuid.UUID
) -> None:
    complaint_id, item_id, _ = await seed_item(app_settings, tenant_id=tenant_id)

    created = await api_client.post(
        f"{REVIEW}/queue/{item_id}/decision",
        headers=headers(tenant_id, token=True),
        json={
            "decision": "reject",
            "rationale": "The photograph is of a different street entirely.",
            "decided_by_label": "ops-oncall",
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["decision"] == "reject"
    assert body["complaint_id"] == str(complaint_id)
    assert len(body["evidence_hash"]) == 64

    listing = await api_client.get(f"{REVIEW}/queue", headers=headers(tenant_id))
    assert listing.json()["total"] == 0

    decided = await api_client.get(
        f"{REVIEW}/queue", headers=headers(tenant_id), params={"status": "decided"}
    )
    assert decided.json()["total"] == 1


async def test_a_second_decision_is_a_conflict(
    api_client: AsyncClient, app_settings: Settings, tenant_id: uuid.UUID
) -> None:
    _, item_id, _ = await seed_item(app_settings, tenant_id=tenant_id)
    payload = {"decision": "approve", "rationale": "Confirmed."}

    first = await api_client.post(
        f"{REVIEW}/queue/{item_id}/decision", headers=headers(tenant_id, token=True), json=payload
    )
    second = await api_client.post(
        f"{REVIEW}/queue/{item_id}/decision", headers=headers(tenant_id, token=True), json=payload
    )
    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["type"].endswith("review-reviewconflict")


async def test_a_decision_with_a_blank_rationale_is_refused_at_the_boundary(
    api_client: AsyncClient, app_settings: Settings, tenant_id: uuid.UUID
) -> None:
    _, item_id, _ = await seed_item(app_settings, tenant_id=tenant_id)
    response = await api_client.post(
        f"{REVIEW}/queue/{item_id}/decision",
        headers=headers(tenant_id, token=True),
        json={"decision": "approve", "rationale": ""},
    )
    assert response.status_code == 422


async def test_an_unknown_decision_is_refused(
    api_client: AsyncClient, app_settings: Settings, tenant_id: uuid.UUID
) -> None:
    """§11.4 has three actions. "delete" is not one of them, and the boundary
    says so rather than the database."""
    _, item_id, _ = await seed_item(app_settings, tenant_id=tenant_id)
    response = await api_client.post(
        f"{REVIEW}/queue/{item_id}/decision",
        headers=headers(tenant_id, token=True),
        json={"decision": "delete", "rationale": "no"},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Media
# ---------------------------------------------------------------------------


async def test_a_redacted_artefact_is_served_with_its_content_address(
    api_client: AsyncClient,
    app_settings: Settings,
    tenant_id: uuid.UUID,
    upload_root: Path,
) -> None:
    """§11.4 requires the reviewer to see the photograph; this is the only route
    that shows one, and it shows the blurred copy."""
    _, item_id, digest = await seed_item(app_settings, tenant_id=tenant_id, with_media=True)
    assert digest is not None

    item = await api_client.get(f"{REVIEW}/queue/{item_id}", headers=headers(tenant_id))
    assert item.json()["redacted_media"] == [digest]

    media = await api_client.get(f"{REVIEW}/media/{digest}", headers=headers(tenant_id))
    assert media.status_code == 200
    assert media.headers["content-type"] == "image/jpeg"
    # The one route that returns attacker-influenced bytes. Without `nosniff` a
    # crafted upload that sniffs as an image and parses as HTML executes in the
    # reviewer's session.
    assert media.headers["x-content-type-options"] == "nosniff"
    assert media.content[:2] == b"\xff\xd8"


async def test_the_item_response_never_carries_the_source_address(
    api_client: AsyncClient,
    app_settings: Settings,
    tenant_id: uuid.UUID,
    upload_root: Path,
) -> None:
    """The *redacted* hash, never the source.

    A source content address in a JSON response is a working handle to an
    unblurred image sitting one guessed route away from being served.
    """
    _, item_id, digest = await seed_item(app_settings, tenant_id=tenant_id, with_media=True)
    body = await api_client.get(f"{REVIEW}/queue/{item_id}", headers=headers(tenant_id))
    assert "a" * 64 not in body.text
    assert digest in body.text


async def test_one_tenant_cannot_fetch_anothers_artefact_by_its_hash(
    api_client: AsyncClient,
    app_settings: Settings,
    tenant_id: uuid.UUID,
    other_tenant_id: uuid.UUID,
    upload_root: Path,
) -> None:
    """The tenant boundary on a content-addressed store.

    The redacted root is shared — identical bytes are one file, by design — so
    resolving the path from the URL alone would let any tenant fetch any other's
    photograph by observing or guessing a hash. The row is what says the
    artefact belongs here.
    """
    _, _, digest = await seed_item(app_settings, tenant_id=other_tenant_id, with_media=True)
    assert digest is not None

    theirs = await api_client.get(f"{REVIEW}/media/{digest}", headers=headers(other_tenant_id))
    mine = await api_client.get(f"{REVIEW}/media/{digest}", headers=headers(tenant_id))

    assert theirs.status_code == 200
    assert mine.status_code == 404


async def test_the_media_route_refuses_anything_that_is_not_a_content_address(
    api_client: AsyncClient, tenant_id: uuid.UUID, upload_root: Path
) -> None:
    """Rejected before any I/O. ``RedactedStore.resolve`` already refuses
    traversal; a check that happens first is one an auditor can see."""
    for candidate in ("../../etc/passwd", "not-a-hash", "a" * 63, "g" * 64):
        response = await api_client.get(f"{REVIEW}/media/{candidate}", headers=headers(tenant_id))
        assert response.status_code == 404, candidate


async def test_an_unknown_hash_is_not_found(
    api_client: AsyncClient, tenant_id: uuid.UUID, upload_root: Path
) -> None:
    response = await api_client.get(f"{REVIEW}/media/{'b' * 64}", headers=headers(tenant_id))
    assert response.status_code == 404


async def test_there_is_no_route_that_serves_quarantine(
    api_client: AsyncClient, tenant_id: uuid.UUID
) -> None:
    """The §22.1 guarantee, asserted from outside the process.

    ``check_media_redaction.py`` proves it about the code's shape and
    ``test_trust_redaction`` proves it about the pixels. This proves the thing a
    reviewer of the deployment would actually try: there is no HTTP path under
    the media prefix that takes a quarantine reference.
    """
    spec = (await api_client.get("/openapi.json")).json()
    media_paths = [path for path in spec["paths"] if path.startswith("/api/v1/review/media")]
    assert media_paths == ["/api/v1/review/media/{redacted_sha256}"]
    # A bare content address, not a URI — so there is no parameter into which a
    # quarantine reference could be written.
    assert "quarantine" not in str(spec["paths"][media_paths[0]])

    response = await api_client.get(
        f"{REVIEW}/media/nemesis+quarantine://ab/{'a' * 64}.jpg", headers=headers(tenant_id)
    )
    assert response.status_code == 404
