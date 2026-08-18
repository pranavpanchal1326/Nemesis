"""The pinned contract test the Phase 4 gate names.

``nemesis/api/api_contract_lock.json`` is the counterpart of
``events/schema_lock.json``: Phase 2 made an event payload change without an
upcaster a CI failure, and this makes a *response shape* change without a version
bump one.

The tests below do two different jobs and both are necessary:

* ``test_the_published_contract_is_intact`` is the pin. It fails when somebody
  removes a public field.
* The rest verify the *checker* — that it actually detects removals, renames,
  type changes, and newly-required parameters, and that it does not fire on
  additive changes. A lock check nobody has watched fail is a lock check that
  might be comparing two empty dicts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nemesis.api import contract
from nemesis.api.versioning import VersionStatus, all_versions
from tests.conftest import postgres_required


def test_the_published_contract_is_intact() -> None:
    """The pin. A removed or narrowed public field fails here.

    Re-lock deliberately with ``nem api-lock`` when a change is genuinely
    additive, and say why in the commit — never as a reflex to make this green.
    """
    locked = json.loads(contract.LOCK_PATH.read_text(encoding="utf-8"))
    problems = contract.compare(locked, contract.build_snapshot())
    assert not problems, "\n  " + "\n  ".join(problems)


def test_the_lock_file_is_committed_and_not_empty() -> None:
    """A lock comparing two empty dicts passes and enforces nothing."""
    assert contract.LOCK_PATH.exists()
    locked = json.loads(contract.LOCK_PATH.read_text(encoding="utf-8"))
    assert locked, "the contract lock is empty"
    assert "v1" in locked
    assert len(locked["v1"]) >= 5, "too few operations locked to be meaningful"


def test_the_public_endpoints_are_actually_locked() -> None:
    """The §26.4 surface specifically, because that is the promise to §16.3."""
    locked = json.loads(contract.LOCK_PATH.read_text(encoding="utf-8"))
    paths = set(locked["v1"])
    for expected in (
        "GET /api/v1/public/{tenant_slug}/ward/{zone_code}/summary",
        "GET /api/v1/public/{tenant_slug}/contractor/{contractor_id}/profile",
        "GET /api/v1/public/{tenant_slug}/budget/{zone_code}",
        "GET /api/v1/public/{tenant_slug}/zones",
    ):
        assert expected in paths, f"{expected} is not under the contract lock"


def test_a_preview_version_is_not_locked() -> None:
    """A version carrying no compatibility promise cannot break one.

    Read from the registry rather than a list here, so promoting v2 to active
    brings it under the lock automatically — the mistake avoided is a version
    going stable while its lock entry stays exempt because nobody edited two
    files.
    """
    locked = json.loads(contract.LOCK_PATH.read_text(encoding="utf-8"))
    previews = {v.name for v in all_versions() if v.status is VersionStatus.PREVIEW}
    assert previews, "no preview version declared; this test would be vacuous"
    assert not (previews & set(locked))


# ---------------------------------------------------------------------------
# The checker itself
# ---------------------------------------------------------------------------


def _one_operation(response: dict[str, Any], params: list[str] | None = None) -> dict[str, Any]:
    return {"v1": {"GET /api/v1/public/x": {"response": response, "required_params": params or []}}}


def test_a_removed_field_is_breaking() -> None:
    locked = _one_operation({"total_reports": {"type": "integer", "required": True}})
    problems = contract.compare(locked, _one_operation({}))
    assert any("removed" in p for p in problems), problems


def test_a_renamed_field_is_breaking() -> None:
    """A rename is a removal plus an addition, and the removal is what breaks."""
    locked = _one_operation({"ward_id": {"type": "string", "required": True}})
    current = _one_operation({"zone_code": {"type": "string", "required": True}})
    assert any("ward_id" in p and "removed" in p for p in contract.compare(locked, current))


def test_a_type_change_is_breaking() -> None:
    locked = _one_operation({"count": {"type": "integer", "required": True}})
    current = _one_operation({"count": {"type": "string", "required": True}})
    assert any("changed type" in p for p in contract.compare(locked, current))


def test_a_field_becoming_optional_is_breaking() -> None:
    """A consumer that reads it unconditionally now receives a null it never handled."""
    locked = _one_operation({"notice": {"type": "string", "required": True}})
    current = _one_operation({"notice": {"type": "string", "required": False}})
    assert any("became optional" in p for p in contract.compare(locked, current))


def test_a_removed_operation_is_breaking() -> None:
    locked = _one_operation({"a": {"type": "string", "required": True}})
    assert any("removed" in p for p in contract.compare(locked, {"v1": {}}))


def test_a_removed_version_is_breaking() -> None:
    """A published version is withdrawn through the clock, never by deleting a router."""
    locked = _one_operation({"a": {"type": "string", "required": True}})
    problems = contract.compare(locked, {})
    assert any("deprecation clock" in p for p in problems), problems


def test_a_newly_required_parameter_is_breaking() -> None:
    """Every existing caller omits it and now gets a 422."""
    locked = _one_operation({"a": {"type": "string", "required": True}}, params=["tenant_slug"])
    current = _one_operation(
        {"a": {"type": "string", "required": True}}, params=["tenant_slug", "fiscal_year"]
    )
    assert any("became required" in p for p in contract.compare(locked, current))


def test_an_added_field_is_not_breaking() -> None:
    """Forcing a version bump for every addition produces a v7 nobody migrates to.

    Which is worse for compatibility than having no versions at all.
    """
    locked = _one_operation({"a": {"type": "string", "required": True}})
    current = _one_operation(
        {
            "a": {"type": "string", "required": True},
            "b": {"type": "integer", "required": False},
        }
    )
    assert contract.compare(locked, current) == []


def test_a_new_operation_is_not_breaking() -> None:
    locked = _one_operation({"a": {"type": "string", "required": True}})
    current = json.loads(json.dumps(locked))
    current["v1"]["GET /api/v1/public/y"] = {"response": {}, "required_params": []}
    assert contract.compare(locked, current) == []


def test_a_new_optional_parameter_is_not_breaking() -> None:
    locked = _one_operation({"a": {"type": "string", "required": True}}, params=["tenant_slug"])
    current = _one_operation({"a": {"type": "string", "required": True}}, params=["tenant_slug"])
    assert contract.compare(locked, current) == []


@postgres_required
def test_the_snapshot_reflects_the_running_app() -> None:
    """The lock compares against generated output, not a committed spec.

    A committed OpenAPI document would be a third artefact that can drift, and
    the one it drifts away from is the one consumers actually receive.
    """
    snapshot = contract.build_snapshot()
    assert "v1" in snapshot
    summary = snapshot["v1"]["GET /api/v1/public/{tenant_slug}/ward/{zone_code}/summary"]
    assert summary["response"]["total_reports"]["type"] == "integer"
    assert "tenant_slug" in summary["required_params"]


def test_the_lock_path_lives_beside_the_code_it_locks() -> None:
    """Not in ``scripts/``: the checker has to construct the app to read the
    contract the code actually serves, and the api container mounts only
    ``./backend``."""
    assert contract.LOCK_PATH.name == "api_contract_lock.json"
    assert contract.LOCK_PATH.parent.name == "api"
    assert Path(contract.__file__).parent == contract.LOCK_PATH.parent


def test_recursive_schemas_do_not_hang_the_resolver() -> None:
    """A taxonomy tree is genuinely self-referential; this is not defensive padding."""
    spec = {
        "components": {
            "schemas": {
                "Node": {
                    "type": "object",
                    "properties": {"child": {"$ref": "#/components/schemas/Node"}},
                    "required": ["child"],
                }
            }
        }
    }
    resolved = contract._resolve({"$ref": "#/components/schemas/Node"}, spec, frozenset())
    assert "child" in resolved
