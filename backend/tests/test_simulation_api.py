"""The Phase 7 HTTP surface — the control-plane conventions, applied again.

What is actually being checked here is not "does the endpoint work" but that it
follows the same rules as everything else under ``/api/v1/control-plane``, since
the whole argument for a separate router is that it keeps those rules rather
than reinventing them:

- reads are tenant-scoped and token-free; writes carry the control-plane token
- another tenant's run is a 404, never a 403
- a refusal is an RFC 9457 problem document with a type a client can branch on
- the routes that change what production does are not reachable by varying a
  path segment on a route that does not

Plus the two Phase-7-specific ones: a backtest over an empty window is a 422
that names the floor, and an uncertified activation is a 409 that names the
evaluation set.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from nemesis.policy import service as policy_service
from nemesis.policy.documents import PolicyKind
from nemesis.tenancy.context import tenant_scope
from tests.conftest import postgres_required
from tests.test_simulation_corpus import BASE, seed_complaint
from tests.test_simulation_evaluation import rubric

pytestmark = [postgres_required, pytest.mark.integration]

BASE_URL = "/api/v1/control-plane/simulations"
TOKEN_HEADER = "X-Control-Plane-Token"


def headers(tenant_id: uuid.UUID, *, token: str | None = None) -> dict[str, str]:
    sent = {"X-Tenant-Id": str(tenant_id)}
    if token is not None:
        sent[TOKEN_HEADER] = token
    return sent


#: The development token every other control-plane test uses. Spelled out
#: rather than read from settings, matching `test_api_keys` and `test_webhooks`:
#: a test that derives the token from the same object the endpoint checks it
#: against would pass even if the check compared a value to itself.
CONTROL_PLANE_TOKEN = "dev-only-insecure-control-plane-token-change-me"


async def seed_history(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID, *, count: int = 40
) -> int:
    """A tenant with baselines, some history, and an approved candidate rubric."""
    maker = async_sessionmaker(migrated_engine, expire_on_commit=False)
    with tenant_scope(tenant_id):
        async with maker() as session:
            await policy_service.seed_baselines(session, tenant_id=tenant_id)
            for index in range(count):
                await seed_complaint(session, tenant_id=tenant_id, at=BASE + timedelta(hours=index))
            version = await policy_service.draft(
                session,
                tenant_id=tenant_id,
                kind=PolicyKind.SEVERITY_RUBRIC,
                body=rubric(visual=0.9),
                change_reason="candidate under test",
            )
            for verb in (policy_service.submit_for_review, policy_service.approve):
                await verb(
                    session,
                    tenant_id=tenant_id,
                    kind=PolicyKind.SEVERITY_RUBRIC,
                    revision=version.revision,
                    reason="candidate under test",
                )
            await session.commit()
            return int(version.revision)


def window_payload() -> dict[str, str]:
    return {
        "window_start": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
        "window_end": datetime(2027, 1, 1, tzinfo=UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# Token discipline
# ---------------------------------------------------------------------------


async def test_a_backtest_requires_the_control_plane_token(
    api_client: AsyncClient, migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A backtest is a write.

    It reads history and decides nothing, but it creates a run row — and with
    ``certify`` a certificate that determines whether an activation may proceed.
    An endpoint that mints the evidence a guardrail consumes is not a read.
    """
    revision = await seed_history(migrated_engine, tenant_id)
    response = await api_client.post(
        f"{BASE_URL}/runs",
        headers=headers(tenant_id),
        json={"kind": "severity_rubric", "revision": revision, **window_payload()},
    )
    assert response.status_code == 403


async def test_reading_runs_needs_no_token(
    api_client: AsyncClient, migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Reading what a backtest found is the same class of operation as reading a rubric."""
    await seed_history(migrated_engine, tenant_id, count=1)
    response = await api_client.get(f"{BASE_URL}/runs", headers=headers(tenant_id))
    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------


async def test_a_backtest_produces_a_stored_report(
    api_client: AsyncClient,
    migrated_engine: AsyncEngine,
    tenant_id: uuid.UUID,
) -> None:
    """And the run is readable afterwards, carrying the same document.

    The API returns the stored report rather than re-deriving one, so the screen
    and the record cannot disagree.
    """
    revision = await seed_history(migrated_engine, tenant_id)
    created = await api_client.post(
        f"{BASE_URL}/runs",
        headers=headers(tenant_id, token=CONTROL_PLANE_TOKEN),
        json={"kind": "severity_rubric", "revision": revision, **window_payload()},
    )
    assert created.status_code == 201, created.text
    body = created.json()

    assert body["run"]["status"] == "completed"
    assert body["run"]["case_count"] == 40
    assert body["run"]["report"] is not None
    assert body["certificate"] is None, "nothing claimed about activation without certify"

    fetched = await api_client.get(
        f"{BASE_URL}/runs/{body['run']['id']}", headers=headers(tenant_id)
    )
    assert fetched.status_code == 200
    assert fetched.json()["report"] == body["run"]["report"]


async def test_a_window_with_too_little_history_is_a_422_naming_the_floor(
    api_client: AsyncClient,
    migrated_engine: AsyncEngine,
    tenant_id: uuid.UUID,
) -> None:
    """422 rather than 404: the window is not missing, it is too narrow.

    The remedy is to widen the request, which is what a 422 with the count in
    the detail tells the caller to do.
    """
    revision = await seed_history(migrated_engine, tenant_id, count=3)
    response = await api_client.post(
        f"{BASE_URL}/runs",
        headers=headers(tenant_id, token=CONTROL_PLANE_TOKEN),
        json={"kind": "severity_rubric", "revision": revision, **window_payload()},
    )

    assert response.status_code == 422
    assert "at least 30" in response.json()["detail"]


async def test_a_failed_run_is_still_recorded(
    api_client: AsyncClient,
    migrated_engine: AsyncEngine,
    tenant_id: uuid.UUID,
) -> None:
    """ "We tried and could not" is a different answer from "nobody tried".

    A table that only holds successes implies a diligence that did not happen.
    """
    revision = await seed_history(migrated_engine, tenant_id, count=3)
    await api_client.post(
        f"{BASE_URL}/runs",
        headers=headers(tenant_id, token=CONTROL_PLANE_TOKEN),
        json={"kind": "severity_rubric", "revision": revision, **window_payload()},
    )

    listing = await api_client.get(f"{BASE_URL}/runs", headers=headers(tenant_id))
    runs = listing.json()

    assert len(runs) == 1
    assert runs[0]["status"] == "failed"
    assert "at least 30" in runs[0]["failure_reason"]


async def test_another_tenants_run_is_a_404_not_a_403(
    api_client: AsyncClient,
    migrated_engine: AsyncEngine,
    tenant_id: uuid.UUID,
    other_tenant_id: uuid.UUID,
) -> None:
    """404-never-403, the convention Phase 5 set.

    A 403 confirms the run exists, which turns a run id into a way to enumerate
    another customer's policy experiments one guess at a time.
    """
    revision = await seed_history(migrated_engine, tenant_id)
    created = await api_client.post(
        f"{BASE_URL}/runs",
        headers=headers(tenant_id, token=CONTROL_PLANE_TOKEN),
        json={"kind": "severity_rubric", "revision": revision, **window_payload()},
    )
    run_id = created.json()["run"]["id"]

    response = await api_client.get(f"{BASE_URL}/runs/{run_id}", headers=headers(other_tenant_id))
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Evaluation sets and the guardrail, over HTTP
# ---------------------------------------------------------------------------


async def test_the_full_guardrail_path_over_http(
    api_client: AsyncClient,
    migrated_engine: AsyncEngine,
    tenant_id: uuid.UUID,
) -> None:
    """Create a set, label it, publish it, and watch an activation be refused.

    The end-to-end version of Phase 7's second gate clause, through the surface
    an operator actually uses.
    """
    revision = await seed_history(migrated_engine, tenant_id)
    listing = await api_client.get(f"{BASE_URL}/runs", headers=headers(tenant_id))
    assert listing.status_code == 200

    authorised = headers(tenant_id, token=CONTROL_PLANE_TOKEN)
    created = await api_client.post(
        f"{BASE_URL}/evaluation-sets",
        headers=authorised,
        json={
            "code": "ward-review",
            "name": "Ward review",
            "kind": "severity_rubric",
            "description": "Complaints the ward engineer re-scored by hand in March",
        },
    )
    assert created.status_code == 201, created.text

    # An unlabelled set cannot be published: an exam with no questions is one
    # every candidate passes.
    premature = await api_client.post(
        f"{BASE_URL}/evaluation-sets/ward-review/publish", headers=authorised
    )
    assert premature.status_code == 422

    labelled = await api_client.post(
        f"{BASE_URL}/evaluation-sets/ward-review/labels",
        headers=authorised,
        json={
            "complaint_id": str(uuid.uuid4()),
            "rationale": "reviewed on site",
            "expected_severity_tier": "medium",
        },
    )
    assert labelled.status_code == 201, labelled.text

    published = await api_client.post(
        f"{BASE_URL}/evaluation-sets/ward-review/publish", headers=authorised
    )
    assert published.status_code == 200
    assert published.json()["status"] == "published"
    assert published.json()["labels_hash"] is not None

    refused = await api_client.post(
        f"/api/v1/control-plane/policies/severity_rubric/{revision}/activate",
        headers=authorised,
        json={"reason": "not evaluated"},
    )
    assert refused.status_code == 409
    problem = refused.json()
    assert problem["type"].endswith("/not-certified")
    assert "ward-review" in problem["detail"]


async def test_a_published_set_appears_in_the_listing(
    api_client: AsyncClient,
    migrated_engine: AsyncEngine,
    tenant_id: uuid.UUID,
) -> None:
    await seed_history(migrated_engine, tenant_id, count=1)
    authorised = headers(tenant_id, token=CONTROL_PLANE_TOKEN)
    await api_client.post(
        f"{BASE_URL}/evaluation-sets",
        headers=authorised,
        json={
            "code": "ward-review",
            "name": "Ward review",
            "kind": "severity_rubric",
            "description": "Complaints the ward engineer re-scored by hand",
        },
    )
    await api_client.post(
        f"{BASE_URL}/evaluation-sets/ward-review/labels",
        headers=authorised,
        json={
            "complaint_id": str(uuid.uuid4()),
            "rationale": "reviewed",
            "expected_safety_fired": False,
        },
    )

    listing = await api_client.get(f"{BASE_URL}/evaluation-sets", headers=headers(tenant_id))
    labels = await api_client.get(
        f"{BASE_URL}/evaluation-sets/ward-review/labels", headers=headers(tenant_id)
    )

    assert listing.status_code == 200
    assert [entry["code"] for entry in listing.json()] == ["ward-review"]
    assert len(labels.json()) == 1
    assert labels.json()[0]["expected_safety_fired"] is False


async def test_an_unknown_evaluation_set_is_a_404(
    api_client: AsyncClient, tenant_id: uuid.UUID
) -> None:
    response = await api_client.get(
        f"{BASE_URL}/evaluation-sets/nope/labels", headers=headers(tenant_id)
    )
    assert response.status_code == 404
    assert response.json()["type"].endswith("/not-found")


async def test_a_misspelled_field_in_a_write_is_refused(
    api_client: AsyncClient, tenant_id: uuid.UUID
) -> None:
    """``extra="forbid"``, for the reason every control-plane input uses it.

    A misspelled field that is silently dropped produces a set that publishes
    successfully and gates differently from the one that was configured.
    """
    response = await api_client.post(
        f"{BASE_URL}/evaluation-sets",
        headers=headers(tenant_id, token=CONTROL_PLANE_TOKEN),
        json={
            "code": "typo",
            "name": "Typo",
            "kind": "severity_rubric",
            "description": "x" * 40,
            "pass_ration": 0.9,
        },
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Route ordering — Phase 6's lesson, re-checked
# ---------------------------------------------------------------------------


async def test_literal_routes_are_not_swallowed_by_variable_ones(
    api_client: AsyncClient, tenant_id: uuid.UUID
) -> None:
    """``/runs`` must not parse as anything but ``/runs``.

    Phase 6 shipped a bug where ``POST /{kind}`` swallowed ``/seed-baselines``,
    and the same shape here would make the endpoint that lists backtests
    unreachable — or worse, reachable as something else.
    """
    for path in ("/runs", "/evaluation-sets"):
        response = await api_client.get(f"{BASE_URL}{path}", headers=headers(tenant_id))
        assert response.status_code == 200, path


async def test_the_shadow_summary_of_an_unknown_candidate_is_empty_not_missing(
    api_client: AsyncClient, tenant_id: uuid.UUID
) -> None:
    """ "Nothing has been observed" is a real answer, and a 404 would not be one.

    A candidate nobody has run shadow mode against has a divergence rate of
    nothing-over-nothing, which is exactly what the caller needs to see before
    concluding the candidate is safe.
    """
    response = await api_client.get(f"{BASE_URL}/shadow/{'a' * 64}", headers=headers(tenant_id))
    assert response.status_code == 200
    assert response.json()["observed"] == 0
    assert response.json()["divergence_rate"] == 0.0


async def test_tuning_reports_its_own_one_sidedness(
    api_client: AsyncClient, tenant_id: uuid.UUID
) -> None:
    """An operator reading only increases must know why decreases never appear.

    They are not absent because none are warranted; no event in the log could
    support one. Saying so in the response is what stops the omission being read
    as a finding.
    """
    response = await api_client.post(
        f"{BASE_URL}/tuning/dedup",
        headers=headers(tenant_id, token=CONTROL_PLANE_TOKEN),
        json={},
    )
    assert response.status_code == 200
    assert response.json()["proposals"] == []
    assert "only be proposed upward" in response.json()["direction"]
