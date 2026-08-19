"""The policy API — the Phase 6 gate over HTTP.

The gate's first clause is that changing a severity weight, an SLA, a safety
keyword, or a routing rule *requires no deploy*, and the only honest way to
demonstrate that is through the surface an operator would actually use. The
service tests prove the lifecycle; these prove the surface — token enforcement,
error translation, route precedence, and the fact that a policy activated by one
request is what the next request reports as deciding.

``scripts/gate_phase6.py`` runs the same shape against the *running stack*, which
is what makes the "no deploy" claim about a deployment rather than about a test
process.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient

from nemesis.api.v1.control_plane import CONTROL_PLANE_TOKEN_HEADER
from nemesis.policy import baselines
from tests.conftest import postgres_required

pytestmark = [postgres_required, pytest.mark.integration]

DEV_TOKEN = "dev-only-insecure-control-plane-token-change-me"
POLICIES = "/api/v1/control-plane/policies"


def headers(tenant_id: uuid.UUID, *, token: bool = True) -> dict[str, str]:
    result = {"X-Tenant-ID": str(tenant_id)}
    if token:
        result[CONTROL_PLANE_TOKEN_HEADER] = DEV_TOKEN
    return result


def rubric(visual: float = 0.4) -> dict[str, Any]:
    return {
        "components": [
            {
                "key": "visual_damage",
                "display_name": "Visual damage",
                "weight": visual,
                "description": "How severe the defect looks.",
            },
            {
                "key": "road_class",
                "display_name": "Location importance",
                "weight": round(1.0 - visual, 6),
                "description": "How significant the location is.",
            },
        ]
    }


async def seed(client: AsyncClient, tenant_id: uuid.UUID) -> None:
    response = await client.post(f"{POLICIES}/seed-baselines", headers=headers(tenant_id))
    assert response.status_code == 200, response.text


async def make_live(
    client: AsyncClient,
    tenant_id: uuid.UUID,
    body: dict[str, Any],
    *,
    kind: str = "severity_rubric",
) -> int:
    """Draft, submit, approve, activate — the whole walk, over HTTP."""
    drafted = await client.post(
        f"{POLICIES}/{kind}",
        headers=headers(tenant_id),
        json={"body": body, "change_reason": "test"},
    )
    assert drafted.status_code == 201, drafted.text
    revision = int(drafted.json()["revision"])
    for verb in ("submit", "approve"):
        response = await client.post(
            f"{POLICIES}/{kind}/{revision}/{verb}",
            headers=headers(tenant_id),
            json={"reason": "test"},
        )
        assert response.status_code == 200, response.text
    activated = await client.post(
        f"{POLICIES}/{kind}/{revision}/activate",
        headers=headers(tenant_id),
        json={"reason": "test"},
    )
    assert activated.status_code == 200, activated.text
    return revision


# ---------------------------------------------------------------------------
# Authorisation
# ---------------------------------------------------------------------------


async def test_a_write_without_the_token_is_refused(
    api_client: AsyncClient, tenant_id: uuid.UUID
) -> None:
    """Changing a rubric redefines what every future complaint scores.

    At least as consequential as editing the taxonomy, so it carries the same
    shared secret until Phase 13 replaces it with an operator identity.
    """
    response = await api_client.post(
        f"{POLICIES}/severity_rubric",
        headers=headers(tenant_id, token=False),
        json={"body": rubric(), "change_reason": "x"},
    )
    assert response.status_code == 403


async def test_activation_without_the_token_is_refused(
    api_client: AsyncClient, tenant_id: uuid.UUID
) -> None:
    await seed(api_client, tenant_id)
    response = await api_client.post(
        f"{POLICIES}/severity_rubric/1/activate",
        headers=headers(tenant_id, token=False),
        json={"reason": "x"},
    )
    assert response.status_code == 403


async def test_reads_do_not_need_the_token(api_client: AsyncClient, tenant_id: uuid.UUID) -> None:
    """Reading which rubric scores your complaints is your own data.

    Same class of operation as reading your own taxonomy, and it goes through
    the same tenant resolution.
    """
    await seed(api_client, tenant_id)
    response = await api_client.get(
        f"{POLICIES}/severity_rubric/active", headers=headers(tenant_id, token=False)
    )
    assert response.status_code == 200


async def test_a_read_still_needs_a_tenant(api_client: AsyncClient) -> None:
    response = await api_client.get(f"{POLICIES}/severity_rubric/active")
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# The gate: a change with no deploy
# ---------------------------------------------------------------------------


async def test_a_severity_weight_changes_with_no_deploy(
    api_client: AsyncClient, tenant_id: uuid.UUID
) -> None:
    """The first gate clause, end to end over the surface an operator uses.

    Nothing here restarts a process, edits a file, or sets an environment
    variable. The weight that scores every subsequent complaint is different
    because of four HTTP requests.
    """
    await seed(api_client, tenant_id)
    before = await api_client.get(f"{POLICIES}/severity_rubric/active", headers=headers(tenant_id))
    assert before.json()["body"]["components"][0]["weight"] == 0.40

    revision = await make_live(api_client, tenant_id, rubric(visual=0.7))

    after = await api_client.get(f"{POLICIES}/severity_rubric/active", headers=headers(tenant_id))
    assert after.json()["revision"] == revision
    assert after.json()["body"]["components"][0]["weight"] == 0.7


async def test_a_safety_keyword_changes_with_no_deploy(
    api_client: AsyncClient, tenant_id: uuid.UUID
) -> None:
    """The same clause for §11.2's ruleset, which used to be keywords in source.

    Critique-log defect #1 lists "safety keywords in source" by name. This is
    the request that replaces the release that used to be needed.
    """
    await seed(api_client, tenant_id)
    body = {
        "rules": [
            {
                "rule_id": "hazard.local",
                "display_name": "Transformer oil leak",
                "rationale": "Oil under a live transformer is a fire risk.",
                "terms": ["transformer oil", "oil leak from pole"],
                "match_mode": "substring",
            }
        ]
    }
    await make_live(api_client, tenant_id, body, kind="safety_ruleset")

    active = await api_client.get(f"{POLICIES}/safety_ruleset/active", headers=headers(tenant_id))
    assert active.json()["body"]["rules"][0]["rule_id"] == "hazard.local"


async def test_the_activation_response_states_the_propagation_latency(
    api_client: AsyncClient, tenant_id: uuid.UUID
) -> None:
    """The honest answer is not "immediately", and saying so is the point.

    An operator who is told the change is live everywhere, sees a worker still
    using the old one, and concludes the button is broken will press it four
    more times — and then start editing the database.
    """
    await seed(api_client, tenant_id)
    drafted = await api_client.post(
        f"{POLICIES}/severity_rubric",
        headers=headers(tenant_id),
        json={"body": rubric(0.6), "change_reason": "x"},
    )
    revision = drafted.json()["revision"]
    for verb in ("submit", "approve"):
        await api_client.post(
            f"{POLICIES}/severity_rubric/{revision}/{verb}",
            headers=headers(tenant_id),
            json={"reason": "x"},
        )
    activated = await api_client.post(
        f"{POLICIES}/severity_rubric/{revision}/activate",
        headers=headers(tenant_id),
        json={"reason": "x"},
    )
    payload = activated.json()
    assert payload["superseded_revision"] == 1
    assert payload["reload"]["reload_interval_seconds"] > 0
    assert payload["reload"]["local_cache_invalidated"] is True


# ---------------------------------------------------------------------------
# The gate: a draft decides nothing
# ---------------------------------------------------------------------------


async def test_a_draft_is_visible_but_does_not_decide(
    api_client: AsyncClient, tenant_id: uuid.UUID
) -> None:
    await seed(api_client, tenant_id)
    drafted = await api_client.post(
        f"{POLICIES}/severity_rubric",
        headers=headers(tenant_id),
        json={"body": rubric(0.95), "change_reason": "experimental"},
    )
    assert drafted.status_code == 201

    listing = await api_client.get(f"{POLICIES}?kind=severity_rubric", headers=headers(tenant_id))
    statuses = {row["revision"]: row["status"] for row in listing.json()}
    assert statuses[2] == "draft"

    active = await api_client.get(f"{POLICIES}/severity_rubric/active", headers=headers(tenant_id))
    assert active.json()["revision"] == 1


async def test_activating_an_unapproved_draft_is_a_conflict_not_a_validation_error(
    api_client: AsyncClient, tenant_id: uuid.UUID
) -> None:
    """422 tells a client to fix the body and resend, which can never work here.

    409 says the resource is not in a state where this applies, which is the
    truth, and the detail names the transitions that are available.
    """
    await seed(api_client, tenant_id)
    drafted = await api_client.post(
        f"{POLICIES}/severity_rubric",
        headers=headers(tenant_id),
        json={"body": rubric(0.6), "change_reason": "x"},
    )
    response = await api_client.post(
        f"{POLICIES}/severity_rubric/{drafted.json()['revision']}/activate",
        headers=headers(tenant_id),
        json={"reason": "x"},
    )
    assert response.status_code == 409
    assert "in_review" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Route precedence — the reason the verb route is registered last
# ---------------------------------------------------------------------------


async def test_activate_is_not_swallowed_by_the_generic_verb_route(
    api_client: AsyncClient, tenant_id: uuid.UUID
) -> None:
    """FastAPI matches in registration order.

    Declared before ``activate``, the catch-all ``/{kind}/{revision}/{verb}``
    would swallow it — and the endpoint that changes what production does would
    be reachable by varying a path segment on the review endpoint. This test is
    what keeps the ordering from being "fixed" by an import sort.
    """
    await seed(api_client, tenant_id)
    revision = await make_live(api_client, tenant_id, rubric(0.6))
    active = await api_client.get(f"{POLICIES}/severity_rubric/active", headers=headers(tenant_id))
    assert active.json()["revision"] == revision


async def test_an_unknown_verb_is_a_404_naming_the_real_ones(
    api_client: AsyncClient, tenant_id: uuid.UUID
) -> None:
    await seed(api_client, tenant_id)
    response = await api_client.post(
        f"{POLICIES}/severity_rubric/1/yolo", headers=headers(tenant_id), json={"reason": "x"}
    )
    assert response.status_code == 404
    assert "approve" in response.json()["detail"]


async def test_seed_baselines_is_not_parsed_as_a_policy_kind(
    api_client: AsyncClient, tenant_id: uuid.UUID
) -> None:
    """``/seed-baselines`` shares a shape with ``POST /{kind}``.

    Registered after it, the enum would reject "seed-baselines" as an unknown
    kind and the backfill endpoint would be unreachable.
    """
    response = await api_client.post(f"{POLICIES}/seed-baselines", headers=headers(tenant_id))
    assert response.status_code == 200
    # Against ``SEEDED_KINDS`` rather than a literal set. What this test is
    # about is that the *route* resolves — the seeded set is a property of
    # ``policy.baselines``, which has its own test, and duplicating it here made
    # adding Phase 8's trust thresholds fail a routing test for a reason that
    # had nothing to do with routing.
    assert set(response.json()["seeded_kinds"]) == {kind.value for kind in baselines.SEEDED_KINDS}


async def test_seeding_twice_reports_what_it_skipped(
    api_client: AsyncClient, tenant_id: uuid.UUID
) -> None:
    """Running the backfill across a fleet twice must be safe and legible."""
    await seed(api_client, tenant_id)
    second = await api_client.post(f"{POLICIES}/seed-baselines", headers=headers(tenant_id))
    assert second.json()["seeded_kinds"] == []
    assert len(second.json()["skipped_kinds"]) == len(baselines.SEEDED_KINDS)


# ---------------------------------------------------------------------------
# Validation reaches the client as a usable message
# ---------------------------------------------------------------------------


async def test_an_invalid_document_is_refused_with_a_reason_an_author_can_act_on(
    api_client: AsyncClient, tenant_id: uuid.UUID
) -> None:
    body = rubric()
    body["components"][0]["weight"] = 0.9
    response = await api_client.post(
        f"{POLICIES}/severity_rubric",
        headers=headers(tenant_id),
        json={"body": body, "change_reason": "x"},
    )
    assert response.status_code == 422
    assert "sum to 1.0" in response.json()["detail"]


async def test_a_sandbox_escape_in_a_routing_condition_is_refused(
    api_client: AsyncClient, tenant_id: uuid.UUID
) -> None:
    """The end-to-end version of the sandbox tests.

    An operator can paste anything into a condition field, and the refusal has
    to happen before the document is stored — not at routing time, on a
    complaint.
    """
    response = await api_client.post(
        f"{POLICIES}/routing_rules",
        headers=headers(tenant_id),
        json={
            "body": {
                "rules": [
                    {
                        "rule_id": "r1",
                        "display_name": "x",
                        "condition": "__import__('os').system('id')",
                        "department_code": "PWD",
                    }
                ]
            },
            "change_reason": "x",
        },
    )
    assert response.status_code == 422
    assert "function calls are not available" in response.json()["detail"]


async def test_an_unknown_field_is_refused_rather_than_dropped(
    api_client: AsyncClient, tenant_id: uuid.UUID
) -> None:
    response = await api_client.post(
        f"{POLICIES}/severity_rubric",
        headers=headers(tenant_id),
        json={"body": rubric(), "change_reason": "x", "efective_from": "2026-01-01"},
    )
    assert response.status_code == 422


async def test_an_unknown_kind_is_rejected_by_the_path(
    api_client: AsyncClient, tenant_id: uuid.UUID
) -> None:
    response = await api_client.get(
        f"{POLICIES}/not_a_policy_kind/active", headers=headers(tenant_id)
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------


async def test_rollback_returns_the_new_revision_not_the_restored_one(
    api_client: AsyncClient, tenant_id: uuid.UUID
) -> None:
    """The number every subsequent decision will be stamped with.

    An operator shown "rolled back to revision 1" would then look for revision 1
    in the log and find that nothing since the rollback mentions it.
    """
    await seed(api_client, tenant_id)
    await make_live(api_client, tenant_id, rubric(0.9))

    response = await api_client.post(
        f"{POLICIES}/severity_rubric/rollback",
        headers=headers(tenant_id),
        json={"to_revision": 1, "reason": "over-scored minor defects"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["version"]["revision"] == 3
    assert payload["version"]["rolled_back_from_revision"] == 1
    assert payload["superseded_revision"] == 2

    active = await api_client.get(f"{POLICIES}/severity_rubric/active", headers=headers(tenant_id))
    assert active.json()["body"]["components"][0]["weight"] == 0.40


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


async def test_another_tenants_revision_is_not_found_never_forbidden(
    api_client: AsyncClient, tenant_id: uuid.UUID, other_tenant_id: uuid.UUID
) -> None:
    """403 would confirm the revision exists, turning the endpoint into an
    oracle for another customer's policy history."""
    await seed(api_client, tenant_id)
    response = await api_client.get(
        f"{POLICIES}/severity_rubric/1", headers=headers(other_tenant_id)
    )
    assert response.status_code == 404


async def test_a_listing_shows_only_the_calling_tenants_policies(
    api_client: AsyncClient, tenant_id: uuid.UUID, other_tenant_id: uuid.UUID
) -> None:
    await seed(api_client, tenant_id)
    response = await api_client.get(POLICIES, headers=headers(other_tenant_id))
    assert response.json() == []


# ---------------------------------------------------------------------------
# The fact schema
# ---------------------------------------------------------------------------


async def test_the_fact_endpoint_lists_the_routing_vocabulary(
    api_client: AsyncClient,
) -> None:
    """So an author can discover the vocabulary without reading the source.

    A name that appears here is a name that will compile, because it is the same
    schema ``RoutingRule`` validates against.
    """
    response = await api_client.get(f"{POLICIES}/facts")
    assert response.status_code == 200
    facts = {row["name"]: row["kind"] for row in response.json()}
    assert facts["severity"] == "number"
    assert facts["category_ancestors"] == "string_set"
    assert all(row["description"] for row in response.json())


async def test_the_fact_endpoint_needs_no_tenant(api_client: AsyncClient) -> None:
    """The schema is a property of the build, not of a customer."""
    response = await api_client.get(f"{POLICIES}/facts")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# The rest of the review surface
# ---------------------------------------------------------------------------


async def test_a_draft_can_be_edited_in_place(
    api_client: AsyncClient, tenant_id: uuid.UUID
) -> None:
    """The revision survives an edit; the content hash does not.

    The revision is what an operator quotes and a decision records, so consuming
    a new one per keystroke would make the numbers meaningless. The hash is what
    answers "is this the document I reviewed", so it has to move.
    """
    await seed(api_client, tenant_id)
    drafted = await api_client.post(
        f"{POLICIES}/severity_rubric",
        headers=headers(tenant_id),
        json={"body": rubric(0.5), "change_reason": "first pass"},
    )
    revision = drafted.json()["revision"]

    edited = await api_client.put(
        f"{POLICIES}/severity_rubric/{revision}",
        headers=headers(tenant_id),
        json={"body": rubric(0.8), "change_reason": "after review feedback"},
    )
    assert edited.status_code == 200
    assert edited.json()["revision"] == revision
    assert edited.json()["content_hash"] != drafted.json()["content_hash"]
    assert edited.json()["body"]["components"][0]["weight"] == 0.8


async def test_editing_an_approved_document_is_refused(
    api_client: AsyncClient, tenant_id: uuid.UUID
) -> None:
    """An approver signed off on specific content."""
    await seed(api_client, tenant_id)
    revision = await make_live(api_client, tenant_id, rubric(0.6))
    response = await api_client.put(
        f"{POLICIES}/severity_rubric/{revision}",
        headers=headers(tenant_id),
        json={"body": rubric(0.7)},
    )
    assert response.status_code == 409
    assert "cannot be edited" in response.json()["detail"]


async def test_a_rejection_records_the_reviewers_reason(
    api_client: AsyncClient, tenant_id: uuid.UUID
) -> None:
    """The author needs to know whether anybody actually looked."""
    await seed(api_client, tenant_id)
    drafted = await api_client.post(
        f"{POLICIES}/severity_rubric",
        headers=headers(tenant_id),
        json={"body": rubric(0.99), "change_reason": "aggressive retune"},
    )
    revision = drafted.json()["revision"]
    await api_client.post(
        f"{POLICIES}/severity_rubric/{revision}/submit",
        headers=headers(tenant_id),
        json={"reason": "please review"},
    )
    rejected = await api_client.post(
        f"{POLICIES}/severity_rubric/{revision}/reject",
        headers=headers(tenant_id),
        json={"reason": "this would flag every kerb scuff as urgent"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "archived"
    assert "kerb scuff" in rejected.json()["rejection_reason"]


async def test_withdrawing_returns_a_document_to_draft(
    api_client: AsyncClient, tenant_id: uuid.UUID
) -> None:
    """And clears the approval, so the signature cannot outlive the content."""
    await seed(api_client, tenant_id)
    drafted = await api_client.post(
        f"{POLICIES}/severity_rubric",
        headers=headers(tenant_id),
        json={"body": rubric(0.6), "change_reason": "x"},
    )
    revision = drafted.json()["revision"]
    for verb in ("submit", "approve"):
        await api_client.post(
            f"{POLICIES}/severity_rubric/{revision}/{verb}",
            headers=headers(tenant_id),
            json={"reason": "x"},
        )
    withdrawn = await api_client.post(
        f"{POLICIES}/severity_rubric/{revision}/withdraw",
        headers=headers(tenant_id),
        json={"reason": "the department changed its mind"},
    )
    assert withdrawn.status_code == 200
    assert withdrawn.json()["status"] == "draft"
    assert withdrawn.json()["approved_at"] is None


async def test_one_revision_reads_back_with_its_body_and_ancestry(
    api_client: AsyncClient, tenant_id: uuid.UUID
) -> None:
    """The API speaks revisions, not UUIDs.

    An operator quotes "revision 7"; a response full of ids for the same
    information is one nobody can read aloud during an incident.
    """
    await seed(api_client, tenant_id)
    drafted = await api_client.post(
        f"{POLICIES}/severity_rubric",
        headers=headers(tenant_id),
        json={"body": rubric(0.6), "change_reason": "x", "based_on_revision": 1},
    )
    revision = drafted.json()["revision"]

    detail = await api_client.get(
        f"{POLICIES}/severity_rubric/{revision}", headers=headers(tenant_id)
    )
    assert detail.status_code == 200
    assert detail.json()["based_on_revision"] == 1
    assert detail.json()["body"]["components"][0]["weight"] == 0.6


async def test_an_unknown_revision_is_a_404(api_client: AsyncClient, tenant_id: uuid.UUID) -> None:
    response = await api_client.get(f"{POLICIES}/severity_rubric/999", headers=headers(tenant_id))
    assert response.status_code == 404


async def test_a_kind_with_no_document_and_no_baseline_reports_why(
    api_client: AsyncClient, tenant_id: uuid.UUID
) -> None:
    """Routing rules name departments the platform cannot invent, and the 404
    says so rather than leaving an operator to guess at a missing feature."""
    response = await api_client.get(f"{POLICIES}/routing_rules/active", headers=headers(tenant_id))
    assert response.status_code == 404
    assert "no baseline" in response.json()["detail"]
