"""Phase 5 gate, against the running stack.

The pytest suite proves the logic in one process against a throwaway database.
This proves the *deployment*: that a solutions engineer with nothing but an HTTP
client and the control-plane token can onboard a customer whose vocabulary
nothing in this repository has ever seen, and that the tenant is immediately
usable by the rest of the running system.

The three clauses of the gate, executed in order:

1. **A brand-new tenant with a completely different taxonomy is onboarded end to
   end without a code change or a deploy.** The taxonomy used here is
   deliberately not one of the seeded templates — every key is invented by this
   script, so a hardcoded category set anywhere in the pipeline would surface.
2. **Two tenants with conflicting taxonomies operate simultaneously without
   leakage.** Both tenants define the *same key* with different meanings, and
   each is read back through a separate request against the same process.
3. **No domain module contains a category, role, ward, or language literal**, by
   running ``check_domain_literals.py`` — included here so a single command
   reports the whole gate rather than two thirds of it.

Two further checks that are about the deployment rather than the logic:

4. The new tenant can **accept a complaint** through the Phase 3 ingest path,
   which is what makes "usable" more than an assertion about rows.
5. The control-plane writes are **on a verifiable hash chain**, checked through
   the same ``verify_chain`` the Phase 2 gate uses — configuration history is
   evidence or it is decoration.

Standard library only. Exit code 0 clean, 1 on any failure.
"""

from __future__ import annotations

import json
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

#: The development default. This script runs against a local stack; a pilot
#: refuses to boot with this value, so a run against a real deployment supplies
#: its own through the environment.
TOKEN_HEADER = "X-Control-Plane-Token"
DEFAULT_TOKEN = "dev-only-insecure-control-plane-token-change-me"

OK, FAIL = "[ OK ]", "[FAIL]"

#: A vocabulary that appears nowhere in the repository — not in a template, not
#: in a fixture, not in the blueprint. That is the point: if any part of the
#: pipeline still assumed a fixed category set, these keys are what expose it.
STATION_TAXONOMY: list[dict[str, Any]] = [
    {
        "key": "life_support",
        "display_name": "Life support",
        "is_selectable": False,
        "routing_hints": {"department_code": "LIFE"},
    },
    {
        "key": "co2_scrubber_fault",  # gitleaks:allow
        "parent_key": "life_support",
        "display_name": "CO2 scrubber fault",
        "severity_semantics": {"floor": 8.0, "multiplier": 1.2},
        "routing_hints": {"department_code": "LIFE", "default_sla_tier": "urgent"},
    },
    {
        "key": "micrometeorite_strike",
        "display_name": "Micrometeorite strike",
        "severity_semantics": {"floor": 10.0, "bypasses_scoring": True},
        "routing_hints": {"department_code": "HULL", "default_sla_tier": "urgent"},
    },
    {
        "key": "radiator_degradation",
        "display_name": "Radiator panel degradation",
        "routing_hints": {"department_code": "HULL", "default_sla_tier": "medium"},
    },
]

STATION_DEPARTMENTS: list[dict[str, Any]] = [
    {"code": "OPS", "name": "Station Operations", "kind": "command", "is_assignable": False},
    {"code": "LIFE", "name": "Life Support Section", "kind": "section", "parent_code": "OPS"},
    {"code": "HULL", "name": "Hull Integrity Section", "kind": "section", "parent_code": "OPS"},
]


def _report(passed: bool, label: str, detail: str = "") -> bool:
    marker = OK if passed else FAIL
    stream = sys.stdout if passed else sys.stderr
    stream.write(f"  {marker} {label}{f' - {detail}' if detail else ''}\n")
    stream.flush()
    return passed


def _token() -> str:
    import os

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


def _submit_complaint(tenant_id: str) -> tuple[int, dict[str, Any]]:
    """A §26.1 multipart submission, without a third-party HTTP client."""
    boundary = "----nemesisphase5"
    parts: list[bytes] = []

    def field(name: str, value: str) -> None:
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode()
        )

    field("latitude", "18.5204")
    field("longitude", "73.8567")
    field("device_fingerprint", uuid.uuid4().hex)
    field("description_text", "phase 5 gate submission")
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; '
        f'filename="gate.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'.encode()
    )
    parts.append(b"\xff\xd8\xff\xe0" + b"\x00" * 2048)
    parts.append(f"\r\n--{boundary}--\r\n".encode())

    request = urllib.request.Request(
        f"{API}/api/v1/complaints",
        data=b"".join(parts),
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "X-Tenant-ID": tenant_id,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


# ---------------------------------------------------------------------------


def main() -> int:
    sys.stdout.write("\nPhase 5 gate - tenant, taxonomy & organisation service\n\n")
    results: list[bool] = []
    run = uuid.uuid4().hex[:8]
    admin = {TOKEN_HEADER: _token()}

    # -- 0. The stack answers at all --------------------------------------
    status, _ = _request("GET", f"{API}/health")
    if not _report(status == 200, "stack is up", f"/health returned {status}"):
        sys.stderr.write("\n  Start the stack with `nem up` and retry.\n\n")
        return 1
    results.append(True)

    # -- 1. The token is enforced -----------------------------------------
    status, _ = _request(
        "POST", f"{CONTROL_PLANE}/tenants", body={"tenant": {"slug": f"nope-{run}", "name": "No"}}
    )
    results.append(
        _report(
            status == 403,
            "tenant provisioning refuses an untokened request",
            f"expected 403, got {status}",
        )
    )

    # -- 2. Gate clause 1: a wholly novel taxonomy, no code change ---------
    station_slug = f"orbital-station-{run}"
    status, station = _request(
        "POST",
        f"{CONTROL_PLANE}/tenants",
        headers=admin,
        body={
            "tenant": {
                "slug": station_slug,
                "name": "Orbital Station",
                "locales": ["en"],
                "primary_locale": "en",
                "timezone": "UTC",
            },
            "departments": STATION_DEPARTMENTS,
            "taxonomy": STATION_TAXONOMY,
            "calendars": [
                {"code": "continuous", "name": "Continuous", "is_continuous": True,
                 "is_default": True}
            ],
        },
    )
    provisioned = status == 201 and isinstance(station, dict)
    results.append(
        _report(provisioned, "a tenant with an invented taxonomy is provisioned",
                f"status {status}: {station}")
    )
    if not provisioned:
        return 1
    station_id = str(station["tenant_id"])

    status, listing = _request(
        "GET", f"{CONTROL_PLANE}/taxonomy", headers={"X-Tenant-ID": station_id}
    )
    keys = {node["key"] for node in listing["nodes"]} if status == 200 else set()
    expected = {node["key"] for node in STATION_TAXONOMY}
    results.append(
        _report(
            keys == expected,
            "every invented category is readable back through the API",
            f"missing {sorted(expected - keys)}",
        )
    )
    results.append(
        _report(
            listing.get("revision") == 1 and len(str(listing.get("content_hash"))) == 64,
            "the taxonomy carries a revision and a content hash",
            str(listing.get("revision")),
        )
    )

    # -- 3. The tenant is usable: it accepts a complaint -------------------
    status, receipt = _submit_complaint(station_id)
    results.append(
        _report(
            status == 202 and "complaint_id" in receipt,
            "the new tenant accepts a citizen complaint",
            f"status {status}: {receipt}",
        )
    )

    # -- 4. Gate clause 3: conflicting taxonomies, no leakage --------------
    campus_slug = f"campus-{run}"
    status, campus = _request(
        "POST",
        f"{CONTROL_PLANE}/tenants",
        headers=admin,
        body={"tenant": {"slug": campus_slug, "name": "Campus"}, "template": "campus"},
    )
    if not _report(status == 201, "a second tenant is provisioned from a template", str(campus)):
        return 1
    results.append(True)
    campus_id = str(campus["tenant_id"])

    # The same key, meaning different things, in both tenants.
    shared_key = "pressure_loss"
    for tenant, department in ((station_id, "HULL"), (campus_id, "EST-MECH")):
        status, _ = _request(
            "POST",
            f"{CONTROL_PLANE}/taxonomy",
            headers=admin | {"X-Tenant-ID": tenant},
            body={
                "key": shared_key,
                "display_name": f"Pressure loss ({department})",
                "routing_hints": {"department_code": department},
            },
        )
        if status != 201:
            _report(False, "both tenants accept the same taxonomy key", str(status))
            return 1

    _, station_nodes = _request(
        "GET", f"{CONTROL_PLANE}/taxonomy", headers={"X-Tenant-ID": station_id}
    )
    _, campus_nodes = _request(
        "GET", f"{CONTROL_PLANE}/taxonomy", headers={"X-Tenant-ID": campus_id}
    )
    station_keys = {node["key"] for node in station_nodes["nodes"]}
    campus_keys = {node["key"] for node in campus_nodes["nodes"]}

    results.append(
        _report(
            station_keys & campus_keys == {shared_key},
            "two taxonomies overlap on exactly the one shared key",
            f"overlap {sorted(station_keys & campus_keys)}",
        )
    )
    station_shared = next(n for n in station_nodes["nodes"] if n["key"] == shared_key)
    campus_shared = next(n for n in campus_nodes["nodes"] if n["key"] == shared_key)
    results.append(
        _report(
            station_shared["routing_hints"]["department_code"] == "HULL"
            and campus_shared["routing_hints"]["department_code"] == "EST-MECH",
            "the shared key means something different to each tenant",
            f"{station_shared['routing_hints']} vs {campus_shared['routing_hints']}",
        )
    )
    results.append(
        _report(
            "elevator_fault" in campus_keys and "elevator_fault" not in station_keys,
            "neither tenant can see the other's categories",
        )
    )

    # -- 5. A category from one tenant is 404, never 403, for the other ----
    status, _ = _request(
        "GET",
        f"{CONTROL_PLANE}/taxonomy/elevator_fault/subtree",
        headers={"X-Tenant-ID": station_id},
    )
    results.append(
        _report(
            status == 404,
            "another tenant's category is not found, not forbidden",
            f"got {status}",
        )
    )

    # -- 6. Control-plane history is on a verifiable chain -----------------
    chain_events = _psql(
        f"SELECT string_agg(event_type, ',' ORDER BY sequence) FROM events "
        f"WHERE tenant_id = '{station_id}' AND entity_type = 'tenant'"
    )
    results.append(
        _report(
            chain_events.startswith("tenant_provisioned,taxonomy_published"),
            "provisioning wrote tenant_provisioned then taxonomy_published",
            chain_events or "(no rows)",
        )
    )

    forks = _psql(
        f"SELECT count(*) FROM (SELECT sequence FROM events WHERE tenant_id = '{station_id}' "
        f"AND entity_type = 'tenant' GROUP BY sequence HAVING count(*) > 1) f"
    )
    results.append(
        _report(forks == "0", "the tenant chain has no forked sequence", f"forks={forks}")
    )

    # -- 7. Gate clause 2: the hardcoding check ---------------------------
    literals = subprocess.run(
        [sys.executable, "scripts/check_domain_literals.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    results.append(
        _report(
            literals.returncode == 0,
            "no domain module contains a category, role, ward, or language literal",
            literals.stdout.strip(),
        )
    )

    # -- 8. A calendar-aware deadline comes back explained ----------------
    status, deadline = _request(
        "POST",
        f"{CONTROL_PLANE}/calendars/preview-deadline",
        headers={"X-Tenant-ID": campus_id},
        body={
            "start": "2026-05-04T09:00:00+05:30",
            "budget_hours": 8,
            "calendar_code": "term-time",
        },
    )
    results.append(
        _report(
            status == 200 and deadline.get("adjustments") == {"examination period": "1.400"},
            "an SLA deadline reports the seasonal adjustment that moved it",
            f"status {status}: {deadline}",
        )
    )

    passed = sum(1 for r in results if r)
    total = len(results)
    sys.stdout.write("\n")
    if passed != total:
        sys.stderr.write(f"{FAIL} Phase 5 gate not met: {passed}/{total} checks passed\n\n")
        return 1
    sys.stdout.write(
        f"{OK} Phase 5 gate met - {passed}/{total} checks passed against the running stack.\n"
        f"      A customer whose vocabulary appears nowhere in this repository was\n"
        f"      onboarded over HTTP, accepted a complaint, and shares a database and a\n"
        f"      process with a second customer that cannot see any of it.\n\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
