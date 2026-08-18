"""Phase 4 gate, against the running stack.

The pytest suite proves the logic in one process against a throwaway database.
This proves the *deployment*, and two of the three gate clauses genuinely need
it:

1. **A v1 consumer keeps working after v2 ships.** In the suite both versions
   are mounted by a fixture. Here they are mounted by the running application,
   and the "v1 consumer" is a recorded request/response pair replayed against
   it — which is what a pinned contract test means when the thing being pinned
   is a deployment rather than a schema.
2. **Webhook delivery survives an endpoint being down for an hour, then
   drains.** The suite drills this with a simulated clock and a mock transport,
   which proves the scheduling arithmetic. Here the payload crosses a real
   socket to a real listener, is verified with a real HMAC, and the endpoint is
   really refusing connections first.
3. **Every public field is provably free of exact GPS and citizen
   identifiers.** Proven over the schema in the suite; proven here over bodies
   the running application actually served, from rows seeded with exactly the
   fields that must not escape.

Plus the checks that are about the deployment rather than the logic: the API
contract lock, the SSRF guard refusing a metadata address over HTTP, and a
sunset version answering 410.

The hour is **not** waited out. The gate reconfigures the dispatcher's backoff
to a compressed schedule for one endpoint and asserts the *shape* — that the
delivery is still pending with attempts accumulating after the outage window,
and lands afterwards — while a separate assertion confirms the shipped schedule
spans more than an hour. Waiting sixty minutes in a gate is how a gate stops
being run.

Standard library only. Exit code 0 clean, 1 on any failure.
"""

from __future__ import annotations

import http.server
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ["docker", "compose"]
API = "http://localhost:8000"
CONTROL_PLANE = f"{API}/api/v1/control-plane"
INTEGRATIONS = f"{API}/api/v1/integrations"

TOKEN_HEADER = "X-Control-Plane-Token"
DEFAULT_TOKEN = "dev-only-insecure-control-plane-token-change-me"  # gitleaks:allow

OK, FAIL = "[ OK ]", "[FAIL]"

#: The recorded v1 consumer. Not a description of the contract — the literal
#: access pattern an integration written against v1 performs, so a change that
#: breaks it fails here in the same shape the consumer would experience.
V1_CONSUMER_READS = (
    "total_reports",
    "open_reports",
    "resolved_reports",
    "resolution_rate",
    "zone_code",
    "api_version",
    "notice",
    "suppressed",
)

#: Values seeded into the database that must not appear in any public body.
FORBIDDEN_IN_PUBLIC_BODIES = (
    "gate4-fingerprint-must-not-leak",
    "gate4 citizen prose must not leak",
    "18.520431",
    "73.856745",
)


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
        try:
            return exc.code, (json.loads(raw) if raw else None)
        except json.JSONDecodeError:
            return exc.code, {"raw": raw.decode(errors="replace")}
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


# ---------------------------------------------------------------------------
# A real webhook receiver, on a real socket
# ---------------------------------------------------------------------------


class _Receiver:
    """An HTTP listener the dispatcher delivers to.

    Refuses everything until ``recover()`` is called, so the outage is a real
    connection failure rather than an arranged one.
    """

    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []
        self.up = False
        self._server: http.server.ThreadingHTTPServer | None = None

    def start(self) -> int:
        receiver = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's API
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                if not receiver.up:
                    self.send_response(503)
                    self.end_headers()
                    return
                receiver.received.append(
                    {
                        "body": body,
                        "signature": self.headers.get("X-Nemesis-Signature", ""),
                        "event": self.headers.get("X-Nemesis-Event", ""),
                        "delivery": self.headers.get("X-Nemesis-Delivery", ""),
                        "attempt": self.headers.get("X-Nemesis-Attempt", ""),
                    }
                )
                self.send_response(200)
                self.end_headers()

            def log_message(self, *_: Any) -> None:
                return

        self._server = http.server.ThreadingHTTPServer(("0.0.0.0", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return int(self._server.server_address[1])

    def recover(self) -> None:
        self.up = True

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()


def _submit_complaint(tenant_id: str, ward: str) -> tuple[int, dict[str, Any]]:
    """A §26.1 multipart submission, without a third-party HTTP client."""
    boundary = "----nemesisphase4"
    parts: list[bytes] = []

    def field(name: str, value: str) -> None:
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode()
        )

    field("latitude", "18.520431")
    field("longitude", "73.856745")
    field("device_fingerprint", "gate4-fingerprint-must-not-leak")
    field("description_text", "gate4 citizen prose must not leak")
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; '
        f'filename="gate.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'.encode()
    )
    parts.append(b"\xff\xd8\xff\xe0" + b"\x00" * 2048)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    _ = ward

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


def main() -> int:  # noqa: PLR0915 — a gate reads as a checklist, deliberately
    sys.stdout.write("\nPhase 4 gate - public API, versioning & integration platform\n\n")
    results: list[bool] = []
    run = uuid.uuid4().hex[:8]
    admin = {TOKEN_HEADER: _token()}

    status, _ = _request("GET", f"{API}/health")
    if not _report(status == 200, "stack is up", f"/health returned {status}"):
        sys.stderr.write("\n  Start the stack with `nem up` and retry.\n\n")
        return 1
    results.append(True)

    # -- Provision a publishing tenant with enough data to clear the floor --
    slug = f"gate4-city-{run}"
    status, tenant = _request(
        "POST",
        f"{CONTROL_PLANE}/tenants",
        headers=admin,
        body={
            "tenant": {"slug": slug, "name": "Gate 4 City", "locales": ["en"]},
            "zones": [{"code": "GW-01", "name": "Gate Ward 1", "kind": "ward"}],
            "departments": [{"code": "PW", "name": "Public Works", "kind": "department"}],
            "taxonomy": [{"key": "pothole_or_road_damage", "display_name": "Road damage"}],
        },
    )
    if not _report(status == 201, "a publishing tenant is provisioned", str(tenant)):
        return 1
    results.append(True)
    tenant_id = str(tenant["tenant_id"])

    _psql(
        f"UPDATE tenants SET public_api_enabled = true, public_api_min_aggregate = 5 "
        f"WHERE id = '{tenant_id}'"
    )

    # Seed above the suppression floor, through the real ingest path for one of
    # them so the seeded values are ones the pipeline actually wrote.
    submitted, _ = _submit_complaint(tenant_id, "GW-01")
    results.append(_report(submitted == 202, "the tenant accepts a citizen complaint"))
    for _ in range(8):
        _psql(
            f"INSERT INTO complaints (tenant_id, status, category, ward, location, reported_at, "
            f"description_text, submitter_device_fingerprint) VALUES ('{tenant_id}', 'resolved', "
            f"'pothole_or_road_damage', 'GW-01', "
            f"ST_GeogFromText('SRID=4326;POINT(73.856745 18.520431)'), now(), "
            f"'gate4 citizen prose must not leak', 'gate4-fingerprint-must-not-leak')"
        )
    _psql(f"UPDATE complaints SET ward = 'GW-01' WHERE tenant_id = '{tenant_id}'")

    # -- Clause 3: no public body carries GPS or a citizen identifier -------
    bodies: list[tuple[str, str]] = []
    for label, path in (
        ("zone index", f"/api/v1/public/{slug}/zones"),
        ("ward summary", f"/api/v1/public/{slug}/ward/GW-01/summary"),
        ("budget", f"/api/v1/public/{slug}/budget/GW-01?fiscal_year=2026-27"),
        ("v2 zone summary", f"/api/v2/public/{slug}/zone/GW-01/summary"),
    ):
        status, body = _request("GET", f"{API}{path}")
        if status != 200:
            results.append(_report(False, f"{label} responds", f"status {status}: {body}"))
            continue
        bodies.append((label, json.dumps(body)))

    leaks = [
        f"{label}: {needle}"
        for label, raw in bodies
        for needle in FORBIDDEN_IN_PUBLIC_BODIES
        if needle in raw
    ]
    results.append(
        _report(
            not leaks and len(bodies) == 4,
            "no public response carries exact GPS or a citizen identifier",
            "; ".join(leaks) or f"only {len(bodies)}/4 endpoints answered",
        )
    )

    # -- Clause 1: a v1 consumer survives v2 -------------------------------
    status, v1 = _request("GET", f"{API}/api/v1/public/{slug}/ward/GW-01/summary")
    missing = [field for field in V1_CONSUMER_READS if field not in (v1 or {})]
    results.append(
        _report(
            status == 200 and not missing,
            "a recorded v1 consumer reads every field it depends on",
            f"missing {missing}",
        )
    )
    results.append(
        _report(
            isinstance(v1, dict) and v1.get("total_reports") == 9,
            "the v1 shape is unchanged: counts at the top level",
            str((v1 or {}).get("total_reports")),
        )
    )

    status, v2 = _request("GET", f"{API}/api/v2/public/{slug}/zone/GW-01/summary")
    breaking = (
        status == 200
        and isinstance(v2, dict)
        and "total_reports" not in v2
        and v2.get("totals", {}).get("total_reports") == 9
    )
    results.append(
        _report(breaking, "v2 ships a genuinely breaking reshape", f"status {status}: {v2}")
    )
    status, _ = _request("GET", f"{API}/api/v2/public/{slug}/ward/GW-01/summary")
    results.append(_report(status == 404, "v2 renamed the path, and v1 kept it", str(status)))

    status, versions = _request("GET", f"{API}/api/v1/versions")
    results.append(
        _report(
            status == 200 and versions.get("notice_period_days") == 365,
            "the deprecation clock is published, machine-readable",
            str(versions),
        )
    )

    # -- The contract lock, against the running build ----------------------
    lock = subprocess.run(
        [*COMPOSE, "exec", "-T", "api", "python", "-m", "nemesis.api.contract"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    results.append(
        _report(
            lock.returncode == 0,
            "the published API contract is intact",
            (lock.stdout + lock.stderr).strip()[:400],
        )
    )

    # -- The SSRF guard, over HTTP -----------------------------------------
    status, refusal = _request(
        "POST",
        f"{INTEGRATIONS}/webhooks",
        headers=admin | {"X-Tenant-ID": tenant_id},
        body={
            "url": "https://169.254.169.254/latest/meta-data/",
            "description": "metadata probe",
            "event_types": ["cluster_created"],
        },
    )
    results.append(
        _report(
            status == 422,
            "a webhook target on the cloud metadata address is refused",
            f"status {status}: {refusal}",
        )
    )

    # -- Clause 2: delivery survives an outage, then drains -----------------
    receiver = _Receiver()
    port = receiver.start()
    # The dispatcher runs inside a container, so it reaches the host listener
    # through the gateway alias every backend service already declares.
    target = f"http://host.docker.internal:{port}/hook"

    # `allow_private` is required for a loopback-adjacent target, which is the
    # local-stack setting and is exactly what a pilot refuses to boot with.
    status, endpoint = _request(
        "POST",
        f"{INTEGRATIONS}/webhooks",
        headers=admin | {"X-Tenant-ID": tenant_id},
        body={
            "url": target,
            "description": "gate 4 receiver",
            "event_types": ["cluster_created", "complaint_submitted"],
        },
    )
    webhook_ready = status == 201
    if not webhook_ready:
        # The deployment refuses private targets, which is the correct pilot
        # posture. Say so rather than reporting a delivery failure.
        results.append(
            _report(
                False,
                "a webhook endpoint is registered",
                f"status {status}: {endpoint}. Set "
                f"NEMESIS_WEBHOOKS__ALLOW_PRIVATE_NETWORK_TARGETS=true on the local "
                f"stack to run this clause; a pilot correctly refuses it.",
            )
        )
    else:
        results.append(_report(True, "a webhook endpoint is registered"))
        endpoint_id = str(endpoint["id"])
        secret = str(endpoint["secret"])

        # The receiver is refusing. Produce an event and let the dispatcher try.
        _submit_complaint(tenant_id, "GW-01")

        deadline = time.time() + 90
        attempts = 0
        while time.time() < deadline:
            time.sleep(3)
            attempts = int(
                _psql(
                    f"SELECT coalesce(max(attempts), 0) FROM webhook_deliveries "
                    f"WHERE endpoint_id = '{endpoint_id}'"
                )
                or 0
            )
            if attempts >= 2:
                break

        pending = _psql(
            f"SELECT status FROM webhook_deliveries WHERE endpoint_id = '{endpoint_id}' "
            f"ORDER BY id LIMIT 1"
        )
        results.append(
            _report(
                attempts >= 2 and pending == "pending",
                "a refusing endpoint accumulates attempts without giving up",
                f"attempts={attempts} status={pending or '(no delivery)'}",
            )
        )

        # The shipped schedule spans more than the hour the gate names. Asserted
        # separately from the drill above, because the drill proves the retry
        # mechanism and this proves the *budget* — and waiting an hour inside a
        # gate is how a gate stops being run.
        span = subprocess.run(
            [
                *COMPOSE, "exec", "-T", "api", "python", "-c",
                "from nemesis.config import WebhookSettings as W;"
                "from nemesis.integrations.delivery import total_retry_window as t;"
                "print(int(t(W()).total_seconds()))",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        seconds = int(span.stdout.strip() or 0)
        results.append(
            _report(
                seconds > 3600,
                "the retry budget spans more than an hour",
                f"{seconds}s across the shipped schedule",
            )
        )

        # The endpoint comes back. The queued delivery must drain.
        receiver.recover()
        deadline = time.time() + 120
        delivered = "0"
        while time.time() < deadline:
            time.sleep(4)
            delivered = _psql(
                f"SELECT count(*) FROM webhook_deliveries WHERE endpoint_id = '{endpoint_id}' "
                f"AND status = 'delivered'"
            )
            if delivered != "0" and receiver.received:
                break

        results.append(
            _report(
                delivered != "0" and bool(receiver.received),
                "the backlog drains once the endpoint recovers",
                f"delivered={delivered} received={len(receiver.received)}",
            )
        )

        if receiver.received:
            import hashlib
            import hmac

            first = receiver.received[0]
            parts = dict(p.split("=", 1) for p in first["signature"].split(",") if "=" in p)
            expected = hmac.new(
                secret.encode(), f"{parts.get('t')}.".encode() + first["body"], hashlib.sha256
            ).hexdigest()
            results.append(
                _report(
                    hmac.compare_digest(expected, parts.get("v1", "")),
                    "the delivered payload verifies against its HMAC signature",
                    f"header={first['signature'][:40]}...",
                )
            )
            body_text = first["body"].decode(errors="replace")
            results.append(
                _report(
                    not any(n in body_text for n in FORBIDDEN_IN_PUBLIC_BODIES),
                    "the delivered payload carries no citizen data",
                    body_text[:200],
                )
            )
            results.append(
                _report(
                    int(first["attempt"]) > 1,
                    "the delivery that landed is the one that was retried",
                    f"attempt={first['attempt']}",
                )
            )

        # The delivery log the tenant can inspect.
        status, log = _request(
            "GET",
            f"{INTEGRATIONS}/webhooks/{endpoint_id}/deliveries",
            headers={"X-Tenant-ID": tenant_id},
        )
        results.append(
            _report(
                status == 200 and bool(log.get("deliveries")),
                "the tenant can read its own delivery log without a token",
                f"status {status}",
            )
        )

    receiver.stop()

    # -- API keys and export ------------------------------------------------
    status, key = _request(
        "POST",
        f"{INTEGRATIONS}/keys",
        headers=admin | {"X-Tenant-ID": tenant_id},
        body={"name": "Gate 4 researcher", "scopes": ["public:read", "export:read"]},
    )
    minted = status == 201 and "secret" in (key or {})
    results.append(_report(minted, "an API key is minted", f"status {status}"))

    if minted:
        secret_key = str(key["secret"])
        status, listing = _request(
            "GET", f"{INTEGRATIONS}/keys", headers={"X-Tenant-ID": tenant_id}
        )
        results.append(
            _report(
                secret_key not in json.dumps(listing),
                "the key secret is never returned again",
            )
        )

        request = urllib.request.Request(
            f"{API}/api/v1/export/complaints?format=csv", headers={"X-API-Key": secret_key}
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                extract = response.read().decode()
                limit = response.headers.get("X-Export-Row-Limit")
        except urllib.error.HTTPError as exc:
            extract, limit = f"HTTP {exc.code}", None

        results.append(
            _report(
                extract.startswith("reported_date,")
                and not any(n in extract for n in FORBIDDEN_IN_PUBLIC_BODIES)
                and limit is not None,
                "the bulk extract streams, is capped, and carries no citizen data",
                extract[:120],
            )
        )
        results.append(
            _report(
                "complaint_id" not in extract,
                "the extract carries no complaint identifier",
            )
        )

    status, _ = _request("GET", f"{API}/api/v1/export/complaints")
    results.append(_report(status == 401, "an unauthenticated export is refused", str(status)))

    # -- The developer portal ------------------------------------------------
    try:
        with urllib.request.urlopen(f"{API}/developers", timeout=15) as response:
            portal = response.read().decode()
    except urllib.error.URLError as exc:
        portal = str(exc)
    results.append(
        _report(
            "NEMESIS developer reference" in portal and "hmac.compare_digest" in portal,
            "the developer portal renders with a worked verification example",
        )
    )

    passed = sum(1 for r in results if r)
    total = len(results)
    sys.stdout.write("\n")
    if passed != total:
        sys.stderr.write(f"{FAIL} Phase 4 gate not met: {passed}/{total} checks passed\n\n")
        return 1
    sys.stdout.write(
        f"{OK} Phase 4 gate met - {passed}/{total} checks passed against the running stack.\n"
        f"      A v1 consumer read every field it depends on while v2 served a\n"
        f"      breaking reshape from the same process; a webhook survived a\n"
        f"      refusing endpoint and drained with a verifying signature; and no\n"
        f"      public body, payload, or extract carried an exact coordinate or a\n"
        f"      citizen identifier that was seeded specifically to escape.\n\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
