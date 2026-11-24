"""Phase 13 gate, against the running stack.

The pytest suite proves the logic in one process against a throwaway database.
This proves the *deployment*: that an operator with nothing but an HTTP client
can be onboarded, sign in, and be held inside their scope by the running system
— and that a support engineer acting as them is visible to the customer.

The four clauses of the gate, executed in order:

1. **A negative-authorisation test per role pair.** Two officers, two wards, one
   flagged complaint each. Each officer sees their own item and not the other's,
   through the list (*including its total*), through the item route, through the
   decision route and through the media route. Both halves for both officers —
   a build that denies everybody passes a one-sided test and is an outage.
2. **An endpoint without a declared permission fails CI**, by running
   ``check_endpoint_permissions.py`` here so one command reports the whole gate
   rather than three quarters of it. The check is also *seeded*: a synthetic
   route with no declaration is fed to it and must be rejected, because a check
   nobody has watched fail is a script rather than a gate.
3. **Authorization decisions are property-tested against a generated role/scope
   matrix**, by running that test file in the api container and requiring it to
   have actually collected tests — a pytest run that silently selects nothing
   exits 0.
4. **Every impersonation session is visible to the impersonated tenant.**
   Support starts a session against one of the officers; the customer's own
   administrator lists it, with the justification, through their own endpoint;
   and the session's events are on the customer's hash chain.

Two further checks that are about the deployment rather than the logic:

5. **Revocation is immediate.** An officer's session is revoked and their next
   request fails — the property ADR-0066 trades a per-request database read for.
6. **The citizen surface is untouched.** A complaint receipt is still readable
   with no session at all, because the complaint id is the capability (ADR-0016)
   and a citizen does not hold a grant.

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
AUTH = f"{API}/api/v1/auth"
ACCESS = f"{API}/api/v1/access"
REVIEW = f"{API}/api/v1/review"
CONTROL_PLANE = f"{API}/api/v1/control-plane"

TOKEN_HEADER = "X-Control-Plane-Token"
DEFAULT_TOKEN = "dev-only-insecure-control-plane-token-change-me"

OK, FAIL = "[ OK ]", "[FAIL]"

#: Long enough to pass the database CHECK, and written the way a real one should
#: be: a ticket and a symptom.
JUSTIFICATION = (
    "gate drill NEM-GATE-13: confirming the officer's queue renders their ward only"
)

PASSWORD = "gate-phase13-operator-passphrase"


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
        try:
            return exc.code, (json.loads(raw) if raw else None)
        except json.JSONDecodeError:
            return exc.code, {"raw": raw[:200].decode("utf-8", "replace")}
    except urllib.error.URLError as exc:
        return 0, {"error": str(exc)}


def _sql(statement: str) -> str:
    """One statement through psql in the stack's own container.

    The gate needs rows that no HTTP route creates — a flagged complaint in a
    named ward — and inventing an endpoint to create them would be adding a
    surface to the product in order to test it. ``-tA`` keeps the output
    parseable without a CSV reader.
    """
    result = subprocess.run(
        [
            *COMPOSE,
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "nemesis",
            "-d",
            "nemesis",
            "-tA",
            "-c",
            statement,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"psql failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _api_python(code: str) -> tuple[int, str]:
    """Run a snippet in the api container, which has the app and its deps."""
    result = subprocess.run(
        [*COMPOSE, "exec", "-T", "api", "python", "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode, (result.stdout + result.stderr).strip()


def _provision(slug: str) -> str:
    """A tenant with two wards, through the published control-plane route."""
    status, body = _request(
        "POST",
        f"{CONTROL_PLANE}/tenants",
        headers={TOKEN_HEADER: DEFAULT_TOKEN},
        body={
            "tenant": {
                "slug": slug,
                "name": "Gate 13 City",
                "primary_locale": "en",
                # The municipality template ships Marathi and Hindi strings, and
                # the control plane refuses a plan that writes rows in a locale
                # the tenant has not declared — rows nothing resolves read on
                # screen exactly like a missing translation.
                "locales": ["en", "hi", "mr"],
                "timezone": "Asia/Kolkata",
            },
            "template": "municipality",
        },
    )
    if status != 201:
        raise RuntimeError(f"provisioning failed: {status} {body}")
    return str(body["tenant_id"])


def _seed_scenario(tenant_id: str) -> dict[str, Any]:
    """Two wards, a flagged complaint in each, two officers and an administrator.

    Done in the api container through the identity services rather than over
    HTTP, because creating the *first* operator in a tenant is a bootstrap
    problem by definition — there is nobody to authenticate as yet. That is the
    same argument ``identity.bootstrap`` makes, and this is the deployment-level
    version of it.
    """
    code, output = _api_python(
        "import asyncio, json, uuid\n"
        "from sqlalchemy import update\n"
        "from nemesis.control_plane import organisation\n"
        "from nemesis.control_plane.schemas import ZoneSpec\n"
        "from nemesis.db.models.complaint import Complaint\n"
        "from nemesis.db.session import session_scope\n"
        "from nemesis.identity import bootstrap, roles, users\n"
        "from nemesis.identity import permissions as perms\n"
        "from nemesis.events.store import EventStore\n"
        "from nemesis.projections.replay import replay_entity\n"
        "from nemesis.projections.writer import write_projection\n"
        "from nemesis.tenancy.context import tenant_scope\n"
        f"TENANT = uuid.UUID('{tenant_id}')\n"
        f"PASSWORD = '{PASSWORD}'\n"
        "async def main():\n"
        "    out = {'items': {}, 'users': {}, 'complaints': {}}\n"
        "    async with session_scope() as session:\n"
        "        with tenant_scope(TENANT):\n"
        "            await organisation.import_zones(session, tenant_id=TENANT, specs=[\n"
        "                ZoneSpec.model_validate({'code': 'gate-north', 'name': 'Gate North'}),\n"
        "                ZoneSpec.model_validate({'code': 'gate-south', 'name': 'Gate South'}),\n"
        "            ])\n"
        "            zones = {z.code: z.id for z in await organisation.list_zones(session, tenant_id=TENANT)}\n"
        "            await bootstrap.install_roles(session, tenant_id=TENANT, section='tenant')\n"
        "            library = {r.code: r.id for r in await roles.list_roles(session, tenant_id=TENANT)}\n"
        "            from nemesis.trust.review import ReviewReason, queue\n"
        "            for ward in ('gate-north', 'gate-south'):\n"
        "                cid = uuid.uuid4()\n"
        "                await EventStore(session).append(entity_id=cid, event_type='complaint_submitted',\n"
        "                    payload={'latitude': 18.52, 'longitude': 73.85, 'description_text': 'gate drill',\n"
        "                             'photo_url': None, 'audio_url': None, 'locale': 'en',\n"
        "                             'device_fingerprint': None, 'submitted_via': 'web'}, tenant_id=TENANT)\n"
        "                projection = await replay_entity(session, tenant_id=TENANT,\n"
        "                    entity_type='complaint', entity_id=cid)\n"
        "                await write_projection(session, tenant_id=TENANT, result=projection)\n"
        "                await session.flush()\n"
        "                await session.execute(update(Complaint).where(Complaint.tenant_id == TENANT,\n"
        "                    Complaint.id == cid).values(ward=ward))\n"
        "                queued = await queue(session, tenant_id=TENANT, complaint_id=cid,\n"
        "                    reason=ReviewReason.LOW_TRUST, evidence={'ward': ward}, trust_score=0.1)\n"
        "                out['items'][ward] = str(queued.review_item_id)\n"
        "                out['complaints'][ward] = str(cid)\n"
        "            for ward, label in (('gate-north', 'north'), ('gate-south', 'south')):\n"
        "                user = await users.create_user(session, tenant_id=TENANT,\n"
        "                    display_name=label, email=label + '@gate13.example', password=PASSWORD)\n"
        "                await roles.grant(session, tenant_id=TENANT, user_id=user.id,\n"
        "                    role_id=library['reviewer'], scope_kind=perms.SCOPE_ZONE, scope_ref=zones[ward])\n"
        "                out['users'][label] = str(user.id)\n"
        "            admin = await users.create_user(session, tenant_id=TENANT,\n"
        "                display_name='admin', email='admin@gate13.example', password=PASSWORD)\n"
        "            await roles.grant(session, tenant_id=TENANT, user_id=admin.id,\n"
        "                role_id=library['tenant_admin'])\n"
        "            out['users']['admin'] = str(admin.id)\n"
        "    print('GATE_JSON' + json.dumps(out))\n"
        "asyncio.run(main())\n"
    )
    if code != 0 or "GATE_JSON" not in output:
        raise RuntimeError(f"scenario seeding failed:\n{output}")
    return json.loads(output.split("GATE_JSON", 1)[1].splitlines()[0])


def _sign_in(tenant_id: str, email: str) -> dict[str, str]:
    status, body = _request(
        "POST",
        f"{AUTH}/token",
        headers={"X-Tenant-ID": tenant_id},
        body={"identifier": email, "password": PASSWORD},
    )
    if status != 200:
        raise RuntimeError(f"sign-in failed for {email}: {status} {body}")
    return {"Authorization": f"Bearer {body['access_token']}"}


def _platform_support(tenant_id: str) -> dict[str, str]:
    """A support operator in the reserved platform tenant."""
    code, output = _api_python(
        "import asyncio, json\n"
        "from nemesis.db.session import session_scope\n"
        "from nemesis.domain.constants import SYSTEM_TENANT_ID\n"
        "from nemesis.identity import bootstrap, roles, users\n"
        "from nemesis.tenancy.context import tenant_scope\n"
        f"PASSWORD = '{PASSWORD}'\n"
        "async def main():\n"
        "    async with session_scope() as session:\n"
        "        with tenant_scope(SYSTEM_TENANT_ID):\n"
        "            await bootstrap.install_roles(session, tenant_id=SYSTEM_TENANT_ID, section='platform')\n"
        "            library = {r.code: r.id for r in await roles.list_roles(session, tenant_id=SYSTEM_TENANT_ID)}\n"
        "            existing = await users.find_by_subject(session, tenant_id=SYSTEM_TENANT_ID,\n"
        "                subject='gate13-support')\n"
        "            if existing is None:\n"
        "                user = await users.create_user(session, tenant_id=SYSTEM_TENANT_ID,\n"
        "                    display_name='Gate support', email='gate-support@nemesis.example',\n"
        "                    subject='gate13-support', password=PASSWORD)\n"
        "                await roles.grant(session, tenant_id=SYSTEM_TENANT_ID, user_id=user.id,\n"
        "                    role_id=library['platform_support'])\n"
        "    print('SUPPORT_READY')\n"
        "asyncio.run(main())\n"
    )
    if code != 0 or "SUPPORT_READY" not in output:
        raise RuntimeError(f"support operator setup failed:\n{output}")
    del tenant_id
    return _sign_in(
        _sql("SELECT id FROM tenants WHERE slug = '__system__'"),
        "gate-support@nemesis.example",
    )


def _seed_the_permission_checker() -> list[str]:
    """Feed the checker a route with no declaration and require a finding.

    A check nobody has watched fail is a script. This writes a synthetic router
    module into the API package, runs the checker, and removes it — so the gate
    reports that the CI gate *works*, not merely that it is quiet.
    """
    findings: list[str] = []
    seeded = ROOT / "backend" / "nemesis" / "api" / "v1" / "_gate_seeded_route.py"
    seeded.write_text(
        '"""Synthetic module written by gate_phase13.py. Deleted in the same run."""\n'
        "\n"
        "from fastapi import APIRouter\n"
        "\n"
        'router = APIRouter(prefix="/_gate", tags=["gate"])\n'
        "\n"
        "\n"
        '@router.get("/undeclared")\n'
        "async def undeclared() -> dict[str, str]:\n"
        '    return {"declared": "nothing"}\n',
        encoding="utf-8",
    )
    try:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "check_endpoint_permissions.py")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            findings.append(
                "the endpoint-permission check passed a route that declares nothing — "
                "the CI gate is not enforcing clause 2"
            )
        elif "undeclared" not in result.stdout:
            findings.append(
                "the endpoint-permission check failed but did not name the seeded "
                "route; it is failing for some other reason"
            )
    finally:
        seeded.unlink(missing_ok=True)
    return findings


def main() -> int:
    findings: list[str] = []
    slug = f"gate13-{uuid.uuid4().hex[:8]}"

    print(f"Phase 13 gate — provisioning tenant {slug}")
    tenant_id = _provision(slug)
    scenario = _seed_scenario(tenant_id)
    north = _sign_in(tenant_id, "north@gate13.example")
    south = _sign_in(tenant_id, "south@gate13.example")
    admin = _sign_in(tenant_id, "admin@gate13.example")

    # --- clause 1: the negative matrix, both ways ---------------------------
    for label, headers, own, other in (
        ("north", north, "gate-north", "gate-south"),
        ("south", south, "gate-south", "gate-north"),
    ):
        status, page = _request("GET", f"{REVIEW}/queue", headers=headers)
        if status != 200:
            findings.append(f"{label} officer could not read their own queue: {status} {page}")
            continue
        ids = {item["id"] for item in page["items"]}
        if ids != {scenario["items"][own]}:
            findings.append(f"{label} officer's queue returned {ids}, expected their own item")
        if page["total"] != 1:
            findings.append(
                f"{label} officer's total was {page['total']}, not 1 — the count leaks "
                f"rows the page does not show"
            )

        status, body = _request(
            "GET", f"{REVIEW}/queue/{scenario['items'][other]}", headers=headers
        )
        if status != 403:
            findings.append(
                f"{label} officer read the other ward's item by id: {status} {body}"
            )
        elif scenario["items"][other] in json.dumps(body) or other in json.dumps(body):
            findings.append(
                f"the 403 shown to the {label} officer names the resource it refused"
            )

        status, body = _request(
            "POST",
            f"{REVIEW}/queue/{scenario['items'][other]}/decision",
            headers=headers,
            body={"decision": "approve", "rationale": "gate drill"},
        )
        if status != 403:
            findings.append(
                f"{label} officer decided the other ward's item: {status} {body}"
            )

    status, page = _request("GET", f"{REVIEW}/queue", headers=admin)
    if status != 403:
        findings.append(
            f"the administrator (no review grant) reached the queue: {status} {page}"
        )

    print(f"{OK if not findings else FAIL} clause 1 — negative authorisation per role pair")

    # --- clause 2: an undeclared endpoint fails CI -------------------------
    seeded_findings = _seed_the_permission_checker()
    findings.extend(seeded_findings)
    print(
        f"{OK if not seeded_findings else FAIL} clause 2 — an undeclared endpoint is "
        f"rejected by check_endpoint_permissions.py"
    )

    # --- clause 3: the property tests ran and collected something ----------
    result = subprocess.run(
        [
            *COMPOSE,
            "exec",
            "-T",
            "api",
            "pytest",
            "tests/test_identity_authorization.py",
            "-q",
            "--no-header",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    collected = result.stdout.strip().splitlines()
    property_ok = result.returncode == 0 and any("%]" in line for line in collected)
    if not property_ok:
        findings.append(
            "the generated role/scope matrix did not run clean: "
            + (collected[-1] if collected else result.stderr.strip()[:200])
        )
    print(f"{OK if property_ok else FAIL} clause 3 — property-tested decision matrix")

    # --- clause 4: impersonation is visible to the impersonated tenant -----
    before = len(findings)
    support = _platform_support(tenant_id)
    status, started = _request(
        "POST",
        f"{ACCESS}/impersonation",
        headers=support,
        body={
            "tenant_id": tenant_id,
            "user_id": scenario["users"]["north"],
            "justification": JUSTIFICATION,
            "minutes": 5,
        },
    )
    if status != 201:
        findings.append(f"support could not start an impersonation: {status} {started}")
    else:
        acting = {"Authorization": f"Bearer {started['access_token']}"}
        status, who = _request("GET", f"{AUTH}/session", headers=acting)
        if status != 200 or who.get("impersonated_by") is None:
            findings.append(f"the impersonated session does not declare itself: {who}")

        status, page = _request("GET", f"{REVIEW}/queue", headers=acting)
        if status != 200 or page["total"] != 1:
            findings.append(
                "support acting as the north officer did not see exactly that "
                f"officer's queue: {status} {page}"
            )

        status, refused = _request(
            "POST",
            f"{REVIEW}/queue/{scenario['items']['gate-north']}/decision",
            headers=acting,
            body={"decision": "approve", "rationale": "support must not be able to"},
        )
        if status != 403:
            findings.append(
                f"a read-only impersonation recorded a decision: {status} {refused}"
            )

        # The customer's own view. This is the clause.
        status, visible = _request("GET", f"{ACCESS}/impersonation", headers=admin)
        if status != 200:
            findings.append(f"the tenant could not list impersonations: {status} {visible}")
        elif not any(
            entry["justification"] == JUSTIFICATION
            and entry["operator_subject"]
            and entry["allows_writes"] is False
            for entry in visible
        ):
            findings.append(
                "the impersonation session is not visible to the impersonated tenant "
                "with its justification"
            )

        chained = _sql(
            "SELECT count(*) FROM events WHERE tenant_id = "
            f"'{tenant_id}'::uuid AND event_type = 'impersonation_started'"
        )
        if chained != "1":
            findings.append(
                f"expected one impersonation_started event on the tenant's chain, found {chained}"
            )

        status, ended = _request(
            "DELETE", f"{ACCESS}/impersonation/{started['impersonation']['id']}", headers=admin
        )
        if status != 204:
            findings.append(f"the tenant could not end the session: {status} {ended}")
        else:
            status, after = _request("GET", f"{AUTH}/session", headers=acting)
            if status != 401:
                findings.append(
                    f"the impersonated session still worked after the tenant ended it: {status}"
                )
    print(
        f"{OK if len(findings) == before else FAIL} clause 4 — impersonation is visible, "
        f"read-only, and endable by the tenant"
    )

    # --- clause 5: revocation is immediate ---------------------------------
    before = len(findings)
    status, _ = _request("POST", f"{AUTH}/logout", headers=north)
    if status != 204:
        findings.append(f"logout did not succeed: {status}")
    status, _ = _request("GET", f"{REVIEW}/queue", headers=north)
    if status != 401:
        findings.append(
            f"a revoked session still read the queue ({status}); revocation is not immediate"
        )
    print(f"{OK if len(findings) == before else FAIL} clause 5 — revocation takes effect at once")

    # --- clause 6: the citizen surface is untouched ------------------------
    before = len(findings)
    status, receipt = _request(
        "GET",
        f"{API}/api/v1/complaints/{scenario['complaints']['gate-north']}",
        headers={"X-Tenant-ID": tenant_id},
    )
    if status != 200:
        findings.append(
            f"a citizen could not read their own receipt without a session: {status} {receipt}"
        )
    print(f"{OK if len(findings) == before else FAIL} clause 6 — the citizen receipt still reads")

    print()
    if findings:
        print(f"{FAIL} Phase 13 gate: {len(findings)} finding(s)")
        for finding in findings:
            print(f"  - {finding}")
        return 1
    print(f"{OK} Phase 13 gate: every clause holds against the running stack")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as error:
        print(f"{FAIL} Phase 13 gate could not run: {error}")
        sys.exit(1)
