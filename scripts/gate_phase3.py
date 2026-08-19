"""Phase 3 gate, against the running stack.

The pytest suite proves the logic. This proves the *deployment*: that a
submission made over HTTP reaches a browser over a WebSocket, through a relay in
a different container, and that killing the worker mid-pipeline loses nothing.
Neither claim is reachable from a test process, because both are about processes.

Six steps, each one an independent failure in practice:

1. the stack is up and the relay holds its advisory lock
2. a submission is accepted and the complaint is retrievable
3. the event reached the outbox and the relay published it
4. a WebSocket client receives the envelope, and it carries no citizen data
5. the pipeline degraded honestly — no provider exists until Phases 8-12, so
   the first stage takes its declared fallback and leaves a queryable dead letter
6. ``SIGKILL`` on the worker loses nothing: a submission made while the worker
   is dead is durably queued, the replacement completes it exactly once, and
   in-flight redelivery is bounded rather than left at Celery's one-hour default

Standard library plus ``websockets`` is not available on the host, so the
WebSocket half runs inside the ``api`` container where the dependency set lives.

Exit code 0 clean, 1 on any failure.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ["docker", "compose"]
API = "http://localhost:8000"

OK, FAIL = "[ OK ]", "[FAIL]"


def _run(cmd: list[str], *, capture: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=ROOT, capture_output=capture, text=True, check=False)


def _psql(sql: str) -> str:
    result = _run(
        [*COMPOSE, "exec", "-T", "postgres", "psql", "-U", "nemesis", "-d", "nemesis", "-tAc", sql]
    )
    return result.stdout.strip()


def _report(passed: bool, label: str, detail: str = "") -> bool:
    marker = OK if passed else FAIL
    stream = sys.stdout if passed else sys.stderr
    stream.write(f"  {marker} {label}{f' — {detail}' if detail else ''}\n")
    stream.flush()
    return passed


# ---------------------------------------------------------------------------


def ensure_tenant() -> str:
    """A gate tenant, created idempotently so the script is re-runnable."""
    slug = "phase3-gate"
    existing = _psql(f"SELECT id FROM tenants WHERE slug = '{slug}'")
    if existing:
        return existing
    tenant_id = str(uuid.uuid4())
    _psql(
        f"INSERT INTO tenants (id, slug, name, plan) "
        f"VALUES ('{tenant_id}', '{slug}', 'Phase 3 Gate', 'pilot')"
    )
    return tenant_id


def submit(tenant_id: str) -> tuple[int, dict[str, object]]:
    """POST a multipart submission with no third-party HTTP client."""
    boundary = "----nemesisgate"
    jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 2048

    parts: list[bytes] = []

    def field(name: str, value: str) -> None:
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode()
        )

    field("latitude", "18.5204")
    field("longitude", "73.8567")
    field("device_fingerprint", uuid.uuid4().hex)
    field("description_text", "phase 3 gate submission")
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; '
        f'filename="gate.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'.encode()
    )
    parts.append(jpeg)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)

    request = urllib.request.Request(
        f"{API}/api/v1/complaints",
        data=body,
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "X-Tenant-ID": tenant_id,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def fetch_complaint(tenant_id: str, complaint_id: str) -> dict[str, object]:
    request = urllib.request.Request(
        f"{API}/api/v1/complaints/{complaint_id}", headers={"X-Tenant-ID": tenant_id}
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        payload: dict[str, object] = json.loads(response.read())
        return payload


def await_condition(sql: str, predicate: object, *, seconds: int = 30) -> str:
    deadline = time.monotonic() + seconds
    value = ""
    while time.monotonic() < deadline:
        value = _psql(sql)
        if callable(predicate) and predicate(value):
            return value
        time.sleep(0.5)
    return value


# ---------------------------------------------------------------------------

_WS_PROBE = r'''
import asyncio, json, sys, uuid
import websockets

async def main() -> None:
    tenant_id = sys.argv[1]
    url = f"ws://localhost:8000/ws/pipeline-events?tenant_id={tenant_id}&since=0"
    async with websockets.connect(url, open_timeout=10) as socket:
        received = []
        try:
            while len(received) < 12:
                raw = await asyncio.wait_for(socket.recv(), timeout=6)
                received.append(json.loads(raw))
        except (asyncio.TimeoutError, TimeoutError):
            pass
    print(json.dumps(received))

asyncio.run(main())
'''


def websocket_replay(tenant_id: str) -> list[dict[str, object]]:
    """Connect with ``since=0`` and collect the tenant's replayed envelopes.

    ``since=0`` rather than waiting for a live publish: the resume path reads
    the same outbox the live path publishes from and builds the envelope with
    the same function, so it exercises the scrubbing and the ordering without
    the gate depending on winning a race against the relay.
    """
    result = _run([*COMPOSE, "exec", "-T", "api", "python", "-c", _WS_PROBE, tenant_id])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip()[-500:])
    return list(json.loads(result.stdout.strip() or "[]"))


def wait_for_worker(seconds: int = 90) -> bool:
    """Block until worker-io is actually consuming, not merely healthy.

    The compose healthcheck pings the worker, which answers before it has
    finished draining whatever the *previous* run of this script left behind —
    and this script kills a worker on purpose, so a re-run inherits that state.
    Without this step the gate is a race against its own last invocation, which
    is how it failed intermittently while the system underneath it was correct.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        ping = _run(
            [
                *COMPOSE,
                "exec",
                "-T",
                "worker-io",
                "celery",
                "-A",
                "nemesis.worker.celery_app:celery_app",
                "inspect",
                "ping",
                "-t",
                "5",
            ]
        )
        depth = _run([*COMPOSE, "exec", "-T", "redis", "redis-cli", "LLEN", "safety"]).stdout.strip()
        if "pong" in ping.stdout.lower() and depth == "0":
            return True
        time.sleep(2)
    return False


def kill_worker() -> None:
    _run([*COMPOSE, "kill", "-s", "SIGKILL", "worker-io"], capture=True)


def start_worker() -> None:
    _run([*COMPOSE, "up", "-d", "worker-io"], capture=True)


# ---------------------------------------------------------------------------


def main() -> int:
    sys.stdout.write("\nPhase 3 gate — ingestion, orchestration, realtime transport\n\n")
    results: list[bool] = []

    # 1. Stack, relay, and worker exporter.
    services = _run([*COMPOSE, "ps", "--format", "{{.Service}}:{{.Health}}"]).stdout
    unhealthy = [
        line
        for line in services.strip().splitlines()
        if line and "healthy" not in line and not line.endswith(":")
    ]
    results.append(
        _report(not unhealthy, "every service is healthy", ", ".join(unhealthy) or "7/7")
    )

    lock_holders = _psql(
        "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND granted"
    )
    results.append(
        _report(
            lock_holders.isdigit() and int(lock_holders) == 1,
            "exactly one relay holds the advisory lock",
            f"{lock_holders} holder(s)",
        )
    )

    results.append(
        _report(
            wait_for_worker(),
            "worker-io is consuming and its queue is drained",
        )
    )

    worker_metrics = _run(
        [
            *COMPOSE,
            "exec",
            "-T",
            "worker-io",
            "python",
            "-c",
            "import urllib.request;"
            "print(urllib.request.urlopen('http://localhost:9100/metrics',timeout=5).status)",
        ]
    )
    results.append(
        _report(
            worker_metrics.stdout.strip() == "200",
            "the worker metrics exporter answers a scrape",
            worker_metrics.stdout.strip() or worker_metrics.stderr.strip()[:80],
        )
    )

    # 2. Submission.
    tenant_id = ensure_tenant()
    status, body = submit(tenant_id)
    complaint_id = str(body.get("complaint_id", ""))
    results.append(_report(status == 202 and bool(complaint_id), "a submission is accepted", f"HTTP {status}"))
    if not complaint_id:
        sys.stderr.write(f"\n{FAIL} gate not met: no complaint to follow\n")
        return 1

    fetched = fetch_complaint(tenant_id, complaint_id)
    results.append(
        _report(
            fetched.get("complaint_id") == complaint_id
            and "description_text" not in fetched
            and "photo_url" not in fetched,
            "the complaint is retrievable and carries no citizen data",
        )
    )

    # 3. Outbox and relay.
    dispatched = await_condition(
        f"SELECT count(*) FROM outbox_messages WHERE tenant_id = '{tenant_id}' "
        f"AND entity_id = '{complaint_id}' AND dispatched_at IS NOT NULL",
        lambda value: value.isdigit() and int(value) >= 1,
    )
    results.append(
        _report(
            dispatched.isdigit() and int(dispatched) >= 1,
            "the relay published the submission from a committed row",
            f"{dispatched} dispatched",
        )
    )

    # 4. Realtime.
    try:
        envelopes = websocket_replay(tenant_id)
        submitted = [e for e in envelopes if e.get("event_type") == "complaint_submitted"]
        clean = all(
            not (isinstance(e.get("payload"), dict) and e["payload"])
            for e in submitted
        )
        results.append(
            _report(
                bool(submitted) and clean,
                "a WebSocket client receives the envelope with an empty payload",
                f"{len(envelopes)} envelope(s)",
            )
        )
    except Exception as exc:
        results.append(_report(False, "a WebSocket client receives the envelope", str(exc)[:120]))

    # 5. Honest degradation (§24.2).
    #
    # **Which stage degrades has moved, and the assertion moved with it rather
    # than being loosened.** Until Phase 8 no provider was registered for any
    # stage, so the pipeline stopped at the first one — `safety_check`. Phase 8
    # ships two providers, and the submission above carries a JPEG magic number
    # with 2 KB of zeroes behind it, which is not a decodable image. So the
    # safety check now runs and passes (there is no hazard in "phase 3 gate
    # submission"), and `trust_verification` is where the pipeline stops,
    # because §22.1 refuses to let an image it cannot decode reach a stage that
    # would serve it.
    #
    # What is asserted is therefore the *property* rather than the stage name:
    # the pipeline stopped at a stage that declares HALTED_FOR_REVIEW, the API
    # says so, and there is a dead letter to find. That survives Phase 9
    # registering the classifier too.
    #
    # Which makes the visible outcome the thing worth asserting, and asserting
    # it through the API rather than the database: a client polling §26.2 has to
    # be able to tell "still processing" from "stopped, waiting for a human",
    # and the status alone cannot say that.
    degraded_stage = await_condition(
        f"SELECT state->>'degraded_stage' FROM event_snapshots WHERE entity_id = '{complaint_id}'"
        f" UNION ALL SELECT payload->>'stage' FROM events "
        f"WHERE entity_id = '{complaint_id}' AND event_type = 'pipeline_stage_degraded' LIMIT 1",
        lambda value: bool(value),
        seconds=90,
    )
    #: Stages whose declared fallback is HALTED_FOR_REVIEW — the ones where
    #: stopping is the correct outcome rather than a skip. Listed here rather
    #: than imported: this script runs on a bare interpreter.
    halting_stages = {"safety_check", "trust_verification", "severity_scoring", "routing"}
    dead_letters = _psql(
        f"SELECT count(*) FROM pipeline_dead_letters WHERE entity_id = '{complaint_id}' "
        f"AND resolved_at IS NULL"
    )
    degraded_view = fetch_complaint(tenant_id, complaint_id)
    results.append(
        _report(
            degraded_stage in halting_stages
            and dead_letters.isdigit()
            and int(dead_letters) >= 1
            and degraded_view.get("degraded_stage") == degraded_stage
            and degraded_view.get("degraded_fallback") == "halted_for_review",
            "an unavailable stage degrades visibly and leaves a queryable dead letter",
            f"stage={degraded_stage or 'unset'}, "
            f"api={degraded_view.get('degraded_fallback')}, dead letters={dead_letters}",
        )
    )

    # 6. SIGKILL, and the honest version of "loses nothing on restart".
    #
    # The drill kills the worker *first* and submits after, deliberately. The
    # obvious ordering — submit, then kill — is a race on whether the message
    # was still in the queue or already in flight, and the two have genuinely
    # different recovery characteristics on a Redis broker:
    #
    #   queued    the message is a list entry in Redis and the replacement
    #             worker takes it the moment it connects. Nothing is lost and
    #             nothing is delayed. That is what this asserts.
    #   in flight the message is in `unacked` and is restored only after
    #             `visibility_timeout`. Also not lost, but not prompt either —
    #             and with the Celery default of 3600 seconds it is an hour,
    #             which is what the next check exists to prevent.
    #
    # Asserting the racy version would produce a gate that passed or failed on
    # timing, which is worse than a narrower claim stated accurately.
    before_events = _psql(f"SELECT count(*) FROM events WHERE entity_id = '{complaint_id}'")

    kill_worker()
    second_status, second_body = submit(tenant_id)
    victim = str(second_body.get("complaint_id", ""))
    queued = _run(
        [*COMPOSE, "exec", "-T", "redis", "redis-cli", "LLEN", "safety"]
    ).stdout.strip()
    results.append(
        _report(
            second_status == 202 and queued.isdigit() and int(queued) >= 1,
            "a submission is accepted and durably queued with the worker dead",
            f"HTTP {second_status}, {queued} queued",
        )
    )

    start_worker()
    recovered = await_condition(
        f"SELECT count(*) FROM events WHERE entity_id = '{victim}' "
        f"AND event_type = 'pipeline_stage_degraded'",
        lambda value: value.isdigit() and int(value) == 1,
        seconds=120,
    )
    after_events = _psql(f"SELECT count(*) FROM events WHERE entity_id = '{complaint_id}'")
    results.append(
        _report(
            recovered == "1",
            "the restarted worker completes it, exactly once",
            f"degradation events={recovered or 'unset'}",
        )
    )
    results.append(
        _report(
            before_events == after_events,
            "the restart appended no duplicate to an untouched complaint",
            f"{before_events} -> {after_events} events",
        )
    )

    visibility = _run(
        [
            *COMPOSE,
            "exec",
            "-T",
            "api",
            "python",
            "-c",
            "from nemesis.worker.celery_app import celery_app as a;"
            "print(a.conf.broker_transport_options.get('visibility_timeout', 0),"
            "a.conf.task_time_limit)",
        ]
    ).stdout.split()
    bounded = (
        len(visibility) == 2
        and visibility[0].isdigit()
        and int(visibility[0]) > 0
        and int(visibility[0]) > int(visibility[1])
    )
    results.append(
        _report(
            bounded,
            "in-flight redelivery is bounded, not left at Celery's one-hour default",
            f"visibility_timeout={visibility[0] if visibility else 'unset'}s",
        )
    )

    broken = _psql(
        "SELECT count(*) FROM event_chain_heads WHERE sequence < 0"
    )
    results.append(_report(broken == "0", "no chain head is inconsistent"))

    sys.stdout.write("\n")
    if all(results):
        sys.stdout.write(f"{OK} Phase 3 gate met — {len(results)} checks passed\n\n")
        return 0
    sys.stderr.write(f"{FAIL} Phase 3 gate not met — {results.count(False)} check(s) failed\n\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
