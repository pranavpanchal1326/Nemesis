"""Phase 9 gate, against the running stack.

The pytest suite proves the scoring rule against deterministic encoders in one
process. The F1 report proves the *model*, inside ``worker-ml``, against a
labelled corpus. This proves the **deployment**: that the number in the
repository is the number this command produces, that a category nothing in this
repository has ever heard of classifies with no deploy, and that the latency
budget is met by the containers rather than by an estimate.

The four gate clauses, executed in order:

1. **A published per-category F1 number in the repo, reproducible by one
   command.** Checked in both directions: the artefact exists, is well-formed,
   names the real checkpoints, and was measured against the corpus that is
   currently committed — and then the command is *run again* and the numbers are
   compared. A report that cannot be reproduced is a screenshot.
2. **Any category below 65% F1 triggers the §43.2 prompt pass and a
   re-measure; the honest number ships either way.** So the gate does not
   require the number to be good. It requires the number to be *published*, and
   it requires the prompt-pass record to exist whenever a category is below the
   floor — which is the part that would otherwise quietly not happen.
3. **A new tenant category is classifiable by adding prompts alone.** A tenant
   is provisioned, a category whose key appears in no module is added over HTTP,
   its prompts are attached over HTTP, and a report using its vocabulary is
   submitted through the real ingest path and classified into it. The gate greps
   the repository for the key to prove no code knows it, and compares the API
   container's identity across the whole run to prove nothing was deployed.
4. **Inference latency within the §27.1 budget on this hardware, measured not
   estimated.** Three numbers, because they answer different questions: the
   harness's per-example encode-and-score time, a **per-model** pass for CLIP,
   e5 and Whisper separately, and the wall time from an HTTP submission to
   ``classification_scored`` appearing on the complaint's own chain with the
   real pipeline in between. The per-model row exists because the corpus is
   text: without it, this clause checks the cheapest of the three models while
   saying "inference latency".

Clause 3 has a second half (3b) that was added after the first version of this
gate passed without either heavy model ever executing. The sandbox tenant ships
no ``clip`` prompt sets, so the image path took its "no prompts for this
encoder" branch on every request and CLIP never ran; nothing outside
``fetch_models.py``'s load check had ever handed Whisper audio. Both now run
against real bytes in the real container, and both are existence proofs rather
than accuracy claims.

Two further checks about the deployment rather than the logic:

5. The §22.1 **distant-face recall curve** is present in the report and was
   measured against the real MediaPipe detector, not skipped.
6. Every classification this gate produced is **on a verifiable hash chain**,
   through the same ``verify_chain`` the earlier gates use.

Standard library only. Exit code 0 clean, 1 on any failure.
"""

from __future__ import annotations

import json
import math
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

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ["docker", "compose"]
API = "http://localhost:8000"
CONTROL_PLANE = f"{API}/api/v1/control-plane"

REPORT_JSON = ROOT / "docs" / "reports" / "perception-f1.json"
REPORT_MARKDOWN = ROOT / "docs" / "reports" / "perception-f1.md"

TOKEN_HEADER = "X-Control-Plane-Token"
TENANT_HEADER = "X-Tenant-ID"
DEFAULT_TOKEN = "dev-only-insecure-control-plane-token-change-me"

OK, FAIL = "[ OK ]", "[FAIL]"

#: The gate's floor, duplicated from ``perception.harness.F1_FLOOR`` on purpose.
#: This script runs on a bare interpreter with no ``nemesis`` on the path, like
#: every other gate here — and a gate that imported the module it checks would
#: agree with it by construction. Clause 1 asserts the two match.
F1_FLOOR = 0.65

#: How long the pipeline has to reach a terminal state for one report. Generous:
#: §27.1 budgets the whole pipeline at thirty seconds and the ml worker runs at
#: concurrency 1, so a cold model load can sit in front of the first submission.
PIPELINE_TIMEOUT_SECONDS = 240

#: §27.1's budget for the classification stage itself, in seconds. The harness
#: measures against the same number, read from ``PerceptionSettings``; here it is
#: stated, and clause 4 asserts the report's copy agrees.
LATENCY_BUDGET_SECONDS = 10.0

#: End-to-end budget from HTTP accept to ``classification_scored`` on the chain.
#: Much larger than the stage budget and deliberately so: it includes the queue
#: hop, the safety stage, the trust stage, and a possible cold model load, none
#: of which are what §27.1 budgets classification at. It is here to catch a
#: pipeline that has stopped rather than to grade the model.
END_TO_END_BUDGET_SECONDS = 120.0

#: A category and a vocabulary that appear nowhere in this repository. Clause 3's
#: whole point is that no code knows them — the gate greps for them to prove it.
INVENTED_CATEGORY = "abandoned_palanquin"
INVENTED_PROMPT = "a complaint about an abandoned palanquin left blocking a lane"
INVENTED_REPORT = "someone has abandoned a palanquin in the middle of our lane and nobody will move it"


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
        with urllib.request.urlopen(request, timeout=180) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, (json.loads(raw) if raw else None)
    except urllib.error.URLError as exc:
        return 0, {"error": str(exc)}


def _png(seed: int) -> bytes:
    """A small PNG, built from bytes. No Pillow: this script has no dependencies.

    **A photograph is not optional here.** §26.1 requires media on every
    submission and the ingest endpoint refuses a body without it — which is a
    422 that looks nothing like a perception problem and cost the first run of
    this gate four cascading failures. It carries no face on purpose: the face
    path is Phase 8's gate, and the only thing this image has to do is exist so
    the report reaches the classification stage.
    """
    size = 64
    raw = b"".join(
        b"\x00"
        + b"".join(
            struct.pack("BBB", (x * 3 + seed) % 256, (y * 5) % 256, (x + y) % 256)
            for x in range(size)
        )
        for y in range(size)
    )

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


def _wav(seconds: float = 2.0, rate: int = 16000) -> bytes:
    """A real 16-bit PCM WAV, built from bytes. Standard library only.

    **What this proves and what it does not.** It is a genuine audio file: the
    ingest sniffer recognises the RIFF/WAVE magic, ``faster-whisper`` hands it to
    ffmpeg, and CTranslate2 runs a real decode over real samples. That exercises
    the entire transcription path, which had never executed outside
    ``fetch_models.py``'s load check.

    It is **not** speech, and what actually happens is worth stating precisely
    rather than glossing: ffmpeg decodes it, faster-whisper logs
    ``Processing audio with duration 00:02.000``, and its VAD filter then
    discards the whole clip as non-speech. So the path proven is *decode plus
    front end*, and the decoder stage is exercised while the acoustic model is
    not. That is a real step up from never running at all and it is less than a
    speech test.

    Proving speech-to-text quality needs a spoken corpus with a licence, which
    this repository does not have — the same shape of gap as the unmeasured
    image modality, and recorded in the same places rather than implied by a
    passing gate.
    """
    frames = int(rate * seconds)
    samples = bytearray()
    for index in range(frames):
        # A slow sweep from 200 Hz to about 1 kHz, in the band a voice occupies.
        phase = 2.0 * math.pi * (200.0 + 400.0 * index / frames) * index / rate
        value = int(12000 * math.sin(phase))
        samples += struct.pack("<h", value)

    data = bytes(samples)
    header = (
        b"RIFF"
        + struct.pack("<I", 36 + len(data))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
        + b"data"
        + struct.pack("<I", len(data))
    )
    return header + data


def _multipart(
    fields: dict[str, str], photo: bytes | None, audio: bytes | None = None
) -> tuple[bytes, str]:
    """A ``multipart/form-data`` body, hand-rolled — this script has no deps.

    Both media parts are optional so the gate can submit a photo-only report, an
    audio-only one, or both. §26.1 requires at least one, and the endpoint
    enforces it; passing neither here is a bug in the gate rather than a case to
    handle.
    """
    boundary = f"----nemesis{uuid.uuid4().hex}"
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        for name, value in fields.items()
    ]
    for name, filename, content_type, payload in (
        ("photo", "report.png", "image/png", photo),
        ("audio", "report.wav", "audio/wav", audio),
    ):
        if payload is None:
            continue
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
            f'filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n'.encode()
        )
        parts.append(payload)
        parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _submit(
    tenant_id: str,
    *,
    text: str | None,
    locale: str = "en",
    seed: int = 1,
    photo: bool = True,
    audio: bool = False,
    device: str = "gate9-device",
) -> tuple[int, Any]:
    fields = {
        "latitude": "18.5204",
        "longitude": "73.8567",
        "device_fingerprint": device,
        "locale": locale,
    }
    if text is not None:
        fields["description_text"] = text
    body, content_type = _multipart(
        fields,
        _png(seed) if photo else None,
        _wav() if audio else None,
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
# Stack helpers
# ---------------------------------------------------------------------------


def _psql(sql: str) -> str:
    result = subprocess.run(
        [*COMPOSE, "exec", "-T", "postgres", "psql", "-U", "nemesis", "-d", "nemesis", "-tAc", sql],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return result.stdout.strip()


def _api_identity() -> str:
    result = subprocess.run(
        [*COMPOSE, "ps", "-q", "api"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
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
        encoding="utf-8",
        errors="replace",
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
        encoding="utf-8",
        errors="replace",
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
    started = time.monotonic()
    deadline = started + timeout
    events: list[str] = []
    while time.monotonic() < deadline:
        events = _events(tenant_id, complaint_id)
        if until in events:
            return events, time.monotonic() - started
        time.sleep(0.5)
    return events, time.monotonic() - started


def _run_harness(out: str) -> tuple[bool, str]:
    """Run the published command inside ``worker-ml``. Returns (ok, tail)."""
    result = subprocess.run(
        [
            *COMPOSE,
            "exec",
            "-T",
            "worker-ml",
            "sh",
            "-c",
            f"PYTHONPATH=/app python scripts/eval_perception.py --out {out}",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    tail = (result.stdout or result.stderr).strip().splitlines()
    return result.returncode == 0, "\n".join(tail[-3:])


def _transcription_counts() -> dict[str, float]:
    """``perception_transcriptions_total`` by label set, from worker-ml's exporter.

    Read from inside the container: port 9100 is deliberately not published to
    the host, because a worker's metrics endpoint is for Prometheus on the
    compose network and not for anything outside it. Failing softly to an empty
    map rather than raising — a metrics endpoint that is down is a different
    incident from the one this clause is about, and the caller's delta is empty
    either way, which reads as "the transcriber did not run" and is the safe
    direction to be wrong in.
    """
    result = subprocess.run(
        [
            *COMPOSE,
            "exec",
            "-T",
            "worker-ml",
            "python",
            "-c",
            "import urllib.request;"
            "print(urllib.request.urlopen('http://localhost:9100', timeout=10).read().decode())",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    counts: dict[str, float] = {}
    for line in result.stdout.splitlines():
        if not line.startswith("nemesis_perception_transcriptions_total{"):
            continue
        labels, _, value = line.rpartition(" ")
        try:
            counts[labels] = float(value)
        except ValueError:  # pragma: no cover - a malformed exposition line
            continue
    return counts


def _await_transcription(
    before: dict[str, float], *, timeout: float = 90.0
) -> tuple[dict[str, float], float]:
    """Poll until ``perception_transcriptions_total`` moves. Returns the delta.

    Bounded well below ``PIPELINE_TIMEOUT_SECONDS``: transcription of a
    two-second clip costs about 2.5 s of inference, and the rest of this budget
    is the queue hop and the trust stage in front of it.
    """
    started = time.monotonic()
    deadline = started + timeout
    delta: dict[str, float] = {}
    while time.monotonic() < deadline:
        after = _transcription_counts()
        delta = {
            key: value - before.get(key, 0.0)
            for key, value in after.items()
            if value - before.get(key, 0.0) > 0
        }
        if delta:
            return delta, time.monotonic() - started
        time.sleep(2.0)
    return delta, time.monotonic() - started


def _read_container_json(path: str) -> dict[str, Any]:
    result = subprocess.run(
        [*COMPOSE, "exec", "-T", "worker-ml", "cat", path],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    try:
        return dict(json.loads(result.stdout))
    except json.JSONDecodeError:
        return {}


def main() -> int:
    sys.stdout.write("\nPhase 9 gate - perception layer, against the running stack\n\n")
    results: list[bool] = []
    started_before = _api_identity()

    # -- 1. Clause 1: a published number, reproducible by one command ------
    sys.stdout.write("\n  Clause 1 - a published per-category F1 number, reproducible\n")

    if not REPORT_JSON.is_file() or not REPORT_MARKDOWN.is_file():
        _report(False, "the report artefacts exist", f"expected {REPORT_JSON} and the markdown")
        return 1
    published = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
    _report(True, "the report artefacts exist", f"{REPORT_JSON.name}, {REPORT_MARKDOWN.name}")

    per_category = published.get("per_category") or []
    results.append(
        _report(
            bool(per_category) and all("f1" in entry for entry in per_category),
            f"a per-category F1 number is published for {len(per_category)} categor(y/ies)",
            f"macro F1 {published.get('totals', {}).get('macro_f1')}",
        )
    )
    results.append(
        _report(
            published.get("f1_floor") == F1_FLOOR,
            "the report and this gate agree on where the floor is",
            f"report={published.get('f1_floor')} gate={F1_FLOOR}",
        )
    )
    # The number has to have come from the real checkpoints. A report measured
    # against a deterministic fake would have the same shape and mean nothing.
    models = published.get("models") or []
    results.append(
        _report(
            any("multilingual-e5" in model for model in models),
            "the number was measured with the checkpoints docs/MODELS.md declares",
            ", ".join(models) or "(none named)",
        )
    )
    # And against the corpus that is committed *now*, not one that has since been
    # edited — which is what the fingerprint is for.
    fingerprint = _psql("SELECT 1")  # keeps psql warm; ignored
    del fingerprint
    reproduced_path = "/tmp/gate9-report"
    ok, tail = _run_harness(reproduced_path)
    results.append(_report(ok, "`nem f1` runs to completion inside worker-ml", tail))
    reproduced = _read_container_json(f"{reproduced_path}/perception-f1.json")

    results.append(
        _report(
            bool(reproduced)
            and reproduced.get("corpus", {}).get("fingerprint")
            == published.get("corpus", {}).get("fingerprint"),
            "the re-run measured the corpus that is committed now",
            f"{str(published.get('corpus', {}).get('fingerprint'))[:12]} vs "
            f"{str(reproduced.get('corpus', {}).get('fingerprint'))[:12]}",
        )
    )
    published_scores = {entry["category"]: entry["f1"] for entry in per_category}
    reproduced_scores = {
        entry["category"]: entry["f1"] for entry in reproduced.get("per_category", [])
    }
    results.append(
        _report(
            published_scores == reproduced_scores,
            "the published numbers are the numbers the command produces",
            (
                "identical"
                if published_scores == reproduced_scores
                else f"published={published_scores} reproduced={reproduced_scores}"
            ),
        )
    )

    # -- 2. Clause 2: the floor triggers a prompt pass, honestly -----------
    sys.stdout.write("\n  Clause 2 - the 65% floor, and the honest number either way\n")
    below = list(published.get("below_floor") or [])
    results.append(
        _report(
            below == [entry["category"] for entry in per_category if entry["f1"] < F1_FLOOR],
            "the report's own below-floor list agrees with its numbers",
            ", ".join(below) or "(nothing below the floor)",
        )
    )
    if below:
        # The clause is not "the number is good". It is that a category under the
        # floor produced §43.2 work — and the record of that work is what would
        # otherwise quietly not exist.
        worklist = published.get("prompt_pass_worklist") or []
        results.append(
            _report(
                bool(worklist),
                "a §43.2 prompt-pass work list was produced for the categories below the floor",
                f"{len(worklist)} confusion pair(s)",
            )
        )
        results.append(
            _report(
                bool(published.get("prompt_pass")),
                "the prompt pass that was done is recorded in the artefact",
                "; ".join(published.get("prompt_pass") or []) or "(no record)",
            )
        )
        # And the work list is measured on the calibration split, not the held-out
        # one. Rewriting prompts against held-out confusions turns the held-out
        # set into a development set and the next number reports the tuning.
        results.append(
            _report(
                worklist != (published.get("confusions") or []),
                "the work list is the calibration split's, not the held-out set's",
                "distinct" if worklist != published.get("confusions") else "identical - leaked",
            )
        )
    else:
        results.append(_report(True, "no category is below the floor", "no prompt pass required"))

    results.append(
        _report(
            bool(published.get("caveats")),
            "the report states what it does not establish",
            f"{len(published.get('caveats') or [])} caveat(s)",
        )
    )

    # -- 3. Clause 3: a new category, by adding prompts alone --------------
    sys.stdout.write("\n  Clause 3 - a category no code knows, classifiable by prompts alone\n")
    grep = subprocess.run(
        ["git", "grep", "-il", INVENTED_CATEGORY, "--", "backend/nemesis", "infra"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    results.append(
        _report(
            not grep.stdout.strip(),
            f"{INVENTED_CATEGORY!r} appears in no shipped module",
            grep.stdout.strip() or "(no matches)",
        )
    )

    slug = f"gate9-{uuid.uuid4().hex[:8]}"
    provisioned = _provision(slug)
    tenant_id = str(provisioned.get("tenant_id", ""))
    if not tenant_id:
        _report(False, "provision a tenant", str(provisioned)[:300])
        return 1
    _report(True, f"provisioned tenant {slug}", tenant_id)
    admin = {TENANT_HEADER: tenant_id, TOKEN_HEADER: _token()}

    status, created = _request(
        "POST",
        f"{CONTROL_PLANE}/taxonomy",
        headers=admin,
        body={
            "key": INVENTED_CATEGORY,
            "display_name": "Abandoned palanquin",
            "routing_hints": {"department_code": "PW"},
        },
    )
    results.append(
        _report(status == 201, "the category is created over HTTP", f"{status} {created}")
    )

    status, attached = _request(
        "PUT",
        f"{CONTROL_PLANE}/taxonomy/prompt-sets",
        headers=admin,
        body={
            "node_key": INVENTED_CATEGORY,
            "locale": "en",
            "encoder": "text",
            "prompts": [INVENTED_PROMPT],
            "negative_prompts": ["a complaint about a pothole in the road"],
            "prompt_set_version": "gate9-1",
        },
    )
    results.append(
        _report(status == 200, "its prompts are attached over HTTP", f"{status} {attached}")
    )

    # The taxonomy read path caches for its stated reload interval, so a report
    # submitted immediately can legitimately still be scored against the previous
    # bundle. Waiting is the honest thing to do - see ADR-0027.
    time.sleep(31)

    status, receipt = _submit(tenant_id, text=INVENTED_REPORT)
    complaint_id = str(receipt.get("complaint_id", "")) if isinstance(receipt, dict) else ""
    results.append(
        _report(status == 202 and bool(complaint_id), "the report is accepted", str(status))
    )

    chain, end_to_end = _await_events(
        tenant_id, complaint_id, until="classification_scored"
    )
    results.append(
        _report(
            "classification_scored" in chain,
            "the real pipeline classified it",
            f"chain={chain} after {end_to_end:.1f}s",
        )
    )
    category = _psql(
        f"SELECT payload->>'category' FROM events WHERE tenant_id = '{tenant_id}' "
        f"AND entity_id = '{complaint_id}' AND event_type = 'classification_scored'"
    )
    results.append(
        _report(
            category == INVENTED_CATEGORY,
            "into the category that was invented ten seconds ago",
            category or "(no category)",
        )
    )
    projected = _psql(
        f"SELECT category FROM complaints WHERE tenant_id = '{tenant_id}' AND id = '{complaint_id}'"
    )
    results.append(
        _report(
            projected == INVENTED_CATEGORY,
            "and the projection agrees, so the rest of the system can act on it",
            projected or "(null)",
        )
    )

    # -- 3b. The two models the text path never touches --------------------
    #
    # **Added after the first version of this gate passed 26/26 without either
    # of them ever executing.** Clause 3 attaches a `text` prompt set, the
    # sandbox tenant ships no `clip` prompts at all, and the image path
    # therefore took its "no prompts for this encoder" branch and skipped
    # scoring entirely — so a gate that submitted a photograph on every request
    # proved nothing about CLIP. Whisper was worse: nothing outside
    # `fetch_models.py`'s load check had ever handed it audio.
    #
    # Neither of these is an accuracy claim. They are existence proofs that the
    # code path runs in the real container against real bytes, which is the
    # weakest useful thing to know and was not known.
    sys.stdout.write("\n  Clause 3b - the image and audio paths actually execute\n")

    status, attached_clip = _request(
        "PUT",
        f"{CONTROL_PLANE}/taxonomy/prompt-sets",
        headers=admin,
        body={
            "node_key": INVENTED_CATEGORY,
            "locale": "en",
            "encoder": "clip",
            "prompts": ["a photo of an abandoned palanquin blocking a lane"],
            "negative_prompts": ["a photo of an empty clean street"],
            "prompt_set_version": "gate9-clip-1",
        },
    )
    results.append(
        _report(status == 200, "CLIP prompts are attached over HTTP", f"{status} {attached_clip}")
    )
    time.sleep(31)  # the taxonomy read path caches; see ADR-0027

    status, receipt = _submit(
        tenant_id, text="a photograph with no description", seed=7, device="gate9-image"
    )
    image_id = str(receipt.get("complaint_id", "")) if isinstance(receipt, dict) else ""
    _await_events(tenant_id, image_id, until="classification_scored")
    image_models = _psql(
        f"SELECT payload->'model_ids'->>'image' FROM events WHERE tenant_id = '{tenant_id}' "
        f"AND entity_id = '{image_id}' AND event_type = 'classification_scored'"
    )
    results.append(
        _report(
            image_models.startswith("open_clip:"),
            "the real CLIP image tower ran and is named in the event",
            image_models or "(the image modality did not contribute)",
        )
    )

    # Audio, with no description at all, so the transcriber is the only thing
    # that can produce text for this report.
    before_transcriptions = _transcription_counts()
    status, receipt = _submit(
        tenant_id, text=None, photo=False, audio=True, device="gate9-audio"
    )
    audio_id = str(receipt.get("complaint_id", "")) if isinstance(receipt, dict) else ""
    results.append(
        _report(status == 202 and bool(audio_id), "an audio-only report is accepted", str(status))
    )
    # **Asserted through the metric rather than through an event, because a
    # transcript with no words emits no event — deliberately.** The stage
    # declines to write an empty transcript into the log, on the reasoning that
    # a projection claiming a transcript exists would send a reviewer looking
    # for text that is not there. That reasoning is right about the *transcript*
    # and leaves "the transcriber ran and heard nothing" indistinguishable from
    # "the transcriber never ran" — which is exactly what
    # ``perception_transcriptions_total{outcome}`` exists to separate. A before
    # and after delta makes the claim about *this* submission rather than about
    # anything the worker has ever done.
    #
    # **Polled on the metric rather than on the chain, because an abstained
    # classification emits no event.** A report the layer declines to classify
    # is parked and dead-lettered — §24.2 working — and its chain stays at
    # ``complaint_submitted``. The first version of this clause waited four
    # minutes for an event that was never going to arrive and then passed
    # anyway on the metric, which is a slow way to be right.
    fired, audio_waited = _await_transcription(before_transcriptions)
    results.append(
        _report(
            bool(fired),
            "the real faster-whisper decoded and processed real audio bytes",
            f"{fired or '(the transcriber was never reached)'} after {audio_waited:.1f}s",
        )
    )
    # Deliberately *not* an assertion about the words, and the outcome label in
    # the delta above says which path ran: empty means ffmpeg decoded the
    # clip and Whisper VAD discarded it as non-speech, which is exactly what a
    # synthesised tone should do. Transcription *quality* stays unmeasured, and
    # the report says so in words rather than leaving a passing gate to imply
    # otherwise.

    # -- 4. Clause 4: latency within the §27.1 budget, measured ------------
    sys.stdout.write("\n  Clause 4 - inference latency, measured on this hardware\n")
    latency = published.get("latency") or {}
    results.append(
        _report(
            latency.get("budget_seconds") == LATENCY_BUDGET_SECONDS,
            "the report measured against §27.1's stated budget",
            f"report={latency.get('budget_seconds')} gate={LATENCY_BUDGET_SECONDS}",
        )
    )
    results.append(
        _report(
            bool(latency.get("within_budget")) and int(latency.get("count") or 0) > 0,
            f"per-example inference p95 is {float(latency.get('p95_seconds') or 0) * 1000:.0f} ms "
            f"over {latency.get('count')} example(s)",
            f"budget {LATENCY_BUDGET_SECONDS * 1000:.0f} ms",
        )
    )
    # **All three models, not just the one the corpus exercises.** The first
    # version of this clause read the harness's per-example number, which times
    # the text encoder — the cheapest of the three — while claiming to check
    # "inference latency". CLIP encode and Whisper transcribe are what a
    # photographed or spoken report actually costs.
    per_model = latency.get("per_model") or []
    results.append(
        _report(
            {entry["operation"] for entry in per_model}
            >= {"encode_image", "encode_text", "transcribe"},
            "all three models were timed, not just the one the corpus uses",
            ", ".join(sorted(entry["operation"] for entry in per_model)) or "(none)",
        )
    )
    over = [
        f"{entry['operation']}={entry['p95_seconds']:.2f}s"
        for entry in per_model
        if not entry["within_budget"]
    ]
    results.append(
        _report(
            not over,
            "every model's p95 is inside the §27.1 budget: "
            + ", ".join(
                f"{entry['operation']} {entry['p95_seconds'] * 1000:.0f} ms"
                for entry in per_model
            ),
            ", ".join(over) or f"budget {LATENCY_BUDGET_SECONDS:.0f}s",
        )
    )

    results.append(
        _report(
            end_to_end < END_TO_END_BUDGET_SECONDS,
            f"HTTP accept to classification_scored took {end_to_end:.1f}s through the real pipeline",
            f"budget={END_TO_END_BUDGET_SECONDS:.0f}s (queue hop and cold load included)",
        )
    )

    # -- 5. §22.1 distant-face recall, measured on the same harness --------
    sys.stdout.write("\n  §22.1 - distant-face recall, on the same harness\n")
    faces = published.get("face_recall")
    results.append(
        _report(
            isinstance(faces, dict) and str(faces.get("detector_id", "")).startswith("mediapipe:"),
            "the curve was measured against the real MediaPipe detector, not skipped",
            str((faces or {}).get("detector_id", "(absent)")),
        )
    )
    if isinstance(faces, dict):
        smallest = faces.get("smallest_reliable_px")
        buckets = faces.get("buckets") or []
        results.append(
            _report(
                len(buckets) >= 4 and any(bucket["recall"] >= 1.0 for bucket in buckets),
                "the curve spans sizes where recall both holds and fails",
                f"{len(buckets)} bucket(s), full recall from "
                f"{smallest if smallest is not None else 'nowhere'} px",
            )
        )
        # Deliberately not a pass/fail threshold on the number itself. §22.1's
        # answer to a shortfall is a second detector or a tiled pass, which is
        # Phase 10+ work; what this phase owes is the measurement and the
        # disclosure, and a gate that failed on the number would delete the
        # incentive to publish an inconvenient one.
        results.append(
            _report(
                any("distant-face" in caveat or "face" in caveat for caveat in published.get("caveats", [])),
                "and the report says what the curve does not establish",
                "recorded",
            )
        )

    # -- 6. The evidence is on a verifiable chain --------------------------
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
        encoding="utf-8",
        errors="replace",
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
    macro = published.get("totals", {}).get("macro_f1")
    sys.stdout.write("\n")
    if passed != total:
        sys.stderr.write(f"{FAIL} Phase 9 gate not met: {passed}/{total} checks passed\n\n")
        return 1
    sys.stdout.write(
        f"{OK} Phase 9 gate met - {passed}/{total} checks passed against the running stack.\n"
        f"      A per-category F1 number is published in the repository and this run\n"
        f"      reproduced it exactly from the committed corpus (macro F1 {macro}).\n"
        f"      {len(below)} categor(y/ies) sit below the {F1_FLOOR:.0%} floor and the §43.2\n"
        f"      prompt pass is recorded against them; the honest number ships either way.\n"
        f"      A category invented during this run, present in no module, was created\n"
        f"      and classified with no deploy. §22.1's distant-face recall curve was\n"
        f"      measured against the real detector and its shortfall is disclosed.\n\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
