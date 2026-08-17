"""§26.1 submission and §26.2 retrieval, over HTTP.

``dispatch_pipeline`` is replaced with a recorder in every test here. That is a
*broker* boundary, not a datastore: the contract being asserted is "a committed
submission is handed to the pipeline with these arguments", and letting the real
call enqueue onto the shared Redis broker would have worker-io pick the task up
and run it against the application database, where the complaint does not exist.
The pipeline's own behaviour is tested against a real database in
``test_pipeline_orchestration.py``.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from nemesis.api.deps import TENANT_HEADER
from nemesis.domain.constants import SYSTEM_TENANT_ID
from tests.conftest import postgres_required

pytestmark = [postgres_required, pytest.mark.integration]

#: Four bytes of JFIF magic and some filler. The sniffer reads magic bytes and
#: nothing else — decoding is Phase 8's, and a real photo here would test
#: Pillow rather than the upload path.
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 512
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 512
OGG = b"OggS" + b"\x00" * 512
NOT_MEDIA = b"%PDF-1.7\n" + b"\x00" * 512


@pytest.fixture
def dispatched(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _record(**kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr("nemesis.api.v1.complaints.dispatch_pipeline", _record)
    return calls


def _form(**overrides: Any) -> dict[str, Any]:
    data = {
        "latitude": "18.5204",
        "longitude": "73.8567",
        # Unique per call: the rate limiter's bucket key includes it, and Redis
        # outlives the test database, so a shared fingerprint would make tests
        # limit each other in whatever order they happened to run.
        "device_fingerprint": uuid.uuid4().hex,
    }
    data.update(overrides)
    return data


async def _submit(
    client: AsyncClient, tenant_id: uuid.UUID, *, files: Any = None, **form: Any
) -> Any:
    return await client.post(
        "/api/v1/complaints",
        data=_form(**form),
        files=files or {"photo": ("p.jpg", JPEG, "image/jpeg")},
        headers={TENANT_HEADER: str(tenant_id)},
    )


# ---------------------------------------------------------------------------
# Submission
# ---------------------------------------------------------------------------


async def test_a_valid_submission_is_accepted_and_dispatched(
    api_client: AsyncClient, tenant_id: uuid.UUID, dispatched: list[dict[str, Any]]
) -> None:
    response = await _submit(api_client, tenant_id, description_text="lift stuck")

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "submitted"
    assert body["estimated_processing_time_seconds"] > 0
    complaint_id = uuid.UUID(body["complaint_id"])
    assert response.headers["Location"] == f"/api/v1/complaints/{complaint_id}"

    # Dispatched exactly once, after the commit, with the tenant it belongs to.
    assert len(dispatched) == 1
    assert dispatched[0]["complaint_id"] == complaint_id
    assert dispatched[0]["tenant_id"] == tenant_id


async def test_an_audio_only_submission_is_accepted(
    api_client: AsyncClient, tenant_id: uuid.UUID, dispatched: list[dict[str, Any]]
) -> None:
    """§26.1: photo required *if audio absent*, and vice versa."""
    response = await _submit(api_client, tenant_id, files={"audio": ("v.ogg", OGG, "audio/ogg")})
    assert response.status_code == 202


async def test_a_submission_with_no_media_is_refused(
    api_client: AsyncClient, tenant_id: uuid.UUID, dispatched: list[dict[str, Any]]
) -> None:
    response = await api_client.post(
        "/api/v1/complaints",
        data=_form(),
        headers={TENANT_HEADER: str(tenant_id)},
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    assert dispatched == []


async def test_the_declared_content_type_is_ignored_in_favour_of_magic_bytes(
    api_client: AsyncClient, tenant_id: uuid.UUID, dispatched: list[dict[str, Any]]
) -> None:
    """§25.1: a content type supplied by the uploader is a claim, not a check."""
    response = await _submit(
        api_client,
        tenant_id,
        # Says JPEG. Is a PDF.
        files={"photo": ("evil.jpg", NOT_MEDIA, "image/jpeg")},
    )
    assert response.status_code == 415
    assert dispatched == []


async def test_audio_bytes_in_the_photo_field_are_refused(
    api_client: AsyncClient, tenant_id: uuid.UUID, dispatched: list[dict[str, Any]]
) -> None:
    """The allow-list is per field, not global.

    A real audio file in the photo field would sniff to a *supported* type and
    pass a single global allow-list, then reach a classifier expecting an image.
    """
    response = await _submit(api_client, tenant_id, files={"photo": ("a.jpg", OGG, "audio/ogg")})
    assert response.status_code == 415


async def test_an_oversized_upload_is_refused(
    api_client: AsyncClient,
    tenant_id: uuid.UUID,
    dispatched: list[dict[str, Any]],
    app_settings: Any,
) -> None:
    oversized = JPEG + b"\x00" * (app_settings.ingest.max_upload_bytes + 1)
    response = await _submit(
        api_client, tenant_id, files={"photo": ("big.jpg", oversized, "image/jpeg")}
    )
    assert response.status_code == 413
    assert dispatched == []


async def test_an_overlong_description_is_refused(
    api_client: AsyncClient,
    tenant_id: uuid.UUID,
    dispatched: list[dict[str, Any]],
    app_settings: Any,
) -> None:
    response = await _submit(
        api_client,
        tenant_id,
        description_text="x" * (app_settings.ingest.max_description_chars + 1),
    )
    assert response.status_code == 422


async def test_an_idempotency_key_returns_the_original_complaint(
    api_client: AsyncClient, tenant_id: uuid.UUID, dispatched: list[dict[str, Any]]
) -> None:
    """A retry after a timeout must not create a second report.

    Note what is *not* claimed: two citizens photographing the same pothole
    within a second are two genuine reports, and §14's dedup is what relates
    them. Only an explicit key makes a resubmission a replay.
    """
    form = _form()
    headers = {TENANT_HEADER: str(tenant_id), "Idempotency-Key": "client-retry-1"}

    first = await api_client.post(
        "/api/v1/complaints",
        data=form,
        files={"photo": ("p.jpg", JPEG, "image/jpeg")},
        headers=headers,
    )
    second = await api_client.post(
        "/api/v1/complaints",
        data=form,
        files={"photo": ("p.jpg", JPEG, "image/jpeg")},
        headers=headers,
    )

    assert first.status_code == second.status_code == 202
    assert first.json()["complaint_id"] == second.json()["complaint_id"]
    assert second.headers.get("Idempotent-Replay") == "true"
    # And the pipeline is not started twice for one complaint.
    assert len(dispatched) == 1


# ---------------------------------------------------------------------------
# Tenant resolution
# ---------------------------------------------------------------------------


async def test_a_missing_tenant_header_is_refused(api_client: AsyncClient) -> None:
    response = await api_client.post(
        "/api/v1/complaints",
        data=_form(),
        files={"photo": ("p.jpg", JPEG, "image/jpeg")},
    )
    assert response.status_code == 400


@pytest.mark.parametrize("value", ["not-a-uuid", str(uuid.uuid4()), str(SYSTEM_TENANT_ID)])
async def test_an_invalid_unknown_or_reserved_tenant_is_refused(
    api_client: AsyncClient, value: str
) -> None:
    """Unknown and reserved both answer 404, and that is deliberate.

    A distinguishable "exists but forbidden" would turn the header into an
    enumeration oracle for the customer list.
    """
    response = await api_client.post(
        "/api/v1/complaints",
        data=_form(),
        files={"photo": ("p.jpg", JPEG, "image/jpeg")},
        headers={TENANT_HEADER: value},
    )
    assert response.status_code in {400, 404}


# ---------------------------------------------------------------------------
# Retrieval (§26.2)
# ---------------------------------------------------------------------------


async def test_retrieval_returns_the_projection_with_an_etag(
    api_client: AsyncClient, tenant_id: uuid.UUID, dispatched: list[dict[str, Any]]
) -> None:
    created = await _submit(api_client, tenant_id, description_text="lift stuck")
    complaint_id = created.json()["complaint_id"]

    response = await api_client.get(
        f"/api/v1/complaints/{complaint_id}", headers={TENANT_HEADER: str(tenant_id)}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["complaint_id"] == complaint_id
    assert body["status"] == "submitted"
    assert body["version"] >= 1
    assert "ETag" in response.headers


async def test_a_matching_etag_returns_304(
    api_client: AsyncClient, tenant_id: uuid.UUID, dispatched: list[dict[str, Any]]
) -> None:
    """§27.3's polling fallback is 5 seconds per client; this is what makes it cheap."""
    created = await _submit(api_client, tenant_id)
    complaint_id = created.json()["complaint_id"]
    headers = {TENANT_HEADER: str(tenant_id)}

    first = await api_client.get(f"/api/v1/complaints/{complaint_id}", headers=headers)
    etag = first.headers["ETag"]

    second = await api_client.get(
        f"/api/v1/complaints/{complaint_id}", headers={**headers, "If-None-Match": etag}
    )
    assert second.status_code == 304
    assert second.content == b""
    # RFC 9110: a 304 repeats the validator, or the client's next request has
    # nothing to condition on.
    assert second.headers["ETag"] == etag


async def test_the_response_carries_no_citizen_data(
    api_client: AsyncClient, tenant_id: uuid.UUID, dispatched: list[dict[str, Any]]
) -> None:
    """Until Phase 13 can say who is asking, the answer is nobody."""
    created = await _submit(api_client, tenant_id, description_text="outside my house")
    complaint_id = created.json()["complaint_id"]

    body = (
        await api_client.get(
            f"/api/v1/complaints/{complaint_id}", headers={TENANT_HEADER: str(tenant_id)}
        )
    ).json()

    for forbidden in (
        "description_text",
        "photo_url",
        "audio_url",
        "device_fingerprint",
        "submitter_device_fingerprint",
    ):
        assert forbidden not in body


async def test_one_tenant_cannot_read_anothers_complaint(
    api_client: AsyncClient,
    tenant_id: uuid.UUID,
    other_tenant_id: uuid.UUID,
    dispatched: list[dict[str, Any]],
) -> None:
    created = await _submit(api_client, tenant_id)
    complaint_id = created.json()["complaint_id"]

    response = await api_client.get(
        f"/api/v1/complaints/{complaint_id}", headers={TENANT_HEADER: str(other_tenant_id)}
    )
    # 404, not 403. The other tenant must not learn the id exists.
    assert response.status_code == 404


async def test_an_unknown_complaint_is_a_problem_document(
    api_client: AsyncClient, tenant_id: uuid.UUID
) -> None:
    response = await api_client.get(
        f"/api/v1/complaints/{uuid.uuid4()}", headers={TENANT_HEADER: str(tenant_id)}
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["type"].endswith("/not-found")


async def test_retrieval_reads_the_projection_and_never_replays(
    api_client: AsyncClient, tenant_id: uuid.UUID, dispatched: list[dict[str, Any]]
) -> None:
    """§27.3 makes this endpoint a 5-second poll per client.

    An earlier version read the row for its version and then replayed the whole
    chain to fill the body — the exact cost the projection layer exists to avoid,
    paid on the hottest read in the system, while the docstring claimed
    otherwise. Asserted by breaking replay outright: if the handler still
    reaches for it, this fails.
    """
    created = await _submit(api_client, tenant_id, description_text="lift stuck")
    complaint_id = created.json()["complaint_id"]

    import nemesis.projections.replay as replay_module

    def _forbidden(*_: Any, **__: Any) -> None:
        raise AssertionError("the read path replayed the event log")

    original = replay_module.replay_entity
    replay_module.replay_entity = _forbidden  # type: ignore[assignment]
    try:
        response = await api_client.get(
            f"/api/v1/complaints/{complaint_id}", headers={TENANT_HEADER: str(tenant_id)}
        )
    finally:
        replay_module.replay_entity = original  # type: ignore[assignment]

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "submitted"
    # Coordinates come back out of the geography column via ST_Y/ST_X rather
    # than from a parallel pair of float columns, so there is one authoritative
    # position per complaint.
    assert body["latitude"] == pytest.approx(18.5204, abs=1e-6)
    assert body["longitude"] == pytest.approx(73.8567, abs=1e-6)
    assert body["reported_at"] is not None


async def test_a_degraded_complaint_reports_why_from_its_own_columns(
    api_client: AsyncClient, tenant_id: uuid.UUID, dispatched: list[dict[str, Any]]
) -> None:
    """§24.2 has to be answerable without a replay.

    A halted complaint keeps a truthful status — it genuinely has not been
    classified — so the status alone cannot distinguish "still processing" from
    "stopped, waiting for a human". These two fields are what can.
    """
    from nemesis.pipeline.orchestrator import record_degradation

    created = await _submit(api_client, tenant_id)
    complaint_id = uuid.UUID(created.json()["complaint_id"])

    await record_degradation(
        tenant_id=tenant_id,
        complaint_id=complaint_id,
        stage="safety_check",
        failure_mode="provider_unavailable",
        attempts=1,
    )

    body = (
        await api_client.get(
            f"/api/v1/complaints/{complaint_id}", headers={TENANT_HEADER: str(tenant_id)}
        )
    ).json()

    assert body["degraded_stage"] == "safety_check"
    assert body["degraded_fallback"] == "halted_for_review"
    # Truthful, not invented: the complaint really has not been classified.
    assert body["status"] == "submitted"


async def test_the_work_order_id_promised_by_section_26_2_is_resolved(
    api_client: AsyncClient, tenant_id: uuid.UUID, dispatched: list[dict[str, Any]]
) -> None:
    """A complaint reaches its work order through its cluster, so it is a join.

    Without it the field is permanently ``null``, which is worse than omitting
    it: a client cannot tell "no work order yet" from "this API never fills
    that in".
    """
    from nemesis.db.session import session_scope
    from nemesis.events.store import EventStore
    from nemesis.pipeline.orchestrator import _materialise_and_enqueue
    from nemesis.tenancy.context import tenant_scope

    created = await _submit(api_client, tenant_id)
    complaint_id = uuid.UUID(created.json()["complaint_id"])
    cluster_id, work_order_id = uuid.uuid4(), uuid.uuid4()

    with tenant_scope(tenant_id):
        async with session_scope() as session:
            store = EventStore(session)
            events = [
                await store.append(
                    entity_id=cluster_id,
                    event_type="cluster_created",
                    payload={
                        "seed_complaint_id": str(complaint_id),
                        "latitude": 18.5204,
                        "longitude": 73.8567,
                    },
                    tenant_id=tenant_id,
                ),
                await store.append(
                    entity_id=work_order_id,
                    event_type="work_order_created",
                    payload={"cluster_id": str(cluster_id)},
                    tenant_id=tenant_id,
                ),
            ]
            await _materialise_and_enqueue(session, tenant_id=tenant_id, appended=events)
            # The complaint's own cluster_id is projected from its chain, which
            # Phase 10 will write. Set here through the same column the
            # projector uses so the join has something to resolve.
            await session.execute(
                text(
                    "UPDATE complaints SET cluster_id = :cluster WHERE id = :id "
                    "AND tenant_id = :tenant"
                ).bindparams(cluster=cluster_id, id=complaint_id, tenant=tenant_id)
            )

    body = (
        await api_client.get(
            f"/api/v1/complaints/{complaint_id}", headers={TENANT_HEADER: str(tenant_id)}
        )
    ).json()
    assert body["cluster_id"] == str(cluster_id)
    assert body["work_order_id"] == str(work_order_id)
