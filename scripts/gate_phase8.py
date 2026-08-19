"""Phase 8 gate, against the running stack.

The pytest suite proves the logic in one process with a deterministic detector.
This proves the *deployment*, and for this phase the gap between the two is
unusually wide: three of the four gate clauses are claims about containers.

The four clauses, executed in order:

1. **The safety bypass provably fires before any scoring stage.** A report
   naming a hazard is submitted over HTTP and the real pipeline runs it. The
   claim is not "the safety stage is first in a list" — it is that the successor
   stages were *never enqueued*, which is checked by asserting the complaint's
   own chain contains ``safety_trigger_fired`` and contains no classification,
   no severity score, and no trust-verification event at all.
2. **No code path can persist an unblurred image.** A photograph containing a
   face is submitted, ``worker-ml`` redacts it with the real MediaPipe detector,
   and three things are then true together: the event says a face was found and
   blurred, the served bytes carry no EXIF, and the quarantine content address —
   which the gate knows, because it computed it — is not fetchable over HTTP.
3. **Safety-queue latency is unaffected by a saturated ml queue.** The ml queue
   is filled with real image work, and a hazard report is submitted *while it is
   draining*. The measurement is how long the danger signal took to reach
   ``FLAGGED`` with that backlog in front of it.
4. **A tenant with custom safety keywords gets correct behaviour with no code
   change.** A ruleset naming a hazard that appears nowhere in this repository
   is activated over HTTP, and a report using that word is bypassed.

Two further checks about the deployment rather than the logic:

5. Every trust decision is **on a verifiable hash chain**, through the same
   ``verify_chain`` the earlier gates use.
6. **No deploy**, measured the way Phases 6 and 7 measure it: the API
   container's id and ``State.StartedAt`` are compared across the whole run.

Standard library only. Exit code 0 clean, 1 on any failure.
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import zlib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ["docker", "compose"]
API = "http://localhost:8000"
CONTROL_PLANE = f"{API}/api/v1/control-plane"
POLICIES = f"{CONTROL_PLANE}/policies"
REVIEW = f"{API}/api/v1/review"

TOKEN_HEADER = "X-Control-Plane-Token"
TENANT_HEADER = "X-Tenant-ID"
DEFAULT_TOKEN = "dev-only-insecure-control-plane-token-change-me"

OK, FAIL = "[ OK ]", "[FAIL]"

#: How long to wait for the pipeline to reach a terminal state for one report.
#: Generous: §27.1 budgets the whole pipeline at thirty seconds and the ml
#: worker runs at concurrency 1, so a backlog is expected during clause 3.
PIPELINE_TIMEOUT_SECONDS = 180

#: Reports pushed onto the ml queue before the danger signal in clause 3. Each
#: carries an image, so each costs a decode plus an inference on a
#: single-concurrency worker — which is the point.
SATURATION_REPORTS = 25

#: The budget the danger signal has to meet with that backlog in front of it.
#: Deliberately far below what the backlog itself takes to drain: the claim is
#: not "fast", it is "unaffected", and a threshold close to the backlog's own
#: drain time would pass whether or not the queues were separate.
SAFETY_BUDGET_SECONDS = 30.0

#: A hazard word that appears nowhere else in this repository. Clause 4's whole
#: point is that no code knows it — the gate greps for it to prove that.
INVENTED_HAZARD = "chlorine trifluoride"


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
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, (json.loads(raw) if raw else None)
    except urllib.error.URLError as exc:
        return 0, {"error": str(exc)}


def _raw_get(url: str, headers: dict[str, str]) -> tuple[int, bytes, dict[str, str]]:
    """A GET that returns bytes, for the one route that serves an image."""
    request = urllib.request.Request(url, method="GET")
    for key, value in headers.items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)
    except urllib.error.URLError:
        return 0, b"", {}


# ---------------------------------------------------------------------------
# Submission — multipart, hand-rolled, standard library only
# ---------------------------------------------------------------------------


def _multipart(fields: dict[str, str], photo: bytes | None) -> tuple[bytes, str]:
    """A ``multipart/form-data`` body. Written out rather than pulled from a
    library because this script runs on a bare interpreter, like every other
    gate here."""
    boundary = f"----nemesis{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
            f"{value}\r\n".encode()
        )
    if photo is not None:
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; '
            f'filename="report.png"\r\nContent-Type: image/png\r\n\r\n'.encode()
        )
        parts.append(photo)
        parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _submit(
    tenant_id: str,
    *,
    text: str,
    photo: bytes | None = None,
    device: str = "gate-device",
    latitude: float = 18.5204,
    longitude: float = 73.8567,
) -> tuple[int, Any]:
    body, content_type = _multipart(
        {
            "latitude": str(latitude),
            "longitude": str(longitude),
            "device_fingerprint": device,
            "description_text": text,
            "locale": "en",
        },
        photo,
    )
    request = urllib.request.Request(f"{API}/api/v1/complaints", data=body, method="POST")
    request.add_header("Content-Type", content_type)
    request.add_header(TENANT_HEADER, tenant_id)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, (json.loads(raw) if raw else None)
    except urllib.error.URLError as exc:
        return 0, {"error": str(exc)}


# ---------------------------------------------------------------------------
# A PNG with a face in it, built from bytes
# ---------------------------------------------------------------------------


def _png(pixels: list[list[tuple[int, int, int]]]) -> bytes:
    """Encode RGB rows as a PNG. No Pillow: this script has no dependencies.

    Written out because the gate has to hand the API a real image file, and the
    alternative — a checked-in binary — is a fixture nobody can read the intent
    of, for the one input whose *content* is the thing under test.
    """
    height, width = len(pixels), len(pixels[0])
    raw = b"".join(
        b"\x00" + b"".join(struct.pack("BBB", *pixel) for pixel in row) for row in pixels
    )

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


def _face_image(seed: int = 0) -> bytes:
    """A crude frontal face on a background, at a size BlazeFace can work with.

    Not a photograph, and it does not need to be: what clause 2 is testing is
    that the real detector runs in the real container and that whatever it finds
    is blurred and stripped before anything can serve it. ``seed`` shifts the
    background so saturation reports do not deduplicate to one stored file —
    the media store is content-addressed, and identical bytes would be one write
    and one decode rather than the twenty-five the clause needs.
    """
    size = 200
    cx, cy = size // 2, size // 2 - 5
    pixels: list[list[tuple[int, int, int]]] = []
    for y in range(size):
        row: list[tuple[int, int, int]] = []
        for x in range(size):
            dx, dy = (x - cx) / 48.0, (y - cy) / 62.0
            if dx * dx + dy * dy <= 1.0:
                pixel = (233, 190, 158)
                for ex in (cx - 20, cx + 20):
                    if (x - ex) ** 2 + (y - (cy - 12)) ** 2 <= 64:
                        pixel = (32, 28, 26)
                if abs(x - cx) < 16 and abs(y - (cy + 34)) < 5:
                    pixel = (150, 62, 58)
                if abs(x - cx) < 5 and abs(y - (cy + 10)) < 14:
                    pixel = (206, 162, 132)
            else:
                pixel = ((90 + seed * 3) % 256, (140 + x // 3) % 256, (180 + y // 3) % 256)
            row.append(pixel)
        pixels.append(row)
    return _png(pixels)


# ---------------------------------------------------------------------------
# Stack helpers
# ---------------------------------------------------------------------------


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
    result = subprocess.run(
        [*COMPOSE, "ps", "-q", "api"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    lines = result.stdout.strip().splitlines()
    if not lines:
        return ""
    identifier = lines[0].strip()
    inspected = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.StartedAt}}", identifier],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return f"{identifier[:12]}@{inspected.stdout.strip()}"


def _trailing_json(output: str) -> dict[str, Any]:
    lines = output.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("{"):
            try:
                return dict(json.loads("\n".join(lines[index:])))
            except json.JSONDecodeError:
                continue
    return {"error": output.strip()[-400:]}


def _provision(slug: str) -> dict[str, Any]:
    result = subprocess.run(
        [*COMPOSE, "exec", "-T", "api", "python", "-m", "nemesis.sandbox", slug, "--complaints", "0"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return {"error": result.stderr.strip()[-400:]}
    return _trailing_json(result.stdout)


def _events(tenant_id: str, complaint_id: str) -> list[str]:
    raw = _psql(
        f"SELECT string_agg(event_type, ',' ORDER BY sequence) FROM events "
        f"WHERE tenant_id = '{tenant_id}' AND entity_type = 'complaint' "
        f"AND entity_id = '{complaint_id}'"
    )
    return [value for value in raw.split(",") if value]


def _await_events(
    tenant_id: str, complaint_id: str, *, until: str, timeout: float = PIPELINE_TIMEOUT_SECONDS
) -> tuple[list[str], float]:
    """Poll the chain until ``until`` appears. Returns the chain and the wait."""
    started = time.monotonic()
    deadline = started + timeout
    events: list[str] = []
    while time.monotonic() < deadline:
        events = _events(tenant_id, complaint_id)
        if until in events:
            return events, time.monotonic() - started
        time.sleep(0.25)
    return events, time.monotonic() - started


def _safety_ruleset() -> dict[str, Any]:
    return {
        "rules": [
            {
                "rule_id": "exotic_oxidiser",
                "display_name": "Exotic oxidiser release",
                "rationale": (
                    "Sets fire to materials that are already fire-resistant, and to "
                    "the concrete under them. Evacuate, do not attempt to contain."
                ),
                "terms": [INVENTED_HAZARD, "interhalogen"],
                "match_mode": "substring",
                "severity_floor": 10.0,
            }
        ]
    }


def _activate_ruleset(tenant_id: str, body: dict[str, Any]) -> tuple[bool, str]:
    headers = {TENANT_HEADER: tenant_id, TOKEN_HEADER: _token()}
    status, drafted = _request(
        "POST",
        f"{POLICIES}/safety_ruleset",
        body={"body": body, "change_reason": "Gate: an industrial tenant's own hazards"},
        headers=headers,
    )
    if status != 201 or not isinstance(drafted, dict):
        return False, f"draft failed: {status} {drafted}"
    revision = drafted["revision"]
    for step, reason in (("submit", "review"), ("approve", "approved"), ("activate", "live")):
        status, payload = _request(
            "POST",
            f"{POLICIES}/safety_ruleset/{revision}/{step}",
            body={"reason": reason},
            headers=headers,
        )
        if status not in (200, 201):
            return False, f"{step} failed: {status} {payload}"
    return True, f"revision {revision} active"


def main() -> int:
    sys.stdout.write("\nPhase 8 gate - trust & safety spine, against the running stack\n\n")
    results: list[bool] = []
    started_before = _api_identity()

    slug = f"gate8-{uuid.uuid4().hex[:8]}"
    provisioned = _provision(slug)
    tenant_id = str(provisioned.get("tenant_id", ""))
    if not tenant_id:
        _report(False, "provision a tenant", str(provisioned)[:300])
        return 1
    _report(True, f"provisioned tenant {slug}", tenant_id)
    headers = {TENANT_HEADER: tenant_id}

    # -- 1. Clause 1: the safety bypass fires before any scoring stage -----
    sys.stdout.write("\n  Clause 1 - the safety bypass fires before any scoring stage\n")
    # **With a photograph attached, deliberately.** §26.1 requires media on every
    # submission, and it makes the clause stronger: the report carries an image
    # the trust stage would have redacted, so "no trust stage ran" is a claim
    # about work that was available and never dispatched.
    status, receipt = _submit(
        tenant_id,
        text="there is a gas leak on the corner and it smells very strong",
        photo=_face_image(seed=101),
    )
    danger_id = str(receipt.get("complaint_id", "")) if isinstance(receipt, dict) else ""
    results.append(_report(status == 202 and bool(danger_id), "hazard report accepted", str(status)))

    events, waited = _await_events(tenant_id, danger_id, until="safety_trigger_fired")
    results.append(
        _report(
            "safety_trigger_fired" in events,
            "the §11.2 fail-safe fired",
            f"chain={events} after {waited:.1f}s",
        )
    )
    # The clause, stated as what did *not* happen. A stage that ran and declined
    # to act would look identical from the status alone.
    scoring = {"classification_scored", "severity_scored", "exif_check_completed", "media_redacted"}
    results.append(
        _report(
            not (scoring & set(events)),
            "no scoring or trust stage ran at all - the pipeline was bypassed, not filtered",
            f"chain={events}",
        )
    )
    status_now = _psql(
        f"SELECT status || '/' || is_safety_flagged FROM complaints "
        f"WHERE tenant_id = '{tenant_id}' AND id = '{danger_id}'"
    )
    results.append(
        _report(
            status_now == "flagged/true",
            "the projection shows it flagged and out of the work list",
            status_now,
        )
    )
    queued = _psql(
        f"SELECT reason || '/' || priority FROM review_queue_items "
        f"WHERE tenant_id = '{tenant_id}' AND complaint_id = '{danger_id}'"
    )
    results.append(
        _report(queued == "safety_trigger/0", "§11.4: the flag reached the queue, at top priority", queued)
    )

    # -- 2. Clause 2: no code path can persist an unblurred image ----------
    sys.stdout.write("\n  Clause 2 - no code path can persist or serve an unblurred image\n")
    photo = _face_image()
    source_sha = hashlib.sha256(photo).hexdigest()
    status, receipt = _submit(
        tenant_id, text="broken kerb outside the school gate", photo=photo, device="gate-photo"
    )
    photo_id = str(receipt.get("complaint_id", "")) if isinstance(receipt, dict) else ""
    results.append(_report(status == 202 and bool(photo_id), "photo report accepted", str(status)))

    events, waited = _await_events(tenant_id, photo_id, until="media_redacted")
    results.append(
        _report("media_redacted" in events, "worker-ml redacted the image", f"after {waited:.1f}s")
    )

    row = _psql(
        f"SELECT detector_id || '|' || faces_detected || '|' || faces_blurred || '|' || "
        f"coalesce(redacted_sha256, 'none') || '|' || quarantine_sha256 "
        f"FROM submission_media WHERE tenant_id = '{tenant_id}' AND complaint_id = '{photo_id}'"
    )
    parts = row.split("|")
    detector = parts[0] if parts else ""
    results.append(
        _report(
            detector.startswith("mediapipe:"),
            "the real MediaPipe detector ran, and is named in the record",
            detector or "(no row)",
        )
    )
    results.append(
        _report(
            len(parts) == 5 and parts[1] == parts[2] and int(parts[1] or 0) >= 1,
            "every detected face was blurred",
            f"detected={parts[1] if len(parts) > 1 else '?'} blurred={parts[2] if len(parts) > 2 else '?'}",
        )
    )
    redacted_sha = parts[3] if len(parts) > 3 else "none"
    results.append(
        _report(
            len(parts) > 4 and parts[4] == source_sha and redacted_sha != source_sha,
            "the served copy is different bytes from the upload",
            f"source={source_sha[:12]} redacted={redacted_sha[:12]}",
        )
    )

    media_status, served, media_headers = _raw_get(f"{REVIEW}/media/{redacted_sha}", headers)
    results.append(
        _report(
            media_status == 200 and served[:2] == b"\xff\xd8",
            "the redacted copy is served as a JPEG",
            f"status={media_status} bytes={len(served)}",
        )
    )
    # Case-insensitive: HTTP header names are, and ``urllib`` hands them back
    # exactly as the server wrote them rather than normalised.
    nosniff = next(
        (
            value
            for key, value in media_headers.items()
            if key.lower() == "x-content-type-options"
        ),
        "(absent)",
    )
    results.append(
        _report(
            nosniff == "nosniff",
            "the one route returning uploader-influenced bytes sets nosniff",
            nosniff,
        )
    )
    # The metadata strip, checked on the wire: an EXIF APP1 segment would carry
    # the capture GPS and an embedded thumbnail of the *unblurred* scene.
    results.append(
        _report(
            b"Exif\x00\x00" not in served,
            "the served bytes carry no EXIF segment",
            f"{len(served)} bytes scanned",
        )
    )
    # And the original is not reachable by its own content address.
    quarantine_status, _, _ = _raw_get(f"{REVIEW}/media/{source_sha}", headers)
    results.append(
        _report(
            quarantine_status == 404,
            "the upload's own content address is not fetchable",
            f"status={quarantine_status}",
        )
    )
    guard = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_media_redaction.py")],
        cwd=str(ROOT / "scripts"),
        capture_output=True,
        text=True,
        check=False,
    )
    results.append(
        _report(
            guard.returncode == 0,
            "the repository-level guard passes, and proved it can still fail",
            guard.stdout.strip().splitlines()[-1] if guard.stdout.strip() else "",
        )
    )

    # -- 3. Clause 3: the safety queue is unaffected by a saturated ml queue
    sys.stdout.write("\n  Clause 3 - safety latency under a saturated ml queue\n")
    for index in range(SATURATION_REPORTS):
        _submit(
            tenant_id,
            text=f"saturation report {index}",
            photo=_face_image(seed=index + 1),
            device=f"gate-load-{index}",
        )
    backlog = _psql(
        f"SELECT count(*) FROM complaints WHERE tenant_id = '{tenant_id}' AND status = 'submitted'"
    )
    status, receipt = _submit(
        tenant_id,
        text="live wire down across the footpath, sparking",
        photo=_face_image(seed=201),
        device="gate-danger-under-load",
    )
    urgent_id = str(receipt.get("complaint_id", "")) if isinstance(receipt, dict) else ""
    _, safety_latency = _await_events(tenant_id, urgent_id, until="safety_trigger_fired")
    results.append(
        _report(
            safety_latency < SAFETY_BUDGET_SECONDS,
            f"a danger signal reached FLAGGED in {safety_latency:.1f}s with {backlog} reports "
            f"queued for the ml worker",
            f"budget={SAFETY_BUDGET_SECONDS:.0f}s",
        )
    )
    # The other half of the claim: the backlog was real. A saturation step that
    # drained before the danger report arrived would make the number above
    # meaningless.
    still_pending = _psql(
        f"SELECT count(*) FROM complaints WHERE tenant_id = '{tenant_id}' "
        f"AND status = 'submitted' AND id != '{urgent_id}'"
    )
    results.append(
        _report(
            int(still_pending or 0) > 0,
            "the ml queue was genuinely still working when the danger signal was flagged",
            f"{still_pending} reports still unprocessed",
        )
    )

    # -- 4. Clause 4: custom keywords, no code change ----------------------
    sys.stdout.write("\n  Clause 4 - a tenant's own hazard vocabulary, with no deploy\n")
    grep = subprocess.run(
        ["git", "grep", "-il", INVENTED_HAZARD, "--", "backend", "infra"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    results.append(
        _report(
            not grep.stdout.strip(),
            f"{INVENTED_HAZARD!r} appears in no shipped module",
            grep.stdout.strip() or "(no matches)",
        )
    )
    activated, detail = _activate_ruleset(tenant_id, _safety_ruleset())
    results.append(_report(activated, "the tenant activated its own safety ruleset over HTTP", detail))

    # The resolver caches for its stated reload interval, so a report submitted
    # immediately can legitimately still be decided by the previous document.
    # Waiting is the honest thing to do — see ADR-0027.
    time.sleep(31)
    status, receipt = _submit(
        tenant_id,
        text=f"a {INVENTED_HAZARD} cylinder is venting in the north yard",
        photo=_face_image(seed=301),
        device="gate-custom",
    )
    custom_id = str(receipt.get("complaint_id", "")) if isinstance(receipt, dict) else ""
    custom_events, waited = _await_events(tenant_id, custom_id, until="safety_trigger_fired")
    results.append(
        _report(
            "safety_trigger_fired" in custom_events,
            "a hazard no code knows about bypassed the pipeline",
            f"chain={custom_events} after {waited:.1f}s",
        )
    )
    fired_rule = _psql(
        f"SELECT payload->>'rule_id' FROM events WHERE tenant_id = '{tenant_id}' "
        f"AND entity_id = '{custom_id}' AND event_type = 'safety_trigger_fired'"
    )
    results.append(
        _report(fired_rule == "exotic_oxidiser", "the tenant's own rule is what fired", fired_rule)
    )

    # -- 5. The evidence is on a verifiable chain --------------------------
    sys.stdout.write("\n  Chain integrity and no-deploy\n")
    verify = subprocess.run(
        [
            *COMPOSE,
            "exec",
            "-T",
            "api",
            "python",
            "-c",
            (
                "import asyncio, uuid\n"
                "from nemesis.db.session import session_scope\n"
                "from nemesis.events.verify import verify_chain\n"
                "from nemesis.tenancy.context import tenant_scope\n"
                f"tid = uuid.UUID('{tenant_id}')\n"
                "async def run():\n"
                "    broken = []\n"
                "    with tenant_scope(tid):\n"
                "        async with session_scope() as s:\n"
                "            rows = (await s.execute(__import__('sqlalchemy').text(\n"
                "                \"SELECT DISTINCT entity_id FROM events WHERE tenant_id = :t \"\n"
                "                \"AND entity_type = 'complaint'\"), {'t': tid})).scalars().all()\n"
                "            for entity in rows:\n"
                "                r = await verify_chain(s, tenant_id=tid,\n"
                "                    entity_type='complaint', entity_id=entity)\n"
                "                if not r.is_intact:\n"
                "                    broken.append(str(entity))\n"
                "    print('BROKEN=' + ','.join(broken) + '|COUNT=' + str(len(rows)))\n"
                "asyncio.run(run())\n"
            ),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    verdict = next(
        (line for line in verify.stdout.splitlines() if line.startswith("BROKEN=")), ""
    )
    results.append(
        _report(
            verdict.startswith("BROKEN=|COUNT=") and not verdict.endswith("COUNT=0"),
            "every complaint chain this gate wrote recomputes",
            verdict or verify.stderr.strip()[-200:],
        )
    )

    decisions_possible = _psql(
        f"SELECT count(*) FROM review_queue_items WHERE tenant_id = '{tenant_id}' AND status = 'open'"
    )
    results.append(
        _report(
            int(decisions_possible or 0) >= 3,
            "§11.4: every flag this gate raised has a destination a human can work",
            f"{decisions_possible} open items",
        )
    )

    # A decision closes the loop and becomes a Phase 11 label.
    open_item = _psql(
        f"SELECT id FROM review_queue_items WHERE tenant_id = '{tenant_id}' "
        f"AND status = 'open' ORDER BY priority LIMIT 1"
    )
    status, decided = _request(
        "POST",
        f"{REVIEW}/queue/{open_item}/decision",
        body={
            "decision": "approve",
            "rationale": "Gate: confirmed by the on-call engineer.",
            "decided_by_label": "gate-phase8",
        },
        headers={TENANT_HEADER: tenant_id, TOKEN_HEADER: _token()},
    )
    label_rows = _psql(
        f"SELECT count(*) FROM review_decisions WHERE tenant_id = '{tenant_id}'"
    )
    results.append(
        _report(
            status == 201 and label_rows == "1",
            "a human decision was recorded as a Phase 11 training label",
            f"status={status} labels={label_rows}",
        )
    )

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
        sys.stderr.write(f"{FAIL} Phase 8 gate not met: {passed}/{total} checks passed\n\n")
        return 1
    sys.stdout.write(
        f"{OK} Phase 8 gate met - {passed}/{total} checks passed against the running stack.\n"
        f"      A hazard report bypassed the pipeline entirely, before any scoring stage\n"
        f"      was enqueued. A photograph was redacted by the real MediaPipe detector in\n"
        f"      worker-ml, served with no EXIF, and its unredacted original was not\n"
        f"      fetchable by its own content address. A danger signal reached FLAGGED in\n"
        f"      {safety_latency:.1f}s with {backlog} image reports queued in front of it. A tenant\n"
        f"      activated a hazard vocabulary that exists in no module, and it fired.\n\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
