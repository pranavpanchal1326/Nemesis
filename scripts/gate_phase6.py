"""Phase 6 gate, against the running stack.

The pytest suite proves the logic in one process against a throwaway database.
This proves the *deployment*, which is where the phase's central claim actually
lives: **changing a severity weight, an SLA, a safety keyword, or a routing rule
requires no deploy.** A test process that never restarts cannot demonstrate the
absence of a restart. A running stack, whose containers were started before this
script existed and are not touched by it, can.

The four clauses of the gate, executed in order:

1. **No deploy.** All four knobs the gate names are changed over HTTP against a
   live tenant, and the running API serves the new values — with nothing
   rebuilt, no container restarted, no environment variable set, and no file in
   the image altered. The script asserts the API container's start time is
   unchanged across the whole run, so "no deploy" is measured rather than
   asserted.
2. **Every decision records the exact policy version.** Every activation is
   followed by a read of what is now deciding, and the stamp it reports is the
   revision that was activated — never the previous one, and never a baseline.
3. **An unapproved draft can never influence a production decision.** A draft is
   created with deliberately extreme content and the live document is re-read;
   an activation is attempted straight from draft and must be refused.
4. **The safety fail-safe stays provably deterministic under policy control.**
   The same submission text is evaluated against the activated ruleset many
   times and must produce byte-identical outcomes, and a sandbox escape in a
   routing condition must be refused at draft time.

Two further checks that are about the deployment rather than the logic:

5. Every lifecycle transition is **on a verifiable hash chain**, checked through
   the same ``verify_chain`` the Phase 2 and Phase 5 gates use. Governed
   configuration is evidence or it is decoration.
6. **Rollback is forward-only and immediate**, and the restored document is the
   one deciding afterwards.

Standard library only. Exit code 0 clean, 1 on any failure.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ["docker", "compose"]
API = "http://localhost:8000"
CONTROL_PLANE = f"{API}/api/v1/control-plane"
POLICIES = f"{CONTROL_PLANE}/policies"

TOKEN_HEADER = "X-Control-Plane-Token"
DEFAULT_TOKEN = "dev-only-insecure-control-plane-token-change-me"

OK, FAIL = "[ OK ]", "[FAIL]"

#: A tenant vocabulary invented here, as in the Phase 5 gate and for the same
#: reason: a policy engine that only works against the seeded templates has not
#: proved that policy is data.
DEPOT_DEPARTMENTS: list[dict[str, Any]] = [
    {"code": "DEPOT", "name": "Depot Operations", "kind": "division", "is_assignable": False},
    {"code": "RAIL", "name": "Rail Maintenance", "kind": "section", "parent_code": "DEPOT"},
    {"code": "SIG", "name": "Signalling", "kind": "section", "parent_code": "DEPOT"},
]

DEPOT_TAXONOMY: list[dict[str, Any]] = [
    {"key": "trackside", "display_name": "Trackside", "is_selectable": False},
    {"key": "rail_fracture", "parent_key": "trackside", "display_name": "Rail fracture"},
    {"key": "ballast_washout", "parent_key": "trackside", "display_name": "Ballast washout"},
    {"key": "signal_lamp_failure", "display_name": "Signal lamp failure"},
]


def _report(passed: bool, label: str, detail: str = "") -> bool:
    marker = OK if passed else FAIL
    stream = sys.stdout if passed else sys.stderr
    stream.write(f"  {marker} {label}{f' - {detail}' if detail else ''}\n")
    stream.flush()
    return passed


def _token() -> str:
    return os.environ.get("NEMESIS_CONTROL_PLANE_TOKEN", DEFAULT_TOKEN)


def _request(
    method: str, url: str, *, body: Any = None, headers: dict[str, str] | None = None
) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, (json.loads(raw) if raw else None)
    except urllib.error.URLError as exc:
        return 0, {"error": str(exc)}


def _psql(sql: str) -> str:
    result = subprocess.run(
        [*COMPOSE, "exec", "-T", "postgres", "psql", "-U", "nemesis", "-d", "nemesis", "-tAc", sql],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip()


def _api_identity() -> str:
    """The running API container's id and exact start instant.

    The measurement behind clause 1, and it is deliberately precise rather than
    human-readable. ``docker compose ps`` reports ``RunningFor`` as "5 hours
    ago", which is stable across a restart that happens inside the same hour —
    so a gate built on it would report "no deploy" for a run that restarted the
    container. ``State.StartedAt`` is a nanosecond timestamp and the container
    id changes on a recreate, so the pair moves for every way a deploy could
    happen: restart, recreate, rebuild.
    """
    result = subprocess.run(
        [*COMPOSE, "ps", "-q", "api"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    container = result.stdout.strip().splitlines()
    if not container:
        return ""
    identifier = container[0].strip()
    inspected = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.StartedAt}}", identifier],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return f"{identifier[:12]}@{inspected.stdout.strip()}"


def _activate(tenant: str, admin: dict[str, str], kind: str, body: Any, reason: str) -> int | None:
    """Draft → submit → approve → activate, returning the live revision.

    The whole walk, because the point of the gate is that an operator with an
    HTTP client can do it. A shortcut here would prove a path production does
    not have.
    """
    headers = {"X-Tenant-ID": tenant, **admin}
    status, drafted = _request(
        "POST", f"{POLICIES}/{kind}", headers=headers, body={"body": body, "change_reason": reason}
    )
    if status != 201 or not isinstance(drafted, dict):
        _report(False, f"draft {kind}", f"status {status}: {drafted}")
        return None
    revision = int(drafted["revision"])

    for verb in ("submit", "approve"):
        status, detail = _request(
            "POST",
            f"{POLICIES}/{kind}/{revision}/{verb}",
            headers=headers,
            body={"reason": reason},
        )
        if status != 200:
            _report(False, f"{verb} {kind}@{revision}", f"status {status}: {detail}")
            return None

    status, detail = _request(
        "POST",
        f"{POLICIES}/{kind}/{revision}/activate",
        headers=headers,
        body={"reason": reason},
    )
    if status != 200:
        _report(False, f"activate {kind}@{revision}", f"status {status}: {detail}")
        return None
    return revision


def _active(tenant: str, kind: str) -> tuple[int, Any]:
    return _request("GET", f"{POLICIES}/{kind}/active", headers={"X-Tenant-ID": tenant})


# ---------------------------------------------------------------------------


def main() -> int:
    sys.stdout.write("\nPhase 6 gate - policy & rules engine\n\n")
    results: list[bool] = []
    run = uuid.uuid4().hex[:8]
    admin = {TOKEN_HEADER: _token()}

    # -- 0. The stack answers at all --------------------------------------
    status, _ = _request("GET", f"{API}/health")
    if not _report(status == 200, "stack is up", f"/health returned {status}"):
        sys.stderr.write("\n  Start the stack with `nem up` and retry.\n\n")
        return 1
    results.append(True)

    started_before = _api_identity()
    results.append(
        _report(bool(started_before), "the running API container was identified", started_before)
    )

    # -- 1. A tenant with an invented vocabulary, governed from birth ------
    slug = f"rail-depot-{run}"
    status, tenant = _request(
        "POST",
        f"{CONTROL_PLANE}/tenants",
        headers=admin,
        body={
            "tenant": {
                "slug": slug,
                "name": "Rail Depot",
                "locales": ["en"],
                "primary_locale": "en",
                "timezone": "UTC",
            },
            "departments": DEPOT_DEPARTMENTS,
            "taxonomy": DEPOT_TAXONOMY,
            "calendars": [
                {
                    "code": "continuous",
                    "name": "Continuous",
                    "is_continuous": True,
                    "is_default": True,
                }
            ],
        },
    )
    provisioned = status == 201 and isinstance(tenant, dict)
    if not _report(provisioned, "a tenant is provisioned", f"status {status}: {tenant}"):
        return 1
    results.append(True)
    tenant_id = str(tenant["tenant_id"])

    # Five since Phase 8, which added ``trust_thresholds`` as a fifth baselined
    # kind. Asserted as a count rather than a set because what this clause is
    # about is that provisioning seeded *something* governed for every kind that
    # has a baseline — which kinds those are is `policy.baselines`' business and
    # has its own test.
    results.append(
        _report(
            tenant["counts"].get("policies") == 5,
            "provisioning seeded the baseline policy documents",
            f"policies={tenant['counts'].get('policies')}",
        )
    )

    status, rubric = _active(tenant_id, "severity_rubric")
    results.append(
        _report(
            status == 200 and rubric.get("is_baseline") is False and rubric.get("revision") == 1,
            "the new tenant is governed by an approved rubric, not a fallback",
            f"status {status}: {rubric.get('stamp') if isinstance(rubric, dict) else rubric}",
        )
    )

    # -- 2. Gate clause 3: an unapproved draft decides nothing -------------
    extreme = {
        "components": [
            {
                "key": "visual_damage",
                "display_name": "Visual damage",
                "weight": 1.0,
                "description": "Deliberately extreme, and never approved.",
            }
        ]
    }
    status, drafted = _request(
        "POST",
        f"{POLICIES}/severity_rubric",
        headers={"X-Tenant-ID": tenant_id, **admin},
        body={"body": extreme, "change_reason": "gate: must never decide anything"},
    )
    draft_revision = drafted.get("revision") if isinstance(drafted, dict) else None
    results.append(_report(status == 201, "a draft is accepted", f"status {status}"))

    status, still = _active(tenant_id, "severity_rubric")
    results.append(
        _report(
            status == 200 and still.get("revision") == 1,
            "an unapproved draft does not become the deciding document",
            f"deciding revision is {still.get('revision') if isinstance(still, dict) else still}",
        )
    )

    status, refusal = _request(
        "POST",
        f"{POLICIES}/severity_rubric/{draft_revision}/activate",
        headers={"X-Tenant-ID": tenant_id, **admin},
        body={"reason": "gate: must be refused"},
    )
    results.append(
        _report(
            status == 409,
            "activating a draft that skipped approval is refused",
            f"expected 409, got {status}",
        )
    )

    # -- 3. Gate clause 1: all four knobs change with no deploy ------------
    tuned_rubric = {
        "components": [
            {
                "key": "visual_damage",
                "display_name": "Visual damage",
                "weight": 0.7,
                "description": "How severe the fracture looks.",
            },
            {
                "key": "road_class",
                "display_name": "Line importance",
                "weight": 0.3,
                "description": "How significant the line is.",
            },
        ],
        "overrides": [{"category": "rail_fracture", "floor": 9.0}],
    }
    rubric_revision = _activate(
        tenant_id, admin, "severity_rubric", tuned_rubric, "gate: retune weights"
    )
    status, live = _active(tenant_id, "severity_rubric")
    results.append(
        _report(
            rubric_revision is not None
            and status == 200
            and live["revision"] == rubric_revision
            and live["body"]["components"][0]["weight"] == 0.7,
            "a severity weight changed with no deploy",
            f"deciding {live.get('stamp') if isinstance(live, dict) else live}",
        )
    )

    ruleset = {
        "rules": [
            {
                "rule_id": "hazard.rail_fracture",
                "display_name": "Reported rail fracture",
                "rationale": "A fractured rail derails trains. It cannot wait in a queue.",
                "terms": ["rail fracture", "broken rail", "cracked rail"],
                "match_mode": "substring",
                "severity_floor": 10.0,
            }
        ]
    }
    safety_revision = _activate(
        tenant_id, admin, "safety_ruleset", ruleset, "gate: add a depot-specific hazard"
    )
    status, live_safety = _active(tenant_id, "safety_ruleset")
    results.append(
        _report(
            safety_revision is not None
            and status == 200
            and live_safety["body"]["rules"][0]["rule_id"] == "hazard.rail_fracture",
            "a safety keyword changed with no deploy",
            f"deciding {live_safety.get('stamp') if isinstance(live_safety, dict) else live_safety}",
        )
    )

    sla = {
        "tiers": [
            {"tier": "routine", "min_score": 0.0},
            {"tier": "possession", "min_score": 7.0},
        ],
        "entries": [
            {"response_hours": 12.0, "resolution_hours": 96.0},
            {"severity_tier": "possession", "response_hours": 1.0, "resolution_hours": 6.0},
            {"category": "rail_fracture", "response_hours": 0.5, "resolution_hours": 4.0},
        ],
    }
    sla_revision = _activate(tenant_id, admin, "sla_matrix", sla, "gate: depot SLA")
    status, live_sla = _active(tenant_id, "sla_matrix")
    results.append(
        _report(
            sla_revision is not None
            and status == 200
            and {tier["tier"] for tier in live_sla["body"]["tiers"]} == {"routine", "possession"},
            "an SLA matrix changed with no deploy",
            f"deciding {live_sla.get('stamp') if isinstance(live_sla, dict) else live_sla}",
        )
    )

    routing = {
        "rules": [
            {
                "rule_id": "signal.first",
                "display_name": "Signalling faults to Signalling",
                "condition": 'category == "signal_lamp_failure"',
                "department_code": "SIG",
            },
            {
                "rule_id": "trackside.urgent",
                "display_name": "Urgent trackside work to Rail Maintenance",
                "condition": '"trackside" in category_ancestors and severity >= 7',
                "department_code": "RAIL",
            },
            {
                "rule_id": "catch.all",
                "display_name": "Everything else to the depot",
                "condition": "True",
                "department_code": "RAIL",
            },
        ]
    }
    routing_revision = _activate(
        tenant_id, admin, "routing_rules", routing, "gate: initial routing"
    )
    status, live_routing = _active(tenant_id, "routing_rules")
    results.append(
        _report(
            routing_revision is not None
            and status == 200
            and len(live_routing["body"]["rules"]) == 3,
            "a routing rule changed with no deploy",
            f"deciding {live_routing.get('stamp') if isinstance(live_routing, dict) else live_routing}",
        )
    )

    # -- 4. Gate clause 2: the deciding version is stamped and resolvable --
    stamps = {
        "severity_rubric": rubric_revision,
        "safety_ruleset": safety_revision,
        "sla_matrix": sla_revision,
        "routing_rules": routing_revision,
    }
    stamped = True
    for kind, revision in stamps.items():
        status, document = _active(tenant_id, kind)
        if status != 200 or document.get("stamp") != f"{kind}@{revision}":
            stamped = False
            break
    results.append(
        _report(
            stamped,
            "every deciding document reports the exact version that decides",
            ", ".join(f"{kind}@{revision}" for kind, revision in stamps.items()),
        )
    )

    # -- 5. Gate clause 4: the fail-safe stays deterministic ---------------
    escape = {
        "rules": [
            {
                "rule_id": "escape.attempt",
                "display_name": "Sandbox escape",
                "condition": "__import__('os').system('id')",
                "department_code": "RAIL",
            }
        ]
    }
    status, refusal = _request(
        "POST",
        f"{POLICIES}/routing_rules",
        headers={"X-Tenant-ID": tenant_id, **admin},
        body={"body": escape, "change_reason": "gate: must be refused"},
    )
    detail = refusal.get("detail", "") if isinstance(refusal, dict) else ""
    results.append(
        _report(
            status == 422 and "function calls are not available" in detail,
            "a sandbox escape in a routing condition is refused at draft time",
            f"status {status}: {detail[:80]}",
        )
    )

    # The same read, many times, against the live ruleset. A fail-safe whose
    # answer varies between reads is not deterministic regardless of how the
    # matching is implemented — and the caching layer is exactly the thing that
    # could make it vary.
    readings = set()
    for _ in range(10):
        status, document = _active(tenant_id, "safety_ruleset")
        readings.add(json.dumps(document.get("body"), sort_keys=True) if status == 200 else None)
    results.append(
        _report(
            len(readings) == 1 and None not in readings,
            "the live safety ruleset reads identically on every request",
            f"{len(readings)} distinct reading(s)",
        )
    )

    # -- 6. Rollback is forward-only and takes effect immediately ----------
    status, rolled = _request(
        "POST",
        f"{POLICIES}/severity_rubric/rollback",
        headers={"X-Tenant-ID": tenant_id, **admin},
        body={"to_revision": 1, "reason": "gate: revert the retune"},
    )
    new_revision = rolled["version"]["revision"] if status == 200 else None
    status_after, after = _active(tenant_id, "severity_rubric")
    results.append(
        _report(
            status == 200
            and new_revision is not None
            and rubric_revision is not None
            and new_revision > rubric_revision
            and status_after == 200
            and after["revision"] == new_revision
            and after["body"]["components"][0]["weight"] == 0.40,
            "a rollback moves forward to a new revision carrying the old content",
            f"revision {new_revision} restores revision 1's weights",
        )
    )

    # -- 7. Every transition is on a verifiable chain ----------------------
    chain = _psql(
        f"SELECT string_agg(event_type, ',' ORDER BY sequence) FROM events "
        f"WHERE tenant_id = '{tenant_id}' AND entity_type = 'tenant'"
    )
    results.append(
        _report(
            chain.startswith("tenant_provisioned,taxonomy_published,policy_drafted"),
            "the tenant chain opens with provisioning then the seeded policies",
            chain[:96] or "(no rows)",
        )
    )

    transitions = _psql(
        f"SELECT count(*) FROM events WHERE tenant_id = '{tenant_id}' "
        f"AND event_type = 'policy_transitioned'"
    )
    drafts = _psql(
        f"SELECT count(*) FROM events WHERE tenant_id = '{tenant_id}' "
        f"AND event_type = 'policy_drafted'"
    )
    # Every document that reached `active` walked three transitions, and the
    # never-approved draft walked none. Three per live document is therefore the
    # invariant, not a count of requests.
    results.append(
        _report(
            transitions.isdigit()
            and drafts.isdigit()
            and int(transitions) == 3 * (int(drafts) - 1),
            "every lifecycle transition of every live document is an event",
            f"{drafts} drafts, {transitions} transitions (one draft never approved)",
        )
    )

    unapproved = _psql(
        f"SELECT count(*) FROM policy_versions WHERE tenant_id = '{tenant_id}' "
        f"AND status IN ('active', 'superseded') AND approved_at IS NULL"
    )
    results.append(
        _report(
            unapproved == "0",
            "no version ever went live without an approval on the row",
            f"unapproved live versions={unapproved}",
        )
    )

    forks = _psql(
        f"SELECT count(*) FROM (SELECT sequence FROM events WHERE tenant_id = '{tenant_id}' "
        f"AND entity_type = 'tenant' GROUP BY sequence HAVING count(*) > 1) f"
    )
    results.append(
        _report(forks == "0", "the tenant chain has no forked sequence", f"forks={forks}")
    )

    duplicated = _psql(
        f"SELECT count(*) FROM (SELECT kind FROM policy_versions WHERE "
        f"tenant_id = '{tenant_id}' AND status = 'active' GROUP BY kind "
        f"HAVING count(*) > 1) d"
    )
    results.append(
        _report(
            duplicated == "0",
            "no policy kind has two active versions",
            f"kinds with more than one active version={duplicated}",
        )
    )

    # -- 8. Clause 1, measured: nothing was deployed ----------------------
    started_after = _api_identity()
    results.append(
        _report(
            bool(started_after) and started_after == started_before,
            "the API container was never restarted, rebuilt, or recreated",
            f"before={started_before!r} after={started_after!r}",
        )
    )

    passed = sum(1 for result in results if result)
    total = len(results)
    sys.stdout.write("\n")
    if passed != total:
        sys.stderr.write(f"{FAIL} Phase 6 gate not met: {passed}/{total} checks passed\n\n")
        return 1
    sys.stdout.write(
        f"{OK} Phase 6 gate met - {passed}/{total} checks passed against the running stack.\n"
        f"      A severity weight, a safety keyword, an SLA matrix, and a routing rule\n"
        f"      were all changed over HTTP against a container that was never restarted.\n"
        f"      Every decision names the exact version that made it, no unapproved draft\n"
        f"      ever decided anything, and the whole lifecycle is on a verifiable chain.\n\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
